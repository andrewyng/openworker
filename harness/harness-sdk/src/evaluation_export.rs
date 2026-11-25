//! Optional durable score delivery using the compliance checkpoint contract.
//! An export retry never submits a turn or executes a tool.
use crate::recording::{EvaluationSink, RecordingConfig};
use anyhow::{ensure, Context, Result};
use openworker_observability::evaluations::{Evaluation, Score, Status};
use serde_json::{json, Value};
use std::{
    io::Read,
    sync::{
        atomic::{AtomicBool, AtomicU64, Ordering},
        mpsc, Arc, Mutex,
    },
    time::Duration,
};
#[derive(Default)]
struct Counters {
    sent: AtomicU64,
    skipped: AtomicU64,
    failed: AtomicU64,
}
pub(crate) struct Controller {
    stop: Arc<AtomicBool>,
    wake: mpsc::SyncSender<()>,
    done: Mutex<mpsc::Receiver<()>>,
    counters: Arc<Counters>,
}
impl Controller {
    pub fn notify(&self) {
        let _ = self.wake.try_send(());
    }
    pub fn health(&self) -> Value {
        json!({"scope":"sdk_host","sent":self.counters.sent.load(Ordering::Relaxed),"skipped_not_exportable":self.counters.skipped.load(Ordering::Relaxed),"failed_or_uncertain":self.counters.failed.load(Ordering::Relaxed)})
    }
    pub fn shutdown(&self) {
        self.stop.store(true, Ordering::Release);
        self.notify();
        let _ = self
            .done
            .lock()
            .unwrap()
            .recv_timeout(Duration::from_secs(5));
    }
}
impl Drop for Controller {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Release);
        self.notify();
    }
}
fn now() -> u64 {
    openworker_runtime::store::now_ms()
}
pub(crate) fn start(options: RecordingConfig) -> Result<Option<Arc<Controller>>> {
    if options.evaluation_sinks.is_empty() {
        return Ok(None);
    }
    for sink in &options.evaluation_sinks {
        sink.validate()?;
        ensure!(
            std::env::var(&sink.authorization_env).is_ok(),
            "Evaluation export credential unavailable"
        );
    }
    let mut store = options.open()?;
    for sink in &options.evaluation_sinks {
        let binding = openworker_runtime::bundle::digest(serde_json::to_string(sink)?.as_bytes());
        store.register_evaluation_sink(&options.deployment, &sink.name, &binding)?;
    }
    drop(store);
    let (wake, rx) = mpsc::sync_channel(1);
    let (done, wait) = mpsc::sync_channel(1);
    let stop = Arc::new(AtomicBool::new(false));
    let stopped = stop.clone();
    let counters = Arc::new(Counters::default());
    let stats = counters.clone();
    std::thread::Builder::new()
        .name("ow-evaluation-export".into())
        .spawn(move || {
            let client = reqwest::blocking::Client::builder()
                .no_proxy()
                .redirect(reqwest::redirect::Policy::none())
                .timeout(Duration::from_secs(3))
                .build();
            if let Ok(client) = client {
                while !stopped.load(Ordering::Acquire) {
                    for sink in &options.evaluation_sinks {
                        if stopped.load(Ordering::Acquire) {
                            break;
                        }
                        if deliver_next(&options, sink, &client, &stats).is_err() {
                            stats.failed.fetch_add(1, Ordering::Relaxed);
                        }
                    }
                    let _ = rx.recv_timeout(Duration::from_secs(1));
                }
            } else {
                stats.failed.fetch_add(1, Ordering::Relaxed);
            }
            let _ = done.send(());
        })?;
    Ok(Some(Arc::new(Controller {
        stop,
        wake,
        done: Mutex::new(wait),
        counters,
    })))
}
fn deliver_next(
    options: &RecordingConfig,
    sink: &EvaluationSink,
    client: &reqwest::blocking::Client,
    stats: &Counters,
) -> Result<()> {
    let binding = openworker_runtime::bundle::digest(serde_json::to_string(sink)?.as_bytes());
    let token = openworker_runtime::id("eval");
    let mut store = options.open()?;
    let Some(delivery) =
        store.claim_evaluation_export(&options.deployment, &sink.name, &binding, &token, now())?
    else {
        return Ok(());
    };
    let value = &delivery.row.evaluation;
    // Non-quality outcomes stay available locally without fabricating a score.
    let result = if value.result.status != Status::Completed || value.target.trace_id.is_none() {
        stats.skipped.fetch_add(1, Ordering::Relaxed);
        Ok(())
    } else {
        send(client, sink, value).map(|_| {
            stats.sent.fetch_add(1, Ordering::Relaxed);
        })
    };
    // Stable score ID/name/timestamp survive lease expiry, restart and lost ACK.
    // This checkpoint advances only for an acknowledged score or explicit skip.
    store.settle_evaluation_export(
        &options.deployment,
        &sink.name,
        &token,
        now(),
        result.is_ok(),
    )?;
    result
}
pub(crate) fn payload(e: &Evaluation, explanation: bool) -> Result<Value> {
    e.validate()?;
    ensure!(e.result.status == Status::Completed, "Not a quality score");
    let trace = e.target.trace_id.as_ref().context("Score trace missing")?;
    let (kind, value) = match e.result.score.as_ref().context("Score missing")? {
        Score::Numeric(v) => ("NUMERIC", json!(v)),
        Score::Boolean(v) => ("BOOLEAN", json!(u8::from(*v))),
        Score::Categorical(v) => ("CATEGORICAL", json!(v)),
        Score::Text(v) => ("TEXT", json!(v)),
    };
    let id =
        openworker_runtime::bundle::digest(format!("{}\0{}", e.deployment, e.result.id).as_bytes());
    let timestamp = chrono::DateTime::from_timestamp_millis(e.result.created_ms as i64)
        .context("Invalid score time")?
        .to_rfc3339_opts(chrono::SecondsFormat::Millis, true);
    let mut body = json!({"id":id,"name":e.result.name,"traceId":trace,"dataType":kind,"value":value,"timestamp":timestamp,"metadata":{"openworker.evaluation.id":e.result.id,"evaluator":e.result.evaluator.name,"evaluatorVersion":e.result.evaluator.version,"evaluatorSource":e.result.evaluator.source,"evaluatorModel":e.result.evaluator.model,"evaluatorUsage":e.result.evaluator.usage,"supersedes":e.result.supersedes}});
    if let Some(span) = &e.target.span_id {
        body["observationId"] = json!(span);
    }
    if explanation {
        if let Some(comment) = &e.result.explanation {
            body["comment"] = json!(comment);
        }
    }
    Ok(body)
}
fn send(client: &reqwest::blocking::Client, sink: &EvaluationSink, e: &Evaluation) -> Result<()> {
    let body = payload(e, sink.include_explanation)?;
    let authorization = zeroize::Zeroizing::new(
        std::env::var(&sink.authorization_env).context("Evaluation credential unavailable")?,
    );
    let response = client
        .post(format!(
            "{}/api/public/scores",
            sink.url.trim_end_matches('/')
        ))
        .header("Authorization", authorization.as_str())
        .json(&body)
        .send()
        .map_err(|_| anyhow::anyhow!("Evaluation delivery outcome unknown"))?;
    ensure!(
        response.status().as_u16() == 200,
        "Evaluation delivery not acknowledged"
    );
    let mut bytes = Vec::new();
    response.take(65537).read_to_end(&mut bytes)?;
    ensure!(bytes.len() <= 65536, "Evaluation acknowledgement too large");
    let ack: Value = serde_json::from_slice(&bytes)
        .map_err(|_| anyhow::anyhow!("Invalid evaluation acknowledgement"))?;
    ensure!(
        ack["id"] == body["id"],
        "Evaluation acknowledgement identity mismatch"
    );
    Ok(())
}
