use super::*;

pub(super) fn initialize(db: &Connection) -> Result<()> {
    use rusqlite::functions::FunctionFlags;
    // Keep the tiny validation/projection function available when opening an
    // existing store from a minimal build. It rejects enabled capture there.
    db.create_scalar_function(
        "ow_compliance_project",
        3,
        FunctionFlags::SQLITE_UTF8 | FunctionFlags::SQLITE_DETERMINISTIC,
        |ctx| {
            if !cfg!(feature = "compliance") {
                return Err(rusqlite::Error::UserFunctionError(
                    "Compliance feature unavailable".into(),
                ));
            }
            let session: String = ctx.get(0)?;
            let kind: String = ctx.get(1)?;
            let data: String = ctx.get(2)?;
            let parse = |text: &str| {
                serde_json::from_str::<Value>(text)
                    .map_err(|e| rusqlite::Error::UserFunctionError(Box::new(e)))
            };
            Ok(crate::compliance::project(&parse(&session)?, &kind, &parse(&data)?).to_string())
        },
    )?;
    #[cfg(feature="compliance")]
    db.execute_batch("BEGIN IMMEDIATE;
      CREATE TABLE IF NOT EXISTS compliance_delivery(session_id TEXT NOT NULL,sink TEXT NOT NULL,data TEXT NOT NULL,PRIMARY KEY(session_id,sink));
      CREATE TABLE IF NOT EXISTS compliance_events(
        session_id TEXT NOT NULL,source_cursor INTEGER NOT NULL,turn_id TEXT,
        created_ms INTEGER NOT NULL,kind TEXT NOT NULL,record TEXT NOT NULL,
        PRIMARY KEY(session_id,source_cursor));
      CREATE INDEX IF NOT EXISTS compliance_kind ON compliance_events(session_id,kind,source_cursor);
      CREATE TRIGGER IF NOT EXISTS compliance_backpressure BEFORE INSERT ON events
      WHEN NEW.kind IN ('turn_accepted','model_request','tool_started') AND EXISTS(
        SELECT 1 FROM sessions s WHERE s.id=NEW.session_id AND json_type(s.data,'$.compliance')='object'
        AND (SELECT count(*) FROM compliance_events e WHERE e.session_id=NEW.session_id AND EXISTS(SELECT 1 FROM compliance_delivery d WHERE d.session_id=e.session_id AND json_extract(d.data,'$.cursor')<e.source_cursor))>=COALESCE(json_extract(s.data,'$.compliance.maxPendingEvents'),10000))
      BEGIN SELECT RAISE(ABORT,'Compliance export backlog full'); END;
      CREATE TRIGGER IF NOT EXISTS compliance_capture AFTER INSERT ON events
      WHEN NEW.kind NOT IN ('assistant_delta','tool_progress','agent_run_state')
        AND EXISTS(SELECT 1 FROM sessions WHERE id=NEW.session_id AND json_type(data,'$.compliance')='object')
      BEGIN
        INSERT INTO compliance_events VALUES(NEW.session_id,NEW.id,NEW.turn_id,
          CAST((julianday('now')-2440587.5)*86400000 AS INTEGER),NEW.kind,
          ow_compliance_project((SELECT data FROM sessions WHERE id=NEW.session_id),NEW.kind,NEW.data));
      END;
      COMMIT;")?;
    Ok(())
}

impl Store {
    pub fn compliance_page(&self, sid: &str, q: crate::compliance::Query) -> Result<Value> {
        anyhow::ensure!(
            cfg!(feature = "compliance"),
            "Compliance is not included in this build"
        );
        q.validate()?;
        let session = self.metadata(sid)?;
        anyhow::ensure!(
            session.compliance.is_some(),
            "Compliance capture is not enabled for this session"
        );
        let cutoff = now_ms() as i64
            - i64::from(session.compliance.as_ref().unwrap().retention_days) * 86_400_000;
        let mut sql=self.db.prepare("SELECT source_cursor,turn_id,created_ms,record FROM compliance_events WHERE session_id=?1 AND created_ms>=?6 AND source_cursor>?2 AND (?3 IS NULL OR kind=?3) AND (?4 IS NULL OR json_extract(record,'$.facts.outcome')=?4) ORDER BY source_cursor LIMIT ?5")?;
        let rows = sql
            .query_map(
                params![
                    sid,
                    q.after,
                    q.kind,
                    q.outcome,
                    (q.limit() + 1) as i64,
                    cutoff
                ],
                |r| {
                    Ok((
                        r.get::<_, i64>(0)?,
                        r.get::<_, Option<String>>(1)?,
                        r.get::<_, i64>(2)?,
                        r.get::<_, String>(3)?,
                    ))
                },
            )?
            .collect::<rusqlite::Result<Vec<_>>>()?;
        let more = rows.len() > q.limit();
        let mut cursor = q.after;
        let mut activities = vec![];
        for (id, turn, at, raw) in rows.into_iter().take(q.limit()) {
            let mut value: Value = serde_json::from_str(&raw)?;
            value["id"] = json!(format!("{sid}:{id}"));
            value["sessionId"] = json!(sid);
            value["turnId"] = json!(turn);
            value["createdAtMs"] = json!(at);
            activities.push(value);
            cursor = id;
        }
        Ok(json!({"activities":activities,"nextAfter":cursor,"hasMore":more}))
    }
}

#[cfg(all(test, feature = "compliance"))]
mod tests {
    use super::*;
    fn session(id: &str) -> Session {
        serde_json::from_value(json!({"id":id,"name":"test","workspace":"/work","coworker":"test","bundle_digest":"b","schema_digest":null,"interactions":[],"turn":null,"sandbox":null,"closed":false,"compliance":{},"resource_policy":crate::resource_policy::Resolved::default()})).unwrap()
    }
    #[test]
    fn evidence_commits_atomically_and_survives_reopen_without_payloads() {
        let tmp = tempfile::tempdir().unwrap();
        let mut store = Store::open(tmp.path()).unwrap();
        let s = session("s_compliance");
        store
            .save(&s, "session_created", json!({"text":"PRIVATE"}))
            .unwrap();
        store
            .append_event(&s.id, "assistant_delta", json!({"text":"PRIVATE"}))
            .unwrap();
        let e=store.append_event(&s.id,"safety_evaluation",json!({"outcome":"blocked","phase":"model_output","code":"PII","text":"PRIVATE","arguments":{"secret":"PRIVATE"}})).unwrap();
        let q = crate::compliance::Query {
            limit: 1,
            ..Default::default()
        };
        let first = store.compliance_page(&s.id, q).unwrap();
        assert_eq!(first["hasMore"], true);
        let cursor = first["nextAfter"].as_i64().unwrap();
        assert!(!first.to_string().contains("PRIVATE"));
        store.db.execute_batch("CREATE TRIGGER deny_evidence BEFORE INSERT ON compliance_events BEGIN SELECT RAISE(ABORT,'test audit failure'); END;").unwrap();
        let before = store.cursor(&s.id).unwrap();
        assert!(store
            .append_event(&s.id, "tool_started", json!({"call_id":"c"}))
            .is_err());
        assert_eq!(store.cursor(&s.id).unwrap(), before);
        store
            .db
            .execute_batch("DROP TRIGGER deny_evidence;")
            .unwrap();
        drop(store);
        let store = Store::open(tmp.path()).unwrap();
        let page = store
            .compliance_page(
                &s.id,
                crate::compliance::Query {
                    after: cursor,
                    ..Default::default()
                },
            )
            .unwrap();
        assert_eq!(page["activities"].as_array().unwrap().len(), 1);
        assert_eq!(
            page["activities"][0]["id"],
            format!("{}:{}", s.id, e.event_id)
        );
        assert_eq!(page["activities"][0]["facts"]["phase"], "model_output");
        assert_eq!(page["hasMore"], false);
        assert!(!page.to_string().contains("PRIVATE"));
    }
}

impl Store {
    /// Trusted maintenance; canonical execution history has its own lifecycle.
    /// No model-facing tool can shorten the installed retention policy.
    pub fn prune_compliance(&self) -> Result<u64> {
        anyhow::ensure!(
            cfg!(feature = "compliance"),
            "Compliance feature unavailable"
        );
        let deleted=self.db.execute("DELETE FROM compliance_events WHERE NOT EXISTS(SELECT 1 FROM compliance_delivery d WHERE d.session_id=compliance_events.session_id AND json_extract(d.data,'$.cursor')<compliance_events.source_cursor) AND created_ms < ?1 - 86400000 * COALESCE((SELECT json_extract(data,'$.compliance.retentionDays') FROM sessions WHERE id=compliance_events.session_id),3650)",[now_ms() as i64])?;
        Ok(deleted as u64)
    }
}

impl Store {
    pub fn claim_compliance_export(
        &mut self,
        sid: &str,
        sink: &str,
        binding: &str,
        token: &str,
    ) -> Result<Option<crate::compliance::delivery::Batch>> {
        use crate::compliance::delivery::{Batch, Checkpoint};
        anyhow::ensure!(
            cfg!(feature = "compliance")
                && crate::compliance::identifier(sink)
                && binding.len() <= 128,
            "Invalid compliance sink"
        );
        let s = self.metadata(sid)?;
        anyhow::ensure!(s.compliance.is_some(), "Compliance disabled");
        let tx = self.db.transaction()?;
        let raw: Option<String> = tx
            .query_row(
                "SELECT data FROM compliance_delivery WHERE session_id=?1 AND sink=?2",
                params![sid, sink],
                |r| r.get(0),
            )
            .optional()?;
        if raw.is_none() {
            let count: i64 = tx.query_row(
                "SELECT count(*) FROM compliance_delivery WHERE session_id=?1",
                [sid],
                |r| r.get(0),
            )?;
            anyhow::ensure!(count < 64, "Compliance sink limit reached");
        }
        let mut c: Checkpoint = raw
            .map(|v| serde_json::from_str(&v))
            .transpose()?
            .unwrap_or_else(|| Checkpoint::new(binding.into()));
        anyhow::ensure!(c.binding == binding, "Export destination changed");
        let mut q=tx.prepare("SELECT source_cursor,turn_id,created_ms,record FROM compliance_events WHERE session_id=?1 AND source_cursor>?2 ORDER BY source_cursor LIMIT 100")?;
        let rows = q
            .query_map(params![sid, c.cursor], |r| {
                Ok((
                    r.get::<_, i64>(0)?,
                    r.get::<_, Option<String>>(1)?,
                    r.get::<_, i64>(2)?,
                    r.get::<_, String>(3)?,
                ))
            })?
            .collect::<rusqlite::Result<Vec<_>>>()?;
        drop(q);
        let mut activities = vec![];
        let mut through = c.cursor;
        for (id, turn, created, raw) in rows {
            let mut v: Value = serde_json::from_str(&raw)?;
            v["id"] = json!(format!("{sid}:{id}"));
            v["sessionId"] = json!(sid);
            v["turnId"] = json!(turn);
            v["createdAtMs"] = json!(created);
            activities.push(v);
            through = id;
        }
        let claimed = c.claim(binding, token, now_ms(), through)?;
        tx.execute("INSERT INTO compliance_delivery VALUES(?1,?2,?3) ON CONFLICT(session_id,sink) DO UPDATE SET data=excluded.data",params![sid,sink,serde_json::to_string(&c)?])?;
        tx.commit()?;
        Ok(claimed.then(|| Batch {
            token: token.into(),
            session_id: sid.into(),
            sink: sink.into(),
            activities,
        }))
    }
    pub fn settle_compliance_export(
        &mut self,
        sid: &str,
        sink: &str,
        token: &str,
        success: bool,
    ) -> Result<()> {
        let tx = self.db.transaction()?;
        let raw: String = tx.query_row(
            "SELECT data FROM compliance_delivery WHERE session_id=?1 AND sink=?2",
            params![sid, sink],
            |r| r.get(0),
        )?;
        let mut c: crate::compliance::delivery::Checkpoint = serde_json::from_str(&raw)?;
        c.settle(token, now_ms(), success)?;
        tx.execute(
            "UPDATE compliance_delivery SET data=?3 WHERE session_id=?1 AND sink=?2",
            params![sid, sink, serde_json::to_string(&c)?],
        )?;
        tx.commit()?;
        Ok(())
    }
    pub fn compliance_delivery_health(&self, sid: &str) -> Result<Value> {
        self.metadata(sid)?;
        let mut q=self.db.prepare("SELECT sink,data,(SELECT count(*) FROM compliance_events e WHERE e.session_id=d.session_id AND e.source_cursor>json_extract(d.data,'$.cursor')) FROM compliance_delivery d WHERE session_id=?1 ORDER BY sink LIMIT 64")?;
        let rows = q.query_map([sid], |r| {
            Ok((
                r.get::<_, String>(0)?,
                r.get::<_, String>(1)?,
                r.get::<_, i64>(2)?,
            ))
        })?;
        let mut v = vec![];
        for row in rows {
            let (sink, data, pending) = row?;
            let c: crate::compliance::delivery::Checkpoint = serde_json::from_str(&data)?;
            v.push(json!({"sink":sink,"cursor":c.cursor,"pending":pending,"failures":c.failures,"nextAtMs":c.next_at_ms,"lastSuccessMs":c.last_success_ms}));
        }
        Ok(json!({"sinks":v}))
    }
}

#[cfg(all(test, feature = "compliance"))]
mod export_tests {
    use super::*;
    #[test]
    fn export_retry_reopen_retention_and_cursor_are_independent_of_execution() {
        let tmp = tempfile::tempdir().unwrap();
        let mut db = Store::open(tmp.path()).unwrap();
        let s:Session=serde_json::from_value(json!({"id":"s_export","name":"test","workspace":"/work","coworker":"test","bundle_digest":"b","schema_digest":null,"interactions":[],"turn":null,"sandbox":null,"closed":false,"compliance":{"retentionDays":1},"resource_policy":crate::resource_policy::Resolved::default()})).unwrap();
        db.save(&s, "session_created", json!({})).unwrap();
        for i in 0..103 {
            db.append_event(
                &s.id,
                "tool_finished",
                json!({"call_id":format!("call-{i}"),"text":"PRIVATE"}),
            )
            .unwrap();
        }
        let first = db
            .claim_compliance_export(&s.id, "siem-v1", "binding", "claim-one")
            .unwrap()
            .unwrap();
        assert_eq!(first.activities.len(), 100);
        assert!(!json!(first.activities).to_string().contains("PRIVATE"));
        assert!(db
            .claim_compliance_export(&s.id, "siem-v1", "binding", "other-worker")
            .unwrap()
            .is_none());
        db.settle_compliance_export(&s.id, "siem-v1", "claim-one", false)
            .unwrap();
        assert_eq!(
            db.compliance_delivery_health(&s.id).unwrap()["sinks"][0]["pending"],
            104
        );
        assert!(db
            .claim_compliance_export(&s.id, "siem-v1", "binding", "too-soon")
            .unwrap()
            .is_none());
        db.db
            .execute(
                "UPDATE compliance_delivery SET data=json_set(data,'$.nextAtMs',0)",
                [],
            )
            .unwrap();
        db.db
            .execute("UPDATE compliance_events SET created_ms=0", [])
            .unwrap();
        assert_eq!(db.prune_compliance().unwrap(), 0); // pending delivery is retained
        drop(db);
        let mut db = Store::open(tmp.path()).unwrap();
        let retry = db
            .claim_compliance_export(&s.id, "siem-v1", "binding", "retry")
            .unwrap()
            .unwrap();
        assert_eq!(
            retry
                .activities
                .iter()
                .map(|v| &v["id"])
                .collect::<Vec<_>>(),
            first
                .activities
                .iter()
                .map(|v| &v["id"])
                .collect::<Vec<_>>()
        );
        db.settle_compliance_export(&s.id, "siem-v1", "retry", true)
            .unwrap();
        assert_eq!(db.prune_compliance().unwrap(), 100);
        let tail = db
            .claim_compliance_export(&s.id, "siem-v1", "binding", "tail")
            .unwrap()
            .unwrap();
        assert_eq!(tail.activities.len(), 4);
        db.settle_compliance_export(&s.id, "siem-v1", "tail", true)
            .unwrap();
        assert_eq!(
            db.compliance_delivery_health(&s.id).unwrap()["sinks"][0]["pending"],
            0
        );
        assert!(db
            .claim_compliance_export(&s.id, "siem-v1", "changed", "bad")
            .is_err());
        assert_eq!(db.events(&s.id, 0).unwrap().len(), 104);
        assert!(db.metadata(&s.id).unwrap().turn.is_none());
    }
}

#[cfg(all(test, feature = "compliance"))]
mod backpressure_tests {
    use super::*;
    #[test]
    fn full_backlog_stops_new_effects_but_allows_completion_and_export() {
        let tmp = tempfile::tempdir().unwrap();
        let mut db = Store::open(tmp.path()).unwrap();
        let s:Session=serde_json::from_value(json!({"id":"s_pressure","name":"test","workspace":"/work","coworker":"test","bundle_digest":"b","schema_digest":null,"interactions":[],"turn":null,"sandbox":null,"closed":false,"compliance":{"maxPendingEvents":100},"resource_policy":crate::resource_policy::Resolved::default()})).unwrap();
        db.save(&s, "session_created", json!({})).unwrap();
        let batch = db
            .claim_compliance_export(&s.id, "sink", "binding", "first")
            .unwrap()
            .unwrap();
        db.settle_compliance_export(&s.id, "sink", &batch.token, false)
            .unwrap();
        for _ in 0..99 {
            db.append_event(&s.id, "tool_finished", json!({})).unwrap();
        }
        assert!(db
            .append_event(&s.id, "tool_started", json!({"call_id":"not-run"}))
            .is_err());
        db.append_event(&s.id, "turn_completed", json!({"status":"cancelled"}))
            .unwrap();
        db.db
            .execute(
                "UPDATE compliance_delivery SET data=json_set(data,'$.nextAtMs',0)",
                [],
            )
            .unwrap();
        let batch = db
            .claim_compliance_export(&s.id, "sink", "binding", "retry")
            .unwrap()
            .unwrap();
        db.settle_compliance_export(&s.id, "sink", &batch.token, true)
            .unwrap();
        db.append_event(&s.id, "tool_started", json!({"call_id":"admitted"}))
            .unwrap();
    }
}
