#![cfg(all(feature = "test-support", target_os = "linux"))]
//! Reviewer regressions over the real host, registry, journal and SQL adapters.
#[path = "support/tool.rs"]
mod tool;
use openworker_runtime::store::{StateStore, Store};
use openworker_sdk::{
    agents::*, ClientOptions, HarnessClient, HostConfig, HostProcessConfig, RestartPolicy,
};
use serde_json::{json, Value};
use std::{path::PathBuf, time::Duration};

async fn connect(config: &HostConfig, process: bool) -> Harness {
    let c = if process {
        HarnessClient::managed_host(
            HostProcessConfig {
                program: config.runner.clone(),
                host: config.clone(),
                restart: RestartPolicy::default(),
            },
            ClientOptions::default(),
        )
        .await
    } else {
        HarnessClient::embedded_host(config.clone(), tool::registry(), ClientOptions::default())
            .await
    }
    .unwrap();
    Harness::connect(c).await.unwrap()
}

fn edit_store(config: &HostConfig, f: impl FnOnce(&mut dyn StateStore)) {
    match &config.storage {
        Storage::Sqlite => f(&mut Store::open(&config.state_dir).unwrap()),
        Storage::Postgres {
            connection_env,
            schema,
            ..
        } => {
            let mut pg = openworker_store_postgres::Config::new(
                std::env::var(connection_env).unwrap(),
                schema.clone(),
            );
            pg.tls = openworker_store_postgres::Tls::LoopbackPlaintext;
            let mut db = openworker_store_postgres::PostgresStore::open(pg).unwrap();
            db.claim_sdk_host(vec!["assistant".into(), "lead".into(), "worker".into()])
                .unwrap();
            f(&mut db);
        }
    }
}

async fn done(c: &HarnessClient, sid: &str, submission: &str) -> Value {
    tokio::time::timeout(Duration::from_secs(90), async {
        loop {
            let r = c
                .request(
                    "run/wait",
                    json!({"session_id":sid,"submission_id":submission,"wait_ms":100}),
                )
                .await
                .unwrap();
            if r["status"] != "running" {
                return r;
            }
        }
    })
    .await
    .unwrap()
}

async fn exercise(postgres: bool, process: bool) {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path().canonicalize().unwrap();
    let work = root.join("work");
    std::fs::create_dir(&work).unwrap();
    let storage = if postgres {
        Storage::Postgres {
            connection_env: "OW_PG_TEST_URL".into(),
            schema: openworker_runtime::id("ow_sdk_reg"),
            ca_file: None,
            loopback_plaintext: true,
        }
    } else {
        Storage::Sqlite
    };
    storage.migrate().unwrap();
    let config = HostConfig {
        desktop: Default::default(),
        state_dir: root.join("state"),
        runner: PathBuf::from(env!("CARGO_BIN_EXE_sdk-test-host")),
        storage,
        coworkers: vec![],
        sandbox: Sandbox::Off,
        scripted: true,
        telemetry: None,
        agents: vec![
            Agent::new("assistant")
                .instructions("Use tools.")
                .tool("sdk.probe")
                .interrupt_on(Vec::<String>::new()),
            Agent::new("lead")
                .instructions("Coordinate.")
                .tool("sdk.probe")
                .subagent(
                    Agent::new("worker")
                        .instructions("Work.")
                        .tool("sdk.probe")
                        .approvals(Approvals::Never),
                ),
        ],
    };
    let h = connect(&config, process).await;
    let s = h.session("assistant", &work).await.unwrap();
    let sid = s.id().to_owned();
    let reference = s.reference().clone();
    let c = h.client();

    // A rejected competitor must leave the live bundle byte-for-byte intact.
    let manifest = config
        .state_dir
        .join("agents/assistant/coworker/manifest.md");
    let before = std::fs::read(&manifest).unwrap();
    let mut competitor = config.clone();
    competitor.agents[0] = competitor.agents[0]
        .clone()
        .instructions("Conflicting instructions.");
    assert!(
        HarnessClient::embedded_host(competitor, tool::registry(), ClientOptions::default())
            .await
            .is_err()
    );
    assert_eq!(std::fs::read(&manifest).unwrap(), before);

    let input = "TEST_RESOURCE_GRANTS [{\"name\":\"sdk__probe\",\"arguments\":{\"block\":false}}]";
    let first = s.run_with_submission("first", input).await.unwrap();
    assert_eq!(first.status, RunStatus::Completed);
    let started = c
        .request(
            "run/start",
            json!({"session_id":sid,"submission_id":"first","text":input}),
        )
        .await
        .unwrap();
    assert_eq!(started["duplicate"], true);
    assert!(c
        .request(
            "run/start",
            json!({"session_id":sid,"submission_id":"first","text":"different"})
        )
        .await
        .is_err());
    // A later interrupted run must not replace the older completed result.
    let question = "TEST_RESOURCE_GRANTS [{\"name\":\"ask_user\",\"arguments\":{\"question\":\"Continue?\",\"options\":[\"Yes\"]}}]";
    let second = s.run_with_submission("second", question).await.unwrap();
    assert_eq!(second.status, RunStatus::Interrupted);
    let old = done(c, &sid, "first").await;
    assert_eq!(old["status"], "completed");
    assert_eq!(old["cursor"], first.cursor);
    assert_eq!(old["final_output"], json!(first.final_output));
    assert!(old["interruptions"].as_array().unwrap().is_empty());
    assert!(c
        .request(
            "run/wait",
            json!({"session_id":sid,"submission_id":"unknown"})
        )
        .await
        .is_err());
    let reserved = h.session("assistant", &work).await.unwrap();
    let reserved_sid = reserved.id().to_owned();
    h.shutdown().await.unwrap();

    // Count pagination AND byte pagination. These are synthetic progress events,
    // not fake turn/approval outcomes; the real engine completes below.
    edit_store(&config, |db| {
        // Simulate a crash after the durable start receipt, before turn admission.
        let cursor = db.cursor(&reserved_sid).unwrap();
        db.append_event(
            &reserved_sid,
            "agent_run_state",
            json!({
                "submission_id":"reserved", "text":"TEST_RESOURCE_GRANTS []",
                "turn":null, "cursors":{&reserved_sid:cursor}
            }),
        )
        .unwrap();
        for n in 0..550 {
            db.append_event(&sid, "regression_progress", json!({"n":n}))
                .unwrap();
        }
        for _ in 0..3 {
            db.append_event(
                &sid,
                "regression_large",
                json!({"text":"x".repeat(260_000)}),
            )
            .unwrap();
        }
    });
    let h = connect(&config, process).await;
    let s = h.resume(reference).await.unwrap();
    let c = h.client();
    assert!(c
        .request(
            "run/wait",
            json!({"session_id":reserved_sid,"submission_id":"reserved","wait_ms":0})
        )
        .await
        .is_err());
    c.request("run/start", json!({"session_id":reserved_sid,"submission_id":"reserved","text":"TEST_RESOURCE_GRANTS []"})).await.unwrap();
    assert_eq!(
        done(c, &reserved_sid, "reserved").await["status"],
        "completed"
    );
    let pending = s.resume().await.unwrap();
    assert_eq!(pending.interruptions[0].id, second.interruptions[0].id);
    s.respond(&pending.interruptions[0], Response::Answer("Yes".into()))
        .await
        .unwrap();
    assert_eq!(s.resume().await.unwrap().status, RunStatus::Completed);
    assert_eq!(done(c, &sid, "first").await["cursor"], first.cursor);

    let mut cursor = 0;
    let mut received = Vec::new();
    let mut pages = 0;
    let mut saw_auto = false;
    let end = loop {
        let page = c
            .request(
                "run/wait",
                json!({"session_id":sid,"submission_id":"second","wait_ms":0,
            "max_events":1000,"cursors":{&sid:cursor}}),
            )
            .await
            .unwrap();
        assert_eq!(page["status"], "completed");
        for e in page["events"].as_array().unwrap() {
            received.push(e["event"]["event_id"].as_i64().unwrap());
            if e["summary"]["type"] == "auto_approved" {
                assert_eq!(e["event"]["data"]["origin"], "app_policy");
                assert_eq!(e["summary"]["tool"], "sdk__probe");
                saw_auto = true;
            }
        }
        pages += 1;
        cursor = page["cursors"][&sid].as_i64().unwrap();
        if page["more_events"] == false {
            break page["cursor"].as_i64().unwrap();
        }
        assert!(pages < 20);
    };
    assert!(saw_auto, "approval attribution survives a host restart");
    assert!(
        pages >= 3,
        "both adapter count and byte limits require continuation"
    );
    assert_eq!(cursor, end);
    let mut expected = Vec::new();
    let mut after = 0;
    while after < end {
        let page = c
            .request("event/read", json!({"session_id":sid,"after":after}))
            .await
            .unwrap();
        for e in page["events"].as_array().unwrap() {
            let id = e["event_id"].as_i64().unwrap();
            if id > end {
                break;
            }
            expected.push(id);
            after = id;
        }
    }
    assert_eq!(received, expected, "no journal events lost or duplicated");
    assert_eq!(
        std::fs::read_to_string(work.join("proof.txt")).unwrap(),
        "called\n"
    );

    // Fill all four slots of the shared worker runtime using other teams.
    let mut blockers = Vec::new();
    for n in 0..4 {
        let team = h.session("lead", &work).await.unwrap();
        let worker = team.reference().team.as_ref().unwrap().workers[0]
            .session_id
            .clone();
        c.request("turn/start", json!({"session_id":worker,"submission_id":format!("block-{n}"),
            "text":"TEST_RESOURCE_GRANTS [{\"name\":\"sdk__probe\",\"arguments\":{\"block\":true}}]"})).await.unwrap();
        blockers.push(worker);
    }
    let lead = h.session("lead", &work).await.unwrap();
    let team = lead.team().unwrap();
    let worker = &lead.reference().team.as_ref().unwrap().workers[0].session_id;
    let item = team
        .create_item(ItemSpec::new("Waiting for capacity", "Produce proof"))
        .await
        .unwrap();
    let assignment = format!(
        "TEST_RESOURCE_GRANTS {}",
        json!([{"name":"team_assign","arguments":{
        "item_id":item["id"],"expected_revision":item["revision"],"session_id":worker}}])
    );
    c.request(
        "run/start",
        json!({"session_id":lead.id(),"submission_id":"assignment","text":assignment}),
    )
    .await
    .unwrap();
    tokio::time::timeout(Duration::from_secs(30), async {
        loop {
            if c.snapshot(lead.id()).await.unwrap()["turn"]["status"] == "completed" {
                break;
            }
            tokio::time::sleep(Duration::from_millis(50)).await;
        }
    })
    .await
    .unwrap();
    let pending = c
        .request(
            "run/wait",
            json!({"session_id":lead.id(),"submission_id":"assignment","wait_ms":1500}),
        )
        .await
        .unwrap();
    assert_eq!(
        pending["status"], "running",
        "queued work is not completed work"
    );
    assert!(c.snapshot(worker).await.unwrap()["turn"].is_null());
    assert_eq!(
        c.team_snapshot(team.id()).await.unwrap()["pending_work"],
        true
    );
    team.cancel().await.unwrap();
    for worker in blockers {
        let snap = c.snapshot(&worker).await.unwrap();
        c.request(
            "turn/cancel",
            json!({"session_id":worker,"turn_id":snap["turn"]["id"]}),
        )
        .await
        .unwrap();
    }
    h.shutdown().await.unwrap();
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn sqlite_agent_regressions_embedded_and_stdio() {
    for process in [false, true] {
        exercise(false, process).await;
    }
}
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
#[ignore = "requires isolated OW_PG_TEST_URL"]
async fn postgres_agent_regressions_embedded_and_stdio() {
    for process in [false, true] {
        exercise(true, process).await;
    }
}
