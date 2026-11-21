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
        CREATE INDEX IF NOT EXISTS eval_jobs_pending
          ON eval_jobs(created_ms,id) WHERE results IS NULL;
        CREATE INDEX IF NOT EXISTS eval_jobs_dataset
          ON eval_jobs(dataset,created_ms DESC,id DESC);",
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

    pub fn eval_datasets(&self) -> Result<Value> {
        self.call(move |db| {
            let mut tx = db.client.transaction()?;
            let p = identity(&mut tx, db.auth.as_ref(), Action::Read, None)?;
            let rows = tx.query(
                "SELECT id,name,owner,version FROM eval_datasets WHERE $1 OR owner=$2
                 ORDER BY lower(name),id",
                &[&(p.scope == Scope::All), &p.principal],
            )?;
            Ok(json!({"datasets":rows.iter().map(|row| json!({
                "id":row.get::<_, String>(0), "name":row.get::<_, String>(1),
                "owner":row.get::<_, String>(2), "version":row.get::<_, i32>(3)
            })).collect::<Vec<_>>() }))
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
                "SELECT id,version,created_ms,results IS NOT NULL AS ready,results FROM eval_jobs WHERE dataset=$1 AND id=$2",
                &[&dataset, &job],
            )?.ok_or(SubmissionError::NotFound)?;
            let mut result = summary(&row);
            result["results"] = row.get::<_, Option<Value>>("results").unwrap_or(Value::Null);
            Ok(result)
        })
    }

    /// The queue and results share one transaction. If the API exits mid-job,
    /// PostgreSQL releases the row and the next API process can score it.
    pub fn score_next_eval(&self) -> Result<bool> {
        self.call(move |db| {
            ensure!(db.auth.is_none(), auth::Error::Forbidden);
            let mut tx = db.client.transaction()?;
            let Some(job) = tx.query_opt(
                "SELECT id,dataset,version FROM eval_jobs WHERE results IS NULL
                 ORDER BY created_ms,id LIMIT 1 FOR UPDATE SKIP LOCKED",
                &[],
            )? else {
                return Ok(false);
            };
            let id: String = job.get(0);
            let dataset: String = job.get(1);
            let version: i32 = job.get(2);
            let rows = tx.query(
                "SELECT id,version,source,evidence->>'output',check_spec FROM eval_cases WHERE dataset=$1 AND version<=$2 ORDER BY version",
                &[&dataset, &version],
            )?;
            let mut results = Vec::with_capacity(rows.len());
            for row in rows {
                let case = row.get::<_, String>(0);
                let output = row.get::<_, Option<String>>(3);
                let spec: Check = serde_json::from_value(row.get(4))?;
                let score = output.as_deref().map(|text| Score::Boolean(match spec.comparison {
                    Match::Contains => text.contains(&spec.expected),
                    Match::Equals => text == spec.expected,
                }));
                let missing = score.is_none();
                let result = Input {
                    id: format!("{}-{}", id, row.get::<_, i32>(1)),
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
                "UPDATE eval_jobs SET results=$2 WHERE id=$1",
                &[&id, &json!(results)],
            )?;
            tx.commit()?;
            Ok(true)
        })
    }
}
