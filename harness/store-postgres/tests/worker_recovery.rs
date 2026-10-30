#![cfg(all(feature = "test-support", target_os = "linux"))]
#[path = "support/concurrency.rs"]
mod concurrency;
#[path = "support/draining.rs"]
mod draining;
#[path = "support/load.rs"]
mod load;
use openworker_runtime::control::{Action, Binding, Command as Control, Status as ControlStatus};
use openworker_store_postgres::{Config, PostgresStore, SubmissionStatus, Tls, TurnSubmission};
use serde_json::{json, Value};
use std::{path::Path, process::Stdio, time::Duration};
use tokio::{
    io::{AsyncBufReadExt, AsyncWriteExt, BufReader},
    process::{Child, ChildStdin, ChildStdout, Command},
};
struct Worker {
    child: Child,
    input: Option<ChildStdin>,
    output: BufReader<ChildStdout>,
    id: u64,
}
fn service(root: &Path, schema: &str, name: &str, sid: &str) -> Child {
    Command::new(env!("CARGO_BIN_EXE_pg-worker-fixture"))
        .arg(root)
        .args([schema, name, sid, "service"])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::inherit())
        .kill_on_drop(true)
        .spawn()
        .unwrap()
}
async fn stop_service(child: &mut Child) {
    // Signal only our still-owned child, then wait for bounded graceful shutdown.
    assert!(child.try_wait().unwrap().is_none());
    assert!(Command::new("kill")
        .args(["-INT", &child.id().unwrap().to_string()])
        .status()
        .await
        .unwrap()
        .success());
    assert!(tokio::time::timeout(Duration::from_secs(15), child.wait())
        .await
        .unwrap()
        .unwrap()
        .success());
}
fn plan(action: &str) -> String {
    format!(
        "TEST_RESOURCE_GRANTS {}",
        json!([{"name":"test__probe","arguments":{"action":action}}])
    )
}
fn service_fixture() -> (tempfile::TempDir, Config, PostgresStore) {
    let root = tempfile::tempdir().unwrap();
    std::fs::create_dir_all(root.path().join("bundle/coworker")).unwrap();
    std::fs::write(
        root.path().join("bundle/coworker/manifest.md"),
        "---\nid: pg-test\nname: Service test\n---\nUse the tool.",
    )
    .unwrap();
    std::fs::write(
        root.path().join("bundle/tools.json"),
        r#"{"version":3,"tools":["test.probe"]}"#,
    )
    .unwrap();
    let mut config = Config::new(
        std::env::var("OW_PG_TEST_URL").unwrap(),
        format!("ow_service_{}", uuid::Uuid::new_v4().simple()),
    );
    config.tls = Tls::LoopbackPlaintext;
    PostgresStore::migrate(&config).unwrap();
    let api = PostgresStore::open(config.clone()).unwrap();
    (root, config, api)
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
#[ignore = "requires isolated OW_PG_TEST_URL and native Linux"]
async fn production_worker_plans_survive_replacement_and_cross_api_approval() {
    let (root, config, api) = service_fixture();
    std::fs::write(
        root.path().join("bundle/coworker/manifest.md"),
        "---\nid: pg-test\nname: Planner test\nplanning: true\n---\nPlan then use the tool.",
    )
    .unwrap();
    std::fs::create_dir(root.path().join("work")).unwrap();
    let other_api = PostgresStore::open(config.clone()).unwrap();
    let mut seeder = Worker::start(root.path(), &config.schema, "seed", None).await;
    let created = seeder
        .rpc(
            "session/create",
            json!({"name":"Plan test","coworker":"pg-test","workspace":root.path().join("work")}),
        )
        .await;
    let sid = created["session"]["id"].as_str().unwrap();
    api.submit_turn(TurnSubmission {
        session_id: sid.into(), submission_id: "planned-task".into(),
        text: format!("TEST_RESOURCE_GRANTS {}", json!([
            {"name":"update_plan","arguments":{"expected_revision":0,"explanation":"Inspect first","steps":[{"id":"inspect","description":"Write one test marker","status":"in_progress"}]}},
            {"name":"test__probe","arguments":{"action":"write_once"}},
            {"name":"update_plan","arguments":{"expected_revision":1,"explanation":"Marker observed","steps":[{"id":"inspect","description":"Write one test marker","status":"completed"}]}}
        ])),
    }).unwrap();
    seeder.kill().await;
    let mut a = service(root.path(), &config.schema, "a", sid);
    let pending = tokio::time::timeout(Duration::from_secs(20), async {
        loop {
            let snap = other_api.read_snapshot(sid).unwrap();
            if !snap["interactions"].as_array().unwrap().is_empty() {
                break snap;
            }
            assert!(a.try_wait().unwrap().is_none());
            tokio::time::sleep(Duration::from_millis(50)).await;
        }
    })
    .await
    .unwrap();
    assert_eq!(pending["plan"]["revision"], 1);
    assert_eq!(pending["interactions"][0]["call"]["name"], "test__probe");
    assert!(!root.path().join("work/proof.txt").exists());
    stop_service(&mut a).await;
    assert_eq!(
        other_api.read_snapshot(sid).unwrap()["plan"],
        pending["plan"]
    );
    other_api
        .submit_control(decision(sid, &pending, "approve-marker", "once"))
        .unwrap();
    let mut b = service(root.path(), &config.schema, "b", sid);
    terminal_receipt(&api, sid, "planned-task", SubmissionStatus::Completed).await;
    stop_service(&mut b).await;
    let final_snapshot = other_api.read_snapshot(sid).unwrap();
    assert_eq!(final_snapshot["plan"]["revision"], 2);
    assert_eq!(final_snapshot["plan"]["steps"][0]["status"], "completed");
    let saved = api.inspect(sid).unwrap();
    assert!(saved.approval_grants.is_empty());
    assert_eq!(saved.interactions.len(), 1);
    let events = other_api.read_events(sid, 0).unwrap();
    assert_eq!(
        events.iter().filter(|e| e.kind == "plan_updated").count(),
        2
    );
    assert_eq!(
        std::fs::read_to_string(root.path().join("work/proof.txt"))
            .unwrap()
            .lines()
            .count(),
        1
    );
}
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
#[ignore = "requires isolated OW_PG_TEST_URL and native Linux"]
async fn production_workers_bound_capacity_renew_and_shutdown_without_replaying_effects() {
    let (root, config, api) = service_fixture();
    let mut seeder = Worker::start(root.path(), &config.schema, "seed", None).await;
    let mut sessions = vec![];
    for i in 0..3 {
        let workspace = root.path().join(format!("work{i}"));
        std::fs::create_dir(&workspace).unwrap();
        let created=seeder.rpc("session/create",json!({"name":format!("session{i}"),"coworker":"pg-test","workspace":workspace,"permissions":{"mode":"bypass"}})).await;
        let sid = created["session"]["id"].as_str().unwrap().to_owned();
        api.submit_turn(TurnSubmission {
            session_id: sid.clone(),
            submission_id: "effect".into(),
            text: plan("write_wait"),
        })
        .unwrap();
        sessions.push(sid);
    }
    seeder.kill().await;
    let mut a = service(root.path(), &config.schema, "a", &sessions[0]);
    let mut b = service(root.path(), &config.schema, "b", &sessions[0]);
    let files = || {
        (0..3)
            .filter(|i| root.path().join(format!("work{i}/proof.txt")).exists())
            .count()
    };
    tokio::time::timeout(Duration::from_secs(25), async {
        while files() != 2 {
            assert!(a.try_wait().unwrap().is_none());
            assert!(b.try_wait().unwrap().is_none());
            tokio::time::sleep(Duration::from_millis(100)).await;
        }
    })
    .await
    .unwrap();
    // More than two lease periods. A nonrenewing worker would be stolen here.
    tokio::time::sleep(Duration::from_secs(7)).await;
    assert_eq!(
        files(),
        2,
        "third session bypassed configured worker capacity"
    );
    let active = sessions
        .iter()
        .find(|sid| api.inspect(sid).unwrap().turn.is_some())
        .unwrap();
    let turn = api.inspect(active).unwrap().turn.unwrap();
    api.submit_control(Control {
        session_id: active.clone(),
        request_id: "cancel".into(),
        turn_id: turn.id,
        actor: "operator".into(),
        action: Action::Cancel,
    })
    .unwrap();
    tokio::time::timeout(Duration::from_secs(15), async {
        while files() != 3 {
            tokio::time::sleep(Duration::from_millis(100)).await;
        }
    })
    .await
    .unwrap();
    stop_service(&mut a).await;
    stop_service(&mut b).await;
    for (i, sid) in sessions.iter().enumerate() {
        let saved = api.inspect(sid).unwrap();
        assert_eq!(saved.turn.unwrap().status, "interrupted");
        assert_eq!(
            std::fs::read_to_string(root.path().join(format!("work{i}/proof.txt")))
                .unwrap()
                .lines()
                .count(),
            1
        );
        api.submit_turn(TurnSubmission {
            session_id: sid.clone(),
            submission_id: "inspect".into(),
            text: plan("inspect"),
        })
        .unwrap();
    }
    let mut c = service(root.path(), &config.schema, "c", &sessions[0]);
    for sid in &sessions {
        terminal_receipt(&api, sid, "inspect", SubmissionStatus::Completed).await;
    }
    stop_service(&mut c).await;
    for i in 0..3 {
        assert_eq!(
            std::fs::read_to_string(root.path().join(format!("work{i}/proof.txt")))
                .unwrap()
                .lines()
                .count(),
            1
        );
    }
}
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
#[ignore = "requires isolated OW_PG_TEST_URL and native Linux"]
async fn production_worker_loses_database_and_replacement_recovers_unknown_once() {
    let (root, config, api) = service_fixture();
    std::fs::create_dir(root.path().join("work")).unwrap();
    let mut seeder = Worker::start(root.path(), &config.schema, "seed", None).await;
    let created=seeder.rpc("session/create",json!({"name":"one","coworker":"pg-test","workspace":root.path().join("work"),"permissions":{"mode":"bypass"}})).await;
    let sid = created["session"]["id"].as_str().unwrap();
    api.submit_turn(TurnSubmission {
        session_id: sid.into(),
        submission_id: "effect".into(),
        text: plan("write_wait"),
    })
    .unwrap();
    seeder.kill().await;
    let mut a = service(root.path(), &config.schema, "a", sid);
    tokio::time::timeout(Duration::from_secs(20), async {
        while !root.path().join("work/proof.txt").exists() {
            assert!(a.try_wait().unwrap().is_none());
            tokio::time::sleep(Duration::from_millis(100)).await;
        }
    })
    .await
    .unwrap();
    tokio::task::block_in_place(|| {
        let mut db = postgres::Client::connect(&config.connection, postgres::NoTls).unwrap();
        db.batch_execute(&format!("SET search_path={},pg_catalog", config.schema))
            .unwrap();
        let owner: String = db
            .query_one("SELECT owner FROM sessions WHERE id=$1", &[&sid])
            .unwrap()
            .get(0);
        let app = format!("ow-store-{owner}");
        let rows = db
            .query(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE application_name=$1",
                &[&app],
            )
            .unwrap();
        assert_eq!(rows.len(), 1);
    });
    assert!(!tokio::time::timeout(Duration::from_secs(15), a.wait())
        .await
        .unwrap()
        .unwrap()
        .success());
    let mut b = service(root.path(), &config.schema, "b", sid);
    terminal_receipt(&api, sid, "effect", SubmissionStatus::Interrupted).await;
    assert_eq!(
        api.inspect(sid)
            .unwrap()
            .history
            .iter()
            .filter(|v| v["type"] == "function_call_output"
                && v.to_string().contains("harness_recovery"))
            .count(),
        1
    );
    api.submit_turn(TurnSubmission {
        session_id: sid.into(),
        submission_id: "inspect".into(),
        text: plan("inspect"),
    })
    .unwrap();
    terminal_receipt(&api, sid, "inspect", SubmissionStatus::Completed).await;
    stop_service(&mut b).await;
    assert_eq!(
        std::fs::read_to_string(root.path().join("work/proof.txt"))
            .unwrap()
            .lines()
            .count(),
        1
    );
}
fn decision(sid: &str, pending: &Value, id: &str, response: &str) -> Control {
    let i = &pending["interactions"][0];
    Control {
        session_id: sid.into(),
        request_id: id.into(),
        turn_id: i["turn_id"].as_str().unwrap().into(),
        actor: "principal-example".into(),
        action: Action::Response {
            interaction_id: i["id"].as_str().unwrap().into(),
            response: response.into(),
            binding: Binding {
                bundle_digest: i["bundle_digest"].as_str().unwrap().into(),
                schema_digest: i["schema_digest"].as_str().unwrap().into(),
                policy_digest: i["policy_digest"].as_str().unwrap().into(),
                resource_policy_digest: i["resource_policy_digest"].as_str().unwrap().into(),
            },
        },
    }
}
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
#[ignore = "requires isolated OW_PG_TEST_URL and native Linux"]
async fn production_worker_parks_approval_reclaims_for_answer_and_keeps_session_grant() {
    let (root, config, api) = service_fixture();
    std::fs::create_dir(root.path().join("work")).unwrap();
    let mut seeder = Worker::start(root.path(), &config.schema, "seed", None).await;
    let created = seeder
        .rpc(
            "session/create",
            json!({"name":"approval","coworker":"pg-test","workspace":root.path().join("work")}),
        )
        .await;
    let sid = created["session"]["id"].as_str().unwrap();
    api.submit_turn(TurnSubmission {
        session_id: sid.into(),
        submission_id: "one".into(),
        text: plan("write_once"),
    })
    .unwrap();
    seeder.kill().await;
    let mut a = service(root.path(), &config.schema, "a", sid);
    let pending = tokio::time::timeout(Duration::from_secs(20), async {
        loop {
            let s = api.inspect(sid).unwrap();
            if s.interactions.iter().any(|i| i.response.is_none()) {
                break json!({"interactions":s.interactions});
            }
            assert!(a.try_wait().unwrap().is_none());
            tokio::time::sleep(Duration::from_millis(100)).await;
        }
    })
    .await
    .unwrap();
    assert!(!root.path().join("work/proof.txt").exists());
    let first_observation = api.inspect(sid).unwrap().turn.unwrap().observation.unwrap();
    assert_eq!(first_observation.segments, 1);
    stop_service(&mut a).await;
    let decision = decision(sid, &pending, "allow-session", "session");
    api.submit_control(decision.clone()).unwrap();
    let mut b = service(root.path(), &config.schema, "b", sid);
    terminal_receipt(&api, sid, "one", SubmissionStatus::Completed).await;
    let after_resume = api.inspect(sid).unwrap();
    let resumed = after_resume
        .turn
        .as_ref()
        .unwrap()
        .observation
        .as_ref()
        .unwrap();
    assert_eq!(resumed.segments, 2);
    assert_eq!(
        resumed.first_started_at_ms,
        first_observation.first_started_at_ms
    );
    assert_eq!(
        resumed.last_segment.trace,
        first_observation.last_segment.trace
    );
    assert_ne!(
        resumed.last_segment.span,
        first_observation.last_segment.span
    );
    let interaction = &after_resume.interactions[0];
    assert!(interaction.requested_at_ms.is_some());
    assert!(interaction.resolved_at_ms >= interaction.requested_at_ms);
    assert_eq!(
        api.control_receipt(sid, "allow-session").unwrap().status,
        ControlStatus::Applied
    );
    api.submit_turn(TurnSubmission {
        session_id: sid.into(),
        submission_id: "two".into(),
        text: plan("write_once"),
    })
    .unwrap();
    terminal_receipt(&api, sid, "two", SubmissionStatus::Completed).await;
    stop_service(&mut b).await;
    let records: Vec<Value> = std::fs::read_to_string(root.path().join("observations.jsonl"))
        .unwrap()
        .lines()
        .map(|s| serde_json::from_str(s).unwrap())
        .collect();
    assert!(records.iter().any(|r| r["operation"] == "agent.execute"
        && r["links"]
            .as_array()
            .is_some_and(|links| links.contains(&json!(first_observation.last_segment)))));
    assert_eq!(
        records
            .iter()
            .filter(|r| r["operation"] == "approval.wait" && r["timing_known"] == true)
            .count(),
        1
    );
    assert_eq!(
        std::fs::read_to_string(root.path().join("work/proof.txt"))
            .unwrap()
            .lines()
            .count(),
        2
    );
    assert_eq!(api.inspect(sid).unwrap().approval_grants.len(), 1);
    assert_eq!(
        api.submit_control(decision).unwrap().status,
        ControlStatus::Applied
    );
}
async fn terminal_receipt(api: &PostgresStore, sid: &str, id: &str, status: SubmissionStatus) {
    tokio::time::timeout(Duration::from_secs(25), async {
        loop {
            let receipt = api.submission(sid, id).unwrap();
            if receipt.status == status {
                break;
            }
            assert!(
                !matches!(
                    receipt.status,
                    SubmissionStatus::Failed
                        | SubmissionStatus::Cancelled
                        | SubmissionStatus::Truncated
                ),
                "unexpected terminal receipt {receipt:?}"
            );
            tokio::time::sleep(Duration::from_millis(30)).await;
        }
    })
    .await
    .unwrap();
}
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
#[ignore = "requires isolated OW_PG_TEST_URL and native Linux"]
async fn remote_controls_survive_crash_grant_session_access_and_cancel_unknown_effect() {
    let root = tempfile::tempdir().unwrap();
    std::fs::create_dir_all(root.path().join("bundle/coworker")).unwrap();
    std::fs::create_dir(root.path().join("work")).unwrap();
    std::fs::create_dir(root.path().join("work2")).unwrap();
    std::fs::write(
        root.path().join("bundle/coworker/manifest.md"),
        "---\nid: pg-test\nname: Control test\n---\nUse the test tool.",
    )
    .unwrap();
    std::fs::write(
        root.path().join("bundle/tools.json"),
        r#"{"version":3,"tools":["test.probe"]}"#,
    )
    .unwrap();
    let schema = format!("ow_remote_{}", uuid::Uuid::new_v4().simple());
    let mut config = Config::new(std::env::var("OW_PG_TEST_URL").unwrap(), schema.clone());
    config.tls = Tls::LoopbackPlaintext;
    PostgresStore::migrate(&config).unwrap();
    let api_a = PostgresStore::open(config.clone()).unwrap();
    let api_b = PostgresStore::open(config).unwrap();
    let mut a = Worker::start(root.path(), &schema, "a", None).await;
    let created = a
        .rpc(
            "session/create",
            json!({"name":"Control","coworker":"pg-test","workspace":root.path().join("work")}),
        )
        .await;
    let sid = created["session"]["id"].as_str().unwrap();
    let plan = |action: &str| {
        format!(
            "TEST_RESOURCE_GRANTS {}",
            json!([{"name":"test__probe","arguments":{"action":action}}])
        )
    };
    a.rpc(
        "turn/start",
        json!({"session_id":sid,"submission_id":"one","text":plan("write_once")}),
    )
    .await;
    let pending = a.pending(sid).await;
    a.kill().await;
    let grant = decision(sid, &pending, "session-grant", "session");
    assert_eq!(
        api_a.submit_control(grant.clone()).unwrap().status,
        ControlStatus::Pending
    );
    assert_eq!(
        api_b.submit_control(grant.clone()).unwrap().status,
        ControlStatus::Pending
    );
    tokio::time::sleep(Duration::from_millis(3200)).await;
    let mut b = Worker::start_mode(root.path(), &schema, "b", Some(sid), true).await;
    terminal_receipt(&api_b, sid, "one", SubmissionStatus::Completed).await;
    assert_eq!(
        api_b.control_receipt(sid, "session-grant").unwrap().status,
        ControlStatus::Applied
    );
    assert_eq!(api_b.inspect(sid).unwrap().approval_grants.len(), 1);
    assert_eq!(
        api_b.submit_control(grant).unwrap().status,
        ControlStatus::Applied
    );
    // Same tool/action group reuses the persisted session grant, without a second prompt.
    let run = api_a
        .submit_turn(TurnSubmission {
            session_id: sid.into(),
            submission_id: "effect".into(),
            text: plan("write_wait"),
        })
        .unwrap();
    tokio::time::timeout(Duration::from_secs(20), async {
        loop {
            if std::fs::read_to_string(root.path().join("work/proof.txt"))
                .unwrap()
                .lines()
                .count()
                == 2
            {
                break;
            }
            tokio::time::sleep(Duration::from_millis(30)).await;
        }
    })
    .await
    .unwrap();
    let cancel = Control {
        session_id: sid.into(),
        request_id: "cancel-effect".into(),
        turn_id: run.turn_id,
        actor: "principal-example".into(),
        action: Action::Cancel,
    };
    api_b.submit_control(cancel.clone()).unwrap();
    terminal_receipt(&api_a, sid, "effect", SubmissionStatus::Interrupted).await;
    assert_eq!(
        api_a.control_receipt(sid, "cancel-effect").unwrap().status,
        ControlStatus::Applied
    );
    assert_eq!(
        api_a.submit_control(cancel).unwrap().status,
        ControlStatus::Applied
    );
    let history = api_a.inspect(sid).unwrap().history;
    assert_eq!(
        history
            .iter()
            .filter(|v| v["type"] == "function_call_output"
                && v["output"]
                    .as_str()
                    .is_some_and(|s| s.contains("harness_cancellation")
                        && s.contains("tool_outcome_unknown")))
            .count(),
        1
    );
    assert_eq!(
        std::fs::read_to_string(root.path().join("work/proof.txt"))
            .unwrap()
            .lines()
            .count(),
        2
    );
    // A free-standing question is not a reusable tool approval.
    api_a.submit_turn(TurnSubmission{session_id:sid.into(),submission_id:"question".into(),text:format!("TEST_RESOURCE_GRANTS {}",json!([{"name":"ask_user","arguments":{"question":"Continue?","options":["Yes","No"]}}]))}).unwrap();
    let pending = b.pending(sid).await;
    assert!(api_b
        .submit_control(decision(sid, &pending, "answer", "Maybe"))
        .is_err());
    api_b
        .submit_control(decision(sid, &pending, "answer", "Yes"))
        .unwrap();
    terminal_receipt(&api_a, sid, "question", SubmissionStatus::Completed).await;
    assert_eq!(api_a.inspect(sid).unwrap().approval_grants.len(), 1);
    // Allow once on a different session must not mint a grant there.
    let second = b
        .rpc(
            "session/create",
            json!({"name":"Once","coworker":"pg-test","workspace":root.path().join("work2")}),
        )
        .await;
    let sid2 = second["session"]["id"].as_str().unwrap();
    api_a
        .submit_turn(TurnSubmission {
            session_id: sid2.into(),
            submission_id: "once".into(),
            text: plan("write_once"),
        })
        .unwrap();
    let pending = b.pending(sid2).await;
    api_b
        .submit_control(decision(sid2, &pending, "once-decision", "once"))
        .unwrap();
    terminal_receipt(&api_a, sid2, "once", SubmissionStatus::Completed).await;
    assert!(api_a.inspect(sid2).unwrap().approval_grants.is_empty());
    api_a
        .submit_turn(TurnSubmission {
            session_id: sid2.into(),
            submission_id: "denied".into(),
            text: plan("write_once"),
        })
        .unwrap();
    let pending = b.pending(sid2).await;
    api_b
        .submit_control(decision(sid2, &pending, "deny-decision", "deny"))
        .unwrap();
    terminal_receipt(&api_a, sid2, "denied", SubmissionStatus::Completed).await;
    // Cancellation while still awaiting approval has a known no-dispatch result.
    let queued = api_a
        .submit_turn(TurnSubmission {
            session_id: sid2.into(),
            submission_id: "before-dispatch".into(),
            text: plan("write_once"),
        })
        .unwrap();
    b.pending(sid2).await;
    api_b
        .submit_control(Control {
            session_id: sid2.into(),
            request_id: "cancel-before".into(),
            turn_id: queued.turn_id,
            actor: "principal-example".into(),
            action: Action::Cancel,
        })
        .unwrap();
    terminal_receipt(&api_a, sid2, "before-dispatch", SubmissionStatus::Cancelled).await;
    assert_eq!(
        std::fs::read_to_string(root.path().join("work2/proof.txt"))
            .unwrap()
            .lines()
            .count(),
        1
    );
    b.close().await;
}
impl Worker {
    async fn start(root: &Path, schema: &str, name: &str, sid: Option<&str>) -> Self {
        Self::start_mode(root, schema, name, sid, false).await
    }
    async fn start_mode(
        root: &Path,
        schema: &str,
        name: &str,
        sid: Option<&str>,
        consume: bool,
    ) -> Self {
        Self::start_binary(
            Path::new(env!("CARGO_BIN_EXE_pg-worker-fixture")),
            root,
            schema,
            name,
            sid,
            consume,
        )
        .await
    }
    async fn start_binary(
        program: &Path,
        root: &Path,
        schema: &str,
        name: &str,
        sid: Option<&str>,
        consume: bool,
    ) -> Self {
        let mut command = Command::new(program);
        command.arg(root).arg(schema).arg(name);
        if let Some(sid) = sid {
            command.arg(sid);
            if consume {
                command.arg("consume");
            }
        }
        let mut child = command
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit())
            .kill_on_drop(true)
            .spawn()
            .unwrap();
        let mut w = Self {
            input: child.stdin.take(),
            output: BufReader::new(child.stdout.take().unwrap()),
            child,
            id: 0,
        };
        w.rpc("initialize", json!({"protocol_version":"0.1"})).await;
        w
    }
    async fn rpc(&mut self, method: &str, params: Value) -> Value {
        self.id += 1;
        let id = self.id;
        self.input
            .as_mut()
            .unwrap()
            .write_all(
                format!(
                    "{}\n",
                    json!({"jsonrpc":"2.0","id":id,"method":method,"params":params})
                )
                .as_bytes(),
            )
            .await
            .unwrap();
        tokio::time::timeout(Duration::from_secs(30), async {
            loop {
                let mut line = String::new();
                assert!(
                    self.output.read_line(&mut line).await.unwrap() > 0,
                    "worker exited"
                );
                let v: Value = serde_json::from_str(&line).unwrap();
                if v["id"] == id {
                    assert!(v.get("error").is_none(), "{v}");
                    return v["result"].clone();
                }
            }
        })
        .await
        .unwrap()
    }
    async fn pending(&mut self, sid: &str) -> Value {
        tokio::time::timeout(Duration::from_secs(20), async {
            loop {
                let v = self.rpc("session/attach", json!({"session_id":sid})).await;
                if !v["interactions"].as_array().unwrap().is_empty() {
                    return v;
                }
                tokio::time::sleep(Duration::from_millis(20)).await;
            }
        })
        .await
        .unwrap()
    }
    async fn kill(&mut self) {
        self.child.kill().await.unwrap();
        self.child.wait().await.unwrap();
    }
    async fn close(mut self) {
        self.input.take();
        tokio::time::timeout(Duration::from_secs(10), self.child.wait())
            .await
            .unwrap()
            .unwrap();
    }
}
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
#[ignore = "requires isolated OW_PG_TEST_URL and native Linux"]
async fn api_accepts_while_worker_is_dead_and_replacement_consumes_fifo() {
    let root = tempfile::tempdir().unwrap();
    std::fs::create_dir_all(root.path().join("bundle/coworker")).unwrap();
    std::fs::create_dir(root.path().join("work")).unwrap();
    std::fs::write(
        root.path().join("bundle/coworker/manifest.md"),
        "---\nid: pg-test\nname: Queue test\n---\nUse the test tool.",
    )
    .unwrap();
    std::fs::write(
        root.path().join("bundle/tools.json"),
        r#"{"version":3,"tools":["test.probe"]}"#,
    )
    .unwrap();
    let schema = format!("ow_ingress_{}", uuid::Uuid::new_v4().simple());
    let mut config = Config::new(std::env::var("OW_PG_TEST_URL").unwrap(), schema.clone());
    config.tls = Tls::LoopbackPlaintext;
    PostgresStore::migrate(&config).unwrap();
    let mut original = Worker::start(root.path(), &schema, "original", None).await;
    let created = original
        .rpc(
            "session/create",
            json!({"name":"Queue","coworker":"pg-test","workspace":root.path().join("work")}),
        )
        .await;
    let sid = created["session"]["id"].as_str().unwrap();
    original.kill().await;
    let api_a = PostgresStore::open(config.clone()).unwrap();
    let api_b = PostgresStore::open(config.clone()).unwrap();
    let make = |id: &str, action: &str| TurnSubmission {
        session_id: sid.into(),
        submission_id: id.into(),
        text: format!(
            "TEST_RESOURCE_GRANTS {}",
            json!([{"name":"test__probe","arguments":{"action":action}}])
        ),
    };
    let first = api_a.submit_turn(make("one", "write_once")).unwrap();
    let second = api_b.submit_turn(make("two", "inspect")).unwrap();
    assert_eq!(api_b.submit_turn(make("one", "write_once")).unwrap(), first);
    assert_eq!(first.status, SubmissionStatus::Queued);
    assert!(api_b.inspect(sid).unwrap().history.is_empty());
    tokio::time::sleep(Duration::from_millis(3200)).await;
    let mut replacement =
        Worker::start_mode(root.path(), &schema, "replacement", Some(sid), true).await;
    let pending = replacement.pending(sid).await;
    assert_eq!(pending["turn"]["id"], first.turn_id);
    assert_eq!(
        api_a.submission(sid, "two").unwrap().status,
        SubmissionStatus::Queued
    );
    let iid = &pending["interactions"][0]["id"];
    replacement
        .rpc(
            "interaction/respond",
            json!({"session_id":sid,"interaction_id":iid,"decision":"once"}),
        )
        .await;
    let next = replacement.pending(sid).await;
    assert_eq!(next["turn"]["id"], second.turn_id);
    assert_eq!(
        api_a.submission(sid, "one").unwrap().status,
        SubmissionStatus::Completed
    );
    replacement.rpc("interaction/respond",json!({"session_id":sid,"interaction_id":next["interactions"][0]["id"],"decision":"once"})).await;
    tokio::time::timeout(Duration::from_secs(20), async {
        while api_a.submission(sid, "two").unwrap().status != SubmissionStatus::Completed {
            tokio::time::sleep(Duration::from_millis(50)).await;
        }
    })
    .await
    .unwrap();
    let saved = api_b.inspect(sid).unwrap();
    assert_eq!(
        saved.history.iter().filter(|v| v["role"] == "user").count(),
        2
    );
    assert_eq!(
        std::fs::read_to_string(root.path().join("work/proof.txt")).unwrap(),
        "one real side effect\n"
    );
    assert_eq!(
        api_b.submit_turn(make("one", "write_once")).unwrap().status,
        SubmissionStatus::Completed
    );
    // A completed duplicate never feeds another model turn or repeats the tool.
    assert_eq!(
        api_b.inspect(sid).unwrap().history.len(),
        saved.history.len()
    );
    replacement.close().await;
}
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
#[ignore = "requires isolated OW_PG_TEST_URL and native Linux"]
async fn real_worker_deaths_preserve_approval_then_deliver_unknown_without_replaying_write() {
    let root = tempfile::tempdir().unwrap();
    std::fs::create_dir_all(root.path().join("bundle/coworker")).unwrap();
    std::fs::create_dir(root.path().join("work")).unwrap();
    std::fs::write(
        root.path().join("bundle/coworker/manifest.md"),
        "---\nid: pg-test\nname: PG test\n---\nUse the test tool.",
    )
    .unwrap();
    std::fs::write(
        root.path().join("bundle/tools.json"),
        r#"{"version":3,"tools":["test.probe"]}"#,
    )
    .unwrap();
    let schema = format!("ow_worker_{}", uuid::Uuid::new_v4().simple());
    let mut config = Config::new(std::env::var("OW_PG_TEST_URL").unwrap(), schema.clone());
    config.tls = Tls::LoopbackPlaintext;
    PostgresStore::migrate(&config).unwrap();
    let mut a = Worker::start(root.path(), &schema, "worker-a", None).await;
    let created = a
        .rpc(
            "session/create",
            json!({"name":"Recovery","coworker":"pg-test","workspace":root.path().join("work")}),
        )
        .await;
    let sid = created["session"]["id"].as_str().unwrap();
    let plan = format!(
        "TEST_RESOURCE_GRANTS {}",
        json!([{"name":"test__probe","arguments":{"action":"write_wait"}}])
    );
    a.rpc(
        "turn/start",
        json!({"session_id":sid,"submission_id":"original","text":plan}),
    )
    .await;
    let first = a.pending(sid).await;
    let iid = first["interactions"][0]["id"].as_str().unwrap();
    a.kill().await;
    tokio::time::sleep(Duration::from_millis(3200)).await;
    let mut b = Worker::start(root.path(), &schema, "worker-b", Some(sid)).await;
    let pending = b.pending(sid).await;
    assert_eq!(pending["interactions"][0]["id"], iid);
    b.rpc(
        "interaction/respond",
        json!({"session_id":sid,"interaction_id":iid,"decision":"once"}),
    )
    .await;
    tokio::time::timeout(Duration::from_secs(10), async {
        while !root.path().join("work/proof.txt").exists() {
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
    })
    .await
    .unwrap();
    b.kill().await;
    tokio::time::sleep(Duration::from_millis(3200)).await;
    let mut c = Worker::start(root.path(), &schema, "worker-c", Some(sid)).await;
    let snap = c.rpc("session/attach", json!({"session_id":sid})).await;
    assert_eq!(snap["turn"]["status"], "interrupted");
    let observer = PostgresStore::open(config.clone()).unwrap();
    let saved = observer.inspect(sid).unwrap();
    assert_eq!(
        saved
            .history
            .iter()
            .filter(|v| v["type"] == "function_call_output"
                && v["output"]
                    .as_str()
                    .is_some_and(|s| s.contains("tool_outcome_unknown")))
            .count(),
        1
    );
    assert_eq!(
        std::fs::read_to_string(root.path().join("work/proof.txt")).unwrap(),
        "one real side effect\n"
    );
    // Explicit continuation for this S1 test. Automatic scheduling/continuation is S3.
    let inspection = format!(
        "TEST_RESOURCE_GRANTS {}",
        json!([{"name":"test__probe","arguments":{"action":"inspect"}}])
    );
    c.rpc(
        "turn/start",
        json!({"session_id":sid,"submission_id":"inspect-after-recovery","text":inspection}),
    )
    .await;
    let pending = c.pending(sid).await;
    c.rpc("interaction/respond",json!({"session_id":sid,"interaction_id":pending["interactions"][0]["id"],"decision":"once"})).await;
    tokio::time::timeout(Duration::from_secs(10), async {
        loop {
            let v = c.rpc("session/attach", json!({"session_id":sid})).await;
            if v["turn"]["status"] == "completed" {
                break;
            }
            tokio::time::sleep(Duration::from_millis(20)).await;
        }
    })
    .await
    .unwrap();
    assert!(observer
        .inspect(sid)
        .unwrap()
        .history
        .iter()
        .any(|v| v["type"] == "function_call_output"
            && v["output"]
                .as_str()
                .is_some_and(|s| s.contains("observed") && s.contains("one real side effect"))));
    assert_eq!(
        std::fs::read_to_string(root.path().join("work/proof.txt")).unwrap(),
        "one real side effect\n"
    );
    c.close().await;
}
