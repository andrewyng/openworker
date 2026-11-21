//! Durable periodic jobs reuse ordinary evaluation admission and app authorization.
use crate::{
    auth::{self, Action, Scope},
    eval_selection::{self, TagFilter},
    evals::{identity, owned_dataset, Turn},
    submissions::validate_id,
    PostgresStore, SubmissionError,
};
use anyhow::{ensure, Result};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

const HOUR: i64 = 3_600_000;

pub(crate) fn migrate(tx: &mut postgres::Transaction<'_>) -> Result<()> {
    tx.batch_execute("CREATE TABLE IF NOT EXISTS eval_schedules(
        id TEXT PRIMARY KEY, owner TEXT NOT NULL, credential TEXT NOT NULL, spec JSONB NOT NULL,
        enabled BOOLEAN NOT NULL DEFAULT TRUE, through_ms BIGINT NOT NULL,next_ms BIGINT NOT NULL,
        lease TEXT, lease_until TIMESTAMPTZ, created_ms BIGINT NOT NULL);
        CREATE INDEX IF NOT EXISTS eval_schedules_due ON eval_schedules(next_ms,id) WHERE enabled;
        CREATE TABLE IF NOT EXISTS eval_schedule_runs(
            schedule TEXT NOT NULL REFERENCES eval_schedules(id), end_ms BIGINT NOT NULL,
            job_id TEXT NOT NULL, turns JSONB, state TEXT NOT NULL DEFAULT 'pending', clipped BOOLEAN NOT NULL DEFAULT FALSE,
            PRIMARY KEY(schedule,end_ms));")?;
    Ok(())
}

#[derive(Clone, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
pub enum Target {
    Dataset {
        dataset_id: String,
        version: i32,
    },
    Tags {
        tag: String,
        evaluator_id: String,
        lookback_hours: u16,
        max_cases: u16,
    },
}

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Spec {
    pub name: String,
    pub every_hours: u16,
    pub target: Target,
}

struct Due {
    id: String,
    owner: String,
    credential: String,
    lease: String,
    job: String,
    start: i64,
    end: i64,
    spec: Spec,
    turns: Option<Vec<Turn>>,
}

impl PostgresStore {
    pub fn save_eval_schedule(&self, id: String, mut spec: Spec) -> Result<Value> {
        spec.name = spec.name.trim().to_owned();
        validate_id(&spec.name, 100)?;
        ensure!(
            !spec.name.chars().any(char::is_control) && (1..=168).contains(&spec.every_hours),
            SubmissionError::InvalidRequest
        );
        match &spec.target {
            Target::Dataset { dataset_id, .. } => validate_id(dataset_id, 100)?,
            Target::Tags {
                tag,
                evaluator_id,
                lookback_hours,
                max_cases,
            } => {
                eval_selection::validate_tag(tag, true)?;
                validate_id(evaluator_id, 100)?;
                ensure!(
                    (1..=168).contains(lookback_hours) && (1..=100).contains(max_cases),
                    SubmissionError::InvalidRequest
                );
            }
        }
        self.call(move|db|{
            let mut tx=db.client.transaction()?;
            let p=identity(&mut tx,db.auth.as_ref(),Action::Submit,None)?;
            match &spec.target {
                Target::Dataset{dataset_id,version}=>{
                    let (_,latest)=owned_dataset(&mut tx,&p,dataset_id)?;
                    ensure!((1..=latest).contains(version),SubmissionError::InvalidRequest);
                }
                Target::Tags{evaluator_id,max_cases,..}=>{
                    let r=tx.query_opt("SELECT judge_spec FROM eval_evaluators WHERE (id,owner)=($1,$2)",
                        &[&evaluator_id,&p.principal])?.ok_or(SubmissionError::NotFound)?;
                    if let Some(judge) = r.get::<_,Option<Value>>(0) {
                        let judge:crate::eval_judge::Rubric=serde_json::from_value(judge)?;
                        ensure!(*max_cases<=u16::from(judge.max_cases),SubmissionError::InvalidRequest);
                    }
                }
            }
            let every=i64::from(spec.every_hours)*HOUR;
            let serialized=json!(spec);
            tx.execute("INSERT INTO eval_schedules(id,owner,credential,spec,through_ms,next_ms,created_ms)
                SELECT $1,$2,$3,$4,0,n+$5,n FROM (SELECT (extract(epoch FROM clock_timestamp())*1000)::bigint n) now
                ON CONFLICT(id) DO NOTHING",&[&id,&p.principal,&p.credential_id,&serialized,&every])?;
            let r=tx.query_one("SELECT owner,spec,next_ms FROM eval_schedules WHERE id=$1",&[&id])?;
            ensure!(r.get::<_,String>(0)==p.principal&&r.get::<_,Value>(1)==serialized,SubmissionError::Conflict);
            let output=json!({"id":id,"next_ms":r.get::<_,i64>(2)});
            tx.commit()?;
            Ok(output)
        })
    }

    pub fn set_eval_schedule_enabled(&self, id: String, enabled: bool) -> Result<Value> {
        validate_id(&id, 100)?;
        self.call(move |db| {
            let mut tx = db.client.transaction()?;
            let p = identity(&mut tx, db.auth.as_ref(), Action::Submit, None)?;
            let changed = tx.execute(
                "UPDATE eval_schedules SET enabled=$2 WHERE id=$1 AND ($3 OR owner=$4)",
                &[&id, &enabled, &(p.scope == Scope::All), &p.principal],
            )?;
            ensure!(changed == 1, SubmissionError::NotFound);
            tx.commit()?;
            Ok(json!({"id":id,"enabled":enabled}))
        })
    }

    pub fn eval_schedules(&self) -> Result<Value> {
        self.call(|db|{
            let mut tx=db.client.transaction()?;
            let p=identity(&mut tx,db.auth.as_ref(),Action::Read,None)?;
            let rows=tx.query("SELECT s.id,s.spec,s.enabled,s.next_ms,
                COALESCE((SELECT jsonb_agg(recent.item ORDER BY recent.end_ms DESC) FROM (
                    SELECT r.end_ms,jsonb_build_object('end_ms',r.end_ms,'job_id',r.job_id,'state',r.state,'clipped',r.clipped) AS item
                    FROM eval_schedule_runs r WHERE r.schedule=s.id ORDER BY r.end_ms DESC LIMIT 5) recent),'[]'::jsonb)
                FROM eval_schedules s WHERE $1 OR s.owner=$2 ORDER BY created_ms DESC,id DESC LIMIT 50",
                &[&(p.scope==Scope::All),&p.principal])?;
            Ok(json!({"schedules":rows.iter().map(|r|json!({"id":r.get::<_,String>(0),"spec":r.get::<_,Value>(1),
                "enabled":r.get::<_,bool>(2),"next_ms":r.get::<_,i64>(3),"runs":r.get::<_,Value>(4)})).collect::<Vec<_>>()}))
        })
    }

    fn claim_eval_schedule(&self) -> Result<Option<Due>> {
        self.call(|db|{
            ensure!(db.auth.is_none(),auth::Error::Forbidden);
            let mut tx=db.client.transaction()?;
            let Some(row)=tx.query_opt("SELECT id,owner,credential,spec,through_ms,next_ms FROM eval_schedules
                WHERE enabled AND next_ms<=(extract(epoch FROM clock_timestamp())*1000)::bigint
                AND (lease_until IS NULL OR lease_until<=clock_timestamp()) ORDER BY next_ms,id LIMIT 1 FOR UPDATE SKIP LOCKED",&[])?
                else {return Ok(None)};
            let id:String=row.get(0);
            let end:i64=row.get(5);
            let job=openworker_runtime::bundle::digest(format!("eval.schedule:{id}:{end}").as_bytes());
            let spec:Spec=serde_json::from_value(row.get(3))?;
            let start=match &spec.target {Target::Tags{lookback_hours,..}=>(end-i64::from(*lookback_hours)*HOUR).max(row.get(4)),_=>row.get(4)};
            tx.execute("INSERT INTO eval_schedule_runs(schedule,end_ms,job_id) VALUES($1,$2,$3) ON CONFLICT DO NOTHING",&[&id,&end,&job])?;
            let turns=tx.query_one("SELECT turns FROM eval_schedule_runs WHERE (schedule,end_ms)=($1,$2)",&[&id,&end])?
                .get::<_,Option<Value>>(0).map(serde_json::from_value).transpose()?;
            let lease=openworker_runtime::id("evalschedule");
            tx.execute("UPDATE eval_schedules SET lease=$2,lease_until=clock_timestamp()+INTERVAL '30 seconds' WHERE id=$1",&[&id,&lease])?;
            tx.commit()?;
            Ok(Some(Due{id,owner:row.get(1),credential:row.get(2),lease,job,start,end,spec,turns}))
        })
    }

    fn run_due_eval(&self, due: &Due, judges: bool) -> Result<&'static str> {
        let scoped = self.for_background_credential(&due.credential)?;
        ensure!(
            scoped.authorize(Action::Submit, None, None)?.principal == due.owner,
            auth::Error::Forbidden
        );
        match &due.spec.target {
            Target::Dataset {
                dataset_id,
                version,
            } => {
                scoped.enqueue_eval(due.job.clone(), dataset_id.clone(), *version)?;
            }
            Target::Tags {
                tag,
                evaluator_id,
                max_cases,
                ..
            } => {
                let filter = || TagFilter {
                    tag: tag.clone(),
                    from_ms: Some(due.start),
                    before_ms: Some(due.end),
                };
                let turns = match &due.turns {
                    Some(turns) => turns.clone(),
                    None => {
                        let preview = scoped.eval_candidates(filter())?;
                        let rows = preview["turns"]
                            .as_array()
                            .ok_or(SubmissionError::InvalidRequest)?;
                        let clipped =
                            preview["truncated"] == true || rows.len() > usize::from(*max_cases);
                        let turns:Vec<_>=rows.iter().take(usize::from(*max_cases)).map(|row|
                            serde_json::from_value::<Turn>(json!({"session_id":row["session_id"],"turn_id":row["turn_id"]})))
                            .collect::<serde_json::Result<_>>()?;
                        let (id, end, lease, snapshot) =
                            (due.id.clone(), due.end, due.lease.clone(), json!(turns));
                        self.call(move|db|{
                            let saved=db.client.execute("UPDATE eval_schedule_runs r SET turns=$4,clipped=$5 FROM eval_schedules s
                                WHERE (r.schedule,r.end_ms)=($1,$2) AND s.id=r.schedule AND s.lease=$3",
                                &[&id,&end,&lease,&snapshot,&clipped])?;
                            ensure!(saved==1,SubmissionError::Conflict);
                            Ok(())
                        })?;
                        turns
                    }
                };
                if turns.is_empty() {
                    return Ok("no_matches");
                }
                scoped.enqueue_tag_eval(
                    due.job.clone(),
                    eval_selection::Enqueue {
                        evaluator_id: evaluator_id.clone(),
                        filter: filter(),
                        turns,
                    },
                    judges,
                )?;
            }
        }
        Ok("queued")
    }

    pub fn tick_eval_schedule(&self, judges: bool) -> Result<bool> {
        let Some(due) = self.claim_eval_schedule()? else {
            return Ok(false);
        };
        let state = match self.run_due_eval(&due, judges) {
            Ok(state) => state,
            Err(e) if e.is::<auth::Error>() => "credential_unavailable",
            Err(e) if e.is::<SubmissionError>() => "selection_unavailable",
            Err(e) => return Err(e), // Leave the original occurrence for retry after the lease.
        };
        self.call(move |db| {
            let mut tx = db.client.transaction()?;
            let update = tx.execute(
                "UPDATE eval_schedules SET through_ms=$2,next_ms=$3,lease=NULL,lease_until=NULL,
                enabled=enabled AND $5<>'credential_unavailable' WHERE id=$1 AND lease=$4",
                &[
                    &due.id,
                    &due.end,
                    &(due.end + i64::from(due.spec.every_hours) * HOUR),
                    &due.lease,
                    &state,
                ],
            )?;
            if update == 1 {
                tx.execute(
                    "UPDATE eval_schedule_runs SET state=$3 WHERE (schedule,end_ms)=($1,$2)",
                    &[&due.id, &due.end, &state],
                )?;
            }
            tx.commit()?;
            Ok(true)
        })
    }
}
