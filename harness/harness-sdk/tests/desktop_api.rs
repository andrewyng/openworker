#![cfg(all(
    feature = "test-support",
    any(target_os = "linux", target_os = "macos", windows)
))]
use futures_util::StreamExt;
use openworker_sdk::{
    agents::{Response, RunStatus},
    *,
};
use serde_json::json;
use std::{
    path::PathBuf,
    sync::{
        atomic::{AtomicUsize, Ordering},
        Arc,
    },
};

async fn start(root: &std::path::Path, process: bool, storage: Storage, persist: bool) -> Harness {
    let executable = PathBuf::from(env!("CARGO_BIN_EXE_sdk-test-host"));
    let builder = Harness::builder()
        .state_dir(root.join("state"))
        .executable(&executable)
        .session_dir(root.join("sessions"))
        .scratch_dir(root.join("scratch"))
        .persist_sessions(persist)
        .compact_at_context_fraction(0.7)
        .storage(storage)
        .sandbox(Sandbox::Off)
        .scripted(true);
    if process {
        builder
            .managed(executable, RestartPolicy::default())
            .await
            .unwrap()
    } else {
        builder.start().await.unwrap()
    }
}
async fn register(h: &Harness, count: Arc<AtomicUsize>) {
    h.register_tools(
        "app",
        [Tool::new("preview")
            .description("Show a preview.")
            .input_schema(json!({"type":"object","properties":{},"additionalProperties":false}))
            .handler(move |_, context| {
                let count = count.clone();
                async move {
                    count.fetch_add(1, Ordering::SeqCst);
                    context.progress("Preview ready", json!({})).await?;
                    Ok(json!({"shown":true}))
                }
            })],
    )
    .await
    .unwrap();
}
async fn exercise(process: bool, postgres: bool, persist: bool) {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path().canonicalize().unwrap();
    let storage = if postgres {
        Storage::Postgres {
            connection_env: "OW_PG_TEST_URL".into(),
            schema: openworker_runtime::id("ow_desktop"),
            ca_file: None,
            loopback_plaintext: true,
        }
    } else {
        Storage::Sqlite
    };
    storage.migrate().unwrap();
    let h = start(&root, process, storage.clone(), persist).await;
    let count = Arc::new(AtomicUsize::new(0));
    register(&h, count.clone()).await;
    let definition = Agent::new("builder")
        .instructions("Use preview.")
        .tool("app.preview");
    let agent = h.create_agent(definition.clone()).await.unwrap();
    assert!(!root.join("scratch").exists());
    let session = agent.new_session().await.unwrap();
    let reference = session.reference().clone();
    assert!(root.join("scratch").exists());
    assert_eq!(session.state().await.unwrap()["status"], "idle");
    assert!(h.client().request("session/send",json!({"session_id":session.id(),"submission_id":"bad","input":{"text":"yes","decision":"once"}})).await.is_err());
    let run = session
        .send(&format!(
            "TEST_RESOURCE_GRANTS {}",
            json!([{"name":"app__preview","arguments":{}}])
        ))
        .await
        .unwrap();
    let result = run.result().await.unwrap();
    assert_eq!(result.status, RunStatus::Interrupted);
    assert_eq!(count.load(Ordering::SeqCst), 0);
    let state = session.state().await.unwrap();
    assert_eq!(state["status"], "waiting");
    assert_eq!(state["can_accept_message"], false);
    assert!(state["last_outcome"].is_null());
    assert!(h
        .client()
        .request(
            "application/result",
            json!({"owner":"wrong","invocation_id":"expired","output":{}})
        )
        .await
        .is_err());
    assert!(session.send("Do another thing").await.is_err());
    session
        .respond(
            &result.interruptions[0],
            Response::Approve(ApprovalDecision::Session),
        )
        .await
        .unwrap();
    assert_eq!(run.result().await.unwrap().status, RunStatus::Completed);
    assert_eq!(count.load(Ordering::SeqCst), 1);
    let mut events = run.events();
    let mut seen = 0;
    while let Some(event) = events.next().await {
        event.unwrap();
        seen += 1;
    }
    assert!(seen > 0);
    assert_eq!(run.result().await.unwrap().status, RunStatus::Completed);
    assert_eq!(session.state().await.unwrap()["status"], "idle");
    assert!(session
        .send(SessionInput::Approval {
            interaction_id: result.interruptions[0].id.clone(),
            target_session_id: None,
            decision: ApprovalDecision::Once
        })
        .await
        .is_err());
    h.shutdown().await.unwrap();
    assert_eq!(
        root.join("sessions/runtime.sqlite3").exists(),
        persist && !postgres
    );
    let h = start(&root, process, storage, persist).await;
    register(&h, count.clone()).await;
    let agent = h.create_agent(definition).await.unwrap();
    if persist {
        let restored = agent.resume_session(reference).await.unwrap();
        assert_eq!(restored.state().await.unwrap()["status"], "idle");
        let run = restored
            .send(&format!(
                "TEST_RESOURCE_GRANTS {}",
                json!([{"name":"app__preview","arguments":{}}])
            ))
            .await
            .unwrap();
        assert_eq!(run.result().await.unwrap().status, RunStatus::Completed);
        assert_eq!(count.load(Ordering::SeqCst), 2);
    } else {
        assert!(agent.resume_session(reference).await.is_err());
    }
    h.shutdown().await.unwrap();
}
#[tokio::test(flavor = "multi_thread")]
async fn sqlite_embedded() {
    exercise(false, false, true).await
}
#[tokio::test(flavor = "multi_thread")]
async fn sqlite_managed() {
    exercise(true, false, true).await
}
#[tokio::test(flavor = "multi_thread")]
async fn ephemeral_embedded() {
    exercise(false, false, false).await
}

#[tokio::test(flavor = "multi_thread")]
async fn startup_providers_mcp_and_separate_reviewer_are_durable() {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path().canonicalize().unwrap();
    let executable = PathBuf::from(env!("CARGO_BIN_EXE_sdk-test-host"));
    let server = McpServer::streamable_http("device", "http://127.0.0.1:54321/mcp");
    let h = Harness::builder()
        .state_dir(root.join("state"))
        .executable(&executable)
        .sandbox(Sandbox::Off)
        .scripted(true)
        .mcp_server(server.clone())
        .model_provider(
            ModelProvider::chat_completions("Local").base_url("http://127.0.0.1:11434/v1"),
        )
        .model_provider(
            ModelProvider::chat_completions("Reviewer").base_url("http://127.0.0.1:11435/v1"),
        )
        .start()
        .await
        .unwrap();
    h.connect_mcp_server(server.clone()).await.unwrap();
    assert!(h
        .connect_mcp_server(McpServer::streamable_http(
            "device",
            "http://127.0.0.1:54322/mcp"
        ))
        .await
        .is_err());
    register(&h, Arc::new(AtomicUsize::new(0))).await;
    let agent = h
        .create_agent(
            Agent::new("worker")
                .instructions("Be helpful.")
                .tool("app.preview")
                .default_model(Model::new("Local", "local:8b"))
                .approvals(Approvals::auto(Model::new("Reviewer", "review:4b"))),
        )
        .await
        .unwrap();
    let session = agent.new_session().await.unwrap();
    let snapshot = h.client().snapshot(session.id()).await.unwrap();
    assert_eq!(
        snapshot["session"]["model_selection"]["connection"],
        "Local"
    );
    assert_eq!(
        snapshot["session"]["reviewer_selection"]["connection"],
        "Reviewer"
    );
    let reference = session.reference().clone();
    h.shutdown().await.unwrap();
    let h = Harness::builder()
        .state_dir(root.join("state"))
        .executable(&executable)
        .sandbox(Sandbox::Off)
        .scripted(true)
        .start()
        .await
        .unwrap();
    h.connect_mcp_server(server).await.unwrap();
    assert_eq!(
        h.resume(reference).await.unwrap().state().await.unwrap()["status"],
        "idle"
    );
    h.shutdown().await.unwrap();
}
#[tokio::test(flavor = "multi_thread")]
#[ignore = "Requires OW_PG_TEST_URL"]
async fn postgres_embedded() {
    exercise(false, true, true).await
}
#[tokio::test(flavor = "multi_thread")]
#[ignore = "Requires OW_PG_TEST_URL"]
async fn postgres_managed() {
    exercise(true, true, true).await
}

#[cfg(unix)]
#[tokio::test(flavor = "multi_thread")]
async fn managed_callback_crash_is_not_replayed_and_binding_recovers() {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path().canonicalize().unwrap();
    let h = start(&root, true, Storage::Sqlite, true).await;
    let calls = Arc::new(AtomicUsize::new(0));
    let counter = calls.clone();
    h.register_tools("app",[Tool::new("effect").description("Test callback.").input_schema(json!({"type":"object","properties":{"block":{"type":"boolean"}},"required":["block"],"additionalProperties":false})).handler(move|args,context|{let counter=counter.clone();async move{counter.fetch_add(1,Ordering::SeqCst);if args["block"]==true{context.cancellation().cancelled().await;}Ok(json!({"done":true}))}})]).await.unwrap();
    let agent = h
        .create_agent(
            Agent::new("worker")
                .instructions("Use callback.")
                .tool("app.effect")
                .approvals(Approvals::Never),
        )
        .await
        .unwrap();
    let session = agent.new_session().await.unwrap();
    let prompt = |block| {
        format!(
            "TEST_RESOURCE_GRANTS {}",
            json!([{"name":"app__effect","arguments":{"block":block}}])
        )
    };
    let run = session.send(prompt(true)).await.unwrap();
    tokio::time::timeout(std::time::Duration::from_secs(20), async {
        while calls.load(Ordering::SeqCst) != 1 {
            tokio::time::sleep(std::time::Duration::from_millis(20)).await;
        }
    })
    .await
    .unwrap();
    let mut connection = h.client().connection();
    let old = match *connection.borrow() {
        ConnectionState::Ready {
            process_id: Some(pid),
            ..
        } => pid,
        _ => panic!("missing child"),
    };
    assert!(std::process::Command::new("/bin/kill")
        .args(["-KILL", &old.to_string()])
        .status()
        .unwrap()
        .success());
    tokio::time::timeout(std::time::Duration::from_secs(30),async{loop{connection.changed().await.unwrap();if matches!(*connection.borrow(),ConnectionState::Ready{process_id:Some(pid),..} if pid!=old){break;}}}).await.unwrap();
    assert!(matches!(
        run.result().await.unwrap().status,
        RunStatus::Failed(_)
    ));
    assert_eq!(calls.load(Ordering::SeqCst), 1);
    assert_eq!(
        session
            .send(prompt(false))
            .await
            .unwrap()
            .result()
            .await
            .unwrap()
            .status,
        RunStatus::Completed
    );
    assert_eq!(calls.load(Ordering::SeqCst), 2);
    h.shutdown().await.unwrap();
}
