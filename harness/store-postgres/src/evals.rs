//! App-owned evaluation cases and a durable queue for deterministic scoring.
use crate::{
    auth::{self, Action, Identity, Scope},
    submissions::validate_id,
    PostgresStore, SubmissionError,
};
use anyhow::{ensure, Result};
use openworker_observability::evaluations::{Evaluator, Input, Score, Source, Status};
use postgres::Transaction;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

pub(crate) const MAX_CASES: i32 = 100;
const SCORE_BATCH: i64 = 16;
const MAX_EVIDENCE_BYTES: usize = 16_384;

pub(crate) fn migrate(tx: &mut Transaction<'_>) -> Result<()> {
    tx.batch_execute(
        "CREATE TABLE IF NOT EXISTS eval_datasets(
            id TEXT PRIMARY KEY, owner TEXT NOT NULL, name TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 0, UNIQUE(owner,name)
        );
        CREATE TABLE IF NOT EXISTS eval_cases(
            id TEXT PRIMARY KEY, dataset TEXT NOT NULL REFERENCES eval_datasets(id),
            version INTEGER NOT NULL, source JSONB NOT NULL, evidence JSONB NOT NULL,
            check_spec JSONB NOT NULL, UNIQUE(dataset,version)
        );
        CREATE TABLE IF NOT EXISTS eval_jobs(
            id TEXT PRIMARY KEY, dataset TEXT NOT NULL REFERENCES eval_datasets(id),
            version INTEGER NOT NULL, created_ms BIGINT NOT NULL,
            results JSONB
        );
        ALTER TABLE eval_cases ADD COLUMN IF NOT EXISTS case_key TEXT;
        ALTER TABLE eval_cases ADD COLUMN IF NOT EXISTS archived BOOLEAN NOT NULL DEFAULT FALSE;
        ALTER TABLE eval_jobs ADD COLUMN IF NOT EXISTS target_count INTEGER;
        ALTER TABLE eval_jobs ADD COLUMN IF NOT EXISTS processed INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE eval_jobs ADD COLUMN IF NOT EXISTS partial_results JSONB;
        ALTER TABLE eval_jobs ALTER COLUMN dataset DROP NOT NULL;
        ALTER TABLE eval_jobs ADD COLUMN IF NOT EXISTS owner TEXT;
        ALTER TABLE eval_jobs ADD COLUMN IF NOT EXISTS selection JSONB;
        ALTER TABLE eval_jobs ADD COLUMN IF NOT EXISTS evaluator JSONB;
        CREATE INDEX IF NOT EXISTS eval_jobs_pending
          ON eval_jobs(created_ms,id) WHERE results IS NULL;
        CREATE INDEX IF NOT EXISTS eval_jobs_dataset
          ON eval_jobs(dataset,created_ms DESC,id DESC);
        CREATE INDEX IF NOT EXISTS eval_jobs_recent
          ON eval_jobs(created_ms DESC,id DESC);
        CREATE INDEX IF NOT EXISTS eval_datasets_name ON eval_datasets(lower(name),id);
        CREATE INDEX IF NOT EXISTS eval_datasets_owner_name ON eval_datasets(owner,lower(name),id);",
    )?;
    crate::eval_selection::migrate(tx)?;
    crate::eval_replay::migrate(tx)?;
    crate::eval_schedule::migrate(tx)?;
    Ok(())
}

#[derive(Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Turn {
    pub session_id: String,
    pub turn_id: String,
}
#[derive(Serialize)]
pub struct Evidence {
    pub turn: Turn,
    pub owner: String,
    pub name: String,
    pub input: Option<String>,
    pub output: Option<String>,
}
#[derive(Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Match {
    Contains,
    Equals,
}
#[derive(Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Check {
    pub comparison: Match,
    pub expected: String,
}
impl Check {
    pub fn passes(&self, output: &str) -> bool {
        match self.comparison {
            Match::Contains => output.contains(&self.expected),
            Match::Equals => output == self.expected,
        }
    }
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SaveCase {
    pub dataset: String,
    pub name: Option<String>,
    pub turn: Turn,
    pub include_input: bool,
    pub check: Check,
    pub batch_id: Option<String>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DatasetVersion {
    pub version: Option<i32>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ReviseCase {
    pub name: Option<String>,
    pub check: Option<Check>,
    #[serde(default)]
    pub archive: bool,
}

#[derive(Default, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DatasetList {
    pub q: Option<String>,
    pub owner: Option<String>,
    pub after: Option<String>,
    pub limit: Option<u16>,
}

#[derive(Default, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RunList {
    pub q: Option<String>,
    pub dataset: Option<String>,
    pub status: Option<RunStatus>,
    pub since: Option<i64>,
    pub after: Option<String>,
    pub limit: Option<u16>,
}

#[derive(Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RunStatus {
    Queued,
    Completed,
}

fn list_page(q: Option<String>, after: Option<&str>, limit: Option<u16>) -> Result<(String, i64)> {
    let q = q.unwrap_or_default().trim().to_owned();
    let limit = i64::from(limit.unwrap_or(20));
    ensure!(
        q.len() <= 400 && !q.chars().any(char::is_control) && (1..=50).contains(&limit),
        SubmissionError::InvalidRequest
    );
    if let Some(after) = after {
        validate_id(after, 100)?;
    }
    Ok((q, limit))
}

pub(crate) fn identity(
    tx: &mut Transaction<'_>,
    auth_context: Option<&auth::Context>,
    action: Action,
    sid: Option<&str>,
) -> Result<Identity> {
    let p = auth::guard(tx, auth_context, action, sid, None)?.ok_or(auth::Error::Forbidden)?;
    ensure!(
        !matches!(p.scope, Scope::Sessions { .. }),
        auth::Error::Forbidden
    );
    Ok(p)
}

pub(crate) fn capture(
    tx: &mut Transaction<'_>,
    turn: Turn,
    completed_only: bool,
) -> Result<Evidence> {
    // Only the submitted plain text and final committed assistant message are
    // eligible. Intermediate attempts, tool results and attachment parts are excluded.
    let row = tx
        .query_opt(
            "SELECT q.status, o.principal,
                CASE WHEN q.input_content IS NULL AND octet_length(q.text)<=$3 THEN q.text END,
                (SELECT CASE WHEN octet_length(data->>'messages') <= 131072 THEN data->'messages' END FROM events e
                  WHERE e.session_id=q.session_id AND e.turn_id=q.turn_id
                    AND e.kind='assistant_message'
                  ORDER BY e.cursor DESC LIMIT 1),
                left(s.data->>'name',100)
             FROM submissions q JOIN api_session_owners o USING(session_id)
             JOIN sessions s ON s.id=q.session_id
             WHERE q.session_id=$1 AND q.turn_id=$2",
            &[
                &turn.session_id,
                &turn.turn_id,
                &(MAX_EVIDENCE_BYTES as i32),
            ],
        )?
        .ok_or(SubmissionError::NotFound)?;
    let completed = row.get::<_, String>(0) == "completed";
    ensure!(
        !completed_only || completed,
        SubmissionError::InvalidRequest
    );
    let messages: Option<Value> = if completed { row.get(3) } else { None };
    let mut output = String::new();
    let mut omitted = false;
    for part in messages
        .iter()
        .flat_map(|v| v.as_array().into_iter().flatten())
        .flat_map(|m| m["content"].as_array().into_iter().flatten())
        .filter(|p| p["type"] == "output_text")
        .filter_map(|p| p["text"].as_str())
    {
        if output.len() + part.len() + 1 > MAX_EVIDENCE_BYTES {
            omitted = true;
            break;
        }
        if !output.is_empty() {
            output.push('\n');
        }
        output.push_str(part);
    }
    Ok(Evidence {
        turn,
        owner: row.get(1),
        name: row
            .get::<_, Option<String>>(4)
            .filter(|name| !name.trim().is_empty())
            .unwrap_or_else(|| "Agent response".into()),
        input: row
            .get::<_, Option<String>>(2)
            .as_deref()
            .map(openworker_runtime::telemetry::redact),
        output: (!omitted && !output.is_empty())
            .then(|| openworker_runtime::telemetry::redact(&output)),
    })
}

pub(crate) fn owned_dataset(
    tx: &mut Transaction<'_>,
    p: &Identity,
    id: &str,
) -> Result<(String, i32)> {
    tx.query_opt(
        "SELECT name,version FROM eval_datasets WHERE id=$1 AND ($2 OR owner=$3)",
        &[&id, &(p.scope == Scope::All), &p.principal],
    )?
    .map(|row| (row.get(0), row.get(1)))
    .ok_or_else(|| SubmissionError::NotFound.into())
}

// Original rows use their own ID as the case identity. A revision keeps that
// identity and supplies a new immutable row for the numbered dataset version.
pub(crate) const CASE_SNAPSHOT: &str = "SELECT DISTINCT ON (COALESCE(case_key,id))
    id,version,source,evidence,check_spec,archived,COALESCE(case_key,id) AS case_key
    FROM eval_cases WHERE dataset=$1 AND version<=$2 ORDER BY COALESCE(case_key,id),version DESC";

fn case_count(tx: &mut Transaction<'_>, dataset: &str, version: i32) -> Result<i32> {
    Ok(tx
        .query_one(
            &format!("SELECT count(*)::int FROM ({CASE_SNAPSHOT}) cases WHERE NOT archived"),
            &[&dataset, &version],
        )?
        .get(0))
}

fn check_name(name: &str) -> Result<()> {
    ensure!(
        !name.trim().is_empty() && name.len() <= 400 && !name.chars().any(char::is_control),
        SubmissionError::InvalidRequest
    );
    Ok(())
}

fn batch_evidence(
    tx: &mut Transaction<'_>,
    p: &Identity,
    job: &str,
    turn: Turn,
) -> Result<Evidence> {
    let row = tx.query_opt("SELECT t.output FROM eval_job_targets t JOIN eval_jobs j ON j.id=t.job
       WHERE t.job=$1 AND (t.session_id,t.turn_id)=($2,$3) AND ($4 OR j.owner=$5) AND j.results IS NOT NULL",
       &[&job,&turn.session_id,&turn.turn_id,&(p.scope==Scope::All),&p.principal])?.ok_or(SubmissionError::NotFound)?;
    let mut evidence = capture(tx, turn, false)?;
    evidence.output = row.get(0);
    Ok(evidence)
}

pub(crate) fn summary(row: &postgres::Row) -> Value {
    json!({
        "id":row.get::<_, String>("id"),
        "dataset_version":row.get::<_, i32>("version"),
        "created_ms":row.get::<_, i64>("created_ms"),
        "status":if row.get::<_, bool>("ready") { "completed" } else { "queued" },
    })
}

pub(crate) fn validate_check(check: &Check) -> Result<()> {
    ensure!(
        !check.expected.trim().is_empty()
            && check.expected.len() <= 4000
            && check.expected == openworker_runtime::telemetry::redact(&check.expected),
        SubmissionError::InvalidRequest
    );
    Ok(())
}

impl PostgresStore {
    pub fn eval_evidence(&self, turn: Turn, batch_id: Option<String>) -> Result<Evidence> {
        validate_id(&turn.session_id, 80)?;
        validate_id(&turn.turn_id, 80)?;
        self.call(move |db| {
            let mut tx = db.client.transaction()?;
            let p = identity(
                &mut tx,
                db.auth.as_ref(),
                Action::Read,
                Some(&turn.session_id),
            )?;
            let evidence = match batch_id {
                Some(job) => batch_evidence(&mut tx, &p, &job, turn)?,
                None => capture(&mut tx, turn, true)?,
            };
            tx.commit()?;
            Ok(evidence)
        })
    }

    pub fn save_eval_case(&self, id: String, mut request: SaveCase) -> Result<Value> {
        validate_id(&request.turn.session_id, 80)?;
        validate_id(&request.turn.turn_id, 80)?;
        request.dataset = request.dataset.trim().to_owned();
        let name = request
            .name
            .as_deref()
            .map(str::trim)
            .filter(|name| !name.is_empty());
        ensure!(
            name.is_none_or(|name| name.len() <= 400 && !name.chars().any(char::is_control)),
            SubmissionError::InvalidRequest
        );
        let name = name.map(str::to_owned);
        ensure!(
            !request.dataset.is_empty()
                && request.dataset.len() <= 100
                && !request.dataset.chars().any(char::is_control),
            SubmissionError::InvalidRequest
        );
        validate_check(&request.check)?;
        self.call(move |db| {
            let mut tx = db.client.transaction()?;
            let p = identity(&mut tx, db.auth.as_ref(), Action::Submit, Some(&request.turn.session_id))?;
            tx.query_one("SELECT pg_advisory_xact_lock(hashtext('eval.case'),hashtext($1))", &[&id])?;
            let mut evidence = match &request.batch_id {
                Some(job) => batch_evidence(&mut tx, &p, job, request.turn)?,
                None => capture(&mut tx, request.turn, true)?,
            };
            if !request.include_input {
                evidence.input = None;
            }
            let dataset_id = openworker_runtime::id("evalset");
            tx.execute(
                "INSERT INTO eval_datasets(id,owner,name) VALUES($1,$2,$3) ON CONFLICT(owner,name) DO NOTHING",
                &[&dataset_id, &evidence.owner, &request.dataset],
            )?;
            let dataset = tx.query_one(
                "SELECT id,version FROM eval_datasets WHERE owner=$1 AND name=$2 FOR UPDATE",
                &[&evidence.owner, &request.dataset],
            )?;
            let dataset_id: String = dataset.get(0);
            let version: i32 = dataset.get(1);
            let mut source = serde_json::to_value(&evidence.turn)?;
            source["name"] = json!(openworker_runtime::telemetry::redact(name.as_deref().unwrap_or(&evidence.name)));
            if let Some(job) = &request.batch_id { source["batch_id"] = json!(job); }
            let check = serde_json::to_value(&request.check)?;
            let evidence = json!({"input":evidence.input,"output":evidence.output});
            if let Some(existing) = tx.query_opt(
                "SELECT dataset,version,source,evidence,check_spec FROM eval_cases WHERE id=$1",
                &[&id],
            )? {
                ensure!(
                    existing.get::<_, String>(0) == dataset_id
                        && existing.get::<_, Value>(2) == source
                        && existing.get::<_, Value>(3) == evidence
                        && existing.get::<_, Value>(4) == check,
                    SubmissionError::Conflict
                );
                tx.commit()?;
                return Ok(json!({"id":id,"dataset_id":dataset_id,"dataset_version":existing.get::<_, i32>(1)}));
            }
            ensure!(case_count(&mut tx,&dataset_id,version)? < MAX_CASES, SubmissionError::Busy);
            let version = version.checked_add(1).ok_or(SubmissionError::Busy)?;
            tx.execute(
                "INSERT INTO eval_cases(id,dataset,version,source,evidence,check_spec) VALUES($1,$2,$3,$4,$5,$6)",
                &[&id, &dataset_id, &version, &source, &evidence, &check],
            )?;
            tx.execute("UPDATE eval_datasets SET version=$2 WHERE id=$1", &[&dataset_id, &version])?;
            tx.commit()?;
            Ok(json!({"id":id,"dataset_id":dataset_id,"dataset_version":version}))
        })
    }

    pub fn revise_eval_case(
        &self,
        id: String,
        dataset: String,
        case: String,
        request: ReviseCase,
    ) -> Result<Value> {
        validate_id(&dataset, 100)?;
        validate_id(&case, 100)?;
        if let Some(name) = &request.name {
            check_name(name)?;
        }
        if let Some(check) = &request.check {
            validate_check(check)?;
        }
        ensure!(
            if request.archive {
                request.name.is_none() && request.check.is_none()
            } else {
                request.name.is_some() && request.check.is_some()
            },
            SubmissionError::InvalidRequest
        );
        self.call(move |db| {
            let mut tx = db.client.transaction()?;
            let p = identity(&mut tx, db.auth.as_ref(), Action::Submit,None)?;
            let current: i32 = tx.query_opt("SELECT version FROM eval_datasets WHERE id=$1 AND ($2 OR owner=$3) FOR UPDATE",
                &[&dataset,&(p.scope==Scope::All),&p.principal])?.ok_or(SubmissionError::NotFound)?.get(0);
            let old = tx.query_opt("SELECT COALESCE(case_key,id),source,evidence,check_spec FROM eval_cases
                WHERE dataset=$1 AND id=$2 AND NOT archived", &[&dataset,&case])?.ok_or(SubmissionError::NotFound)?;
            let key: String = old.get(0);
            let mut source: Value = old.get(1);
            let check: Value = match request.check { Some(check)=>json!(check), None=>old.get(3) };
            if let Some(name)=request.name { source["name"]=json!(openworker_runtime::telemetry::redact(name.trim())); }
            if let Some(row)=tx.query_opt("SELECT version,dataset,case_key,source,check_spec,archived FROM eval_cases WHERE id=$1", &[&id])? {
                ensure!(row.get::<_,String>(1)==dataset && row.get::<_,Option<String>>(2).as_deref()==Some(&key)
                    && row.get::<_,Value>(3)==source && row.get::<_,Value>(4)==check && row.get::<_,bool>(5)==request.archive,
                    SubmissionError::Conflict);
                return Ok(json!({"id":id,"dataset_version":row.get::<_,i32>(0)}));
            }
            let latest: String = tx.query_one("SELECT id FROM eval_cases WHERE dataset=$1 AND COALESCE(case_key,id)=$2 ORDER BY version DESC LIMIT 1", &[&dataset,&key])?.get(0);
            ensure!(latest==case, SubmissionError::Conflict);
            let version = current.checked_add(1).ok_or(SubmissionError::Busy)?;
            let evidence: Value = old.get(2);
            tx.execute("INSERT INTO eval_cases(id,dataset,version,source,evidence,check_spec,case_key,archived)
                VALUES($1,$2,$3,$4,$5,$6,$7,$8)", &[&id,&dataset,&version,&source,&evidence,&check,&key,&request.archive])?;
            tx.execute("UPDATE eval_datasets SET version=$2 WHERE id=$1", &[&dataset,&version])?;
            tx.commit()?;
            Ok(json!({"id":id,"dataset_version":version}))
        })
    }

    pub fn eval_datasets(&self, filter: DatasetList) -> Result<Value> {
        let (q, limit) = list_page(filter.q, filter.after.as_deref(), filter.limit)?;
        if let Some(owner) = &filter.owner {
            validate_id(owner, 64)?;
        }
        self.call(move |db| {
            let mut tx = db.client.transaction()?;
            let p = identity(&mut tx, db.auth.as_ref(), Action::Read, None)?;
            let rows = tx.query(
                "SELECT d.id,d.name,d.owner,d.version,
                    (SELECT count(*) FROM (SELECT DISTINCT ON (COALESCE(c.case_key,c.id)) c.archived
                     FROM eval_cases c WHERE c.dataset=d.id ORDER BY COALESCE(c.case_key,c.id),c.version DESC) a WHERE NOT a.archived) AS case_count,
                    (SELECT j.created_ms FROM eval_jobs j WHERE j.dataset=d.id
                     ORDER BY j.created_ms DESC,j.id DESC LIMIT 1) AS last_run_ms
                 FROM eval_datasets d
                 LEFT JOIN eval_datasets anchor ON anchor.id=$4 AND ($1 OR anchor.owner=$2)
                 WHERE ($1 OR d.owner=$2) AND ($3::text IS NULL OR d.owner=$3)
                   AND ($4::text IS NULL OR (lower(d.name),d.id)>(lower(anchor.name),anchor.id))
                   AND ($5='' OR strpos(lower(d.name),lower($5))>0 OR strpos(lower(d.owner),lower($5))>0)
                 ORDER BY lower(d.name),d.id LIMIT $6",
                &[&(p.scope == Scope::All), &p.principal, &filter.owner, &filter.after, &q, &(limit + 1)],
            )?;
            let page = &rows[..rows.len().min(limit as usize)];
            let next_cursor = (rows.len() > page.len()).then(|| page.last().unwrap().get::<_, String>(0));
            Ok(json!({"datasets":page.iter().map(|row| json!({
                "id":row.get::<_, String>(0), "name":row.get::<_, String>(1),
                "owner":row.get::<_, String>(2), "version":row.get::<_, i32>(3),
                "case_count":row.get::<_,i64>(4), "last_run_ms":row.get::<_, Option<i64>>(5),
            })).collect::<Vec<_>>(), "next_cursor":next_cursor}))
        })
    }

    pub fn eval_runs(&self, filter: RunList) -> Result<Value> {
        let (q, limit) = list_page(filter.q, filter.after.as_deref(), filter.limit)?;
        if let Some(dataset) = &filter.dataset {
            validate_id(dataset, 100)?;
        }
        ensure!(
            filter
                .since
                .is_none_or(|since| (0..=253402300799000).contains(&since)),
            SubmissionError::InvalidRequest
        );
        let ready = filter
            .status
            .map(|status| matches!(status, RunStatus::Completed));
        self.call(move |db| {
            let mut tx = db.client.transaction()?;
            let p = identity(&mut tx, db.auth.as_ref(), Action::Read, None)?;
            if let Some(id) = &filter.dataset {
                owned_dataset(&mut tx, &p, id)?;
            }
            let rows = tx.query(
                "WITH anchor AS (
                    SELECT j.created_ms,j.id FROM eval_jobs j LEFT JOIN eval_datasets d ON d.id=j.dataset
                    WHERE j.id=$4 AND ($1 OR COALESCE(d.owner,j.owner)=$2)
                 ), page AS MATERIALIZED (
                    SELECT j.id,j.dataset,j.version,j.created_ms,j.results IS NOT NULL AS ready,
                        COALESCE(d.name,j.evaluator->>'name') AS display_name,COALESCE(d.owner,j.owner) AS owner,
                        j.selection->>'tag' AS tag
                    FROM eval_jobs j LEFT JOIN eval_datasets d ON d.id=j.dataset
                    LEFT JOIN anchor ON TRUE
                    WHERE ($1 OR COALESCE(d.owner,j.owner)=$2) AND ($3::text IS NULL OR j.dataset=$3)
                      AND ($4::text IS NULL OR (j.created_ms,j.id)<(anchor.created_ms,anchor.id))
                      AND ($5='' OR strpos(lower(COALESCE(d.name,j.evaluator->>'name')),lower($5))>0
                           OR strpos(lower(COALESCE(d.owner,j.owner)),lower($5))>0
                           OR strpos(j.selection->>'tag',lower($5))>0 OR strpos(j.id,lower($5))>0)
                      AND ($6::bool IS NULL OR (j.results IS NOT NULL)=$6)
                      AND ($7::bigint IS NULL OR j.created_ms >= $7)
                    ORDER BY j.created_ms DESC,j.id DESC LIMIT $8
                 )
                 SELECT p.*, CASE WHEN p.ready THEN (
                    SELECT jsonb_build_object(
                        'passed',count(*) FILTER(WHERE item->'result'->'score'->'value'='true'::jsonb),
                        'failed',count(*) FILTER(WHERE item->'result'->'score'->'value'='false'::jsonb),
                        'errors',count(*) FILTER(WHERE item->'result'->>'status'='error')
                    ) FROM jsonb_array_elements(j.results) AS item
                 ) END AS counts
                 FROM page p JOIN eval_jobs j ON j.id=p.id
                 ORDER BY p.created_ms DESC,p.id DESC",
                &[&(p.scope == Scope::All), &p.principal, &filter.dataset, &filter.after, &q,
                  &ready, &filter.since, &(limit + 1)],
            )?;
            let page = &rows[..rows.len().min(limit as usize)];
            let next_cursor = (rows.len() > page.len())
                .then(|| page.last().unwrap().get::<_, String>("id"));
            let runs = page.iter().map(|row| {
                let mut run = summary(row);
                let dataset: Option<String> = row.get("dataset");
                run["dataset_name"] = json!(dataset.as_ref().map(|_| row.get::<_, String>("display_name")));
                run["dataset_id"] = json!(dataset);
                run["name"] = json!(row.get::<_, String>("display_name"));
                run["tag"] = json!(row.get::<_, Option<String>>("tag"));
                run["owner"] = json!(row.get::<_, String>("owner"));
                run["counts"] = json!(row.get::<_, Option<Value>>("counts"));
                run
            }).collect::<Vec<_>>();
            Ok(json!({"runs":runs,"next_cursor":next_cursor}))
        })
    }

    pub fn eval_dataset(&self, id: String, requested: Option<i32>) -> Result<Value> {
        validate_id(&id, 100)?;
        self.call(move |db| {
            let mut tx = db.client.transaction()?;
            let p = identity(&mut tx, db.auth.as_ref(), Action::Read, None)?;
            let (name, latest) = owned_dataset(&mut tx, &p, &id)?;
            let version = requested.unwrap_or(latest);
            ensure!((0..=latest).contains(&version), SubmissionError::NotFound);
            let rows = tx.query(
                &format!("SELECT id,version,source,check_spec FROM ({CASE_SNAPSHOT}) cases WHERE NOT archived ORDER BY version"),
                &[&id, &version],
            )?;
            let jobs = tx.query(
                "SELECT id,version,created_ms,results IS NOT NULL AS ready FROM eval_jobs WHERE dataset=$1
                 ORDER BY created_ms DESC,id DESC LIMIT 50",
                &[&id],
            )?;
            Ok(json!({"id":id,"name":name,"version":version,"latest_version":latest,"jobs":jobs.iter().map(summary).collect::<Vec<_>>(),
                "cases":rows.iter().map(|row| json!({
                    "id":row.get::<_, String>(0), "version":row.get::<_, i32>(1), "turn":row.get::<_, Value>(2),
                    "check":row.get::<_, Value>(3)
                })).collect::<Vec<_>>() }))
        })
    }

    pub fn eval_case(&self, dataset: String, case: String) -> Result<Value> {
        validate_id(&dataset, 100)?;
        validate_id(&case, 100)?;
        self.call(move |db| {
            let mut tx = db.client.transaction()?;
            let p = identity(&mut tx, db.auth.as_ref(), Action::Read, None)?;
            owned_dataset(&mut tx, &p, &dataset)?;
            let row = tx
                .query_opt(
                    "SELECT source,evidence,check_spec FROM eval_cases WHERE dataset=$1 AND id=$2",
                    &[&dataset, &case],
                )?
                .ok_or(SubmissionError::NotFound)?;
            Ok(json!({
                "turn":row.get::<_, Value>(0),
                "evidence":row.get::<_, Value>(1),
                "check":row.get::<_, Value>(2),
            }))
        })
    }

    pub fn enqueue_eval(&self, id: String, dataset: String, version: i32) -> Result<Value> {
        validate_id(&dataset, 100)?;
        self.call(move |db| {
            let mut tx = db.client.transaction()?;
            let p = identity(&mut tx, db.auth.as_ref(), Action::Submit, None)?;
            let (_, current) = owned_dataset(&mut tx, &p, &dataset)?;
            ensure!((1..=current).contains(&version), SubmissionError::InvalidRequest);
            let count=case_count(&mut tx,&dataset,version)?;
            ensure!(count>0,SubmissionError::InvalidRequest);
            let now = openworker_runtime::store::now_ms() as i64;
            tx.execute(
                "INSERT INTO eval_jobs(id,dataset,version,created_ms,target_count) VALUES($1,$2,$3,$4,$5) ON CONFLICT(id) DO NOTHING",
                &[&id, &dataset, &version, &now,&count],
            )?;
            let row = tx.query_one(
                "SELECT id,version,created_ms,results IS NOT NULL AS ready,dataset FROM eval_jobs WHERE id=$1",
                &[&id],
            )?;
            ensure!(row.get::<_, String>("dataset") == dataset && row.get::<_, i32>("version") == version, SubmissionError::Conflict);
            let result = summary(&row);
            tx.commit()?;
            Ok(result)
        })
    }

    pub fn eval_job(&self, dataset: String, job: String) -> Result<Value> {
        validate_id(&dataset, 100)?;
        validate_id(&job, 100)?;
        self.call(move |db| {
            let mut tx = db.client.transaction()?;
            let p = identity(&mut tx, db.auth.as_ref(), Action::Read, None)?;
            owned_dataset(&mut tx, &p, &dataset)?;
            let row = tx.query_opt(
                "SELECT id,version,created_ms,results IS NOT NULL AS ready,results,processed,COALESCE(target_count,version) AS total FROM eval_jobs WHERE dataset=$1 AND id=$2",
                &[&dataset, &job],
            )?.ok_or(SubmissionError::NotFound)?;
            let mut result = summary(&row);
            let total: i32 = row.get("total");
            let processed = if row.get::<_, bool>("ready") { total } else { row.get("processed") };
            result["progress"] = json!({"completed":processed,"total":total});
            result["results"] = row.get::<_, Option<Value>>("results").unwrap_or(Value::Null);
            Ok(result)
        })
    }

    /// Checkpoint a bounded group of deterministic cases. The job-row lock
    /// prevents competing replicas from scoring the same group.
    pub fn score_next_eval(&self) -> Result<bool> {
        self.call(move |db| {
            ensure!(db.auth.is_none(), auth::Error::Forbidden);
            let mut tx = db.client.transaction()?;
            let Some(job) = tx.query_opt(
                "SELECT id,dataset,version,processed,partial_results,evaluator,COALESCE(target_count,version) FROM eval_jobs WHERE results IS NULL AND judge_model IS NULL
                 ORDER BY created_ms,id LIMIT 1 FOR UPDATE SKIP LOCKED",
                &[],
            )? else {
                return Ok(false);
            };
            let id: String = job.get(0);
            let dataset: Option<String> = job.get(1);
            let version: i32 = job.get(2);
            let total: i32 = job.get(6);
            let mut processed: i32 = job.get(3);
            let mut results = match job.get::<_, Option<Value>>(4) {
                Some(value) => serde_json::from_value::<Vec<Value>>(value)?,
                None => Vec::new(),
            };
            let evaluator: Option<crate::eval_selection::SavedEvaluator> = job.get::<_, Option<Value>>(5)
                .map(serde_json::from_value).transpose()?;
            let rows = if let Some(dataset) = dataset {
                tx.query(&format!("SELECT id,ordinal,source,output,check_spec FROM (
                    SELECT id,row_number() OVER (ORDER BY version)::int AS ordinal,source,evidence->>'output' AS output,check_spec
                    FROM ({CASE_SNAPSHOT}) cases WHERE NOT archived) ordered WHERE ordinal>$3 ORDER BY ordinal LIMIT $4"),
                    &[&dataset, &version, &processed, &SCORE_BATCH])?
            } else {
                tx.query("SELECT NULL::text,ordinal,source,output,NULL::jsonb FROM eval_job_targets
                 WHERE job=$1 AND ordinal>$2 ORDER BY ordinal LIMIT $3", &[&id, &processed, &SCORE_BATCH])?
            };
            ensure!(!rows.is_empty(), "Evaluation case snapshot is incomplete");
            for row in rows {
                let case = row.get::<_, Option<String>>(0);
                let ordinal: i32 = row.get(1);
                ensure!(ordinal == processed + 1 && ordinal <= total, "Evaluation case snapshot is incomplete");
                processed = ordinal;
                let output = row.get::<_, Option<String>>(3);
                let spec: Check = match &evaluator {
                    Some(evaluator) => evaluator.check.clone().ok_or_else(|| anyhow::anyhow!("Missing deterministic evaluator"))?,
                    None => serde_json::from_value(row.get(4))?,
                };
                let score = output.as_deref().map(|text| Score::Boolean(spec.passes(text)));
                let missing = score.is_none();
                let result = Input {
                    id: format!("{id}-{ordinal}"),
                    name: "response-check".into(),
                    evaluator: Evaluator {
                        name: evaluator.as_ref().map_or_else(|| "response-text".into(), |e| e.name.clone()),
                        version: evaluator.as_ref().map_or_else(|| "1".into(), |e| e.version.to_string()),
                        source: Source::Code,
                        model: None,
                        usage: None,
                    },
                    status: if missing { Status::Error } else { Status::Completed },
                    score,
                    created_ms: openworker_runtime::store::now_ms(),
                    explanation: missing.then(|| "Final response was unavailable for scoring.".into()),
                    dataset_case: case,
                    experiment: Some(id.clone()),
                    supersedes: None,
                };
                results.push(json!({"turn":row.get::<_, Value>(2),"result":result}));
            }
            tx.execute(
                "UPDATE eval_jobs SET
                    processed=$3,
                    results=CASE WHEN $3=COALESCE(target_count,version) THEN $2::jsonb END,
                    partial_results=CASE WHEN $3<COALESCE(target_count,version) THEN $2::jsonb END
                 WHERE id=$1",
                &[&id, &json!(results), &processed],
            )?;
            tx.commit()?;
            Ok(true)
        })
    }
}
