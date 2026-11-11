#![cfg(feature = "test-support")]
use openworker_sdk::agents::*;
use serde_json::{json, Value};
use std::time::Duration;
fn plan(steps: Value) -> String {
    format!("TEST_RESOURCE_GRANTS {steps}")
}
async fn scenario(managed: bool, execution: &str, approval: u8) {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path().canonicalize().unwrap();
    let work = root.join("work");
    std::fs::create_dir(&work).unwrap();
    std::fs::write(work.join("proof.txt"), "CHILD_FILE_EVIDENCE").unwrap();
    let executable = std::path::PathBuf::from(env!("CARGO_BIN_EXE_sdk-test-host"));
    let builder = Harness::builder()
        .state_dir(root.join("state"))
        .executable(executable.clone())
        .sandbox(Sandbox::Off)
        .scripted(true)
        .agent(
            Agent::new("assistant")
                .instructions("Coordinate independently.")
                .tool("coding.read_file")
                .approvals(if approval > 0 {
                    if approval == 2 {
                        Approvals::Always
                    } else {
                        Approvals::Only(Default::default())
                    }
                } else {
                    Approvals::Never
                })
                .allow_subagents(true),
        );
    #[cfg(feature = "recording")]
    let builder = builder.recording(openworker_sdk::RecordingConfig::sqlite(
        root.join("traces.sqlite"),
    ));
    let h = if managed {
        builder
            .managed(executable, Default::default())
            .await
            .unwrap()
    } else {
        builder.embedded().await.unwrap()
    };
    let parent = h.session("assistant", &work).await.unwrap();
    let request = json!({"agent_id":"reader","system_prompt":"Read the named evidence file.","context":plan(json!([{"name":"coding__read_file","arguments":{"path":"proof.txt"}}])),"tools":["coding.read_file"],"execution":execution});
    let mut steps = vec![json!({"name":"create_sub_agent","arguments":request})];
    if execution == "async" {
        steps.push(
            json!({"name":"wait_agent","arguments":{"agent_ids":["reader"],"timeout_ms":15000}}),
        );
    }
    let mut run = tokio::time::timeout(Duration::from_secs(35), parent.run(&plan(json!(steps))))
        .await
        .unwrap()
        .unwrap();
    if approval > 0 {
        assert_eq!(run.status, RunStatus::Interrupted);
        let card = &run.interruptions[0];
        assert!(parent
            .respond(card, Response::Approve(ApprovalDecision::Session))
            .await
            .is_err());
        parent
            .respond(card, Response::Approve(ApprovalDecision::Once))
            .await
            .unwrap();
        run = tokio::time::timeout(Duration::from_secs(35), parent.resume())
            .await
            .unwrap()
            .unwrap();
    }
    if approval == 2 {
        assert_eq!(
            run.status,
            RunStatus::Interrupted,
            "child approval must remain visible while parent is parked"
        );
        let card = &run.interruptions[0];
        assert_ne!(card.session_id, parent.id());
        parent
            .respond(card, Response::Approve(ApprovalDecision::Once))
            .await
            .unwrap();
        run = tokio::time::timeout(Duration::from_secs(35), parent.resume())
            .await
            .unwrap()
            .unwrap();
    }
    let events = h
        .client()
        .request("event/read", json!({"session_id":parent.id(),"after":0}))
        .await
        .unwrap();
    assert_eq!(run.status, RunStatus::Completed, "{run:?} {events}");
    #[cfg(feature = "recording")]
    {
        let turns = h.observability().turns(parent.id(), 0, 25).await.unwrap();
        let tid = turns["turns"][0]["turn_id"].as_str().unwrap();
        let children = h
            .observability()
            .children(openworker_sdk::TraceQuery::turn(parent.id(), tid))
            .await
            .unwrap();
        assert_eq!(children["children"].as_array().unwrap().len(), 1);
        assert_eq!(children["children"][0]["agent_id"], "reader");
        assert_eq!(children["children"][0]["origin"]["parent_turn_id"], tid);
        assert!(children["children"][0]["origin"]["parent_call_id"].is_string());
        assert!(!children.to_string().contains("CHILD_FILE_EVIDENCE"));
        assert!(children["children"][0]["child_turn_id"].is_string());
        let tree = h
            .observability()
            .subtree_summary(openworker_sdk::TraceQuery::turn(parent.id(), tid))
            .await
            .unwrap();
        assert_eq!(tree["selection"]["runs"].as_array().unwrap().len(), 2);
        assert_eq!(tree["selection"]["selection_truncated"], false);
        assert!(tree["summary"]["wall_elapsed_us"].is_null());
    }

    let doc = h
        .client()
        .request(
            "agent-team/command",
            json!({"session_id":parent.id(),"operation":"snapshot","arguments":{}}),
        )
        .await
        .unwrap();
    assert_eq!(
        doc["roster"]["instances"].as_object().unwrap().len(),
        2,
        "{doc} {events}"
    );
    let child = doc["roster"]["instances"]["reader"]["session_id"]
        .as_str()
        .unwrap();
    let child_events = h
        .client()
        .request("event/read", json!({"session_id":child,"after":0}))
        .await
        .unwrap();
    assert!(
        child_events.to_string().contains("CHILD_FILE_EVIDENCE"),
        "{child_events}"
    );
    let snapshot = h.client().snapshot(child).await.unwrap();
    assert!(snapshot["session"]["package"]["standalone_subagents"].is_null());
    assert!(events.to_string().contains("create_sub_agent"));
    h.shutdown().await.unwrap();
}
#[tokio::test]
async fn embedded_sync() {
    scenario(false, "sync", 0).await;
}
#[tokio::test]
async fn embedded_async() {
    scenario(false, "async", 0).await;
}
#[tokio::test]
async fn managed_sync() {
    scenario(true, "sync", 0).await;
}
#[tokio::test]
async fn managed_async() {
    scenario(true, "async", 0).await;
}

#[tokio::test]
async fn exact_child_definition_approval() {
    scenario(false, "sync", 1).await;
}

#[tokio::test]
async fn child_approval_is_visible_while_sync_parent_waits() {
    scenario(false, "sync", 2).await;
}
