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
    let mut child_tools = vec!["coding.read_file"];
    let mut child_calls =
        vec![json!({"name":"coding__read_file","arguments":{"path":"proof.txt"}})];
    let write_path = match approval {
        6 => Some("reports/proof.txt"),
        7 => Some("reports-old/proof.txt"),
        _ => None,
    };
    if let Some(path) = write_path {
        child_tools.push("coding.write_file");
        child_calls.push(json!({"name":"coding__write_file","arguments":{"path":path,"content":"reviewed report"}}));
    }
    let executable = std::path::PathBuf::from(env!("CARGO_BIN_EXE_sdk-test-host"));
    let builder = Harness::builder()
        .state_dir(root.join("state"))
        .executable(executable.clone())
        .sandbox(Sandbox::Off)
        .scripted(true)
        .agent(
            Agent::new("assistant")
                .instructions("Coordinate independently.")
                .tools(child_tools.iter().copied())
                .never_allow_read_write_outside_workspace(approval == 4)
                .approvals(if approval > 0 {
                    if approval >= 2 {
                        Approvals::Always
                    } else {
                        Approvals::Only(Default::default())
                    }
                } else {
                    Approvals::Never
                })
                .subagent_options(SubagentOptions {
                    creation_grant: (approval >= 3).then(|| SubagentCreationGrant {
                        max_creations: 1,
                        tools: child_tools.iter().map(|tool| (*tool).into()).collect(),
                        workspace: (approval >= 4).then(|| SubagentWorkspaceGrant {
                            write: if write_path.is_some() {
                                vec!["reports/".into()]
                            } else {
                                vec![]
                            },
                            read: vec![if approval != 5 {
                                ".".into()
                            } else {
                                "other-directory".into()
                            }],
                        }),
                    }),
                    ..SubagentOptions::enabled()
                }),
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
    let request = json!({"agent_id":"reader","system_prompt":"Read the named evidence file.","context":plan(json!(child_calls)),"tools":child_tools,"execution":execution});
    let mut steps = vec![json!({"name":"create_sub_agent","arguments":request})];
    if execution == "async" {
        steps.push(
            json!({"name":"wait_agent","arguments":{"agent_ids":["reader"],"timeout_ms":15000}}),
        );
    }
    let prompt = plan(json!(steps));
    let mut run = tokio::time::timeout(Duration::from_secs(35), parent.run(&prompt))
        .await
        .unwrap()
        .unwrap();
    if matches!(approval, 1 | 2) {
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
    if matches!(approval, 2 | 3 | 5 | 7) {
        assert_eq!(
            run.status,
            RunStatus::Interrupted,
            "child approval must remain visible while parent is parked"
        );
        let card = &run.interruptions[0];
        assert_ne!(card.session_id, parent.id());
        if let Some(path) = write_path {
            assert!(!work.join(path).exists());
        }
        parent
            .respond(card, Response::Approve(ApprovalDecision::Once))
            .await
            .unwrap();
        run = tokio::time::timeout(Duration::from_secs(35), parent.resume())
            .await
            .unwrap()
            .unwrap();
    }
    if approval == 3 {
        assert_eq!(run.status, RunStatus::Completed);
        let retry = parent.run(&prompt).await.unwrap();
        assert_eq!(retry.status, RunStatus::Completed, "{retry:?}");
        let mut second =
            serde_json::from_str::<Value>(prompt.strip_prefix("TEST_RESOURCE_GRANTS ").unwrap())
                .unwrap();
        second[0]["arguments"]["agent_id"] = json!("second-reader");
        let second = parent.run(&plan(second)).await.unwrap();
        assert_eq!(second.status, RunStatus::Interrupted, "{second:?}");
        assert_eq!(second.interruptions[0].session_id, parent.id());
    }
    if let Some(path) = write_path {
        assert_eq!(
            std::fs::read_to_string(work.join(path)).unwrap(),
            "reviewed report"
        );
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
    assert_eq!(
        snapshot["session"]["permissions"]["never_allow_read_write_outside_workspace"]
            .as_bool()
            .unwrap_or(false),
        approval == 4,
    );
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

#[tokio::test]
async fn creation_grant_is_counted_once_and_leaves_child_approval_intact() {
    scenario(false, "sync", 3).await;
}

#[cfg(unix)]
#[tokio::test]
async fn scoped_workspace_reads_skip_approval_only_inside_granted_directory() {
    scenario(false, "sync", 4).await;
    scenario(false, "sync", 5).await;
}

#[cfg(unix)]
#[tokio::test]
async fn scoped_workspace_writes_require_approval_outside_granted_directory() {
    scenario(false, "sync", 6).await;
    scenario(false, "sync", 7).await;
}
