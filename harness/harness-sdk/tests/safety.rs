#![cfg(all(feature = "test-support", feature = "guardrails"))]
use openworker_sdk::{agents::*, *};
use serde_json::{json, Value};
use std::{
    sync::{
        atomic::{AtomicUsize, Ordering},
        Arc,
    },
    time::Duration,
};
use tokio::io::{AsyncReadExt, AsyncWriteExt};

#[derive(Clone, Copy)]
enum Api {
    Responses,
    Chat,
    Anthropic,
}
fn frames(api: Api) -> String {
    let frame = |v: Value| format!("data: {v}\n\n");
    match api {
        Api::Responses => {
            frame(json!({"type":"response.output_text.delta","delta":"SECRET_SENTINEL"}))
                + &frame(
                    json!({"type":"response.completed","response":{"status":"completed","output":[{"type":"message","role":"assistant","content":[{"type":"output_text","text":"SECRET_SENTINEL"}]}],"usage":{"input_tokens":10,"output_tokens":8}}}),
                )
        }
        Api::Chat => {
            frame(
                json!({"id":"fixture","choices":[{"index":0,"delta":{"content":"SECRET_"},"finish_reason":null}]}),
            ) + &frame(
                json!({"id":"fixture","choices":[{"index":0,"delta":{"content":"SENTINEL"},"finish_reason":"stop"}]}),
            ) + "data: [DONE]\n\n"
        }
        Api::Anthropic => {
            frame(
                json!({"type":"message_start","message":{"id":"fixture","role":"assistant","content":[],"usage":{"input_tokens":10,"output_tokens":0}}}),
            ) + &frame(
                json!({"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}),
            ) + &frame(
                json!({"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"SECRET_SENTINEL"}}),
            ) + &frame(json!({"type":"content_block_stop","index":0}))
                + &frame(
                    json!({"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":8}}),
                )
                + &frame(json!({"type":"message_stop"}))
        }
    }
}
struct Replacement(Arc<AtomicUsize>);
impl guardrails::Handler for Replacement {
    fn call(
        &self,
        context: guardrails::Context,
        _: tokio_util::sync::CancellationToken,
    ) -> guardrails::CheckFuture<'_> {
        Box::pin(async move {
            self.0.fetch_add(1, Ordering::SeqCst);
            Ok(guardrails::Decision::Replace {
                text: context.text.replace("SECRET_SENTINEL", "[hidden]"),
            })
        })
    }
}
async fn exercise(api: Api, managed: bool, postgres: bool) {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let endpoint = format!("http://{}/v1", listener.local_addr().unwrap());
    let server = tokio::spawn(async move {
        let (mut socket, _) = listener.accept().await.unwrap();
        let mut raw = vec![];
        let mut buf = [0; 4096];
        let end = loop {
            let n = socket.read(&mut buf).await.unwrap();
            assert!(n > 0);
            raw.extend_from_slice(&buf[..n]);
            if let Some(i) = raw.windows(4).position(|s| s == b"\r\n\r\n") {
                break i + 4;
            }
        };
        let len: usize = String::from_utf8_lossy(&raw[..end])
            .lines()
            .find_map(|l| {
                l.to_ascii_lowercase()
                    .strip_prefix("content-length:")
                    .map(|v| v.trim().parse().unwrap())
            })
            .unwrap();
        while raw.len() < end + len {
            let n = socket.read(&mut buf).await.unwrap();
            assert!(n > 0);
            raw.extend_from_slice(&buf[..n]);
        }
        let body: Value = serde_json::from_slice(&raw[end..end + len]).unwrap();
        assert!(!body.to_string().contains("SECRET_SENTINEL"));
        assert!(body.to_string().contains("[hidden]"));
        let response = frames(api);
        socket.write_all(format!("HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{response}",response.len()).as_bytes()).await.unwrap();
    });
    let root = tempfile::tempdir().unwrap();
    let executable = std::path::PathBuf::from(env!("CARGO_BIN_EXE_sdk-test-host"));
    let schema = format!("ow_sf_{}", openworker_runtime::id("x").replace('-', "_"));
    let storage = if postgres {
        Storage::Postgres {
            connection_env: "OW_PG_TEST_URL".into(),
            schema: schema.clone(),
            ca_file: None,
            loopback_plaintext: true,
        }
    } else {
        Storage::Sqlite
    };
    storage.migrate().unwrap();
    let builder = Harness::builder()
        .storage(storage)
        .state_dir(root.path().join("state"))
        .executable(&executable)
        .sandbox(Sandbox::Off)
        .compliance(compliance::Config::default());
    let harness = if managed {
        builder.managed(executable, RestartPolicy::default()).await
    } else {
        builder.start().await
    }
    .unwrap();
    harness
        .set_model_provider(
            match api {
                Api::Responses => ModelProvider::responses("Fixture"),
                Api::Chat => ModelProvider::chat_completions("Fixture"),
                Api::Anthropic => ModelProvider::anthropic("Fixture"),
            }
            .base_url(endpoint)
            .credentials(Credentials::secure("synthetic-fixture-key").memory_only()),
        )
        .await
        .unwrap();
    let calls = Arc::new(AtomicUsize::new(0));
    let reference = guardrails::HandlerRef {
        name: "redact".into(),
        version: "1".into(),
    };
    harness
        .register_safety_handler(reference.clone(), Arc::new(Replacement(calls.clone())))
        .await
        .unwrap();
    let rule = guardrails::Rule::regex(
        "redact",
        "SECRET_[A-Z]+",
        guardrails::Action::Callback { handler: reference },
    );
    let config = guardrails::Config {
        guardrails: guardrails::Guardrails {
            user_input: vec![rule.clone()],
            model_output: vec![rule],
            ..Default::default()
        },
        ..Default::default()
    };
    let agent = harness
        .create_agent(
            Agent::new("guide")
                .instructions("Give a short hint")
                .tools([] as [&str; 0])
                .memory(false)
                .planning(false)
                .scheduling(false)
                .default_model(Model::new("Fixture", "fixture")),
        )
        .await
        .unwrap();
    let session = agent
        .new_session_with(SessionOptions::default().safety(config))
        .await
        .unwrap();
    let run = session.send("SECRET_SENTINEL").await.unwrap();
    let result = run.result().await.unwrap();
    assert_eq!(result.status, RunStatus::Completed);
    assert_eq!(result.final_output.as_deref(), Some("[hidden]"));
    assert_eq!(calls.load(Ordering::SeqCst), 2);
    let snapshot = harness.client().snapshot(session.id()).await.unwrap();
    assert!(!snapshot.to_string().contains("SECRET_SENTINEL"));
    let mut cursor = 0;
    loop {
        let page = harness
            .client()
            .request(
                "event/read",
                json!({"session_id":session.id(),"after":cursor}),
            )
            .await
            .unwrap();
        assert!(!page.to_string().contains("SECRET_SENTINEL"));
        cursor = page["cursor"].as_i64().unwrap();
        if page["has_more"] != true {
            break;
        }
    }
    let evidence = session
        .compliance_activity(compliance::Query::default())
        .await
        .unwrap();
    assert!(!evidence.to_string().contains("SECRET_SENTINEL"));
    assert!(evidence["activities"]
        .as_array()
        .unwrap()
        .iter()
        .any(|e| e["kind"] == "safety_evaluation"));
    harness.shutdown().await.unwrap();
    server.await.unwrap();
    if postgres {
        tokio::task::block_in_place(|| {
            let mut db = postgres::Client::connect(
                &std::env::var("OW_PG_TEST_URL").unwrap(),
                postgres::NoTls,
            )
            .unwrap();
            db.batch_execute(&format!("DROP SCHEMA {schema} CASCADE"))
                .unwrap();
        });
    }
}
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn embedded_callbacks_gate_input_and_output() {
    tokio::time::timeout(
        Duration::from_secs(60),
        exercise(Api::Responses, false, false),
    )
    .await
    .unwrap();
}
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn managed_callbacks_use_control_lane_and_keep_content_private() {
    tokio::time::timeout(
        Duration::from_secs(60),
        exercise(Api::Responses, true, false),
    )
    .await
    .unwrap();
}

#[tokio::test]
async fn chat_embedded() {
    tokio::time::timeout(Duration::from_secs(60), exercise(Api::Chat, false, false))
        .await
        .unwrap();
}
#[tokio::test]
async fn chat_managed() {
    tokio::time::timeout(Duration::from_secs(60), exercise(Api::Chat, true, false))
        .await
        .unwrap();
}
#[tokio::test]
async fn anthropic_embedded() {
    tokio::time::timeout(
        Duration::from_secs(60),
        exercise(Api::Anthropic, false, false),
    )
    .await
    .unwrap();
}
#[tokio::test]
async fn anthropic_managed() {
    tokio::time::timeout(
        Duration::from_secs(60),
        exercise(Api::Anthropic, true, false),
    )
    .await
    .unwrap();
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
#[ignore = "requires isolated OW_PG_TEST_URL"]
async fn postgres_embedded_and_managed_safety() {
    for managed in [false, true] {
        tokio::time::timeout(
            Duration::from_secs(60),
            exercise(Api::Responses, managed, true),
        )
        .await
        .unwrap();
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn separate_model_detector_blocks_spelled_identifier_before_admission() {
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    let server = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let endpoint = format!("http://{}/v1", server.local_addr().unwrap());
    let task = tokio::spawn(async move {
        let (mut stream, _) = server.accept().await.unwrap();
        let mut raw = Vec::new();
        let mut buf = [0; 4096];
        loop {
            let n = stream.read(&mut buf).await.unwrap();
            assert!(n > 0);
            raw.extend_from_slice(&buf[..n]);
            if let Some(i) = raw.windows(4).position(|v| v == b"\r\n\r\n") {
                let len: usize = String::from_utf8_lossy(&raw[..i])
                    .lines()
                    .find_map(|line| {
                        line.to_ascii_lowercase()
                            .strip_prefix("content-length:")
                            .map(|n| n.trim().parse().unwrap())
                    })
                    .unwrap();
                if raw.len() >= i + 4 + len {
                    let body: Value = serde_json::from_slice(&raw[i + 4..i + 4 + len]).unwrap();
                    assert_eq!(body["model"], "privacy-fixture");
                    assert!(body.to_string().contains("one two three four"));
                    assert!(body["tools"].as_array().is_none_or(|t| t.is_empty()));
                    break;
                }
            }
        }
        let output = json!({"matches":["one two three four"]}).to_string();
        let event = json!({"type":"response.completed","response":{"status":"completed","output":[{"type":"message","role":"assistant","content":[{"type":"output_text","text":output}]}],"usage":{"input_tokens":20,"output_tokens":12}}});
        let data = format!("data: {event}\n\n");
        stream.write_all(format!("HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{data}",data.len()).as_bytes()).await.unwrap();
    });
    let tmp = tempfile::tempdir().unwrap();
    let h = Harness::builder()
        .executable(env!("CARGO_BIN_EXE_sdk-test-host"))
        .state_dir(tmp.path().join("state"))
        .sandbox(Sandbox::Off)
        .scripted(true)
        .start()
        .await
        .unwrap();
    h.set_model_provider(
        ModelProvider::responses("Privacy")
            .base_url(endpoint)
            .credentials(Credentials::secure("fixture-privacy-key").memory_only()),
    )
    .await
    .unwrap();
    let a = h
        .create_agent(
            Agent::new("guide")
                .instructions("Hint only")
                .tools([] as [&str; 0])
                .memory(false)
                .planning(false)
                .scheduling(false),
        )
        .await
        .unwrap();
    let policy = guardrails::Config {
        guardrails: guardrails::Guardrails {
            user_input: vec![guardrails::Rule::model(
                "spoken",
                guardrails::ModelRef::new("Privacy", "privacy-fixture"),
                "Detect identifiers written as words",
                guardrails::Action::Block,
            )],
            ..Default::default()
        },
        ..Default::default()
    };
    let s = a
        .new_session_with(SessionOptions::default().safety(policy))
        .await
        .unwrap();
    assert!(tokio::time::timeout(
        Duration::from_secs(15),
        s.send("My identifier is one two three four")
    )
    .await
    .unwrap()
    .is_err());
    tokio::time::timeout(Duration::from_secs(5), task)
        .await
        .unwrap()
        .unwrap();
    let snapshot = h.client().snapshot(s.id()).await.unwrap();
    assert!(!snapshot.to_string().contains("one two three four"));
    assert!(snapshot["turn"].is_null());
    h.shutdown().await.unwrap();
}
