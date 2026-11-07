#![cfg(feature = "test-support")]
use openworker_sdk::{agents::*, *};
use serde_json::json;
use std::{
    path::PathBuf,
    sync::{
        atomic::{AtomicUsize, Ordering},
        Arc,
    },
    time::Duration,
};
use tokio::io::{AsyncReadExt, AsyncWriteExt};

async fn exercise(process: bool) {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url = format!("http://{}/v1", listener.local_addr().unwrap());
    let server = tokio::spawn(async move {
        for expected in ["original-token", "refreshed-token", "proactive-token"] {
            let (mut stream, _) = listener.accept().await.unwrap();
            let mut data = Vec::new();
            let mut buf = [0u8; 4096];
            let end = loop {
                let n = stream.read(&mut buf).await.unwrap();
                assert!(n > 0);
                data.extend_from_slice(&buf[..n]);
                if let Some(end) = data.windows(4).position(|w| w == b"\r\n\r\n") {
                    break end + 4;
                }
            };
            let headers = String::from_utf8_lossy(&data[..end]).to_ascii_lowercase();
            assert!(headers.contains(&format!("authorization: bearer {expected}")));
            let length: usize = headers
                .lines()
                .find_map(|l| l.strip_prefix("content-length: "))
                .unwrap()
                .parse()
                .unwrap();
            while data.len() < end + length {
                let n = stream.read(&mut buf).await.unwrap();
                assert!(n > 0);
                data.extend_from_slice(&buf[..n]);
            }
            let (status, content) = if expected == "original-token" {
                (
                    "401 Unauthorized",
                    "{\"error\":{\"code\":\"authentication_error\"}}".to_owned(),
                )
            } else {
                (
                    "200 OK",
                    format!(
                        "data: {}\n\ndata: [DONE]\n\n",
                        json!({"id":"fixture","choices":[{"index":0,"delta":{"content":"hello"},"finish_reason":"stop"}]})
                    ),
                )
            };
            stream.write_all(format!("HTTP/1.1 {status}\r\nContent-Type: text/event-stream\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{content}",content.len()).as_bytes()).await.unwrap();
        }
    });
    let tmp = tempfile::tempdir().unwrap();
    let exe = PathBuf::from(env!("CARGO_BIN_EXE_sdk-test-host"));
    let builder = Harness::builder()
        .state_dir(tmp.path().join("state"))
        .executable(&exe)
        .sandbox(Sandbox::Off);
    let h = if process {
        builder
            .managed(exe, RestartPolicy::default())
            .await
            .unwrap()
    } else {
        builder.start().await.unwrap()
    };
    let calls = Arc::new(AtomicUsize::new(0));
    let count = calls.clone();
    h.set_model_provider(
        ModelProvider::chat_completions("ExampleApp")
            .base_url(url)
            .refresh_access_token(move |reason| {
                assert!(matches!(reason, TokenRefreshReason::Rejected));
                count.fetch_add(1, Ordering::SeqCst);
                async {
                    Ok(AccessToken::new(
                        "refreshed-token",
                        openworker_runtime::provider_auth::now() + 900,
                    )
                    .unwrap())
                }
            }),
    )
    .await
    .unwrap();
    h.update_provider_credentials(
        "ExampleApp",
        AccessToken::new(
            "original-token",
            openworker_runtime::provider_auth::now() + 900,
        )
        .unwrap(),
    )
    .await
    .unwrap();
    h.register_tools(
        "app",
        [Tool::new("noop")
            .description("Unused")
            .input_schema(json!({"type":"object","properties":{}}))
            .handler(|_, _| async {
                panic!("Tool must not execute");
                #[allow(unreachable_code)]
                Ok(json!({}))
            })],
    )
    .await
    .unwrap();
    let agent = h
        .create_agent(
            Agent::new("helper")
                .instructions("Say hello")
                .tool("app.noop")
                .default_model(Model::new("ExampleApp", "fixture")),
        )
        .await
        .unwrap();
    let session = agent.new_session().await.unwrap();
    assert_eq!(
        session.run("hello").await.unwrap().status,
        RunStatus::Completed
    );
    h.update_provider_credentials(
        "ExampleApp",
        AccessToken::new(
            "proactive-token",
            openworker_runtime::provider_auth::now() + 900,
        )
        .unwrap(),
    )
    .await
    .unwrap();
    assert_eq!(
        session.run("again").await.unwrap().status,
        RunStatus::Completed
    );
    assert_eq!(calls.load(Ordering::SeqCst), 1);
    h.shutdown().await.unwrap();
    server.await.unwrap();
}
#[tokio::test]
async fn embedded_access_token_refresh_and_rotation() {
    tokio::time::timeout(Duration::from_secs(60), exercise(false))
        .await
        .unwrap();
}
#[tokio::test]
async fn managed_access_token_refresh_and_rotation() {
    tokio::time::timeout(Duration::from_secs(60), exercise(true))
        .await
        .unwrap();
}
