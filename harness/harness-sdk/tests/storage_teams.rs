#![cfg(all(feature = "test-support", target_os = "linux"))]
#[path = "support/tool.rs"]
mod tool;
use openworker_runtime::{
    team_service::{Command, Create},
    teams::{Member, Role},
};
use openworker_sdk::*;
use serde_json::{json, Value};
use std::{path::PathBuf, time::Duration};

async fn client(config: &HostConfig, process: bool) -> HarnessClient {
    if process {
        HarnessClient::managed_host(
            HostProcessConfig {
                program: config.runner.clone(),
                host: config.clone(),
                restart: RestartPolicy::default(),
            },
            ClientOptions::default(),
        )
        .await
        .unwrap()
    } else {
        HarnessClient::embedded_host(config.clone(), tool::registry(), ClientOptions::default())
            .await
            .unwrap()
    }
}
async fn command(c: &HarnessClient, id: &str, key: &str, operation: &str, args: Value) -> Value {
    c.team_command(
        id,
        Command {
            request_id: key.into(),
            operation: operation.into(),
            arguments: args,
        },
    )
    .await
    .unwrap()
}
async fn wait(c: &HarnessClient, sid: &str, p: impl Fn(&Value) -> bool) -> Value {
    tokio::time::timeout(Duration::from_secs(90), async {
        loop {
            let v = c.snapshot(sid).await.unwrap();
            if p(&v) {
                return v;
            }
            assert!(
                !matches!(
                    v["turn"]["status"].as_str(),
                    Some("failed" | "truncated" | "interrupted")
                ),
                "Unexpected terminal state: {v}"
            );
            tokio::time::sleep(Duration::from_millis(100)).await;
        }
    })
    .await
    .unwrap()
}
async fn exercise(postgres: bool, process: bool) {
    let root = tempfile::tempdir().unwrap();
    let root = root.path().canonicalize().unwrap();
    std::fs::create_dir(root.join("work")).unwrap();
    let mut profiles = vec![];
    for role in ["lead", "worker"] {
        let bundle = root.join(role);
        std::fs::create_dir_all(bundle.join("coworker")).unwrap();
        std::fs::write(bundle.join("coworker/manifest.md"),format!("---\nid: {role}\nname: {role}\nteam_role: {role}\nplanning: true\n---\nUse the shared team board.")).unwrap();
        std::fs::write(
            bundle.join("tools.json"),
            r#"{"version":3,"tools":["test.probe"]}"#,
        )
        .unwrap();
        profiles.push(CoworkerConfig {
            bundle,
            model: "team-fixture".into(),
            provider: if role == "lead" {
                ProviderKind::Openai
            } else {
                ProviderKind::Anthropic
            },
            servers: None,
            tool_policy: None,
            runtime_policy: None,
        });
    }
    let storage = if postgres {
        Storage::Postgres {
            connection_env: "OW_PG_TEST_URL".into(),
            schema: format!("ow_sdk_{}", openworker_runtime::id("x").replace('-', "_")),
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
        coworkers: profiles,
        sandbox: Sandbox::Off,
        scripted: true,
        telemetry: None,
        agents: vec![],
    };
    let c = client(&config, process).await;
    let mut competing = config.clone();
    if postgres {
        competing.state_dir = root.join("competitor");
    }
    assert!(
        HarnessClient::embedded(competing, tool::registry(), ClientOptions::default())
            .await
            .is_err(),
        "A second owner must not start against the same storage"
    );
    assert_eq!(c.info()["capabilities"]["durable_teams"], true);
    let mut sessions = vec![];
    for role in ["lead", "worker"] {
        sessions.push(
            c.create_session(RuntimeSessionOptions {
                name: role.into(),
                coworker: role.into(),
                workspace: root.join("work"),
                model: None,
                permissions: None,
            })
            .await
            .unwrap(),
        );
    }
    let lead = &sessions[0];
    let worker = &sessions[1];
    let created = c
        .create_team(Create {
            request_id: "team".into(),
            name: "SDK team".into(),
            members: vec![
                Member {
                    session_id: lead.clone(),
                    name: "Lead".into(),
                    role: Role::Lead,
                },
                Member {
                    session_id: worker.clone(),
                    name: "Worker".into(),
                    role: Role::Worker,
                },
            ],
        })
        .await
        .unwrap();
    let team = created["team_id"].as_str().unwrap();
    let item=command(&c,team,"item","team_create_item",json!({"title":"Produce proof","description":"Write once","criteria":"One durable proof","dependencies":[]})).await["data"]["item"]["id"].as_str().unwrap().to_owned();
    let task = format!(
        "TEST_RESOURCE_GRANTS {}",
        json!([
            {"name":"update_plan","arguments":{"expected_revision":0,"explanation":"Coordinate the assignment","steps":[{"id":"proof","description":"Produce verified proof","status":"in_progress"}]}},
            {"name":"ask_user","arguments":{"question":"Proceed with the synthetic assignment?","options":["Yes","No"]}},
            {"name":"team_assign","arguments":{"item_id":item,"expected_revision":1,"session_id":worker}}
        ])
    );
    let mut progress = c.subscribe();
    c.attach(worker, 0).await.unwrap();
    c.start_turn(lead, "assign", &task).await.unwrap();
    let question = wait(&c, lead, |v| {
        !v["interactions"].as_array().unwrap().is_empty()
    })
    .await;
    assert_eq!(question["plan"]["revision"], 1);
    assert_eq!(question["interactions"][0]["kind"], "question");
    c.answer(
        lead,
        question["interactions"][0]["id"].as_str().unwrap(),
        "Yes",
    )
    .await
    .unwrap();
    let waiting = wait(&c, worker, |v| {
        !v["interactions"].as_array().unwrap().is_empty()
    })
    .await;
    let approval = waiting["interactions"][0]["id"]
        .as_str()
        .unwrap()
        .to_owned();
    tokio::time::timeout(Duration::from_secs(10), async {
        loop {
            if let Notification::Event(e) = progress.recv().await.unwrap() {
                if e.session_id == *worker && e.kind == "interaction_required" {
                    break;
                }
            }
        }
    })
    .await
    .unwrap();
    assert!(!root.join("work/team-proof.txt").exists());
    let c = if process {
        let mut state = c.connection();
        let initial = state.borrow().clone();
        let generation = match initial {
            ConnectionState::Ready {
                generation,
                process_id: Some(pid),
            } => {
                assert!(tokio::process::Command::new("kill")
                    .args(["-KILL", &pid.to_string()])
                    .status()
                    .await
                    .unwrap()
                    .success());
                generation
            }
            _ => panic!("Expected owned child"),
        };
        tokio::time::timeout(Duration::from_secs(90), async {
            loop {
                if matches!(*state.borrow(),ConnectionState::Ready{generation:g,..} if g>generation)
                {
                    break;
                }
                state.changed().await.unwrap();
            }
        })
        .await
        .unwrap();
        c
    } else {
        c.shutdown().await.unwrap();
        drop(c);
        client(&config, false).await
    };
    assert_eq!(
        c.snapshot(worker).await.unwrap()["interactions"][0]["id"],
        approval
    );
    assert_eq!(c.snapshot(lead).await.unwrap()["plan"]["revision"], 1);
    assert_eq!(
        c.snapshot(worker).await.unwrap()["session"]["model_provider"],
        "anthropic"
    );
    assert!(c
        .request("skills/list", json!({"session_id":worker}))
        .await
        .unwrap()["skills"]
        .is_array());
    c.approve(worker, &approval, ApprovalDecision::Once)
        .await
        .unwrap();
    wait(&c, worker, |v| v["turn"]["status"] == "completed").await;
    tokio::time::timeout(Duration::from_secs(90), async {
        loop {
            if c.team_snapshot(team).await.unwrap()["board"]["items"][&item]["status"] == "done" {
                break;
            }
            tokio::time::sleep(Duration::from_millis(100)).await;
        }
    })
    .await
    .unwrap();
    wait(&c, lead, |v| v["turn"]["status"] == "completed").await;
    assert_eq!(
        std::fs::read_to_string(root.join("work/team-proof.txt")).unwrap(),
        "TEAM_PROOF\n"
    );
    let events = c.team_events(team, 0).await.unwrap();
    assert!(events["events"]
        .as_array()
        .unwrap()
        .iter()
        .any(|e| e["type"] == "message_posted"));
    assert!(c
        .request("history/read", json!({"session_id":worker,"after":0}))
        .await
        .unwrap()["items"]
        .as_array()
        .unwrap()
        .iter()
        .any(|v| v["item"]["runtime_notice"] == "team_delivery"));
    let spare=command(&c,team,"cancel-item","team_create_item",json!({"title":"Cancelled work","description":"Must not write","criteria":"No effect","dependencies":[]})).await["data"]["item"]["id"].as_str().unwrap().to_owned();
    command(
        &c,
        team,
        "cancel-assignment",
        "team_assign",
        json!({"item_id":spare,"expected_revision":1,"session_id":worker}),
    )
    .await;
    wait(&c, worker, |v| {
        !v["interactions"].as_array().unwrap().is_empty()
    })
    .await;
    command(&c, team, "cancel-team", "team_cancel", json!({})).await;
    wait(&c, worker, |v| v["turn"]["status"] == "cancelled").await;
    assert_eq!(
        std::fs::read_to_string(root.join("work/team-proof.txt")).unwrap(),
        "TEAM_PROOF\n"
    );
    assert_eq!(
        c.team_snapshot(team).await.unwrap()["board"]["cancelled"],
        true
    );
    c.shutdown().await.unwrap();
    println!(
        "{}",
        json!({"storage":if postgres{"postgres"}else{"sqlite"},"transport":if process{"stdio"}else{"embedded"},"accepted_item":"done","effects":1,"approval_restart":true,"child_killed":process,"plan_retained":true,"question_answered":true,"progress_stream":true,"team_cancelled":true})
    );
}
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn sqlite_embedded_and_stdio_teams() {
    for process in [false, true] {
        exercise(false, process).await;
    }
}
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
#[ignore = "requires isolated OW_PG_TEST_URL"]
async fn postgres_embedded_and_stdio_teams() {
    for process in [false, true] {
        exercise(true, process).await;
    }
}
