#![cfg(all(feature = "test-support", target_os = "linux"))]
#[path = "support/tool.rs"]
mod tool;
use openworker_sdk::{
    agents::*, ClientOptions, HarnessClient, HostConfig, HostProcessConfig, RestartPolicy,
};
use serde_json::json;
use std::{path::PathBuf, time::Duration};

async fn connect(config: &HostConfig, process: bool) -> Harness {
    let client = if process {
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
    Harness::connect(client).await.unwrap()
}

async fn exercise(postgres: bool, process: bool) {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path().canonicalize().unwrap();
    let work = root.join("work");
    std::fs::create_dir(&work).unwrap();
    let storage = if postgres {
        Storage::Postgres {
            connection_env: "OW_PG_TEST_URL".into(),
            schema: openworker_runtime::id("ow_dynamic"),
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
        agents: vec![],
    };
    let h = connect(&config, process).await;
    eprintln!("dynamic fixture: connected (process={process}, postgres={postgres})");
    assert!(h.list_agents().await.unwrap().is_empty());
    eprintln!("dynamic fixture: listed");
    assert!(h.session("missing", &work).await.is_err());
    eprintln!("dynamic fixture: rejected missing agent");
    assert_eq!(
        h.client()
            .request("connection/list", json!({}))
            .await
            .unwrap()["connections"],
        json!([])
    );
    h.set_model_provider(
        ModelProvider::chat_completions("Ollama").base_url("http://127.0.0.1:11434/v1"),
    )
    .await
    .unwrap();
    eprintln!("dynamic fixture: provider configured before agents");
    let definition = Agent::new("builder")
        .instructions("Use the probe.")
        .tool("sdk.probe");
    let a = h.create_agent(definition.clone()).await.unwrap();
    eprintln!("dynamic fixture: registered");
    let duplicate = h.create_agent(definition.clone()).await.unwrap();
    assert_eq!(a.id(), duplicate.id());
    let (left, right) = tokio::join!(
        h.create_agent(definition.clone()),
        h.create_agent(definition.clone())
    );
    assert_eq!(left.unwrap().id(), right.unwrap().id());
    assert!(h
        .create_agent(definition.clone().instructions("Changed instructions"))
        .await
        .is_err());
    let invalid = Agent::new("missing_tools")
        .instructions("Fail registration.")
        .tool("unknown.tool");
    assert!(h.create_agent(invalid).await.is_err());
    assert_eq!(h.list_agents().await.unwrap(), ["builder"]);
    let s = a.new_session_in(&work).await.unwrap();
    eprintln!("dynamic fixture: session created");
    let saved = s.reference().clone();
    let r = s
        .run(&format!(
            "TEST_RESOURCE_GRANTS {}",
            json!([{"name":"sdk__probe","arguments":{"block":false}}])
        ))
        .await
        .unwrap();
    assert_eq!(r.status, RunStatus::Interrupted);
    eprintln!("dynamic fixture: approval parked");
    assert!(!work.join("proof.txt").exists());
    // Adding another agent must not reset a parked approval or trigger its effect.
    h.create_agent(
        Agent::new("observer")
            .instructions("Observe.")
            .tool("sdk.probe"),
    )
    .await
    .unwrap();
    assert_eq!(
        s.snapshot().await.unwrap()["interactions"][0]["id"],
        r.interruptions[0].id
    );
    assert!(!work.join("proof.txt").exists());
    s.respond(
        &r.interruptions[0],
        Response::Approve(ApprovalDecision::Once),
    )
    .await
    .unwrap();
    let r = s.resume().await.unwrap();
    eprintln!("dynamic fixture: run resumed");
    assert_eq!(r.status, RunStatus::Completed);
    assert_eq!(
        std::fs::read_to_string(work.join("proof.txt")).unwrap(),
        "called\n"
    );
    h.client()
        .request(
            "connection/set",
            json!({"coworker":"builder","id":"primary","label":"fixture",
        "provider":"openai","api_key":"test-not-a-live-key","persistence":"memory"}),
        )
        .await
        .unwrap();
    let selected = a
        .new_session_with_model(
            &work,
            ModelChoice {
                connection: "primary".into(),
                model: "chosen-fixture".into(),
            },
        )
        .await
        .unwrap();
    assert_eq!(
        selected.snapshot().await.unwrap()["session"]["model"],
        "chosen-fixture"
    );
    let again = a.new_session_in(&work).await.unwrap();
    assert_ne!(
        again.snapshot().await.unwrap()["session"]["model"],
        "chosen-fixture"
    );
    h.shutdown().await.unwrap();
    eprintln!("dynamic fixture: shutdown");
    drop(s);
    drop(selected);
    drop(again);
    drop(a);
    drop(duplicate);
    drop(h);
    let h = connect(&config, process).await;
    eprintln!("dynamic fixture: restarted");
    assert_eq!(h.list_agents().await.unwrap(), ["builder", "observer"]);
    let a = h.create_agent(definition).await.unwrap();
    let restored = a.resume_session(saved).await.unwrap();
    assert_eq!(
        restored.snapshot().await.unwrap()["session"]["coworker"],
        "builder"
    );
    assert_eq!(
        std::fs::read_to_string(work.join("proof.txt")).unwrap(),
        "called\n"
    );
    // A freshly added agent's Only policy must be observed by the existing host task.
    let automatic = h
        .create_agent(
            Agent::new("automatic")
                .instructions("Probe.")
                .tool("sdk.probe")
                .interrupt_on([] as [&str; 0]),
        )
        .await
        .unwrap();
    let auto_session = automatic.new_session_in(&work).await.unwrap();
    let result = auto_session
        .run(&format!(
            "TEST_RESOURCE_GRANTS {}",
            json!([{"name":"sdk__probe","arguments":{"block":false}}])
        ))
        .await
        .unwrap();
    assert_eq!(result.status, RunStatus::Completed);
    h.shutdown().await.unwrap();
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn sqlite_embedded() {
    tokio::time::timeout(Duration::from_secs(90), exercise(false, false))
        .await
        .unwrap();
}
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn sqlite_managed() {
    tokio::time::timeout(Duration::from_secs(90), exercise(false, true))
        .await
        .unwrap();
}
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
#[ignore = "requires disposable OW_PG_TEST_URL"]
async fn postgres_embedded() {
    tokio::time::timeout(Duration::from_secs(90), exercise(true, false))
        .await
        .unwrap();
}
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
#[ignore = "requires disposable OW_PG_TEST_URL"]
async fn postgres_managed() {
    tokio::time::timeout(Duration::from_secs(90), exercise(true, true))
        .await
        .unwrap();
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn live_configuration_requires_explicit_model_without_calling_provider() {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path().canonicalize().unwrap();
    std::fs::create_dir(root.join("work")).unwrap();
    let config = HostConfig {
        desktop: Default::default(),
        state_dir: root.join("state"),
        runner: PathBuf::from(env!("CARGO_BIN_EXE_sdk-test-host")),
        storage: Storage::Sqlite,
        coworkers: vec![],
        agents: vec![],
        sandbox: Sandbox::Off,
        scripted: false,
        telemetry: None,
    };
    let h = connect(&config, false).await;
    let agent = h
        .create_agent(
            Agent::new("builder")
                .instructions("Build.")
                .tool("sdk.probe"),
        )
        .await
        .unwrap();
    let error = match agent.new_session_in(root.join("work")).await {
        Ok(_) => panic!("No implicit fallback model should be selected"),
        Err(error) => error,
    };
    assert!(error.to_string().contains("default model"));
    assert!(
        h.client().request("session/list", json!({})).await.unwrap()["sessions"]
            .as_array()
            .unwrap()
            .is_empty()
    );
    h.shutdown().await.unwrap();
}

#[tokio::test(flavor = "current_thread")]
async fn registering_startup_definition_makes_it_durable_without_replacement() {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path().canonicalize().unwrap();
    let agent = Agent::new("builder")
        .instructions("Build.")
        .tool("sdk.probe");
    let mut config = HostConfig {
        desktop: Default::default(),
        state_dir: root.join("state"),
        runner: PathBuf::from(env!("CARGO_BIN_EXE_sdk-test-host")),
        storage: Storage::Sqlite,
        coworkers: vec![],
        agents: vec![agent.clone()],
        sandbox: Sandbox::Off,
        scripted: true,
        telemetry: None,
    };
    let h = connect(&config, false).await;
    h.create_agent(agent.clone()).await.unwrap();
    h.shutdown().await.unwrap();
    drop(h);
    config.agents.clear();
    let h = connect(&config, false).await;
    assert_eq!(h.list_agents().await.unwrap(), ["builder"]);
    h.create_agent(
        Agent::new("another")
            .instructions("Build.")
            .tool("sdk.probe"),
    )
    .await
    .unwrap();
    h.shutdown().await.unwrap();
    drop(h);
    config.agents = vec![agent.instructions("Changed")];
    assert!(
        HarnessClient::embedded_host(config, tool::registry(), ClientOptions::default())
            .await
            .is_err()
    );
}
