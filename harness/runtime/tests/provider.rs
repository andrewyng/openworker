use openworker_runtime::{
    model::Progress,
    provider::{self, Decoder, Failure},
};
use serde_json::json;
use tokio::{
    io::{AsyncReadExt, AsyncWriteExt},
    net::TcpListener,
    sync::mpsc,
};

#[test]
fn terminal_failures_never_become_completion() {
    let required = &openworker_runtime::compaction::DEVIATIONS["rust"];
    for (event, kind) in [
        (
            json!({"type":"response.failed","response":{"status":"failed","error":{"code":"server_error"}}}),
            Failure::Transient,
        ),
        (
            json!({"type":"response.incomplete","response":{"incomplete_details":{"reason":"max_output_tokens"}}}),
            Failure::Truncated,
        ),
        (
            json!({"type":"error","code":"misalignment_policy_violation"}),
            Failure::SafetyBlocked,
        ),
    ] {
        let mut parser = Decoder::default();
        assert_eq!(parser.event(&event).unwrap_err().kind, kind);
        assert_eq!(
            parser.finish().is_ok(),
            required["failed_terminal_is_success"].as_bool().unwrap()
        );
    }
    let mut parser = Decoder::default();
    assert_eq!(
        parser
            .event(&json!({"type":"response.output_text.delta","delta":"partial"}))
            .unwrap(),
        Some("partial".into())
    );
    let result = parser.finish();
    assert_eq!(
        result.is_ok(),
        required["missing_terminal_is_success"].as_bool().unwrap()
    );
    assert_eq!(result.unwrap_err().kind, Failure::IncompleteStream);
}
#[test]
fn usage_and_private_provider_items_are_preserved_only_on_valid_completion() {
    let mut parser = Decoder::default();
    let items = json!([{"type":"reasoning","encrypted_content":"opaque"},{"type":"message","content":[{"type":"output_text","text":"hello"}]}]);
    parser.event(&json!({"type":"response.completed","response":{"status":"completed","output":items,"usage":{"input_tokens":100,"output_tokens":20,"input_tokens_details":{"cached_tokens":80}}}})).unwrap();
    let completed = parser.finish().unwrap();
    assert_eq!(completed.output, items.as_array().unwrap().clone());
    assert_eq!(completed.usage.as_ref().unwrap().input_tokens, 100);
    assert_eq!(completed.usage.unwrap().cached_input_tokens, 80);
}
#[test]
fn safety_auth_and_quota_never_retry() {
    for (status, code) in [
        (403, "misalignment_policy_violation"),
        (401, ""),
        (429, "insufficient_quota"),
        (400, ""),
    ] {
        assert!(!provider::Error::new(provider::classify(status, code)).retryable());
    }
    let mut e = provider::Error::new(Failure::RateLimit);
    e.retry_after = Some(31);
    assert!(!e.retryable());
    e.retry_after = Some(2);
    assert!(e.retryable());
}

#[test]
fn safety_error_after_completed_event_still_blocks_dispatch() {
    let mut decoder = Decoder::default();
    decoder.event(&json!({"type":"response.completed","response":{"status":"completed","output":[{"type":"message","content":[{"type":"output_text","text":"done"}]}]}})).unwrap();
    assert_eq!(
        decoder
            .event(&json!({"type":"error","code":"misalignment_policy_violation"}))
            .unwrap_err()
            .kind,
        Failure::SafetyBlocked
    );
    assert!(decoder.finish().is_err());
}

async fn http_once(response: &str) -> (String, tokio::task::JoinHandle<()>) {
    let server = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = server.local_addr().unwrap();
    let response = response.to_string();
    let task = tokio::spawn(async move {
        let (mut socket, _) = server.accept().await.unwrap();
        let mut request = [0; 8192];
        let _ = socket.read(&mut request).await.unwrap();
        socket.write_all(response.as_bytes()).await.unwrap();
    });
    (format!("http://{address}"), task)
}
#[tokio::test]
async fn real_http_parser_handles_rate_limit_and_truncated_stream() {
    let body = r#"{"error":{"code":"rate_limit_exceeded"}}"#;
    let (url,server)=http_once(&format!("HTTP/1.1 429 Too Many Requests\r\nRetry-After: 2\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",body.len(),body)).await;
    let (tx, _) = mpsc::channel(10);
    let error = provider::request(&reqwest::Client::new(), &url, "test-only", json!({}), tx)
        .await
        .unwrap_err();
    server.await.unwrap();
    assert_eq!(error.kind, Failure::RateLimit);
    assert_eq!(error.retry_after, Some(2));
    let body = "data: {\"type\":\"response.output_text.delta\",\"delta\":\"partial\"}\n\n";
    let response = format!(
        "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
        body.len(),
        body
    );
    let (url, server) = http_once(&response).await;
    let (tx, mut rx) = mpsc::channel(10);
    let error = provider::request(&reqwest::Client::new(), &url, "test-only", json!({}), tx)
        .await
        .unwrap_err();
    server.await.unwrap();
    assert_eq!(error.kind, Failure::IncompleteStream);
    assert!(matches!(rx.recv().await, Some(Progress::OutputStarted)));
    assert!(matches!(rx.recv().await,Some(Progress::Delta(t)) if t=="partial"));
    assert!(rx.recv().await.is_none());
}
