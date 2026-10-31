//! Durable working-agent generation limits. Input tokens are not output tokens.
//! One tool-free summary call is reserved in addition to the work-call allowance;
//! its output reserve is included in max_output_tokens. Failed requests with no
//! trustworthy usage retain their reserved output charge instead of assuming zero.
use anyhow::{ensure, Result};
use serde::{Deserialize, Serialize};
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Limits {
    pub max_model_calls: u32,
    pub max_output_tokens: u64,
    #[serde(default = "summary_tokens")]
    pub summary_tokens: u32,
}
fn summary_tokens() -> u32 {
    512
}
impl Default for Limits {
    fn default() -> Self {
        Self {
            max_model_calls: 100,
            max_output_tokens: 256000,
            summary_tokens: 512,
        }
    }
}
impl Limits {
    pub fn validate(&self) -> Result<()> {
        ensure!(
            (1..=10000).contains(&self.max_model_calls),
            "maxModelCalls must be 1..10000"
        );
        ensure!(
            (128..=2048).contains(&self.summary_tokens)
                && self.max_output_tokens >= u64::from(self.summary_tokens) + 128
                && self.max_output_tokens <= 100_000_000,
            "Invalid output/summary token limits"
        );
        Ok(())
    }
}
#[derive(Clone, Debug, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct State {
    pub limits: Limits,
    pub requested: Option<Limits>,
    pub model_calls: u32,
    pub charged_output_tokens: u64,
    pub reported_output_tokens: u64,
    pub summary_started: bool,
    pub stop_reason: Option<String>,
    pub validation_errors: u32,
    pub consecutive_validation_errors: u32,
}
impl State {
    pub fn new(defaults: Option<&Limits>, requested: Option<Limits>) -> Result<Self> {
        let limits = requested.as_ref().or(defaults).cloned().unwrap_or_default();
        limits.validate()?;
        Ok(Self {
            limits,
            requested,
            ..Default::default()
        })
    }
    pub fn reason(&self) -> Option<&'static str> {
        if self.model_calls >= self.limits.max_model_calls {
            Some("model_call_limit")
        } else if self.charged_output_tokens
            >= self.limits.max_output_tokens - u64::from(self.limits.summary_tokens)
        {
            Some("output_token_limit")
        } else {
            None
        }
    }
    pub fn reserve(&mut self, summary: bool) -> Result<u32> {
        self.reserve_up_to(summary, 8192)
    }
    pub fn reserve_up_to(&mut self, summary: bool, cap: u32) -> Result<u32> {
        let remaining = self
            .limits
            .max_output_tokens
            .saturating_sub(self.charged_output_tokens);
        let amount = if summary {
            ensure!(!self.summary_started, "Summary call already dispatched");
            self.summary_started = true;
            remaining.min(u64::from(self.limits.summary_tokens))
        } else {
            ensure!(self.reason().is_none(), "Turn generation budget exhausted");
            self.model_calls += 1;
            remaining
                .saturating_sub(u64::from(self.limits.summary_tokens))
                .min(u64::from(cap))
        };
        ensure!(amount > 0, "No output tokens remain");
        self.charged_output_tokens = self.charged_output_tokens.saturating_add(amount);
        Ok(amount as u32)
    }
    pub fn settle(&mut self, reserved: u32, reported: Option<u64>) {
        if let Some(n) = reported {
            self.charged_output_tokens = self
                .charged_output_tokens
                .saturating_sub(u64::from(reserved))
                .saturating_add(n);
            self.reported_output_tokens = self.reported_output_tokens.saturating_add(n);
        }
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn reserve_summary_inside_total_and_charge_unknown() {
        let mut s = State::new(
            None,
            Some(Limits {
                max_model_calls: 3,
                max_output_tokens: 1024,
                summary_tokens: 256,
            }),
        )
        .unwrap();
        let n = s.reserve(false).unwrap();
        assert_eq!(n, 768);
        s.settle(n, None);
        assert_eq!(s.reason(), Some("output_token_limit"));
        assert_eq!(s.reserve(true).unwrap(), 256);
        assert_eq!(s.charged_output_tokens, 1024);
        assert!(s.reserve(true).is_err());
    }
    #[test]
    fn override_step_stop_and_persist() {
        let mut s = State::new(
            Some(&Limits::default()),
            Some(Limits {
                max_model_calls: 1,
                ..Default::default()
            }),
        )
        .unwrap();
        let n = s.reserve(false).unwrap();
        s.settle(n, Some(20));
        assert_eq!(s.reason(), Some("model_call_limit"));
        assert!(s.reserve(false).is_err());
        let mut copy: State = serde_json::from_value(serde_json::to_value(&s).unwrap()).unwrap();
        assert_eq!(copy.model_calls, 1);
        assert_eq!(copy.charged_output_tokens, 20);
        assert_eq!(copy.reserve(true).unwrap(), 512);
    }
}
