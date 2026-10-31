//! Durable ingress is independent of execution ownership. The trusted app supplies
//! authorized session IDs; this module is not a tenant authorization service.
use crate::db::{locked, Database};
use anyhow::{bail, ensure, Result};
use openworker_runtime::store::{terminal, Session};
use postgres::{Row, Transaction};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TurnSubmission {
    pub session_id: String,
    pub submission_id: String,
    pub text: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub limits: Option<openworker_runtime::budget::Limits>,
}
impl TurnSubmission {
    pub(crate) fn validate(&self) -> Result<()> {
        validate_id(&self.session_id, 80)?;
        validate_id(&self.submission_id, 100)?;
        if let Some(l) = &self.limits {
            l.validate().map_err(|_| SubmissionError::InvalidRequest)?;
        }
        validate_id(&self.text, 32_000)
    }
}
pub(crate) fn validate_id(value: &str, limit: usize) -> Result<()> {
    if value.trim().is_empty() || value.len() > limit || value.contains('\0') {
        return Err(SubmissionError::InvalidRequest.into());
    }
    Ok(())
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SubmissionStatus {
    Queued,
    Started,
    Completed,
    Failed,
    Cancelled,
    Interrupted,
    Truncated,
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct SubmissionReceipt {
    pub session_id: String,
    pub submission_id: String,
    pub turn_id: String,
    pub sequence: i64,
    pub status: SubmissionStatus,
    /// Epoch at initial turn promotion, not the session's current owner.
    pub owner_epoch: Option<i64>,
}
/// Stable ingress domain errors. Database unavailability/unknown outcomes retain
/// the adapter's conservative connection error contract; don't automatically retry.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum SubmissionError {
    InvalidRequest,
    NotFound,
    Conflict,
    QueueFull,
    Busy,
    Closed,
    ProviderBlocked,
    OutOfOrder,
    IncompleteState,
    ControlsPending,
    ControlConflict,
}
impl SubmissionError {
    pub fn code(self) -> &'static str {
        match self {
            Self::InvalidRequest => "invalid_request",
            Self::NotFound => "not_found",
            Self::Conflict => "idempotency_conflict",
            Self::QueueFull => "queue_full",
            Self::Busy => "session_busy",
            Self::Closed => "session_closed",
            Self::ProviderBlocked => "provider_blocked",
            Self::OutOfOrder => "submission_out_of_order",
            Self::IncompleteState => "stored_data_incomplete",
            Self::ControlsPending => "controls_pending",
            Self::ControlConflict => "control_target_conflict",
        }
    }
}
impl std::fmt::Display for SubmissionError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(self.code())
    }
}
impl std::error::Error for SubmissionError {}

/// Receipt retention is part of deduplication. A deleted receipt must not look
/// like permission to accept the same task again with a new turn identity.
pub(crate) fn verify(tx: &mut Transaction<'_>, sid: &str) -> Result<()> {
    let complete: bool = tx.query_one(
        "SELECT command_sequence=(SELECT count(*) FROM submissions WHERE session_id=$1) FROM sessions WHERE id=$1",
        &[&sid],
    )?.get(0);
    if !complete {
        return Err(SubmissionError::IncompleteState.into());
    }
    Ok(())
}

fn receipt(row: &Row) -> Result<SubmissionReceipt> {
    Ok(SubmissionReceipt {
        session_id: row.get("session_id"),
        submission_id: row.get("id"),
        turn_id: row.get("turn_id"),
        sequence: row.get("sequence"),
        status: serde_json::from_value(json!(row.get::<_, String>("status")))?,
        owner_epoch: row.get("owner_epoch"),
    })
}
// Caller holds the session row lock. An allocated ID/sequence only becomes
// visible if the containing transaction (including events/audit) commits.
pub(crate) fn insert(
    tx: &mut Transaction<'_>,
    request: &TurnSubmission,
    status: &str,
    epoch: Option<i64>,
) -> Result<SubmissionReceipt> {
    insert_reserved(tx, request, status, epoch, &openworker_runtime::id("t"))
}
pub(crate) fn insert_reserved(
    tx: &mut Transaction<'_>,
    request: &TurnSubmission,
    status: &str,
    epoch: Option<i64>,
    turn_id: &str,
) -> Result<SubmissionReceipt> {
    request.validate()?;
    validate_id(turn_id, 80)?;
    let sequence: i64 = tx.query_one(
        "UPDATE sessions SET command_sequence=command_sequence+1 WHERE id=$1 RETURNING command_sequence",
        &[&request.session_id],
    )?.get(0);
    let row = tx.query_one(
        "INSERT INTO submissions(session_id,id,text,turn_id,sequence,status,owner_epoch,limits) VALUES($1,$2,$3,$4,$5,$6,$7,$8) RETURNING *",
        &[&request.session_id, &request.submission_id, &request.text, &turn_id, &sequence, &status, &epoch, &request.limits.as_ref().map(serde_json::to_value).transpose()?],
    )?;
    receipt(&row)
}

/// Called before the final owner-fenced event in the same state transaction.
pub(crate) fn finish(tx: &mut Transaction<'_>, session: &Session) -> Result<()> {
    verify(tx, &session.id)?;
    if session.closed {
        // Admission and close serialize on the session row. Do not strand an
        // already accepted decision/cancel by removing it from worker eligibility.
        if crate::controls::pending(tx, &session.id)? {
            return Err(SubmissionError::ControlsPending.into());
        }
        let queued: bool = tx
            .query_one(
                "SELECT EXISTS(SELECT 1 FROM submissions WHERE session_id=$1 AND status='queued')",
                &[&session.id],
            )?
            .get(0);
        if queued {
            bail!("Cannot close session with queued submissions");
        }
    }
    if let Some(turn) = &session.turn {
        if terminal(&turn.status) {
            if let Some(row) = tx.query_opt(
                "SELECT status FROM submissions WHERE session_id=$1 AND id=$2 AND turn_id=$3",
                &[&session.id, &turn.submission_id, &turn.id],
            )? {
                let old: String = row.get(0);
                if old != "started" && old != turn.status {
                    bail!("Terminal submission outcome cannot be rewritten");
                }
                tx.execute(
                    "UPDATE submissions SET status=$4 WHERE session_id=$1 AND id=$2 AND turn_id=$3",
                    &[&session.id, &turn.submission_id, &turn.id, &turn.status],
                )?;
                crate::team_execution::finished(tx, session)?;
            }
        }
    }
    Ok(())
}
impl Database {
    pub(crate) fn submit_turn(&mut self, request: TurnSubmission) -> Result<SubmissionReceipt> {
        self.submit_turn_with_mode(request, false)
    }
    pub(crate) fn submit_turn_with_mode(
        &mut self,
        request: TurnSubmission,
        single: bool,
    ) -> Result<SubmissionReceipt> {
        request.validate()?;
        let mut tx = self.client.transaction()?;
        let p = crate::auth::guard(
            &mut tx,
            self.auth.as_ref(),
            crate::auth::Action::Submit,
            Some(&request.session_id),
            None,
        )?;
        crate::auth::audit(
            &mut tx,
            p.as_ref(),
            crate::auth::Action::Submit,
            Some(&request.session_id),
            Some(&request.submission_id),
        )?;
        let session = locked(&mut tx, &request.session_id, None)?;
        verify(&mut tx, &request.session_id)?;
        // Deduplicate before lifecycle/capacity checks: lost receipts remain
        // reconcilable when the queue fills or the session later closes.
        if let Some(row) = tx.query_opt(
            "SELECT * FROM submissions WHERE session_id=$1 AND id=$2",
            &[&request.session_id, &request.submission_id],
        )? {
            if row.get::<_, String>("text") != request.text
                || row.get::<_, Option<Value>>("limits")
                    != request
                        .limits
                        .as_ref()
                        .map(serde_json::to_value)
                        .transpose()?
            {
                return Err(SubmissionError::Conflict.into());
            }
            let result = receipt(&row)?;
            tx.commit()?;
            return Ok(result);
        }
        if session.closed {
            return Err(SubmissionError::Closed.into());
        }
        crate::agent_team::ensure_admission(&mut tx, &session)?;

        if session.provider_blocked {
            return Err(SubmissionError::ProviderBlocked.into());
        }
        let count: i64 = tx
            .query_one(
                "SELECT count(*) FROM submissions WHERE session_id=$1 AND status='queued'",
                &[&request.session_id],
            )?
            .get(0);
        if single && (count > 0 || session.turn.as_ref().is_some_and(|t| !terminal(&t.status))) {
            return Err(SubmissionError::Busy.into());
        }
        if count >= i64::from(self.max_queued_turns) {
            return Err(SubmissionError::QueueFull.into());
        }
        let configured = crate::service_admin::current(&mut tx)?;
        ensure!(
            !configured.configuration.admissions_paused,
            SubmissionError::Busy
        );
        let waiting: i64 = tx
            .query_one(
                "SELECT count(*) FROM submissions WHERE session_id=$1 AND status='queued'",
                &[&request.session_id],
            )?
            .get(0);
        ensure!(
            waiting < configured.configuration.max_queued_turns,
            SubmissionError::QueueFull
        );
        let result = insert(&mut tx, &request, "queued", None)?;
        journal_acceptance(&mut tx, &result, p.as_ref().map(|p| p.principal.as_str()))?;
        tx.commit()?;
        Ok(result)
    }
    pub(crate) fn submission(&mut self, sid: &str, key: &str) -> Result<SubmissionReceipt> {
        let mut tx = self.client.transaction()?;
        crate::auth::guard(
            &mut tx,
            self.auth.as_ref(),
            crate::auth::Action::Read,
            Some(sid),
            None,
        )?;
        locked(&mut tx, sid, None)?;
        verify(&mut tx, sid)?;
        let row = tx
            .query_opt(
                "SELECT * FROM submissions WHERE session_id=$1 AND id=$2",
                &[&sid, &key],
            )?
            .ok_or(SubmissionError::NotFound)?;
        let result = receipt(&row)?;
        tx.commit()?;
        Ok(result)
    }
    pub(crate) fn next_turn(&mut self, sid: &str) -> Result<Option<TurnSubmission>> {
        let epoch = self.epoch(sid)?;
        let mut tx = self.client.transaction()?;
        let session = locked(&mut tx, sid, Some((&self.owner, epoch)))?;
        verify(&mut tx, sid)?;
        if crate::controls::pending(&mut tx, sid)? {
            return Ok(None);
        }
        if session.closed
            || session.provider_blocked
            || session.turn.as_ref().is_some_and(|t| !terminal(&t.status))
        {
            return Ok(None);
        }
        if let Err(error) = crate::agent_team::ensure_admission(&mut tx, &session) {
            if error.downcast_ref::<SubmissionError>() == Some(&SubmissionError::Closed) {
                tx.commit()?;
                return Ok(None);
            }
            return Err(error);
        }
        let row = tx.query_opt(
            "SELECT id,text,limits FROM submissions WHERE session_id=$1 AND status='queued' ORDER BY sequence LIMIT 1", &[&sid],
        )?;
        let result = row.map(|r| TurnSubmission {
            session_id: sid.into(),
            submission_id: r.get(0),
            text: r.get(1),
            limits: r
                .get::<_, Option<Value>>(2)
                .map(serde_json::from_value)
                .transpose()
                .expect("Validated stored turn limits"),
        });
        tx.commit()?;
        Ok(result)
    }
}

pub(crate) fn journal_acceptance(
    tx: &mut Transaction<'_>,
    result: &SubmissionReceipt,
    principal: Option<&str>,
) -> Result<()> {
    // Shared row lock serializes ALL cursor allocation. Ingress does not
    // mutate canonical state, so it must not invalidate a worker's snapshot.
    let row = tx.query_one(
        "UPDATE sessions SET cursor=cursor+1 WHERE id=$1 RETURNING cursor,epoch",
        &[&result.session_id],
    )?;
    let cursor: i64 = row.get(0);
    let observed_epoch: i64 = row.get(1);
    let data = json!({"submission_id":result.submission_id,"turn_id":result.turn_id,"sequence":result.sequence,"status":"queued"});
    tx.execute(
        "INSERT INTO events VALUES($1,$2,$3,'command_accepted',$4)",
        &[&result.session_id, &cursor, &result.turn_id, &data],
    )?;
    // Epoch is an observation for correlation, not an API execution grant.
    tx.execute("INSERT INTO audit(session_id,cursor,turn_id,kind,owner_epoch,actor) VALUES($1,$2,$3,'command_accepted',$4,$5)",
            &[&result.session_id, &cursor, &result.turn_id, &observed_epoch,&principal])?;
    Ok(())
}
