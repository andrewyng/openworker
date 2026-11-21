//! Search committed, content-free guardrail findings in execution storage.
use crate::{
    auth::{self, Action, Scope},
    submissions::validate_id,
    PostgresStore, SubmissionError,
};
use anyhow::{ensure, Result};
use postgres::Transaction;
use serde::{Deserialize, Serialize};
use serde_json::Value;

const NORMALIZED_OUTCOME: &str = "CASE WHEN e.data->>'outcome'='allow'
    AND jsonb_typeof(e.data->'matchCount')='number'
    AND e.data->'matchCount'>'0'::jsonb
    THEN 'matched' ELSE e.data->>'outcome' END";
const HIT_OUTCOMES: &str = "('blocked','redacted','matched')";

pub(crate) fn hit_sessions(
    tx: &mut Transaction<'_>,
    session_ids: &[&str],
) -> Result<std::collections::HashSet<String>> {
    let sql = format!(
        "SELECT id FROM unnest($1::text[]) AS ids(id) WHERE EXISTS (
            SELECT 1 FROM audit a JOIN events e USING(session_id,cursor)
            WHERE a.session_id=ids.id AND a.kind='safety_evaluation'
              AND ({NORMALIZED_OUTCOME}) IN {HIT_OUTCOMES})"
    );
    Ok(tx
        .query(&sql, &[&session_ids])?
        .iter()
        .map(|r| r.get(0))
        .collect())
}

pub(crate) fn migrate(tx: &mut Transaction<'_>) -> Result<()> {
    tx.batch_execute(
        "CREATE INDEX IF NOT EXISTS audit_guardrails_recent
           ON audit(created DESC,session_id DESC,cursor DESC) WHERE kind='safety_evaluation';
         CREATE INDEX IF NOT EXISTS audit_guardrails_session
           ON audit(session_id,created DESC,cursor DESC) WHERE kind='safety_evaluation';",
    )?;
    Ok(())
}

#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Cursor {
    pub time_us: i64,
    pub session_id: String,
    pub event_id: i64,
}

#[derive(Default)]
pub struct Search {
    pub session_id: Option<String>,
    pub turn_id: Option<String>,
    pub rule: Option<String>,
    pub outcome: Option<Outcome>,
    pub from_ms: Option<i64>,
    pub limit: Option<u16>,
    pub after: Option<Cursor>,
}

#[derive(Clone, Copy, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Outcome {
    Redacted,
    Blocked,
    Matched,
    Unavailable,
}

impl Outcome {
    fn value(self) -> &'static str {
        match self {
            Self::Redacted => "redacted",
            Self::Blocked => "blocked",
            Self::Matched => "matched",
            Self::Unavailable => "unavailable",
        }
    }
}

#[derive(Serialize)]
pub struct Finding {
    pub session_id: String,
    pub turn_id: Option<String>,
    pub session_name: String,
    pub occurred_ms: i64,
    pub rule: String,
    pub version: String,
    pub phase: String,
    pub outcome: String,
    pub hit: bool,
    pub tag: Option<String>,
    pub match_count: Option<u64>,
    pub duration_ms: Option<u64>,
}

#[derive(Serialize)]
pub struct Page {
    pub findings: Vec<Finding>,
    #[serde(skip)]
    pub next: Option<Cursor>,
}

impl PostgresStore {
    pub fn guardrail_findings(&self, query: Search) -> Result<Page> {
        let limit = i64::from(query.limit.unwrap_or(30));
        ensure!((1..=100).contains(&limit), SubmissionError::InvalidRequest);
        if let Some(sid) = &query.session_id {
            validate_id(sid, 80)?;
        }
        if let Some(turn) = &query.turn_id {
            ensure!(query.session_id.is_some(), SubmissionError::InvalidRequest);
            validate_id(turn, 80)?;
        }
        if let Some(rule) = &query.rule {
            validate_id(rule, 128)?;
            ensure!(
                !rule.chars().any(char::is_control),
                SubmissionError::InvalidRequest
            );
        }
        ensure!(
            query
                .from_ms
                .is_none_or(|v| (0..=253402300799000).contains(&v)),
            SubmissionError::InvalidRequest
        );
        if let Some(cursor) = &query.after {
            validate_id(&cursor.session_id, 80)?;
            ensure!(
                (1..=253402300799000000).contains(&cursor.time_us) && cursor.event_id > 0,
                SubmissionError::InvalidRequest
            );
        }
        self.call(move |db| {
            let mut tx = db.client.transaction()?;
            let identity = auth::guard(&mut tx, db.auth.as_ref(), Action::Read, query.session_id.as_deref(), None)?;
            let all = identity.as_ref().is_none_or(|p| p.scope == Scope::All);
            let scoped_owner = identity.as_ref().and_then(|p| (p.scope == Scope::Owned).then_some(&p.principal));
            let scoped_sessions = identity.as_ref().and_then(|p| match &p.scope {
                Scope::Sessions { ids } => Some(ids),
                _ => None,
            });
            let after = query.after.as_ref();
            let sql = format!(
                "SELECT a.session_id,
                    COALESCE(e.turn_id,q.turn_id) AS linked_turn,
                    left(s.data->>'name',256),
                    (extract(epoch FROM a.created)*1000000)::bigint AS time_us,
                    a.cursor, left(e.data->>'check',128), left(e.data->>'version',40),
                    left(e.data->>'phase',48), f.outcome,
                    e.data->'matchCount', e.data->'durationMs'
                 FROM audit a JOIN events e USING(session_id,cursor)
                 CROSS JOIN LATERAL (
                    SELECT {NORMALIZED_OUTCOME} AS outcome
                 ) f
                 JOIN sessions s ON s.id=a.session_id
                 LEFT JOIN submissions q ON e.turn_id IS NULL
                    AND q.session_id=a.session_id AND q.id=e.data->>'submission_id'
                 WHERE a.kind='safety_evaluation'
                   AND ($1::boolean OR ($2::text IS NOT NULL AND EXISTS(
                        SELECT 1 FROM api_session_owners o WHERE o.session_id=a.session_id AND o.principal=$2))
                        OR a.session_id=ANY($3::text[]))
                   AND ($4::text IS NULL OR a.session_id=$4)
                   AND ($5::text IS NULL OR COALESCE(e.turn_id,q.turn_id)=$5)
                   AND ($6::text IS NULL OR e.data->>'check'=$6)
                   AND (($7::text IS NULL AND (f.outcome IN {HIT_OUTCOMES}
                        OR ($4::text IS NOT NULL AND f.outcome='unavailable')))
                        OR f.outcome=$7)
                   AND ($8::bigint IS NULL OR a.created>=TIMESTAMPTZ 'epoch'+$8*INTERVAL '1 millisecond')
                   AND ($9::bigint IS NULL OR
                        (a.created,a.session_id,a.cursor)<(TIMESTAMPTZ 'epoch'+$9*INTERVAL '1 microsecond',$10,$11))
                 ORDER BY a.created DESC,a.session_id DESC,a.cursor DESC LIMIT $12",
            );
            let rows = tx.query(
                &sql,
                &[&all,&scoped_owner,&scoped_sessions,&query.session_id,&query.turn_id,&query.rule,
                  &query.outcome.map(Outcome::value),&query.from_ms,
                  &after.map(|c| c.time_us),&after.map(|c| c.session_id.as_str()),
                  &after.map(|c| c.event_id),&(limit + 1)],
            )?;
            let page = &rows[..rows.len().min(limit as usize)];
            let next = (rows.len() > page.len()).then(|| {
                let row = page.last().unwrap();
                Cursor { time_us: row.get(3),session_id: row.get(0),event_id: row.get(4) }
            });
            let findings = page.iter().map(|row| {
                let outcome: String = row.get(8);
                let hit = matches!(outcome.as_str(), "redacted" | "blocked" | "matched");
                let rule = openworker_runtime::telemetry::redact(&row.get::<_, Option<String>>(5).unwrap_or_else(|| "guardrail".into()));
                Finding {
                    session_id: row.get(0),
                    turn_id: row.get(1),
                    session_name: row.get::<_, Option<String>>(2).unwrap_or_default(),
                    occurred_ms: row.get::<_, i64>(3) / 1000,
                    tag: hit.then(|| format!("guardrail:{rule}")),
                    rule,
                    version: row.get::<_, Option<String>>(6).unwrap_or_default(),
                    phase: row.get::<_, Option<String>>(7).unwrap_or_default(),
                    outcome,
                    hit,
                    match_count: row.get::<_, Option<Value>>(9).and_then(|v| v.as_u64()),
                    duration_ms: row.get::<_, Option<Value>>(10).and_then(|v| v.as_u64()),
                }
            }).collect();
            tx.commit()?;
            Ok(Page { findings, next })
        })
    }
}
