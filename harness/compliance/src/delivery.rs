//! Durable per-session/sink checkpoint. Retrying delivery can duplicate an event,
//! never an agent turn or tool action. Receivers deduplicate stable activity IDs.
use anyhow::{ensure, Result};
use serde::{Deserialize, Serialize};
use serde_json::Value;
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Checkpoint {
    pub binding: String,
    pub cursor: i64,
    pub failures: u32,
    pub next_at_ms: u64,
    pub last_success_ms: Option<u64>,
    pub lease: Option<Lease>,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Lease {
    pub token: String,
    pub until_ms: u64,
    pub through: i64,
}
impl Checkpoint {
    pub fn new(binding: String) -> Self {
        Self {
            binding,
            cursor: 0,
            failures: 0,
            next_at_ms: 0,
            last_success_ms: None,
            lease: None,
        }
    }
    pub fn claim(&mut self, binding: &str, token: &str, now: u64, through: i64) -> Result<bool> {
        ensure!(
            self.binding == binding,
            "Export destination binding changed; register a new sink version"
        );
        ensure!(
            super::identifier(token) && through >= self.cursor,
            "Invalid export claim"
        );
        if now < self.next_at_ms
            || self.lease.as_ref().is_some_and(|l| l.until_ms > now)
            || through == self.cursor
        {
            return Ok(false);
        }
        self.lease = Some(Lease {
            token: token.into(),
            until_ms: now.saturating_add(60_000),
            through,
        });
        Ok(true)
    }
    pub fn settle(&mut self, token: &str, now: u64, success: bool) -> Result<()> {
        let lease = self
            .lease
            .as_ref()
            .ok_or_else(|| anyhow::anyhow!("Export lease missing"))?;
        ensure!(
            lease.token == token && lease.until_ms >= now,
            "Stale export acknowledgement"
        );
        if success {
            self.cursor = lease.through;
            self.failures = 0;
            self.next_at_ms = 0;
            self.last_success_ms = Some(now)
        } else {
            self.failures = self.failures.saturating_add(1);
            self.next_at_ms = now.saturating_add((1000u64 << self.failures.min(8)).min(300_000));
        }
        self.lease = None;
        Ok(())
    }
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Batch {
    pub token: String,
    pub session_id: String,
    pub sink: String,
    pub activities: Vec<Value>,
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn outage_restart_and_stale_ack_never_advance_cursor() {
        let mut c = Checkpoint::new("binding".into());
        assert!(c.claim("binding", "one", 100, 10).unwrap());
        assert!(!c.claim("binding", "two", 101, 10).unwrap());
        assert!(c.settle("forged", 102, true).is_err());
        c.settle("one", 102, false).unwrap();
        assert_eq!(c.cursor, 0);
        assert!(!c.claim("binding", "two", 103, 10).unwrap());
        let mut restored: Checkpoint =
            serde_json::from_value(serde_json::to_value(c).unwrap()).unwrap();
        assert!(restored.claim("binding", "two", 10000, 10).unwrap());
        assert!(restored.claim("binding", "three", 80000, 12).unwrap());
        assert!(restored.settle("two", 80001, true).is_err());
        restored.settle("three", 80001, true).unwrap();
        assert_eq!(restored.cursor, 12);
        assert!(restored.claim("other", "four", 80002, 13).is_err());
    }
}
