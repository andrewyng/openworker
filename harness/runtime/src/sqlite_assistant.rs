use super::*;
use crate::assistant_tools::{self, MemoryBank};
use rusqlite::OptionalExtension;
pub(super) fn migrate(db: &Connection) -> Result<()> {
    let exists:bool=db.query_row("SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE type='table' AND name='assistant_version')",[],|r|r.get(0))?;
    if exists {
        let versions = db
            .prepare("SELECT version FROM assistant_version")?
            .query_map([], |r| r.get::<_, i64>(0))?
            .collect::<rusqlite::Result<Vec<_>>>()?;
        anyhow::ensure!(versions == [1], "Unsupported assistant state schema");
        return Ok(());
    }
    db.execute_batch("BEGIN IMMEDIATE; CREATE TABLE assistant_version(version INTEGER NOT NULL); INSERT INTO assistant_version VALUES(1); CREATE TABLE assistant_memories(scope TEXT PRIMARY KEY,data TEXT NOT NULL); COMMIT;")?;
    Ok(())
}
impl Store {
    pub fn assistant_call(
        &mut self,
        sid: &str,
        tid: &str,
        call: &Value,
        args: &Value,
    ) -> Result<Event> {
        let mut s = self.get(sid)?;
        let scope = assistant_tools::scope(&s);
        let tx = self.db.transaction()?;
        let old: Option<String> = tx
            .query_row(
                "SELECT data FROM assistant_memories WHERE scope=?",
                [&scope],
                |r| r.get(0),
            )
            .optional()?;
        let mut bank: MemoryBank = old
            .map(|v| serde_json::from_str(&v))
            .transpose()?
            .unwrap_or_default();
        let data = assistant_tools::finish(&mut s, &mut bank, tid, call, args)?;
        if call["name"]
            .as_str()
            .is_some_and(|n| n.starts_with("memory_"))
        {
            tx.execute("INSERT INTO assistant_memories VALUES(?,?) ON CONFLICT(scope) DO UPDATE SET data=excluded.data",params![scope,serde_json::to_string(&bank)?])?;
        }
        Self::append_history(&tx, &s)?;
        tx.execute(
            "UPDATE sessions SET data=? WHERE id=?",
            params![serde_json::to_string(&s)?, sid],
        )?;
        tx.execute(
            "INSERT INTO events(session_id,turn_id,kind,data) VALUES(?,?,?,?)",
            params![
                sid,
                tid,
                "tool_finished",
                crate::safety::public_internal_event(&s, data.clone()).to_string()
            ],
        )?;
        let event = Event {
            event_id: tx.last_insert_rowid(),
            session_id: sid.into(),
            turn_id: Some(tid.into()),
            kind: "tool_finished".into(),
            data,
        };
        tx.commit()?;
        Ok(event)
    }
}
