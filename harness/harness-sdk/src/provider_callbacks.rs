//! Private credential callbacks use the control lane, never the tool/event journal.
use anyhow::{ensure, Context, Result};
use openworker_runtime::provider_auth::{AccessToken, Reason, Refresh};
use serde_json::{json, Value};
use std::{
    collections::BTreeMap,
    sync::{Arc, Mutex},
};
use tokio::sync::{broadcast, oneshot};

struct Pending {
    owner: String,
    result: oneshot::Sender<Result<AccessToken>>,
}
pub(crate) struct Bridge {
    events: broadcast::Sender<Value>,
    pending: Mutex<BTreeMap<String, Pending>>,
    owners: Mutex<BTreeMap<String, String>>,
}
impl Bridge {
    pub fn new(events: broadcast::Sender<Value>) -> Arc<Self> {
        Arc::new(Self {
            events,
            pending: Default::default(),
            owners: Default::default(),
        })
    }
    pub fn register(
        self: &Arc<Self>,
        manager: &openworker_runtime::connections::Manager,
        p: Value,
    ) -> Result<Value> {
        let name = openworker_runtime::runtime::text(&p, "name", 64)?;
        let owner = openworker_runtime::runtime::text(&p, "owner", 100)?;
        let enabled = p["refresh"].as_bool().context("Missing refresh option")?;
        let mut owners = self.owners.lock().unwrap();
        ensure!(
            owners.get(name).is_none_or(|s| s == owner),
            "Provider callback already bound"
        );
        let bridge = self.clone();
        let provider = name.to_owned();
        let executor = owner.to_owned();
        let refresh: Option<Refresh> = enabled.then(|| {
            Arc::new(move |reason| {
                let bridge = bridge.clone();
                let name = provider.clone();
                let owner = executor.clone();
                Box::pin(async move { bridge.call(name, owner, reason).await })
                    as openworker_runtime::provider_auth::RefreshFuture
            }) as Refresh
        });
        manager.register_access_auth(name, refresh)?;
        owners.insert(name.into(), owner.into());
        Ok(json!({"registered":true}))
    }
    async fn call(
        self: Arc<Self>,
        name: String,
        owner: String,
        reason: Reason,
    ) -> Result<AccessToken> {
        let id = openworker_runtime::id("auth");
        let (tx, rx) = oneshot::channel();
        {
            let mut pending = self.pending.lock().unwrap();
            ensure!(pending.len() < 64, "Provider callback capacity exceeded");
            pending.insert(
                id.clone(),
                Pending {
                    owner: owner.clone(),
                    result: tx,
                },
            );
        }
        struct Guard {
            bridge: Arc<Bridge>,
            id: String,
            owner: String,
        }
        impl Drop for Guard {
            fn drop(&mut self) {
                if self
                    .bridge
                    .pending
                    .lock()
                    .unwrap()
                    .remove(&self.id)
                    .is_some()
                {
                    let _ = self.bridge.events.send(json!({"kind":"provider_auth_cancel","owner":self.owner,"invocation_id":self.id}));
                }
            }
        }
        let _guard = Guard {
            bridge: self.clone(),
            id: id.clone(),
            owner: owner.clone(),
        };
        self.events.send(json!({"kind":"provider_auth","owner":owner,"invocation_id":id,"provider":name,"reason":reason})).map_err(|_|anyhow::anyhow!("Provider authentication unavailable"))?;
        rx.await
            .map_err(|_| anyhow::anyhow!("Provider authentication unavailable"))?
    }
    pub fn respond(&self, p: Value) -> Result<Value> {
        let id = p["invocation_id"].as_str().context("Missing invocation")?;
        let owner = p["owner"].as_str().context("Missing owner")?;
        let mut pending = self.pending.lock().unwrap();
        ensure!(
            pending.get(id).is_some_and(|v| v.owner == owner),
            "Provider callback expired or owner mismatch"
        );
        let result = if p.get("error").is_some() {
            Err(anyhow::anyhow!("Provider authentication required"))
        } else {
            parse_token(&p)
        };
        let _ = pending.remove(id).unwrap().result.send(result);
        Ok(json!({"accepted":true}))
    }
}
pub(crate) fn parse_token(p: &Value) -> Result<AccessToken> {
    AccessToken::new(
        p["access_token"].as_str().context("Missing access token")?,
        p["expires_at"].as_u64().context("Invalid expiry")?,
    )
}

pub(crate) struct ClientAuth {
    owner: String,
    handlers: Mutex<BTreeMap<String, Refresh>>,
    running: Mutex<BTreeMap<String, tokio_util::sync::CancellationToken>>,
    stop: tokio_util::sync::CancellationToken,
}
impl ClientAuth {
    pub fn new(client: &crate::HarnessClient) -> Arc<Self> {
        let state = Arc::new(Self {
            owner: openworker_runtime::id("auth-owner"),
            handlers: Default::default(),
            running: Default::default(),
            stop: Default::default(),
        });
        let mut events = client.subscribe();
        let mut connection = client.inner.state.clone();
        let weak = Arc::downgrade(&state);
        let client = Arc::downgrade(&client.inner);
        let stop = state.stop.clone();
        tokio::spawn(async move {
            loop {
                let event = tokio::select! {_=stop.cancelled()=>break, event=events.recv()=>event,
                    changed=connection.changed()=>{
                        if changed.is_err(){break;}
                        if !matches!(*connection.borrow(),crate::ConnectionState::Ready{..}) {
                            if let Some(state)=weak.upgrade(){for cancel in state.running.lock().unwrap().values(){cancel.cancel();}}
                        }
                        continue;
                    }
                };
                let Some(state) = weak.upgrade() else { break };
                match event {
                    Ok(crate::Notification::Application(v)) => state.accept(client.clone(), v),
                    Err(broadcast::error::RecvError::Closed) => break,
                    Err(_) => {
                        for cancel in state.running.lock().unwrap().values() {
                            cancel.cancel();
                        }
                    }
                    _ => {}
                }
            }
        });
        state
    }
    pub async fn register(
        &self,
        client: &crate::HarnessClient,
        name: String,
        refresh: Option<Refresh>,
    ) -> crate::Result<()> {
        if client.info()["capabilities"]["provider_access_tokens"] != true {
            return Err(crate::Error::Setup(
                "Update the harness for access-token authentication".into(),
            ));
        }
        let wire = json!({"name":name,"owner":self.owner,"refresh":refresh.is_some()});
        if let Some(refresh) = refresh {
            self.handlers.lock().unwrap().insert(name, refresh);
        }
        client
            .request("provider/auth/register", wire.clone())
            .await?;
        let mut bindings = client.inner.registrations.lock().unwrap();
        bindings
            .retain(|v| !(v["_method"] == "provider/auth/register" && v["name"] == wire["name"]));
        let mut binding = wire;
        binding["_method"] = json!("provider/auth/register");
        bindings.push(binding);
        Ok(())
    }
    fn accept(self: &Arc<Self>, client: std::sync::Weak<crate::Handle>, v: Value) {
        if v["owner"] != self.owner {
            return;
        }
        let Some(id) = v["invocation_id"].as_str().map(str::to_owned) else {
            return;
        };
        if v["kind"] == "provider_auth_cancel" {
            if let Some(c) = self.running.lock().unwrap().get(&id) {
                c.cancel();
            }
            return;
        }
        if v["kind"] != "provider_auth" {
            return;
        }
        let handler = v["provider"]
            .as_str()
            .and_then(|s| self.handlers.lock().unwrap().get(s).cloned());
        let Some(client) = client.upgrade().map(|inner| crate::HarnessClient { inner }) else {
            return;
        };
        let cancel = self.stop.child_token();
        {
            let mut running = self.running.lock().unwrap();
            if running.len() >= 64 || running.contains_key(&id) {
                return;
            }
            running.insert(id.clone(), cancel.clone());
        }
        let state = self.clone();
        tokio::spawn(async move {
            use futures_util::FutureExt;
            let work = async {
                let reason: Reason = serde_json::from_value(v["reason"].clone()).ok()?;
                let handler = handler?;
                std::panic::AssertUnwindSafe(async { handler(reason).await })
                    .catch_unwind()
                    .await
                    .ok()?
                    .ok()
            };
            let token = tokio::select! {_=cancel.cancelled()=>None, result=tokio::time::timeout(std::time::Duration::from_secs(30),work)=>result.ok().flatten()};
            if !cancel.is_cancelled() {
                let mut p = json!({"owner":state.owner,"invocation_id":id});
                if let Some(t) = token {
                    p["access_token"] = json!(t.token.as_str());
                    p["expires_at"] = json!(t.expires_at);
                } else {
                    p["error"] = json!(true);
                }
                let _ = client.callback_message("provider/auth/result", p).await;
            }
            state.running.lock().unwrap().remove(&id);
        });
    }
    pub fn stop(&self) {
        self.stop.cancel();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[tokio::test]
    async fn callback_results_require_live_invocation_and_matching_owner() {
        let root = tempfile::tempdir().unwrap();
        let manager = openworker_runtime::connections::Manager::open(root.path()).unwrap();
        manager.set_provider(json!({"name":"ExampleApp","api":"chat-completions","base_url":"http://127.0.0.1:12345/v1","access_token_auth":true})).unwrap();
        let (events, mut rx) = broadcast::channel(8);
        let bridge = Bridge::new(events);
        bridge
            .register(
                &manager,
                json!({"name":"ExampleApp","owner":"owner-one","refresh":true}),
            )
            .unwrap();
        assert!(bridge
            .register(
                &manager,
                json!({"name":"ExampleApp","owner":"owner-two","refresh":true})
            )
            .is_err());
        let selected = manager
            .resolve(&openworker_runtime::connections::Choice::new(
                "ExampleApp",
                "fixture",
            ))
            .unwrap();
        let tokens = manager.access_tokens(&selected).unwrap().unwrap();
        let task = tokio::spawn(async move { tokens.get(None).await });
        let request = rx.recv().await.unwrap();
        let mut reply = json!({"owner":"wrong-owner","invocation_id":request["invocation_id"],"access_token":"synthetic-secret","expires_at":openworker_runtime::provider_auth::now()+900});
        assert!(bridge.respond(reply.clone()).is_err());
        reply["owner"] = json!("owner-one");
        bridge.respond(reply.clone()).unwrap();
        assert_eq!(
            task.await.unwrap().unwrap().key.as_str(),
            "synthetic-secret"
        );
        assert!(bridge.respond(reply).is_err());
    }
}
