//! Tagged turn selection and versioned deterministic evaluators.
use crate::{
    auth::{self, Action, Scope},
    evals::{capture, identity, summary, validate_check, Check, Turn, MAX_CASES},
    guardrail_query::{HIT_OUTCOMES, NORMALIZED_OUTCOME},
    submissions::validate_id,
    PostgresStore, SubmissionError,
};
use anyhow::{ensure, Result};
use postgres::{Row, Transaction};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::HashSet;

const MAX_TIME: i64 = 253_402_300_799_000;

pub(crate) fn migrate(tx: &mut Transaction<'_>) -> Result<()> {
    tx.batch_execute(
        "CREATE TABLE IF NOT EXISTS eval_tags(
            session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            turn_id TEXT NOT NULL DEFAULT '', tag TEXT NOT NULL, assigned_ms BIGINT NOT NULL,
            PRIMARY KEY(session_id,turn_id,tag)
        );
        CREATE INDEX IF NOT EXISTS eval_tags_selection ON eval_tags(tag,assigned_ms DESC,session_id,turn_id);
        CREATE TABLE IF NOT EXISTS eval_evaluators(
            id TEXT PRIMARY KEY, owner TEXT NOT NULL, name TEXT NOT NULL, version INTEGER NOT NULL,
            check_spec JSONB NOT NULL, UNIQUE(owner,name,version)
        );
        ALTER TABLE eval_evaluators ADD COLUMN IF NOT EXISTS judge_spec JSONB;
        ALTER TABLE eval_jobs ADD COLUMN IF NOT EXISTS judge_model JSONB;
        ALTER TABLE eval_jobs ADD COLUMN IF NOT EXISTS judge_lease TEXT;
        ALTER TABLE eval_jobs ADD COLUMN IF NOT EXISTS judge_until TIMESTAMPTZ;
        ALTER TABLE eval_jobs ADD COLUMN IF NOT EXISTS judge_attempts INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE eval_jobs ADD COLUMN IF NOT EXISTS judge_credential TEXT;
        ALTER TABLE eval_jobs ADD COLUMN IF NOT EXISTS judge_retry_of TEXT;
        CREATE TABLE IF NOT EXISTS eval_job_targets(
            job TEXT NOT NULL REFERENCES eval_jobs(id) ON DELETE CASCADE,
            ordinal INTEGER NOT NULL, session_id TEXT NOT NULL, turn_id TEXT NOT NULL,
            source JSONB NOT NULL, output TEXT, PRIMARY KEY(job,ordinal),
            UNIQUE(job,session_id,turn_id)
        );
        CREATE INDEX IF NOT EXISTS eval_job_targets_source ON eval_job_targets(session_id,turn_id,job);
        CREATE INDEX IF NOT EXISTS events_guardrail_rule ON events((data->>'check'),session_id,cursor)
          WHERE kind='safety_evaluation';",
    )?;
    Ok(())
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct UpdateTag {
    tag: String,
    enabled: bool,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SaveEvaluator {
    name: String,
    check: Option<Check>,
    pub judge: Option<crate::eval_judge::Rubric>,
}

#[derive(Serialize, Deserialize)]
pub(crate) struct SavedEvaluator {
    id: String,
    pub(crate) name: String,
    pub(crate) version: i32,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(crate) check: Option<Check>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(crate) judge: Option<crate::eval_judge::Rubric>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct EvaluatorList {
    q: Option<String>,
    after: Option<String>,
}

impl SavedEvaluator {
    fn from_row(row: &Row) -> Result<Self> {
        Ok(Self {
            id: row.get("id"),
            name: row.get("name"),
            version: row.get("version"),
            check: serde_json::from_value(row.get("check_spec"))?,
            judge: row
                .get::<_, Option<Value>>("judge_spec")
                .map(serde_json::from_value)
                .transpose()?,
        })
    }
}

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TagFilter {
    pub(crate) tag: String,
    pub(crate) from_ms: Option<i64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(crate) before_ms: Option<i64>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Enqueue {
    pub(crate) evaluator_id: String,
    pub(crate) filter: TagFilter,
    pub(crate) turns: Vec<Turn>,
}

pub(crate) fn validate_tag(tag: &str, allow_guardrail: bool) -> Result<()> {
    let guardrail = allow_guardrail && tag.starts_with("guardrail:");
    let id = if guardrail {
        &tag["guardrail:".len()..]
    } else {
        tag
    };
    validate_id(id, if guardrail { 128 } else { 64 })?;
    ensure!(
        id.bytes().all(|b| {
            b.is_ascii_lowercase()
                || b.is_ascii_digit()
                || b"._-".contains(&b)
                || (guardrail && b.is_ascii_uppercase())
        }),
        SubmissionError::InvalidRequest
    );
    Ok(())
}

impl TagFilter {
    fn validate(&self) -> Result<()> {
        validate_tag(&self.tag, true)?;
        ensure!(
            self.from_ms
                .is_none_or(|from| (0..=MAX_TIME).contains(&from)),
            SubmissionError::InvalidRequest
        );
        ensure!(
            self.before_ms
                .is_none_or(|before| (self.from_ms.unwrap_or(0)..=MAX_TIME).contains(&before)),
            SubmissionError::InvalidRequest
        );
        Ok(())
    }
}

fn validate_source(session_id: &str, turn_id: Option<&str>) -> Result<()> {
    validate_id(session_id, 80)?;
    if let Some(turn_id) = turn_id {
        validate_id(turn_id, 80)?;
    }
    Ok(())
}

fn candidates(
    tx: &mut Transaction<'_>,
    principal: &auth::Identity,
    filter: &TagFilter,
    selected: Option<&[Turn]>,
) -> Result<Vec<Row>> {
    let sql = format!(
        "WITH tagged AS (
            SELECT session_id, NULLIF(turn_id,'') AS turn_id, assigned_ms
            FROM eval_tags WHERE tag=$1 AND assigned_ms >= $2 AND assigned_ms < $7
            UNION ALL
            SELECT a.session_id, COALESCE(e.turn_id,s.turn_id), (extract(epoch FROM a.created)*1000)::bigint
            FROM events e JOIN audit a USING(session_id,cursor)
            LEFT JOIN submissions s ON e.turn_id IS NULL AND s.session_id=e.session_id
                AND s.id=e.data->>'submission_id'
            WHERE starts_with($1,'guardrail:') AND e.kind='safety_evaluation'
              AND a.kind='safety_evaluation' AND e.data->>'check'=substring($1 from 11)
              AND a.created >= TIMESTAMPTZ 'epoch'+$2*INTERVAL '1 millisecond'
              AND a.created < TIMESTAMPTZ 'epoch'+$7*INTERVAL '1 millisecond'
              AND COALESCE(e.turn_id,s.turn_id) IS NOT NULL
              AND ({NORMALIZED_OUTCOME}) IN {HIT_OUTCOMES}
        ), eligible AS (
            SELECT q.session_id,q.turn_id,max(t.assigned_ms) AS tagged_ms
            FROM tagged t JOIN submissions q ON q.session_id=t.session_id
                AND (t.turn_id IS NULL OR t.turn_id=q.turn_id)
            JOIN api_session_owners o ON o.session_id=q.session_id
            WHERE ($3 OR o.principal=$4)
              AND q.status IN ('completed','failed','cancelled','interrupted','truncated','blocked')
              AND ($5::jsonb IS NULL OR EXISTS(
                  SELECT 1 FROM jsonb_to_recordset($5) AS wanted(session_id text,turn_id text)
                  WHERE (wanted.session_id,wanted.turn_id)=(q.session_id,q.turn_id)))
            GROUP BY q.session_id,q.turn_id
        )
        SELECT f.session_id,f.turn_id,f.tagged_ms,q.status,
            (extract(epoch FROM q.created)*1000)::bigint AS created_ms,
            left(s.data->>'name',100) AS name
        FROM eligible f JOIN submissions q USING(session_id,turn_id)
        JOIN sessions s ON s.id=f.session_id
        ORDER BY f.tagged_ms DESC,q.created DESC,f.session_id DESC,f.turn_id DESC LIMIT $6"
    );
    let selected = selected.map(|turns| json!(turns));
    Ok(tx.query(
        &sql,
        &[
            &filter.tag,
            &filter.from_ms.unwrap_or(0),
            &(principal.scope == Scope::All),
            &principal.principal,
            &selected,
            &i64::from(MAX_CASES + 1),
            &filter.before_ms.unwrap_or(MAX_TIME),
        ],
    )?)
}

fn source(row: &Row) -> Value {
    json!({
        "session_id":row.get::<_,String>("session_id"), "turn_id":row.get::<_,String>("turn_id"),
        "name":row.get::<_,Option<String>>("name"), "status":row.get::<_,String>("status"),
        "tagged_ms":row.get::<_,i64>("tagged_ms"), "created_ms":row.get::<_,i64>("created_ms"),
    })
}

impl PostgresStore {
    pub fn eval_tags(&self, sid: String, tid: Option<String>) -> Result<Value> {
        validate_source(&sid, tid.as_deref())?;
        self.call(move |db| {
            let mut tx = db.client.transaction()?;
            let p = identity(&mut tx, db.auth.as_ref(), Action::Read, Some(&sid))?;
            let rows = tx.query(
                "SELECT tag,assigned_ms FROM eval_tags WHERE session_id=$1 AND turn_id=$2 ORDER BY tag",
                &[&sid, &tid.as_deref().unwrap_or("")],
            )?;
            let tags: Vec<_> = rows.iter().map(|row|
                json!({"tag":row.get::<_,String>(0),"assigned_ms":row.get::<_,i64>(1)})).collect();
            let results = match &tid {
                Some(tid) => tx.query(
                    "SELECT j.id,j.results->(t.ordinal-1)->'result' FROM eval_job_targets t
                     JOIN eval_jobs j ON j.id=t.job
                     WHERE (t.session_id,t.turn_id)=($1,$2) AND ($3 OR j.owner=$4) AND j.results IS NOT NULL
                     ORDER BY j.created_ms DESC,j.id DESC LIMIT 20",
                    &[&sid, &tid, &(p.scope == Scope::All), &p.principal],
                )?.iter().map(|row|
                    json!({"job_id":row.get::<_,String>(0),"result":row.get::<_,Value>(1)})).collect(),
                None => Vec::new(),
            };
            Ok(json!({"tags":tags,"evaluations":results}))
        })
    }

    pub fn update_eval_tag(
        &self,
        sid: String,
        tid: Option<String>,
        request: UpdateTag,
    ) -> Result<()> {
        validate_source(&sid, tid.as_deref())?;
        validate_tag(&request.tag, false)?;
        self.call(move |db| {
            let mut tx = db.client.transaction()?;
            identity(&mut tx, db.auth.as_ref(), Action::Submit, Some(&sid))?;
            let session =
                tx.query_opt("SELECT id FROM sessions WHERE id=$1 FOR UPDATE", &[&sid])?;
            ensure!(session.is_some(), SubmissionError::NotFound);
            if let Some(tid) = &tid {
                let row = tx.query_opt(
                    "SELECT 1 FROM submissions WHERE session_id=$1 AND turn_id=$2",
                    &[&sid, &tid],
                )?;
                ensure!(row.is_some(), SubmissionError::NotFound);
            }
            let turn = tid.as_deref().unwrap_or("");
            if request.enabled {
                let tags: i64 = tx.query_one(
                    "SELECT count(*) FROM eval_tags WHERE session_id=$1 AND turn_id=$2 AND tag<>$3",
                    &[&sid,&turn,&request.tag],
                )?.get(0);
                ensure!(tags < 32, SubmissionError::InvalidRequest);
                tx.execute(
                    "INSERT INTO eval_tags(session_id,turn_id,tag,assigned_ms)
                     VALUES($1,$2,$3,(extract(epoch FROM clock_timestamp())*1000)::bigint)
                     ON CONFLICT DO NOTHING",
                    &[&sid, &turn, &request.tag],
                )?;
            } else {
                tx.execute(
                    "DELETE FROM eval_tags WHERE session_id=$1 AND turn_id=$2 AND tag=$3",
                    &[&sid, &turn, &request.tag],
                )?;
            }
            tx.commit()?;
            Ok(())
        })
    }

    pub fn eval_evaluators(&self, filter: EvaluatorList) -> Result<Value> {
        let q = filter.q.unwrap_or_default().trim().to_owned();
        ensure!(
            q.len() <= 100 && !q.chars().any(char::is_control),
            SubmissionError::InvalidRequest
        );
        if let Some(after) = &filter.after {
            validate_id(after, 100)?;
        }
        self.call(move |db| {
            let mut tx = db.client.transaction()?;
            let p = identity(&mut tx, db.auth.as_ref(), Action::Read, None)?;
            let rows = tx.query(
                "SELECT DISTINCT ON (name) id,name,version,check_spec,judge_spec FROM eval_evaluators
                 WHERE owner=$1 AND ($2='' OR strpos(lower(name),lower($2))>0)
                   AND ($3::text IS NULL OR name>(SELECT name FROM eval_evaluators WHERE owner=$1 AND id=$3))
                 ORDER BY name,version DESC LIMIT 21",
                &[&p.principal, &q, &filter.after],
            )?;
            let page = &rows[..rows.len().min(20)];
            let next = (rows.len() > page.len()).then(|| page.last().unwrap().get::<_,String>("id"));
            let evaluators = page.iter().map(SavedEvaluator::from_row).collect::<Result<Vec<_>>>()?;
            Ok(json!({"evaluators":evaluators,"next_cursor":next}))
        })
    }

    pub fn save_eval_evaluator(&self, id: String, request: SaveEvaluator) -> Result<Value> {
        let name = request.name.trim().to_owned();
        validate_id(&name, 100)?;
        ensure!(
            !name.chars().any(char::is_control)
                && name == openworker_runtime::telemetry::redact(&name),
            SubmissionError::InvalidRequest
        );
        ensure!(
            request.check.is_some() != request.judge.is_some(),
            SubmissionError::InvalidRequest
        );
        if let Some(check) = &request.check {
            validate_check(check)?;
        }
        if let Some(rubric) = &request.judge {
            rubric.validate()?;
        }
        self.call(move |db| {
            let mut tx = db.client.transaction()?;
            let p = identity(&mut tx, db.auth.as_ref(), Action::Submit, None)?;
            if let Some(rubric)=&request.judge { crate::eval_judge::require_profile(&mut tx,&p,&rubric.profile)?; }
            tx.query_one(
                "SELECT pg_advisory_xact_lock(hashtext($1),hashtext($2))",
                &[&p.principal, &name],
            )?;
            let spec = json!(request.check);
            tx.execute(
                "INSERT INTO eval_evaluators(id,owner,name,version,check_spec,judge_spec)
                 SELECT $1,$2,$3,COALESCE(max(version),0)+1,$4,$5 FROM eval_evaluators
                 WHERE owner=$2 AND name=$3 ON CONFLICT(id) DO NOTHING",
                &[&id, &p.principal, &name, &spec, &request.judge.as_ref().map(|r|json!(r))],
            )?;
            let row = tx.query_one(
                "SELECT id,name,version,check_spec,judge_spec,owner FROM eval_evaluators WHERE id=$1",
                &[&id],
            )?;
            let saved = SavedEvaluator::from_row(&row)?;
            ensure!(
                row.get::<_, String>("owner") == p.principal
                    && saved.name == name
                    && saved.check == request.check && saved.judge==request.judge,
                SubmissionError::Conflict
            );
            tx.commit()?;
            Ok(json!(saved))
        })
    }

    pub fn eval_candidates(&self, filter: TagFilter) -> Result<Value> {
        filter.validate()?;
        self.call(move |db| {
            let mut tx = db.client.transaction()?;
            let p = identity(&mut tx, db.auth.as_ref(), Action::Read, None)?;
            let rows = candidates(&mut tx, &p, &filter, None)?;
            Ok(json!({
                "turns":rows.iter().take(MAX_CASES as usize).map(source).collect::<Vec<_>>(),
                "truncated":rows.len() > MAX_CASES as usize,
            }))
        })
    }

    pub fn enqueue_tag_eval(
        &self,
        id: String,
        request: Enqueue,
        judges_enabled: bool,
    ) -> Result<Value> {
        request.filter.validate()?;
        validate_id(&request.evaluator_id, 100)?;
        ensure!(
            (1..=MAX_CASES as usize).contains(&request.turns.len()),
            SubmissionError::InvalidRequest
        );
        let mut seen = HashSet::with_capacity(request.turns.len());
        for turn in &request.turns {
            validate_source(&turn.session_id, Some(&turn.turn_id))?;
            ensure!(
                seen.insert((&turn.session_id, &turn.turn_id)),
                SubmissionError::InvalidRequest
            );
        }
        self.call(move |db| {
            let mut tx = db.client.transaction()?;
            let p = identity(&mut tx, db.auth.as_ref(), Action::Submit, None)?;
            tx.query_one("SELECT pg_advisory_xact_lock(hashtext('eval.batch'),hashtext($1))", &[&id])?;
            let selection = json!(request.filter);
            if let Some(row) = tx.query_opt(
                "SELECT id,owner,version,created_ms,results IS NOT NULL AS ready,selection,evaluator->>'id' AS evaluator_id
                 FROM eval_jobs WHERE dataset IS NULL AND id=$1", &[&id],
            )? {
                let ordered = tx.query("SELECT session_id,turn_id FROM eval_job_targets WHERE job=$1 ORDER BY ordinal", &[&id])?;
                ensure!(row.get::<_,String>("owner")==p.principal && row.get::<_,Value>("selection")==selection
                    && row.get::<_,String>("evaluator_id")==request.evaluator_id
                    && ordered.len()==request.turns.len()
                    && ordered.iter().zip(&request.turns).all(|(row,turn)|
                        row.get::<_,String>(0)==turn.session_id && row.get::<_,String>(1)==turn.turn_id),
                    SubmissionError::Conflict);
                return Ok(summary(&row));
            }
            let evaluator = tx.query_opt(
                "SELECT id,name,version,check_spec,judge_spec FROM eval_evaluators WHERE id=$1 AND owner=$2", &[&request.evaluator_id,&p.principal],
            )?.ok_or(SubmissionError::NotFound)?;
            let evaluator = SavedEvaluator::from_row(&evaluator)?;
            let judge_model = if let Some(rubric)=&evaluator.judge {
                ensure!(judges_enabled && request.turns.len()<=usize::from(rubric.max_cases), SubmissionError::InvalidRequest);
                crate::eval_judge::require_profile(&mut tx,&p,&rubric.profile)?;
                Some(json!(crate::model_registry::resolve_profile(&mut tx,&rubric.profile)?.selection))
            } else { None };
            let matched = candidates(&mut tx, &p, &request.filter, Some(&request.turns))?;
            ensure!(matched.len()==request.turns.len(), SubmissionError::Conflict);
            let mut inputs = std::collections::HashMap::new();
            for row in &matched {
                let input = source(row);
                inputs.insert((row.get::<_,String>("session_id"),row.get::<_,String>("turn_id")), input);
            }
            let inserted = tx.query_one(
                "INSERT INTO eval_jobs(id,version,created_ms,owner,selection,evaluator,judge_model,judge_credential)
                 VALUES($1,$2,(extract(epoch FROM clock_timestamp())*1000)::bigint,$3,$4,$5,$6,$7)
                 RETURNING id,version,created_ms,FALSE AS ready",
                &[&id,&(request.turns.len() as i32),&p.principal,&selection,&json!(evaluator),&judge_model,
                    &judge_model.as_ref().map(|_|&p.credential_id)],
            )?;
            let mut judge_input_bytes = 0;
            for (index, turn) in request.turns.into_iter().enumerate() {
                auth::guard(&mut tx, db.auth.as_ref(), Action::Submit, Some(&turn.session_id), None)?;
                let evidence = capture(&mut tx, turn, false)?;
                if let Some(rubric)=&evaluator.judge {
                    judge_input_bytes += rubric.instructions.len() + evidence.output.as_ref().map_or(0,String::len);
                    ensure!(judge_input_bytes<=crate::eval_judge::MAX_JOB_BYTES,SubmissionError::InvalidRequest);
                }
                let source = &inputs[&(evidence.turn.session_id.clone(),evidence.turn.turn_id.clone())];
                tx.execute("INSERT INTO eval_job_targets(job,ordinal,session_id,turn_id,source,output)
                    VALUES($1,$2,$3,$4,$5,$6)", &[&id,&(index as i32 + 1),&evidence.turn.session_id,
                    &evidence.turn.turn_id,source,&evidence.output])?;
            }
            tx.commit()?;
            Ok(summary(&inserted))
        })
    }

    pub fn tagged_eval_job(&self, id: String) -> Result<Value> {
        validate_id(&id, 100)?;
        self.call(move |db| {
            let mut tx = db.client.transaction()?;
            let p = identity(&mut tx, db.auth.as_ref(), Action::Read, None)?;
            let row = tx.query_opt(
                "SELECT id,version,created_ms,results IS NOT NULL AS ready,selection,evaluator,results,processed,judge_model->>'model' AS resolved_model
                 FROM eval_jobs WHERE dataset IS NULL AND id=$1 AND ($2 OR owner=$3)",
                &[&id,&(p.scope==Scope::All),&p.principal],
            )?.ok_or(SubmissionError::NotFound)?;
            let mut job = summary(&row);
            job.as_object_mut().unwrap().remove("dataset_version");
            job["selection"] = row.get("selection");
            job["evaluator"] = row.get("evaluator");
            job["resolved_model"] = json!(row.get::<_,Option<String>>("resolved_model"));
            job["progress"] = json!({"completed":row.get::<_,i32>("processed"),"total":row.get::<_,i32>("version")});
            job["targets"] = json!(tx.query("SELECT source FROM eval_job_targets WHERE job=$1 ORDER BY ordinal",
                &[&id])?.iter().map(|r| r.get::<_,Value>(0)).collect::<Vec<_>>());
            job["results"] = row.get::<_,Option<Value>>("results").unwrap_or(Value::Null);
            Ok(job)
        })
    }

    pub fn retry_eval_judge(&self, id: String, original: String) -> Result<Value> {
        validate_id(&original, 100)?;
        self.call(move|db|{
            let mut tx=db.client.transaction()?;
            let p=identity(&mut tx,db.auth.as_ref(),Action::Submit,None)?;
            let row=tx.query_opt("SELECT selection,evaluator,judge_model,results FROM eval_jobs
                WHERE id=$1 AND owner=$2 AND judge_model IS NOT NULL AND results IS NOT NULL",
                &[&original,&p.principal])?.ok_or(SubmissionError::NotFound)?;
            let evaluator:SavedEvaluator=serde_json::from_value(row.get(1))?;
            let rubric=evaluator.judge.as_ref().ok_or(SubmissionError::InvalidRequest)?;
            crate::eval_judge::require_profile(&mut tx,&p,&rubric.profile)?;
            let inserted=tx.execute("INSERT INTO eval_jobs(id,version,created_ms,owner,selection,evaluator,judge_model,judge_credential,judge_retry_of)
                SELECT $1,count(*)::int,(extract(epoch FROM clock_timestamp())*1000)::bigint,$2,$3,$4,$5,$6,$7
                FROM jsonb_array_elements($8::jsonb) r WHERE r->'result'->>'status'='error'
                HAVING count(*)>0 ON CONFLICT(id) DO NOTHING",
                &[&id,&p.principal,&row.get::<_,Value>(0),&json!(evaluator),&row.get::<_,Value>(2),&p.credential_id,&original,&row.get::<_,Value>(3)])?;
            let found=tx.query_opt("SELECT owner,judge_retry_of FROM eval_jobs WHERE id=$1",&[&id])?
                .ok_or(SubmissionError::InvalidRequest)?;
            ensure!(found.get::<_,String>(0)==p.principal && found.get::<_,Option<String>>(1).as_deref()==Some(original.as_str()),
                SubmissionError::Conflict);
            if inserted>0 {
                tx.execute("INSERT INTO eval_job_targets(job,ordinal,session_id,turn_id,source,output)
                    SELECT $1,row_number() OVER (ORDER BY t.ordinal)::int,t.session_id,t.turn_id,t.source,t.output
                    FROM eval_job_targets t WHERE t.job=$2 AND ($3::jsonb->(t.ordinal-1))->'result'->>'status'='error'",
                    &[&id,&original,&row.get::<_,Value>(3)])?;
            }
            tx.commit()?;
            Ok(json!({"id":id}))
        })
    }
}
