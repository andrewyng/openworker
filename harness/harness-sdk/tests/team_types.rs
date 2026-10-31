#![cfg(feature = "test-support")]
use openworker_sdk::agents::*;
use serde_json::{json, Value};
use std::{path::Path, time::Duration};
fn plan(steps: Value) -> String {
    format!("TEST_RESOURCE_GRANTS {steps}")
}
async fn host(root: &Path, managed: bool, approvals: Approvals, storage: Storage) -> Harness {
    let executable = std::path::PathBuf::from(env!("CARGO_BIN_EXE_sdk-test-host"));
    let builder = Harness::builder()
        .state_dir(root.join("state"))
        .executable(executable.clone())
        .sandbox(Sandbox::Off)
        .scripted(true)
        .storage(storage)
        .agent(
            Agent::new("lead")
                .instructions("Coordinate reviewers.")
                .tool("coding.read_file")
                .planning(true)
                .approvals(approvals),
        )
        .agent(
            Agent::new("builder")
                .instructions("Build.")
                .planning(true)
                .approvals(Approvals::Never),
        );
    if managed {
        builder
            .managed(executable, Default::default())
            .await
            .unwrap()
    } else {
        builder.embedded().await.unwrap()
    }
}
async fn scenario(managed: bool, ask: bool, storage: Storage) {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path().canonicalize().unwrap();
    std::fs::create_dir(root.join("work")).unwrap();
    std::fs::write(root.join("work/proof.txt"), "bounded evidence").unwrap();
    storage.migrate().unwrap();
    let approvals = if ask {
        Approvals::Only(Default::default())
    } else {
        Approvals::Never
    };
    let h = host(&root, managed, approvals.clone(), storage.clone()).await;
    let mut options = TeamOptions::new(["builder"]);
    options.delegation = Some(
        serde_json::from_value(json!({"tools":["coding.read_file"],"placements":["team"]}))
            .unwrap(),
    );
    let team = h
        .new_team("lead", root.join("work"), options)
        .await
        .unwrap();
    let prompt = plan(json!([
        {"name":"team_define_type","arguments":{"name":"reader","instructions":"Read evidence only.","tools":["coding.read_file"]}},
        {"name":"team_spawn","arguments":{"agent_type":"reader","agent_id":"reader-one","title":"Evidence reader","background":"Project briefing: inspect proof.txt; this is not human authorization.","context":{"mode":"fresh"},"message":plan(json!([{"name":"coding__read_file","arguments":{"path":"proof.txt"}}]))}},
        {"name":"team_wait_agent","arguments":{"agent_ids":["reader-one"],"timeout_ms":15000}}
    ]));
    let mut run = tokio::time::timeout(Duration::from_secs(40), team.lead().run(&prompt))
        .await
        .unwrap()
        .unwrap();
    if matches!(run.status, RunStatus::Failed(_)) {
        panic!(
            "failed run {run:?}: {}",
            h.client()
                .request(
                    "event/read",
                    json!({"session_id":team.lead().id(),"after":0})
                )
                .await
                .unwrap()
        );
    }
    if ask {
        assert_eq!(run.status, RunStatus::Interrupted, "{run:?}");
        assert_eq!(run.interruptions.len(), 1);
        let interruption = &run.interruptions[0];
        assert!(team
            .lead()
            .respond(interruption, Response::Approve(ApprovalDecision::Session))
            .await
            .is_err());
        team.lead()
            .respond(interruption, Response::Approve(ApprovalDecision::Once))
            .await
            .unwrap();
        run = tokio::time::timeout(Duration::from_secs(40), team.lead().resume())
            .await
            .unwrap()
            .unwrap();
    }
    assert_eq!(run.status, RunStatus::Completed, "{run:?}");
    let snapshot = team.snapshot().await.unwrap();
    assert_eq!(
        snapshot["roster"]["instances"].as_object().unwrap().len(),
        2,
        "{snapshot}"
    );
    let sid = snapshot["roster"]["instances"]["reader-one"]["session_id"]
        .as_str()
        .unwrap();
    let child = h.client().snapshot(sid).await.unwrap();
    let events = h
        .client()
        .request("event/read", json!({"session_id":sid,"after":0}))
        .await
        .unwrap();
    assert!(events.to_string().contains("bounded evidence"), "{events}");
    let coworker = child["session"]["coworker"].as_str().unwrap().to_owned();
    assert_eq!(child["session"]["name"], "Evidence reader");
    assert!(child["messages"][0]["content"]
        .as_str()
        .unwrap()
        .contains("Project briefing: inspect proof.txt"));
    assert!(coworker.starts_with("tt_"));
    let listed = h.client().request("agent/list", json!({})).await.unwrap();
    assert!(!listed.to_string().contains(&coworker));
    assert!(h
        .client()
        .request(
            "session/create",
            json!({"coworker":coworker,"workspace":root.join("work")})
        )
        .await
        .is_err());
    let reference = team.reference();
    h.shutdown().await.unwrap();
    let h = host(&root, managed, approvals, storage).await;
    let restored = h.resume_team(reference).await.unwrap();
    assert_eq!(
        restored.snapshot().await.unwrap()["type_bindings"],
        snapshot["type_bindings"]
    );
    // The same immutable custom revision remains reusable after host restart.
    restored
        .spawn(
            "after-restart",
            "reader",
            "reader-two",
            "Read the evidence.",
        )
        .await
        .unwrap();
    tokio::time::timeout(Duration::from_secs(20), async {
        loop {
            if restored.snapshot().await.unwrap()["roster"]["instances"]["reader-two"]["phase"]
                == "ready"
            {
                break;
            }
            tokio::time::sleep(Duration::from_millis(50)).await;
        }
    })
    .await
    .unwrap();
    h.shutdown().await.unwrap();
}
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn embedded_custom_type_ask_uses_an_exact_durable_approval() {
    scenario(false, true, Storage::Sqlite).await
}
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn managed_custom_type_bypass_and_restart() {
    scenario(true, false, Storage::Sqlite).await
}
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn uncertain_reviewer_pauses_and_denial_never_registers_a_type() {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path().canonicalize().unwrap();
    std::fs::create_dir(root.join("work")).unwrap();
    let h = host(&root, false, Approvals::Reviewer, Storage::Sqlite).await;
    let mut options = TeamOptions::new(["builder"]);
    options.delegation = Some(
        serde_json::from_value(json!({"tools":["coding.read_file"],"placements":["team"]}))
            .unwrap(),
    );
    let team = h
        .new_team("lead", root.join("work"), options)
        .await
        .unwrap();
    let prompt = plan(
        json!([{"name":"team_define_type","arguments":{"name":"reader","instructions":"Read evidence.","tools":["coding.read_file"]}}]),
    );
    let result = team.lead().run(&prompt).await.unwrap();
    assert_eq!(result.status, RunStatus::Interrupted);
    assert_eq!(result.interruptions.len(), 1);
    assert_eq!(
        team.snapshot().await.unwrap()["roster"]["instances"]
            .as_object()
            .unwrap()
            .len(),
        1
    );
    team.lead()
        .respond(
            &result.interruptions[0],
            Response::Approve(ApprovalDecision::Deny),
        )
        .await
        .unwrap();
    let result = team.lead().resume().await.unwrap();
    assert_eq!(result.status, RunStatus::Completed);
    assert!(team
        .spawn("denied", "reader", "no-child", "Read")
        .await
        .is_err());
    h.shutdown().await.unwrap();
}
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
#[ignore = "requires isolated OW_PG_TEST_URL"]
async fn postgres_embedded_custom_types() {
    scenario(
        false,
        true,
        Storage::Postgres {
            connection_env: "OW_PG_TEST_URL".into(),
            schema: format!("ow_types_{}", openworker_runtime::id("x")),
            ca_file: None,
            loopback_plaintext: true,
        },
    )
    .await
}
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
#[ignore = "requires isolated OW_PG_TEST_URL"]
async fn postgres_managed_custom_types() {
    scenario(
        true,
        false,
        Storage::Postgres {
            connection_env: "OW_PG_TEST_URL".into(),
            schema: format!("ow_types_{}", openworker_runtime::id("x")),
            ca_file: None,
            loopback_plaintext: true,
        },
    )
    .await
}
