//! Trusted-host aggregate observer; no session IDs or payloads leave PostgreSQL.
use crate::db::Database;
use anyhow::{ensure, Result};
#[derive(Debug, Clone, serde::Serialize)]
pub struct OperationalSnapshot {
    pub observed_at: u64,
    pub active: u64,
    pub queued: u64,
    pub approvals: u64,
    pub questions: u64,
    pub oldest_queued_seconds: u64,
    pub stalled: u64,
    pub unknown_recent: u64,
}
impl Database {
    pub(crate) fn operational_snapshot(
        &mut self,
        stalled_after: u64,
        recent_window: u64,
    ) -> Result<OperationalSnapshot> {
        ensure!(self.auth.is_none(), crate::auth::Error::Forbidden);
        ensure!(
            (1..=86400).contains(&stalled_after) && (1..=86400).contains(&recent_window),
            "Invalid observation window"
        );
        // One statement = one MVCC snapshot. No row locks, names, arguments,
        // messages, raw errors or SQL text returned to the exporter.
        let r=self.client.query_one("WITH current_sessions AS MATERIALIZED (
          SELECT id,data,COALESCE(data->'turn'->>'status','idle') AS state FROM sessions WHERE NOT (data->>'closed')::boolean
        ), active AS MATERIALIZED (
          SELECT * FROM current_sessions WHERE state NOT IN ('idle','waiting','recoverable','completed','failed','cancelled','interrupted','truncated','blocked')
        ), waiting AS (
          SELECT i->>'kind' AS kind FROM current_sessions s CROSS JOIN LATERAL jsonb_array_elements(s.data->'interactions') i
          WHERE s.state IN ('waiting','recoverable') AND i->>'response' IS NULL AND i->>'turn_id'=s.data->'turn'->>'id'
        ) SELECT floor(extract(epoch FROM statement_timestamp()))::bigint,
          (SELECT count(*) FROM active),
          (SELECT count(*) FROM submissions WHERE status='queued'),
          (SELECT count(*) FROM waiting WHERE kind='approval'),
          (SELECT count(*) FROM waiting WHERE kind='question'),
          (SELECT COALESCE(greatest(0,floor(extract(epoch FROM statement_timestamp()-min(created)))),0)::bigint FROM submissions WHERE status='queued'),
          (SELECT count(*) FROM active s WHERE (SELECT a.created FROM audit a WHERE a.session_id=s.id ORDER BY a.cursor DESC LIMIT 1)<statement_timestamp()-make_interval(secs=>$1)),
          (SELECT count(*) FROM events e JOIN audit a USING(session_id,cursor) WHERE e.kind IN ('turn_completed','turn_recovered') AND COALESCE((e.data->>'outcome_unknown_count')::bigint,0)>0 AND a.created>statement_timestamp()-make_interval(secs=>$2))",
          &[&(stalled_after as f64),&(recent_window as f64)])?;
        let n = |i: usize| -> u64 { r.get::<_, i64>(i).max(0) as u64 };
        Ok(OperationalSnapshot {
            observed_at: n(0),
            active: n(1),
            queued: n(2),
            approvals: n(3),
            questions: n(4),
            oldest_queued_seconds: n(5),
            stalled: n(6),
            unknown_recent: n(7),
        })
    }
}
