//! Real HTTP/stream parsing at the configured loopback endpoint. No model keys/network.
use openworker_runtime::{
    connections::{Choice, Manager},
    model::{Model, Progress},
};
use serde_json::{json, Value};
use std::sync::Arc;
use tokio::io::{AsyncReadExt, AsyncWriteExt};

#[tokio::test]
async fn local_chat_completions_streams_without_authorization_header() {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let server = tokio::spawn(async move {
        let (mut socket, _) = listener.accept().await.unwrap();
        let mut bytes = Vec::new();
        let mut buf = [0u8; 2048];
        loop {
            let n = socket.read(&mut buf).await.unwrap();
            assert!(n > 0);
            bytes.extend_from_slice(&buf[..n]);
            if let Some(end) = bytes.windows(4).position(|w| w == b"\r\n\r\n") {
                let headers = String::from_utf8_lossy(&bytes[..end]).to_lowercase();
                let len: usize = headers
                    .lines()
                    .find_map(|l| l.strip_prefix("content-length: "))
                    .unwrap()
                    .parse()
                    .unwrap();
                if bytes.len() >= end + 4 + len {
                    assert!(headers.starts_with("post /v1/chat/completions "));
                    assert!(!headers.contains("authorization:"));
                    let body: Value = serde_json::from_slice(&bytes[end + 4..]).unwrap();
                    assert_eq!(body["model"], "local:8b");
                    assert!(body["max_tokens"].as_u64().is_some_and(|n| n > 0));
                    assert!(body.get("max_completion_tokens").is_none());
                    assert_eq!(body["messages"][0]["content"], "Be helpful.");
                    break;
                }
            }
            assert!(bytes.len() < 16384);
        }
        let stream = format!(
            "data: {}\n\ndata: {}\n\ndata: [DONE]\n\n",
            json!({"id":"test","choices":[{"index":0,"delta":{"content":"Hello"},"finish_reason":null}]}),
            json!({"id":"test","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]})
        );
        socket.write_all(format!("HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",stream.len()).as_bytes()).await.unwrap();
        // Fragment arbitrary UTF-8/JSON/SSE boundaries, as real network reads do.
        for part in stream.as_bytes().chunks(7) {
            socket.write_all(part).await.unwrap();
        }
    });
    let root = tempfile::tempdir().unwrap();
    let manager = Arc::new(Manager::open(root.path()).unwrap());
    manager.set_provider(json!({"name":"Local","api":"chat-completions","base_url":format!("http://{address}/v1")})).unwrap();
    let selected = manager.resolve(&Choice::new("Local", "local:8b")).unwrap();
    let mut model = Model::new(selected.model.clone(), false);
    model.connection = Some((manager, selected));
    let (tx, mut rx) = tokio::sync::mpsc::channel(32);
    tokio::time::timeout(
        std::time::Duration::from_secs(10),
        model.next(
            "Be helpful.",
            &[json!({"role":"user","content":"Hi"})],
            &[],
            tx,
        ),
    )
    .await
    .unwrap()
    .unwrap();
    assert!(matches!(rx.recv().await, Some(Progress::OutputStarted)));
    assert!(matches!(rx.recv().await,Some(Progress::Delta(s)) if s=="Hello"));
    assert!(
        matches!(rx.recv().await,Some(Progress::Complete(c)) if c.output[0]["content"][0]["text"]=="Hello")
    );
    server.await.unwrap();
}
