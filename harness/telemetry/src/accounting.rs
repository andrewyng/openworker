//! Provider-reported counters and logical turn correlation, independent of any
//! exporter or recording database. Never include prompt or credential content.
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ModelPurpose {
    Main,
    AutoApprover,
    Compaction,
    Helper,
    Title,
    Probe,
}
impl ModelPurpose {
    pub fn name(self) -> &'static str {
        match self {
            Self::Main => "main",
            Self::AutoApprover => "auto_approver",
            Self::Compaction => "compaction",
            Self::Helper => "helper",
            Self::Title => "title",
            Self::Probe => "probe",
        }
    }
}
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TokenUsage {
    pub input: Option<u64>,
    pub output: Option<u64>,
    pub cache_read: Option<u64>,
    pub cache_write: Option<u64>,
    pub reasoning: Option<u64>,
}
impl TokenUsage {
    pub fn validate(&self) -> bool {
        [
            self.input,
            self.output,
            self.cache_read,
            self.cache_write,
            self.reasoning,
        ]
        .into_iter()
        .flatten()
        .all(|n| n <= i64::MAX as u64)
            && self.input.zip(self.cache_read).is_none_or(|(t, n)| n <= t)
            && self.input.zip(self.cache_write).is_none_or(|(t, n)| n <= t)
            && self.output.zip(self.reasoning).is_none_or(|(t, n)| n <= t)
            && match (self.input, self.cache_read, self.cache_write) {
                (Some(t), Some(a), Some(b)) => a.checked_add(b).is_some_and(|n| n <= t),
                _ => true,
            }
    }
    pub fn present(&self) -> bool {
        self.input.is_some()
            || self.output.is_some()
            || self.cache_read.is_some()
            || self.cache_write.is_some()
            || self.reasoning.is_some()
    }
}
pub(crate) fn turn_hash(turn: &str) -> [u8; 32] {
    Sha256::digest(turn.as_bytes()).into()
}
pub(crate) fn turn_trace(session: [u8; 32], turn: [u8; 32]) -> [u8; 16] {
    let mut h = Sha256::new();
    h.update(b"openworker.logical-turn.v1\0");
    h.update(session);
    h.update(turn);
    h.finalize()[..16].try_into().unwrap()
}
