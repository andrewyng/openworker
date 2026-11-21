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

const MAX_CASES: i32 = 100;
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
        ALTER TABLE eval_jobs ADD COLUMN IF NOT EXISTS processed INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE eval_jobs ADD COLUMN IF NOT EXISTS partial_results JSONB;
        CREATE INDEX IF NOT EXISTS eval_jobs_pending
          ON eval_jobs(created_ms,id) WHERE results IS NULL;
        CREATE INDEX IF NOT EXISTS eval_jobs_dataset
          ON eval_jobs(dataset,created_ms DESC,id DESC);
        CREATE INDEX IF NOT EXISTS eval_jobs_recent
          ON eval_jobs(created_ms DESC,id DESC);
        CREATE INDEX IF NOT EXISTS eval_datasets_name ON eval_datasets(lower(name),id);
        CREATE INDEX IF NOT EXISTS eval_datasets_owner_name ON eval_datasets(owner,lower(name),id);",
    )?;
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
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SaveCase {
    pub dataset: String,
    pub name: Option<String>,
    pub turn: Turn,
    pub include_input: bool,
    pub check: Check,
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

fn identity(
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

fn capture(tx: &mut Transaction<'_>, turn: Turn) -> Result<Evidence> {
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
    ensure!(
        row.get::<_, String>(0) == "completed",
        SubmissionError::InvalidRequest
    );
    let messages: Option<Value> = row.get(3);
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

fn owned_dataset(tx: &mut Transaction<'_>, p: &Identity, id: &str) -> Result<(String, i32)> {
    tx.query_opt(
        "SELECT name,version FROM eval_datasets WHERE id=$1 AND ($2 OR owner=$3)",
        &[&id, &(p.scope == Scope::All), &p.principal],
    )?
    .map(|row| (row.get(0), row.get(1)))
    .ok_or_else(|| SubmissionError::NotFound.into())
}

fn summary(row: &postgres::Row) -> Value {
    json!({
        "id":row.get::<_, String>("id"),
        "dataset_version":row.get::<_, i32>("version"),
        "created_ms":row.get::<_, i64>("created_ms"),
        "status":if row.get::<_, bool>("ready") { "completed" } else { "queued" },
    })
}

impl PostgresStore {
    pub fn eval_evidence(&self, turn: Turn) -> Result<Evidence> {
        validate_id(&turn.session_id, 80)?;
        validate_id(&turn.turn_id, 80)?;
        self.call(move |db| {
            let mut tx = db.client.transaction()?;
            identity(
                &mut tx,
                db.auth.as_ref(),
                Action::Read,
                Some(&turn.session_id),
            )?;
            let evidence = capture(&mut tx, turn)?;
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
                && !request.dataset.chars().any(char::is_control)
                && !request.check.expected.trim().is_empty()
                && request.check.expected.len() <= 4000,
            SubmissionError::InvalidRequest
        );
        ensure!(
            request.check.expected
                == openworker_runtime::telemetry::redact(&request.check.expected),
            SubmissionError::InvalidRequest
        );
        self.call(move |db| {
            let mut tx = db.client.transaction()?;
            identity(&mut tx, db.auth.as_ref(), Action::Submit, Some(&request.turn.session_id))?;
            tx.query_one("SELECT pg_advisory_xact_lock(hashtext('eval.case'),hashtext($1))", &[&id])?;
            let mut evidence = capture(&mut tx, request.turn)?;
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
            ensure!(version < MAX_CASES, SubmissionError::Busy);
            let version = version + 1;
            tx.execute(
                "INSERT INTO eval_cases(id,dataset,version,source,evidence,check_spec) VALUES($1,$2,$3,$4,$5,$6)",
                &[&id, &dataset_id, &version, &source, &evidence, &check],
            )?;
            tx.execute("UPDATE eval_datasets SET version=$2 WHERE id=$1", &[&dataset_id, &version])?;
            tx.commit()?;
            Ok(json!({"id":id,"dataset_id":dataset_id,"dataset_version":version}))
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
                "last_run_ms":row.get::<_, Option<i64>>(4),
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
                    SELECT j.created_ms,j.id FROM eval_jobs j JOIN eval_datasets d ON d.id=j.dataset
                    WHERE j.id=$4 AND ($1 OR d.owner=$2)
                 ), page AS MATERIALIZED (
                    SELECT j.id,j.dataset,j.version,j.created_ms,j.results IS NOT NULL AS ready,
                        d.name AS dataset_name,d.owner
                    FROM eval_jobs j JOIN eval_datasets d ON d.id=j.dataset
                    LEFT JOIN anchor ON TRUE
                    WHERE ($1 OR d.owner=$2) AND ($3::text IS NULL OR j.dataset=$3)
                      AND ($4::text IS NULL OR (j.created_ms,j.id)<(anchor.created_ms,anchor.id))
                      AND ($5='' OR strpos(lower(d.name),lower($5))>0 OR strpos(lower(d.owner),lower($5))>0 OR strpos(j.id,lower($5))>0)
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
                run["dataset_id"] = json!(row.get::<_, String>("dataset"));
                run["dataset_name"] = json!(row.get::<_, String>("dataset_name"));
                run["owner"] = json!(row.get::<_, String>("owner"));
                run["counts"] = json!(row.get::<_, Option<Value>>("counts"));
                run
            }).collect::<Vec<_>>();
            Ok(json!({"runs":runs,"next_cursor":next_cursor}))
        })
    }

    pub fn eval_dataset(&self, id: String) -> Result<Value> {
        validate_id(&id, 100)?;
        self.call(move |db| {
            let mut tx = db.client.transaction()?;
            let p = identity(&mut tx, db.auth.as_ref(), Action::Read, None)?;
            let (name, version) = owned_dataset(&mut tx, &p, &id)?;
            let rows = tx.query(
                "SELECT id,version,source,check_spec FROM eval_cases WHERE dataset=$1 AND version<=$2 ORDER BY version",
                &[&id, &version],
            )?;
            let jobs = tx.query(
                "SELECT id,version,created_ms,results IS NOT NULL AS ready FROM eval_jobs WHERE dataset=$1
                 ORDER BY created_ms DESC,id DESC LIMIT 50",
                &[&id],
            )?;
            Ok(json!({"id":id,"name":name,"version":version,"jobs":jobs.iter().map(summary).collect::<Vec<_>>(),
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
            let now = openworker_runtime::store::now_ms() as i64;
            tx.execute(
                "INSERT INTO eval_jobs(id,dataset,version,created_ms) VALUES($1,$2,$3,$4) ON CONFLICT(id) DO NOTHING",
                &[&id, &dataset, &version, &now],
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
                "SELECT id,version,created_ms,results IS NOT NULL AS ready,results,processed FROM eval_jobs WHERE dataset=$1 AND id=$2",
                &[&dataset, &job],
            )?.ok_or(SubmissionError::NotFound)?;
            let mut result = summary(&row);
            let total: i32 = row.get("version");
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
                "SELECT id,dataset,version,processed,partial_results FROM eval_jobs WHERE results IS NULL
                 ORDER BY created_ms,id LIMIT 1 FOR UPDATE SKIP LOCKED",
                &[],
            )? else {
                return Ok(false);
            };
            let id: String = job.get(0);
            let dataset: String = job.get(1);
            let version: i32 = job.get(2);
            let mut processed: i32 = job.get(3);
            let mut results = match job.get::<_, Option<Value>>(4) {
                Some(value) => serde_json::from_value::<Vec<Value>>(value)?,
                None => Vec::new(),
            };
            let rows = tx.query(
                "SELECT id,version,source,evidence->>'output',check_spec FROM eval_cases
                 WHERE dataset=$1 AND version>$3 AND version<=$2 ORDER BY version LIMIT $4",
                &[&dataset, &version, &processed, &SCORE_BATCH],
            )?;
            ensure!(!rows.is_empty(), "Evaluation case snapshot is incomplete");
            for row in rows {
                let case = row.get::<_, String>(0);
                let ordinal: i32 = row.get(1);
                ensure!(ordinal == processed + 1, "Evaluation case snapshot is incomplete");
                processed = ordinal;
                let output = row.get::<_, Option<String>>(3);
                let spec: Check = serde_json::from_value(row.get(4))?;
                let score = output.as_deref().map(|text| Score::Boolean(match spec.comparison {
                    Match::Contains => text.contains(&spec.expected),
                    Match::Equals => text == spec.expected,
                }));
                let missing = score.is_none();
                let result = Input {
                    id: format!("{id}-{ordinal}"),
                    name: "response-check".into(),
                    evaluator: Evaluator {
                        name: "response-text".into(),
                        version: "1".into(),
                        source: Source::Code,
                        model: None,
                        usage: None,
                    },
                    status: if missing { Status::Error } else { Status::Completed },
                    score,
                    created_ms: openworker_runtime::store::now_ms(),
                    explanation: missing.then(|| "Final response was unavailable for scoring.".into()),
                    dataset_case: Some(case),
                    experiment: Some(id.clone()),
                    supersedes: None,
                };
                results.push(json!({"turn":row.get::<_, Value>(2),"result":result}));
            }
            tx.execute(
                "UPDATE eval_jobs SET
                    processed=$3,
                    results=CASE WHEN $3=version THEN $2::jsonb END,
                    partial_results=CASE WHEN $3<version THEN $2::jsonb END
                 WHERE id=$1",
                &[&id, &json!(results), &processed],
            )?;
            tx.commit()?;
            Ok(true)
        })
    }
}
