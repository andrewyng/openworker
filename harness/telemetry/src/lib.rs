//! Instance-owned instrumentation, content-free by default. No exporter or
//! global provider is installed implicitly. Execution only enqueues records.
//! Prompt/tool content is attached only under an explicit [`ContentPolicy`].
use sha2::{Digest, Sha256};
use std::{
    future::Future,
    sync::{
        atomic::{AtomicBool, AtomicU64, Ordering},
        mpsc, Arc, OnceLock,
    },
    time::{Duration, Instant, SystemTime},
};
mod accounting;
pub mod attributes;
mod observation;
pub use accounting::{ModelPurpose, TokenUsage};
#[cfg(feature = "otel")]
pub mod otel;
#[cfg(feature = "otlp")]
pub mod otlp;
pub use observation::{Gauge, Observation};

/// Names are bounded identifiers, never paths, endpoints or credential-shaped
/// strings. Reserved typed fields cannot be overridden via metadata strings.
pub fn metadata_attribute(key: &str, value: &str) -> bool {
    attributes::field(key).is_some_and(|f| f.capture == attributes::CaptureClass::Metadata)
        && !value.is_empty()
        && value.len() <= 256
        && !value.starts_with('/')
        && !value.contains("://")
        && value
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"_-./:".contains(&b))
        && redact(value) == value
        && (key != attributes::POLICY_DIGEST
            || value.len() == 64 && value.bytes().all(|b| b.is_ascii_hexdigit()))
}
pub fn export_attribute(key: &str, value: &str) -> bool {
    metadata_attribute(key, value)
        || attributes::field(key)
            .is_some_and(|f| f.capture == attributes::CaptureClass::Full && value.len() <= 65539)
}

/// What, beyond content-free timings, outcomes and token counts, may leave the
/// process. Every level above `Off` is an explicit operator decision.
#[derive(
    Clone,
    Copy,
    Debug,
    Default,
    PartialEq,
    Eq,
    PartialOrd,
    Ord,
    serde::Serialize,
    serde::Deserialize,
)]
#[serde(rename_all = "snake_case")]
pub enum Capture {
    /// Content-free (the default).
    #[default]
    Off,
    /// Registered model/tool names and sandbox/policy identifiers; no text, arguments
    /// or results.
    Metadata,
    /// Also prompts, completions, tool arguments and tool results, truncated
    /// to `max_bytes` per value and passed through best-effort secret
    /// redaction. May still contain personal or confidential data.
    Full,
}
#[derive(Clone, Debug, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ContentPolicy {
    #[serde(default)]
    pub capture: Capture,
    /// Per-attribute byte limit for `Full` capture (1..=65536).
    #[serde(default = "default_max_bytes")]
    pub max_bytes: usize,
    /// Mask common credential shapes (API keys, bearer tokens, private keys).
    #[serde(default = "yes")]
    pub redact_secrets: bool,
}
fn default_max_bytes() -> usize {
    8192
}
fn yes() -> bool {
    true
}
impl Default for ContentPolicy {
    fn default() -> Self {
        Self {
            capture: Capture::Off,
            max_bytes: default_max_bytes(),
            redact_secrets: true,
        }
    }
}
impl ContentPolicy {
    pub fn validate(&self) -> anyhow::Result<()> {
        anyhow::ensure!(
            (1..=65536).contains(&self.max_bytes),
            "Content capture max_bytes must be 1..=65536"
        );
        Ok(())
    }
    fn bound(&self, text: &str, keep_end: bool) -> String {
        let text = if self.redact_secrets {
            redact(text)
        } else {
            text.to_owned()
        };
        if text.len() <= self.max_bytes {
            return text;
        }
        if keep_end {
            let mut start = text.len() - self.max_bytes;
            while !text.is_char_boundary(start) {
                start += 1;
            }
            format!("…{}", &text[start..])
        } else {
            let mut end = self.max_bytes;
            while !text.is_char_boundary(end) {
                end -= 1;
            }
            format!("{}…", &text[..end])
        }
    }
}

/// Best-effort masking of common credential shapes. Not a guarantee: treat
/// `Capture::Full` output as sensitive regardless.
pub fn redact(text: &str) -> String {
    const PREFIXES: [&str; 9] = [
        "sk-",
        "sk_",
        "pk-lf-",
        "sk-lf-",
        "ghp_",
        "gho_",
        "github_pat_",
        "xox",
        "AKIA",
    ];
    let token =
        |c: char| c.is_ascii_alphanumeric() || matches!(c, '-' | '_' | '.' | '/' | '+' | '=');
    let mut out = String::with_capacity(text.len());
    let mut rest = text;
    while !rest.is_empty() {
        // PEM private keys: drop everything up to the END marker.
        if let Some(body) = rest.strip_prefix("-----BEGIN ") {
            if body
                .split("-----")
                .next()
                .is_some_and(|h| h.ends_with("PRIVATE KEY"))
            {
                let end = rest
                    .find("-----END ")
                    .and_then(|i| rest[i + 9..].find("-----").map(|j| i + 9 + j + 5));
                out.push_str("[REDACTED PRIVATE KEY]");
                rest = &rest[end.unwrap_or(rest.len())..];
                continue;
            }
        }
        let lower = rest.get(..7).map(str::to_ascii_lowercase);
        if lower.as_deref() == Some("bearer ") {
            let len = rest[7..]
                .find(|c: char| !token(c))
                .unwrap_or(rest.len() - 7);
            if len >= 8 {
                out.push_str("Bearer [REDACTED]");
                rest = &rest[7 + len..];
                continue;
            }
        }
        let at_boundary = out
            .chars()
            .next_back()
            .is_none_or(|c| !(c.is_ascii_alphanumeric() || c == '-' || c == '_'));
        if at_boundary {
            if let Some(prefix) = PREFIXES.iter().find(|p| rest.starts_with(**p)) {
                let len = rest.find(|c: char| !token(c)).unwrap_or(rest.len());
                if len >= prefix.len() + 12 {
                    out.push_str(prefix);
                    out.push_str("[REDACTED]");
                    rest = &rest[len..];
                    continue;
                }
            }
        }
        let c = rest.chars().next().unwrap();
        out.push(c);
        rest = &rest[c.len_utf8()..];
    }
    out
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Operation {
    Api,
    Admission,
    Command,
    Turn,
    Model,
    ModelStarted,
    ApprovalWait,
    QuestionWait,
    Tool,
    Policy,
    Environment,
    Storage,
    Worker,
    Lease,
    Recovery,
    RecoveryScan,
    ApprovalRequested,
    ApprovalResolved,
    QuestionRequested,
    QuestionResolved,
    Cancellation,
    UnknownOutcome,
}
impl Operation {
    /// Fixed hints are safe even when identifying metadata/content is off.
    pub fn gen_ai_name(self) -> Option<&'static str> {
        match self {
            Self::Model => Some("chat"),
            Self::Tool => Some("execute_tool"),
            Self::Turn => Some("invoke_agent"),
            _ => None,
        }
    }
    pub fn observation_type(self) -> &'static str {
        match self {
            Self::Model => "generation",
            Self::Turn => "agent",
            Self::Tool => "tool",
            Self::Policy => "guardrail",
            Self::ModelStarted
            | Self::ApprovalRequested
            | Self::ApprovalResolved
            | Self::QuestionRequested
            | Self::QuestionResolved
            | Self::Cancellation
            | Self::UnknownOutcome => "event",
            _ => "span",
        }
    }
    pub fn name(self) -> &'static str {
        match self {
            Self::Api => "api.request",
            Self::Admission => "command.admission",
            Self::Command => "runtime.command",
            Self::Turn => "agent.execute",
            Self::Model => "model.attempt",
            Self::ModelStarted => "model.requested",
            Self::ApprovalWait => "approval.wait",
            Self::QuestionWait => "question.wait",
            Self::Tool => "tool.execute",
            Self::Policy => "policy.review",
            Self::Environment => "environment.operation",
            Self::Storage => "storage.call",
            Self::Worker => "worker.activation",
            Self::Lease => "worker.lease",
            Self::Recovery => "session.recovery",
            Self::RecoveryScan => "session.recovery_scan",
            Self::ApprovalRequested => "approval.requested",
            Self::ApprovalResolved => "approval.resolved",
            Self::QuestionRequested => "question.requested",
            Self::QuestionResolved => "question.resolved",
            Self::Cancellation => "turn.cancellation",
            Self::UnknownOutcome => "tool.unknown_outcome",
        }
    }
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Outcome {
    Ok,
    Error,
    Interrupted,
    Waiting,
    Denied,
    Cancelled,
    Unknown,
}
impl Outcome {
    pub fn name(self) -> &'static str {
        match self {
            Self::Ok => "ok",
            Self::Error => "error",
            Self::Interrupted => "interrupted",
            Self::Waiting => "waiting",
            Self::Denied => "denied",
            Self::Cancelled => "cancelled",
            Self::Unknown => "unknown",
        }
    }
}
#[derive(Clone, Copy, Debug, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct TraceContext {
    pub trace: [u8; 16],
    pub span: [u8; 8],
}
tokio::task_local! {static CURRENT: TraceContext;}
tokio::task_local! {static CURRENT_TURN: [u8;32];}

/// Fixed-size identity hashes, not payload redaction or anonymization.
#[derive(Clone, Copy, Debug, Default)]
pub struct Identity {
    pub session: Option<[u8; 32]>,
    pub action: Option<[u8; 32]>,
}
impl Identity {
    pub fn new(session: Option<&str>, action: Option<&str>) -> Self {
        Self {
            session: session.map(|s| Sha256::digest(s.as_bytes()).into()),
            action: action.map(|s| Sha256::digest(s.as_bytes()).into()),
        }
    }
}
#[derive(Clone, Debug)]
pub struct Record {
    pub operation: Operation,
    pub outcome: Outcome,
    pub context: TraceContext,
    pub parent: Option<TraceContext>,
    pub links: Vec<TraceContext>,
    pub turn_hash: Option<[u8; 32]>,
    pub purpose: Option<ModelPurpose>,
    pub usage_details: TokenUsage,
    pub timing_known: bool,
    pub output_bytes: Option<u64>,
    pub identity: Identity,
    pub started: SystemTime,
    pub ended: SystemTime,
    pub elapsed: Duration,
    pub attempt: Option<u32>,
    pub input_tokens: Option<u64>,
    pub output_tokens: Option<u64>,
    pub first_output: Option<Duration>,
    /// Opt-in metadata/content attributes. Empty unless a [`ContentPolicy`]
    /// allows them; exported on spans only, never as metric labels or logs.
    pub attributes: Vec<(&'static str, String)>,
}
#[derive(Default)]
pub struct Stats {
    pub(crate) dropped: AtomicU64,
    pub(crate) failures: AtomicU64,
    pub(crate) accepted: AtomicU64,
}
#[derive(Clone, Copy, Debug, Default, serde::Serialize)]
pub struct Snapshot {
    pub accepted: u64,
    pub dropped: u64,
    pub export_failures: u64,
}
impl Stats {
    pub fn snapshot(&self) -> Snapshot {
        Snapshot {
            accepted: self.accepted.load(Ordering::Relaxed),
            dropped: self.dropped.load(Ordering::Relaxed),
            export_failures: self.failures.load(Ordering::Relaxed),
        }
    }
}
/// Called only on the dedicated telemetry thread. Embedders own the sink and its
/// providers. A buggy/blocking sink cannot block agent dispatch or bounded stop.
pub trait Sink: Send + 'static {
    fn export(&mut self, records: &[Record], stats: Snapshot) -> bool;
    /// Older custom sinks can ignore optional measurements. No trace/log record
    /// is manufactured from a gauge. Implement this to export numeric samples.
    fn observe(&mut self, _observations: &[Observation], _stats: Snapshot) -> bool {
        true
    }
}
enum Message {
    Record(Record),
    Observations([Observation; Gauge::ALL.len()], usize),
}
struct Inner {
    content: ContentPolicy,
    tx: mpsc::SyncSender<Message>,
    stats: Arc<Stats>,
    stop: Arc<AtomicBool>,
    deadline: Arc<OnceLock<Instant>>,
}
impl Drop for Inner {
    fn drop(&mut self) {
        self.deadline
            .get_or_init(|| Instant::now() + Duration::from_secs(5));
        self.stop.store(true, Ordering::Release);
    }
}
#[derive(Clone, Default)]
pub struct Telemetry(Option<Arc<Inner>>);
pub struct Controller {
    stop: Arc<AtomicBool>,
    done: mpsc::Receiver<()>,
    stats: Arc<Stats>,
    deadline: Arc<OnceLock<Instant>>,
}
impl Controller {
    pub fn snapshot(&self) -> Snapshot {
        self.stats.snapshot()
    }
    /// Single total caller wait budget, never a per-record wait. Worker may
    /// finish a bounded I/O operation after this returns false. No durable spool.
    pub fn shutdown(&self, budget: Duration) -> bool {
        self.deadline
            .get_or_init(|| Instant::now() + budget.min(Duration::from_secs(5)));
        self.stop.store(true, Ordering::Release);
        self.done
            .recv_timeout(budget.min(Duration::from_secs(5)))
            .is_ok()
    }
}
impl Drop for Controller {
    fn drop(&mut self) {
        self.deadline
            .get_or_init(|| Instant::now() + Duration::from_secs(5));
        self.stop.store(true, Ordering::Release);
    }
}
impl Telemetry {
    pub fn start(sink: impl Sink, capacity: usize) -> anyhow::Result<(Self, Controller)> {
        Self::start_with_content(sink, capacity, ContentPolicy::default())
    }
    /// Like [`Self::start`], attaching metadata/content per `content`.
    pub fn start_with_content(
        sink: impl Sink,
        capacity: usize,
        content: ContentPolicy,
    ) -> anyhow::Result<(Self, Controller)> {
        content.validate()?;
        anyhow::ensure!(
            (1..=2048).contains(&capacity),
            "Telemetry capacity must be 1..=2048"
        );
        let (tx, rx) = mpsc::sync_channel(capacity);
        let (done, wait) = mpsc::sync_channel(1);
        let stats = Arc::new(Stats::default());
        let stop = Arc::new(AtomicBool::new(false));
        let deadline = Arc::new(OnceLock::<Instant>::new());
        let until = deadline.clone();
        let (s, t) = (stats.clone(), stop.clone());
        std::thread::Builder::new()
            .name("ow-telemetry".into())
            .spawn(move || {
                let mut sink = sink;
                let mut batch = Vec::with_capacity(128);
                loop {
                    match rx.recv_timeout(Duration::from_millis(200)) {
                        Ok(r) => batch.push(r),
                        Err(mpsc::RecvTimeoutError::Disconnected) if batch.is_empty() => break,
                        Err(_) => {}
                    }
                    while batch.len() < 128 {
                        match rx.try_recv() {
                            Ok(r) => batch.push(r),
                            Err(_) => break,
                        }
                    }
                    if until.get().is_some_and(|d| Instant::now() >= *d) {
                        s.dropped.fetch_add(batch.len() as u64, Ordering::Relaxed);
                        while rx.try_recv().is_ok() {
                            s.dropped.fetch_add(1, Ordering::Relaxed);
                        }
                        break;
                    }
                    if !batch.is_empty() {
                        let ok = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                            let mut records = Vec::new();
                            let mut observations = Vec::new();
                            for message in batch.drain(..) {
                                match message {
                                    Message::Record(r) => records.push(r),
                                    Message::Observations(o, n) => {
                                        observations.extend_from_slice(&o[..n])
                                    }
                                }
                            }
                            let a = records.is_empty() || sink.export(&records, s.snapshot());
                            let b = observations.is_empty()
                                || sink.observe(&observations, s.snapshot());
                            a && b
                        }))
                        .unwrap_or(false);
                        if !ok {
                            s.failures.fetch_add(1, Ordering::Relaxed);
                        }
                        batch.clear();
                    }
                    if t.load(Ordering::Acquire) {
                        match rx.try_recv() {
                            Ok(r) => batch.push(r),
                            Err(_) => break,
                        }
                    }
                }
                let _ = done.send(());
            })?;
        Ok((
            Self(Some(Arc::new(Inner {
                content,
                tx,
                stats: stats.clone(),
                stop: stop.clone(),
                deadline: deadline.clone(),
            }))),
            Controller {
                stop,
                done: wait,
                stats,
                deadline,
            },
        ))
    }
    pub fn enabled(&self) -> bool {
        self.0.is_some()
    }
    pub fn snapshot(&self) -> Snapshot {
        self.0
            .as_ref()
            .map(|i| i.stats.snapshot())
            .unwrap_or_default()
    }
    pub fn span(&self, operation: Operation, session: Option<&str>, action: Option<&str>) -> Span {
        if !self.enabled() {
            return Span {
                telemetry: self.clone(),
                record: None,
                clock: Instant::now(),
                context: TraceContext {
                    trace: [0; 16],
                    span: [0; 8],
                },
            };
        }
        let parent = CURRENT.try_with(|p| *p).ok().filter(|p| p.trace != [0; 16]);
        let now = SystemTime::now();
        let context = TraceContext {
            trace: parent
                .map(|p| p.trace)
                .unwrap_or_else(|| *uuid::Uuid::new_v4().as_bytes()),
            span: uuid::Uuid::new_v4().as_bytes()[..8].try_into().unwrap(),
        };
        Span {
            telemetry: self.clone(),
            record: self.enabled().then(|| Record {
                operation,
                outcome: if operation == Operation::Tool {
                    Outcome::Unknown
                } else {
                    Outcome::Interrupted
                },
                context,
                parent,
                links: vec![],
                turn_hash: CURRENT_TURN.try_with(|t| *t).ok(),
                purpose: None,
                usage_details: TokenUsage::default(),
                timing_known: true,
                output_bytes: None,
                identity: Identity::new(session, action),
                started: now,
                ended: now,
                elapsed: Duration::ZERO,
                attempt: None,
                input_tokens: None,
                output_tokens: None,
                first_output: None,
                attributes: vec![],
            }),
            clock: Instant::now(),
            context,
        }
    }
    fn record(&self, r: Record) {
        self.enqueue(Message::Record(r));
    }
    pub fn observe(&self, gauge: Gauge, value: u64) {
        self.observe_many(&[(gauge, value)]);
    }
    /// One snapshot is accepted or dropped as a unit, so a new freshness marker
    /// cannot accompany partially dropped state. At most the fixed gauge catalog.
    pub fn observe_many(&self, values: &[(Gauge, u64)]) {
        if values.is_empty() {
            return;
        }
        assert!(
            values.len() <= Gauge::ALL.len(),
            "Observation batch exceeds fixed catalog"
        );
        let mut samples = [Observation {
            gauge: Gauge::TurnsQueued,
            value: 0,
        }; Gauge::ALL.len()];
        for (sample, &(gauge, value)) in samples.iter_mut().zip(values) {
            *sample = Observation { gauge, value };
        }
        self.enqueue(Message::Observations(samples, values.len()));
    }
    fn enqueue(&self, message: Message) {
        if let Some(inner) = &self.0 {
            if inner.stop.load(Ordering::Acquire) || inner.tx.try_send(message).is_err() {
                inner.stats.dropped.fetch_add(1, Ordering::Relaxed);
            } else {
                inner.stats.accepted.fetch_add(1, Ordering::Relaxed);
            }
        }
    }
    pub fn event(&self, op: Operation, outcome: Outcome, sid: Option<&str>, action: Option<&str>) {
        self.span(op, sid, action).finish(outcome);
    }
    /// A completed cross-process wait uses durable wall-clock endpoints. Clock
    /// skew remains unknown, never a fabricated zero-duration interval.
    pub fn interval(
        &self,
        op: Operation,
        outcome: Outcome,
        sid: &str,
        turn: &str,
        action: &str,
        started_ms: u64,
        ended_ms: u64,
    ) {
        if started_ms > i64::MAX as u64 || ended_ms > i64::MAX as u64 {
            return;
        }
        let mut span = self.span(op, Some(sid), Some(action));
        span.turn(turn);
        if let Some(mut r) = span.record.take() {
            r.started = std::time::UNIX_EPOCH + Duration::from_millis(started_ms);
            r.ended = std::time::UNIX_EPOCH + Duration::from_millis(ended_ms);
            r.timing_known = ended_ms >= started_ms;
            r.elapsed = Duration::from_millis(ended_ms.saturating_sub(started_ms));
            r.outcome = outcome;
            let mut h = Sha256::new();
            h.update(b"openworker.wait.v1\0");
            h.update(r.context.trace);
            h.update(action.as_bytes());
            h.update(op.name().as_bytes());
            r.context.span = h.finalize()[..8].try_into().unwrap();
            r.parent = None;
            self.record(r);
        }
    }
}
pub struct Span {
    telemetry: Telemetry,
    record: Option<Record>,
    clock: Instant,
    context: TraceContext,
}
impl Span {
    pub fn session(&mut self, id: &str) {
        if let Some(r) = &mut self.record {
            r.identity.session = Identity::new(Some(id), None).session;
        }
    }
    pub fn context(&self) -> TraceContext {
        self.context
    }
    pub fn attempt(&mut self, n: u32) {
        if let Some(r) = &mut self.record {
            r.attempt = Some(n);
        }
    }
    pub fn usage(&mut self, input: u64, output: u64) {
        self.reported_usage(TokenUsage {
            input: Some(input),
            output: Some(output),
            ..Default::default()
        });
    }
    pub fn reported_usage(&mut self, usage: TokenUsage) {
        if usage.validate() {
            if let Some(r) = self
                .record
                .as_mut()
                .filter(|r| r.operation == Operation::Model)
            {
                r.input_tokens = usage.input;
                r.output_tokens = usage.output;
                r.usage_details = usage;
            }
        }
    }
    pub fn purpose(&mut self, purpose: ModelPurpose) {
        if let Some(r) = &mut self.record {
            r.purpose = Some(purpose);
        }
    }
    pub fn output_bytes(&mut self, n: u64) {
        if let Some(r) = &mut self.record {
            r.output_bytes = Some(n);
        }
    }
    pub fn link(&mut self, context: TraceContext) {
        if let Some(r) = &mut self.record {
            if context.trace != [0; 16] && context.span != [0; 8] && r.links.len() < 4 {
                r.links.push(context);
            }
        }
    }
    /// Stable logical-turn trace, with a distinct span for each active segment.
    pub fn turn(&mut self, turn: &str) {
        if let Some(r) = &mut self.record {
            let hash = accounting::turn_hash(turn);
            r.turn_hash = Some(hash);
            if let Some(session) = r.identity.session {
                r.context.trace = accounting::turn_trace(session, hash);
                self.context = r.context;
                if r.parent.is_some_and(|p| p.trace != r.context.trace) {
                    r.parent = None;
                }
            }
        }
    }
    /// Immutable open-request fact, linked to its eventual completed request.
    /// Missing completion after a crash is unknown, not a successful zero-token call.
    pub fn model_started(&self) {
        if let Some(record) = &self.record {
            let mut r = record.clone();
            r.operation = Operation::ModelStarted;
            r.outcome = Outcome::Waiting;
            r.parent = Some(record.context);
            r.context.span = uuid::Uuid::new_v4().as_bytes()[..8].try_into().unwrap();
            r.ended = r.started;
            r.elapsed = Duration::ZERO;
            r.input_tokens = None;
            r.output_tokens = None;
            r.usage_details = TokenUsage::default();
            r.first_output = None;
            r.attributes
                .retain(|(key, _)| matches!(*key, "gen_ai.request.model"));
            self.telemetry.record(r);
        }
    }
    fn policy(&self) -> Option<&ContentPolicy> {
        self.record.as_ref()?;
        self.telemetry
            .0
            .as_ref()
            .map(|i| &i.content)
            .filter(|p| p.capture != Capture::Off)
    }
    /// Current capture level (`Off` when telemetry is disabled).
    pub fn capture(&self) -> Capture {
        self.policy().map_or(Capture::Off, |p| p.capture)
    }
    /// Short identifying metadata such as a model or tool name. Recorded at
    /// `Capture::Metadata` and above. Bounded to 256 bytes.
    pub fn metadata(&mut self, key: &'static str, value: &str) {
        if self.policy().is_some() && metadata_attribute(key, value) {
            let mut end = value.len().min(256);
            while !value.is_char_boundary(end) {
                end -= 1;
            }
            let value = value[..end].to_owned();
            self.push_attribute(key, value);
        }
    }
    /// Payload text, computed lazily and only at `Capture::Full`. Keeps the
    /// beginning of long values (outputs, results).
    pub fn content(&mut self, key: &'static str, value: impl FnOnce() -> String) {
        self.push_content(key, value, false);
    }
    /// Like [`Self::content`] but keeps the end of long values (conversation
    /// history, where the latest messages matter most).
    pub fn content_tail(&mut self, key: &'static str, value: impl FnOnce() -> String) {
        self.push_content(key, value, true);
    }
    fn push_content(&mut self, key: &'static str, value: impl FnOnce() -> String, tail: bool) {
        if !attributes::field(key).is_some_and(|f| f.capture == attributes::CaptureClass::Full) {
            return;
        }
        let Some(policy) = self.policy().filter(|p| p.capture == Capture::Full) else {
            return;
        };
        let text = policy.bound(&value(), tail);
        self.push_attribute(key, text);
    }
    fn push_attribute(&mut self, key: &'static str, value: String) {
        if let Some(r) = &mut self.record {
            if let Some((_, old)) = r.attributes.iter_mut().find(|(k, _)| *k == key) {
                *old = value;
            } else if r.attributes.len() < 12 {
                r.attributes.push((key, value));
            }
        }
    }
    pub fn first_output(&mut self) {
        if let Some(r) = &mut self.record {
            r.first_output.get_or_insert(self.clock.elapsed());
        }
    }
    pub fn finish(mut self, outcome: Outcome) {
        if let Some(r) = &mut self.record {
            r.outcome = outcome;
        }
    }
}
impl Drop for Span {
    fn drop(&mut self) {
        if let Some(mut r) = self.record.take() {
            r.elapsed = self.clock.elapsed();
            r.ended = r.started + r.elapsed;
            self.telemetry.record(r);
        }
    }
}
pub async fn scope<F: Future>(context: TraceContext, f: F) -> F::Output {
    CURRENT.scope(context, f).await
}
pub async fn scope_turn<F: Future>(turn: &str, context: TraceContext, f: F) -> F::Output {
    CURRENT_TURN
        .scope(accounting::turn_hash(turn), CURRENT.scope(context, f))
        .await
}
