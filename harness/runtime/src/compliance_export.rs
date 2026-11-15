//! Opt-in SIEM delivery, separate from agent work and observer callbacks.
use anyhow::{ensure, Result};
use serde::{Deserialize, Serialize};
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Sink {
    pub name: String,
    pub endpoint: String,
    pub credential_env: String,
    #[serde(default)]
    pub allow_loopback_http: bool,
}
impl Sink {
    pub fn validate(&self) -> Result<()> {
        ensure!(
            crate::compliance::identifier(&self.name) && self.name.len() <= 80,
            "Invalid SIEM sink name"
        );
        ensure!(
            !self.credential_env.is_empty()
                && self.credential_env.len() <= 128
                && self
                    .credential_env
                    .bytes()
                    .all(|c| c.is_ascii_alphanumeric() || c == b'_'),
            "Invalid SIEM credential reference"
        );
        let url = reqwest::Url::parse(&self.endpoint)
            .map_err(|_| anyhow::anyhow!("Invalid SIEM endpoint"))?;
        ensure!(
            url.username().is_empty()
                && url.password().is_none()
                && url.query().is_none()
                && url.fragment().is_none(),
            "Invalid SIEM endpoint"
        );
        let local = url
            .host_str()
            .is_some_and(|v| matches!(v, "127.0.0.1" | "[::1]"));
        ensure!(
            url.scheme() == "https"
                || (self.allow_loopback_http && local && url.scheme() == "http"),
            "SIEM requires HTTPS (or explicit loopback fixture)"
        );
        Ok(())
    }
    pub fn binding(&self) -> Result<String> {
        self.validate()?;
        Ok(crate::bundle::digest(&serde_json::to_vec(self)?))
    }
}
#[cfg(feature = "compliance-export")]
pub fn start(
    store: crate::store::SharedStore,
    sinks: Vec<Sink>,
    stop: tokio_util::sync::CancellationToken,
) -> Result<()> {
    use serde_json::json;
    use std::time::Duration;
    ensure!(sinks.len() <= 16, "Too many SIEM sinks");
    let mut names = std::collections::BTreeSet::new();
    for sink in &sinks {
        sink.validate()?;
        ensure!(names.insert(&sink.name), "Duplicate SIEM sink name");
    }
    if sinks.is_empty() {
        return Ok(());
    }
    let client = reqwest::Client::builder()
        .redirect(reqwest::redirect::Policy::none())
        .no_proxy()
        .timeout(Duration::from_secs(20))
        .build()?;
    tokio::spawn(async move {
        let mut after = String::new();
        loop {
            if stop.is_cancelled() {
                break;
            }
            let sessions = store
                .lock()
                .unwrap()
                .compliance_sessions(&after)
                .unwrap_or_default();
            if sessions.len() < 100 {
                after.clear()
            } else if let Some(s) = sessions.last() {
                after = s.id.clone()
            }
            for s in sessions.into_iter().filter(|s| s.compliance.is_some()) {
                for sink in &sinks {
                    let token = crate::id("export");
                    let binding = sink.binding().expect("validated sink");
                    let batch = store
                        .lock()
                        .unwrap()
                        .claim_compliance_export(&s.id, &sink.name, &binding, &token);
                    let Ok(Some(batch)) = batch else { continue };
                    let secret = std::env::var(&sink.credential_env)
                        .ok()
                        .filter(|k| !k.is_empty() && k.len() <= 8192)
                        .map(zeroize::Zeroizing::new);
                    let success = if let Some(secret) = secret {
                        // Stable per-event IDs survive batches, retries and worker
                        // takeover. Receiver acknowledgement is only HTTP success;
                        // a lost acknowledgement retries delivery, never execution.
                        let request=client.post(&sink.endpoint).bearer_auth(secret.as_str()).json(&json!({"schemaVersion":1,"sink":sink.name,"sessionId":s.id,"activities":batch.activities})).send();
                        tokio::select! {_=stop.cancelled()=>return,result=request=>result.is_ok_and(|r|r.status().is_success())}
                    } else {
                        false
                    };
                    let _ = store
                        .lock()
                        .unwrap()
                        .settle_compliance_export(&s.id, &sink.name, &token, success);
                }
            }
            tokio::select! {_=stop.cancelled()=>break,_=tokio::time::sleep(Duration::from_secs(1))=>{}}
        }
    });
    Ok(())
}
#[cfg(not(feature = "compliance-export"))]
pub fn start(
    _store: crate::store::SharedStore,
    sinks: Vec<Sink>,
    _stop: tokio_util::sync::CancellationToken,
) -> Result<()> {
    ensure!(
        sinks.is_empty(),
        "Compliance exporter not included in this build"
    );
    Ok(())
}
