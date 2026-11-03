use super::*;
pub(super) fn initialize(db: &Connection) -> Result<()> {
    use rusqlite::functions::FunctionFlags;
    db.create_scalar_function("ow_observer_hooks", 2, FunctionFlags::SQLITE_UTF8, |ctx| {
        let raw: String = ctx.get(0)?;
        let s: Session = serde_json::from_str(&raw)
            .map_err(|e| rusqlite::Error::UserFunctionError(Box::new(e)))?;
        let turn_id: Option<String> = ctx.get(1)?;
        let config = s
            .safety
            .merge(
                &s.turn
                    .filter(|t| Some(&t.id) == turn_id.as_ref())
                    .map(|t| t.safety)
                    .unwrap_or_default(),
            )
            .map_err(|_| {
                rusqlite::Error::UserFunctionError("Required observer unavailable".into())
            })?;
        Ok(json!(config
            .hooks
            .into_iter()
            .filter(|h| h.when == crate::safety::HookPhase::OnEvent)
            .collect::<Vec<_>>())
        .to_string())
    })?;
    db.create_scalar_function(
        "ow_observer_project",
        3,
        FunctionFlags::SQLITE_UTF8,
        |ctx| {
            let s: String = ctx.get(0)?;
            let k: String = ctx.get(1)?;
            let d: String = ctx.get(2)?;
            let parse = |v: &str| {
                serde_json::from_str::<Value>(v)
                    .map_err(|e| rusqlite::Error::UserFunctionError(Box::new(e)))
            };
            Ok(crate::compliance::project(&parse(&s)?, &k, &parse(&d)?).to_string())
        },
    )?;
    #[cfg(feature="guardrails")]
    db.execute_batch("CREATE TABLE IF NOT EXISTS safety_observers(session_id TEXT NOT NULL,cursor INTEGER NOT NULL,hook_name TEXT NOT NULL,hook_version TEXT NOT NULL,hook TEXT NOT NULL,record TEXT NOT NULL,turn_id TEXT,next_ms INTEGER NOT NULL DEFAULT 0,attempts INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(session_id,cursor,hook_name,hook_version));
    DROP TRIGGER IF EXISTS safety_observer_capture;
    CREATE TRIGGER safety_observer_capture AFTER INSERT ON events
    WHEN NEW.kind NOT IN ('assistant_delta','tool_progress','agent_run_state','observer_delivery')
    BEGIN
      INSERT OR IGNORE INTO safety_observers(session_id,cursor,hook_name,hook_version,hook,record,turn_id)
      SELECT NEW.session_id,NEW.id,json_extract(h.value,'$.name'),json_extract(h.value,'$.version'),h.value,ow_observer_project(s.data,NEW.kind,NEW.data),NEW.turn_id
      FROM sessions s,json_each(ow_observer_hooks(s.data,NEW.turn_id)) h WHERE s.id=NEW.session_id;
    END;")?;
    Ok(())
}
impl Store {
    pub fn pending_observers(&self, sid: &str) -> Result<Vec<Value>> {
        #[cfg(not(feature = "guardrails"))]
        {
            let _ = sid;
            Ok(vec![])
        }
        #[cfg(feature = "guardrails")]
        {
            self.metadata(sid)?;
            let mut q=self.db.prepare("SELECT cursor,hook_name,hook_version,hook,record,turn_id,attempts,next_ms FROM safety_observers WHERE session_id=?1 ORDER BY next_ms,cursor,hook_name LIMIT 64")?;
            let rows = q.query_map(params![sid], |r| {
                Ok((
                    r.get::<_, i64>(0)?,
                    r.get::<_, String>(1)?,
                    r.get::<_, String>(2)?,
                    r.get::<_, String>(3)?,
                    r.get::<_, String>(4)?,
                    r.get::<_, Option<String>>(5)?,
                    r.get::<_, u32>(6)?,
                    r.get::<_, i64>(7)?,
                ))
            })?;
            rows.map(|r|{let (cursor,name,version,hook,record,turn,attempts,next)=r?;Ok(json!({"cursor":cursor,"name":name,"version":version,"hook":serde_json::from_str::<Value>(&hook)?,"record":serde_json::from_str::<Value>(&record)?,"turnId":turn,"attempts":attempts,"nextMs":next}))}).collect()
        }
    }
    pub fn settle_observer(&self, sid: &str, receipt: &Value, success: bool) -> Result<()> {
        self.metadata(sid)?;
        let cursor = receipt["cursor"]
            .as_i64()
            .context("Invalid observer cursor")?;
        let name = receipt["name"].as_str().context("Invalid observer name")?;
        let version = receipt["version"]
            .as_str()
            .context("Invalid observer version")?;
        if success {
            self.db.execute("DELETE FROM safety_observers WHERE session_id=?1 AND cursor=?2 AND hook_name=?3 AND hook_version=?4",params![sid,cursor,name,version])?;
        } else {
            self.db.execute("UPDATE safety_observers SET attempts=MIN(attempts+1,30),next_ms=?5+MIN(300000,1000*(1<<MIN(attempts,8))) WHERE session_id=?1 AND cursor=?2 AND hook_name=?3 AND hook_version=?4",params![sid,cursor,name,version,now_ms() as i64])?;
        }
        Ok(())
    }
}
