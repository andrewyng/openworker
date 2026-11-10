#![cfg(all(feature = "test-support", any(windows, target_os = "linux")))]
#[path = "support/tool.rs"]
mod tool;
use openworker_sdk::*;
use serde_json::{json, Value};
use std::{path::PathBuf, time::Duration};
struct Fixture {
    root: tempfile::TempDir,
    config: RuntimeConfig,
}
impl Fixture {
    fn new() -> Self {
        Self::with_planning(false)
    }
    fn with_planning(planning: bool) -> Self {
        let root = tempfile::tempdir().unwrap();
        let bundle = root.path().join("bundle");
        std::fs::create_dir_all(bundle.join("coworker")).unwrap();
        std::fs::create_dir(root.path().join("work")).unwrap();
        std::fs::write(
            bundle.join("coworker/manifest.md"),
            format!("---\nid: sdk\nname: SDK test\nplanning: {planning}\n---\nUse the test tool."),
        )
        .unwrap();
        std::fs::write(
            bundle.join("tools.json"),
            r#"{"version":3,"tools":["sdk.probe"]}"#,
        )
        .unwrap();
        let mut config = RuntimeConfig::new(
            root.path().join("state"),
            bundle,
            PathBuf::from(env!("CARGO_BIN_EXE_sdk-test-host")),
        );
        config.sandbox = Sandbox::Off;
        config.scripted = true;
        Self { root, config }
    }
    async fn client(&self, process: bool) -> HarnessClient {
        let options = ClientOptions {
            request_timeout: Duration::from_secs(60),
            shutdown_timeout: Duration::from_secs(3),
        };
        if process {
            HarnessClient::managed_stdio(
                ProcessConfig {
                    program: self.config.runner.clone(),
                    runtime: self.config.clone(),
                    restart: RestartPolicy {
                        max_restarts: 2,
                        initial_delay: Duration::from_millis(50),
                        max_delay: Duration::from_millis(100),
                    },
                },
                options,
            )
            .await
            .unwrap()
        } else {
            HarnessClient::embedded(self.config.clone(), tool::registry(), options)
                .await
                .unwrap()
        }
    }
    async fn session(&self, c: &HarnessClient) -> String {
        c.create_session(RuntimeSessionOptions {
            name: "SDK".into(),
            coworker: "sdk".into(),
            workspace: self.root.path().join("work"),
            model: None,
            permissions: None,
        })
        .await
        .unwrap()
    }
}
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn plans_stream_and_survive_restart_on_both_backends_without_approving_actions() {
    for process in [false, true] {
        let f = Fixture::with_planning(true);
        let c = f.client(process).await;
        assert_eq!(c.info()["capabilities"]["durable_plans"], true);
        let sid = f.session(&c).await;
        let mut events = c.subscribe();
        c.attach(&sid, 0).await.unwrap();
        let steps =
            json!([{"id":"probe","description":"Inspect the workspace","status":"in_progress"}]);
        let prompt = format!(
            "TEST_RESOURCE_GRANTS {}",
            json!([
                {"name":"update_plan","arguments":{"expected_revision":0,"explanation":"First investigate","steps":steps}},
                {"name":"sdk__probe","arguments":{"block":false}},
                {"name":"update_plan","arguments":{"expected_revision":1,"explanation":"Verified the marker","steps":[{"id":"probe","description":"Inspect the workspace","status":"completed"}]}}
            ])
        );
        c.start_turn(&sid, "planned-task", &prompt).await.unwrap();
        let waiting = wait(&c, &sid, |v| {
            !v["interactions"].as_array().unwrap().is_empty()
        })
        .await;
        assert_eq!(waiting["plan"]["revision"], 1);
        assert_eq!(waiting["interactions"][0]["call"]["name"], "sdk__probe");
        assert!(!f.root.path().join("work/proof.txt").exists());
        // The plan arrived through the same notification path used by applications.
        tokio::time::timeout(Duration::from_secs(5), async {
            loop {
                if let Notification::Event(value) = events.recv().await.unwrap() {
                    if value.kind == "plan_updated" {
                        break;
                    }
                }
            }
        })
        .await
        .unwrap();
        let plan_before = waiting["plan"].clone();
        let interaction = waiting["interactions"][0]["id"]
            .as_str()
            .unwrap()
            .to_owned();
        c.shutdown().await.unwrap();
        drop(c);
        let c = f.client(process).await;
        let recovered = c.snapshot(&sid).await.unwrap();
        assert_eq!(recovered["plan"], plan_before);
        assert_eq!(recovered["interactions"][0]["id"], interaction);
        c.approve(&sid, &interaction, ApprovalDecision::Once)
            .await
            .unwrap();
        let done = wait(&c, &sid, |v| v["turn"]["status"] == "completed").await;
        assert_eq!(done["plan"]["revision"], 2);
        assert_eq!(done["plan"]["steps"][0]["status"], "completed");
        assert_eq!(
            std::fs::read_to_string(f.root.path().join("work/proof.txt")).unwrap(),
            "called\n"
        );
        let replay = c
            .request("event/read", json!({"session_id":sid,"after":0}))
            .await
            .unwrap();
        assert_eq!(
            replay["events"]
                .as_array()
                .unwrap()
                .iter()
                .filter(|e| e["type"] == "plan_updated")
                .count(),
            2
        );
        c.shutdown().await.unwrap();
    }
}
async fn wait(c: &HarnessClient, sid: &str, p: impl Fn(&Value) -> bool) -> Value {
    tokio::time::timeout(Duration::from_secs(30), async {
        loop {
            let v = c.snapshot(sid).await.unwrap();
            if p(&v) {
                return v;
            }
            tokio::time::sleep(Duration::from_millis(30)).await;
        }
    })
    .await
    .unwrap()
}
fn plan(block: bool) -> String {
    format!(
        "TEST_RESOURCE_GRANTS {}",
        json!([{"name":"sdk__probe","arguments":{"block":block}}])
    )
}
async fn kill(c: &HarnessClient) {
    let pid = match *c.connection().borrow() {
        ConnectionState::Ready {
            process_id: Some(pid),
            ..
        } => pid,
        _ => panic!("not a child"),
    };
    #[cfg(windows)]
    let status = std::process::Command::new("taskkill.exe")
        .args(["/F", "/PID", &pid.to_string()])
        .output()
        .unwrap()
        .status;
    #[cfg(not(windows))]
    let status = std::process::Command::new("kill")
        .args(["-KILL", &pid.to_string()])
        .status()
        .unwrap();
    assert!(status.success());
}
async fn generation(c: &HarnessClient, n: u64) {
    let mut state = c.connection();
    tokio::time::timeout(Duration::from_secs(20), async {
        loop {
            if matches!(*state.borrow(),ConnectionState::Ready {generation,..} if generation>=n) {
                return;
            }
            state.changed().await.unwrap();
        }
    })
    .await
    .unwrap();
}
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn same_sessions_approvals_results_and_shutdown_on_both_backends() {
    for process in [false, true] {
        let f = Fixture::new();
        let c = f.client(process).await;
        let mut events = c.subscribe();
        let sid = f.session(&c).await;
        assert_eq!(c.info()["protocol_version"], "0.1");
        let snap = c.attach(&sid, 0).await.unwrap();
        c.acknowledge(&sid, snap["cursor"].as_i64().unwrap())
            .await
            .unwrap();
        assert!(c.acknowledge(&sid, i64::MAX).await.is_err());
        c.start_turn(&sid, "stable-submission", &plan(false))
            .await
            .unwrap();
        let waiting = wait(&c, &sid, |v| {
            !v["interactions"].as_array().unwrap().is_empty()
        })
        .await;
        let iid = waiting["interactions"][0]["id"].as_str().unwrap();
        c.approve(&sid, iid, ApprovalDecision::Session)
            .await
            .unwrap();
        wait(&c, &sid, |v| v["turn"]["status"] == "completed").await;
        assert_eq!(
            std::fs::read_to_string(f.root.path().join("work/proof.txt")).unwrap(),
            "called\n"
        );
        assert_eq!(
            c.start_turn(&sid, "stable-submission", &plan(false))
                .await
                .unwrap()["duplicate"],
            true
        );
        assert_eq!(
            c.request("permissions/list", json!({"session_id":sid}))
                .await
                .unwrap()["grants"]
                .as_array()
                .unwrap()
                .len(),
            1
        );
        assert!(events.try_recv().is_ok());
        let question = format!(
            "TEST_RESOURCE_GRANTS {}",
            json!([{"name":"ask_user","arguments":{"question":"Choose a color","options":["Blue","Green"]}}])
        );
        c.start_turn(&sid, "question", &question).await.unwrap();
        let pending = wait(&c, &sid, |v| {
            !v["interactions"].as_array().unwrap().is_empty()
        })
        .await;
        c.answer(
            &sid,
            pending["interactions"][0]["id"].as_str().unwrap(),
            "Blue",
        )
        .await
        .unwrap();
        wait(&c, &sid, |v| v["turn"]["status"] == "completed").await;
        let turn = c.start_turn(&sid, "cancel", &plan(true)).await.unwrap();
        tokio::time::timeout(Duration::from_secs(20), async {
            while std::fs::read_to_string(f.root.path().join("work/proof.txt"))
                .unwrap()
                .lines()
                .count()
                < 2
            {
                tokio::time::sleep(Duration::from_millis(20)).await;
            }
        })
        .await
        .unwrap();
        c.cancel_turn(&sid, turn["turn"]["id"].as_str().unwrap())
            .await
            .unwrap();
        wait(&c, &sid, |v| {
            matches!(
                v["turn"]["status"].as_str(),
                Some("interrupted" | "cancelled")
            )
        })
        .await;
        c.shutdown().await.unwrap();
        c.shutdown().await.unwrap();
        assert_eq!(*c.connection().borrow(), ConnectionState::Closed);
        assert_eq!(
            c.start_turn(&sid, "later", "hi").await.unwrap_err(),
            Error::Closed
        );
        drop(c);
        let again = f.client(process).await;
        assert_eq!(again.attach(&sid, 0).await.unwrap()["session"]["id"], sid);
        again.shutdown().await.unwrap();
    }
}
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn process_restart_restores_approval_identity_and_never_replays_started_effects() {
    let f = Fixture::new();
    let c = f.client(true).await;
    let mut events = c.subscribe();
    let sid = f.session(&c).await;
    c.attach(&sid, 0).await.unwrap();
    c.start_turn(&sid, "crash", &plan(true)).await.unwrap();
    let pending = wait(&c, &sid, |v| {
        !v["interactions"].as_array().unwrap().is_empty()
    })
    .await;
    let iid = pending["interactions"][0]["id"]
        .as_str()
        .unwrap()
        .to_string();
    c.acknowledge(&sid, pending["cursor"].as_i64().unwrap())
        .await
        .unwrap();
    c.request(
        "tool/control",
        json!({"session_id":sid,"tool":"sdk.probe","request":{"action":"enable"}}),
    )
    .await
    .unwrap();
    kill(&c).await;
    generation(&c, 2).await;
    assert_eq!(
        c.request(
            "tool/control",
            json!({"session_id":sid,"tool":"sdk.probe","request":{"action":"status"}})
        )
        .await
        .unwrap()["enabled"],
        false
    );
    assert_eq!(
        c.attach(&sid, 0).await.unwrap()["interactions"][0]["id"],
        iid
    );
    c.approve(&sid, &iid, ApprovalDecision::Once).await.unwrap();
    tokio::time::timeout(Duration::from_secs(20), async {
        while !f.root.path().join("work/proof.txt").exists() {
            tokio::time::sleep(Duration::from_millis(20)).await;
        }
    })
    .await
    .unwrap();
    kill(&c).await;
    generation(&c, 3).await;
    assert_eq!(
        c.attach(&sid, 0).await.unwrap()["turn"]["status"],
        "interrupted"
    );
    tokio::time::sleep(Duration::from_millis(200)).await;
    assert_eq!(
        std::fs::read_to_string(f.root.path().join("work/proof.txt")).unwrap(),
        "called\n"
    );
    let mut recovered = 0;
    while let Ok(e) = events.try_recv() {
        if matches!(e, Notification::Recovered { .. }) {
            recovered += 1;
        }
    }
    assert_eq!(recovered, 2);
    kill(&c).await;
    let mut state = c.connection();
    tokio::time::timeout(Duration::from_secs(20), async {
        while *state.borrow() != ConnectionState::Failed {
            state.changed().await.unwrap();
        }
    })
    .await
    .unwrap();
    assert_eq!(
        c.request("session/list", json!({})).await.unwrap_err(),
        Error::Unavailable
    );
    c.shutdown().await.unwrap();
}
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn in_flight_disconnect_is_unknown_and_not_retried() {
    let f = Fixture::new();
    let c = f.client(true).await;
    let sid = f.session(&c).await;
    c.attach(&sid, 0).await.unwrap();
    let marker = f.root.path().join("control-started");
    let other = c.clone();
    let path = marker.clone();
    let request = tokio::spawn(async move {
        other.request("tool/control",json!({"session_id":sid,"tool":"sdk.probe","request":{"action":"block","marker":path}})).await
    });
    tokio::time::timeout(Duration::from_secs(20), async {
        while !marker.exists() {
            tokio::time::sleep(Duration::from_millis(20)).await;
        }
    })
    .await
    .unwrap();
    kill(&c).await;
    assert_eq!(request.await.unwrap().unwrap_err(), Error::OutcomeUnknown);
    generation(&c, 2).await;
    assert_eq!(std::fs::read_to_string(marker).unwrap(), "started\n");
    c.shutdown().await.unwrap();
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn embedded_shutdown_revokes_unused_access_and_last_handle_drop_releases_child() {
    let f = Fixture::new();
    let registry = tool::registry();
    let probe = registry.native("sdk.probe").unwrap().clone();
    let c = HarnessClient::embedded(f.config.clone(), registry, ClientOptions::default())
        .await
        .unwrap();
    let sid = f.session(&c).await;
    c.request(
        "tool/control",
        json!({"session_id":sid,"tool":"sdk.probe","request":{"action":"enable"}}),
    )
    .await
    .unwrap();
    c.shutdown().await.unwrap();
    assert_eq!(
        probe
            .implementation
            .control(&sid, json!({"action":"status"}))
            .unwrap()["enabled"],
        false
    );
    drop(c);
    let child = f.client(true).await;
    let mut state = child.connection();
    drop(child);
    tokio::time::timeout(Duration::from_secs(20), async {
        while *state.borrow() != ConnectionState::Closed {
            state.changed().await.unwrap();
        }
    })
    .await
    .unwrap();
    let reopened = f.client(true).await;
    reopened.shutdown().await.unwrap();
}
#[tokio::test]
async fn malformed_startup_fails_and_does_not_leak_a_store_owner() {
    let f = Fixture::new();
    let mut config = f.config.clone();
    config.model = "sdk-malformed".into();
    assert!(HarnessClient::managed_stdio(
        ProcessConfig {
            program: config.runner.clone(),
            runtime: config,
            restart: RestartPolicy::default()
        },
        ClientOptions::default()
    )
    .await
    .is_err());
    let c = f.client(true).await;
    c.shutdown().await.unwrap();
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn shutdown_during_backoff_never_restarts() {
    let f = Fixture::new();
    let c = HarnessClient::managed_stdio(
        ProcessConfig {
            program: f.config.runner.clone(),
            runtime: f.config.clone(),
            restart: RestartPolicy {
                max_restarts: 3,
                initial_delay: Duration::from_secs(5),
                max_delay: Duration::from_secs(5),
            },
        },
        ClientOptions::default(),
    )
    .await
    .unwrap();
    let mut state = c.connection();
    kill(&c).await;
    tokio::time::timeout(Duration::from_secs(20), async {
        while !matches!(*state.borrow(), ConnectionState::Recovering { .. }) {
            state.changed().await.unwrap();
        }
    })
    .await
    .unwrap();
    tokio::time::timeout(Duration::from_secs(3), c.shutdown())
        .await
        .unwrap()
        .unwrap();
    assert_eq!(*state.borrow(), ConnectionState::Closed);
    let reopened = f.client(true).await;
    reopened.shutdown().await.unwrap();
}
