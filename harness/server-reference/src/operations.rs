//! Opt-in observer on its own bounded connection. Never gates agent work.
use openworker_store_postgres::{Config, PostgresStore};
use openworker_telemetry::{Gauge, Telemetry};
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use tokio_util::sync::CancellationToken;
#[derive(Clone, serde::Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct Options {
    pub sample_interval_seconds: u64,
    pub stalled_after_seconds: u64,
    pub recent_window_seconds: u64,
}
impl Default for Options {
    fn default() -> Self {
        Self {
            sample_interval_seconds: 10,
            stalled_after_seconds: 300,
            recent_window_seconds: 300,
        }
    }
}
impl Options {
    pub fn validate(&self) -> anyhow::Result<()> {
        anyhow::ensure!(
            (1..=60).contains(&self.sample_interval_seconds)
                && (1..=86400).contains(&self.stalled_after_seconds)
                && (1..=86400).contains(&self.recent_window_seconds),
            "Invalid operational observation bounds"
        );
        Ok(())
    }
}
pub async fn run(
    mut config: Config,
    options: Options,
    telemetry: Telemetry,
    stop: CancellationToken,
) {
    // Do not instrument our own sampling query as storage activity/errors.
    config.telemetry = Telemetry::default();
    let mut store = PostgresStore::open(config).ok();
    let mut tick = tokio::time::interval(Duration::from_secs(options.sample_interval_seconds));
    tick.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
    loop {
        tokio::select! {biased;_=stop.cancelled()=>break,_=tick.tick()=>{}}
        telemetry.observe(
            Gauge::SamplerHeartbeat,
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap_or_default()
                .as_secs(),
        );
        let result = store.as_ref().and_then(|s| {
            s.operational_snapshot(options.stalled_after_seconds, options.recent_window_seconds)
                .ok()
        });
        if let Some(s) = result {
            telemetry.observe_many(&[
                (Gauge::SessionsActive, s.active),
                (Gauge::TurnsQueued, s.queued),
                (Gauge::ApprovalsPending, s.approvals),
                (Gauge::QuestionsPending, s.questions),
                (Gauge::OldestQueuedSeconds, s.oldest_queued_seconds),
                (Gauge::SessionsStalled, s.stalled),
                (Gauge::UnknownRecent, s.unknown_recent),
                (Gauge::StateObservedAt, s.observed_at),
                (Gauge::DatabaseHealthy, 1),
            ]);
        } else {
            telemetry.observe(Gauge::DatabaseHealthy, 0);
            // A poisoned connection is not reopened in a loop. Inspection stays
            // unhealthy/stale until operator restart, without blocking the API.
            store = None;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn defaults_and_bounds_are_explicit() {
        let o: Options = serde_json::from_str("{}").unwrap();
        assert_eq!(o.sample_interval_seconds, 10);
        assert_eq!(o.stalled_after_seconds, 300);
        assert_eq!(o.recent_window_seconds, 300);
        o.validate().unwrap();
        assert!(Options {
            sample_interval_seconds: 0,
            ..o.clone()
        }
        .validate()
        .is_err());
        assert!(Options {
            stalled_after_seconds: 86401,
            ..o.clone()
        }
        .validate()
        .is_err());
        assert!(Options {
            recent_window_seconds: 0,
            ..o
        }
        .validate()
        .is_err());
        assert!(serde_json::from_str::<Options>(r#"{"prompt":"private"}"#).is_err());
    }
}
