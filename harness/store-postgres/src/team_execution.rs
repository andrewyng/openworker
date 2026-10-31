//! Team deliveries use the ordinary turn queue. Never replay uncertain effects.
use anyhow::{ensure, Context, Result};
use openworker_runtime::{
    store::{terminal, Session},
    teams::{Board, Item, Status},
};
use postgres::Transaction;
use serde_json::{json, Value};

pub(crate) fn finished(tx: &mut Transaction<'_>, s: &Session) -> Result<()> {
    if !crate::teams::present(tx)? {
        return Ok(());
    }
    let Some(t) = &s.turn else {
        return Ok(());
    };
    tx.execute("INSERT INTO team_completions(session_id,submission_id,team_id,status) SELECT session_id,submission_id,team_id,$3 FROM team_deliveries WHERE session_id=$1 AND submission_id=$2 AND item_id IS NOT NULL ON CONFLICT DO NOTHING",&[&s.id,&t.submission_id,&t.status])?;
    Ok(())
}
impl crate::db::Database {
    /// Durable completion outbox: completion commit never locks another session.
    /// An idle scheduler slot can notify the lead after crash/restart exactly once
    /// at the submission level; delivery does not imply exactly-once model billing.
    pub(crate) fn dispatch_team_completion(&mut self) -> Result<()> {
        let mut tx = self.client.transaction()?;
        if !crate::teams::present(&mut tx)? {
            return Ok(());
        }
        let Some(row)=tx.query_opt("SELECT team_id FROM team_completions WHERE NOT processed ORDER BY team_id,session_id,submission_id LIMIT 1",&[])? else{return Ok(());};
        let team: String = row.get(0);
        let mut board = crate::teams::load(&mut tx, &team, true)?;
        let Some(row)=tx.query_opt("SELECT session_id,submission_id,status FROM team_completions WHERE team_id=$1 AND NOT processed ORDER BY session_id,submission_id LIMIT 1 FOR UPDATE SKIP LOCKED",&[&team])? else{return Ok(());};
        let (sid, key, status): (String, String, String) = (row.get(0), row.get(1), row.get(2));
        if let Some(item) =
            openworker_runtime::team_service::complete(&mut board, &sid, &key, &status)
        {
            let id = item.id.clone();
            let item_snapshot = json!(item);
            let lead = board.lead.clone();
            let lead_session = crate::db::locked(&mut tx, &lead, None)?;
            let queued: i64 = tx
                .query_one(
                    "SELECT count(*) FROM submissions WHERE session_id=$1 AND status='queued'",
                    &[&lead],
                )?
                .get(0);
            let notified = board.deliveries < 128
                && queued < i64::from(self.max_queued_turns)
                && !lead_session.closed
                && !lead_session.provider_blocked;
            if notified {
                deliver(
                    &mut tx,
                    &mut board,
                    &lead,
                    None,
                    &openworker_runtime::team_service::completion_text(&sid, &id, &status),
                    self.max_queued_turns,
                )?;
            }
            crate::teams::event(
                &mut tx,
                &board,
                "assignment_turn_finished",
                json!({"session_id":sid,"item":item_snapshot,"status":status,"lead_notified":notified,"attention_required":!notified}),
            )?;
        }
        tx.execute(
            "UPDATE team_completions SET processed=TRUE WHERE session_id=$1 AND submission_id=$2",
            &[&sid, &key],
        )?;
        tx.commit()?;
        Ok(())
    }
}

pub(crate) fn assignment_finished(tx: &mut Transaction<'_>, item: &Item) -> Result<()> {
    if let (Some(sid), Some(key)) = (&item.assignee, &item.submission_id) {
        let status: String = tx
            .query_opt(
                "SELECT status FROM submissions WHERE session_id=$1 AND id=$2",
                &[sid, key],
            )?
            .context("Assignment receipt missing")?
            .get(0);
        ensure!(terminal(&status),"Previous assignment is still queued or running; cancel/wait before reassigning or accepting");
    }
    Ok(())
}
pub(crate) fn current_assignment(tx: &mut Transaction<'_>, sid: &str, item: &Item) -> Result<()> {
    let s = crate::db::locked(tx, sid, None)?;
    let t = s.turn.as_ref().context("No worker turn")?;
    ensure!(
        item.submission_id.as_deref() == Some(&t.submission_id),
        "This turn does not own the current assignment"
    );
    Ok(())
}
fn session_event(
    tx: &mut Transaction<'_>,
    sid: &str,
    tid: Option<&str>,
    kind: &str,
    data: Value,
) -> Result<()> {
    let row = tx.query_one(
        "UPDATE sessions SET cursor=cursor+1 WHERE id=$1 RETURNING cursor,epoch",
        &[&sid],
    )?;
    let cursor: i64 = row.get(0);
    let epoch: i64 = row.get(1);
    tx.execute(
        "INSERT INTO events VALUES($1,$2,$3,$4,$5)",
        &[&sid, &cursor, &tid, &kind, &data],
    )?;
    tx.execute(
        "INSERT INTO audit(session_id,cursor,turn_id,kind,owner_epoch) VALUES($1,$2,$3,$4,$5)",
        &[&sid, &cursor, &tid, &kind, &epoch],
    )?;
    Ok(())
}
pub(crate) fn deliver(
    tx: &mut Transaction<'_>,
    board: &mut Board,
    sid: &str,
    item: Option<&Item>,
    text: &str,
    queue_limit: u16,
) -> Result<String> {
    ensure!(
        board.deliveries < 128,
        "Team wake budget exhausted; stop and ask the application to review the team"
    );
    let session = crate::db::locked(tx, sid, None)?;
    ensure!(
        !session.closed && !session.provider_blocked,
        "Recipient unavailable or provider-blocked"
    );
    crate::submissions::verify(tx, sid)?;
    let queued: i64 = tx
        .query_one(
            "SELECT count(*) FROM submissions WHERE session_id=$1 AND status='queued'",
            &[&sid],
        )?
        .get(0);
    ensure!(queued < i64::from(queue_limit), "Recipient queue full");
    let key = openworker_runtime::id("delivery");
    let receipt = crate::submissions::insert(
        tx,
        &crate::TurnSubmission {
            limits: None,
            session_id: sid.into(),
            submission_id: key.clone(),
            text: text.into(),
        },
        "queued",
        None,
    )?;
    let item_id = item.map(|i| i.id.as_str());
    let assignment = item.map(|i| i.assignment as i64);
    tx.execute(
        "INSERT INTO team_deliveries VALUES($1,$2,$3,$4,$5)",
        &[&sid, &key, &board.id, &item_id, &assignment],
    )?;
    board.deliveries += 1;
    session_event(
        tx,
        sid,
        Some(&receipt.turn_id),
        "team_delivery_queued",
        json!({"team_id":board.id,"submission_id":key,"turn_id":receipt.turn_id,"item_id":item_id}),
    )?;
    Ok(key)
}
pub(crate) fn notice(tx: &mut Transaction<'_>, sid: &str, key: &str) -> Result<Option<Value>> {
    if !crate::teams::present(tx)? {
        return Ok(None);
    }
    let Some(row)=tx.query_opt("SELECT team_id,item_id,assignment FROM team_deliveries WHERE session_id=$1 AND submission_id=$2",&[&sid,&key])? else {return Ok(None);};
    let team: String = row.get(0);
    let item: Option<String> = row.get(1);
    let assignment: Option<i64> = row.get(2);
    let board = crate::teams::load(tx, &team, false)?;
    ensure!(!board.cancelled, "Team cancelled");
    if let Some(item) = &item {
        let current = board.items.get(item).context("Assignment missing")?;
        ensure!(
            current.assignee.as_deref() == Some(sid)
                && current.assignment as i64 == assignment.unwrap_or(-1)
                && current.submission_id.as_deref() == Some(key)
                && !matches!(current.status, Status::Done | Status::Cancelled),
            "Assignment changed"
        );
        ensure!(
            current
                .dependencies
                .iter()
                .all(|d| board.items[d].status == Status::Done),
            "Dependencies not complete"
        );
    }
    Ok(Some(
        json!({"team_id":team,"item_id":item,"assignment":assignment}),
    ))
}
pub(crate) fn check(tx: &mut Transaction<'_>, s: &Session, kind: &str) -> Result<()> {
    if matches!(
        kind,
        "model_request" | "tool_started" | "interaction_resolved" | "team_tool_finished"
    ) {
        if let Some(turn) = &s.turn {
            notice(tx, &s.id, &turn.submission_id)?;
        }
    }
    Ok(())
}
pub(crate) fn cancel(tx: &mut Transaction<'_>, board: &mut Board, actor: &str) -> Result<()> {
    if board.cancelled {
        return Ok(());
    }
    board.cancelled = true;
    for sid in board.members.keys() {
        let session = crate::db::locked(tx, sid, None)?;
        tx.execute("UPDATE submissions SET status='cancelled' WHERE session_id=$1 AND status='queued' AND id IN (SELECT submission_id FROM team_deliveries WHERE session_id=$1 AND team_id=$2)",&[&sid,&board.id])?;
        if let Some(t) = session.turn.as_ref().filter(|t| !terminal(&t.status)) {
            let command = openworker_runtime::control::Command {
                session_id: sid.clone(),
                request_id: openworker_runtime::id("team_cancel"),
                turn_id: t.id.clone(),
                actor: actor.into(),
                action: openworker_runtime::control::Action::Cancel,
            };
            if tx
                .query_opt(
                    "SELECT id FROM controls WHERE session_id=$1 AND target=$2",
                    &[&sid, &command.target()],
                )?
                .is_none()
            {
                let seq:i64=tx.query_one("UPDATE sessions SET control_count=control_count+1 WHERE id=$1 RETURNING control_count",&[&sid])?.get(0);
                tx.execute("INSERT INTO controls(session_id,id,turn_id,target,command,sequence,status) VALUES($1,$2,$3,$4,$5,$6,'pending')",&[&sid,&command.request_id,&t.id,&command.target(),&json!(command),&seq])?;
                session_event(
                    tx,
                    sid,
                    Some(&t.id),
                    "control_received",
                    json!({"request_id":command.request_id,"target":command.target(),"actor":actor,"status":"pending"}),
                )?;
            }
        }
        session_event(
            tx,
            sid,
            session.turn.as_ref().map(|t| t.id.as_str()),
            "team_cancelled",
            json!({"team_id":board.id}),
        )?;
    }
    Ok(())
}
