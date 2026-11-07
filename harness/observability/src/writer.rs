use crate::{Conflict, Step, Store, Write};
use anyhow::{ensure, Result};
use openworker_telemetry::{Capture, Observation, Record, Sink};
use serde::Serialize;
use std::{
    sync::{
        atomic::{AtomicBool, AtomicU64, Ordering},
        mpsc, Arc, Mutex, OnceLock,
    },
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};

#[derive(Default)]
struct Counters {
    accepted: AtomicU64,
    persisted: AtomicU64,
    duplicates: AtomicU64,
    conflicts: AtomicU64,
    dropped: AtomicU64,
    failed_writes: AtomicU64,
    rejected: AtomicU64,
    abandoned: AtomicU64,
    pruned: AtomicU64,
    prune_failures: AtomicU64,
    last_success_ms: AtomicU64,
}
#[derive(Clone, Default)]
pub struct Statistics(Arc<Counters>);
#[derive(Clone, Debug, Default, Serialize)]
pub struct Snapshot {
    pub accepted: u64,
    pub persisted: u64,
    pub duplicates: u64,
    pub conflicts: u64,
    pub dropped: u64,
    pub failed_writes: u64,
    pub rejected: u64,
    pub abandoned: u64,
    pub pending: u64,
    pub pruned: u64,
    pub prune_failures: u64,
    pub last_success_ms: u64,
}
impl Statistics {
    pub fn snapshot(&self) -> Snapshot {
        let load = |v: &AtomicU64| v.load(Ordering::Relaxed);
        let mut s = Snapshot {
            accepted: load(&self.0.accepted),
            persisted: load(&self.0.persisted),
            duplicates: load(&self.0.duplicates),
            conflicts: load(&self.0.conflicts),
            dropped: load(&self.0.dropped),
            failed_writes: load(&self.0.failed_writes),
            rejected: load(&self.0.rejected),
            abandoned: load(&self.0.abandoned),
            pending: 0,
            pruned: load(&self.0.pruned),
            prune_failures: load(&self.0.prune_failures),
            last_success_ms: load(&self.0.last_success_ms),
        };
        s.pending = s.accepted.saturating_sub(
            s.persisted + s.duplicates + s.conflicts + s.failed_writes + s.abandoned,
        );
        s
    }
}
fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
        .min(u64::MAX as u128) as u64
}
struct Shared {
    deployment: String,
    admission: Mutex<()>,
    stop: AtomicBool,
    deadline: OnceLock<Instant>,
    statistics: Statistics,
}
#[derive(Clone)]
pub struct Recorder {
    tx: mpsc::SyncSender<Step>,
    shared: Arc<Shared>,
}
pub struct Controller {
    shared: Arc<Shared>,
    done: mpsc::Receiver<()>,
}
impl Recorder {
    pub fn start(
        mut store: Box<dyn Store>,
        deployment: String,
        capacity: usize,
        retention_days: u16,
    ) -> Result<(Self, Controller)> {
        ensure!(
            crate::identifier(&deployment),
            "Invalid recording deployment"
        );
        ensure!(
            (1..=2048).contains(&capacity),
            "Recording capacity must be 1..=2048"
        );
        ensure!(
            (1..=365).contains(&retention_days),
            "Recording retention must be 1..=365 days"
        );
        let (tx, rx) = mpsc::sync_channel::<Step>(capacity);
        let (done, wait) = mpsc::sync_channel(1);
        let shared = Arc::new(Shared {
            deployment: deployment.clone(),
            admission: Mutex::new(()),
            stop: AtomicBool::new(false),
            deadline: OnceLock::new(),
            statistics: Statistics::default(),
        });
        let thread = shared.clone();
        std::thread::Builder::new()
            .name("ow-recording".into())
            .spawn(move || {
                let counts = &thread.statistics.0;
                let mut prune_at = Instant::now() + Duration::from_secs(60);
                loop {
                    if thread.deadline.get().is_some_and(|d| Instant::now() >= *d) {
                        while rx.try_recv().is_ok() {
                            counts.abandoned.fetch_add(1, Ordering::Relaxed);
                        }
                        break;
                    }
                    let next = if thread.stop.load(Ordering::Acquire) {
                        match rx.try_recv() {
                            Ok(s) => Some(s),
                            Err(_) => break,
                        }
                    } else {
                        match rx.recv_timeout(Duration::from_millis(200)) {
                            Ok(s) => Some(s),
                            Err(mpsc::RecvTimeoutError::Disconnected) => break,
                            Err(_) => None,
                        }
                    };
                    if let Some(step) = next {
                        // Stable insert identity makes explicit observation reconciliation
                        // safe. Never invoke/replay tools or automatically retry SQL here.
                        let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                            store.put(&step)
                        }));
                        match result {
                            Ok(Ok(Write::Inserted)) => {
                                counts.persisted.fetch_add(1, Ordering::Relaxed);
                                counts.last_success_ms.store(now_ms(), Ordering::Relaxed);
                            }
                            Ok(Ok(Write::Duplicate)) => {
                                counts.duplicates.fetch_add(1, Ordering::Relaxed);
                            }
                            Ok(Err(e)) if e.is::<Conflict>() => {
                                counts.conflicts.fetch_add(1, Ordering::Relaxed);
                            }
                            _ => {
                                counts.failed_writes.fetch_add(1, Ordering::Relaxed);
                            }
                        }
                    }
                    if !thread.stop.load(Ordering::Acquire) && Instant::now() >= prune_at {
                        let cutoff = now_ms().saturating_sub(u64::from(retention_days) * 86400000);
                        match std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                            store.prune(&deployment, cutoff, 1000)
                        })) {
                            Ok(Ok(n)) => {
                                counts.pruned.fetch_add(n, Ordering::Relaxed);
                            }
                            _ => {
                                counts.prune_failures.fetch_add(1, Ordering::Relaxed);
                            }
                        }
                        prune_at = Instant::now() + Duration::from_secs(60);
                    }
                }
                let _ = done.send(());
            })?;
        Ok((
            Self {
                tx,
                shared: shared.clone(),
            },
            Controller { shared, done: wait },
        ))
    }
    pub fn statistics(&self) -> Statistics {
        self.shared.statistics.clone()
    }
    pub fn record(&self, step: Step) -> bool {
        if step.deployment != self.shared.deployment || step.validate().is_err() {
            self.shared
                .statistics
                .0
                .rejected
                .fetch_add(1, Ordering::Relaxed);
            return false;
        }
        // Serialize only stop/enqueue, never validation or database work. Producer
        // contention drops visibly instead of blocking an agent thread.
        let Ok(_admission) = self.shared.admission.try_lock() else {
            self.shared
                .statistics
                .0
                .dropped
                .fetch_add(1, Ordering::Relaxed);
            return false;
        };
        if self.shared.stop.load(Ordering::Acquire) {
            self.shared
                .statistics
                .0
                .dropped
                .fetch_add(1, Ordering::Relaxed);
            return false;
        }
        match self.tx.try_send(step) {
            Ok(()) => {
                self.shared
                    .statistics
                    .0
                    .accepted
                    .fetch_add(1, Ordering::Relaxed);
                true
            }
            Err(_) => {
                self.shared
                    .statistics
                    .0
                    .dropped
                    .fetch_add(1, Ordering::Relaxed);
                false
            }
        }
    }
}
impl Controller {
    pub fn statistics(&self) -> Statistics {
        self.shared.statistics.clone()
    }
    pub fn shutdown(&self, budget: Duration) -> bool {
        let budget = budget.min(Duration::from_secs(5));
        {
            let _admission = self
                .shared
                .admission
                .lock()
                .unwrap_or_else(|e| e.into_inner());
            self.shared.deadline.get_or_init(|| Instant::now() + budget);
            self.shared.stop.store(true, Ordering::Release);
        }
        self.done.recv_timeout(budget).is_ok()
    }
}
impl Drop for Controller {
    fn drop(&mut self) {
        let _admission = self
            .shared
            .admission
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        self.shared
            .deadline
            .get_or_init(|| Instant::now() + Duration::from_secs(5));
        self.shared.stop.store(true, Ordering::Release);
    }
}

/// Local recording is queued before vendor export and uses its own writer. Only
/// allowlisted metadata is persisted even when an exporter explicitly captures
/// full content. Raising local capture never changes the export privacy choice.
pub struct RecordingSink {
    recorder: Recorder,
    deployment: String,
    role: String,
    exporter: Option<Box<dyn Sink>>,
    export_capture: Capture,
}
impl RecordingSink {
    pub fn new(
        recorder: Recorder,
        deployment: String,
        role: String,
        exporter: Option<Box<dyn Sink>>,
        export_capture: Capture,
    ) -> Result<Self> {
        ensure!(
            crate::identifier(&deployment) && crate::identifier(&role),
            "Invalid recording scope"
        );
        Ok(Self {
            recorder,
            deployment,
            role,
            exporter,
            export_capture,
        })
    }
}
impl Sink for RecordingSink {
    fn export(&mut self, records: &[Record], stats: openworker_telemetry::Snapshot) -> bool {
        let mut ok = true;
        for r in records {
            match Step::from_record(&self.deployment, &self.role, r) {
                Ok(s) => ok = self.recorder.record(s) && ok,
                Err(_) => {
                    self.recorder
                        .shared
                        .statistics
                        .0
                        .rejected
                        .fetch_add(1, Ordering::Relaxed);
                    ok = false;
                }
            }
        }
        if let Some(exporter) = &mut self.exporter {
            let mut outgoing = records.to_vec();
            for record in &mut outgoing {
                match self.export_capture {
                    Capture::Off => record.attributes.clear(),
                    Capture::Metadata => record.attributes.retain(|(key, value)| {
                        openworker_telemetry::metadata_attribute(key, value)
                    }),
                    Capture::Full => record
                        .attributes
                        .retain(|(key, value)| openworker_telemetry::export_attribute(key, value)),
                }
            }
            ok = exporter.export(&outgoing, stats) && ok;
        }
        ok
    }
    fn observe(&mut self, samples: &[Observation], stats: openworker_telemetry::Snapshot) -> bool {
        self.exporter
            .as_mut()
            .is_none_or(|s| s.observe(samples, stats))
    }
}
