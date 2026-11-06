//! Private application invocation bridge. Descriptors persist; executors do not.
use anyhow::{ensure, Context, Result};
use openworker_tools::{Tool, ToolContext, ToolDescriptor, ToolError, ToolFuture, ToolOutput};
use serde_json::{json, Value};
use std::{
    collections::BTreeMap,
    sync::{Arc, Mutex},
};
use tokio::sync::{broadcast, oneshot};

struct Pending {
    owner: String,
    context: ToolContext,
    result: oneshot::Sender<std::result::Result<ToolOutput, ToolError>>,
}
pub(crate) struct Bridge {
    pub events: broadcast::Sender<Value>,
    pending: Mutex<BTreeMap<String, Pending>>,
    owners: Mutex<BTreeMap<String, String>>,
    available: tokio::sync::Notify,
}
impl Bridge {
    pub fn new() -> Arc<Self> {
        let (events, _) = broadcast::channel(128);
        Arc::new(Self {
            events,
            pending: Default::default(),
            owners: Default::default(),
            available: Default::default(),
        })
    }
    pub fn bind(&self, name: &str, owner: &str) {
        self.owners
            .lock()
            .unwrap()
            .insert(name.into(), owner.into());
        self.available.notify_waiters();
    }
    pub fn check_owner(&self, name: &str, owner: &str) -> Result<()> {
        ensure!(
            self.owners
                .lock()
                .unwrap()
                .get(name)
                .is_none_or(|old| old == owner),
            "Application tool is bound to another executor"
        );
        Ok(())
    }
    pub fn proxy(self: &Arc<Self>, descriptor: ToolDescriptor) -> impl Tool + use<> {
        Proxy {
            bridge: self.clone(),
            descriptor,
        }
    }
    pub async fn respond(&self, value: Value, progress: bool) -> Result<Value> {
        let id = value["invocation_id"]
            .as_str()
            .context("Missing invocation identity")?;
        let owner = value["owner"].as_str().context("Missing callback owner")?;
        if progress {
            let context = {
                let pending = self.pending.lock().unwrap();
                let item = pending.get(id).context("Invocation expired")?;
                ensure!(item.owner == owner, "Callback owner mismatch");
                item.context.clone()
            };
            context
                .progress(
                    value["message"].as_str().context("Missing progress text")?,
                    value["data"].clone(),
                )
                .await
                .map_err(|_| anyhow::anyhow!("Callback progress rejected"))?;
        } else {
            ensure!(
                value.get("output").is_some() != value.get("error").is_some(),
                "Supply output or error"
            );
            let result = if value.get("output").is_some() {
                Ok(callback_output(&value)?)
            } else {
                // Error contents are app-provided. Do not forward arbitrary stack traces/secrets.
                Err(ToolError::new(
                    "outcome_unknown",
                    "Application tool did not confirm its outcome; inspect the app before retrying",
                ))
            };
            let mut pending = self.pending.lock().unwrap();
            let item = pending
                .get(id)
                .context("Invocation expired or already completed")?;
            ensure!(item.owner == owner, "Callback owner mismatch");
            let item = pending.remove(id).unwrap();
            let _ = item.result.send(result);
        }
        Ok(json!({"accepted":true}))
    }
}
fn callback_output(value: &Value) -> Result<ToolOutput> {
    let output = value.get("output").context("Missing callback output")?;
    ensure!(
        output.to_string().len() <= 128000,
        "Callback result exceeds limit"
    );
    let images: Vec<openworker_tools::ToolImage> = match value.get("images") {
        Some(images) => {
            serde_json::from_value(images.clone()).context("Invalid callback images")?
        }
        None => vec![],
    };
    ensure!(
        images.len() <= 1
            && images.iter().all(|image| image.base64.len() <= 768_000
                && matches!(image.mime_type.as_str(), "image/png" | "image/jpeg")),
        "Callback image exceeds limits"
    );
    // Runtime validates bytes and retains only the latest ephemeral image.
    Ok(ToolOutput {
        data: output.clone(),
        images,
    })
}

struct Proxy {
    descriptor: ToolDescriptor,
    bridge: Arc<Bridge>,
}
struct InFlight {
    id: String,
    owner: String,
    bridge: Arc<Bridge>,
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
                .send(json!({"kind":"cancel","invocation_id":self.id,"owner":self.owner}));
        }
    }
}
impl Tool for Proxy {
    fn descriptor(&self) -> ToolDescriptor {
        self.descriptor.clone()
    }
    fn call(&self, context: ToolContext, arguments: Value) -> ToolFuture<'_> {
        Box::pin(async move {
            let owner = loop {
                let notified = self.bridge.available.notified();
                tokio::pin!(notified);
                notified.as_mut().enable();
                if let Some(owner) = self
                    .bridge
                    .owners
                    .lock()
                    .unwrap()
                    .get(&self.descriptor.qualified_name)
                    .cloned()
                {
                    break owner;
                }
                tokio::select! {_=context.cancellation.cancelled()=>return Err(ToolError::cancelled()),_=notified=>{}}
            };
            let id = openworker_runtime::id("callback");
            let (tx, rx) = oneshot::channel();
            {
                let mut pending = self.bridge.pending.lock().unwrap();
                if pending.len() >= 64 {
                    return Err(ToolError::new(
                        "capacity",
                        "Application callback capacity exceeded",
                    ));
                }
                pending.insert(
                    id.clone(),
                    Pending {
                        owner: owner.clone(),
                        context: context.clone(),
                        result: tx,
                    },
                );
            }
            let _guard = InFlight {
                id: id.clone(),
                owner: owner.clone(),
                bridge: self.bridge.clone(),
            };
            self.bridge.events.send(json!({"kind":"call","owner":owner,"invocation_id":id,"tool":self.descriptor.qualified_name,"arguments":arguments,
                "context":{"session_id":context.session_id,"turn_id":context.turn_id,"call_id":context.call_id,"workspace":context.workspace}}))
                .map_err(|_|ToolError::new("outcome_unknown","Application callback delivery was lost; do not replay"))?;
            tokio::select! {
                _=context.cancellation.cancelled()=>Err(ToolError::new("outcome_unknown","Application callback cancelled; effects may already have occurred")),
                response=rx=>response.unwrap_or_else(|_|Err(ToolError::new("outcome_unknown","Application callback result lost; do not replay"))),
            }
        })
    }
}

#[cfg(test)]
mod image_tests {
    use super::*;
    #[test]
    fn image_envelope_is_explicit_bounded_and_keeps_legacy_json_unchanged() {
        let legacy = json!({"output":{"images":["ordinary data"]}});
        let out = callback_output(&legacy).unwrap();
        assert_eq!(out.data, legacy["output"]);
        assert!(out.images.is_empty());
        let image = json!({"mime_type":"image/png","base64":"iVBORw0KGgo="});
        let out = callback_output(&json!({"output":{"ok":true},"images":[image.clone()]})).unwrap();
        assert_eq!(out.images.len(), 1);
        assert_eq!(out.data, json!({"ok":true}));
        assert!(callback_output(&json!({"output":{},"images":[image.clone(),image]})).is_err());
        assert!(callback_output(
            &json!({"output":{},"images":[{"mime_type":"image/svg+xml","base64":"bad"}]})
        )
        .is_err());
        assert!(callback_output(
            &json!({"output":{},"images":[{"mime_type":"image/png","base64":"a".repeat(768001)}]})
        )
        .is_err());
        assert!(callback_output(&json!({"output":"a".repeat(128001)})).is_err());
    }
}
