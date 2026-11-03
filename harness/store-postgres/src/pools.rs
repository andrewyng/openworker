//! Trusted-host diagnostics. Presence never grants ownership or permission.
use crate::{db::Database, worker::Selector, PostgresStore};
use anyhow::{ensure, Result};
use postgres::Transaction;
use serde_json::{json, Value};

pub(crate) fn migrate(tx: &mut Transaction<'_>) -> Result<()> {
    let exists: bool = tx
        .query_one("SELECT to_regclass('pool_version') IS NOT NULL", &[])?
        .get(0);
    if exists {
        let rows = tx.query("SELECT version FROM pool_version WHERE singleton", &[])?;
        ensure!(
            rows.len() == 1 && rows[0].get::<_, i32>(0) == 1,
            "Unsupported pool catalog version"
        );
    } else {
        ensure!(
            !tx.query_one("SELECT to_regclass('worker_presence') IS NOT NULL", &[])?
                .get::<_, bool>(0),
            "Unversioned worker catalog"
        );
        tx.batch_execute("CREATE TABLE pool_version(singleton BOOLEAN PRIMARY KEY CHECK(singleton), version INTEGER NOT NULL);
          INSERT INTO pool_version VALUES(TRUE,1);
          CREATE TABLE worker_presence(incarnation TEXT PRIMARY KEY,node TEXT NOT NULL,selector JSONB NOT NULL,
            slots INTEGER NOT NULL CHECK(slots BETWEEN 1 AND 32),draining BOOLEAN NOT NULL,
            expires TIMESTAMPTZ NOT NULL);
          CREATE INDEX worker_presence_expiry ON worker_presence(expires);")?;
    }
    Ok(())
}
impl PostgresStore {
    /// One advertisement per store incarnation; renew every five seconds.
    /// Twenty-second expiry uses database time. Caller must check dependencies
    /// and actual runtime identity before advertising. Never callable by agents.
    pub fn advertise_worker(
        &self,
        node: &str,
        selector: Selector,
        slots: u16,
        draining: bool,
    ) -> Result<()> {
        crate::submissions::validate_id(node, 120)?;
        selector.validate()?;
        ensure!((1..=32).contains(&slots), "Invalid pool capacity");
        let (node, selector) = (node.to_owned(), serde_json::to_value(selector)?);
        self.call(move |db| {
            ensure!(db.auth.is_none(), crate::auth::Error::Forbidden);
            // Bounded housekeeping of expired *operational* advertisements only.
            // Sessions, leases and approvals are never changed here.
            db.client.execute("DELETE FROM worker_presence WHERE incarnation IN (SELECT incarnation FROM worker_presence WHERE expires < clock_timestamp()-interval '1 day' LIMIT 100)", &[])?;
            let n=db.client.execute("INSERT INTO worker_presence VALUES($1,$2,$3,$4,$5,clock_timestamp()+interval '20 seconds')
              ON CONFLICT(incarnation) DO UPDATE SET draining=EXCLUDED.draining,expires=EXCLUDED.expires
              WHERE worker_presence.node=EXCLUDED.node AND worker_presence.selector=EXCLUDED.selector AND worker_presence.slots=EXCLUDED.slots",
              &[&db.owner,&node,&selector,&i32::from(slots),&draining])?;
            ensure!(n==1, "Worker incarnation identity changed");
            Ok(())
        })
    }
    pub fn withdraw_worker(&self) -> Result<()> {
        self.call(|db| {
            ensure!(db.auth.is_none(), crate::auth::Error::Forbidden);
            db.client.execute(
                "DELETE FROM worker_presence WHERE incarnation=$1",
                &[&db.owner],
            )?;
            Ok(())
        })
    }
    /// Keyset-paginated catalog by immutable execution digest. Count ALL open
    /// sessions, including idle/completed sessions that can accept future turns.
    /// Not an atomic retirement gate; stop creation and close sessions separately.
    pub fn worker_pools(&self, after: Option<&str>, limit: u16) -> Result<Value> {
        ensure!((1..=100).contains(&limit), "Pool page limit must be 1–100");
        if let Some(after) = after {
            crate::submissions::validate_id(after, 128)?;
        }
        let after = after.unwrap_or("").to_owned();
        self.call(move |db| db.worker_pools(&after, limit))
    }
}
impl Database {
    fn worker_pools(&mut self, after: &str, limit: u16) -> Result<Value> {
        ensure!(self.auth.is_none(), crate::auth::Error::Forbidden);
        // A repeatable-read snapshot across the bounded page and its aggregates.
        let mut tx = self
            .client
            .build_transaction()
            .isolation_level(postgres::IsolationLevel::RepeatableRead)
            .read_only(true)
            .start()?;
        let observed: i64 = tx
            .query_one(
                "SELECT floor(extract(epoch FROM transaction_timestamp()))::bigint",
                &[],
            )?
            .get(0);
        let rows=tx.query("SELECT digest FROM (
          SELECT data->>'bundle_digest' AS digest FROM sessions
          UNION SELECT selector->>'bundle_digest' FROM worker_presence WHERE expires>transaction_timestamp()
          ) d WHERE digest>$1 ORDER BY digest LIMIT $2", &[&after,&(i64::from(limit)+1)])?;
        let more = rows.len() > usize::from(limit);
        let mut pools = Vec::new();
        for row in rows.into_iter().take(usize::from(limit)) {
            let digest: String = row.get(0);
            let r=tx.query_one("WITH s AS MATERIALIZED(SELECT id,data,COALESCE(data->'turn'->>'status','idle') AS state FROM sessions WHERE data->>'bundle_digest'=$1 AND NOT (data->>'closed')::boolean),
              pending AS (SELECT i->>'kind' AS kind FROM s CROSS JOIN LATERAL jsonb_array_elements(s.data->'interactions') i
                WHERE s.state IN ('waiting','recoverable') AND i->>'response' IS NULL AND i->>'turn_id'=s.data->'turn'->>'id')
              SELECT (SELECT count(*) FROM s),
                (SELECT count(*) FROM s WHERE state NOT IN ('idle','waiting','recoverable','completed','failed','cancelled','interrupted','truncated','blocked')),
                (SELECT count(*) FROM submissions q JOIN s ON s.id=q.session_id WHERE q.status='queued'),
                (SELECT count(*) FROM controls c JOIN s ON s.id=c.session_id WHERE c.status='pending'),
                (SELECT count(*) FROM pending WHERE kind='approval'),(SELECT count(*) FROM pending WHERE kind='question')", &[&digest])?;
            // Full selectors matter: same execution digest can still target a
            // different deployment/model/backend. Do not infer coverage by count.
            let workers:Vec<Value>=tx.query("SELECT node,selector,slots,draining FROM worker_presence WHERE selector->>'bundle_digest'=$1 AND expires>transaction_timestamp() ORDER BY node,incarnation LIMIT 101", &[&digest])?.into_iter().map(|r|json!({"node":r.get::<_,String>(0),"selector":r.get::<_,Value>(1),"slots":r.get::<_,i32>(2),"draining":r.get::<_,bool>(3)})).collect();
            let truncated = workers.len() > 100;
            let uncovered:i64=tx.query_one("SELECT count(*) FROM sessions s WHERE data->>'bundle_digest'=$1 AND NOT (data->>'closed')::boolean AND NOT EXISTS(
              SELECT 1 FROM worker_presence w WHERE w.expires>transaction_timestamp() AND NOT w.draining
              AND w.selector->>'bundle_digest'=s.data->>'bundle_digest' AND w.selector->>'coworker'=s.data->>'coworker'
              AND w.selector->>'backend'=s.data->>'execution_backend' AND w.selector->>'scripted'=s.data->>'scripted'
              AND ((NOT (s.data ? 'model_selection') AND w.selector->>'fallback_model'=s.data->>'model' AND COALESCE(w.selector->>'model_provider','openai')=COALESCE(s.data->>'model_provider','openai'))
                   OR (w.selector->'model_connections'->>(s.data->'model_selection'->>'connection'))=s.data->'model_selection'->>'identity')
              AND (NOT (s.data ? 'reviewer_selection') OR (w.selector->'model_connections'->>(s.data->'reviewer_selection'->>'connection'))=s.data->'reviewer_selection'->>'identity')
              AND COALESCE(w.selector->>'environment_provider','')=COALESCE(s.data->'environment'->'spec'->>'provider','')
              AND COALESCE(w.selector->>'environment_identity','')=COALESCE(s.data->'environment'->'spec'->>'provider_identity','')
              AND COALESCE(w.selector->>'environment_profile','')=COALESCE(s.data->'environment'->'spec'->>'profile_digest',''))", &[&digest])?.get(0);
            pools.push(json!({"execution_digest":digest,"open_sessions":r.get::<_,i64>(0),"active_sessions":r.get::<_,i64>(1),"queued_turns":r.get::<_,i64>(2),"pending_controls":r.get::<_,i64>(3),"approvals":r.get::<_,i64>(4),"questions":r.get::<_,i64>(5),"sessions_without_advertised_worker":uncovered,"workers":workers.into_iter().take(100).collect::<Vec<_>>(),"workers_truncated":truncated}));
        }
        let next = if more {
            pools.last().map(|p| p["execution_digest"].clone())
        } else {
            None
        };
        tx.commit()?;
        Ok(
            json!({"observed_at":observed,"pools":pools,"next":next,"retirement_requires_creation_cutoff":true}),
        )
    }
}
