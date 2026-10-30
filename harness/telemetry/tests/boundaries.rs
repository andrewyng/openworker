use openworker_telemetry::*;
use std::{
    sync::{mpsc, Arc, Mutex},
    time::{Duration, Instant},
};
struct Capture(Arc<Mutex<Vec<Record>>>);
impl Sink for Capture {
    fn export(&mut self, r: &[Record], _: Snapshot) -> bool {
        self.0.lock().unwrap().extend_from_slice(r);
        true
    }
}
#[tokio::test]
async fn hierarchy_is_task_local_and_only_hashes_cross_the_boundary() {
    let rows = Arc::new(Mutex::new(vec![]));
    let (t, c) = Telemetry::start(Capture(rows.clone()), 2048).unwrap();
    let root = t.span(
        Operation::Turn,
        Some("PRIVATE_SESSION"),
        Some("PRIVATE_TEXT"),
    );
    let context = root.context();
    scope(context, async {
        let mut model = t.span(Operation::Model, Some("PRIVATE_SESSION"), None);
        model.attempt(2);
        model.usage(40, 10);
        model.first_output();
        model.finish(Outcome::Ok);
        let other = t.clone();
        tokio::spawn(async move { other.event(Operation::Command, Outcome::Ok, None, None) })
            .await
            .unwrap();
    })
    .await;
    root.finish(Outcome::Waiting);
    assert!(c.shutdown(Duration::from_secs(2)));
    let records = rows.lock().unwrap();
    assert_eq!(records.len(), 3);
    let model = records
        .iter()
        .find(|r| r.operation == Operation::Model)
        .unwrap();
    assert_eq!(model.parent, Some(context));
    assert_eq!(model.context.trace, context.trace);
    let independent = records
        .iter()
        .find(|r| r.operation == Operation::Command)
        .unwrap();
    assert!(independent.parent.is_none());
    assert_ne!(independent.context.trace, context.trace);
    assert!(!format!("{records:?}").contains("PRIVATE"));
    assert_eq!(model.input_tokens, Some(40));
    assert!(model.first_output.is_some());
}
struct Block(mpsc::SyncSender<()>, mpsc::Receiver<()>);
#[tokio::test]
async fn durable_turn_identity_links_segments_without_counting_pause_as_execution() {
    let rows = Arc::new(Mutex::new(vec![]));
    let (t, c) = Telemetry::start(Capture(rows.clone()), 32).unwrap();
    let mut first = t.span(Operation::Turn, Some("session"), Some("turn"));
    first.turn("turn");
    let a = first.context();
    first.finish(Outcome::Waiting);
    let mut second = t.span(Operation::Turn, Some("session"), Some("turn"));
    second.turn("turn");
    second.link(a);
    let b = second.context();
    scope_turn("turn", b, async {
        let mut model = t.span(Operation::Model, Some("session"), None);
        model.purpose(ModelPurpose::Main);
        model.reported_usage(TokenUsage {
            input: Some(0),
            output: Some(0),
            ..Default::default()
        });
        model.finish(Outcome::Ok);
    })
    .await;
    second.finish(Outcome::Ok);
    t.interval(
        Operation::ApprovalWait,
        Outcome::Ok,
        "session",
        "turn",
        "approval",
        1000,
        6000,
    );
    t.interval(
        Operation::QuestionWait,
        Outcome::Ok,
        "session",
        "turn",
        "question",
        7000,
        6500,
    );
    assert!(c.shutdown(Duration::from_secs(2)));
    let r = rows.lock().unwrap();
    assert_eq!(a.trace, b.trace);
    assert_ne!(a.span, b.span);
    assert_eq!(r[0].turn_hash, r[1].turn_hash);
    let resumed = r.iter().find(|r| r.context == b).unwrap();
    assert_eq!(resumed.links, vec![a]);
    assert!(resumed.elapsed < Duration::from_secs(1));
    let m = r.iter().find(|r| r.operation == Operation::Model).unwrap();
    assert_eq!(m.parent, Some(b));
    assert_eq!(m.context.trace, a.trace);
    assert_eq!(m.input_tokens, Some(0));
    let wait = r
        .iter()
        .find(|r| r.operation == Operation::ApprovalWait)
        .unwrap();
    assert_eq!(wait.elapsed, Duration::from_secs(5));
    assert!(wait.timing_known);
    assert_eq!(wait.context.trace, a.trace);
    assert!(
        !r.iter()
            .find(|r| r.operation == Operation::QuestionWait)
            .unwrap()
            .timing_known
    );
}
impl Sink for Block {
    fn export(&mut self, _: &[Record], _: Snapshot) -> bool {
        let _ = self.0.try_send(());
        let _ = self.1.recv();
        false
    }
}
#[test]
fn observation_snapshot_is_enqueued_or_dropped_as_one_unit() {
    struct Samples(Arc<Mutex<Vec<Observation>>>);
    impl Sink for Samples {
        fn export(&mut self, _: &[Record], _: Snapshot) -> bool {
            panic!("Gauges must not create logs or spans")
        }
        fn observe(&mut self, s: &[Observation], _: Snapshot) -> bool {
            self.0.lock().unwrap().extend_from_slice(s);
            true
        }
    }
    let rows = Arc::new(Mutex::new(Vec::new()));
    let (t, c) = Telemetry::start(Samples(rows.clone()), 1).unwrap();
    t.observe_many(&[(Gauge::TurnsQueued, 5), (Gauge::StateObservedAt, 123)]);
    assert!(c.shutdown(Duration::from_secs(2)));
    assert_eq!(c.snapshot().accepted, 1);
    let rows = rows.lock().unwrap();
    assert_eq!(rows.len(), 2);
    assert_eq!((rows[0].value, rows[1].value), (5, 123));
}
#[test]
fn full_queue_and_blocked_exporter_never_block_producers_or_shutdown() {
    let (entered, wait) = mpsc::sync_channel(1);
    let (release, blocked) = mpsc::channel();
    let (t, c) = Telemetry::start(Block(entered, blocked), 2).unwrap();
    t.event(Operation::Tool, Outcome::Ok, None, None);
    wait.recv_timeout(Duration::from_secs(1)).unwrap();
    let now = Instant::now();
    for _ in 0..10000 {
        t.observe(Gauge::TurnsQueued, 1)
    }
    assert!(now.elapsed() < Duration::from_secs(2));
    assert_eq!(c.snapshot().accepted, 3);
    assert_eq!(c.snapshot().dropped, 9998);
    let now = Instant::now();
    assert!(!c.shutdown(Duration::from_millis(20)));
    assert!(now.elapsed() < Duration::from_millis(200));
    drop(release);
    assert!(c.shutdown(Duration::from_secs(2)));
    assert!(c.snapshot().export_failures >= 1);
}
#[test]
fn disabled_and_abandoned_spans_are_truthful() {
    assert!(
        std::mem::size_of::<Record>() <= 512,
        "Keep queue payloads bounded independently of strings/JSON"
    );
    let t = Telemetry::default();
    assert!(!t.enabled());
    t.event(Operation::Command, Outcome::Ok, None, None);
    assert_eq!(t.snapshot().accepted, 0);
    let rows = Arc::new(Mutex::new(vec![]));
    let (t, c) = Telemetry::start(Capture(rows.clone()), 2).unwrap();
    drop(t.span(Operation::Tool, None, None));
    drop(t.span(Operation::Model, None, None));
    assert!(c.shutdown(Duration::from_secs(2)));
    let rows = rows.lock().unwrap();
    assert_eq!(rows.len(), 2);
    assert_eq!(rows[0].outcome, Outcome::Unknown);
    assert_eq!(rows[1].outcome, Outcome::Interrupted);
    assert_eq!(rows[1].input_tokens, None);
}
