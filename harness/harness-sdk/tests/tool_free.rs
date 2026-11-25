#![cfg(feature = "test-support")]
use futures_util::StreamExt;
use openworker_sdk::{agents::*, *};
use serde_json::{json, Value};
use std::{
    path::PathBuf,
    sync::{
        atomic::{AtomicBool, AtomicUsize, Ordering},
        Arc,
    },
    time::Duration,
};
use tokio::io::{AsyncReadExt, AsyncWriteExt};

#[derive(Clone, Copy, Debug)]
enum Api {
    Responses,
    Chat,
    Anthropic,
}
const REJECT: [&str; 4] = [
    "app__unused",
    "ask_user",
    "read_history",
    "unregistered_tool",
];
fn frame(value: Value) -> String {
    format!("data: {value}\n\n")
}
fn frames(api: Api, tool: Option<&str>) -> (String, String) {
    match api {
        Api::Responses => {
            let output = match tool {
                Some(name) => {
                    json!([{"type":"function_call","call_id":"call-unexpected","name":name,"arguments":"{}"}])
                }
                None => {
                    json!([{"type":"message","role":"assistant","content":[{"type":"output_text","text":"Hello learner."}]}])
                }
            };
            let prefix = if tool.is_none() {
                frame(json!({"type":"response.output_text.delta","delta":"Hello "}))
            } else {
                String::new()
            };
            let mut tail = if tool.is_none() {
                frame(json!({"type":"response.output_text.delta","delta":"learner."}))
            } else {
                String::new()
            };
            tail.push_str(&frame(json!({"type":"response.completed","response":{"status":"completed","output":output,"usage":{"input_tokens":10,"output_tokens":4}}})));
            (prefix, tail)
        }
        Api::Chat => {
            let prefix = if tool.is_none() {
                frame(
                    json!({"id":"fixture","choices":[{"index":0,"delta":{"content":"Hello "},"finish_reason":null}]}),
                )
            } else {
                String::new()
            };
            let delta = match tool {
                Some(name) => {
                    json!({"tool_calls":[{"index":0,"id":"call-unexpected","type":"function","function":{"name":name,"arguments":"{}"}}]})
                }
                None => json!({"content":"learner."}),
            };
            let tail = frame(
                json!({"id":"fixture","choices":[{"index":0,"delta":delta,"finish_reason":if tool.is_some(){"tool_calls"}else{"stop"}}]}),
            ) + "data: [DONE]\n\n";
            (prefix, tail)
        }
        Api::Anthropic => {
            let mut prefix = frame(
                json!({"type":"message_start","message":{"id":"fixture","role":"assistant","content":[],"usage":{"input_tokens":10,"output_tokens":0}}}),
            );
            prefix.push_str(&frame(json!({"type":"content_block_start","index":0,"content_block":match tool {
                Some(name) => json!({"type":"tool_use","id":"call-unexpected","name":name,"input":{}}),
                None => json!({"type":"text","text":""}),
            }})));
            if tool.is_none() {
                prefix.push_str(&frame(json!({"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello "}})));
            }
            let mut tail = if tool.is_none() {
                frame(
                    json!({"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"learner."}}),
                )
            } else {
                String::new()
            };
            tail.push_str(&frame(json!({"type":"content_block_stop","index":0})));
            tail.push_str(&frame(json!({"type":"message_delta","delta":{"stop_reason":if tool.is_some(){"tool_use"}else{"end_turn"}},"usage":{"output_tokens":4}})));
            tail.push_str(&frame(json!({"type":"message_stop"})));
            (prefix, tail)
        }
    }
}

async fn exercise(api: Api, managed: bool) {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url = format!("http://{}/v1", listener.local_addr().unwrap());
    let (release, gate) = tokio::sync::oneshot::channel();
    let finished = Arc::new(AtomicBool::new(false));
    let completed = finished.clone();
    let requests = Arc::new(AtomicUsize::new(0));
    let count = requests.clone();
    let server = tokio::spawn(async move {
        let mut gate = Some(gate);
        for tool in std::iter::once(None).chain(REJECT.into_iter().map(Some)) {
            let (mut socket, _) = listener.accept().await.unwrap();
            let mut raw = vec![];
            let mut buffer = [0u8; 4096];
            let end = loop {
                let n = socket.read(&mut buffer).await.unwrap();
                assert!(n > 0);
                raw.extend_from_slice(&buffer[..n]);
                assert!(raw.len() < 1024 * 1024);
                if let Some(i) = raw.windows(4).position(|v| v == b"\r\n\r\n") {
                    break i + 4;
                }
            };
            let headers = String::from_utf8_lossy(&raw[..end]);
            let length: usize = headers
                .lines()
                .find_map(|l| {
                    l.to_ascii_lowercase()
                        .strip_prefix("content-length:")
                        .map(|v| v.trim().parse().unwrap())
                })
                .unwrap();
            while raw.len() < end + length {
                let n = socket.read(&mut buffer).await.unwrap();
                assert!(n > 0);
                raw.extend_from_slice(&buffer[..n]);
            }
            let body: Value = serde_json::from_slice(&raw[end..end + length]).unwrap();
            assert_eq!(
                body.get("tools").unwrap_or(&json!([])),
                &json!([]),
                "{api:?}: hidden tools in provider request"
            );
            assert!(body.get("tool_choice").is_none());
            assert_eq!(body["stream"], true);
            count.fetch_add(1, Ordering::SeqCst);
            let (prefix, tail) = frames(api, tool);
            socket.write_all(format!("HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nContent-Length: {}\r\nConnection: close\r\n\r\n", prefix.len()+tail.len()).as_bytes()).await.unwrap();
            socket.write_all(prefix.as_bytes()).await.unwrap();
            socket.flush().await.unwrap();
            if tool.is_none() {
                gate.take().unwrap().await.unwrap();
                completed.store(true, Ordering::SeqCst);
            }
            socket.write_all(tail.as_bytes()).await.unwrap();
        }
    });
    let root = tempfile::tempdir().unwrap();
    let executable = PathBuf::from(env!("CARGO_BIN_EXE_sdk-test-host"));
    let guide = Agent::new("guide")
        .instructions("Give hints from supplied lesson context only.")
        .tools([] as [&str; 0])
        .memory(false)
        .planning(false)
        .scheduling(false)
        .default_model(Model::new("Fixture", "fixture"));
    let builder = Harness::builder()
        .state_dir(root.path().join("state"))
        .sandbox(Sandbox::Off)
        .agent(guide.clone().name("Startup guide"));
    let harness = if managed {
        builder
            .executable(&executable)
            .managed(executable, RestartPolicy::default())
            .await
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
            .base_url(url)
            .credentials(Credentials::secure("synthetic-fixture-key").memory_only()),
        )
        .await
        .unwrap();
    // Register the same definition at runtime too; registration is idempotent.
    harness
        .create_agent(guide.name("Startup guide"))
        .await
        .unwrap();
    let effects = Arc::new(AtomicUsize::new(0));
    let seen = effects.clone();
    harness
        .register_tools(
            "app",
            [Tool::new("unused")
                .description("Must not be inherited by the guide.")
                .input_schema(json!({"type":"object","properties":{},"additionalProperties":false}))
                .handler(move |_, _| {
                    let seen = seen.clone();
                    async move {
                        seen.fetch_add(1, Ordering::SeqCst);
                        Ok(json!({"ran":true}))
                    }
                })],
        )
        .await
        .unwrap();
    let agent = harness
        .create_agent(
            Agent::new("dynamic_guide")
                .instructions("Give hints from supplied lesson context only.")
                .tools([] as [&str; 0])
                .memory(false)
                .planning(false)
                .scheduling(false)
                .default_model(Model::new("Fixture", "fixture")),
        )
        .await
        .unwrap();
    if !managed {
        assert!(
            harness
                .create_agent(
                    Agent::new("requires_runner")
                        .instructions("Use the app tool.")
                        .tool("app.unused")
                )
                .await
                .is_err(),
            "Later registration cannot silently enable bound tools without a helper"
        );
        assert!(!harness
            .list_agents()
            .await
            .unwrap()
            .iter()
            .any(|a| a == "requires_runner"));
    }
    let session = agent.new_session().await.unwrap();
    let run = session
        .send("Help me understand the lesson.")
        .await
        .unwrap();
    let mut events = run.events();
    let mut deltas = String::new();
    let mut release = Some(release);
    while let Some(event) = events.next().await {
        match event.unwrap() {
            RunEvent::TextDelta {
                session_id,
                turn_id,
                attempt_id,
                text,
            } => {
                assert_eq!(session_id, session.id());
                assert!(turn_id.is_some());
                assert!(!attempt_id.is_empty());
                if let Some(release) = release.take() {
                    assert!(!finished.load(Ordering::SeqCst));
                    release.send(()).unwrap();
                }
                deltas.push_str(&text);
            }
            RunEvent::ToolStarted { .. }
            | RunEvent::ToolFinished { .. }
            | RunEvent::Interrupted(_) => panic!("Tool-free guide used tools/approval"),
            _ => {}
        }
    }
    assert!(release.is_none(), "No incremental text was streamed");
    let result = run.result().await.unwrap();
    assert_eq!(result.status, RunStatus::Completed);
    assert_eq!(result.final_output.as_deref(), Some("Hello learner."));
    assert_eq!(deltas, "Hello learner.");
    for name in REJECT {
        let session = agent.new_session().await.unwrap();
        let run = session.send("Unexpected tool call fixture.").await.unwrap();
        let result = run.result().await.unwrap();
        assert!(
            matches!(result.status, RunStatus::Failed(_)),
            "{name}: {result:?}"
        );
        assert!(
            result.interruptions.is_empty(),
            "Unregistered {name} must not ask for approval"
        );
        let mut events = run.events();
        while let Some(event) = events.next().await {
            assert!(!matches!(
                event.unwrap(),
                RunEvent::ToolStarted { .. }
                    | RunEvent::ToolFinished { .. }
                    | RunEvent::Interrupted(_)
            ));
        }
    }
    assert_eq!(effects.load(Ordering::SeqCst), 0);
    assert_eq!(requests.load(Ordering::SeqCst), 5);
    let reference = session.reference().clone();
    harness.shutdown().await.unwrap();
    if !managed {
        let restored = Harness::builder()
            .state_dir(root.path().join("state"))
            .sandbox(Sandbox::Off)
            .start()
            .await
            .unwrap();
        let recovered = restored.resume(reference).await.unwrap();
        assert_eq!(
            recovered.snapshot().await.unwrap()["turn"]["status"],
            "completed"
        );
        restored.shutdown().await.unwrap();
    }
    server.await.unwrap();
}

macro_rules! case {
    ($name:ident,$api:ident,$managed:expr) => {
        #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
        async fn $name() {
            tokio::time::timeout(Duration::from_secs(60), exercise(Api::$api, $managed))
                .await
                .unwrap();
        }
    };
}
case!(responses_embedded, Responses, false);
case!(responses_managed, Responses, true);
case!(chat_completions_embedded, Chat, false);
case!(chat_completions_managed, Chat, true);
case!(anthropic_embedded, Anthropic, false);
case!(anthropic_managed, Anthropic, true);

#[tokio::test]
async fn missing_runner_is_not_a_sandbox_or_managed_fallback() {
    let root = tempfile::tempdir().unwrap();
    let missing = root.path().join("missing-openworker-harness");
    assert!(Harness::builder()
        .state_dir(root.path().join("sandbox"))
        .agent(
            Agent::new("guide")
                .instructions("Answer briefly.")
                .tools([] as [&str; 0])
        )
        .executable(&missing)
        .sandbox(Sandbox::Openshell)
        .start()
        .await
        .is_err());
    assert!(Harness::builder()
        .state_dir(root.path().join("managed"))
        .executable(&missing)
        .sandbox(Sandbox::Off)
        .managed(missing, RestartPolicy::default())
        .await
        .is_err());
}
