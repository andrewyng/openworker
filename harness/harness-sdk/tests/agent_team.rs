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
async fn question_scenario(retiring: bool) {
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
    if retiring {
        team.retire(
            "graceful-question",
            "question-worker",
            RetirementMode::FinishCurrent,
        )
        .await
        .unwrap();
    }
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

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn new_child_questions_can_be_answered_through_the_lead_handle() {
    question_scenario(false).await;
}
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn graceful_retirement_preserves_answering_the_current_child_question() {
    question_scenario(true).await;
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

async fn retirement_scenario(
    managed: bool,
    storage: openworker_sdk::Storage,
    mode: RetirementMode,
) {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path().canonicalize().unwrap();
    let work = root.join("work");
    std::fs::create_dir(&work).unwrap();
    storage.migrate().unwrap();
    let h = host_storage(&root, managed, storage.clone()).await;
    let mut options = TeamOptions::new(["builder"]);
    options.policy.max_instances = 2;
    let team = h.new_team("lead", &work, options).await.unwrap();
    let task = plan(
        json!([{"name":"team_wait_agent","arguments":{"agent_ids":["lead"],"timeout_ms":1500}},{"name":"team_post_board","arguments":{"message":"@lead finished current work"}}]),
    );
    let reserved = team
        .spawn("initial", "builder", "builder-one", &task)
        .await
        .unwrap();
    let sid = reserved["instance"]["session_id"].as_str().unwrap();
    tokio::time::timeout(Duration::from_secs(15), async {
        loop {
            if let Ok(s) = h.client().snapshot(sid).await {
                if s["turn"]["status"] == "waiting_agents" {
                    break;
                }
            }
            tokio::time::sleep(Duration::from_millis(20)).await;
        }
    })
    .await
    .expect("Child must reach its parked wait");
    let first = team
        .retire("retire-original", "builder-one", mode)
        .await
        .unwrap();
    assert_eq!(first["instance"]["phase"], "retiring");
    assert_eq!(
        first,
        team.retire("retire-original", "builder-one", mode)
            .await
            .unwrap()
    );
    assert!(team
        .spawn("too-early", "builder", "builder-two", "new task")
        .await
        .is_err());
    assert!(h
        .client()
        .start_turn(sid, "late-task", "must not start")
        .await
        .is_err());
    tokio::time::timeout(Duration::from_secs(20), async {
        loop {
            if team.snapshot().await.unwrap()["roster"]["instances"]["builder-one"]["phase"]
                == "closed"
            {
                break;
            }
            tokio::time::sleep(Duration::from_millis(50)).await;
        }
    })
    .await
    .expect("Retirement must complete cleanup before releasing capacity");
    let child = h.client().snapshot(sid).await.unwrap();
    assert_eq!(child["session"]["closed"], true);
    assert_eq!(
        child["turn"]["status"],
        if mode == RetirementMode::Cancel {
            "cancelled"
        } else {
            "completed"
        }
    );
    let page = team.members(None, 1).await.unwrap();
    assert!(page["next"].is_string());
    assert_eq!(page["members"][0]["instance"]["phase"], "closed");
    team.spawn("replacement", "builder", "builder-two", "replacement task")
        .await
        .unwrap();
    assert!(team
        .spawn("recycle", "builder", "builder-one", "different task")
        .await
        .is_err());
    let reference = team.reference();
    h.shutdown().await.unwrap();
    let h = host_storage(&root, managed, storage).await;
    let team = h.resume_team(reference).await.unwrap();
    assert_eq!(
        team.snapshot().await.unwrap()["roster"]["instances"]["builder-one"]["phase"],
        "closed"
    );
    assert_eq!(
        first,
        team.retire("retire-original", "builder-one", mode)
            .await
            .unwrap()
    );
    h.shutdown().await.unwrap();
}
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn sqlite_embedded_retirement_modes() {
    for mode in [RetirementMode::FinishCurrent, RetirementMode::Cancel] {
        retirement_scenario(false, openworker_sdk::Storage::Sqlite, mode).await;
    }
}
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn sqlite_managed_retirement_modes() {
    for mode in [RetirementMode::FinishCurrent, RetirementMode::Cancel] {
        retirement_scenario(true, openworker_sdk::Storage::Sqlite, mode).await;
    }
}
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
#[ignore = "requires isolated OW_PG_TEST_URL"]
async fn postgres_embedded_retirement_modes() {
    for mode in [RetirementMode::FinishCurrent, RetirementMode::Cancel] {
        retirement_scenario(
            false,
            openworker_sdk::Storage::Postgres {
                connection_env: "OW_PG_TEST_URL".into(),
                schema: format!("ow_retire_{}", openworker_runtime::id("x")),
                ca_file: None,
                loopback_plaintext: true,
            },
            mode,
        )
        .await;
    }
}
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
#[ignore = "requires isolated OW_PG_TEST_URL"]
async fn postgres_managed_retirement_modes() {
    for mode in [RetirementMode::FinishCurrent, RetirementMode::Cancel] {
        retirement_scenario(
            true,
            openworker_sdk::Storage::Postgres {
                connection_env: "OW_PG_TEST_URL".into(),
                schema: format!("ow_retire_{}", openworker_runtime::id("x")),
                ca_file: None,
                loopback_plaintext: true,
            },
            mode,
        )
        .await;
    }
}
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_retired_child_keeps_its_final_events_in_the_original_run_replay_set() {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path().canonicalize().unwrap();
    let work = root.join("work");
    std::fs::create_dir(&work).unwrap();
    let h = host(&root, false).await;
    let team = h
        .new_team("lead", &work, TeamOptions::new(["builder"]))
        .await
        .unwrap();
    let child = plan(
        json!([{"name":"team_post_board","arguments":{"message":"@lead complete child evidence"}}]),
    );
    let prompt = plan(json!([
        {"name":"team_spawn","arguments":{"agent_type":"builder","agent_id":"ephemeral-child","message":child}},
        {"name":"team_wait_agent","arguments":{"agent_ids":["ephemeral-child"],"timeout_ms":10000}},
        {"name":"team_manage","arguments":{"action":"retire","agent_id":"ephemeral-child"}}
    ]));
    let mut stream = team.lead().run_streamed(&prompt).await.unwrap();
    // Deliberately consume no stream pages until after retirement. A fresh live
    // roster alone would lose this child's final events at the first read.
    tokio::time::timeout(Duration::from_secs(30), async {
        loop {
            if team.snapshot().await.unwrap()["roster"]["instances"]["ephemeral-child"]["phase"]
                == "closed"
            {
                break;
            }
            tokio::time::sleep(Duration::from_millis(25)).await;
        }
    })
    .await
    .expect("Child must retire before stream consumption");
    let mut kinds = std::collections::BTreeSet::new();
    tokio::time::timeout(Duration::from_secs(20), async {
        while let Some(event) = stream.next().await {
            if let RunEvent::TeamActivity {
                agent_id, event, ..
            } = event
            {
                if agent_id == "ephemeral-child" {
                    kinds.insert(event.kind);
                }
            }
        }
    })
    .await
    .unwrap();
    assert_eq!(stream.finish().await.unwrap().status, RunStatus::Completed);
    assert!(kinds.contains("team_instance_created"), "{kinds:?}");
    assert!(kinds.contains("turn_completed"), "{kinds:?}");
    assert!(kinds.contains("session_closed"), "{kinds:?}");
    h.shutdown().await.unwrap();
}
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
#[ignore = "requires isolated OW_PG_TEST_URL"]
async fn postgres_external_cancel_retains_priority_during_graceful_retirement() {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path().canonicalize().unwrap();
    let work = root.join("work");
    std::fs::create_dir(&work).unwrap();
    let schema = format!("ow_ctl_{}", openworker_runtime::id("x"));
    let storage = openworker_sdk::Storage::Postgres {
        connection_env: "OW_PG_TEST_URL".into(),
        schema: schema.clone(),
        ca_file: None,
        loopback_plaintext: true,
    };
    storage.migrate().unwrap();
    let h = host_storage(&root, false, storage).await;
    let mut config =
        openworker_store_postgres::Config::new(std::env::var("OW_PG_TEST_URL").unwrap(), schema);
    config.tls = openworker_store_postgres::Tls::LoopbackPlaintext;
    let api = openworker_store_postgres::PostgresStore::open(config).unwrap();
    let team = h
        .new_team("lead", &work, TeamOptions::new(["builder"]))
        .await
        .unwrap();
    let child=team.spawn("child","builder","builder-one",&plan(json!([{"name":"team_wait_agent","arguments":{"agent_ids":["lead"],"timeout_ms":60000}}]))).await.unwrap();
    let sid = child["instance"]["session_id"].as_str().unwrap();
    let snapshot = tokio::time::timeout(Duration::from_secs(20), async {
        loop {
            if let Ok(s) = h.client().snapshot(sid).await {
                if s["turn"]["status"] == "waiting_agents" {
                    break s;
                }
            }
            tokio::time::sleep(Duration::from_millis(25)).await;
        }
    })
    .await
    .unwrap();
    team.retire("retire", "builder-one", RetirementMode::FinishCurrent)
        .await
        .unwrap();
    api.submit_control(openworker_runtime::control::Command {
        session_id: sid.into(),
        request_id: "external-cancel".into(),
        turn_id: snapshot["turn"]["id"].as_str().unwrap().into(),
        actor: "trusted-test-api".into(),
        action: openworker_runtime::control::Action::Cancel,
    })
    .unwrap();
    tokio::time::timeout(Duration::from_secs(20), async {
        loop {
            if team.snapshot().await.unwrap()["roster"]["instances"]["builder-one"]["phase"]
                == "closed"
            {
                break;
            }
            tokio::time::sleep(Duration::from_millis(25)).await;
        }
    })
    .await
    .expect("Retirement must not hide the saved external control");
    assert_eq!(
        h.client().snapshot(sid).await.unwrap()["turn"]["status"],
        "cancelled"
    );
    assert_eq!(
        api.control_receipt(sid, "external-cancel").unwrap().status,
        openworker_runtime::control::Status::Applied
    );
    h.shutdown().await.unwrap();
}
