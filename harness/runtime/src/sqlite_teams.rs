//! SQLite transactions for the shared team domain. The Store's exclusive lock
//! is the ownership boundary; this adapter is not a distributed worker database.
use super::{terminal, Event, Session, Store};
use crate::{
    team_service::{self, Command, Create, Error, Transaction as TeamTransaction},
    teams::{Board, Item, Member, Role},
};
use anyhow::{ensure, Context, Result};
use rusqlite::{params, Connection, OptionalExtension};
use serde_json::{json, Value};

pub(super) fn migrate(db: &Connection) -> Result<()> {
    let exists: bool = db.query_row(
        "SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE type='table' AND name='team_version')",
        [],
        |r| r.get(0),
    )?;
    if exists {
        let versions: Vec<i64> = db
            .prepare("SELECT version FROM team_version")?
            .query_map([], |r| r.get(0))?
            .collect::<rusqlite::Result<_>>()?;
        ensure!(versions == [1], "Unsupported SQLite team schema");
    }
    db.execute_batch("BEGIN IMMEDIATE;
      CREATE TABLE IF NOT EXISTS team_version(version INTEGER NOT NULL);
      INSERT INTO team_version SELECT 1 WHERE NOT EXISTS(SELECT 1 FROM team_version);
      CREATE TABLE IF NOT EXISTS teams(id TEXT PRIMARY KEY,request_id TEXT UNIQUE NOT NULL,parameters TEXT NOT NULL,data TEXT NOT NULL,cursor INTEGER NOT NULL DEFAULT 0);
      CREATE TABLE IF NOT EXISTS team_members(session_id TEXT PRIMARY KEY,team_id TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS team_events(team_id TEXT NOT NULL,cursor INTEGER NOT NULL,kind TEXT NOT NULL,data TEXT NOT NULL,PRIMARY KEY(team_id,cursor));
      CREATE TABLE IF NOT EXISTS team_commands(team_id TEXT NOT NULL,id TEXT NOT NULL,request TEXT NOT NULL,result TEXT NOT NULL,PRIMARY KEY(team_id,id));
      CREATE TABLE IF NOT EXISTS team_queue(ordinal INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT UNIQUE NOT NULL,session_id TEXT NOT NULL,team_id TEXT NOT NULL,item_id TEXT,assignment INTEGER,text TEXT NOT NULL,status TEXT NOT NULL,processed INTEGER NOT NULL DEFAULT 0);
      CREATE TABLE IF NOT EXISTS team_cancels(session_id TEXT PRIMARY KEY,turn_id TEXT NOT NULL);
      COMMIT;")?;
    let versions: Vec<i64> = db
        .prepare("SELECT version FROM team_version")?
        .query_map([], |r| r.get(0))?
        .collect::<rusqlite::Result<_>>()?;
    ensure!(versions == [1], "Unsupported SQLite team schema");
    Ok(())
}
fn session(db: &Connection, sid: &str) -> Result<Session> {
    let raw: String = db.query_row("SELECT data FROM sessions WHERE id=?", [sid], |r| r.get(0))?;
    Ok(serde_json::from_str(&raw)?)
}
fn load(db: &Connection, id: &str) -> Result<Board> {
    let raw: String = db
        .query_row("SELECT data FROM teams WHERE id=?", [id], |r| r.get(0))
        .optional()?
        .ok_or(Error::NotFound)?;
    Ok(serde_json::from_str(&raw)?)
}
fn member(db: &Connection, m: &Member) -> Result<()> {
    team_service::fresh_member(&session(db, &m.session_id)?, m.role)?;
    let exists: bool = db.query_row(
        "SELECT EXISTS(SELECT 1 FROM team_members WHERE session_id=?)",
        [&m.session_id],
        |r| r.get(0),
    )?;
    ensure!(!exists, "Session already belongs to a team");
    Ok(())
}
fn record(db: &Connection, sid: &str, tid: Option<&str>, kind: &str, data: Value) -> Result<Event> {
    db.execute(
        "INSERT INTO events(session_id,turn_id,kind,data) VALUES(?,?,?,?)",
        params![sid, tid, kind, data.to_string()],
    )?;
    Ok(Event {
        event_id: db.last_insert_rowid(),
        session_id: sid.into(),
        turn_id: tid.map(str::to_owned),
        kind: kind.into(),
        data,
    })
}
struct Tx<'a>(&'a Connection);
impl TeamTransaction for Tx<'_> {
    fn event(&mut self, b: &Board, kind: &str, data: Value) -> Result<i64> {
        let raw = serde_json::to_string(b)?;
        ensure!(raw.len() <= 200000, "Team board storage limit reached");
        let cursor: i64 =
            self.0
                .query_row("SELECT cursor FROM teams WHERE id=?", [&b.id], |r| r.get(0))?;
        ensure!(cursor < 10000, Error::Limit);
        self.0.execute(
            "UPDATE teams SET data=?,cursor=? WHERE id=?",
            params![raw, cursor + 1, b.id],
        )?;
        self.0.execute(
            "INSERT INTO team_events VALUES(?,?,?,?)",
            params![b.id, cursor + 1, kind, data.to_string()],
        )?;
        Ok(cursor + 1)
    }
    fn read_events(
        &mut self,
        b: &Board,
        after: i64,
        limit: i64,
        item: Option<&str>,
        messages: bool,
    ) -> Result<Value> {
        let cursor: i64 =
            self.0
                .query_row("SELECT cursor FROM teams WHERE id=?", [&b.id], |r| r.get(0))?;
        ensure!(
            after >= 0 && after <= cursor && (1..=50).contains(&limit),
            "Invalid team cursor or page size"
        );
        let mut q=self.0.prepare("SELECT cursor,kind,data FROM team_events WHERE team_id=? AND cursor>? ORDER BY cursor LIMIT ?")?;
        let rows = q.query_map(params![b.id, after, limit], |r| {
            Ok((
                r.get::<_, i64>(0)?,
                r.get::<_, String>(1)?,
                r.get::<_, String>(2)?,
            ))
        })?;
        let mut next = after;
        let mut events = vec![];
        for r in rows {
            let (seq, kind, raw) = r?;
            ensure!(seq == next + 1, "Team event history incomplete");
            next = seq;
            let data: Value = serde_json::from_str(&raw)?;
            if messages && kind != "message_posted" {
                continue;
            }
            if item.is_some_and(|i| !i.is_empty() && data["item_id"] != i) {
                continue;
            }
            events.push(json!({"cursor":seq,"type":kind,"data":data}));
        }
        ensure!(
            next > after || after == cursor,
            "Team event history incomplete"
        );
        Ok(json!({"events":events,"next_after":next,"has_more":next<cursor,"cursor":cursor}))
    }
    fn assignment_finished(&mut self, item: &Item) -> Result<()> {
        if let (Some(sid), Some(key)) = (&item.assignee, &item.submission_id) {
            let status: String = self.0.query_row(
                "SELECT status FROM team_queue WHERE session_id=? AND id=?",
                params![sid, key],
                |r| r.get(0),
            )?;
            ensure!(terminal(&status),"Previous assignment is still queued or running; cancel/wait before reassigning or accepting");
        }
        Ok(())
    }
    fn current_assignment(&mut self, sid: &str, item: &Item) -> Result<()> {
        let s = session(self.0, sid)?;
        let t = s.turn.context("No worker turn")?;
        ensure!(
            item.submission_id.as_deref() == Some(&t.submission_id),
            "This turn does not own the current assignment"
        );
        Ok(())
    }
    fn deliver(
        &mut self,
        b: &mut Board,
        sid: &str,
        item: Option<&Item>,
        text: &str,
        limit: u16,
    ) -> Result<String> {
        ensure!(b.deliveries < 128, "Team wake budget exhausted");
        let s = session(self.0, sid)?;
        ensure!(
            !s.closed && !s.provider_blocked,
            "Recipient unavailable or provider-blocked"
        );
        let n: i64 = self.0.query_row(
            "SELECT count(*) FROM team_queue WHERE session_id=? AND status='queued'",
            [sid],
            |r| r.get(0),
        )?;
        ensure!(n < i64::from(limit), "Recipient queue full");
        let key = crate::id("delivery");
        self.0.execute("INSERT INTO team_queue(id,session_id,team_id,item_id,assignment,text,status) VALUES(?,?,?,?,?,?,'queued')",params![key,sid,b.id,item.map(|i|&i.id),item.map(|i|i.assignment as i64),text])?;
        b.deliveries += 1;
        record(
            self.0,
            sid,
            None,
            "team_delivery_queued",
            json!({"team_id":b.id,"submission_id":key,"item_id":item.map(|i|&i.id)}),
        )?;
        Ok(key)
    }
}
pub(super) fn notice(db: &Connection, sid: &str, key: &str) -> Result<Option<Value>> {
    let row = db
        .query_row(
            "SELECT team_id,item_id,assignment,status FROM team_queue WHERE session_id=? AND id=?",
            params![sid, key],
            |r| {
                Ok((
                    r.get::<_, String>(0)?,
                    r.get::<_, Option<String>>(1)?,
                    r.get::<_, Option<i64>>(2)?,
                    r.get::<_, String>(3)?,
                ))
            },
        )
        .optional()?;
    let Some((team, item, assignment, status)) = row else {
        return Ok(None);
    };
    let b = load(db, &team)?;
    ensure!(!b.cancelled && status != "cancelled", "Team cancelled");
    if let Some(id) = &item {
        let i = b.items.get(id).context("Assignment missing")?;
        ensure!(
            i.assignee.as_deref() == Some(sid)
                && i.submission_id.as_deref() == Some(key)
                && Some(i.assignment as i64) == assignment
                && !matches!(
                    i.status,
                    crate::teams::Status::Done | crate::teams::Status::Cancelled
                ),
            "Assignment changed"
        );
        ensure!(
            i.dependencies
                .iter()
                .all(|d| b.items[d].status == crate::teams::Status::Done),
            "Dependencies not complete"
        );
    }
    Ok(Some(
        json!({"team_id":team,"item_id":item,"assignment":assignment}),
    ))
}
pub(super) fn check(db: &Connection, s: &Session, kind: &str) -> Result<()> {
    if matches!(
        kind,
        "model_request" | "tool_started" | "interaction_resolved"
    ) {
        let cancelled: bool = db.query_row(
            "SELECT EXISTS(SELECT 1 FROM team_cancels WHERE session_id=?)",
            [&s.id],
            |r| r.get(0),
        )?;
        ensure!(!cancelled, "Team cancellation pending");
        if let Some(t) = &s.turn {
            notice(db, &s.id, &t.submission_id)?;
        }
    }
    Ok(())
}
pub(super) fn finished(db: &Connection, s: &Session) -> Result<()> {
    if let Some(t) = &s.turn {
        if terminal(&t.status) {
            db.execute(
                "UPDATE team_queue SET status=? WHERE session_id=? AND id=?",
                params![t.status, s.id, t.submission_id],
            )?;
        }
    }
    Ok(())
}
impl Store {
    pub fn team_create(&mut self, request: Create) -> Result<Value> {
        let b = team_service::new_board(&request)?;
        let raw = serde_json::to_string(&request)?;
        let tx = self.db.transaction()?;
        if let Some((id, old)) = tx
            .query_row(
                "SELECT id,parameters FROM teams WHERE request_id=?",
                [&request.request_id],
                |r| Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?)),
            )
            .optional()?
        {
            ensure!(old == raw, Error::Conflict);
            return Ok(json!({"team_id":id,"duplicate":true}));
        }
        for m in b.members.values() {
            member(&tx, m)?;
        }
        tx.execute(
            "INSERT INTO teams(id,request_id,parameters,data) VALUES(?,?,?,?)",
            params![b.id, request.request_id, raw, serde_json::to_string(&b)?],
        )?;
        for sid in b.members.keys() {
            tx.execute("INSERT INTO team_members VALUES(?,?)", params![sid, b.id])?;
        }
        let cursor = Tx(&tx).event(
            &b,
            "team_created",
            json!({"actor":"trusted-host","members":b.members}),
        )?;
        tx.commit()?;
        Ok(json!({"team_id":b.id,"cursor":cursor,"duplicate":false}))
    }
    pub fn team_snapshot(&self, id: &str) -> Result<Value> {
        let b = load(&self.db, id)?;
        let c: i64 = self
            .db
            .query_row("SELECT cursor FROM teams WHERE id=?", [id], |r| r.get(0))?;
        // Include queued deliveries and the completion outbox, even when no
        // member has a current turn (e.g. waiting for credentials/capacity).
        let pending: bool = self.db.query_row(
            "SELECT EXISTS(SELECT 1 FROM team_queue WHERE team_id=? AND (status NOT IN ('completed','failed','cancelled','interrupted','truncated') OR (item_id IS NOT NULL AND processed=0)))",
            [id], |r| r.get(0),
        )?;
        Ok(json!({"board":b,"cursor":c,"pending_work":pending}))
    }
    pub fn team_events(&self, id: &str, after: i64) -> Result<Value> {
        Tx(&self.db).read_events(&load(&self.db, id)?, after, 50, None, false)
    }
    pub fn team_context(&self, sid: &str) -> Result<Option<crate::teams::Context>> {
        let id: Option<String> = self
            .db
            .query_row(
                "SELECT team_id FROM team_members WHERE session_id=?",
                [sid],
                |r| r.get(0),
            )
            .optional()?;
        let Some(id) = id else {
            return Ok(None);
        };
        let b = load(&self.db, &id)?;
        let s = self.metadata(sid)?;
        let delivery = if let Some(t) = s.turn {
            self.db
                .query_row(
                    "SELECT item_id,assignment FROM team_queue WHERE session_id=? AND id=?",
                    params![sid, t.submission_id],
                    |r| Ok((r.get::<_, Option<String>>(0)?, r.get::<_, Option<i64>>(1)?)),
                )
                .optional()?
        } else {
            None
        };
        Ok(Some(crate::teams::Context {
            team_id: id,
            role: b.members[sid].role,
            session_id: sid.into(),
            active_item: delivery.as_ref().and_then(|d| d.0.clone()),
            assignment: delivery.and_then(|d| d.1),
        }))
    }
    pub fn team_command(&mut self, id: &str, command: Command) -> Result<Value> {
        team_service::request_id(&command.request_id)?;
        let tx = self.db.transaction()?;
        let mut b = load(&tx, id)?;
        let key = format!("app:{}", command.request_id);
        let raw = serde_json::to_string(&command)?;
        if let Some((old, result)) = tx
            .query_row(
                "SELECT request,result FROM team_commands WHERE team_id=? AND id=?",
                params![id, key],
                |r| Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?)),
            )
            .optional()?
        {
            ensure!(old == raw, Error::Conflict);
            return Ok(serde_json::from_str(&result)?);
        }
        let count: i64 = tx.query_row(
            "SELECT count(*) FROM team_commands WHERE team_id=?",
            [id],
            |r| r.get(0),
        )?;
        ensure!(count < 20000, Error::Limit);
        let result = if command.operation == "team_add_member" {
            let m: Member = serde_json::from_value(command.arguments.clone())?;
            team_service::additional_member(&b, &m)?;
            member(&tx, &m)?;
            tx.execute(
                "INSERT INTO team_members VALUES(?,?)",
                params![m.session_id, id],
            )?;
            b.members.insert(m.session_id.clone(), m.clone());
            let cursor = Tx(&tx).event(
                &b,
                "member_added",
                json!({"actor":"app:trusted-host","member":m}),
            )?;
            json!({"cursor":cursor,"member":m})
        } else if command.operation == "team_cancel" {
            ensure!(
                command.arguments == json!({}),
                "Cancel accepts no arguments"
            );
            b.cancelled = true;
            tx.execute(
                "UPDATE team_queue SET status='cancelled' WHERE team_id=? AND status='queued'",
                [id],
            )?;
            for sid in b.members.keys() {
                let s = session(&tx, sid)?;
                if let Some(t) = s.turn {
                    if !terminal(&t.status) {
                        tx.execute(
                            "INSERT INTO team_cancels VALUES(?,?) ON CONFLICT DO NOTHING",
                            params![sid, t.id],
                        )?;
                    }
                }
            }
            let cursor =
                Tx(&tx).event(&b, "team_cancelled", json!({"actor":"app:trusted-host"}))?;
            json!({"cursor":cursor,"cancelled":true})
        } else {
            team_service::operate(
                &mut Tx(&tx),
                &mut b,
                "app:trusted-host",
                Role::Lead,
                &command.operation,
                &command.arguments,
                100,
            )?
        };
        tx.execute(
            "INSERT INTO team_commands VALUES(?,?,?,?)",
            params![id, key, raw, result.to_string()],
        )?;
        tx.commit()?;
        Ok(result)
    }
    pub fn team_call(&mut self, sid: &str, tid: &str, call: &Value, args: &Value) -> Result<Event> {
        let mut s = self.get(sid)?;
        let t = s.turn.as_ref().context("No turn")?;
        ensure!(
            t.id == tid && t.status == "running" && t.queue.first() == Some(call),
            "Team call stale or cancelled"
        );
        let mut tx = self.db.transaction()?;
        check(&tx, &s, "tool_started")?;
        let id: String = tx.query_row(
            "SELECT team_id FROM team_members WHERE session_id=?",
            [sid],
            |r| r.get(0),
        )?;
        let mut b = load(&tx, &id)?;
        let role = b.members.get(sid).context("Not a member")?.role;
        ensure!(s.package.team_role == Some(role), "Coworker role changed");
        let name = call["name"].as_str().context("Tool name missing")?;
        let output = {
            let save = tx.savepoint()?;
            match team_service::operate(&mut Tx(&save), &mut b, sid, role, name, args, 100) {
                Ok(v) => {
                    save.commit()?;
                    v
                }
                Err(e) => {
                    drop(save);
                    if e.downcast_ref::<rusqlite::Error>().is_some() {
                        return Err(e);
                    }
                    json!({"isError":true,"message":e.to_string()})
                }
            }
        };
        let key = format!(
            "agent:{sid}:{tid}:{}",
            call["call_id"].as_str().context("Call ID missing")?
        );
        tx.execute(
            "INSERT INTO team_commands VALUES(?,?,?,?)",
            params![id, key, call.to_string(), output.to_string()],
        )?;
        if !crate::safety::hold_internal_result(&mut s, call) {
            s.history.push(json!({"type":"function_call_output","call_id":call["call_id"],"output":output.to_string()}));
            s.turn.as_mut().unwrap().queue.remove(0);
        }
        Self::append_history(&tx, &s)?;
        tx.execute(
            "UPDATE sessions SET data=? WHERE id=?",
            params![serde_json::to_string(&s)?, sid],
        )?;
        let mut event = record(
            &tx,
            sid,
            Some(tid),
            "tool_finished",
            crate::safety::public_internal_event(
                &s,
                json!({"name":name,"call_id":call["call_id"],"result":output,"execution_kind":"internal","team_id":id,"enforcement":"team-role-and-session-scope"}),
            ),
        )?;
        event.data["result"] = output;
        tx.commit()?;
        Ok(event)
    }
    pub fn team_next(&self, sid: &str) -> Result<Option<(String, String)>> {
        Ok(self.db.query_row("SELECT id,text FROM team_queue WHERE session_id=? AND status='queued' ORDER BY ordinal LIMIT 1",[sid],|r|Ok((r.get(0)?,r.get(1)?))).optional()?)
    }
    pub fn team_tick(&mut self) -> Result<()> {
        let tx = self.db.transaction()?;
        let row=tx.query_row("SELECT session_id,id,team_id,status FROM team_queue WHERE item_id IS NOT NULL AND processed=0 AND status IN ('completed','failed','cancelled','interrupted','truncated') ORDER BY ordinal LIMIT 1",[],|r|Ok((r.get::<_,String>(0)?,r.get::<_,String>(1)?,r.get::<_,String>(2)?,r.get::<_,String>(3)?))).optional()?;
        if let Some((sid, key, id, status)) = row {
            let mut b = load(&tx, &id)?;
            if let Some(item) = team_service::complete(&mut b, &sid, &key, &status) {
                let lead = b.lead.clone();
                let s = session(&tx, &lead)?;
                let n: i64 = tx.query_row(
                    "SELECT count(*) FROM team_queue WHERE session_id=? AND status='queued'",
                    [&lead],
                    |r| r.get(0),
                )?;
                let notified = b.deliveries < 128 && n < 100 && !s.closed && !s.provider_blocked;
                if notified {
                    Tx(&tx).deliver(
                        &mut b,
                        &lead,
                        None,
                        &team_service::completion_text(&sid, &item.id, &status),
                        100,
                    )?;
                }
                Tx(&tx).event(&b,"assignment_turn_finished",json!({"session_id":sid,"item":item,"status":status,"lead_notified":notified,"attention_required":!notified}))?;
            }
            tx.execute("UPDATE team_queue SET processed=1 WHERE id=?", [key])?;
        }
        tx.commit()?;
        Ok(())
    }
    pub fn team_cancellations(&self) -> Result<Vec<(String, String)>> {
        Ok(self
            .db
            .prepare("SELECT session_id,turn_id FROM team_cancels")?
            .query_map([], |r| Ok((r.get(0)?, r.get(1)?)))?
            .collect::<rusqlite::Result<_>>()?)
    }
    pub fn team_cancel_settled(&mut self, sid: &str, tid: &str) -> Result<()> {
        self.db.execute(
            "DELETE FROM team_cancels WHERE session_id=? AND turn_id=?",
            params![sid, tid],
        )?;
        Ok(())
    }
}
