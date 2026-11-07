use openworker_guardrails::{Context, Decision, Handler, Phase};
use openworker_security_adapters::{Config, Credential, Protocol, Scanner};
use serde_json::{json, Value};
use std::{sync::Arc, time::Duration};
use tokio::{
    io::{AsyncReadExt, AsyncWriteExt},
    net::TcpListener,
};
use tokio_util::sync::CancellationToken;
use zeroize::Zeroizing;
struct Key;
impl Credential for Key {
    fn resolve(&self) -> anyhow::Result<Zeroizing<String>> {
        Ok(Zeroizing::new("MOCK_ONLY_KEY".into()))
    }
}
fn context() -> Context {
    Context {
        call_id: None,
        evaluation_id: "scan-1".into(),
        session_id: "s_one".into(),
        turn_id: Some("turn-1".into()),
        phase: Phase::UserInput,
        text: "selected text".into(),
        tool: None,
        arguments: None,
        matches: vec![],
        context: json!({"history":"MUST_NOT_SEND","secret":"MUST_NOT_SEND"}),
    }
}
async fn mock(status: u16, body: String, delay: u64) -> (String, tokio::task::JoinHandle<String>) {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let task = tokio::spawn(async move {
        let (mut stream, _) = listener.accept().await.unwrap();
        let mut bytes = vec![];
        let mut chunk = [0; 4096];
        loop {
            let n = stream.read(&mut chunk).await.unwrap();
            if n == 0 {
                break;
            }
            bytes.extend_from_slice(&chunk[..n]);
            if let Some(i) = bytes.windows(4).position(|p| p == b"\r\n\r\n") {
                let header = String::from_utf8_lossy(&bytes[..i]);
                let size = header
                    .lines()
                    .find_map(|l| {
                        l.to_ascii_lowercase()
                            .strip_prefix("content-length: ")
                            .and_then(|s| s.parse::<usize>().ok())
                    })
                    .unwrap_or(0);
                if bytes.len() >= i + 4 + size {
                    break;
                }
            }
        }
        tokio::time::sleep(Duration::from_millis(delay)).await;
        let reply=format!("HTTP/1.1 {status} Test\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",body.len());
        let _ = stream.write_all(reply.as_bytes()).await;
        String::from_utf8(bytes).unwrap()
    });
    (format!("http://{address}/v1/scan/sync/request"), task)
}
fn config(endpoint: String, protocol: Protocol) -> Config {
    Config {
        endpoint,
        protocol,
        credential_env: "MOCK_KEY".into(),
        timeout_ms: 500,
        allow_loopback_http: true,
    }
}
#[tokio::test]
async fn prisma_mock_allow_block_and_selected_context() {
    for action in ["allow", "block"] {
        let (url, server) = mock(
            200,
            json!({"action":action,"tr_id":"scan-1","scan_id":"mock-scan"}).to_string(),
            0,
        )
        .await;
        let scanner = Scanner::new(
            config(
                url,
                Protocol::PrismaAirs {
                    profile: "test-profile".into(),
                },
            ),
            Arc::new(Key),
        )
        .unwrap();
        let decision = scanner
            .call(context(), CancellationToken::new())
            .await
            .unwrap();
        assert_eq!(matches!(decision, Decision::Allow), action == "allow");
        let request = server.await.unwrap();
        assert!(request.contains("x-pan-token: MOCK_ONLY_KEY"));
        assert!(!request.contains("MUST_NOT_SEND"));
        let body: Value = serde_json::from_str(request.split("\r\n\r\n").nth(1).unwrap()).unwrap();
        assert_eq!(body["tr_id"], "scan-1");
        assert_eq!(body["ai_profile"]["profile_name"], "test-profile");
        assert_eq!(body["contents"][0]["prompt"], "selected text");
    }
}
#[tokio::test]
async fn malformed_unknown_mismatched_and_http_failures_are_not_allows() {
    for (status, body) in [
        (200, "{}"),
        (200, "not json"),
        (200, "{\"action\":\"unsure\"}"),
        (200, "{\"action\":\"allow\",\"tr_id\":\"other\"}"),
        (401, "PRIVATE_ERROR"),
        (429, "PRIVATE_ERROR"),
        (500, "PRIVATE_ERROR"),
        (302, "PRIVATE_ERROR"),
    ] {
        let (url, server) = mock(status, body.into(), 0).await;
        let scanner = Scanner::new(
            config(
                url,
                Protocol::PrismaAirs {
                    profile: "test".into(),
                },
            ),
            Arc::new(Key),
        )
        .unwrap();
        let error = scanner
            .call(context(), CancellationToken::new())
            .await
            .unwrap_err()
            .to_string();
        assert!(!error.contains("PRIVATE_ERROR"));
        assert!(!error.contains("MOCK_ONLY_KEY"));
        server.await.unwrap();
    }
}
#[tokio::test]
async fn bounded_timeout_cancellation_and_oversize() {
    let (url, server) = mock(200, "{}".into(), 100).await;
    let mut c = config(url, Protocol::SelectedContext);
    c.timeout_ms = 10;
    let scanner = Scanner::new(c, Arc::new(Key)).unwrap();
    assert!(scanner
        .call(context(), CancellationToken::new())
        .await
        .is_err());
    server.await.unwrap();
    let (url, server) = mock(200, " ".repeat(65537), 0).await;
    let scanner = Scanner::new(config(url, Protocol::SelectedContext), Arc::new(Key)).unwrap();
    assert!(scanner
        .call(context(), CancellationToken::new())
        .await
        .is_err());
    server.await.unwrap();
    let scanner = Scanner::new(
        config(
            "https://unused.example/scan".into(),
            Protocol::SelectedContext,
        ),
        Arc::new(Key),
    )
    .unwrap();
    let cancel = CancellationToken::new();
    cancel.cancel();
    assert!(scanner.call(context(), cancel).await.is_err());
    let mut c = context();
    c.phase = Phase::ModelInput;
    assert!(scanner.call(c, CancellationToken::new()).await.is_err());
}
#[test]
fn endpoints_do_not_accept_unencrypted_remote_hosts_or_embedded_secrets() {
    for url in [
        "http://example.com/scan",
        "https://token@example.com/scan",
        "https://example.com/scan?key=secret",
        "https://example.com/scan#secret",
    ] {
        assert!(
            Scanner::new(config(url.into(), Protocol::SelectedContext), Arc::new(Key)).is_err()
        );
    }
}
