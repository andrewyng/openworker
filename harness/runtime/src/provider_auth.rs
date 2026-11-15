//! Short-lived access tokens. No tokens or callback results enter durable journals.
use anyhow::{bail, ensure, Result};
use serde::{Deserialize, Serialize};
use std::{
    future::Future,
    pin::Pin,
    sync::{Arc, Mutex},
    time::{Duration, SystemTime, UNIX_EPOCH},
};
use zeroize::Zeroizing;

pub fn now() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs()
}
#[derive(Clone, Copy, Debug, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Reason {
    Missing,
    Expiring,
    Rejected,
}
/// An opaque bearer token; deliberately no Debug/Serialize implementation.
pub struct AccessToken {
    pub token: Zeroizing<String>,
    pub expires_at: u64,
}
impl AccessToken {
    pub fn new(token: impl Into<String>, expires_at: u64) -> Result<Self> {
        let token = Zeroizing::new(token.into());
        ensure!(
            (8..=16384).contains(&token.len()) && token.bytes().all(|b| (33..=126).contains(&b)),
            "Invalid access token"
        );
        ensure!(expires_at > now(), "Access token has expired");
        Ok(Self { token, expires_at })
    }
}
pub type RefreshFuture = Pin<Box<dyn Future<Output = Result<AccessToken>> + Send>>;
pub type Refresh = Arc<dyn Fn(Reason) -> RefreshFuture + Send + Sync>;
#[derive(Default)]
struct State {
    token: Option<AccessToken>,
    revision: u64,
    rejected: Option<u64>,
    refresh: Option<Refresh>,
    failed_until: u64,
}
#[derive(Default)]
pub struct Tokens {
    state: Mutex<State>,
    refresh_gate: tokio::sync::Mutex<()>,
    changed: tokio::sync::Notify,
}
pub struct Snapshot {
    pub key: Zeroizing<String>,
    pub revision: u64,
}
impl Tokens {
    pub fn bind(&self, refresh: Option<Refresh>) {
        let mut s = self.state.lock().unwrap();
        s.refresh = refresh;
        s.failed_until = 0;
    }
    pub fn ready(&self) -> bool {
        let s = self.state.lock().unwrap();
        s.refresh.is_some()
            || s.token
                .as_ref()
                .is_some_and(|t| t.expires_at > now() && s.rejected != Some(s.revision))
    }
    pub fn update(&self, token: AccessToken) {
        let mut s = self.state.lock().unwrap();
        s.token = Some(token);
        s.revision += 1;
        s.rejected = None;
        s.failed_until = 0;
        self.changed.notify_waiters();
    }
    pub async fn get(&self, rejected: Option<u64>) -> Result<Snapshot> {
        if let Some(revision) = rejected {
            let mut s = self.state.lock().unwrap();
            if s.revision == revision {
                s.rejected = Some(revision);
            }
        }
        let _gate = self.refresh_gate.lock().await;
        let notified = self.changed.notified();
        tokio::pin!(notified);
        notified.as_mut().enable();
        let (refresh, reason, revision) = {
            let s = self.state.lock().unwrap();
            if let Some(t) = &s.token {
                if s.rejected != Some(s.revision)
                    && t.expires_at > now() + if s.refresh.is_some() { 60 } else { 0 }
                {
                    return Ok(Snapshot {
                        key: Zeroizing::new(t.token.to_string()),
                        revision: s.revision,
                    });
                }
            }
            ensure!(s.failed_until <= now(), "Provider authentication required");
            let refresh = s
                .refresh
                .clone()
                .ok_or_else(|| anyhow::anyhow!("Provider authentication required"))?;
            let reason = if s.rejected == Some(s.revision) {
                Reason::Rejected
            } else if s.token.is_none() {
                Reason::Missing
            } else {
                Reason::Expiring
            };
            (refresh, reason, s.revision)
        };
        // A proactive replacement wins over a slow callback, even if that callback fails.
        let result = tokio::select! {
            _=notified=>None,
            result=tokio::time::timeout(Duration::from_secs(30), async { refresh(reason).await })=>Some(result),
        };
        let mut s = self.state.lock().unwrap();
        if s.revision == revision {
            match result {
                Some(Ok(Ok(token))) if token.expires_at > now() + 60 => {
                    s.token = Some(token);
                    s.revision += 1;
                    s.rejected = None;
                }
                _ => {
                    s.failed_until = now() + 5;
                    bail!("Provider authentication required");
                }
            }
        }
        let t = s
            .token
            .as_ref()
            .ok_or_else(|| anyhow::anyhow!("Provider authentication required"))?;
        ensure!(
            t.expires_at > now() && s.rejected != Some(s.revision),
            "Provider authentication required"
        );
        Ok(Snapshot {
            key: Zeroizing::new(t.token.to_string()),
            revision: s.revision,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicUsize, Ordering};
    fn token(value: &str) -> AccessToken {
        AccessToken::new(value, now() + 900).unwrap()
    }
    #[tokio::test]
    async fn persisted_auth_mode_never_falls_back_to_anonymous_after_restart() {
        use crate::connections::{Choice, Manager};
        let tmp = tempfile::tempdir().unwrap();
        let config = serde_json::json!({"name":"ExampleApp","api":"chat-completions","base_url":"http://127.0.0.1:12345/v1","access_token_auth":true});
        let manager = Manager::open(tmp.path()).unwrap();
        manager.set_provider(config.clone()).unwrap();
        assert!(manager
            .resolve(&Choice::new("ExampleApp", "fixture"))
            .is_err());
        manager.register_access_auth("ExampleApp", None).unwrap();
        manager
            .update_access_token("ExampleApp", token("before-restart-token"))
            .unwrap();
        let selection = manager
            .resolve(&Choice::new("ExampleApp", "fixture"))
            .unwrap();
        let identity = selection.identity.clone();
        drop(manager);
        let manager = Manager::open(tmp.path()).unwrap();
        manager.set_provider(config).unwrap();
        assert!(!manager.credentials_ready(&selection));
        assert!(manager
            .access_tokens(&selection)
            .unwrap()
            .unwrap()
            .get(None)
            .await
            .is_err());
        manager
            .update_access_token("ExampleApp", token("after-restart-token"))
            .unwrap();
        assert_eq!(
            manager
                .resolve(&Choice::new("ExampleApp", "fixture"))
                .unwrap()
                .identity,
            identity
        );
        assert!(manager.set_provider(serde_json::json!({"name":"ExampleApp","api":"chat-completions","base_url":"http://127.0.0.1:12346/v1"})).is_err());
    }
    #[tokio::test]
    async fn single_flight_initial_and_rejected_refresh() {
        let tokens = Arc::new(Tokens::default());
        let calls = Arc::new(AtomicUsize::new(0));
        let count = calls.clone();
        tokens.bind(Some(Arc::new(move |_| {
            let count = count.clone();
            Box::pin(async move {
                let n = count.fetch_add(1, Ordering::SeqCst);
                tokio::time::sleep(Duration::from_millis(20)).await;
                Ok(token(&format!("synthetic-token-{n}")))
            })
        })));
        for rejected in [None, Some(1)] {
            let mut jobs = tokio::task::JoinSet::new();
            for _ in 0..8 {
                let t = tokens.clone();
                jobs.spawn(async move { t.get(rejected).await.unwrap().revision });
            }
            while let Some(result) = jobs.join_next().await {
                assert_eq!(result.unwrap(), if rejected.is_some() { 2 } else { 1 });
            }
        }
        assert_eq!(calls.load(Ordering::SeqCst), 2);
    }
    #[tokio::test]
    async fn proactive_rotation_wins_over_pending_refresh() {
        let tokens = Arc::new(Tokens::default());
        let started = Arc::new(tokio::sync::Notify::new());
        let notified = started.notified();
        let start = started.clone();
        tokens.bind(Some(Arc::new(move |_| {
            start.notify_one();
            Box::pin(std::future::pending())
        })));
        let t = tokens.clone();
        let job = tokio::spawn(async move { t.get(None).await.unwrap() });
        notified.await;
        tokens.update(token("proactive-token"));
        let value = tokio::time::timeout(Duration::from_secs(1), job)
            .await
            .unwrap()
            .unwrap();
        assert_eq!(value.key.as_str(), "proactive-token");
    }
    #[tokio::test]
    async fn expiry_and_refresh_failure_fail_closed_without_secret_diagnostics() {
        let tokens = Tokens::default();
        tokens.update(AccessToken {
            token: Zeroizing::new("expired-secret".into()),
            expires_at: now() - 1,
        });
        assert!(tokens.get(None).await.is_err());
        let calls = Arc::new(AtomicUsize::new(0));
        let count = calls.clone();
        tokens.bind(Some(Arc::new(move |_| {
            count.fetch_add(1, Ordering::SeqCst);
            Box::pin(async { bail!("private-refresh-secret") })
        })));
        for _ in 0..3 {
            let error = tokens.get(None).await.err().unwrap();
            assert_eq!(error.to_string(), "Provider authentication required");
        }
        assert_eq!(calls.load(Ordering::SeqCst), 1);
        tokens.update(token("new-proactive-token"));
        assert!(tokens.get(None).await.is_ok());
    }
    #[tokio::test]
    async fn refreshes_before_expiry_and_rejects_too_short_callback_token() {
        let tokens = Tokens::default();
        tokens.update(AccessToken::new("nearly-expired", now() + 30).unwrap());
        tokens.bind(Some(Arc::new(|reason| {
            assert!(matches!(reason, Reason::Expiring));
            Box::pin(async { Ok(token("refreshed-token")) })
        })));
        assert_eq!(
            tokens.get(None).await.unwrap().key.as_str(),
            "refreshed-token"
        );
        let tokens = Tokens::default();
        tokens.bind(Some(Arc::new(|_| {
            Box::pin(async { AccessToken::new("too-short-lived", now() + 10) })
        })));
        assert!(tokens.get(None).await.is_err());
    }
}
