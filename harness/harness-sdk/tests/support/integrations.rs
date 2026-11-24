//! Development-only integrations for managed SDK conformance tests.
use anyhow::{ensure, Result};
use openworker_sdk::{
    guardrails::{CheckFuture, Config, Context, Decision, Handler, HandlerRef, Hook, HookPhase},
    integrations::{Capability, Delivery, Event, Instance, Observer, Registration, Registry},
};
use serde_json::{json, Value};
use std::{io::Write, path::PathBuf, sync::Arc};
use tokio_util::sync::CancellationToken;

struct RecordingObserver(PathBuf);

impl Observer for RecordingObserver {
    fn notify<'a>(&'a self, event: Event<'a>) -> Delivery<'a> {
        Box::pin(async move {
            let mut log = std::fs::OpenOptions::new()
                .create(true)
                .append(true)
                .open(&self.0)?;
            writeln!(
                log,
                "{}",
                json!({"session":event.session_id,"state":event.state,"seq":event.sequence,"interaction":event.interaction_id,
                    "agent_context":event.agent_context.map(|c| json!({"agent_id":c.agent_id,"parent_agent_id":c.parent_agent_id,"root_agent_id":c.root_agent_id}))})
            )?;
            // Fail every delivery, including session creation and tool completion.
            anyhow::bail!("fixture observer unavailable")
        })
    }
}

struct DenyTool;
impl Handler for DenyTool {
    fn call(&self, _: Context, _: CancellationToken) -> CheckFuture<'_> {
        Box::pin(async { Ok(Decision::block("FIXTURE_TOOL_BLOCKED")) })
    }
}

pub fn registry() -> Result<Registry> {
    let mut registry = Registry::default();
    registry.register(
        "mock-observer",
        Registration {
            version: "1",
            capability: Capability::Lifecycle,
            configure: |settings, _| {
                let log: PathBuf = serde_json::from_value(settings["log"].clone())?;
                ensure!(
                    log.is_absolute(),
                    "fixture observer requires an absolute log path"
                );
                Ok(Instance::Observer(Arc::new(RecordingObserver(log))))
            },
        },
    )?;
    registry.register(
        "mock-scanner",
        Registration {
            version: "1",
            capability: Capability::BlockingChecks,
            configure: |settings, handlers| {
                ensure!(
                    settings == &Value::Object(Default::default()),
                    "unexpected scanner settings"
                );
                let handler = HandlerRef {
                    name: "mock-deny".into(),
                    version: "1".into(),
                };
                handlers.register(handler.clone(), Arc::new(DenyTool))?;
                Ok(Instance::Checks(Config {
                    hooks: vec![Hook {
                        name: "deny-before-tool".into(),
                        version: "1".into(),
                        when: HookPhase::BeforeTool,
                        handler,
                        tools: vec![],
                        timeout_ms: 1000,
                    }],
                    ..Default::default()
                }))
            },
        },
    )?;
    Ok(registry)
}
