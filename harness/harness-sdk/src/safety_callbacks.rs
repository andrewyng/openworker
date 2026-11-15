//! Private inspection callbacks. Payloads never use the session event journal.
use anyhow::{ensure, Context, Result};
use openworker_runtime::safety::{
    CheckFuture, Context as CheckContext, Decision, Handler, HandlerRef, Registry,
};
use serde_json::{json, Value};
use std::{
    collections::BTreeMap,
    sync::{Arc, Mutex},
};
use tokio::sync::{broadcast, oneshot};
use tokio_util::sync::CancellationToken;

struct Pending {
    owner: String,
    result: oneshot::Sender<Result<Decision>>,
}
pub(crate) struct Bridge {
    pub registry: Arc<Registry>,
    events: broadcast::Sender<Value>,
    owners: Mutex<BTreeMap<(String, String), String>>,
    pending: Mutex<BTreeMap<String, Pending>>,
}
impl Bridge {
    pub fn new(events: broadcast::Sender<Value>) -> Arc<Self> {
        Arc::new(Self {
            registry: Arc::new(Registry::default()),
            events,
            owners: Default::default(),
            pending: Default::default(),
        })
    }
    pub fn register(self: &Arc<Self>, p: Value) -> Result<Value> {
        ensure!(
            openworker_runtime::safety::available(),
            "Guardrails are not included in this build"
        );
        let reference: HandlerRef = serde_json::from_value(p["handler"].clone())?;
        reference.validate()?;
        let owner = openworker_runtime::runtime::text(&p, "owner", 100)?;
        let key = (reference.name.clone(), reference.version.clone());
        let mut owners = self.owners.lock().unwrap();
        if let Some(existing) = owners.get(&key) {
            ensure!(
                existing == owner,
                "Safety callback belongs to another executor"
            );
            return Ok(json!({"registered":true}));
        }
        self.registry.register(
            reference.clone(),
            Arc::new(Proxy {
                bridge: Arc::downgrade(self),
                reference,
                owner: owner.into(),
            }),
        )?;
        owners.insert(key, owner.into());
        Ok(json!({"registered":true}))
    }
    #[cfg(feature = "security-adapters")]
    pub fn register_adapter(&self, p: Value) -> Result<Value> {
        let reference: HandlerRef = serde_json::from_value(p["handler"].clone())?;
        reference.validate()?;
        let config: openworker_security_adapters::Config =
            serde_json::from_value(p["config"].clone())?;
        let owner = format!(
            "scanner:{}",
            openworker_runtime::bundle::digest(&serde_json::to_vec(&config)?)
        );
        let key = (reference.name.clone(), reference.version.clone());
        let mut owners = self.owners.lock().unwrap();
        if let Some(existing) = owners.get(&key) {
            ensure!(
                existing == &owner,
                "Handler already registered with different configuration"
            );
            return Ok(json!({"registered":true}));
        }
        let scanner = openworker_security_adapters::Scanner::from_environment(config)?;
        self.registry.register(reference, Arc::new(scanner))?;
        owners.insert(key, owner);
        Ok(json!({"registered":true}))
    }
    pub fn respond(&self, p: Value) -> Result<Value> {
        let id = openworker_runtime::runtime::text(&p, "invocation_id", 100)?;
        let owner = openworker_runtime::runtime::text(&p, "owner", 100)?;
        ensure!(
            p.get("decision").is_some() != p.get("error").is_some(),
            "Supply decision or error"
        );
        let result = if p.get("error").is_some() {
            Err(anyhow::anyhow!("Safety callback unavailable"))
        } else {
            ensure!(
                p["decision"].to_string().len() <= 132000,
                "Safety callback result too large"
            );
            let decision: Decision =
                serde_json::from_value(p["decision"].clone()).context("Invalid safety decision")?;
            decision.validate()?;
            Ok(decision)
        };
        let mut pending = self.pending.lock().unwrap();
        ensure!(
            pending.get(id).is_some_and(|v| v.owner == owner),
            "Safety invocation expired or owner mismatch"
        );
        let _ = pending.remove(id).unwrap().result.send(result);
        Ok(json!({"accepted":true}))
    }
}
struct Proxy {
    bridge: std::sync::Weak<Bridge>,
    reference: HandlerRef,
    owner: String,
}
struct InFlight {
    bridge: Arc<Bridge>,
    id: String,
    owner: String,
}
impl Drop for InFlight {
    fn drop(&mut self) {
        if self
            .bridge
            .pending
            .lock()
            .unwrap()
            .remove(&self.id)
            .is_some()
        {
            let _ = self
                .bridge
                .events
                .send(json!({"kind":"safety_cancel","owner":self.owner,"invocation_id":self.id}));
        }
    }
}
impl Handler for Proxy {
    fn call(&self, context: CheckContext, cancel: CancellationToken) -> CheckFuture<'_> {
        Box::pin(async move {
            let bridge = self
                .bridge
                .upgrade()
                .context("Safety callback host stopped")?;
            let id = openworker_runtime::id("safety");
            let (tx, rx) = oneshot::channel();
            {
                let mut pending = bridge.pending.lock().unwrap();
                ensure!(pending.len() < 64, "Safety callback capacity exceeded");
                pending.insert(
                    id.clone(),
                    Pending {
                        owner: self.owner.clone(),
                        result: tx,
                    },
                );
            }
            let _guard = InFlight {
                bridge: bridge.clone(),
                id: id.clone(),
                owner: self.owner.clone(),
            };
            bridge.events.send(json!({"kind":"safety_call","owner":self.owner,"invocation_id":id,"handler":self.reference,"context":context})).map_err(|_|anyhow::anyhow!("Safety callback unavailable"))?;
            tokio::select! {biased;_=cancel.cancelled()=>Err(anyhow::anyhow!("Safety callback cancelled")),value=rx=>value.context("Safety callback disconnected")?}
        })
    }
}

pub(crate) struct ClientSafety {
    owner: String,
    handlers: Mutex<BTreeMap<(String, String), Arc<dyn Handler>>>,
    running: Mutex<BTreeMap<String, CancellationToken>>,
    stop: CancellationToken,
}
impl ClientSafety {
    pub fn new(client: &crate::HarnessClient) -> Arc<Self> {
        let state = Arc::new(Self {
            owner: openworker_runtime::id("safety-owner"),
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
                let event = tokio::select! {_=stop.cancelled()=>break,e=events.recv()=>e,changed=connection.changed()=>{if changed.is_err(){break;}else if !matches!(*connection.borrow(),crate::ConnectionState::Ready{..}){if let Some(state)=weak.upgrade(){state.cancel_all();}}continue;}};
                let Some(state) = weak.upgrade() else {
                    break;
                };
                match event {
                    Ok(crate::Notification::Application(v)) => state.accept(client.clone(), v),
                    Err(broadcast::error::RecvError::Closed) => break,
                    Err(_) => state.cancel_all(),
                    _ => {}
                }
            }
        });
        state
    }
    fn cancel_all(&self) {
        for cancel in self.running.lock().unwrap().values() {
            cancel.cancel();
        }
    }
    pub async fn register(
        &self,
        client: &crate::HarnessClient,
        reference: HandlerRef,
        handler: Arc<dyn Handler>,
    ) -> crate::Result<()> {
        if client.info()["capabilities"]["guardrails"] != true {
            return Err(crate::Error::Setup(
                "This harness build does not support guardrails".into(),
            ));
        }
        reference
            .validate()
            .map_err(|_| crate::Error::InvalidInput("Invalid safety handler identity".into()))?;
        let key = (reference.name.clone(), reference.version.clone());
        {
            let mut handlers = self.handlers.lock().unwrap();
            if handlers.contains_key(&key) {
                return Err(crate::Error::InvalidInput(
                    "Safety handler already registered; use a new version".into(),
                ));
            }
            handlers.insert(key.clone(), handler);
        }
        let wire = json!({"handler":reference,"owner":self.owner});
        if let Err(e) = client.request("safety/register", wire.clone()).await {
            self.handlers.lock().unwrap().remove(&key);
            return Err(e);
        }
        let mut binding = wire;
        binding["_method"] = json!("safety/register");
        client.inner.registrations.lock().unwrap().push(binding);
        Ok(())
    }
    fn accept(self: &Arc<Self>, client: std::sync::Weak<crate::Handle>, v: Value) {
        if v["owner"] != self.owner {
            return;
        }
        let Some(id) = v["invocation_id"].as_str().map(str::to_owned) else {
            return;
        };
        if v["kind"] == "safety_cancel" {
            if let Some(cancel) = self.running.lock().unwrap().get(&id) {
                cancel.cancel();
            }
            return;
        }
        if v["kind"] != "safety_call" {
            return;
        }
        let reference = serde_json::from_value::<HandlerRef>(v["handler"].clone()).ok();
        let handler = reference.and_then(|r| {
            self.handlers
                .lock()
                .unwrap()
                .get(&(r.name, r.version))
                .cloned()
        });
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
                let context: CheckContext = serde_json::from_value(v["context"].clone()).ok()?;
                let handler = handler?;
                std::panic::AssertUnwindSafe(async { handler.call(context, cancel.clone()).await })
                    .catch_unwind()
                    .await
                    .ok()?
                    .ok()
            };
            let result = tokio::select! {_=cancel.cancelled()=>None,result=tokio::time::timeout(std::time::Duration::from_secs(120),work)=>result.ok().flatten()};
            if !cancel.is_cancelled() {
                let mut p = json!({"owner":state.owner,"invocation_id":id});
                if let Some(d) = result {
                    p["decision"] = json!(d);
                } else {
                    p["error"] = json!(true);
                }
                let _ = client.callback_message("safety/result", p).await;
            }
            state.running.lock().unwrap().remove(&id);
        });
    }
    pub fn stop(&self) {
        self.stop.cancel();
    }
}

#[cfg(all(test, feature = "guardrails"))]
mod tests {
    use super::*;
    fn context() -> CheckContext {
        CheckContext {
            call_id: None,
            evaluation_id: "eval-1".into(),
            session_id: "s_one".into(),
            turn_id: Some("turn-1".into()),
            phase: openworker_runtime::safety::Phase::UserInput,
            text: "PRIVATE".into(),
            tool: None,
            arguments: None,
            matches: vec![],
            context: json!({}),
        }
    }
    #[tokio::test]
    async fn owner_bound_callbacks_reject_forgery_late_and_cancelled_responses() {
        let (events, mut rx) = broadcast::channel(16);
        let bridge = Bridge::new(events);
        let reference = HandlerRef {
            name: "check".into(),
            version: "1".into(),
        };
        bridge
            .register(json!({"owner":"app-one","handler":reference}))
            .unwrap();
        assert!(bridge
            .register(json!({"owner":"app-two","handler":reference}))
            .is_err());
        let handler = bridge.registry.handler(&reference).unwrap();
        let task =
            tokio::spawn(async move { handler.call(context(), CancellationToken::new()).await });
        let event = rx.recv().await.unwrap();
        let id = event["invocation_id"].clone();
        assert!(bridge
            .respond(json!({"owner":"app-two","invocation_id":id,"decision":{"action":"allow"}}))
            .is_err());
        let decision = serde_json::to_value(Decision::Allow).unwrap();
        bridge
            .respond(json!({"owner":"app-one","invocation_id":id,"decision":decision}))
            .unwrap();
        assert!(matches!(task.await.unwrap().unwrap(), Decision::Allow));
        assert!(bridge
            .respond(json!({"owner":"app-one","invocation_id":id,"decision":decision}))
            .is_err());
        let handler = bridge.registry.handler(&reference).unwrap();
        let cancel = CancellationToken::new();
        let c = cancel.clone();
        let task = tokio::spawn(async move { handler.call(context(), c).await });
        let event = rx.recv().await.unwrap();
        cancel.cancel();
        assert!(task.await.unwrap().is_err());
        assert_eq!(rx.recv().await.unwrap()["kind"], "safety_cancel");
        assert!(bridge.respond(json!({"owner":"app-one","invocation_id":event["invocation_id"],"decision":decision})).is_err());
        assert!(bridge.pending.lock().unwrap().is_empty());
    }
    #[tokio::test]
    async fn callback_capacity_fails_closed_without_more_events() {
        let (events, mut rx) = broadcast::channel(128);
        let bridge = Bridge::new(events);
        let reference = HandlerRef {
            name: "check".into(),
            version: "1".into(),
        };
        bridge
            .register(json!({"owner":"app","handler":reference}))
            .unwrap();
        let mut calls = vec![];
        for _ in 0..64 {
            let handler = bridge.registry.handler(&reference).unwrap();
            calls.push(tokio::spawn(async move {
                handler.call(context(), CancellationToken::new()).await
            }));
            rx.recv().await.unwrap();
        }
        assert!(bridge
            .registry
            .handler(&reference)
            .unwrap()
            .call(context(), CancellationToken::new())
            .await
            .is_err());
        assert!(rx.try_recv().is_err());
        for call in calls {
            call.abort();
            let _ = call.await;
        }
        assert!(bridge.pending.lock().unwrap().is_empty());
    }
}
