//! Metadata-only diagnostic recordings. Separate from execution state and audit.
//! Committed rows survive restart; the bounded input queue is not a durable outbox.
use anyhow::{ensure, Result};
use openworker_telemetry::Record;
use serde::{Deserialize, Serialize};
use std::time::UNIX_EPOCH;
#[cfg(feature = "postgres")]
pub mod postgres;
pub mod query;
pub mod sqlite;
pub mod summary;
mod writer;
pub use writer::{Controller, Recorder, RecordingSink, Snapshot, Statistics};

pub const VERSION: u8 = 2;
pub const MAX_RECORD_BYTES: usize = 8192;

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Usage {
    pub input: Option<u64>,
    pub output: Option<u64>,
    pub cache_read: Option<u64>,
    pub cache_write: Option<u64>,
    pub reasoning: Option<u64>,
}

/// Versioned recording with pseudonymous session/turn/action correlation.
/// A logical turn can contain multiple linked active segments across workers.
/// Arbitrary attributes/content deliberately have no field in this contract.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Step {
    pub version: u8,
    pub deployment: String,
    pub role: String,
    pub trace_id: String,
    pub span_id: String,
    pub parent_trace_id: Option<String>,
    pub parent_span_id: Option<String>,
    pub session_hash: Option<String>,
    pub action_hash: Option<String>,
    #[serde(default)]
    pub turn_hash: Option<String>,
    #[serde(default)]
    pub links: Vec<StepLink>,
    pub operation: String,
    pub outcome: String,
    pub started_ms: u64,
    pub ended_ms: u64,
    pub duration_us: Option<u64>,
    pub first_output_us: Option<u64>,
    pub attempt: Option<u32>,
    pub model: Option<String>,
    pub tool: Option<String>,
    pub purpose: Option<String>,
    pub usage: Option<Usage>,
    #[serde(default)]
    pub output_bytes: Option<u64>,
    #[serde(default)]
    pub sandbox: Option<String>,
    #[serde(default)]
    pub enforcement: Option<String>,
    #[serde(default)]
    pub policy_digest: Option<String>,
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct StepLink {
    pub trace_id: String,
    pub span_id: String,
}
fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}
fn hex_id(value: &str, size: usize) -> bool {
    value.len() == size
        && value
            .bytes()
            .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase())
        && value.bytes().any(|b| b != b'0')
}
pub(crate) fn identifier(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 64
        && value
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'_' | b'-'))
}
fn label(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 128
        && !value.starts_with('/')
        && !value.contains("://")
        && value
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'_' | b'-' | b'.' | b'/' | b':'))
        && openworker_telemetry::redact(value) == value
}
impl Step {
    pub fn from_record(deployment: &str, role: &str, r: &Record) -> Result<Self> {
        let attribute = |name| {
            r.attributes
                .iter()
                .find(|(key, _)| *key == name)
                .map(|(_, value)| value)
                .filter(|v| label(v))
                .cloned()
        };
        let usage =
            (r.input_tokens.is_some() || r.output_tokens.is_some() || r.usage_details.present())
                .then_some(Usage {
                    input: r.input_tokens,
                    output: r.output_tokens,
                    cache_read: r.usage_details.cache_read,
                    cache_write: r.usage_details.cache_write,
                    reasoning: r.usage_details.reasoning,
                });
        let s = Self {
            version: VERSION,
            deployment: deployment.into(),
            role: role.into(),
            trace_id: hex(&r.context.trace),
            span_id: hex(&r.context.span),
            parent_trace_id: r.parent.map(|p| hex(&p.trace)),
            parent_span_id: r.parent.map(|p| hex(&p.span)),
            session_hash: r.identity.session.map(|s| hex(&s)),
            action_hash: r.identity.action.map(|s| hex(&s)),
            turn_hash: r.turn_hash.map(|s| hex(&s)),
            links: r
                .links
                .iter()
                .map(|c| StepLink {
                    trace_id: hex(&c.trace),
                    span_id: hex(&c.span),
                })
                .collect(),
            operation: r.operation.name().into(),
            outcome: r.outcome.name().into(),
            started_ms: u64::try_from(r.started.duration_since(UNIX_EPOCH)?.as_millis())?,
            ended_ms: u64::try_from(r.ended.duration_since(UNIX_EPOCH)?.as_millis())?,
            duration_us: r
                .timing_known
                .then(|| u64::try_from(r.elapsed.as_micros()))
                .transpose()?,
            first_output_us: r
                .first_output
                .map(|d| u64::try_from(d.as_micros()))
                .transpose()?,
            attempt: r.attempt,
            model: attribute("gen_ai.request.model"),
            tool: attribute("gen_ai.tool.name"),
            purpose: r.purpose.map(|p| p.name().to_owned()),
            usage,
            output_bytes: r.output_bytes,
            sandbox: attribute("openworker.sandbox.provider"),
            enforcement: attribute("openworker.sandbox.enforcement"),
            policy_digest: attribute("openworker.policy.digest").filter(|s| hex_id(s, 64)),
        };
        s.validate()?;
        Ok(s)
    }
    pub fn validate(&self) -> Result<()> {
        ensure!(
            (1..=VERSION).contains(&self.version),
            "Unsupported observation version"
        );
        ensure!(
            identifier(&self.deployment) && identifier(&self.role),
            "Invalid observation scope"
        );
        ensure!(
            hex_id(&self.trace_id, 32) && hex_id(&self.span_id, 16),
            "Invalid observation identity"
        );
        ensure!(
            self.parent_trace_id.is_some() == self.parent_span_id.is_some(),
            "Incomplete parent context"
        );
        ensure!(
            self.parent_trace_id.as_ref().is_none_or(|v| hex_id(v, 32))
                && self.parent_span_id.as_ref().is_none_or(|v| hex_id(v, 16)),
            "Invalid observation parent"
        );
        ensure!(
            self.parent_trace_id.as_ref() != Some(&self.trace_id)
                || self.parent_span_id.as_ref() != Some(&self.span_id),
            "Observation cannot parent itself"
        );
        ensure!(
            self.session_hash.as_ref().is_none_or(|v| hex_id(v, 64))
                && self.action_hash.as_ref().is_none_or(|v| hex_id(v, 64))
                && self.turn_hash.as_ref().is_none_or(|v| hex_id(v, 64)),
            "Invalid correlation hash"
        );
        ensure!(
            matches!(
                self.operation.as_str(),
                "api.request"
                    | "command.admission"
                    | "runtime.command"
                    | "agent.execute"
                    | "model.attempt"
                    | "model.requested"
                    | "approval.wait"
                    | "question.wait"
                    | "tool.execute"
                    | "policy.review"
                    | "environment.operation"
                    | "storage.call"
                    | "worker.activation"
                    | "worker.lease"
                    | "session.recovery"
                    | "session.recovery_scan"
                    | "approval.requested"
                    | "approval.resolved"
                    | "question.requested"
                    | "question.resolved"
                    | "turn.cancellation"
                    | "tool.unknown_outcome"
            ),
            "Invalid observation operation"
        );
        ensure!(
            matches!(
                self.outcome.as_str(),
                "ok" | "error" | "interrupted" | "denied" | "waiting" | "cancelled" | "unknown"
            ),
            "Invalid observation outcome"
        );
        ensure!(
            (self.duration_us.is_none() || self.ended_ms >= self.started_ms)
                && self.ended_ms <= i64::MAX as u64
                && self.started_ms <= i64::MAX as u64
                && self.duration_us.is_none_or(|n| n <= i64::MAX as u64),
            "Invalid observation timing"
        );
        ensure!(
            self.first_output_us
                .is_none_or(|v| self.duration_us.is_some_and(|d| v <= d)),
            "First output exceeds request duration"
        );
        ensure!(
            self.attempt.is_none_or(|v| (1..=10000).contains(&v)),
            "Invalid observation attempt"
        );
        ensure!(
            self.model.as_ref().is_none_or(|v| label(v))
                && self.tool.as_ref().is_none_or(|v| label(v)),
            "Invalid observation label"
        );
        ensure!(
            self.purpose.as_ref().is_none_or(|v| matches!(
                v.as_str(),
                "main" | "auto_approver" | "compaction" | "helper" | "title" | "probe"
            )),
            "Invalid model purpose"
        );
        ensure!(
            self.operation == "model.attempt"
                || (self.usage.is_none()
                    && (self.operation == "model.requested" || self.purpose.is_none())),
            "Usage belongs to model requests"
        );
        ensure!(
            self.links.len() <= 4
                && self
                    .links
                    .iter()
                    .all(|l| hex_id(&l.trace_id, 32) && hex_id(&l.span_id, 16)),
            "Invalid observation links"
        );
        ensure!(
            self.output_bytes.is_none_or(|n| n <= i64::MAX as u64)
                && self.sandbox.as_ref().is_none_or(|s| label(s))
                && self.enforcement.as_ref().is_none_or(|s| label(s))
                && self.policy_digest.as_ref().is_none_or(|s| hex_id(s, 64)),
            "Invalid tool facts"
        );
        if let Some(u) = &self.usage {
            ensure!(
                [u.input, u.output, u.cache_read, u.cache_write, u.reasoning]
                    .into_iter()
                    .flatten()
                    .all(|v| v <= i64::MAX as u64),
                "Observation usage out of range"
            );
            ensure!(
                u.cache_read.zip(u.input).is_none_or(|(a, b)| a <= b)
                    && u.cache_write.zip(u.input).is_none_or(|(a, b)| a <= b)
                    && u.reasoning.zip(u.output).is_none_or(|(a, b)| a <= b),
                "Usage breakdown exceeds total"
            );
            if let (Some(a), Some(b), Some(total)) = (u.cache_read, u.cache_write, u.input) {
                ensure!(
                    a.checked_add(b).is_some_and(|v| v <= total),
                    "Cache breakdown exceeds input"
                );
            }
        }
        ensure!(
            serde_json::to_vec(self)?.len() <= MAX_RECORD_BYTES,
            "Observation too large"
        );
        Ok(())
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Write {
    Inserted,
    Duplicate,
}
#[derive(Debug)]
pub struct Conflict;
impl std::fmt::Display for Conflict {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("Observation identity conflict")
    }
}
impl std::error::Error for Conflict {}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct StoredStep {
    pub cursor: u64,
    pub step: Step,
}

/// Trusted host interface only. A service must authorize the canonical session
/// before querying; the observation database is never an authorization authority.
pub trait Store: Send + 'static {
    /// Read-only bounded session/turn projection; no authorization authority.
    fn timeline(&mut self, _query: &query::Query) -> Result<query::Page> {
        anyhow::bail!("Recording queries unsupported by this adapter")
    }
    fn put(&mut self, step: &Step) -> Result<Write>;
    fn get(&mut self, deployment: &str, span_id: &str) -> Result<Option<Step>>;
    fn list(
        &mut self,
        deployment: &str,
        session_hash: Option<&str>,
        after: u64,
        limit: u16,
    ) -> Result<Vec<StoredStep>>;
    fn prune(&mut self, deployment: &str, ended_before_ms: u64, limit: u16) -> Result<u64>;
}
pub(crate) fn query(deployment: &str, session: Option<&str>, after: u64, limit: u16) -> Result<()> {
    ensure!(
        identifier(deployment)
            && session.is_none_or(|s| hex_id(s, 64))
            && after <= i64::MAX as u64
            && (1..=1000).contains(&limit),
        "Invalid observation query"
    );
    Ok(())
}
