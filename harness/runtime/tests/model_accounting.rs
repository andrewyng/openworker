//! Real loopback SSE through the same request boundary used by every model purpose.
//! No model keys, cloud traffic or tool execution. Usage snapshots replace, never add.
use openworker_runtime::{
    connections::{Choice, Manager},
    model::{CallContext, Model},
    provider_auth::{self, AccessToken},
    telemetry::*,
};
use serde_json::{json, Value};
use std::{
    sync::{Arc, Mutex},
    time::Duration,
};
use tokio::io::{AsyncReadExt, AsyncWriteExt};

struct CaptureSink(Arc<Mutex<Vec<Record>>>);
impl Sink for CaptureSink {
    fn export(&mut self, records: &[Record], _: Snapshot) -> bool {
        self.0.lock().unwrap().extend_from_slice(records);
        true
    }
}
async fn endpoint(responses: Vec<(u16, String)>) -> (String, tokio::task::JoinHandle<()>) {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url = format!("http://{}/v1", listener.local_addr().unwrap());
    let task = tokio::spawn(async move {
        for (status, body) in responses {
            let (mut s, _) = listener.accept().await.unwrap();
            let mut raw = vec![];
            let mut buf = [0; 4096];
            loop {
                let n = s.read(&mut buf).await.unwrap();
                assert!(n > 0);
                raw.extend_from_slice(&buf[..n]);
                if let Some(end) = raw.windows(4).position(|w| w == b"\r\n\r\n") {
                    let h = String::from_utf8_lossy(&raw[..end]).to_ascii_lowercase();
                    let len: usize = h
                        .lines()
                        .find_map(|s| s.strip_prefix("content-length: "))
                        .unwrap()
                        .parse()
                        .unwrap();
                    if raw.len() >= end + 4 + len {
                        break;
                    }
                }
                assert!(raw.len() < 65536);
            }
            s.write_all(format!("HTTP/1.1 {status} Fixture\r\nContent-Type: text/event-stream\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",body.len()).as_bytes()).await.unwrap();
            // First-byte timing must survive transport fragmentation.
            for part in body.as_bytes().chunks(13) {
                s.write_all(part).await.unwrap();
            }
        }
    });
    (url, task)
}
fn sse(events: Vec<Value>) -> String {
    events
        .into_iter()
        .map(|v| format!("data: {v}\n\n"))
        .collect()
}
fn chat(text: &str, usage: bool) -> String {
    let mut frames = vec![
        json!({"choices":[{"index":0,"delta":{"content":text},"finish_reason":null}]}),
        json!({"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}),
    ];
    if usage {
        frames.push(json!({"choices":[],"usage":{"prompt_tokens":100,"completion_tokens":20,"prompt_tokens_details":{"cached_tokens":40},"completion_tokens_details":{"reasoning_tokens":5}}}));
    }
    sse(frames) + "data: [DONE]\n\n"
}
fn model(
    manager: &Arc<Manager>,
    api: &str,
    url: &str,
    t: &Telemetry,
    purpose: ModelPurpose,
) -> Model {
    manager
        .set_provider(json!({"name":"Fixture","api":api,"base_url":url}))
        .unwrap();
    let selection = manager
        .resolve(&Choice::new("Fixture", "fixture-model"))
        .unwrap();
    let mut m = Model::new(selection.model.clone(), false);
    m.connection = Some((manager.clone(), selection));
    m.accounted(CallContext {
        telemetry: t.clone(),
        session: "PRIVATE_SESSION".into(),
        turn: "PRIVATE_TURN".into(),
        action: "request-1".into(),
        purpose,
        attempt: 1,
    })
}
async fn next(m: &Model) -> bool {
    let (tx, mut rx) = tokio::sync::mpsc::channel(32);
    let collect = async { while rx.recv().await.is_some() {} };
    let history = [json!({"role":"user","content":"PRIVATE_PROMPT"})];
    let (result, _) = tokio::join!(m.next("PRIVATE_INSTRUCTIONS", &history, &[], tx), collect);
    result.is_ok()
}
fn telemetry() -> (Telemetry, Controller, Arc<Mutex<Vec<Record>>>) {
    let rows = Arc::new(Mutex::new(vec![]));
    let (t, c) = Telemetry::start_with_content(
        CaptureSink(rows.clone()),
        128,
        ContentPolicy {
            capture: Capture::Metadata,
            ..Default::default()
        },
    )
    .unwrap();
    (t, c, rows)
}
#[tokio::test]
async fn all_real_model_purposes_keep_usage_once_and_share_turn() {
    let (t, c, rows) = telemetry();
    let root = tempfile::tempdir().unwrap();
    let mgr = Arc::new(Manager::open(root.path()).unwrap());
    let (url, server) = endpoint(vec![
        (200, chat("Answer", true)),
        (200, chat(r#"{"decision":"ask","reason":"Check"}"#, true)),
        (200, chat("Summary", true)),
    ])
    .await;
    let m = model(&mgr, "chat-completions", &url, &t, ModelPurpose::Main);
    assert!(next(&m).await);
    let review = m.clone().accounted(CallContext {
        purpose: ModelPurpose::AutoApprover,
        action: "review".into(),
        ..m.observation.clone().unwrap()
    });
    review.review("PRIVATE_REVIEW").await;
    let summary = m.clone().accounted(CallContext {
        purpose: ModelPurpose::Compaction,
        action: "summary".into(),
        ..m.observation.clone().unwrap()
    });
    assert_eq!(
        summary
            .summarize("PRIVATE_SYSTEM", "PRIVATE_CONTEXT", 256)
            .await
            .unwrap(),
        "Summary"
    );
    server.await.unwrap();
    assert!(c.shutdown(Duration::from_secs(3)));
    let r = rows.lock().unwrap();
    let calls: Vec<_> = r
        .iter()
        .filter(|r| r.operation == Operation::Model)
        .collect();
    assert_eq!(calls.len(), 3);
    assert_eq!(
        r.iter()
            .filter(|r| r.operation == Operation::ModelStarted)
            .count(),
        3
    );
    let fixture: Value =
        serde_json::from_str(include_str!("../../tests/contracts/observability.json")).unwrap();
    assert_eq!(
        calls.iter().map(|r| r.input_tokens.unwrap()).sum::<u64>(),
        fixture["three_calls"]["input"].as_u64().unwrap()
    );
    assert_eq!(
        calls.iter().map(|r| r.output_tokens.unwrap()).sum::<u64>(),
        fixture["three_calls"]["output"].as_u64().unwrap()
    );
    for call in &calls {
        assert_eq!(
            call.usage_details,
            TokenUsage {
                input: Some(100),
                output: Some(20),
                cache_read: Some(40),
                reasoning: Some(5),
                ..Default::default()
            }
        );
        assert!(call.first_output.is_some());
        assert_eq!(call.context.trace, calls[0].context.trace);
        assert_eq!(call.turn_hash, calls[0].turn_hash);
    }
    assert_eq!(
        calls
            .iter()
            .map(|r| r.purpose.unwrap().name())
            .collect::<Vec<_>>(),
        vec!["main", "auto_approver", "compaction"]
    );
    assert!(!format!("{r:?}").contains("PRIVATE"));
    assert!(r
        .iter()
        .filter(|r| r.operation == Operation::ModelStarted)
        .all(|r| r.input_tokens.is_none() && calls.iter().any(|c| Some(c.context) == r.parent)));
}
#[tokio::test]
async fn anthropic_partial_usage_survives_truncation_without_authorizing_output() {
    let (t, c, rows) = telemetry();
    let root = tempfile::tempdir().unwrap();
    let mgr = Arc::new(Manager::open(root.path()).unwrap());
    let start = json!({"type":"message_start","message":{"role":"assistant","content":[],"usage":{"input_tokens":20,"cache_read_input_tokens":10,"cache_creation_input_tokens":5}}});
    let truncated = sse(vec![
        start.clone(),
        json!({"type":"content_block_start","index":0,"content_block":{"type":"tool_use","id":"call1","name":"write_file","input":{}}}),
        json!({"type":"message_delta","delta":{"stop_reason":"max_tokens"},"usage":{"output_tokens":12}}),
    ]);
    let (url, server) = endpoint(vec![(200, sse(vec![start])), (200, truncated)]).await;
    let m = model(&mgr, "anthropic", &url, &t, ModelPurpose::Main);
    assert!(!next(&m).await);
    assert!(!next(&m).await);
    server.await.unwrap();
    assert!(c.shutdown(Duration::from_secs(3)));
    let r = rows.lock().unwrap();
    let calls: Vec<_> = r
        .iter()
        .filter(|r| r.operation == Operation::Model)
        .collect();
    assert_eq!(calls.len(), 2);
    assert_eq!(
        calls[0].usage_details,
        TokenUsage {
            input: Some(35),
            output: None,
            cache_read: Some(10),
            cache_write: Some(5),
            reasoning: None
        }
    );
    assert_eq!(calls[0].first_output, None);
    assert_eq!(calls[1].output_tokens, Some(12));
    assert!(calls[1].first_output.is_some());
    assert!(calls.iter().all(|r| r.outcome == Outcome::Error));
}
#[tokio::test]
async fn responses_failed_request_keeps_reported_cost_and_tool_first_output() {
    let (t, c, rows) = telemetry();
    let root = tempfile::tempdir().unwrap();
    let mgr = Arc::new(Manager::open(root.path()).unwrap());
    let body = sse(vec![
        json!({"type":"response.created"}),
        json!({"type":"response.output_item.added","item":{"type":"function_call","name":"write_file"}}),
        json!({"type":"response.incomplete","response":{"incomplete_details":{"reason":"max_output_tokens"},"usage":{"input_tokens":100,"output_tokens":20,"input_tokens_details":{"cached_tokens":40},"output_tokens_details":{"reasoning_tokens":5}}}}),
    ]);
    let (url, server) = endpoint(vec![(200, body)]).await;
    let m = model(&mgr, "responses", &url, &t, ModelPurpose::Main);
    assert!(!next(&m).await);
    server.await.unwrap();
    assert!(c.shutdown(Duration::from_secs(3)));
    let r = rows.lock().unwrap();
    let call = r.iter().find(|r| r.operation == Operation::Model).unwrap();
    assert_eq!(call.outcome, Outcome::Error);
    assert_eq!(call.input_tokens, Some(100));
    assert_eq!(call.usage_details.reasoning, Some(5));
    assert!(call.first_output.is_some());
}
#[tokio::test]
async fn authentication_refresh_records_both_http_attempts_and_missing_usage_is_unknown() {
    let (t, c, rows) = telemetry();
    let root = tempfile::tempdir().unwrap();
    let mgr = Arc::new(Manager::open(root.path()).unwrap());
    let (url, server) = endpoint(vec![
        (401, r#"{"error":{"code":"invalid_api_key"}}"#.into()),
        (200, chat("OK", false)),
    ])
    .await;
    let m = model(&mgr, "chat-completions", &url, &t, ModelPurpose::Main);
    mgr.register_access_auth(
        "Fixture",
        Some(Arc::new(|reason| {
            assert!(matches!(reason, provider_auth::Reason::Rejected));
            Box::pin(async {
                AccessToken::new("synthetic-replacement", provider_auth::now() + 900)
            })
        })),
    )
    .unwrap();
    mgr.update_access_token(
        "Fixture",
        AccessToken::new("synthetic-original", provider_auth::now() + 900).unwrap(),
    )
    .unwrap();
    assert!(next(&m).await);
    server.await.unwrap();
    assert!(c.shutdown(Duration::from_secs(3)));
    let r = rows.lock().unwrap();
    let calls: Vec<_> = r
        .iter()
        .filter(|r| r.operation == Operation::Model)
        .collect();
    assert_eq!(calls.len(), 2);
    assert_eq!(calls[0].outcome, Outcome::Error);
    assert_eq!(calls[1].outcome, Outcome::Ok);
    assert_ne!(calls[0].context.span, calls[1].context.span);
    assert_eq!(calls[0].identity.action, calls[1].identity.action);
    assert!(calls
        .iter()
        .all(|r| r.input_tokens.is_none() && r.output_tokens.is_none()));
    assert!(!format!("{r:?}").contains("synthetic-"));
}

#[tokio::test]
async fn cancellation_keeps_partial_usage_and_interrupted_outcome() {
    let (t, c, rows) = telemetry();
    let root = tempfile::tempdir().unwrap();
    let mgr = Arc::new(Manager::open(root.path()).unwrap());
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url = format!("http://{}/v1", listener.local_addr().unwrap());
    let server = tokio::spawn(async move {
        let (mut socket, _) = listener.accept().await.unwrap();
        let mut raw = [0; 8192];
        assert!(socket.read(&mut raw).await.unwrap() > 0);
        let start = sse(vec![
            json!({"type":"message_start","message":{"role":"assistant","content":[],"usage":{"input_tokens":35,"output_tokens":0}}}),
        ]);
        socket.write_all(format!("HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nContent-Length: 100000\r\n\r\n{start}").as_bytes()).await.unwrap();
        std::future::pending::<()>().await;
    });
    let m = model(&mgr, "anthropic", &url, &t, ModelPurpose::Main);
    let (tx, mut rx) = tokio::sync::mpsc::channel(32);
    let request = tokio::spawn(async move {
        m.next(
            "private",
            &[json!({"role":"user","content":"private"})],
            &[],
            tx,
        )
        .await
    });
    let progress = tokio::time::timeout(Duration::from_secs(5), rx.recv())
        .await
        .unwrap()
        .unwrap();
    assert!(matches!(
        progress,
        openworker_runtime::model::Progress::Usage(TokenUsage {
            input: Some(35),
            output: Some(0),
            ..
        })
    ));
    request.abort();
    let _ = request.await;
    server.abort();
    let _ = server.await;
    assert!(c.shutdown(Duration::from_secs(3)));
    let r = rows.lock().unwrap();
    let call = r.iter().find(|r| r.operation == Operation::Model).unwrap();
    assert_eq!(call.outcome, Outcome::Interrupted);
    assert_eq!(call.input_tokens, Some(35));
    assert_eq!(call.output_tokens, Some(0));
    assert!(call.first_output.is_none());
}
