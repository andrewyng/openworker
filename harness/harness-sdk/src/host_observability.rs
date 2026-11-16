//! Trusted host query boundary. No model tool can call these SDK methods.
use super::Host;
use anyhow::{ensure, Result};
use serde::Deserialize;
use serde_json::{json, Value};

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Turns {
    session_id: String,
    #[serde(default)]
    after: i64,
    #[serde(default = "limit")]
    limit: usize,
}
fn limit() -> usize {
    25
}
impl Host {
    pub(super) async fn observation_dispatch(&self, method: &str, p: Value) -> Result<Value> {
        ensure!(!self.stop.is_cancelled(), "Host stopped");
        if method == "observability/health" {
            ensure!(
                p.as_object().is_some_and(|o| o.is_empty()),
                "Health query accepts no parameters"
            );
            let recording: Option<Value> = None;
            #[cfg(feature = "recording")]
            let recording = self
                .recording
                .as_ref()
                .map(|r| json!(r.statistics.snapshot()))
                .or(recording);
            let evaluation_export: Option<Value> = None;
            #[cfg(feature = "evaluation-export")]
            let evaluation_export = self
                .evaluation_exporter
                .as_ref()
                .map(|e| e.health())
                .or(evaluation_export);
            return Ok(
                json!({"evaluation_export":evaluation_export,"scope":"sdk_host","recording_enabled":self.config.recording.is_some(),"recording":recording,"telemetry":self.instrumentation.snapshot(),"exporter_enabled":self.config.telemetry.is_some()}),
            );
        }
        if method == "observability/flush" {
            #[derive(Deserialize)]
            #[serde(deny_unknown_fields)]
            struct Flush {
                timeout_ms: u64,
            }
            let q: Flush = serde_json::from_value(p)?;
            ensure!(
                (1..=5000).contains(&q.timeout_ms),
                "Flush budget must be 1..5000ms"
            );
            let telemetry = self.instrumentation.clone();
            #[cfg(feature = "recording")]
            let recording = self.recording.clone();
            return tokio::task::spawn_blocking(move || {
                let until=std::time::Instant::now()+std::time::Duration::from_millis(q.timeout_ms);
                let exported=telemetry.flush(std::time::Duration::from_millis(q.timeout_ms));
                let drained=true;
                #[cfg(feature="recording")] let drained=if let Some(r)=recording {
                    while r.statistics.snapshot().pending>0 && std::time::Instant::now()<until { std::thread::sleep(std::time::Duration::from_millis(10)); }
                    r.statistics.snapshot().pending==0
                } else {drained};
                let _=until;
                Ok(json!({"telemetry_barrier":exported,"recording_drained":drained,"guarantee":"bounded_best_effort","evaluation_delivery":"independent_durable_queue"}))
            }).await?;
        }
        ensure!(self.config.recording.is_some(), "Recording is disabled");
        if method == "observability/turns" {
            let q: Turns = serde_json::from_value(p)?;
            ensure!(
                (1..=100).contains(&q.limit) && q.after >= 0,
                "Invalid turn page"
            );
            let store = self.store.lock().unwrap();
            let session = store.metadata(&q.session_id)?;
            // Existing journal reads are bounded at 500 events / 512 KiB. An
            // empty page can still have a next cursor; do not scan unbounded history.
            let events = store.events(&q.session_id, q.after)?;
            let end = store.cursor(&q.session_id)?;
            let mut after = q.after;
            let mut turns = Vec::new();
            for e in events {
                after = e.event_id;
                if e.kind == "turn_accepted" {
                    if let Some(id) = e.turn_id {
                        let current = session.turn.as_ref().filter(|t| t.id == id);
                        turns.push(json!({"session_id":session.id,"turn_id":id,"status":current.map(|t| &t.status),"agent":session.coworker,"name":session.name}));
                        if turns.len() == q.limit {
                            break;
                        }
                    }
                }
            }
            return Ok(
                json!({"turns":turns,"next_cursor":(after < end).then_some(after),"through":end}),
            );
        }
        #[cfg(feature = "evaluations")]
        if method.starts_with("observability/evaluation-") {
            return self.evaluation_dispatch(method, p).await;
        }
        #[cfg(feature = "recording")]
        if method == "observability/evidence" {
            return self.evidence_dispatch(p);
        }
        if method == "observability/children" {
            let q: crate::TraceQuery = serde_json::from_value(p)?;
            q.validate()?;
            let children = self
                .store
                .lock()
                .unwrap()
                .observation_children(&q.session_id, &q.turn_id)?;
            return Ok(json!({"children":children,"coverage":"captured_spawn_origins_only"}));
        }
        #[cfg(feature = "recording")]
        if method == "observability/subtree-summary" {
            use openworker_observability::{
                query::{Query, Scope, MAX_SUMMARY_STEPS},
                summary,
            };
            use openworker_runtime::observation_links::{subtree, Run};
            let q: crate::TraceQuery = serde_json::from_value(p)?;
            q.validate()?;
            ensure!(
                q.after == 0,
                "Subtree summary cannot start at a page offset"
            );
            let selection = {
                let store = self.store.lock().unwrap();
                ensure!(
                    store.has_turn(&q.session_id, &q.turn_id)?,
                    "Trace target turn unavailable"
                );
                subtree(
                    Run {
                        session_id: q.session_id.clone(),
                        turn_id: q.turn_id.clone(),
                    },
                    |r| store.observation_children(&r.session_id, &r.turn_id),
                )?
            };
            let recording = self
                .recording
                .clone()
                .ok_or_else(|| anyhow::anyhow!("Recording unavailable"))?;
            let read = Query {
                deployment: recording.options.deployment.clone(),
                scopes: selection
                    .runs
                    .iter()
                    .map(|r| Scope {
                        session_hash: openworker_runtime::bundle::digest(r.session_id.as_bytes()),
                        turn_hash: openworker_runtime::bundle::digest(r.turn_id.as_bytes()),
                    })
                    .collect(),
                after: 0,
                through: q.through,
                limit: MAX_SUMMARY_STEPS,
            };
            let permit = self
                .recording_readers
                .clone()
                .try_acquire_owned()
                .map_err(|_| anyhow::anyhow!("Recording readers busy"))?;
            let page = tokio::task::spawn_blocking(move || {
                let _permit = permit;
                recording.options.open()?.timeline(&read)
            })
            .await??;
            for run in &selection.runs {
                ensure!(
                    self.store
                        .lock()
                        .unwrap()
                        .has_turn(&run.session_id, &run.turn_id)?,
                    "Trace target unavailable"
                );
            }
            return Ok(
                json!({"selection":selection,"summary":summary::build(&page,summary::Canonical::default())}),
            );
        }
        #[cfg(feature = "recording")]
        if matches!(method, "observability/timeline" | "observability/summary") {
            use openworker_observability::{
                query::{Query, Scope, MAX_SUMMARY_STEPS},
                summary::{self, Canonical},
            };
            let q: crate::TraceQuery = serde_json::from_value(p)?;
            q.validate()?;
            ensure!(
                self.store
                    .lock()
                    .unwrap()
                    .has_turn(&q.session_id, &q.turn_id)?,
                "Trace target turn unavailable"
            );
            let session = self.store.lock().unwrap().metadata(&q.session_id)?;
            let current = session.turn.as_ref().filter(|t| t.id == q.turn_id);
            let canonical = Canonical {
                terminal: current.is_some_and(|t| openworker_runtime::store::terminal(&t.status)),
                expected_segments: current
                    .and_then(|t| t.observation.as_ref())
                    .map(|o| o.segments),
                first_started_ms: current
                    .and_then(|t| t.observation.as_ref())
                    .map(|o| o.first_started_at_ms),
                last_span_id: current.and_then(|t| t.observation.as_ref()).map(|o| {
                    o.last_segment
                        .span
                        .iter()
                        .map(|b| format!("{b:02x}"))
                        .collect()
                }),
                pending_waits: current.map(|t| {
                    session
                        .interactions
                        .iter()
                        .filter(|i| i.turn_id == t.id && i.response.is_none())
                        .count() as u64
                }),
            };
            let recording = self
                .recording
                .clone()
                .ok_or_else(|| anyhow::anyhow!("Recording unavailable"))?;
            let is_summary = method == "observability/summary";
            ensure!(
                !is_summary || q.after == 0,
                "Summary cannot start at a page offset"
            );
            let read = Query {
                deployment: recording.options.deployment.clone(),
                scopes: vec![Scope {
                    session_hash: openworker_runtime::bundle::digest(q.session_id.as_bytes()),
                    turn_hash: openworker_runtime::bundle::digest(q.turn_id.as_bytes()),
                }],
                after: q.after,
                through: q.through,
                limit: if is_summary {
                    MAX_SUMMARY_STEPS
                } else {
                    q.limit
                },
            };
            let permit = self
                .recording_readers
                .clone()
                .try_acquire_owned()
                .map_err(|_| anyhow::anyhow!("Recording readers busy"))?;
            let page = tokio::task::spawn_blocking(move || {
                let _permit = permit;
                recording.options.open()?.timeline(&read)
            })
            .await??;
            // Recheck after a potentially slow independent database read.
            self.store.lock().unwrap().metadata(&q.session_id)?;
            let run = json!({"session_id":q.session_id,"turn_id":q.turn_id});
            return if is_summary {
                Ok(json!({"run":run,"summary":summary::build(&page,canonical)}))
            } else {
                Ok(
                    json!({"run":run,"page":page,"coverage":{"guarantee":"best_effort","order":"recording_cursor","snapshot_across_requests":false}}),
                )
            };
        }
        anyhow::bail!("Unsupported observability operation or capability")
    }
}
