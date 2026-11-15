//! Acknowledged evaluation writes. No action or model call is replayed on failure.
use super::Host;
use anyhow::{ensure, Context, Result};
use openworker_observability::evaluations::{Evaluation, Input, Query, Target};
use serde::Deserialize;
use serde_json::{json, Value};
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Record {
    session_id: String,
    turn_id: String,
    result: Input,
    span_id: Option<String>,
}
impl Host {
    pub(super) async fn evaluation_dispatch(&self, method: &str, p: Value) -> Result<Value> {
        let recorder = self.recording.clone().context("Recording disabled")?;
        ensure!(recorder.options.evaluations, "Evaluation storage disabled");
        if method == "observability/evaluation-record" {
            let q: Record = serde_json::from_value(p)?;
            crate::TraceQuery::turn(&q.session_id, &q.turn_id).validate()?;
            ensure!(
                self.store
                    .lock()
                    .unwrap()
                    .has_turn(&q.session_id, &q.turn_id)?,
                "Evaluation target turn unavailable"
            );
            let session_hash = openworker_runtime::bundle::digest(q.session_id.as_bytes());
            let turn_hash = openworker_runtime::bundle::digest(q.turn_id.as_bytes());
            let permit = self
                .recording_readers
                .clone()
                .try_acquire_owned()
                .context("Recording readers busy")?;
            let value=tokio::task::spawn_blocking(move || -> Result<Value> {
                let _permit=permit;
                let mut store=recorder.options.open()?;
                // Trace retention is independent from result retention. A valid
                // duplicate must not need its old span to remain in raw storage.
                if let Some(old)=store.evaluation(&recorder.options.deployment,&q.result.id)? {
                    ensure!(old.target.session_hash==session_hash&&old.target.turn_hash==turn_hash&&old.target.span_id==q.span_id,"Evaluation identity conflict");
                    let candidate=Evaluation{result:q.result.clone(),..old.clone()}.redacted();candidate.validate()?;
                    ensure!(candidate==old,"Evaluation identity conflict");
                    return Ok(json!({"result":old,"duplicate":true}));
                }
                let trace_id=if let Some(span)=&q.span_id {
                    let step=store.get(&recorder.options.deployment,span)?.context("Evaluation span unavailable")?;
                    ensure!(step.session_hash.as_deref()==Some(&session_hash)&&step.turn_hash.as_deref()==Some(&turn_hash),"Evaluation span scope mismatch");Some(step.trace_id)
                } else {None};
                let e=Evaluation{version:1,deployment:recorder.options.deployment.clone(),target:Target{session_hash,turn_hash,trace_id,span_id:q.span_id},result:q.result}.redacted();
                e.validate()?;
                let cutoff=openworker_runtime::store::now_ms().saturating_sub(u64::from(recorder.options.evaluation_retention_days)*86_400_000);
                store.prune_evaluations(&recorder.options.deployment,cutoff,1000)?;
                let result=store.record_evaluation_bounded(&e,recorder.options.max_pending_evaluations)?;
                Ok(json!({"result":e,"duplicate":result==openworker_observability::Write::Duplicate}))
            }).await??;
            #[cfg(feature = "evaluation-export")]
            if let Some(exporter) = &self.evaluation_exporter {
                exporter.notify();
            }
            return Ok(value);
        }
        if method == "observability/evaluation-list" {
            let q: crate::TraceQuery = serde_json::from_value(p)?;
            q.validate()?;
            ensure!(q.limit <= 50, "Evaluation page limit is 50");
            ensure!(
                self.store
                    .lock()
                    .unwrap()
                    .has_turn(&q.session_id, &q.turn_id)?,
                "Evaluation target turn unavailable"
            );
            let query = Query {
                deployment: recorder.options.deployment.clone(),
                session_hash: openworker_runtime::bundle::digest(q.session_id.as_bytes()),
                turn_hash: openworker_runtime::bundle::digest(q.turn_id.as_bytes()),
                after: q.after,
                limit: q.limit,
            };
            let permit = self
                .recording_readers
                .clone()
                .try_acquire_owned()
                .context("Recording readers busy")?;
            let value = tokio::task::spawn_blocking(move || -> Result<Value> {
                let _permit = permit;
                Ok(json!(recorder.options.open()?.evaluations(&query)?))
            })
            .await??;
            ensure!(
                self.store
                    .lock()
                    .unwrap()
                    .has_turn(&q.session_id, &q.turn_id)?,
                "Evaluation target turn unavailable"
            );
            return Ok(value);
        }
        anyhow::bail!("Unknown evaluation operation")
    }
}
