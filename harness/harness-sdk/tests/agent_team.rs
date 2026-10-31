#![cfg(feature = "test-support")]
use openworker_sdk::agents::*;
use serde_json::{json, Value};
use std::{path::PathBuf, time::Duration};
fn plan(steps: Value) -> String {
    format!("TEST_RESOURCE_GRANTS {steps}")
}
async fn host(root: &std::path::Path, managed: bool) -> Harness {
    host_storage(root, managed, openworker_sdk::Storage::Sqlite).await
}
async fn host_storage(
    root: &std::path::Path,
    managed: bool,
    storage: openworker_sdk::Storage,
) -> Harness {
    let b = Harness::builder()
        .state_dir(root.join("state"))
        .executable(PathBuf::from(env!("CARGO_BIN_EXE_sdk-test-host")))
        .sandbox(Sandbox::Off)
        .scripted(true)
        .storage(storage)
        .agent(
            Agent::new("lead")
                .planning(true)
                .instructions("Coordinate named builders and verifiers; wait instead of polling.")
                .approvals(Approvals::Never),
        )
        .agent(
            Agent::new("builder")
                .planning(true)
                .instructions("Build your assigned part and report.")
                .approvals(Approvals::Never),
        )
        .agent(
            Agent::new("verifier")
                .planning(true)
                .instructions("Verify independently and report.")
                .approvals(Approvals::Never),
        );
    if managed {
        b.managed(
            PathBuf::from(env!("CARGO_BIN_EXE_sdk-test-host")),
            openworker_sdk::RestartPolicy::default(),
        )
        .await
        .unwrap()
    } else {
        b.embedded().await.unwrap()
    }
}
async fn scenario(managed: bool) {
    scenario_storage(managed, openworker_sdk::Storage::Sqlite).await
}
async fn scenario_storage(managed: bool, storage: openworker_sdk::Storage) {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path().canonicalize().unwrap();
    let work = root.join("work");
    std::fs::create_dir(&work).unwrap();
    storage.migrate().unwrap();
    let h = host_storage(&root, managed, storage.clone()).await;
    let team = h
        .new_team("lead", &work, TeamOptions::new(["builder", "verifier"]))
        .await
        .unwrap();
    let build =
        plan(json!([{"name":"team_post_board","arguments":{"message":"@lead build proof ready"}}]));
    let verify = plan(
        json!([{"name":"team_post_board","arguments":{"message":"@lead independent verification passed"}}]),
    );
    let prompt = plan(json!([
    {"name":"team_spawn","arguments":{"agent_type":"builder","agent_id":"builder-ui","message":build}},
    {"name":"team_wait_agent","arguments":{"agent_ids":["builder-ui"],"timeout_ms":10000}},
    {"name":"team_spawn","arguments":{"agent_type":"verifier","agent_id":"verifier-ui","message":verify}},
    {"name":"team_wait_agent","arguments":{"agent_ids":["verifier-ui"],"timeout_ms":10000}}
    ]));
    let mut stream = team.lead().run_streamed(&prompt).await.unwrap();
    let mut instances = std::collections::BTreeSet::new();
    tokio::time::timeout(Duration::from_secs(40), async {
        while let Some(event) = stream.next().await {
            if let RunEvent::TeamActivity { agent_id, .. } = event {
                instances.insert(agent_id);
            }
        }
    })
    .await
    .expect("team stream must not deadlock");
    let r = stream.finish().await.unwrap();
    assert!(
        instances.contains("lead")
            && instances.contains("builder-ui")
            && instances.contains("verifier-ui"),
        "Stream must identify instances, not only types: {instances:?}"
    );
    assert_eq!(r.status, RunStatus::Completed, "{r:?}");
    let doc = team.snapshot().await.unwrap();
    let instances = doc["roster"]["instances"].as_object().unwrap();
    assert_eq!(instances.len(), 3);
    let page = team
        .history(HistoryQuery {
            message_id: None,
            direction: Default::default(),
            limit: 20,
        })
        .await
        .unwrap();
    assert_eq!(page.messages.len(), 2);
    let saved = team.reference();
    let id = team.id().to_owned();
    h.shutdown().await.unwrap();
    let h = host_storage(&root, managed, storage).await;
    let restored = h.resume_team(saved).await.unwrap();
    assert_eq!(restored.id(), id);
    assert_eq!(
        restored
            .history(HistoryQuery {
                message_id: None,
                direction: Default::default(),
                limit: 20
            })
            .await
            .unwrap()
            .messages
            .len(),
        2
    );
    h.shutdown().await.unwrap();
}
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn embedded_named_team() {
    scenario(false).await;
}
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn managed_named_team() {
    scenario(true).await;
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn aggregate_limit_returns_a_bounded_summary_instead_of_failing_the_run() {
    for managed in [false, true] {
        let tmp = tempfile::tempdir().unwrap();
        let root = tmp.path().canonicalize().unwrap();
        let work = root.join("work");
        std::fs::create_dir(&work).unwrap();
        let h = host(&root, managed).await;
        let mut options = TeamOptions::new(["builder"]);
        options.limits = Some(TurnLimits {
            max_model_calls: 1,
            max_output_tokens: 1024,
            summary_tokens: 512,
        });
        let team = h.new_team("lead", &work, options).await.unwrap();
        let result = team.lead().run(&plan(json!([
            {"name":"team_post_board","arguments":{"message":"first permitted action"}},
            {"name":"team_post_board","arguments":{"message":"must not reach this second action"}}
        ]))).await.unwrap();
        assert_eq!(result.status, RunStatus::Completed, "{result:?}");
        assert!(result.final_output.is_some());
        let snapshot = team.lead().snapshot().await.unwrap();
        assert_eq!(
            snapshot["turn"]["budget"]["stop_reason"],
            "team_budget_exceeded"
        );
        // Only the admitted work call and one tool-free summary are charged.
        assert_eq!(snapshot["turn"]["provider_attempts"], 2);
        assert_eq!(snapshot["turn"]["budget"]["charged_output_tokens"], 1024);
        let doc = team.snapshot().await.unwrap();
        assert_eq!(doc["budget"]["model_calls"], 1);
        assert_eq!(doc["budget"]["summary_started"], true);
        assert_eq!(
            team.history(HistoryQuery {
                message_id: None,
                direction: Default::default(),
                limit: 20
            })
            .await
            .unwrap()
            .messages
            .len(),
            1
        );
        h.shutdown().await.unwrap();
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn durable_stop_cancels_parked_children_in_both_process_modes() {
    for managed in [false, true] {
        let tmp = tempfile::tempdir().unwrap();
        let root = tmp.path().canonicalize().unwrap();
        let work = root.join("work");
        std::fs::create_dir(&work).unwrap();
        let h = host(&root, managed).await;
        let team = h
            .new_team("lead", &work, TeamOptions::new(["builder"]))
            .await
            .unwrap();
        let waiting = plan(
            json!([{"name":"team_wait_agent","arguments":{"agent_ids":["lead"],"timeout_ms":60000}}]),
        );
        let child = team
            .spawn("spawn-parked", "builder", "builder-ui", &waiting)
            .await
            .unwrap();
        let sid = child["instance"]["session_id"].as_str().unwrap();
        tokio::time::timeout(Duration::from_secs(20), async {
            loop {
                if h.client()
                    .snapshot(sid)
                    .await
                    .is_ok_and(|s| s["turn"]["status"] == "waiting_agents")
                {
                    break;
                }
                tokio::time::sleep(Duration::from_millis(50)).await;
            }
        })
        .await
        .expect("child must park");
        team.stop("stop-team").await.unwrap();
        tokio::time::timeout(Duration::from_secs(20), async {
            loop {
                if h.client().snapshot(sid).await.unwrap()["turn"]["status"] == "cancelled" {
                    break;
                }
                tokio::time::sleep(Duration::from_millis(50)).await;
            }
        })
        .await
        .expect("durable stop must cancel the child");
        assert!(team
            .spawn("late", "builder", "late-builder", "Must not run")
            .await
            .is_err());
        h.shutdown().await.unwrap();
    }
}
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn new_child_questions_can_be_answered_through_the_lead_handle() {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path().canonicalize().unwrap();
    let work = root.join("work");
    std::fs::create_dir(&work).unwrap();
    let h = host(&root, false).await;
    let team = h
        .new_team("lead", work, TeamOptions::new(["builder"]))
        .await
        .unwrap();
    let child = plan(
        json!([{"name":"ask_user","arguments":{"question":"Which theme?","options":["Light","Dark"]}}]),
    );
    let prompt = plan(
        json!([{"name":"team_spawn","arguments":{"agent_type":"builder","agent_id":"question-worker","message":child}},{"name":"team_wait_agent","arguments":{"agent_ids":["question-worker"],"timeout_ms":20000}}]),
    );
    let run = tokio::time::timeout(Duration::from_secs(30), team.lead().run(&prompt))
        .await
        .unwrap()
        .unwrap();
    assert_eq!(run.status, RunStatus::Interrupted);
    assert_eq!(run.interruptions.len(), 1);
    assert_ne!(run.interruptions[0].session_id, team.lead().id());
    team.lead()
        .respond(&run.interruptions[0], Response::Answer("Light".into()))
        .await
        .unwrap();
    let result = tokio::time::timeout(Duration::from_secs(30), team.lead().resume())
        .await
        .unwrap()
        .unwrap();
    assert_eq!(result.status, RunStatus::Completed);
    h.shutdown().await.unwrap();
}

fn postgres() -> openworker_sdk::Storage {
    openworker_sdk::Storage::Postgres {
        connection_env: "OW_PG_TEST_URL".into(),
        schema: format!("ow_team_{}", openworker_runtime::id("x")),
        ca_file: None,
        loopback_plaintext: true,
    }
}
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
#[ignore = "requires isolated OW_PG_TEST_URL"]
async fn postgres_embedded_named_team() {
    scenario_storage(false, postgres()).await;
}
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
#[ignore = "requires isolated OW_PG_TEST_URL"]
async fn postgres_managed_named_team() {
    scenario_storage(true, postgres()).await;
}
