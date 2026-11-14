//! Optional, acknowledged evaluation records. Never sent through the lossy trace queue.
use anyhow::{ensure, Result};
pub const DEFAULT_MAX_PENDING: u32 = 10_000;
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Target {
    pub session_hash: String,
    pub turn_hash: String,
    pub trace_id: Option<String>,
    pub span_id: Option<String>,
}
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Evaluator {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub model: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub usage: Option<crate::Usage>,
    pub name: String,
    pub version: String,
    pub source: Source,
}
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Source {
    Human,
    Code,
    Model,
}
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(
    tag = "type",
    content = "value",
    rename_all = "snake_case",
    deny_unknown_fields
)]
pub enum Score {
    Numeric(f64),
    Boolean(bool),
    Categorical(String),
    Text(String),
}
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Status {
    Completed,
    Skipped,
    Error,
}
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Input {
    pub id: String,
    pub name: String,
    pub evaluator: Evaluator,
    pub status: Status,
    pub score: Option<Score>,
    pub created_ms: u64,
    pub explanation: Option<String>,
    pub dataset_case: Option<String>,
    pub experiment: Option<String>,
    /// Corrections append a new ID, preserving the original evaluation.
    pub supersedes: Option<String>,
}
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Evaluation {
    pub version: u8,
    pub deployment: String,
    pub target: Target,
    pub result: Input,
}
impl Evaluation {
    pub fn validate(&self) -> Result<()> {
        ensure!(
            self.version == 1 && crate::identifier(&self.deployment),
            "Unsupported evaluation schema or deployment"
        );
        ensure!(
            crate::hex_id(&self.target.session_hash, 64)
                && crate::hex_id(&self.target.turn_hash, 64),
            "Invalid evaluation scope"
        );
        ensure!(
            self.target
                .trace_id
                .as_ref()
                .is_none_or(|v| crate::hex_id(v, 32))
                && self
                    .target
                    .span_id
                    .as_ref()
                    .is_none_or(|v| crate::hex_id(v, 16))
                && (self.target.span_id.is_none() || self.target.trace_id.is_some()),
            "Invalid evaluation trace target"
        );
        for id in [
            &self.result.id,
            &self.result.name,
            &self.result.evaluator.name,
            &self.result.evaluator.version,
        ]
        .into_iter()
        .chain(self.result.dataset_case.iter())
        .chain(self.result.experiment.iter())
        .chain(self.result.supersedes.iter())
        {
            ensure!(crate::label(id), "Invalid evaluation identifier");
        }
        ensure!(
            self.result
                .evaluator
                .model
                .as_ref()
                .is_none_or(|m| crate::label(m)),
            "Invalid evaluator model"
        );
        ensure!(
            (self.result.evaluator.model.is_none() && self.result.evaluator.usage.is_none())
                || self.result.evaluator.source == Source::Model,
            "Only model evaluators carry model accounting"
        );
        if let Some(u) = &self.result.evaluator.usage {
            ensure!(
                openworker_telemetry::TokenUsage {
                    input: u.input,
                    output: u.output,
                    cache_read: u.cache_read,
                    cache_write: u.cache_write,
                    reasoning: u.reasoning
                }
                .validate(),
                "Invalid evaluator usage"
            );
        }
        ensure!(
            self.result.created_ms > 0 && self.result.created_ms <= 253402300799000,
            "Invalid evaluation timestamp"
        );
        ensure!(
            (self.result.status == Status::Completed) == self.result.score.is_some(),
            "Only completed evaluations carry a score"
        );
        ensure!(
            self.result.supersedes.as_ref() != Some(&self.result.id),
            "An evaluation cannot supersede itself"
        );
        if let Some(score) = &self.result.score {
            match score {
                Score::Numeric(n) => ensure!(n.is_finite(), "Numeric score must be finite"),
                Score::Categorical(s) => {
                    ensure!(!s.is_empty() && s.len() <= 256, "Invalid category")
                }
                Score::Text(s) => ensure!(s.len() <= 4000, "Score text exceeds limit"),
                Score::Boolean(_) => {}
            }
        }
        ensure!(
            self.result
                .explanation
                .as_ref()
                .is_none_or(|s| s.len() <= 4000),
            "Evaluation explanation exceeds limit"
        );
        ensure!(
            serde_json::to_vec(self)?.len() <= 16384,
            "Evaluation exceeds record limit"
        );
        Ok(())
    }
    /// Best-effort secret redaction precedes durable storage and export.
    pub fn redacted(mut self) -> Self {
        if let Some(text) = &mut self.result.explanation {
            *text = openworker_telemetry::redact(text);
        }
        if let Some(Score::Text(text) | Score::Categorical(text)) = &mut self.result.score {
            *text = openworker_telemetry::redact(text);
        }
        self
    }
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Row {
    pub cursor: u64,
    pub evaluation: Evaluation,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Page {
    pub results: Vec<Row>,
    pub next: Option<u64>,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Query {
    pub deployment: String,
    pub session_hash: String,
    pub turn_hash: String,
    pub after: u64,
    pub limit: u16,
}
impl Query {
    pub fn validate(&self) -> Result<()> {
        ensure!(
            crate::identifier(&self.deployment)
                && crate::hex_id(&self.session_hash, 64)
                && crate::hex_id(&self.turn_hash, 64)
                && self.after <= i64::MAX as u64
                && (1..=50).contains(&self.limit),
            "Invalid evaluation query"
        );
        Ok(())
    }
}
pub(crate) fn page(mut results: Vec<Row>, q: &Query) -> Result<Page> {
    for row in &results {
        row.evaluation.validate()?;
        ensure!(
            row.cursor > q.after
                && row.evaluation.deployment == q.deployment
                && row.evaluation.target.session_hash == q.session_hash
                && row.evaluation.target.turn_hash == q.turn_hash,
            "Evaluation scope mismatch"
        );
    }
    let more = results.len() > usize::from(q.limit);
    results.truncate(q.limit.into());
    let next = more.then(|| results.last().unwrap().cursor);
    Ok(Page { results, next })
}

#[cfg(test)]
mod tests {
    use super::*;
    pub fn sample() -> Evaluation {
        Evaluation {
            version: 1,
            deployment: "sdk".into(),
            target: Target {
                session_hash: format!("{:064x}", 1),
                turn_hash: format!("{:064x}", 2),
                trace_id: None,
                span_id: None,
            },
            result: Input {
                id: "e1".into(),
                name: "correctness".into(),
                evaluator: Evaluator {
                    name: "fixture".into(),
                    version: "1".into(),
                    source: Source::Code,
                    model: None,
                    usage: None,
                },
                status: Status::Completed,
                score: Some(Score::Boolean(true)),
                created_ms: 1,
                explanation: None,
                dataset_case: None,
                experiment: None,
                supersedes: None,
            },
        }
    }
    #[test]
    fn status_and_target_cannot_misrepresent_results() {
        let mut e = sample();
        assert!(e.validate().is_ok());
        e.result.status = Status::Error;
        assert!(e.validate().is_err());
        e.result.score = None;
        assert!(e.validate().is_ok());
        e.target.span_id = Some(format!("{:016x}", 1));
        assert!(e.validate().is_err());
    }
}

/// Reuses the compliance outbox lease/backoff contract; only evaluation delivery
/// is retried. No runtime action is addressed by this checkpoint.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Delivery {
    pub token: String,
    pub row: Row,
}
pub fn delivery_key(deployment: &str, sink: &str, binding: &str, token: &str) -> Result<()> {
    ensure!(
        crate::identifier(deployment)
            && crate::identifier(sink)
            && crate::hex_id(binding, 64)
            && crate::identifier(token),
        "Invalid evaluation delivery scope"
    );
    Ok(())
}
