//! Metadata evidence contract shared by local and service deployments. Event
//! bodies are projected through an allowlist; this is not a transcript archive.
use anyhow::{ensure, Result};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default, rename_all = "camelCase", deny_unknown_fields)]
pub struct Config {
    pub retention_days: u16,
    pub max_pending_events: u32,
}
impl Default for Config {
    fn default() -> Self {
        Self {
            retention_days: 30,
            max_pending_events: 10_000,
        }
    }
}
impl Config {
    pub fn validate(&self) -> Result<()> {
        ensure!(
            (100..=1_000_000).contains(&self.max_pending_events),
            "Compliance backlog limit must be 100..1000000 events"
        );
        ensure!(
            (1..=3650).contains(&self.retention_days),
            "Compliance retention must be 1..3650 days"
        );
        Ok(())
    }
}

#[derive(Clone, Debug, Serialize, Deserialize, Default)]
#[serde(default, rename_all = "camelCase", deny_unknown_fields)]
pub struct Query {
    pub after: i64,
    pub limit: u16,
    pub kind: Option<String>,
    pub outcome: Option<String>,
}
impl Query {
    pub fn validate(&self) -> Result<()> {
        ensure!(
            self.after >= 0 && self.limit <= 500,
            "Invalid compliance page bounds"
        );
        for v in [&self.kind, &self.outcome].into_iter().flatten() {
            ensure!(identifier(v), "Invalid compliance filter");
        }
        Ok(())
    }
    pub fn limit(&self) -> usize {
        if self.limit == 0 {
            100
        } else {
            usize::from(self.limit)
        }
    }
}

pub fn identifier(s: &str) -> bool {
    !s.is_empty()
        && s.len() <= 160
        && s.bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"._:-/".contains(&b))
}
/// High-volume content deltas are not separate compliance actions. Their model
/// attempt starts/completions and policy decisions remain evidence.
pub fn include(kind: &str) -> bool {
    !matches!(
        kind,
        "assistant_delta" | "tool_progress" | "agent_run_state"
    ) && identifier(kind)
}
pub fn project(session: &Value, kind: &str, data: &Value) -> Value {
    let mut facts = serde_json::Map::new();
    for key in [
        "call_id",
        "callId",
        "tool",
        "interaction_id",
        "attempt_id",
        "submission_id",
        "evaluationId",
        "check",
        "version",
        "outcome",
        "phase",
        "purpose",
        "code",
        "status",
        "name",
        "origin",
        "approval_origin",
        "grant_id",
        "policy_digest",
        "resource_policy_digest",
        "bundle_digest",
    ] {
        if let Some(value) = data[key].as_str().filter(|v| identifier(v)) {
            facts.insert(key.into(), json!(value));
        }
    }
    for key in [
        "executed",
        "outputBlocked",
        "result_held",
        "guarded",
        "automatic_replay",
    ] {
        if let Some(v) = data[key].as_bool() {
            facts.insert(key.into(), json!(v));
        }
    }
    for key in [
        "durationMs",
        "matchCount",
        "retry",
        "max_retries",
        "attempt",
        "output_tokens",
        "reserved_output_tokens",
        "reported_output_tokens",
    ] {
        if let Some(v) = data[key].as_u64() {
            facts.insert(key.into(), json!(v));
        }
    }
    json!({"schemaVersion":1,"kind":kind,"agent":session["coworker"],"team":session["agent_team"]["team_id"],"instance":session["agent_team"]["agent_id"],"facts":facts})
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn projection_never_copies_candidate_payloads_or_callback_errors() {
        let data = json!({"call_id":"call-1","outcome":"blocked","code":"PII","durationMs":12,"text":"SECRET","arguments":{"key":"SECRET"},"result":"SECRET","feedbackToModel":"SECRET","error":"SECRET"});
        let v = project(&json!({"coworker":"guide"}), "safety_evaluation", &data);
        assert!(!v.to_string().contains("SECRET"));
        assert_eq!(v["facts"]["code"], "PII");
        assert!(!include("assistant_delta"));
        assert!(include("tool_finished"));
    }
    #[test]
    fn limits_are_bounded() {
        assert!(Config {
            retention_days: 0,
            ..Default::default()
        }
        .validate()
        .is_err());
        assert!(Query {
            after: -1,
            ..Default::default()
        }
        .validate()
        .is_err());
        assert_eq!(Query::default().limit(), 100);
    }
}

pub mod delivery;
