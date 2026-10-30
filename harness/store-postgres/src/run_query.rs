//! Execution-owned run directory. Recordings never grant access or invent runs.
use crate::{
    auth::{self, Action, Scope},
    db::Database,
    PostgresStore, SubmissionError,
};
use anyhow::{ensure, Result};
use openworker_runtime::store::TurnObservation;
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Cursor {
    pub created_us: i64,
    pub session_id: String,
    pub sequence: i64,
}
#[derive(Clone)]
pub struct Search {
    pub text: String,
    pub agent: Option<String>,
    pub from_ms: Option<i64>,
    pub to_ms: Option<i64>,
    pub after: Option<Cursor>,
    pub as_of_us: Option<i64>,
    pub limit: u16,
}
impl Default for Search {
    fn default() -> Self {
        Self {
            text: String::new(),
            agent: None,
            from_ms: None,
            to_ms: None,
            after: None,
            as_of_us: None,
            limit: 25,
        }
    }
}
impl Search {
    pub fn validate(&self) -> Result<()> {
        ensure!(
            self.text.len() <= 200
                && !self.text.chars().any(char::is_control)
                && self.agent.as_ref().is_none_or(|s| !s.is_empty()
                    && s.len() <= 128
                    && !s.chars().any(char::is_control))
                && (1..=100).contains(&self.limit),
            SubmissionError::InvalidRequest
        );
        ensure!(
            [self.from_ms, self.to_ms]
                .into_iter()
                .flatten()
                .all(|n| (0..=253402300799000).contains(&n))
                && self.from_ms.zip(self.to_ms).is_none_or(|(a, b)| a <= b),
            SubmissionError::InvalidRequest
        );
        if let Some(c) = &self.after {
            crate::submissions::validate_id(&c.session_id, 80)?;
            ensure!(
                c.created_us > 0 && c.sequence > 0,
                SubmissionError::InvalidRequest
            );
        }
        ensure!(
            self.as_of_us.is_none_or(|n| n > 0),
            SubmissionError::InvalidRequest
        );
        Ok(())
    }
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Run {
    pub session_id: String,
    pub turn_id: String,
    pub submission_id: String,
    pub name: String,
    pub agent: String,
    pub model: String,
    pub status: String,
    pub created_ms: i64,
    #[serde(skip)]
    pub observation: Option<TurnObservation>,
    #[serde(skip)]
    pub pending_waits: u64,
}
pub struct Page {
    pub runs: Vec<Run>,
    pub next: Option<Cursor>,
    pub as_of_us: i64,
}
// Only projected metadata leaves this query. No transcript, submission text,
// arguments, credentials or model payload is selected into a result.
const COLUMNS:&str="q.session_id,q.turn_id,q.id,left(s.data->>'name',512),left(s.data->>'coworker',128),left(s.data->>'model',128),
 CASE WHEN s.data->'turn'->>'id'=q.turn_id THEN s.data->'turn'->>'status' ELSE q.status END,
 (extract(epoch FROM q.created)*1000000)::bigint,q.sequence";
fn run(row: &postgres::Row) -> Run {
    Run {
        session_id: row.get(0),
        turn_id: row.get(1),
        submission_id: row.get(2),
        name: row.get::<_, Option<String>>(3).unwrap_or_default(),
        agent: row.get::<_, Option<String>>(4).unwrap_or_default(),
        model: row.get::<_, Option<String>>(5).unwrap_or_default(),
        status: row.get(6),
        created_ms: row.get::<_, i64>(7) / 1000,
        observation: None,
        pending_waits: 0,
    }
}
impl Database {
    pub(crate) fn search_runs(&mut self, query: Search) -> Result<Page> {
        let mut tx = self.client.transaction()?;
        let p = auth::guard(&mut tx, self.auth.as_ref(), Action::Read, None, None)?;
        let all = p.as_ref().is_none_or(|p| p.scope == Scope::All);
        let owner = p
            .as_ref()
            .and_then(|p| (p.scope == Scope::Owned).then(|| p.principal.clone()));
        let ids = p.as_ref().and_then(|p| {
            if let Scope::Sessions { ids } = &p.scope {
                Some(ids.clone())
            } else {
                None
            }
        });
        let now: i64 = tx
            .query_one(
                "SELECT (extract(epoch FROM clock_timestamp())*1000000)::bigint",
                &[],
            )?
            .get(0);
        let as_of = query.as_of_us.unwrap_or(now);
        ensure!(as_of <= now, SubmissionError::InvalidRequest);
        let after = query.after.as_ref();
        let sql=format!("SELECT {COLUMNS} FROM submissions q JOIN sessions s ON s.id=q.session_id
          WHERE ($1::boolean OR ($2::text IS NOT NULL AND EXISTS(SELECT 1 FROM api_session_owners o WHERE o.session_id=s.id AND o.principal=$2)) OR s.id=ANY($3::text[]))
          AND ($4='' OR strpos(lower(concat_ws(' ',s.data->>'name',s.id,q.turn_id,q.id)),lower($4))>0)
          AND ($5::text IS NULL OR s.data->>'coworker'=$5)
          AND ($6::bigint IS NULL OR q.created>=TIMESTAMPTZ 'epoch'+$6*INTERVAL '1 millisecond')
          AND ($7::bigint IS NULL OR q.created<=TIMESTAMPTZ 'epoch'+$7*INTERVAL '1 millisecond')
          AND q.created<=TIMESTAMPTZ 'epoch'+$8::bigint*INTERVAL '1 microsecond'
          AND ($9::bigint IS NULL OR (q.created,q.session_id,q.sequence)<(TIMESTAMPTZ 'epoch'+$9::bigint*INTERVAL '1 microsecond',$10,$11))
          ORDER BY q.created DESC,q.session_id DESC,q.sequence DESC LIMIT $12");
        let rows = tx.query(
            &sql,
            &[
                &all,
                &owner,
                &ids,
                &query.text,
                &query.agent,
                &query.from_ms,
                &query.to_ms,
                &as_of,
                &after.map(|c| c.created_us),
                &after.map(|c| c.session_id.as_str()),
                &after.map(|c| c.sequence),
                &(i64::from(query.limit) + 1),
            ],
        )?;
        let more = rows.len() > usize::from(query.limit);
        let rows: Vec<_> = rows.into_iter().take(query.limit.into()).collect();
        let next = if more {
            rows.last().map(|r| Cursor {
                created_us: r.get(7),
                session_id: r.get(0),
                sequence: r.get(8),
            })
        } else {
            None
        };
        let runs = rows.iter().map(run).collect();
        tx.commit()?;
        Ok(Page {
            runs,
            next,
            as_of_us: as_of,
        })
    }
    pub(crate) fn observation_run(&mut self, sid: &str, tid: &str) -> Result<Run> {
        let mut tx = self.client.transaction()?;
        auth::guard(&mut tx, self.auth.as_ref(), Action::Read, Some(sid), None)?;
        let sql=format!("SELECT {COLUMNS} FROM submissions q JOIN sessions s ON s.id=q.session_id WHERE q.session_id=$1 AND q.turn_id=$2");
        let row = tx
            .query_opt(&sql, &[&sid, &tid])?
            .ok_or(SubmissionError::NotFound)?;
        let mut run = run(&row);
        run.observation=tx.query_opt("SELECT data->'observation' FROM events WHERE session_id=$1 AND turn_id=$2 AND kind='execution_segment_started' ORDER BY cursor DESC LIMIT 1",&[&sid,&tid])?.map(|r|serde_json::from_value(r.get(0))).transpose()?;
        if !openworker_runtime::store::terminal(&run.status) {
            let pending:i64=tx.query_one("SELECT count(*) FROM sessions s,jsonb_array_elements(COALESCE(s.data->'interactions','[]'::jsonb)) i WHERE s.id=$1 AND i->>'turn_id'=$2 AND i->>'response' IS NULL AND i->>'resolved_at_ms' IS NULL",&[&sid,&tid])?.get(0);
            run.pending_waits = pending as u64;
        }
        tx.commit()?;
        Ok(run)
    }
}
impl PostgresStore {
    pub fn search_runs(&self, query: Search) -> Result<Page> {
        query.validate()?;
        self.call(move |db| db.search_runs(query))
    }
    /// Rechecks current credential/scope and run existence; never reads recordings.
    pub fn observation_run(&self, sid: &str, tid: &str) -> Result<Run> {
        crate::submissions::validate_id(sid, 80)?;
        crate::submissions::validate_id(tid, 80)?;
        let (sid, tid) = (sid.to_owned(), tid.to_owned());
        self.call(move |db| db.observation_run(&sid, &tid))
    }
}
