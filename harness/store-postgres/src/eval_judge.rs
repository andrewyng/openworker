//! Bounded model-judge tasks with durable claims. Model calls run outside PostgreSQL.
use crate::{
    auth::{self, Action, Identity, Role},
    evals::identity,
    PostgresStore, SubmissionError,
};
use anyhow::{ensure, Result};
use openworker_observability::evaluations::Input;
use openworker_runtime::connections::Selection;
use postgres::Transaction;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

pub const MAX_JOB_BYTES: usize = 64 * 1024;
pub const OUTPUT_TOKENS: u32 = 256;

#[derive(Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Rubric {
    pub profile: String,
    pub instructions: String,
    pub max_cases: u8,
}
impl Rubric {
    pub(crate) fn validate(&self) -> Result<()> {
        ensure!(
            (1..=10).contains(&self.max_cases)
                && !self.instructions.trim().is_empty()
                && self.instructions.len() <= 4000
                && self.instructions == openworker_runtime::telemetry::redact(&self.instructions)
                && openworker_runtime::bundle::safe_name(&self.profile)
                && self.profile.len() <= 64,
            SubmissionError::InvalidRequest
        );
        Ok(())
    }
}

pub(crate) fn require_profile(tx: &mut Transaction<'_>, p: &Identity, profile: &str) -> Result<()> {
    if p.role == Role::App {
        ensure!(
            crate::app_agents::grants(tx, &p.principal)?
                .model_profiles
                .iter()
                .any(|granted| granted == profile),
            auth::Error::Forbidden
        );
    }
    Ok(())
}

pub struct Claim {
    pub job: String,
    pub position: i32,
    pub lease: String,
    pub name: String,
    pub version: i32,
    pub rubric: Rubric,
    pub model: Selection,
    pub output: Option<String>,
    pub credential: Option<String>,
    pub exhausted: bool,
}

impl PostgresStore {
    pub fn require_eval_judge_profile(&self, profile: &str) -> Result<()> {
        let profile = profile.to_owned();
        self.call(move |db| {
            let mut tx = db.client.transaction()?;
            let p = identity(&mut tx, db.auth.as_ref(), Action::Submit, None)?;
            require_profile(&mut tx, &p, &profile)
        })
    }
    pub fn eval_judge_profiles(&self) -> Result<Vec<String>> {
        self.call(|db| {
            let mut tx = db.client.transaction()?;
            let p = identity(&mut tx, db.auth.as_ref(), Action::Read, None)?;
            let grants = (p.role == Role::App)
                .then(|| {
                    crate::app_agents::grants(&mut tx, &p.principal)
                        .map(|grants| grants.model_profiles)
                })
                .transpose()?;
            Ok(tx
                .query(
                    "SELECT name FROM model_profiles WHERE $1::text[] IS NULL OR name=ANY($1)
                ORDER BY name LIMIT 100",
                    &[&grants],
                )?
                .iter()
                .map(|r| r.get(0))
                .collect())
        })
    }

    pub fn claim_eval_judge(&self) -> Result<Option<Claim>> {
        self.call(|db| {
            ensure!(db.auth.is_none(), auth::Error::Forbidden);
            let mut tx = db.client.transaction()?;
            let Some(row) = tx.query_opt("SELECT j.id,j.processed+1,evaluator,judge_model,t.output,j.judge_attempts,j.judge_credential
                FROM eval_jobs j JOIN eval_job_targets t ON t.job=j.id AND t.ordinal=j.processed+1
                WHERE j.judge_model IS NOT NULL AND j.results IS NULL
                  AND (judge_until IS NULL OR judge_until<=clock_timestamp())
                ORDER BY created_ms,j.id LIMIT 1 FOR UPDATE OF j SKIP LOCKED", &[])? else { return Ok(None) };
            let job: String = row.get(0);
            let evaluator: crate::eval_selection::SavedEvaluator = serde_json::from_value(row.get(2))?;
            let lease = openworker_runtime::id("judge");
            let claim = Claim {
                job, position:row.get(1), lease,
                name:evaluator.name, version:evaluator.version,
                rubric:evaluator.judge.ok_or_else(|| anyhow::anyhow!("Missing model judge rubric"))?,
                model:serde_json::from_value(row.get(3))?, output:row.get(4), exhausted:row.get::<_,i32>(5)>=2,credential:row.get(6),
            };
            tx.execute("UPDATE eval_jobs SET judge_lease=$2,judge_until=clock_timestamp()+INTERVAL '45 seconds',
                judge_attempts=least(2,judge_attempts+1) WHERE id=$1",
                &[&claim.job,&claim.lease])?;
            tx.commit()?;
            Ok(Some(claim))
        })
    }

    pub fn finish_eval_judge(&self, claim: Claim, result: Input) -> Result<()> {
        self.call(move |db| {
            ensure!(db.auth.is_none(), auth::Error::Forbidden);
            let mut tx = db.client.transaction()?;
            let Some(row) = tx.query_opt("SELECT source FROM eval_jobs j
                JOIN eval_job_targets t ON t.job=j.id AND t.ordinal=$3
                WHERE j.id=$1 AND j.judge_lease=$2 AND j.processed+1=$3 FOR UPDATE OF j",
                &[&claim.job,&claim.lease,&claim.position])? else { return Ok(()) };
            let source: Value = row.get(0);
            let item = json!([{"turn":source,"result":result}]);
            tx.execute("UPDATE eval_jobs SET processed=$2,
                results=CASE WHEN $2=version THEN COALESCE(partial_results,'[]'::jsonb)||$3::jsonb END,
                partial_results=CASE WHEN $2<version THEN COALESCE(partial_results,'[]'::jsonb)||$3::jsonb END,
                judge_lease=NULL,judge_until=NULL,judge_attempts=0 WHERE id=$1", &[&claim.job,&claim.position,&item])?;
            tx.commit()?;
            Ok(())
        })
    }
}
