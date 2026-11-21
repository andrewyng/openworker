//! Snapshot an immutable dataset and compare two releases through normal admission.
use crate::{
    auth::{self, Action, Scope},
    evals::{capture, identity, owned_dataset, Check, Turn, CASE_SNAPSHOT},
    submissions::validate_id,
    PostgresStore, SubmissionError,
};
use anyhow::{ensure, Result};
use serde::Deserialize;
use serde_json::{json, Value};

pub(crate) fn migrate(tx: &mut postgres::Transaction<'_>) -> Result<()> {
    tx.batch_execute(
        "CREATE TABLE IF NOT EXISTS eval_replays(
        id TEXT PRIMARY KEY, owner TEXT NOT NULL, credential TEXT NOT NULL,
        dataset TEXT NOT NULL REFERENCES eval_datasets(id), request JSONB NOT NULL,
        created_ms BIGINT NOT NULL);
      CREATE INDEX IF NOT EXISTS eval_replays_owner ON eval_replays(owner,created_ms DESC,id DESC);
      CREATE TABLE IF NOT EXISTS eval_replay_runs(
        replay TEXT NOT NULL REFERENCES eval_replays(id), ordinal INTEGER NOT NULL,
        source JSONB NOT NULL, input TEXT NOT NULL, check_spec JSONB NOT NULL,
        release_id TEXT NOT NULL, operation TEXT, result JSONB,
        lease TEXT, next_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        cleanup BOOLEAN NOT NULL DEFAULT FALSE, PRIMARY KEY(replay,ordinal));
      CREATE INDEX IF NOT EXISTS eval_replay_pending ON eval_replay_runs(next_at,replay,ordinal)
        WHERE result IS NULL OR (operation IS NOT NULL AND NOT cleanup);",
    )?;
    Ok(())
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Enqueue {
    pub dataset_version: i32,
    pub baseline: String,
    pub candidate: String,
    pub runtime_profile: Option<String>,
}

pub struct Task {
    pub replay: String,
    pub ordinal: i32,
    pub lease: String,
    pub owner: String,
    pub credential: String,
    pub operation: Option<String>,
    pub release: String,
    pub profile: Option<String>,
    pub input: String,
    pub check: Check,
    pub cleanup: bool,
}

impl PostgresStore {
    pub fn enqueue_replay(&self, id: String, dataset: String, request: Enqueue) -> Result<Value> {
        validate_id(&dataset, 100)?;
        crate::service_catalog::revision(&request.baseline)?;
        crate::service_catalog::revision(&request.candidate)?;
        ensure!(
            request.baseline != request.candidate,
            SubmissionError::InvalidRequest
        );
        if let Some(profile) = &request.runtime_profile {
            crate::service_catalog::revision(profile)?;
        }
        for release in [&request.baseline, &request.candidate] {
            let entry = self.catalog_get("release", release)?;
            ensure!(entry.id == *release, SubmissionError::InvalidRequest);
        }
        if let Some(profile) = &request.runtime_profile {
            ensure!(
                self.catalog_get("profile", profile)?.id == *profile,
                SubmissionError::InvalidRequest
            );
        }
        self.call(move |db| {
            let mut tx=db.client.transaction()?;
            let p=identity(&mut tx,db.auth.as_ref(),Action::Submit,None)?;
            let (_,latest)=owned_dataset(&mut tx,&p,&dataset)?;
            ensure!((1..=latest).contains(&request.dataset_version),SubmissionError::InvalidRequest);
            let spec=json!({"version":request.dataset_version,"baseline":request.baseline,
                "candidate":request.candidate,"profile":request.runtime_profile});
            tx.query_one("SELECT pg_advisory_xact_lock(hashtext('eval.replay'),hashtext($1))",&[&id])?;
            if let Some(row)=tx.query_opt("SELECT owner,dataset,request FROM eval_replays WHERE id=$1",&[&id])? {
                ensure!(row.get::<_,String>(0)==p.principal && row.get::<_,String>(1)==dataset
                    && row.get::<_,Value>(2)==spec,SubmissionError::Conflict);
                return Ok(json!({"id":id}));
            }
            let cases=tx.query(&format!("SELECT id,source,evidence->>'input',check_spec FROM ({CASE_SNAPSHOT})
                cases WHERE NOT archived ORDER BY version"),&[&dataset,&request.dataset_version])?;
            ensure!((1..=4).contains(&cases.len()),SubmissionError::InvalidRequest);
            tx.execute("INSERT INTO eval_replays VALUES($1,$2,$3,$4,$5,(extract(epoch FROM clock_timestamp())*1000)::bigint)",
                &[&id,&p.principal,&p.credential_id,&dataset,&spec])?;
            for (index,row) in cases.iter().enumerate() {
                let input=row.get::<_,Option<String>>(2).filter(|text|!text.trim().is_empty())
                    .ok_or(SubmissionError::InvalidRequest)?;
                let mut source:Value=row.get(1);
                source["case_id"]=json!(row.get::<_,String>(0));
                let check:Value=row.get(3);
                for (side,release) in [&request.baseline,&request.candidate].into_iter().enumerate() {
                    tx.execute("INSERT INTO eval_replay_runs(replay,ordinal,source,input,check_spec,release_id) VALUES($1,$2,$3,$4,$5,$6)",
                        &[&id,&((index*2+side) as i32),&source,&input,&check,&release])?;
                }
            }
            tx.commit()?;
            Ok(json!({"id":id}))
        })
    }

    pub fn eval_replays(&self) -> Result<Value> {
        self.call(|db| {
            let mut tx=db.client.transaction()?;
            let p=identity(&mut tx,db.auth.as_ref(),Action::Read,None)?;
            let rows=tx.query("SELECT r.id,r.dataset,r.request,r.created_ms,d.name FROM eval_replays r
                JOIN eval_datasets d ON d.id=r.dataset WHERE $1 OR r.owner=$2 ORDER BY r.created_ms DESC,r.id DESC LIMIT 30",
                &[&(p.scope==Scope::All),&p.principal])?;
            Ok(json!({"replays":rows.iter().map(|r| json!({"id":r.get::<_,String>(0),"dataset_id":r.get::<_,String>(1),
                "request":r.get::<_,Value>(2),"created_ms":r.get::<_,i64>(3),"dataset_name":r.get::<_,String>(4)})).collect::<Vec<_>>()}))
        })
    }

    pub fn eval_replay(&self, id: String) -> Result<Value> {
        validate_id(&id, 100)?;
        self.call(move |db| {
            let mut tx=db.client.transaction()?;
            let p=identity(&mut tx,db.auth.as_ref(),Action::Read,None)?;
            let row=tx.query_opt("SELECT r.dataset,r.request,r.created_ms,d.name FROM eval_replays r
                JOIN eval_datasets d ON d.id=r.dataset WHERE r.id=$1 AND ($2 OR r.owner=$3)",
                &[&id,&(p.scope==Scope::All),&p.principal])?.ok_or(SubmissionError::NotFound)?;
            let runs=tx.query("SELECT t.ordinal,t.source,t.result,o.session_id,
                CASE WHEN o.status='complete' THEN 't_'||substring(o.id from 4) END AS turn_id,t.cleanup
                FROM eval_replay_runs t LEFT JOIN service_operations o ON o.id=t.operation WHERE t.replay=$1
                ORDER BY t.ordinal",&[&id])?;
            Ok(json!({"id":id,"dataset_id":row.get::<_,String>(0),"request":row.get::<_,Value>(1),
                "created_ms":row.get::<_,i64>(2),"dataset_name":row.get::<_,String>(3),
                "runs":runs.iter().map(|r| json!({"ordinal":r.get::<_,i32>(0),"source":r.get::<_,Value>(1),"result":r.get::<_,Option<Value>>(2),
                    "session_id":r.get::<_,Option<String>>(3),"turn_id":r.get::<_,Option<String>>(4),"cleanup":r.get::<_,bool>(5)})).collect::<Vec<_>>()}))
        })
    }

    pub fn claim_eval_replay(&self) -> Result<Option<Task>> {
        self.call(|db| {
            ensure!(db.auth.is_none(),auth::Error::Forbidden);
            let mut tx=db.client.transaction()?;
            let Some(r)=tx.query_opt("SELECT t.replay,t.ordinal,r.owner,r.credential,t.operation,t.release_id,
                r.request->>'profile',t.input,t.check_spec,t.result IS NOT NULL
                FROM eval_replay_runs t JOIN eval_replays r ON r.id=t.replay
                WHERE (t.result IS NULL OR (t.operation IS NOT NULL AND NOT t.cleanup)) AND t.next_at <= clock_timestamp()
                ORDER BY t.next_at,t.replay,t.ordinal LIMIT 1 FOR UPDATE OF t SKIP LOCKED",&[])? else {return Ok(None)};
            let task=Task { replay:r.get(0),ordinal:r.get(1),owner:r.get(2),credential:r.get(3),operation:r.get(4),
                release:r.get(5),profile:r.get(6),input:r.get(7),check:serde_json::from_value(r.get(8))?,
                cleanup:r.get(9),lease:openworker_runtime::id("replay") };
            tx.execute("UPDATE eval_replay_runs SET lease=$3,next_at=clock_timestamp()+INTERVAL '45 seconds'
                WHERE (replay,ordinal)=($1,$2)",&[&task.replay,&task.ordinal,&task.lease])?;
            tx.commit()?;
            Ok(Some(task))
        })
    }

    pub fn update_eval_replay(
        &self,
        task: Task,
        operation: Option<String>,
        result: Option<Value>,
        cleanup: bool,
    ) -> Result<()> {
        self.call(move |db| {
            ensure!(db.auth.is_none(),auth::Error::Forbidden);
            db.client.execute("UPDATE eval_replay_runs SET operation=COALESCE(operation,$4),result=COALESCE(result,$5),cleanup=(cleanup OR $6),
                lease=NULL,next_at=clock_timestamp()+INTERVAL '2 seconds' WHERE (replay,ordinal,lease)=($1,$2,$3)",
                &[&task.replay,&task.ordinal,&task.lease,&operation,&result,&cleanup])?;
            Ok(())
        })
    }

    pub fn eval_replay_result(&self, turn: Turn, check: Check) -> Result<Option<Value>> {
        self.call(move |db| {
            ensure!(db.auth.is_none(),auth::Error::Forbidden);
            let mut tx=db.client.transaction()?;
            let Some(r)=tx.query_opt("SELECT status FROM submissions WHERE (session_id,turn_id)=($1,$2)",
                &[&turn.session_id,&turn.turn_id])? else { return Ok(None) };
            let status:String=r.get(0);
            if !openworker_runtime::store::terminal(&status) {return Ok(None);}
            let metrics=tx.query_one("SELECT
                (extract(epoch FROM (max(a.created) FILTER (WHERE e.kind IN ('turn_completed','turn_recovered'))
                  -min(a.created) FILTER (WHERE e.kind='turn_started')))*1000)::bigint,
                sum(CASE WHEN jsonb_typeof(e.data->'input_tokens')='number' THEN (e.data->>'input_tokens')::numeric END)::bigint,
                sum(CASE WHEN jsonb_typeof(e.data->'output_tokens')='number' THEN (e.data->>'output_tokens')::numeric END)::bigint
               FROM events e JOIN audit a USING(session_id,cursor) WHERE (e.session_id,e.turn_id)=($1,$2)
               AND e.kind IN ('turn_completed','turn_recovered','turn_started','model_usage')",
               &[&turn.session_id,&turn.turn_id])?;
            let output=capture(&mut tx,turn,false)?.output;
            let score=output.as_deref().map(|output|check.passes(output));
            Ok(Some(json!({"status":if score.is_some(){"scored"}else{"incomplete"},"turn_status":status,
                "passed":score,"latency_ms":metrics.get::<_,Option<i64>>(0),
                "input_tokens":metrics.get::<_,Option<i64>>(1),"output_tokens":metrics.get::<_,Option<i64>>(2)})))
        })
    }
}
