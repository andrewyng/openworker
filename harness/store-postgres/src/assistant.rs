use crate::db::{self, Database};
use anyhow::{ensure, Result};
use openworker_runtime::{
    assistant_tools::{self, MemoryBank},
    store::Event,
};
use serde_json::{json, Value};
pub(crate) fn migrate(tx: &mut postgres::Transaction<'_>) -> Result<()> {
    let exists: bool = tx
        .query_one("SELECT to_regclass('assistant_version') IS NOT NULL", &[])?
        .get(0);
    if exists {
        let rows = tx.query("SELECT version FROM assistant_version WHERE singleton", &[])?;
        ensure!(
            rows.len() == 1 && rows[0].get::<_, i32>(0) == 1,
            "Unsupported assistant state schema"
        );
    } else {
        tx.batch_execute("CREATE TABLE assistant_version(singleton BOOLEAN PRIMARY KEY CHECK(singleton),version INTEGER NOT NULL); INSERT INTO assistant_version VALUES(TRUE,1); CREATE TABLE assistant_memories(scope TEXT PRIMARY KEY,data JSONB NOT NULL);")?;
    }
    Ok(())
}
impl Database {
    pub(crate) fn assistant_call(
        &mut self,
        sid: &str,
        tid: &str,
        call: &Value,
        args: &Value,
    ) -> Result<Event> {
        let epoch = self.epoch(sid)?;
        let mut tx = self.client.transaction()?;
        let version = tx.query("SELECT version FROM assistant_version WHERE singleton", &[])?;
        ensure!(
            version.len() == 1 && version[0].get::<_, i32>(0) == 1,
            "Unsupported assistant state schema; migrate explicitly"
        );
        let mut s = db::locked(&mut tx, sid, Some((&self.owner, epoch)))?;
        db::read_history(&mut tx, &mut s)?;
        crate::controls::before_event(&mut tx, &s, "tool_started", &json!({}))?;
        let memory = call["name"]
            .as_str()
            .is_some_and(|n| n.starts_with("memory_"));
        let scope = assistant_tools::scope(&s);
        let mut bank = MemoryBank::default();
        if memory {
            tx.execute(
                "INSERT INTO assistant_memories VALUES($1,$2) ON CONFLICT DO NOTHING",
                &[&scope, &serde_json::to_value(&bank)?],
            )?;
            bank = serde_json::from_value(
                tx.query_one(
                    "SELECT data FROM assistant_memories WHERE scope=$1 FOR UPDATE",
                    &[&scope],
                )?
                .get(0),
            )?;
        }
        let data = assistant_tools::finish(&mut s, &mut bank, tid, call, args)?;
        if memory {
            tx.execute(
                "UPDATE assistant_memories SET data=$2 WHERE scope=$1",
                &[&scope, &serde_json::to_value(&bank)?],
            )?;
        }
        db::append_history(&mut tx, &s)?;
        db::write_metadata(&mut tx, &s)?;
        let mut event = db::event(
            &mut tx,
            &s,
            &self.owner,
            epoch,
            "tool_finished",
            openworker_runtime::safety::public_internal_event(&s, data.clone()),
        )?;
        event.data = data;
        tx.commit()?;
        Ok(event)
    }
}
