//! Loopback worker readiness. No prompts, identities or diagnostics on the wire.
use axum::{http::StatusCode, routing::get, Router};
use openworker_store_postgres::worker::Drain;
use std::{
    sync::{Arc, Mutex},
    time::{Duration, Instant},
};

#[derive(Clone)]
pub struct Readiness {
    last: Arc<Mutex<Option<Instant>>>,
    drain: Drain,
}
impl Readiness {
    pub fn new(drain: Drain) -> Self {
        Self {
            last: Arc::new(Mutex::new(None)),
            drain,
        }
    }
    pub fn checked(&self) {
        *self.last.lock().unwrap() = Some(Instant::now());
    }
    pub fn clear(&self) {
        *self.last.lock().unwrap() = None;
    }
    pub fn ready(&self) -> bool {
        !self.drain.is_requested()
            && self
                .last
                .lock()
                .unwrap()
                .is_some_and(|t| t.elapsed() < Duration::from_secs(20))
    }
    pub fn router(&self) -> Router {
        let state = self.clone();
        Router::new().route(
            "/readyz",
            get(move || {
                let state = state.clone();
                async move {
                    if state.ready() {
                        StatusCode::NO_CONTENT
                    } else {
                        StatusCode::SERVICE_UNAVAILABLE
                    }
                }
            }),
        )
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn starts_unready_stales_and_drain_immediately_removes_readiness() {
        let drain = Drain::default();
        let state = Readiness::new(drain.clone());
        assert!(!state.ready());
        state.checked();
        assert!(state.ready());
        *state.last.lock().unwrap() = Some(Instant::now() - Duration::from_secs(21));
        assert!(!state.ready());
        state.checked();
        state.clear();
        assert!(!state.ready());
        state.checked();
        drain.request();
        assert!(!state.ready());
    }
}
