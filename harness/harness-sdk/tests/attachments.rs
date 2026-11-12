#![cfg(all(feature = "test-support", feature = "attachments"))]
use openworker_sdk::{agents::*, *};
use serde_json::Value;
use std::{
    path::PathBuf,
    sync::{Arc, Mutex},
    time::Duration,
};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
async fn exercise(managed: bool, small_stack: bool) {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url = format!("http://{}/v1", listener.local_addr().unwrap());
    let requests = Arc::new(Mutex::new(vec![]));
    let captured = requests.clone();
    let server = tokio::spawn(async move {
        for _ in 0..3 {
            let (mut socket, _) = listener.accept().await.unwrap();
            let mut raw = vec![];
            let mut buffer = [0; 8192];
            let end = loop {
                let n = socket.read(&mut buffer).await.unwrap();
                assert!(n > 0);
                raw.extend_from_slice(&buffer[..n]);
                if let Some(i) = raw.windows(4).position(|b| b == b"\r\n\r\n") {
                    break i + 4;
                }
            };
            let len = String::from_utf8_lossy(&raw[..end])
                .lines()
                .find_map(|l| {
                    l.to_lowercase()
                        .strip_prefix("content-length:")
                        .map(|v| v.trim().parse::<usize>().unwrap())
                })
                .unwrap();
            assert!(len < 2_000_000);
            while raw.len() < end + len {
                let n = socket.read(&mut buffer).await.unwrap();
                assert!(n > 0);
                raw.extend_from_slice(&buffer[..n]);
            }
            let body: Value = serde_json::from_slice(&raw[end..end + len]).unwrap();
            captured.lock().unwrap().push(body);
            let response="data: {\"id\":\"fixture\",\"choices\":[{\"index\":0,\"delta\":{\"content\":\"Read.\"},\"finish_reason\":\"stop\"}]}\n\ndata: [DONE]\n\n";
            socket.write_all(format!("HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",response.len(),response).as_bytes()).await.unwrap();
        }
    });
    let root = tempfile::tempdir().unwrap();
    let original = PathBuf::from(env!("CARGO_BIN_EXE_sdk-test-host"));
    let binary = if small_stack {
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let path = root.path().join("small-stack-host");
            let quoted = original.to_str().unwrap().replace('\'', "'\\''");
            std::fs::write(
                &path,
                format!("#!/bin/sh\nulimit -s 1024\nexec '{quoted}' \"$@\"\n"),
            )
            .unwrap();
            std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o700)).unwrap();
            path
        }
        #[cfg(not(unix))]
        {
            panic!("Process-stack wrapper is Unix-only")
        }
    } else {
        original.clone()
    };
    let builder = || {
        Harness::builder()
            .state_dir(root.path())
            .executable(&original)
            .sandbox(Sandbox::Off)
    };
    let harness = if managed {
        builder()
            .managed(binary.clone(), RestartPolicy::default())
            .await
    } else {
        builder().start().await
    }
    .unwrap();
    harness
        .set_model_provider(ModelProvider::chat_completions("Fixture").base_url(url.clone()))
        .await
        .unwrap();
    let definition = || {
        Agent::new("reader")
            .instructions("Read attached files.")
            .tools([] as [&str; 0])
            .memory(false)
            .planning(false)
            .scheduling(false)
            .default_model(Model::new("Fixture", "fixture"))
    };
    let session = harness
        .create_agent(definition())
        .await
        .unwrap()
        .new_session()
        .await
        .unwrap();
    let document = Attachment::from_bytes("notes.md", "text/plain", vec![65; 300000]).unwrap();
    let input = UserInput::new(vec![
        InputContent::text("Before"),
        document.clone().into(),
        InputContent::text("After"),
    ]);
    let stream = session.run_input_streamed(input.clone()).await.unwrap();
    let mut stream = stream;
    let mut deltas = String::new();
    while let Some(e) = stream.next().await {
        if let RunEvent::TextDelta { text, .. } = e {
            deltas.push_str(&text);
        }
    }
    assert_eq!(stream.finish().await.unwrap().status, RunStatus::Completed);
    assert_eq!(deltas, "Read.");
    let request = requests.lock().unwrap()[0].clone();
    let parts = &request["messages"][1]["content"];
    assert_eq!(parts[0]["text"], "Before");
    assert!(parts[1]["text"]
        .as_str()
        .unwrap()
        .starts_with("Attached text file: notes.md\n"));
    assert_eq!(parts[2]["text"], "After");
    let run = session
        .send_input(
            input.clone(),
            TurnOptions {
                submission_id: Some("same".into()),
                ..Default::default()
            },
        )
        .await
        .unwrap();
    assert_eq!(run.result().await.unwrap().status, RunStatus::Completed);
    assert_eq!(
        session
            .send_input(
                input,
                TurnOptions {
                    submission_id: Some("same".into()),
                    ..Default::default()
                }
            )
            .await
            .unwrap()
            .result()
            .await
            .unwrap()
            .status,
        RunStatus::Completed
    );
    assert_eq!(requests.lock().unwrap().len(), 2);
    assert!(session
        .send_input(
            UserInput::new(vec![InputContent::text("different")]),
            TurnOptions {
                submission_id: Some("same".into()),
                ..Default::default()
            }
        )
        .await
        .is_err());
    let reference = session.reference().clone();
    harness.shutdown().await.unwrap();
    drop(session);
    drop(harness);
    let harness = if managed {
        builder()
            .managed(binary.clone(), RestartPolicy::default())
            .await
    } else {
        builder().start().await
    }
    .unwrap();
    harness
        .set_model_provider(ModelProvider::chat_completions("Fixture").base_url(url))
        .await
        .unwrap();
    harness.create_agent(definition()).await.unwrap();
    let resumed = harness.resume(reference).await.unwrap();
    assert_eq!(
        resumed.run("Continue").await.unwrap().status,
        RunStatus::Completed
    );
    assert_eq!(
        requests.lock().unwrap()[2]["messages"][1]["content"],
        *parts
    );
    harness.shutdown().await.unwrap();
    server.await.unwrap();
}
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn attachments_work_in_process_and_managed_subprocess() {
    tokio::time::timeout(Duration::from_secs(120), async {
        exercise(false, false).await;
        exercise(true, false).await
    })
    .await
    .unwrap();
}

/// Reproduce the small Windows process-main stack on Unix using an owned
/// wrapper. The normal native Windows test already exercises its real stack.
#[cfg(unix)]
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn managed_attachments_fit_a_small_host_stack() {
    tokio::time::timeout(Duration::from_secs(120), exercise(true, true))
        .await
        .unwrap();
}
