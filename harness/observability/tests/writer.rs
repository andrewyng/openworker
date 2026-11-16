use openworker_observability::{
    sqlite::Sqlite, Recorder, RecordingSink, Step, Store, StoredStep, Write,
};
use openworker_telemetry::{
    Capture, ContentPolicy, Operation, Outcome, Record, Sink, Snapshot, Telemetry,
};
use std::{
    sync::{mpsc, Arc, Mutex},
    time::{Duration, Instant},
};
struct Collect(Arc<Mutex<Vec<Record>>>);
impl Sink for Collect {
    fn export(&mut self, r: &[Record], _: Snapshot) -> bool {
        self.0.lock().unwrap().extend_from_slice(r);
        true
    }
}
fn record() -> Record {
    let out = Arc::new(Mutex::new(Vec::new()));
    let (t, c) = Telemetry::start_with_content(
        Collect(out.clone()),
        10,
        ContentPolicy {
            capture: Capture::Full,
            ..Default::default()
        },
    )
    .unwrap();
    let mut s = t.span(Operation::Model, Some("session-id"), Some("attempt-id"));
    s.metadata("gen_ai.request.model", "claude-sonnet");
    s.content("gen_ai.prompt", || {
        "private prompt sk-notarealsecret1234567890".into()
    });
    s.content("gen_ai.completion", || "private answer".into());
    s.usage(100, 20);
    s.first_output();
    s.finish(Outcome::Ok);
    assert!(c.shutdown(Duration::from_secs(1)));
    let r = out.lock().unwrap()[0].clone();
    r
}
#[test]
fn typed_purpose_and_reported_breakdowns_survive_conversion_without_inference() {
    let mut r = record();
    assert!(Step::from_record("test", "worker", &r)
        .unwrap()
        .purpose
        .is_none());
    r.purpose = Some(openworker_telemetry::ModelPurpose::Compaction);
    r.turn_hash = Some([7; 32]);
    r.links.push(openworker_telemetry::TraceContext {
        trace: [3; 16],
        span: [4; 8],
    });
    r.usage_details = openworker_telemetry::TokenUsage {
        input: Some(100),
        output: Some(20),
        cache_read: Some(40),
        cache_write: Some(10),
        reasoning: Some(5),
    };
    let step = Step::from_record("test", "worker", &r).unwrap();
    assert_eq!(step.version, 2);
    assert_eq!(step.purpose.as_deref(), Some("compaction"));
    assert_eq!(step.links.len(), 1);
    let usage = step.usage.as_ref().unwrap();
    assert_eq!(usage.cache_read, Some(40));
    assert_eq!(usage.cache_write, Some(10));
    assert_eq!(usage.reasoning, Some(5));
    let encoded = serde_json::to_string(&step).unwrap();
    assert!(!encoded.contains("private"));
    assert_eq!(serde_json::from_str::<Step>(&encoded).unwrap(), step);
}
#[test]
fn real_instrumentation_reopens_metadata_only_and_keeps_export_off() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("obs.db");
    let (store, done) = Recorder::start(
        Box::new(Sqlite::open(&path).unwrap()),
        "deployment".into(),
        16,
        30,
    )
    .unwrap();
    let stats = store.statistics();
    let exported = Arc::new(Mutex::new(Vec::new()));
    let mut sink = RecordingSink::new(
        store,
        "deployment".into(),
        "worker".into(),
        Some(Box::new(Collect(exported.clone()))),
        Capture::Off,
    )
    .unwrap();
    let r = record();
    assert!(sink.export(&[r.clone(), r.clone()], Snapshot::default()));
    let mut changed = r.clone();
    changed.input_tokens = Some(101);
    assert!(sink.export(&[changed], Snapshot::default()));
    assert!(done.shutdown(Duration::from_secs(2)));
    let s = stats.snapshot();
    assert_eq!(
        (s.persisted, s.duplicates, s.conflicts, s.pending),
        (1, 1, 1, 0)
    );
    assert!(exported
        .lock()
        .unwrap()
        .iter()
        .all(|r| r.attributes.is_empty()));
    let mut reopened = Sqlite::open(&path).unwrap();
    let rows = reopened.list("deployment", None, 0, 100).unwrap();
    assert_eq!(rows.len(), 1);
    assert_eq!(rows[0].step.model.as_deref(), Some("claude-sonnet"));
    let json = serde_json::to_string(&rows).unwrap();
    assert!(
        !json.contains("private prompt")
            && !json.contains("private answer")
            && !json.contains("sk-notareal")
            && !json.contains("session-id")
    );
    assert_eq!(rows[0].step.usage.as_ref().unwrap().input, Some(100));
    assert_eq!(rows[0].step.usage.as_ref().unwrap().cache_read, None);
}
struct Blocked {
    entered: mpsc::SyncSender<()>,
    release: mpsc::Receiver<()>,
}
impl Store for Blocked {
    fn put(&mut self, _: &Step) -> anyhow::Result<Write> {
        self.entered.send(()).unwrap();
        self.release.recv().unwrap();
        Ok(Write::Inserted)
    }
    fn get(&mut self, _: &str, _: &str) -> anyhow::Result<Option<Step>> {
        unreachable!()
    }
    fn list(
        &mut self,
        _: &str,
        _: Option<&str>,
        _: u64,
        _: u16,
    ) -> anyhow::Result<Vec<StoredStep>> {
        unreachable!()
    }
    fn prune(&mut self, _: &str, _: u64, _: u16) -> anyhow::Result<u64> {
        unreachable!()
    }
}
#[test]
fn blocked_writer_has_bounded_queue_and_shutdown_without_blocking_producer() {
    let (entered, rx) = mpsc::sync_channel(1);
    let (tx, release) = mpsc::sync_channel(1);
    let (recorder, c) = Recorder::start(
        Box::new(Blocked { entered, release }),
        "deployment".into(),
        1,
        30,
    )
    .unwrap();
    let step = Step::from_record("deployment", "test", &record()).unwrap();
    assert!(recorder.record(step.clone()));
    rx.recv_timeout(Duration::from_secs(1)).unwrap();
    assert!(recorder.record(step.clone()));
    let at = Instant::now();
    for _ in 0..1000 {
        assert!(!recorder.record(step.clone()));
    }
    assert!(at.elapsed() < Duration::from_secs(1));
    assert!(!c.shutdown(Duration::from_millis(10)));
    assert!(!recorder.record(step));
    tx.send(()).unwrap();
    let deadline = Instant::now() + Duration::from_secs(2);
    while recorder.statistics().snapshot().abandoned == 0 && Instant::now() < deadline {
        std::thread::sleep(Duration::from_millis(5));
    }
    let stats = recorder.statistics().snapshot();
    assert_eq!(stats.persisted, 1);
    assert_eq!(stats.abandoned, 1);
    assert_eq!(stats.dropped, 1001);
}
#[test]
fn invalid_scope_is_rejected_before_queue() {
    let d = tempfile::tempdir().unwrap();
    let (recorder, c) = Recorder::start(
        Box::new(Sqlite::open(&d.path().join("obs.db")).unwrap()),
        "deployment".into(),
        8,
        30,
    )
    .unwrap();
    assert!(!recorder.record(Step::from_record("other", "test", &record()).unwrap()));
    assert!(c.shutdown(Duration::from_secs(1)));
    assert_eq!(recorder.statistics().snapshot().rejected, 1);
}

struct StalledExporter {
    entered: mpsc::SyncSender<()>,
    release: mpsc::Receiver<()>,
}
impl Sink for StalledExporter {
    fn export(&mut self, _: &[Record], _: Snapshot) -> bool {
        self.entered.send(()).unwrap();
        self.release.recv().unwrap();
        false
    }
}
#[test]
fn local_commit_does_not_wait_for_a_stalled_vendor_exporter() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("obs.db");
    let (recorder, recording_done) = Recorder::start(
        Box::new(Sqlite::open(&path).unwrap()),
        "deployment".into(),
        16,
        30,
    )
    .unwrap();
    let stats = recorder.statistics();
    let (tx, entered) = mpsc::sync_channel(1);
    let (release, rx) = mpsc::sync_channel(1);
    let sink = RecordingSink::new(
        recorder,
        "deployment".into(),
        "test".into(),
        Some(Box::new(StalledExporter {
            entered: tx,
            release: rx,
        })),
        Capture::Off,
    )
    .unwrap();
    let (t, done) = Telemetry::start_with_content(
        sink,
        16,
        ContentPolicy {
            capture: Capture::Metadata,
            ..Default::default()
        },
    )
    .unwrap();
    let mut span = t.span(Operation::Tool, Some("session"), Some("call"));
    span.metadata("gen_ai.tool.name", "read_file");
    span.finish(Outcome::Ok);
    entered.recv_timeout(Duration::from_secs(1)).unwrap();
    let deadline = Instant::now() + Duration::from_secs(2);
    while stats.snapshot().persisted == 0 && Instant::now() < deadline {
        std::thread::sleep(Duration::from_millis(5));
    }
    let committed = stats.snapshot().persisted;
    release.send(()).unwrap();
    assert!(done.shutdown(Duration::from_secs(2)));
    assert!(recording_done.shutdown(Duration::from_secs(2)));
    assert_eq!(
        committed, 1,
        "Local commit must occur while exporter remains blocked"
    );
    assert_eq!(stats.snapshot().failed_writes, 0);
    assert_eq!(t.snapshot().export_failures, 1);
    assert_eq!(
        Sqlite::open(&path)
            .unwrap()
            .list("deployment", None, 0, 100)
            .unwrap()
            .len(),
        1
    );
}
