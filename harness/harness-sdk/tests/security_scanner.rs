#![cfg(all(feature = "test-support", feature = "security-adapters"))]
use openworker_sdk::{agents::*, *};
use serde_json::{json, Value};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
async fn exercise(managed: bool) {
    let root = tempfile::tempdir().unwrap();
    let key = format!(
        "OW_PRISMA_FIXTURE_{}_{}",
        std::process::id(),
        usize::from(managed)
    );
    std::env::set_var(&key, "MOCK_PRISMA_KEY");
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let endpoint = format!(
        "http://{}/v1/scan/sync/request",
        listener.local_addr().unwrap()
    );
    let server = tokio::spawn(async move {
        let (mut socket, _) = listener.accept().await.unwrap();
        let mut raw = vec![];
        let mut chunk = [0; 4096];
        loop {
            let n = socket.read(&mut chunk).await.unwrap();
            assert!(n > 0);
            raw.extend_from_slice(&chunk[..n]);
            if let Some(end) = raw.windows(4).position(|v| v == b"\r\n\r\n") {
                let header = String::from_utf8_lossy(&raw[..end]);
                let size = header
                    .lines()
                    .find_map(|l| {
                        l.to_ascii_lowercase()
                            .strip_prefix("content-length: ")
                            .and_then(|v| v.parse::<usize>().ok())
                    })
                    .unwrap();
                if raw.len() >= end + 4 + size {
                    assert!(header.contains("x-pan-token: MOCK_PRISMA_KEY"));
                    let body: Value =
                        serde_json::from_slice(&raw[end + 4..end + 4 + size]).unwrap();
                    assert_eq!(body["contents"][0]["prompt"], "BLOCKED_SENTINEL");
                    assert!(!body.to_string().contains("MOCK_PRISMA_KEY"));
                    let reply =
                        json!({"action":"block","tr_id":body["tr_id"],"scan_id":"mock-scan"})
                            .to_string();
                    socket.write_all(format!("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{reply}",reply.len()).as_bytes()).await.unwrap();
                    break;
                }
            }
        }
    });
    let exe = std::path::PathBuf::from(env!("CARGO_BIN_EXE_sdk-test-host"));
    let b = Harness::builder()
        .state_dir(root.path().join("state"))
        .executable(&exe)
        .inherit_env(&key)
        .sandbox(Sandbox::Off)
        .scripted(true)
        .compliance(Default::default());
    let h = if managed {
        b.managed(exe, RestartPolicy::default()).await
    } else {
        b.start().await
    }
    .unwrap();
    let reference = guardrails::HandlerRef {
        name: "airs".into(),
        version: "1".into(),
    };
    h.register_security_adapter(
        reference.clone(),
        security_adapters::Config {
            endpoint,
            protocol: security_adapters::Protocol::PrismaAirs {
                profile: "fixture".into(),
            },
            credential_env: key.clone(),
            timeout_ms: 1000,
            allow_loopback_http: true,
        },
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
    let s = a
        .new_session_with(SessionOptions::default().safety(guardrails::Config {
            hooks: vec![guardrails::Hook {
                name: "airs".into(),
                version: "1".into(),
                when: guardrails::HookPhase::BeforeInput,
                tools: vec![],
                handler: reference,
                timeout_ms: 2000,
            }],
            ..Default::default()
        }))
        .await
        .unwrap();
    assert!(s.send("BLOCKED_SENTINEL").await.is_err());
    let snapshot = h.client().snapshot(s.id()).await.unwrap();
    assert!(snapshot["turn"].is_null());
    assert!(!snapshot.to_string().contains("BLOCKED_SENTINEL"));
    let evidence = s.compliance_activity(Default::default()).await.unwrap();
    assert!(!evidence.to_string().contains("BLOCKED_SENTINEL"));
    assert!(evidence["activities"]
        .as_array()
        .unwrap()
        .iter()
        .any(|e| e["facts"]["code"] == "EXTERNAL_SECURITY_BLOCK"));
    h.shutdown().await.unwrap();
    server.await.unwrap();
    std::env::remove_var(key);
}
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn embedded_mock_prisma_blocks_before_admission() {
    tokio::time::timeout(std::time::Duration::from_secs(60), exercise(false))
        .await
        .unwrap();
}
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn managed_mock_prisma_uses_declared_host_credential() {
    tokio::time::timeout(std::time::Duration::from_secs(60), exercise(true))
        .await
        .unwrap();
}
