//! Durable server teams. All model identities come from their fenced session.
use crate::{
    auth,
    db::{self, Database},
    PostgresStore,
};
use anyhow::{ensure, Context, Result};
use openworker_runtime::{
    store::Event,
    teams::{Board, Item, Member, Role},
};
use postgres::Transaction;
use serde_json::{json, Value};

pub use openworker_runtime::team_service::{Command, Create, Error};
fn request_error(error: anyhow::Error) -> anyhow::Error {
    if error.downcast_ref::<postgres::Error>().is_some()
        || error.downcast_ref::<auth::Error>().is_some()
        || error.downcast_ref::<crate::SubmissionError>().is_some()
        || error.downcast_ref::<Error>().is_some()
    {
        error
    } else {
        Error::Invalid.into()
    }
}

pub(crate) fn migrate(tx: &mut Transaction<'_>) -> Result<()> {
    if tx
        .query_one("SELECT to_regclass('team_version') IS NOT NULL", &[])?
        .get::<_, bool>(0)
    {
        let rows = tx.query("SELECT version FROM team_version WHERE singleton", &[])?;
        ensure!(
            rows.len() == 1 && rows[0].get::<_, i32>(0) == 1,
            "Unsupported team schema"
        );
    } else {
        ensure!(
            !tx.query_one("SELECT to_regclass('teams') IS NOT NULL", &[])?
                .get::<_, bool>(0),
            "Unversioned team store"
        );
        tx.batch_execute("CREATE TABLE team_version(singleton BOOLEAN PRIMARY KEY CHECK(singleton),version INTEGER NOT NULL);
          INSERT INTO team_version VALUES(TRUE,1);
          CREATE TABLE teams(id TEXT PRIMARY KEY, request_id TEXT NOT NULL, actor TEXT NOT NULL, parameters JSONB NOT NULL, data JSONB NOT NULL, cursor BIGINT NOT NULL DEFAULT 0, UNIQUE(actor,request_id));
          CREATE TABLE team_members(session_id TEXT PRIMARY KEY REFERENCES sessions(id),team_id TEXT NOT NULL REFERENCES teams(id));
          CREATE TABLE team_events(team_id TEXT NOT NULL REFERENCES teams(id),cursor BIGINT NOT NULL,kind TEXT NOT NULL,data JSONB NOT NULL,PRIMARY KEY(team_id,cursor));
          CREATE TABLE team_commands(team_id TEXT NOT NULL REFERENCES teams(id),id TEXT NOT NULL,actor TEXT NOT NULL,request JSONB NOT NULL,result JSONB NOT NULL,PRIMARY KEY(team_id,id));
          CREATE TABLE team_deliveries(session_id TEXT NOT NULL,submission_id TEXT NOT NULL,team_id TEXT NOT NULL REFERENCES teams(id),item_id TEXT,assignment BIGINT,PRIMARY KEY(session_id,submission_id),FOREIGN KEY(session_id,submission_id) REFERENCES submissions(session_id,id));
          CREATE TABLE team_completions(session_id TEXT NOT NULL,submission_id TEXT NOT NULL,team_id TEXT NOT NULL REFERENCES teams(id),status TEXT NOT NULL,processed BOOLEAN NOT NULL DEFAULT FALSE,PRIMARY KEY(session_id,submission_id));")?;
    }
    Ok(())
}
pub(crate) fn present(tx: &mut Transaction<'_>) -> Result<bool> {
    Ok(tx
        .query_one("SELECT to_regclass('team_version') IS NOT NULL", &[])?
        .get(0))
}
pub(crate) fn load(tx: &mut Transaction<'_>, id: &str, lock: bool) -> Result<Board> {
    let sql = if lock {
        "SELECT data FROM teams WHERE id=$1 FOR UPDATE"
    } else {
        "SELECT data FROM teams WHERE id=$1"
    };
    let row = tx.query_opt(sql, &[&id])?.ok_or(Error::NotFound)?;
    Ok(serde_json::from_value(row.get(0))?)
}
pub(crate) fn event(
    tx: &mut Transaction<'_>,
    board: &Board,
    kind: &str,
    data: Value,
) -> Result<i64> {
    let value = serde_json::to_value(board)?;
    ensure!(
        // Keep room for a full board read nested inside a canonical tool result.
        value.to_string().len() <= 200000,
        "Team board storage limit reached"
    );
    let cursor:i64=tx.query_opt("UPDATE teams SET data=$2,cursor=cursor+1 WHERE id=$1 AND cursor<10000 RETURNING cursor",&[&board.id,&value])?.ok_or(Error::Limit)?.get(0);
    tx.execute(
        "INSERT INTO team_events VALUES($1,$2,$3,$4)",
        &[&board.id, &cursor, &kind, &data],
    )?;
    Ok(cursor)
}
fn read_events(
    tx: &mut Transaction<'_>,
    board: &Board,
    after: i64,
    limit: i64,
    item: Option<&str>,
    messages: bool,
) -> Result<Value> {
    let cursor: i64 = tx
        .query_one("SELECT cursor FROM teams WHERE id=$1", &[&board.id])?
        .get(0);
    ensure!(
        after >= 0 && after <= cursor && (1..=50).contains(&limit),
        "Invalid team cursor or page size"
    );
    let rows=tx.query("SELECT cursor,kind,data FROM team_events WHERE team_id=$1 AND cursor>$2 ORDER BY cursor LIMIT $3",&[&board.id,&after,&limit])?;
    let mut next = after;
    let mut events = vec![];
    for row in rows {
        let seq: i64 = row.get(0);
        let kind: String = row.get(1);
        let data: Value = row.get(2);
        ensure!(seq == next + 1, "Team event history incomplete");
        next = seq;
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

impl Database {
    pub(crate) fn team_create(&mut self, request: Create) -> Result<Value> {
        crate::submissions::validate_id(&request.request_id, 100)?;
        let board = openworker_runtime::team_service::new_board(&request)?;
        let lead = board.lead.clone();
        let members = &board.members;
        let mut tx = self.client.transaction()?;
        let identity = auth::guard(
            &mut tx,
            self.auth.as_ref(),
            auth::Action::Control,
            Some(&lead),
            None,
        )?;
        let actor = identity
            .as_ref()
            .map_or("trusted-host", |p| p.principal.as_str());
        // Serialize duplicate creations before allocating membership.
        tx.query_one(
            "SELECT pg_advisory_xact_lock(hashtext($1))",
            &[&format!("team-create:{actor}:{}", request.request_id)],
        )?;
        let parameters = serde_json::to_value(&request)?;
        if let Some(row) = tx.query_opt(
            "SELECT id,parameters FROM teams WHERE actor=$1 AND request_id=$2",
            &[&actor, &request.request_id],
        )? {
            ensure!(row.get::<_, Value>(1) == parameters, Error::Conflict);
            let id: String = row.get(0);
            tx.commit()?;
            return Ok(json!({"team_id":id,"duplicate":true}));
        }
        let mut principals = std::collections::BTreeSet::new();
        for m in members.values() {
            auth::guard(
                &mut tx,
                self.auth.as_ref(),
                auth::Action::Control,
                Some(&m.session_id),
                None,
            )?;
            let s = db::locked(&mut tx, &m.session_id, None)?;
            openworker_runtime::team_service::fresh_member(&s, m.role)?;
            ensure!(
                !tx.query_one(
                    "SELECT EXISTS(SELECT 1 FROM team_members WHERE session_id=$1)",
                    &[&m.session_id]
                )?
                .get::<_, bool>(0),
                "Session already belongs to a team"
            );
            for row in tx.query(
                "SELECT principal FROM api_session_owners WHERE session_id=$1",
                &[&m.session_id],
            )? {
                principals.insert(row.get::<_, String>(0));
            }
        }
        ensure!(
            principals.len() <= 1,
            "Team members must share an application owner"
        );
        tx.execute(
            "INSERT INTO teams(id,request_id,actor,parameters,data) VALUES($1,$2,$3,$4,$5)",
            &[
                &board.id,
                &request.request_id,
                &actor,
                &parameters,
                &json!(board),
            ],
        )?;
        for sid in board.members.keys() {
            tx.execute("INSERT INTO team_members VALUES($1,$2)", &[&sid, &board.id])?;
        }
        let cursor = event(
            &mut tx,
            &board,
            "team_created",
            json!({"actor":actor,"members":board.members}),
        )?;
        auth::audit(
            &mut tx,
            identity.as_ref(),
            auth::Action::Control,
            Some(&board.lead),
            Some(&request.request_id),
        )?;
        tx.commit()?;
        Ok(json!({"team_id":board.id,"cursor":cursor,"duplicate":false}))
    }
    pub(crate) fn team_snapshot(&mut self, id: &str) -> Result<Value> {
        let mut tx = self.client.transaction()?;
        let board = load(&mut tx, id, false)?;
        auth::guard(
            &mut tx,
            self.auth.as_ref(),
            auth::Action::Read,
            Some(&board.lead),
            None,
        )?;
        // Row lock keeps the snapshot and cursor coherent with board writes.
        let board = load(&mut tx, id, true)?;
        let cursor: i64 = tx
            .query_one("SELECT cursor FROM teams WHERE id=$1", &[&id])?
            .get(0);
        let pending: bool = tx.query_one(
            "SELECT EXISTS(SELECT 1 FROM team_deliveries d JOIN submissions s ON s.session_id=d.session_id AND s.id=d.submission_id WHERE d.team_id=$1 AND s.status NOT IN ('completed','failed','cancelled','interrupted','truncated','blocked')) OR EXISTS(SELECT 1 FROM team_completions WHERE team_id=$1 AND NOT processed)",
            &[&id],
        )?.get(0);
        tx.commit()?;
        Ok(json!({"board":board,"cursor":cursor,"pending_work":pending}))
    }
    pub(crate) fn team_events(&mut self, id: &str, after: i64) -> Result<Value> {
        let mut tx = self.client.transaction()?;
        let board = load(&mut tx, id, true)?;
        auth::guard(
            &mut tx,
            self.auth.as_ref(),
            auth::Action::Read,
            Some(&board.lead),
            None,
        )?;
        let result = read_events(&mut tx, &board, after, 50, None, false)?;
        tx.commit()?;
        Ok(result)
    }
    pub(crate) fn team_command(&mut self, id: &str, command: Command) -> Result<Value> {
        crate::submissions::validate_id(&command.request_id, 100)?;
        let mut tx = self.client.transaction()?;
        let mut board = load(&mut tx, id, true)?;
        let identity = auth::guard(
            &mut tx,
            self.auth.as_ref(),
            auth::Action::Control,
            Some(&board.lead),
            None,
        )?;
        for sid in board.members.keys() {
            auth::guard(
                &mut tx,
                self.auth.as_ref(),
                auth::Action::Control,
                Some(sid),
                None,
            )?;
        }
        let actor = format!(
            "app:{}",
            identity
                .as_ref()
                .map_or("trusted-host", |p| p.principal.as_str())
        );
        let key = format!("app:{}", command.request_id);
        let raw = json!(command);
        if let Some(row) = tx.query_opt(
            "SELECT actor,request,result FROM team_commands WHERE team_id=$1 AND id=$2",
            &[&id, &key],
        )? {
            ensure!(
                row.get::<_, String>(0) == actor && row.get::<_, Value>(1) == raw,
                Error::Conflict
            );
            let result: Value = row.get(2);
            tx.commit()?;
            return Ok(result);
        }
        // Reads have dedicated endpoints. Bound idempotency records as well as
        // board events so app-issued read commands cannot grow storage forever.
        ensure!(
            tx.query_one(
                "SELECT count(*) FROM team_commands WHERE team_id=$1",
                &[&id]
            )?
            .get::<_, i64>(0)
                < 20000,
            Error::Limit
        );
        let result = if command.operation == "team_add_member" {
            ensure!(!board.cancelled, "Team cancelled");
            let member: Member = serde_json::from_value(command.arguments.clone())?;
            openworker_runtime::team_service::additional_member(&board, &member)?;
            auth::guard(
                &mut tx,
                self.auth.as_ref(),
                auth::Action::Control,
                Some(&member.session_id),
                None,
            )?;
            let session = db::locked(&mut tx, &member.session_id, None)?;
            openworker_runtime::team_service::fresh_member(&session, member.role)?;
            ensure!(
                !tx.query_one(
                    "SELECT EXISTS(SELECT 1 FROM team_members WHERE session_id=$1)",
                    &[&member.session_id]
                )?
                .get::<_, bool>(0),
                "Session already belongs to a team"
            );
            let owners = tx.query(
                "SELECT DISTINCT principal FROM api_session_owners WHERE session_id IN ($1,$2)",
                &[&board.lead, &member.session_id],
            )?;
            ensure!(
                owners.len() <= 1,
                "Team members must share an application owner"
            );
            tx.execute(
                "INSERT INTO team_members VALUES($1,$2)",
                &[&member.session_id, &id],
            )?;
            board
                .members
                .insert(member.session_id.clone(), member.clone());
            let cursor = event(
                &mut tx,
                &board,
                "member_added",
                json!({"actor":actor,"member":member}),
            )?;
            json!({"cursor":cursor,"member":member})
        } else if command.operation == "team_cancel" {
            ensure!(
                command.arguments == json!({}),
                "Cancel accepts no arguments"
            );
            crate::team_execution::cancel(&mut tx, &mut board, &actor)?;
            let cursor = event(&mut tx, &board, "team_cancelled", json!({"actor":actor}))?;
            json!({"cursor":cursor,"cancelled":true})
        } else {
            openworker_runtime::team_service::operate(
                &mut PgTeamTx(&mut tx),
                &mut board,
                &actor,
                Role::Lead,
                &command.operation,
                &command.arguments,
                self.max_queued_turns,
            )?
        };
        tx.execute(
            "INSERT INTO team_commands VALUES($1,$2,$3,$4,$5)",
            &[&id, &key, &actor, &raw, &result],
        )?;
        auth::audit(
            &mut tx,
            identity.as_ref(),
            auth::Action::Control,
            Some(&board.lead),
            Some(&command.request_id),
        )?;
        tx.commit()?;
        Ok(result)
    }
    pub(crate) fn team_context(
        &mut self,
        sid: &str,
    ) -> Result<Option<openworker_runtime::teams::Context>> {
        let mut tx = self.client.transaction()?;
        if !present(&mut tx)? {
            return Ok(None);
        }
        let result = if let Some(row) = tx.query_opt(
            "SELECT team_id FROM team_members WHERE session_id=$1",
            &[&sid],
        )? {
            let id: String = row.get(0);
            let board = load(&mut tx, &id, false)?;
            let m = board
                .members
                .get(sid)
                .context("Team membership inconsistent")?;
            let delivery=tx.query_opt("SELECT d.item_id,d.assignment FROM team_deliveries d JOIN sessions s ON s.id=d.session_id WHERE d.session_id=$1 AND d.submission_id=s.data->'turn'->>'submission_id'",&[&sid])?;
            Some(openworker_runtime::teams::Context {
                team_id: id,
                role: m.role,
                session_id: sid.into(),
                active_item: delivery.as_ref().and_then(|r| r.get(0)),
                assignment: delivery.as_ref().and_then(|r| r.get(1)),
            })
        } else {
            None
        };
        tx.commit()?;
        Ok(result)
    }
    pub(crate) fn team_call(
        &mut self,
        sid: &str,
        tid: &str,
        call: Value,
        args: Value,
    ) -> Result<Event> {
        let epoch = self.epoch(sid)?;
        let mut tx = self.client.transaction()?;
        let team: String = tx
            .query_opt(
                "SELECT team_id FROM team_members WHERE session_id=$1",
                &[&sid],
            )?
            .context("Session is not in a team")?
            .get(0);
        let mut board = load(&mut tx, &team, true)?;
        let mut s = db::locked(&mut tx, sid, Some((&self.owner, epoch)))?;
        db::read_history(&mut tx, &mut s)?;
        let t = s.turn.as_ref().context("No turn")?;
        ensure!(
            t.id == tid && t.status == "running" && t.queue.first() == Some(&call),
            "Team call stale or cancelled"
        );
        let role = board
            .members
            .get(sid)
            .context("Team membership unavailable")?
            .role;
        ensure!(s.package.team_role == Some(role), "Coworker role changed");
        let name = call["name"].as_str().context("Tool name missing")?;
        crate::controls::before_event(&mut tx, &s, "tool_started", &json!({}))?;
        let key = format!(
            "agent:{sid}:{tid}:{}",
            call["call_id"].as_str().context("Call ID missing")?
        );
        let output = {
            let mut operation = tx.transaction()?;
            match openworker_runtime::team_service::operate(
                &mut PgTeamTx(&mut operation),
                &mut board,
                sid,
                role,
                name,
                &args,
                self.max_queued_turns,
            ) {
                Ok(v) => {
                    operation.commit()?;
                    v
                }
                Err(e) => {
                    operation.rollback()?;
                    if e.downcast_ref::<postgres::Error>().is_some() {
                        return Err(e);
                    }
                    json!({"isError":true,"message":e.to_string()})
                }
            }
        };
        tx.execute(
            "INSERT INTO team_commands VALUES($1,$2,$3,$4,$5)",
            &[&team, &key, &sid, &call, &output],
        )?;
        if !openworker_runtime::safety::hold_internal_result(&mut s, &call) {
            s.history.push(json!({"type":"function_call_output","call_id":call["call_id"],"output":output.to_string()}));
            s.turn.as_mut().unwrap().queue.remove(0);
        }
        db::append_history(&mut tx, &s)?;
        db::write_metadata(&mut tx, &s)?;
        let mut event = db::event(
            &mut tx,
            &s,
            &self.owner,
            epoch,
            "tool_finished",
            openworker_runtime::safety::public_internal_event(
                &s,
                json!({"name":name,"call_id":call["call_id"],"result":output,"execution_kind":"internal","team_id":team,"enforcement":"team-role-and-session-scope"}),
            ),
        )?;
        event.data["result"] = output;
        tx.commit()?;
        Ok(event)
    }
}
impl PostgresStore {
    pub fn create_team(&self, request: Create) -> Result<Value> {
        self.call(move |db| db.team_create(request).map_err(request_error))
    }
    pub fn team_snapshot(&self, id: &str) -> Result<Value> {
        let id = id.to_owned();
        self.call(move |db| db.team_snapshot(&id).map_err(request_error))
    }
    pub fn team_events(&self, id: &str, after: i64) -> Result<Value> {
        let id = id.to_owned();
        self.call(move |db| db.team_events(&id, after).map_err(request_error))
    }
    pub fn team_command(&self, id: &str, command: Command) -> Result<Value> {
        let id = id.to_owned();
        self.call(move |db| db.team_command(&id, command).map_err(request_error))
    }
}

struct PgTeamTx<'a, 'b>(&'a mut Transaction<'b>);
impl openworker_runtime::team_service::Transaction for PgTeamTx<'_, '_> {
    fn read_events(
        &mut self,
        b: &Board,
        a: i64,
        l: i64,
        i: Option<&str>,
        m: bool,
    ) -> Result<Value> {
        read_events(self.0, b, a, l, i, m)
    }
    fn assignment_finished(&mut self, i: &Item) -> Result<()> {
        crate::team_execution::assignment_finished(self.0, i)
    }
    fn current_assignment(&mut self, s: &str, i: &Item) -> Result<()> {
        crate::team_execution::current_assignment(self.0, s, i)
    }
    fn deliver(
        &mut self,
        b: &mut Board,
        s: &str,
        i: Option<&Item>,
        t: &str,
        l: u16,
    ) -> Result<String> {
        crate::team_execution::deliver(self.0, b, s, i, t, l)
    }
    fn event(&mut self, b: &Board, k: &str, d: Value) -> Result<i64> {
        event(self.0, b, k, d)
    }
}
