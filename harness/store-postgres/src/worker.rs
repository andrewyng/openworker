//! Bounded worker role; HTTP/tenant authorization stays outside this module.
//! Each slot has one store incarnation and one runtime for one session. Recovery
//! therefore cannot interrupt a sibling session running on this worker.
use crate::{Config, PostgresStore};
use anyhow::{bail, Context, Result};
use openworker_runtime::telemetry::{Gauge, Telemetry};
use openworker_runtime::{execution::Backend, runtime::Runtime, store::StateStore};
use std::{
    sync::{
        atomic::{AtomicBool, AtomicU64, Ordering},
        Arc,
    },
    time::{Duration, SystemTime, UNIX_EPOCH},
};
use tokio::{
    task::JoinSet,
    time::{interval, MissedTickBehavior},
};
use tokio_util::sync::CancellationToken;

/// Host-owned planned-stop handle. Request is immediate; acknowledgement means
/// all admissions already in flight have crossed the serialized boundary.
/// One-shot: use a fresh handle for each worker lifetime. Race acknowledgement
/// with the run result: an immediate failure/abort may exit without acknowledging.
#[derive(Clone, Default)]
pub struct Drain(Arc<Intake>);
#[derive(Default)]
struct Intake {
    gate: tokio::sync::RwLock<()>,
    request: CancellationToken,
    acknowledged: CancellationToken,
    draining: AtomicBool,
    expired: AtomicBool,
}
impl Drain {
    pub fn request(&self) {
        self.0.request.cancel();
    }
    pub fn is_requested(&self) -> bool {
        self.0.request.is_cancelled()
    }
    pub async fn acknowledged(&self) {
        self.0.acknowledged.cancelled().await;
    }
    pub fn is_acknowledged(&self) -> bool {
        self.0.draining.load(Ordering::Acquire)
    }
}
struct DrainWatch(tokio::task::JoinHandle<()>);
impl Drop for DrainWatch {
    fn drop(&mut self) {
        self.0.abort();
    }
}
async fn watch_drain(drain: Drain, stop: CancellationToken, timeout: Duration) {
    tokio::select! {biased; _=stop.cancelled()=>return, _=drain.0.request.cancelled()=>{}}
    let deadline = tokio::time::sleep(timeout);
    tokio::pin!(deadline);
    tokio::select! {biased;
        _=stop.cancelled()=>return,
        _=&mut deadline=>{drain.0.expired.store(true,Ordering::Release);stop.cancel();return;},
        permit=drain.0.gate.write()=>{
            drain.0.draining.store(true,Ordering::Release);
            drain.0.acknowledged.cancel();
            drop(permit);
        }
    }
    tokio::select! {biased; _=stop.cancelled()=>{}, _=deadline=>{drain.0.expired.store(true,Ordering::Release);stop.cancel();}}
}

#[derive(Clone, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Selector {
    pub coworker: String,
    pub bundle_digest: String,
    pub backend: Backend,
    pub scripted: bool,
    pub fallback_model: String,
    #[serde(default)]
    pub model_provider: openworker_runtime::provider::Kind,
    #[serde(default)]
    pub model_connections: std::collections::BTreeMap<String, String>,
    /// None is an explicitly local/unsandboxed pool, never a remote fallback.
    pub environment_provider: Option<String>,
    pub environment_identity: Option<String>,
    pub environment_profile: Option<String>,
}
impl Selector {
    pub fn validate(&self) -> Result<()> {
        crate::submissions::validate_id(&self.coworker, 120)?;
        crate::submissions::validate_id(&self.bundle_digest, 128)?;
        crate::submissions::validate_id(&self.fallback_model, 200)?;
        anyhow::ensure!(
            self.model_connections.len() <= 16,
            "Too many model connection identities"
        );
        for (id, identity) in &self.model_connections {
            crate::submissions::validate_id(id, 64)?;
            crate::submissions::validate_id(identity, 128)?;
        }
        if let Some(p) = &self.environment_provider {
            crate::submissions::validate_id(p, 80)?;
        }
        if self.environment_provider.is_some() != self.environment_identity.is_some()
            || self.environment_provider.is_some() != self.environment_profile.is_some()
        {
            bail!("Remote scheduling requires provider, deployment identity and profile");
        }
        for p in [&self.environment_identity, &self.environment_profile]
            .into_iter()
            .flatten()
        {
            crate::submissions::validate_id(p, 512)?;
        }
        Ok(())
    }
}
#[derive(Clone)]
pub struct Options {
    pub slots: usize,
    pub poll_interval: Duration,
    pub shutdown_timeout: Duration,
}
impl Default for Options {
    fn default() -> Self {
        Self {
            slots: 4,
            poll_interval: Duration::from_millis(250),
            shutdown_timeout: Duration::from_secs(45),
        }
    }
}
/// Must configure the matching coworker, model connections and provider registry
/// before returning. Use a session-private local controller directory, not the
/// remote workspace. The supplied store already owns exactly this session.
pub type Factory = Arc<dyn Fn(&str, PostgresStore) -> Result<Arc<Runtime>> + Send + Sync>;
pub type Catalog = Arc<dyn Fn() -> Result<Vec<(Selector, Factory)>> + Send + Sync>;
pub struct Worker {
    catalog: Option<Catalog>,
    config: Config,
    selector: Selector,
    options: Options,
    factory: Factory,
}
impl Worker {
    pub fn new(
        config: Config,
        selector: Selector,
        options: Options,
        factory: Factory,
    ) -> Result<Self> {
        config.validate()?;
        selector.validate()?;
        if selector.environment_provider.is_none() && !cfg!(feature = "test-support") {
            bail!(
                "Server worker requires a bound environment; local execution is test-support only"
            );
        }
        if !(1..=32).contains(&options.slots)
            || !(Duration::from_millis(50)..=Duration::from_secs(5))
                .contains(&options.poll_interval)
            || !(Duration::from_secs(1)..=Duration::from_secs(120))
                .contains(&options.shutdown_timeout)
            || config.lease_duration < config.timeout * 5
        {
            bail!("Invalid worker bounds; lease must be at least five database timeouts");
        }
        Ok(Self {
            config,
            selector,
            options,
            factory,
            catalog: None,
        })
    }
    pub fn with_catalog(mut self, catalog: Catalog) -> Self {
        self.catalog = Some(catalog);
        self
    }
    /// Stops on the first database/lease/runtime failure. A poisoned connection
    /// is never silently reopened to replay work. Deployment supervision may
    /// restart the worker; new incarnations inspect durable state after expiry.
    pub async fn run(self, shutdown: CancellationToken) -> Result<()> {
        self.run_with_drain(Drain::default(), shutdown, Duration::from_secs(300))
            .await
    }
    /// Planned drain leaves running execution alone until it completes/parks or
    /// the deadline expires. `shutdown` remains an immediate interruption signal.
    /// Storage/lease failures always interrupt immediately. The drain deadline
    /// starts at request observation, INCLUDING time spent awaiting admission.
    pub async fn run_with_drain(
        self,
        drain: Drain,
        shutdown: CancellationToken,
        timeout: Duration,
    ) -> Result<()> {
        anyhow::ensure!(
            (Duration::from_secs(1)..=Duration::from_secs(3600)).contains(&timeout),
            "Drain timeout must be 1–3600 seconds"
        );
        let stop = shutdown.child_token();
        let _stop_on_drop = StopOnDrop(stop.clone());
        let _watch = DrainWatch(tokio::spawn(watch_drain(
            drain.clone(),
            stop.clone(),
            timeout,
        )));
        let occupied = Arc::new(AtomicU64::new(0));
        let _metrics = WorkerMetrics::start(
            self.config.telemetry.clone(),
            self.options.slots as u64,
            occupied.clone(),
            drain.clone(),
        );
        let mut slots = JoinSet::new();
        for _ in 0..self.options.slots {
            let (config, selector, options, factory, stop, occupied, drain) = (
                self.config.clone(),
                self.selector.clone(),
                self.options.clone(),
                self.factory.clone(),
                stop.clone(),
                occupied.clone(),
                drain.clone(),
            );
            let catalog = self.catalog.clone();
            slots.spawn(async move {
                slot(
                    config, selector, options, factory, stop, occupied, drain, catalog,
                )
                .await
            });
        }
        let mut error = None;
        while let Some(result) = slots.join_next().await {
            match result {
                Ok(Ok(())) => {}
                Ok(Err(e)) => {
                    error.get_or_insert(e);
                    stop.cancel();
                }
                Err(e) => {
                    error.get_or_insert(e.into());
                    stop.cancel();
                }
            }
        }
        if drain.is_requested() && !stop.is_cancelled() {
            let _permit = drain.0.gate.write().await;
            drain.0.draining.store(true, Ordering::Release);
            drain.0.acknowledged.cancel();
        }
        if let Some(e) = error {
            Err(e)
        } else if drain.0.expired.load(Ordering::Acquire) {
            bail!("Worker drain deadline expired; interrupted execution, inspect durable results before retrying")
        } else {
            Ok(())
        }
    }
}
struct Occupied(Arc<AtomicU64>);
impl Occupied {
    fn enter(n: Arc<AtomicU64>) -> Self {
        n.fetch_add(1, Ordering::Relaxed);
        Self(n)
    }
}
impl Drop for Occupied {
    fn drop(&mut self) {
        self.0.fetch_sub(1, Ordering::Relaxed);
    }
}
struct WorkerMetrics {
    telemetry: Telemetry,
    task: Option<tokio::task::JoinHandle<()>>,
}
fn report_worker(t: &Telemetry, capacity: u64, active: u64, draining: bool) {
    t.observe_many(&[
        (Gauge::WorkerCapacity, capacity),
        (Gauge::WorkerActive, active),
        (Gauge::WorkerDraining, u64::from(draining)),
        (
            Gauge::WorkerHeartbeat,
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap_or_default()
                .as_secs(),
        ),
    ]);
}
impl WorkerMetrics {
    fn start(telemetry: Telemetry, capacity: u64, occupied: Arc<AtomicU64>, drain: Drain) -> Self {
        let task = if telemetry.enabled() {
            let t = telemetry.clone();
            Some(tokio::spawn(async move {
                let mut tick = interval(Duration::from_secs(5));
                tick.set_missed_tick_behavior(MissedTickBehavior::Skip);
                loop {
                    tick.tick().await;
                    report_worker(
                        &t,
                        if drain.is_requested() { 0 } else { capacity },
                        occupied.load(Ordering::Relaxed),
                        drain.is_acknowledged(),
                    );
                }
            }))
        } else {
            None
        };
        Self { telemetry, task }
    }
}
impl Drop for WorkerMetrics {
    fn drop(&mut self) {
        if let Some(task) = &self.task {
            task.abort();
            report_worker(&self.telemetry, 0, 0, false);
        }
    }
}
#[allow(clippy::too_many_arguments)] // Explicit worker ownership, limits and cancellation handles.
async fn slot(
    config: Config,
    selector: Selector,
    options: Options,
    factory: Factory,
    stop: CancellationToken,
    occupied: Arc<AtomicU64>,
    drain: Drain,
    catalog: Option<Catalog>,
) -> Result<()> {
    let mut turn = 0usize;
    let mut spare = Some(PostgresStore::open(config.clone())?);
    let mut tick = interval(options.poll_interval);
    tick.set_missed_tick_behavior(MissedTickBehavior::Skip);
    loop {
        tokio::select! {biased; _=stop.cancelled()=>return Ok(()), _=tick.tick()=>{} }
        let permit = drain.0.gate.read().await;
        if drain.is_requested() {
            return Ok(());
        }
        let store = spare.take().context("Worker slot missing store")?;
        let mut selected = None;
        let candidates = match &catalog {
            Some(get) => get()?,
            None => vec![(selector.clone(), factory.clone())],
        };
        for offset in 0..candidates.len() {
            let index = (turn + offset) % candidates.len();
            let (selector, factory) = &candidates[index];
            selector.validate()?;
            if let Some(lease) = store.claim_next(selector.clone())? {
                selected = Some((lease, selector.clone(), factory.clone()));
                turn = index + 1;
                break;
            }
        }
        let claim = selected;
        drop(permit);
        let Some((lease, selector, factory)) = claim else {
            spare = Some(store);
            continue;
        };
        if drain.is_requested() {
            store.release(&lease.session_id)?;
            return Ok(());
        }
        let _occupied = Occupied::enter(occupied.clone());
        execute(
            &lease.session_id,
            store,
            &config,
            &selector,
            &options,
            &factory,
            Lifecycle {
                stop: &stop,
                drain: &drain,
            },
        )
        .await?;
        if stop.is_cancelled() {
            return Ok(());
        }
        spare = Some(PostgresStore::open(config.clone())?);
    }
}
struct StopOnDrop(CancellationToken);
impl Drop for StopOnDrop {
    fn drop(&mut self) {
        self.0.cancel();
    }
}
struct RuntimeStop(Arc<Runtime>);
impl Drop for RuntimeStop {
    fn drop(&mut self) {
        self.0.request_shutdown();
    }
}
#[derive(Clone, Copy)]
struct Lifecycle<'a> {
    stop: &'a CancellationToken,
    drain: &'a Drain,
}
async fn execute(
    sid: &str,
    store: PostgresStore,
    config: &Config,
    selector: &Selector,
    options: &Options,
    factory: &Factory,
    lifecycle: Lifecycle<'_>,
) -> Result<()> {
    let span = config.telemetry.span(
        openworker_runtime::telemetry::Operation::Worker,
        Some(sid),
        None,
    );
    let result = openworker_runtime::telemetry::scope(
        span.context(),
        execute_inner(sid, store, config, selector, options, factory, lifecycle),
    )
    .await;
    span.finish(if result.is_ok() {
        openworker_runtime::telemetry::Outcome::Ok
    } else {
        openworker_runtime::telemetry::Outcome::Error
    });
    result
}
async fn execute_inner(
    sid: &str,
    store: PostgresStore,
    config: &Config,
    selector: &Selector,
    options: &Options,
    factory: &Factory,
    Lifecycle { stop, drain }: Lifecycle<'_>,
) -> Result<()> {
    let heartbeat_stop = CancellationToken::new();
    let guard = StopOnDrop(heartbeat_stop.clone());
    let renew = store.clone();
    let heartbeat_sid = sid.to_owned();
    let period = config.lease_duration / 4;
    let mut heartbeat = tokio::spawn(async move {
        let mut tick = interval(period);
        tick.set_missed_tick_behavior(MissedTickBehavior::Skip);
        loop {
            tokio::select! {biased; _=heartbeat_stop.cancelled()=>return anyhow::Ok(()),_=tick.tick()=>{renew.renew(&heartbeat_sid)?;} }
        }
    });
    // Hashing/config validation may do filesystem work; keep heartbeats runnable.
    let runtime = match tokio::task::block_in_place(|| factory(sid, store.clone())) {
        Ok(rt) => rt,
        Err(e) => {
            drop(guard);
            let _ = heartbeat.await;
            return Err(e.context("Worker initialization failed; claim retained until expiry"));
        }
    };
    let _runtime_stop = RuntimeStop(runtime.clone());
    let work = async {
        if runtime.execution_digest() != selector.bundle_digest {
            bail!("Worker factory identity differs from scheduling selector");
        }
        let mut tick = interval(options.poll_interval);
        tick.set_missed_tick_behavior(MissedTickBehavior::Skip);
        loop {
            tick.tick().await;
            let _admission = drain.0.gate.read().await;
            if drain.is_requested() && !runtime.is_busy(sid) {
                return Ok(());
            }
            if let Some(control) = store.next_control(sid)? {
                if drain.is_requested()
                    && matches!(
                        control.command.action,
                        openworker_runtime::control::Action::Response { .. }
                    )
                {
                    continue;
                }
                // Runtime returns a durable rejected/applied receipt or pending
                // while the current job settles. Never resubmit caller payload.
                runtime
                    .apply_control(sid, &control.command.request_id)
                    .await?;
            } else if !runtime.is_busy(sid) {
                let metadata = store.clone().metadata(sid)?;
                if metadata.agent_team.is_some() {
                    if store
                        .clone()
                        .agent_team(sid, "snapshot", &serde_json::json!({}), None)?["stopped"]
                        == true
                    {
                        return Ok(());
                    }
                    if metadata
                        .turn
                        .as_ref()
                        .is_some_and(|t| t.status == "resuming_agents")
                    {
                        runtime
                            .dispatch("team/resume", serde_json::json!({"session_id":sid}))
                            .await?;
                        continue;
                    }
                    let wake =
                        store
                            .clone()
                            .agent_team(sid, "wake", &serde_json::json!({}), None)?;
                    if wake["resume"] == true {
                        runtime
                            .dispatch("team/resume", serde_json::json!({"session_id":sid}))
                            .await?;
                        continue;
                    }
                    if wake["wake"] == true {
                        store.enqueue_agent_inbox_wake(sid)?;
                    }

                    if metadata
                        .turn
                        .as_ref()
                        .is_some_and(|t| t.status == "waiting_agents")
                    {
                        return Ok(());
                    }
                }
                if drain.is_requested() {
                    return Ok(());
                }
                let next = match store.next_turn(sid) {
                    Ok(next) => next,
                    Err(e)
                        if matches!(
                            e.downcast_ref::<crate::SubmissionError>(),
                            Some(
                                crate::SubmissionError::ProviderBlocked
                                    | crate::SubmissionError::Closed
                            )
                        ) =>
                    {
                        return Ok(())
                    }
                    Err(e) => return Err(e),
                };
                if let Some(next) = next {
                    if let Err(error) = runtime
                        .dispatch("turn/start", serde_json::to_value(next)?)
                        .await
                    {
                        // A control can arrive after the peek. Acceptance has
                        // committed nothing: re-read the priority path next tick,
                        // without restarting this worker or changing task IDs.
                        if error.downcast_ref::<crate::SubmissionError>()
                            != Some(&crate::SubmissionError::ControlsPending)
                        {
                            return Err(error);
                        }
                    }
                } else {
                    return Ok(());
                }
            }
        }
    };
    let mut heartbeat_selected = false;
    let result = tokio::select! {biased;
        _=stop.cancelled()=>Ok(()),
        outcome=&mut heartbeat=>{heartbeat_selected=true;match outcome {Ok(Ok(()))=>Err(anyhow::anyhow!("Heartbeat stopped unexpectedly")),Ok(Err(e))=>Err(e.context("Lease renewal failed; stopping worker")),Err(e)=>Err(e.into())}},
        r=work=>r,
    };
    // Cancel already-running futures/close only this session's attachments. A
    // lost lease fences writes; it cannot undo an external effect already sent.
    let cleanup = tokio::time::timeout(options.shutdown_timeout, runtime.shutdown()).await;
    drop(guard);
    let heartbeat_cleanup = if !heartbeat_selected {
        heartbeat
            .await
            .context("Heartbeat task failed")
            .and_then(|v| v)
    } else {
        Ok(())
    };
    result?;
    heartbeat_cleanup?;
    cleanup.context("Worker shutdown incomplete; host must terminate worker, lease retained")??;
    store.release(sid)?;
    Ok(())
}

#[cfg(test)]
mod metric_tests {
    use super::*;
    #[tokio::test]
    async fn drain_ack_waits_for_admission_and_deadline_includes_that_wait() {
        let drain = Drain::default();
        let stop = CancellationToken::new();
        let permit = drain.0.gate.read().await;
        let task = tokio::spawn(watch_drain(
            drain.clone(),
            stop.clone(),
            Duration::from_secs(1),
        ));
        drain.request();
        tokio::task::yield_now().await;
        assert!(!drain.is_acknowledged());
        tokio::time::timeout(Duration::from_secs(2), stop.cancelled())
            .await
            .unwrap();
        assert!(drain.0.expired.load(Ordering::Acquire));
        assert!(!drain.is_acknowledged());
        drop(permit);
        task.await.unwrap();
    }
    #[tokio::test]
    async fn acknowledgement_follows_inflight_admission_and_abort_is_independent() {
        let drain = Drain::default();
        let stop = CancellationToken::new();
        let permit = drain.0.gate.read().await;
        let task = tokio::spawn(watch_drain(
            drain.clone(),
            stop.clone(),
            Duration::from_secs(5),
        ));
        drain.request();
        tokio::task::yield_now().await;
        assert!(!drain.is_acknowledged());
        drop(permit);
        tokio::time::timeout(Duration::from_secs(1), drain.acknowledged())
            .await
            .unwrap();
        assert!(drain.is_acknowledged());
        assert!(!stop.is_cancelled());
        stop.cancel();
        task.await.unwrap();
        assert!(!drain.0.expired.load(Ordering::Acquire));
    }
    #[test]
    fn occupied_slots_release_on_normal_exit_and_unwind() {
        let count = Arc::new(AtomicU64::new(0));
        let a = Occupied::enter(count.clone());
        let b = Occupied::enter(count.clone());
        assert_eq!(count.load(Ordering::Relaxed), 2);
        drop(a);
        assert_eq!(count.load(Ordering::Relaxed), 1);
        let _ = std::panic::catch_unwind(|| {
            let _c = Occupied::enter(count.clone());
            panic!("fixture");
        });
        assert_eq!(count.load(Ordering::Relaxed), 1);
        drop(b);
        assert_eq!(count.load(Ordering::Relaxed), 0);
    }
}
