#![cfg(all(feature = "compliance-export", feature = "test-support"))]
use openworker_sdk::{agents::*, *};
use serde_json::{json, Value};
use std::{
    sync::{Arc, Mutex},
    time::Duration,
};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn siem_outage_retries_evidence_without_running_an_agent() {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let endpoint = format!("http://{}/events", listener.local_addr().unwrap());
    let received = Arc::new(Mutex::new(vec![]));
    let seen = received.clone();
    let server = tokio::spawn(async move {
        for i in 0..2 {
            let (mut socket, _) = listener.accept().await.unwrap();
            let mut raw = vec![];
            let mut chunk = [0; 4096];
            loop {
                let n = socket.read(&mut chunk).await.unwrap();
                assert!(n > 0);
                raw.extend_from_slice(&chunk[..n]);
                if let Some(end) = raw.windows(4).position(|v| v == b"\r\n\r\n") {
                    let h = String::from_utf8_lossy(&raw[..end]);
                    let size = h
                        .lines()
                        .find_map(|l| {
                            l.to_ascii_lowercase()
                                .strip_prefix("content-length: ")
                                .and_then(|v| v.parse::<usize>().ok())
                        })
                        .unwrap();
                    if raw.len() >= end + 4 + size {
                        let body: Value =
                            serde_json::from_slice(&raw[end + 4..end + 4 + size]).unwrap();
                        seen.lock().unwrap().push(body);
                        break;
                    }
                }
            }
            let status = if i == 0 { "503 Unavailable" } else { "200 OK" };
            socket
                .write_all(
                    format!("HTTP/1.1 {status}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                        .as_bytes(),
                )
                .await
                .unwrap();
        }
    });
    let root = tempfile::tempdir().unwrap();
    let key = format!("OW_SIEM_FIXTURE_{}", std::process::id());
    std::env::set_var(&key, "fake-siem-key");
    let h = Harness::builder()
        .state_dir(root.path().join("state"))
        .executable(env!("CARGO_BIN_EXE_sdk-test-host"))
        .sandbox(Sandbox::Off)
        .scripted(true)
        .compliance_sink(ComplianceSink {
            name: "fixture-v1".into(),
            endpoint,
            credential_env: key.clone(),
            allow_loopback_http: true,
        })
        .start()
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
    let s = a.new_session().await.unwrap();
    tokio::time::timeout(Duration::from_secs(15), async {
        loop {
            if s.compliance_delivery_health().await.unwrap()["sinks"]
                .as_array()
                .is_some_and(|v| !v.is_empty() && v[0]["pending"] == 0)
            {
                break;
            }
            tokio::time::sleep(Duration::from_millis(25)).await;
        }
    })
    .await
    .unwrap();
    server.await.unwrap();
    let all = received.lock().unwrap().clone();
    assert_eq!(all.len(), 2);
    assert_eq!(all[0], all[1]);
    assert!(!json!(all).to_string().contains("fake-siem-key"));
    let snapshot = h.client().snapshot(s.id()).await.unwrap();
    assert!(snapshot["turn"].is_null());
    h.shutdown().await.unwrap();
    std::env::remove_var(key);
}
