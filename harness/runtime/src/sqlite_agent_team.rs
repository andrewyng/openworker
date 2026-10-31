use super::*;
use crate::agent_team::{self, Document, Message, Transaction};
use rusqlite::OptionalExtension;
pub(super) fn migrate(db: &Connection) -> Result<()> {
    db.execute_batch("CREATE TABLE IF NOT EXISTS agent_teams(id TEXT PRIMARY KEY,data TEXT NOT NULL); CREATE TABLE IF NOT EXISTS agent_team_messages(team TEXT NOT NULL,seq INTEGER NOT NULL,id TEXT NOT NULL UNIQUE,data TEXT NOT NULL,PRIMARY KEY(team,seq));")?;
    Ok(())
}
struct Tx<'a>(&'a Connection);
impl Transaction for Tx<'_> {
    fn load(&mut self, id: &str) -> Result<Document> {
        Ok(serde_json::from_str(&self.0.query_row(
            "SELECT data FROM agent_teams WHERE id=?",
            [id],
            |r| r.get::<_, String>(0),
        )?)?)
    }
    fn write(&mut self, d: &Document, creating: bool) -> Result<()> {
        if creating {
            self.0.execute(
                "INSERT INTO agent_teams VALUES(?,?)",
                params![d.id, serde_json::to_string(d)?],
            )?;
        } else {
            anyhow::ensure!(
                self.0.execute(
                    "UPDATE agent_teams SET data=? WHERE id=?",
                    params![serde_json::to_string(d)?, d.id]
                )? == 1,
                "Missing team"
            );
        }
        Ok(())
    }
    fn append(&mut self, team: &str, m: &Message) -> Result<()> {
        self.0.execute(
            "INSERT INTO agent_team_messages VALUES(?,?,?,?)",
            params![team, m.sequence, m.id, serde_json::to_string(m)?],
        )?;
        Ok(())
    }
    fn messages(
        &mut self,
        team: &str,
        after: u64,
        before: Option<u64>,
        recipient: Option<&str>,
        board: bool,
        reverse: bool,
        limit: usize,
    ) -> Result<Vec<Message>> {
        let order = if reverse { "DESC" } else { "ASC" };
        let sql=format!("SELECT data FROM agent_team_messages WHERE team=?1 AND seq>?2 AND (?3 IS NULL OR seq<?3) AND (?4 IS NULL OR EXISTS(SELECT 1 FROM json_each(json_extract(data,'$.recipients')) WHERE value=?4)) AND (?5=0 OR json_extract(data,'$.board')=1) ORDER BY seq {order} LIMIT ?6");
        let rows = self
            .0
            .prepare(&sql)?
            .query_map(
                params![team, after, before, recipient, board, limit as i64],
                |r| r.get::<_, String>(0),
            )?
            .collect::<rusqlite::Result<Vec<_>>>()?;
        rows.into_iter()
            .map(|v| Ok(serde_json::from_str(&v)?))
            .collect()
    }
    fn anchor(&mut self, team: &str, id: &str) -> Result<u64> {
        self.0.query_row("SELECT seq FROM agent_team_messages WHERE team=? AND id=? AND json_extract(data,'$.board')=1",params![team,id],|r|r.get(0)).optional()?.context("Unknown board message ID")
    }
    fn session(&mut self, id: &str) -> Result<Session> {
        Ok(serde_json::from_str(&self.0.query_row(
            "SELECT data FROM sessions WHERE id=?",
            [id],
            |r| r.get::<_, String>(0),
        )?)?)
    }
}
impl Store {
    pub fn agent_team(
        &mut self,
        sid: &str,
        operation: &str,
        args: &Value,
        call: Option<(&str, &Value)>,
    ) -> Result<Value> {
        let mut s = if call.is_some() {
            self.get(sid)?
        } else {
            self.metadata(sid)?
        };
        let tx = self
            .db
            .transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
        let before = serde_json::to_value(&s)?;
        let result = agent_team::apply(&mut Tx(&tx), &mut s, operation, args, now_ms())?;
        if call.is_none()
            && (matches!(
                operation,
                "snapshot" | "pending" | "pending_inbox" | "team_read_board"
            ) || (operation == "wake" && result["resume"] != true)
                || (operation == "deliver"
                    && result["messages"].as_array().is_some_and(Vec::is_empty)
                    && result["wait_notices"] == 0))
        {
            tx.commit()?;
            return Ok(result);
        }
        if let Some((tid, call)) = call {
            finish(&mut s, tid, call, &result)?;
        }
        if call.is_none() && !s.history.is_empty() {
            let pending = std::mem::take(&mut s.history);
            let rows = tx
                .prepare("SELECT data FROM history WHERE session_id=? ORDER BY seq")?
                .query_map([sid], |r| r.get::<_, String>(0))?
                .collect::<rusqlite::Result<Vec<_>>>()?;
            s.history = rows
                .into_iter()
                .map(|raw| serde_json::from_str(&raw))
                .collect::<serde_json::Result<_>>()?;
            s.history.extend(pending);
        }
        if call.is_some() || !s.history.is_empty() {
            Self::append_history(&tx, &s)?;
        }

        if call.is_some() || before != serde_json::to_value(&s)? {
            tx.execute(
                "UPDATE sessions SET data=? WHERE id=?",
                params![serde_json::to_string(&s)?, sid],
            )?;
        }
        tx.execute("INSERT INTO events(session_id,turn_id,kind,data) VALUES(?,?,?,?)",params![sid,s.turn.as_ref().map(|t|&t.id),"agent_team",json!({"operation":operation,"result":result,"call_id":call.map(|(_,c)|&c["call_id"])}).to_string()])?;
        tx.commit()?;
        Ok(result)
    }
}
pub(crate) fn finish(s: &mut Session, tid: &str, call: &Value, result: &Value) -> Result<()> {
    crate::agent_team::finish(s, tid, call, result)
}
