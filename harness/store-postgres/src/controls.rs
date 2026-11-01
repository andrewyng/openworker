use crate::db::{event, locked, read_history, Database};
use anyhow::{bail, Context, Result};
use openworker_runtime::{
    control::{Action, Command, Record, Status},
    store::{terminal, Event, Session},
};
use postgres::{Row, Transaction};
use serde_json::{json, Value};

fn record(row: &Row) -> Result<Record> {
    Ok(Record {
        command: serde_json::from_value(row.get("command"))?,
        status: serde_json::from_value(json!(row.get::<_, String>("status")))?,
        reason: row.get("reason"),
    })
}
fn verify(tx: &mut Transaction<'_>, sid: &str) -> Result<()> {
    let ok:bool=tx.query_one("SELECT control_count=(SELECT count(*) FROM controls WHERE session_id=$1) FROM sessions WHERE id=$1",&[&sid])?.get(0);
    if !ok {
        bail!("Stored control data incomplete");
    }
    Ok(())
}
pub(crate) fn pending(tx: &mut Transaction<'_>, sid: &str) -> Result<bool> {
    verify(tx, sid)?;
    Ok(tx
        .query_one(
            "SELECT EXISTS(SELECT 1 FROM controls WHERE session_id=$1 AND status='pending')",
            &[&sid],
        )?
        .get(0))
}
fn cancel_pending(tx: &mut Transaction<'_>, s: &Session) -> Result<bool> {
    let tid = s.turn.as_ref().map(|t| t.id.as_str()).unwrap_or("");
    Ok(tx.query_one("SELECT EXISTS(SELECT 1 FROM controls WHERE session_id=$1 AND turn_id=$2 AND target=$3 AND status='pending')",&[&s.id,&tid,&format!("cancel:{tid}")])?.get(0))
}
/// Runs inside the owner's state transaction, before the final lease fence.
pub(crate) fn before_event(
    tx: &mut Transaction<'_>,
    s: &Session,
    kind: &str,
    data: &Value,
) -> Result<()> {
    verify(tx, &s.id)?;
    if matches!(
        kind,
        "tool_started" | "model_request" | "interaction_resolved"
    ) && cancel_pending(tx, s)?
    {
        bail!("Cancellation intent pending; new work refused");
    }
    if kind == "interaction_resolved" {
        if let Some(iid) = data["interaction_id"].as_str() {
            if let Some(row) = tx.query_opt(
                "SELECT id FROM controls WHERE session_id=$1 AND target=$2 AND status='pending'",
                &[&s.id, &format!("response:{iid}")],
            )? {
                if data["control_request_id"].as_str() != Some(row.get::<_, String>(0).as_str()) {
                    bail!("Apply the saved control decision; direct response cannot override it");
                }
            }
        }
    }
    let Some(key) = data["control_request_id"].as_str() else {
        return Ok(());
    };
    if !matches!(kind, "interaction_resolved" | "turn_cancelling") {
        return Ok(());
    }
    let saved = record(
        &tx.query_opt(
            "SELECT * FROM controls WHERE session_id=$1 AND id=$2",
            &[&s.id, &key],
        )?
        .context("Missing control command")?,
    )?;
    if saved.status != Status::Pending || data["actor"] != saved.command.actor {
        bail!("Stale or mismatched control command");
    }
    let t = s.turn.as_ref().context("Missing control turn")?;
    if t.id != saved.command.turn_id {
        bail!("Control turn changed");
    }
    match &saved.command.action {
        Action::Response {
            interaction_id,
            response,
            binding,
        } => {
            let i = s
                .interactions
                .iter()
                .find(|i| i.id == *interaction_id)
                .context("Missing interaction")?;
            if kind != "interaction_resolved"
                || i.turn_id != t.id
                || i.response.as_ref() != Some(response)
                || t.status != "accepted"
                || i.bundle_digest != binding.bundle_digest
                || i.schema_digest != binding.schema_digest
                || i.policy_digest != binding.policy_digest
                || i.resource_policy_digest != binding.resource_policy_digest
            {
                bail!("Control application mismatch");
            }
        }
        Action::Cancel => {
            if kind != "turn_cancelling" || t.status != "cancelling" {
                bail!("Control cancellation mismatch");
            }
        }
    }
    tx.execute(
        "UPDATE controls SET status='applied' WHERE session_id=$1 AND id=$2",
        &[&s.id, &key],
    )?;
    Ok(())
}
impl Database {
    pub(crate) fn submit_control(&mut self, command: Command) -> Result<Record> {
        command
            .validate()
            .map_err(|_| crate::SubmissionError::InvalidRequest)?;
        let mut tx = self.client.transaction()?;
        let p = crate::auth::guard(
            &mut tx,
            self.auth.as_ref(),
            crate::auth::Action::Control,
            Some(&command.session_id),
            None,
        )?;
        crate::auth::actor(p.as_ref(), &command.actor)?;
        let s = locked(&mut tx, &command.session_id, None)?;
        verify(&mut tx, &s.id)?;
        if let Some(row) = tx.query_opt(
            "SELECT * FROM controls WHERE session_id=$1 AND id=$2",
            &[&s.id, &command.request_id],
        )? {
            let saved = record(&row)?;
            if saved.command != command {
                return Err(crate::SubmissionError::Conflict.into());
            }
            crate::auth::audit(
                &mut tx,
                p.as_ref(),
                crate::auth::Action::Control,
                Some(&command.session_id),
                Some(&command.request_id),
            )?;
            tx.commit()?;
            return Ok(saved);
        }
        if s.closed {
            return Err(crate::SubmissionError::Closed.into());
        }
        if tx
            .query_opt(
                "SELECT id FROM controls WHERE session_id=$1 AND target=$2",
                &[&s.id, &command.target()],
            )?
            .is_some()
        {
            return Err(crate::SubmissionError::ControlConflict.into());
        }
        command
            .check_saved(&s)
            .map_err(|_| crate::SubmissionError::ControlConflict)?;
        if matches!(command.action, Action::Response { .. }) && cancel_pending(&mut tx, &s)? {
            return Err(crate::SubmissionError::ControlConflict.into());
        }
        let count: i64 = tx
            .query_one(
                "SELECT count(*) FROM controls WHERE session_id=$1 AND status='pending'",
                &[&s.id],
            )?
            .get(0);
        if count >= 128 {
            return Err(crate::SubmissionError::QueueFull.into());
        }
        let row=tx.query_one("UPDATE sessions SET control_count=control_count+1,cursor=cursor+1 WHERE id=$1 RETURNING control_count,cursor,epoch",&[&s.id])?;
        let (seq, cursor, epoch): (i64, i64, i64) = (row.get(0), row.get(1), row.get(2));
        let payload = serde_json::to_value(&command)?;
        tx.execute("INSERT INTO controls(session_id,id,turn_id,target,command,sequence,status) VALUES($1,$2,$3,$4,$5,$6,'pending')",&[&s.id,&command.request_id,&command.turn_id,&command.target(),&payload,&seq])?;
        let data = json!({"request_id":command.request_id,"actor":command.actor,"target":command.target(),"status":"pending"});
        tx.execute(
            "INSERT INTO events VALUES($1,$2,$3,'control_received',$4)",
            &[&s.id, &cursor, &command.turn_id, &data],
        )?;
        tx.execute("INSERT INTO audit(session_id,cursor,turn_id,kind,owner_epoch,actor) VALUES($1,$2,$3,'control_received',$4,$5)",&[&s.id,&cursor,&command.turn_id,&epoch,&command.actor])?;
        crate::auth::audit(
            &mut tx,
            p.as_ref(),
            crate::auth::Action::Control,
            Some(&command.session_id),
            Some(&command.request_id),
        )?;
        tx.commit()?;
        Ok(Record {
            command,
            status: Status::Pending,
            reason: None,
        })
    }
    pub(crate) fn control_record(&mut self, sid: &str, key: &str, owned: bool) -> Result<Record> {
        let epoch = if owned { Some(self.epoch(sid)?) } else { None };
        let mut tx = self.client.transaction()?;
        crate::auth::guard(
            &mut tx,
            self.auth.as_ref(),
            crate::auth::Action::Read,
            Some(sid),
            None,
        )?;
        locked(&mut tx, sid, epoch.map(|e| (self.owner.as_str(), e)))?;
        verify(&mut tx, sid)?;
        let result = record(
            &tx.query_opt(
                "SELECT * FROM controls WHERE session_id=$1 AND id=$2",
                &[&sid, &key],
            )?
            .ok_or(crate::SubmissionError::NotFound)?,
        )?;
        tx.commit()?;
        Ok(result)
    }
    pub(crate) fn next_control(&mut self, sid: &str) -> Result<Option<Record>> {
        let epoch = self.epoch(sid)?;
        let mut tx = self.client.transaction()?;
        locked(&mut tx, sid, Some((&self.owner, epoch)))?;
        verify(&mut tx, sid)?;
        let result=tx.query_opt("SELECT * FROM controls WHERE session_id=$1 AND status='pending' ORDER BY (command->'action'->>'kind'='cancel') DESC,sequence LIMIT 1",&[&sid])?.map(|r|record(&r)).transpose()?;
        tx.commit()?;
        Ok(result)
    }
    pub(crate) fn settle_control(
        &mut self,
        sid: &str,
        key: &str,
        rejected: bool,
        reason: &str,
    ) -> Result<Event> {
        if ![
            "stale_identity_or_target",
            "runtime_validation_failed",
            "already_terminal",
        ]
        .contains(&reason)
        {
            bail!("Invalid control settlement reason");
        }
        let epoch = self.epoch(sid)?;
        let mut tx = self.client.transaction()?;
        let s = locked(&mut tx, sid, Some((&self.owner, epoch)))?;
        verify(&mut tx, sid)?;
        let saved = record(
            &tx.query_opt(
                "SELECT * FROM controls WHERE session_id=$1 AND id=$2",
                &[&sid, &key],
            )?
            .context("Control missing")?,
        )?;
        if saved.status != Status::Pending {
            bail!("Control already settled");
        }
        if !rejected
            && (!matches!(saved.command.action, Action::Cancel)
                || s.turn
                    .as_ref()
                    .is_none_or(|t| t.id != saved.command.turn_id || !terminal(&t.status)))
        {
            bail!("Cannot acknowledge unapplied control");
        }
        let status = if rejected { "rejected" } else { "applied" };
        tx.execute(
            "UPDATE controls SET status=$3,reason=$4 WHERE session_id=$1 AND id=$2",
            &[&sid, &key, &status, &reason],
        )?;
        let result = event(
            &mut tx,
            &s,
            &self.owner,
            epoch,
            "control_settled",
            json!({"control_request_id":key,"actor":saved.command.actor,"status":status,"reason":reason}),
        )?;
        tx.commit()?;
        Ok(result)
    }
    pub(crate) fn uncertain_calls(&mut self, sid: &str) -> Result<Vec<Value>> {
        let epoch = self.epoch(sid)?;
        let mut tx = self.client.transaction()?;
        let mut s = locked(&mut tx, sid, Some((&self.owner, epoch)))?;
        read_history(&mut tx, &mut s)?;
        let mut calls = vec![];
        if let Some(t) = &s.turn {
            for c in &t.queue {
                let started:bool=tx.query_one("SELECT EXISTS(SELECT 1 FROM events WHERE session_id=$1 AND turn_id=$2 AND kind='tool_started' AND data->>'call_id'=$3)",&[&sid,&t.id,&c["call_id"].as_str().context("Bad call ID")?])?.get(0);
                if started
                    && !s.history.iter().any(|v| {
                        v["type"] == "function_call_output" && v["call_id"] == c["call_id"]
                    })
                {
                    calls.push(c.clone());
                }
            }
        }
        tx.commit()?;
        Ok(calls)
    }
}
