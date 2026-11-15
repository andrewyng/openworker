//! Compliance records commit with execution events. Queries require independent
//! metadata/content authority and retain the session's original ownership scope.
use crate::{auth, db, PostgresStore};
use anyhow::{ensure, Result};
use openworker_runtime::compliance::Query;
use postgres::Transaction;
use serde_json::{json, Value};

pub(crate) fn migrate(tx: &mut Transaction<'_>) -> Result<()> {
    #[cfg(feature = "guardrails")]
    tx.batch_execute(include_str!("observers.sql"))?;
    #[cfg(feature = "compliance")]
    tx.batch_execute(include_str!("compliance.sql"))?;
    #[cfg(not(feature = "compliance"))]
    let _ = tx;
    Ok(())
}
impl db::Database {
    pub(crate) fn compliance_sessions(
        &mut self,
        after: &str,
    ) -> Result<Vec<openworker_runtime::store::Session>> {
        let mut tx = self.client.transaction()?;
        auth::guard(
            &mut tx,
            self.auth.as_ref(),
            auth::Action::ComplianceExport,
            None,
            None,
        )?;
        let rows=tx.query("SELECT * FROM sessions WHERE id>$1 AND jsonb_typeof(data->'compliance')='object' ORDER BY id LIMIT 100",&[&after])?;
        let sessions = rows.iter().map(db::metadata).collect::<Result<Vec<_>>>()?;
        tx.commit()?;
        Ok(sessions)
    }
    pub(crate) fn compliance_page(&mut self, sid: &str, q: Query) -> Result<Value> {
        ensure!(
            cfg!(feature = "compliance"),
            "Compliance not included in this build"
        );
        q.validate()?;
        let mut tx = self.client.transaction()?;
        let identity = auth::guard(
            &mut tx,
            self.auth.as_ref(),
            auth::Action::ComplianceRead,
            Some(sid),
            None,
        )?;
        let session = db::locked(&mut tx, sid, None)?;
        ensure!(
            session.compliance.is_some(),
            "Compliance capture is not enabled for this session"
        );
        let cutoff = openworker_runtime::store::now_ms() as i64
            - i64::from(session.compliance.as_ref().unwrap().retention_days) * 86_400_000;
        let rows=tx.query("SELECT source_cursor,turn_id,created_ms,record FROM compliance_events WHERE session_id=$1 AND created_ms>=$6 AND source_cursor>$2 AND ($3::text IS NULL OR kind=$3) AND ($4::text IS NULL OR record->'facts'->>'outcome'=$4) ORDER BY source_cursor LIMIT $5",&[&sid,&q.after,&q.kind,&q.outcome,&((q.limit()+1)as i64),&cutoff])?;
        let more = rows.len() > q.limit();
        let mut cursor = q.after;
        let mut activities = vec![];
        for row in rows.into_iter().take(q.limit()) {
            let id: i64 = row.get(0);
            let mut value: Value = row.get(3);
            value["id"] = json!(format!("{sid}:{id}"));
            value["sessionId"] = json!(sid);
            value["turnId"] = json!(row.get::<_, Option<String>>(1));
            value["createdAtMs"] = json!(row.get::<_, i64>(2));
            activities.push(value);
            cursor = id;
        }
        auth::audit(
            &mut tx,
            identity.as_ref(),
            auth::Action::ComplianceRead,
            Some(sid),
            None,
        )?;
        tx.commit()?;
        Ok(json!({"activities":activities,"nextAfter":cursor,"hasMore":more}))
    }
    pub(crate) fn prune_compliance(&mut self) -> Result<u64> {
        ensure!(
            cfg!(feature = "compliance"),
            "Compliance feature unavailable"
        );
        let mut tx = self.client.transaction()?;
        let identity = auth::guard(&mut tx, self.auth.as_ref(), auth::Action::Admin, None, None)?;
        let now = openworker_runtime::store::now_ms() as i64;
        let deleted=tx.execute("DELETE FROM compliance_events e USING sessions s WHERE e.session_id=s.id AND NOT EXISTS(SELECT 1 FROM compliance_delivery d WHERE d.session_id=e.session_id AND (d.data->>'cursor')::bigint<e.source_cursor) AND e.created_ms < $1 - 86400000::bigint * COALESCE((s.data->'compliance'->>'retentionDays')::bigint,3650)",&[&now])?;
        auth::audit(
            &mut tx,
            identity.as_ref(),
            auth::Action::Admin,
            None,
            Some("compliance-retention"),
        )?;
        tx.commit()?;
        Ok(deleted)
    }
    pub(crate) fn compliance_content(&mut self, sid: &str, after: i64) -> Result<Value> {
        ensure!(
            cfg!(feature = "compliance") && after >= 0,
            "Invalid compliance content query"
        );
        let mut tx = self.client.transaction()?;
        let identity = auth::guard(
            &mut tx,
            self.auth.as_ref(),
            auth::Action::ComplianceContent,
            Some(sid),
            None,
        )?;
        let session = db::locked(&mut tx, sid, None)?;
        ensure!(session.compliance.is_some(), "Compliance is not enabled");
        let rows = tx.query(
            "SELECT seq,data FROM history WHERE session_id=$1 AND seq>=$2 ORDER BY seq LIMIT 101",
            &[&sid, &after],
        )?;
        let more = rows.len() > 100;
        let mut cursor = after;
        let mut items = vec![];
        let mut bytes = 0;
        for row in rows.into_iter().take(100) {
            let seq: i64 = row.get(0);
            let value: Value = row.get(1);
            let size = value.to_string().len();
            if !items.is_empty() && bytes + size > 512000 {
                auth::audit(
                    &mut tx,
                    identity.as_ref(),
                    auth::Action::ComplianceContent,
                    Some(sid),
                    None,
                )?;
                tx.commit()?;
                return Ok(json!({"items":items,"nextAfter":cursor,"hasMore":true}));
            }
            if value["type"] != "reasoning" {
                bytes += size;
                items.push(json!({"index":seq,"item":value}));
            }
            cursor = seq + 1;
        }
        auth::audit(
            &mut tx,
            identity.as_ref(),
            auth::Action::ComplianceContent,
            Some(sid),
            None,
        )?;
        tx.commit()?;
        Ok(json!({"items":items,"nextAfter":cursor,"hasMore":more}))
    }
}
impl PostgresStore {
    pub fn compliance_page(&self, sid: &str, q: Query) -> Result<Value> {
        let sid = sid.to_owned();
        self.call(move |db| db.compliance_page(&sid, q))
    }
    pub fn prune_compliance(&self) -> Result<u64> {
        self.call(|db| db.prune_compliance())
    }
    pub fn compliance_content(&self, sid: &str, after: i64) -> Result<Value> {
        let sid = sid.to_owned();
        self.call(move |db| db.compliance_content(&sid, after))
    }
}

impl db::Database {
    pub(crate) fn claim_compliance_export(
        &mut self,
        sid: &str,
        sink: &str,
        binding: &str,
        token: &str,
    ) -> Result<Option<openworker_runtime::compliance::delivery::Batch>> {
        use openworker_runtime::compliance::delivery::{Batch, Checkpoint};
        ensure!(
            cfg!(feature = "compliance")
                && openworker_runtime::compliance::identifier(sink)
                && binding.len() <= 128,
            "Invalid compliance sink"
        );
        let mut tx = self.client.transaction()?;
        let identity = auth::guard(
            &mut tx,
            self.auth.as_ref(),
            auth::Action::ComplianceExport,
            Some(sid),
            None,
        )?;
        let s = db::locked(&mut tx, sid, None)?;
        ensure!(s.compliance.is_some(), "Compliance disabled");
        let raw = tx
            .query_opt(
                "SELECT data FROM compliance_delivery WHERE session_id=$1 AND sink=$2 FOR UPDATE",
                &[&sid, &sink],
            )?
            .map(|r| r.get::<_, Value>(0));
        if raw.is_none() {
            let count: i64 = tx
                .query_one(
                    "SELECT count(*) FROM compliance_delivery WHERE session_id=$1",
                    &[&sid],
                )?
                .get(0);
            ensure!(count < 64, "Compliance sink limit reached");
        }
        let mut c: Checkpoint = raw
            .map(serde_json::from_value)
            .transpose()?
            .unwrap_or_else(|| Checkpoint::new(binding.into()));
        ensure!(c.binding == binding, "Export destination changed");
        let rows=tx.query("SELECT source_cursor,turn_id,created_ms,record FROM compliance_events WHERE session_id=$1 AND source_cursor>$2 ORDER BY source_cursor LIMIT 100",&[&sid,&c.cursor])?;
        let mut through = c.cursor;
        let mut activities = vec![];
        for row in rows {
            let id: i64 = row.get(0);
            let mut v: Value = row.get(3);
            v["id"] = json!(format!("{sid}:{id}"));
            v["sessionId"] = json!(sid);
            v["turnId"] = json!(row.get::<_, Option<String>>(1));
            v["createdAtMs"] = json!(row.get::<_, i64>(2));
            activities.push(v);
            through = id;
        }
        let claimed = c.claim(binding, token, openworker_runtime::store::now_ms(), through)?;
        tx.execute("INSERT INTO compliance_delivery VALUES($1,$2,$3) ON CONFLICT(session_id,sink) DO UPDATE SET data=excluded.data",&[&sid,&sink,&json!(c)])?;
        if claimed {
            auth::audit(
                &mut tx,
                identity.as_ref(),
                auth::Action::ComplianceExport,
                Some(sid),
                Some("compliance-export"),
            )?;
        }
        tx.commit()?;
        Ok(claimed.then(|| Batch {
            token: token.into(),
            session_id: sid.into(),
            sink: sink.into(),
            activities,
        }))
    }
    pub(crate) fn settle_compliance_export(
        &mut self,
        sid: &str,
        sink: &str,
        token: &str,
        success: bool,
    ) -> Result<()> {
        let mut tx = self.client.transaction()?;
        auth::guard(
            &mut tx,
            self.auth.as_ref(),
            auth::Action::ComplianceExport,
            Some(sid),
            None,
        )?;
        db::locked(&mut tx, sid, None)?;
        let raw: Value = tx
            .query_one(
                "SELECT data FROM compliance_delivery WHERE session_id=$1 AND sink=$2 FOR UPDATE",
                &[&sid, &sink],
            )?
            .get(0);
        let mut c: openworker_runtime::compliance::delivery::Checkpoint =
            serde_json::from_value(raw)?;
        c.settle(token, openworker_runtime::store::now_ms(), success)?;
        tx.execute(
            "UPDATE compliance_delivery SET data=$3 WHERE session_id=$1 AND sink=$2",
            &[&sid, &sink, &json!(c)],
        )?;
        tx.commit()?;
        Ok(())
    }
    pub(crate) fn compliance_delivery_health(&mut self, sid: &str) -> Result<Value> {
        let mut tx = self.client.transaction()?;
        let identity = auth::guard(
            &mut tx,
            self.auth.as_ref(),
            auth::Action::ComplianceRead,
            Some(sid),
            None,
        )?;
        db::locked(&mut tx, sid, None)?;
        let rows=tx.query("SELECT sink,data,(SELECT count(*) FROM compliance_events e WHERE e.session_id=d.session_id AND e.source_cursor>(d.data->>'cursor')::bigint) FROM compliance_delivery d WHERE session_id=$1 ORDER BY sink LIMIT 64",&[&sid])?;
        let mut v = vec![];
        for row in rows {
            let c: openworker_runtime::compliance::delivery::Checkpoint =
                serde_json::from_value(row.get(1))?;
            v.push(json!({"sink":row.get::<_,String>(0),"pending":row.get::<_,i64>(2),"cursor":c.cursor,"failures":c.failures,"nextAtMs":c.next_at_ms,"lastSuccessMs":c.last_success_ms}));
        }
        auth::audit(
            &mut tx,
            identity.as_ref(),
            auth::Action::ComplianceRead,
            Some(sid),
            Some("delivery-health"),
        )?;
        tx.commit()?;
        Ok(json!({"sinks":v}))
    }
}
impl PostgresStore {
    pub fn compliance_delivery_health(&self, sid: &str) -> Result<Value> {
        let sid = sid.to_owned();
        self.call(move |db| db.compliance_delivery_health(&sid))
    }
}

/// Check before the SQL trigger so ordinary backpressure is a domain rejection,
/// not a database transport failure. The exporter can keep draining this store.
pub(crate) fn admit_event(
    tx: &mut Transaction<'_>,
    s: &openworker_runtime::store::Session,
    kind: &str,
) -> Result<()> {
    if !matches!(kind, "turn_accepted" | "model_request" | "tool_started") {
        return Ok(());
    }
    let Some(config) = &s.compliance else {
        return Ok(());
    };
    ensure!(
        cfg!(feature = "compliance"),
        "Required compliance capability not included in this build"
    );
    let pending:i64=tx.query_one("SELECT count(*) FROM compliance_events e WHERE e.session_id=$1 AND EXISTS(SELECT 1 FROM compliance_delivery d WHERE d.session_id=e.session_id AND (d.data->>'cursor')::bigint<e.source_cursor)",&[&s.id])?.get(0);
    ensure!(
        pending < i64::from(config.max_pending_events),
        "Compliance export backlog full; restore the exporter before starting more work"
    );
    Ok(())
}
