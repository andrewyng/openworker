#![cfg(feature = "test-support")]
use openworker_sdk::{
    agents::{Agent, Harness, RunStatus, Sandbox},
    integrations::*,
};
use serde_json::json;
use std::{
    sync::{Arc, Mutex},
    time::Duration,
};

static EVENTS: Mutex<Vec<String>> = Mutex::new(Vec::new());
struct Recorder;
impl Observer for Recorder {
    fn notify<'a>(&'a self, event: Event<'a>) -> Delivery<'a> {
        Box::pin(async move {
            EVENTS.lock().unwrap().push(event.state.to_owned());
            Ok(())
        })
    }
}

#[tokio::test]
async fn lifecycle_delivery_works_without_security_features() {
    EVENTS.lock().unwrap().clear();
    let mut registry = Registry::default();
    registry
        .register(
            "record",
            Registration {
                version: "1",
                capability: Capability::Lifecycle,
                configure: |_, _| Ok(Instance::Observer(Arc::new(Recorder))),
            },
        )
        .unwrap();
    let root = tempfile::tempdir().unwrap();
    let harness = Harness::builder()
        .integration_registry(registry)
        .integration("record", json!({}))
        .state_dir(root.path())
        .sandbox(Sandbox::Off)
        .scripted(true)
        .start()
        .await
        .unwrap();
    let agent = harness
        .create_agent(
            Agent::new("observer-demo")
                .instructions("Answer briefly.")
                .tools([] as [&str; 0])
                .memory(false)
                .planning(false)
                .scheduling(false),
        )
        .await
        .unwrap();
    let session = agent.new_session().await.unwrap();
    assert_eq!(
        session.run("TEST_RESOURCE_GRANTS []").await.unwrap().status,
        RunStatus::Completed
    );
    session.close().await.unwrap();
    tokio::time::timeout(Duration::from_secs(8), async {
        loop {
            if EVENTS.lock().unwrap().iter().any(|s| s == "session_closed") {
                break;
            }
            tokio::time::sleep(Duration::from_millis(50)).await;
        }
    })
    .await
    .unwrap();
    let events = EVENTS.lock().unwrap().clone();
    for expected in [
        "session_created",
        "turn_started",
        "model_attempt_started",
        "model_attempt_finished",
        "turn_completed",
        "session_closed",
    ] {
        assert!(
            events.iter().any(|s| s == expected),
            "Missing {expected}: {events:?}"
        );
    }
    harness.shutdown().await.unwrap();
}
