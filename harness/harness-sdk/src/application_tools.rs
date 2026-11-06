use crate::{Error, HarnessClient, Notification, Result};
use serde_json::{json, Value};
use std::{
    collections::BTreeMap,
    future::Future,
    pin::Pin,
    sync::{Arc, Mutex, Weak},
};
use tokio_util::sync::CancellationToken;
type Handler = Arc<
    dyn Fn(Value, ToolContext) -> Pin<Box<dyn Future<Output = anyhow::Result<Value>> + Send>>
        + Send
        + Sync,
>;

/// Application callback builder; compiled into the app, not necessarily the child harness.
pub struct Tool {
    name: String,
    description: String,
    schema: Value,
    version: String,
    handler: Option<Handler>,
}
impl Tool {
    pub fn new(name: impl Into<String>) -> Self {
        Self {
            name: name.into(),
            description: String::new(),
            schema: json!({"type":"object","properties":{},"additionalProperties":false}),
            version: "1".into(),
            handler: None,
        }
    }
    pub fn description(mut self, text: impl Into<String>) -> Self {
        self.description = text.into();
        self
    }
    pub fn input_schema(mut self, schema: Value) -> Self {
        self.schema = schema;
        self
    }
    pub fn version(mut self, version: impl Into<String>) -> Self {
        self.version = version.into();
        self
    }
    pub fn handler<F, Fut>(mut self, handler: F) -> Self
    where
        F: Fn(Value, ToolContext) -> Fut + Send + Sync + 'static,
        Fut: Future<Output = anyhow::Result<Value>> + Send + 'static,
    {
        self.handler = Some(Arc::new(move |args, context| {
            Box::pin(handler(args, context))
        }));
        self
    }
}
#[derive(Clone)]
pub struct ToolContext {
    pub session_id: String,
    pub turn_id: String,
    pub call_id: String,
    pub workspace: std::path::PathBuf,
    cancel: CancellationToken,
    client: HarnessClient,
    owner: String,
    invocation: String,
}
impl ToolContext {
    pub fn cancellation(&self) -> CancellationToken {
        self.cancel.clone()
    }
    pub async fn progress(&self, message: impl Into<String>, data: Value) -> Result<()> {
        self.client.callback_message("application/progress",json!({"owner":self.owner,"invocation_id":self.invocation,"message":message.into(),"data":data})).await?;
        Ok(())
    }
}
pub(crate) struct ApplicationTools {
    owner: String,
    handlers: Mutex<BTreeMap<String, (openworker_tools::ToolDescriptor, Handler)>>,
    running: Mutex<BTreeMap<String, CancellationToken>>,
    stop: CancellationToken,
    registration: tokio::sync::Mutex<()>,
}
impl Drop for ApplicationTools {
    fn drop(&mut self) {
        self.stop.cancel();
        for token in self.running.lock().unwrap().values() {
            token.cancel();
        }
    }
}
impl ApplicationTools {
    pub fn new(client: &HarnessClient) -> Arc<Self> {
        let state = Arc::new(Self {
            owner: openworker_runtime::id("app"),
            handlers: Default::default(),
            running: Default::default(),
            stop: CancellationToken::new(),
            registration: Default::default(),
        });
        let weak = Arc::downgrade(&state);
        let client = Arc::downgrade(&client.inner);
        let mut events = client.upgrade().unwrap().events.subscribe();
        let mut connection = client.upgrade().unwrap().state.clone();
        let stop = state.stop.clone();
        tokio::spawn(async move {
            loop {
                let event = tokio::select! {_=stop.cancelled()=>break,event=events.recv()=>event,
                    changed=connection.changed()=>{
                        if changed.is_err(){break;}
                        if !matches!(*connection.borrow(),crate::ConnectionState::Ready{..}) {
                            if let Some(state)=weak.upgrade(){for token in state.running.lock().unwrap().values(){token.cancel();}}
                        }
                        continue;
                    }
                };
                let Some(state) = weak.upgrade() else {
                    break;
                };
                match event {
                    Ok(Notification::Application(value)) => state.accept(client.clone(), value),
                    Err(tokio::sync::broadcast::error::RecvError::Closed) => break,
                    Err(tokio::sync::broadcast::error::RecvError::Lagged(_)) => {
                        for token in state.running.lock().unwrap().values() {
                            token.cancel();
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
        client: &HarnessClient,
        namespace: &str,
        tools: impl IntoIterator<Item = Tool>,
    ) -> Result<()> {
        if client.info()["capabilities"]["application_callbacks"] != true {
            return Err(Error::Setup(
                "Runtime does not support application callbacks; update the bundled harness".into(),
            ));
        }
        let _registration = self.registration.lock().await;
        let mut values = vec![];
        for tool in tools {
            let descriptor = openworker_tools::ToolDescriptor {
                qualified_name: format!("{namespace}.{}", tool.name),
                description: tool.description,
                input_schema: tool.schema,
                version: tool.version,
            };
            let handler = tool.handler.ok_or_else(|| {
                Error::InvalidInput("Application tool handler is required".into())
            })?;
            values.push((descriptor, handler));
        }
        // Never overwrite an already registered callback silently.
        {
            let handlers = self.handlers.lock().unwrap();
            for (descriptor, _) in &values {
                if handlers.contains_key(&descriptor.qualified_name) {
                    return Err(Error::InvalidInput(
                        "Application tool already registered on this handle".into(),
                    ));
                }
            }
        }
        let request =
            json!({"owner":self.owner,"tools":values.iter().map(|(d,_)|d).collect::<Vec<_>>()});
        {
            let mut handlers = self.handlers.lock().unwrap();
            for (descriptor, handler) in values {
                handlers.insert(descriptor.qualified_name.clone(), (descriptor, handler));
            }
        }
        if let Err(error) = client
            .request("application/register", request.clone())
            .await
        {
            if matches!(error, Error::Remote(_) | Error::InvalidInput(_)) {
                let mut handlers = self.handlers.lock().unwrap();
                for descriptor in request["tools"].as_array().unwrap() {
                    handlers.remove(descriptor["qualified_name"].as_str().unwrap());
                }
            }
            return Err(error);
        }
        client.inner.registrations.lock().unwrap().push(request);
        Ok(())
    }
    pub fn stop(&self) {
        self.stop.cancel();
        for token in self.running.lock().unwrap().values() {
            token.cancel();
        }
    }
    fn accept(self: &Arc<Self>, client: Weak<crate::Handle>, value: Value) {
        if value["owner"] != self.owner {
            return;
        }
        let Some(id) = value["invocation_id"].as_str().map(str::to_owned) else {
            return;
        };
        if value["kind"] == "cancel" {
            if let Some(token) = self.running.lock().unwrap().get(&id) {
                token.cancel();
            }
            return;
        }
        if value["kind"] != "call" {
            return;
        }
        let handler = value["tool"].as_str().and_then(|name| {
            self.handlers
                .lock()
                .unwrap()
                .get(name)
                .map(|(_, h)| h.clone())
        });
        let Some(client) = client.upgrade().map(|inner| HarnessClient { inner }) else {
            return;
        };
        let cancel = self.stop.child_token();
        {
            let mut running = self.running.lock().unwrap();
            if running.contains_key(&id) || running.len() >= 64 {
                return;
            }
            running.insert(id.clone(), cancel.clone());
        }
        let state = self.clone();
        tokio::spawn(async move {
            use futures_util::FutureExt;
            let context = ToolContext {
                session_id: value["context"]["session_id"].as_str().unwrap_or("").into(),
                turn_id: value["context"]["turn_id"].as_str().unwrap_or("").into(),
                call_id: value["context"]["call_id"].as_str().unwrap_or("").into(),
                workspace: value["context"]["workspace"].as_str().unwrap_or("").into(),
                cancel: cancel.clone(),
                client: client.clone(),
                owner: state.owner.clone(),
                invocation: id.clone(),
            };
            let work = async {
                match handler {
                    Some(h) => std::panic::AssertUnwindSafe(async {
                        h(value["arguments"].clone(), context).await
                    })
                    .catch_unwind()
                    .await
                    .ok()
                    .and_then(|r| r.ok()),
                    None => None,
                }
            };
            let result = tokio::select! {_=cancel.cancelled()=>None,result=tokio::time::timeout(std::time::Duration::from_secs(70),work)=>result.ok().flatten()};
            if !cancel.is_cancelled() {
                let mut response = json!({"owner":state.owner,"invocation_id":id});
                if let Some(output) = result {
                    response["output"] = output;
                } else {
                    response["error"] = json!({"code":"application_error"});
                }
                let _ = client
                    .callback_message("application/result", response)
                    .await;
            }
            cancel.cancel();
            state.running.lock().unwrap().remove(&id);
        });
    }
}
