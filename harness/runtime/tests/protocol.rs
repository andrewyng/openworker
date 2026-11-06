#![cfg(unix)]
use serde_json::{json, Value};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};

fn runner_fixture(path: &std::path::Path) {
    // Transport tests never execute this file. Use the platform's native header.
    let bytes = if cfg!(target_os = "macos") {
        b"\xcf\xfa\xed\xfe-test-fixture".as_slice()
    } else {
        b"\x7fELF-test-fixture".as_slice()
    };
    std::fs::write(path, bytes).unwrap();
}

#[tokio::test]
async fn stdio_initialization_validation_snapshot_and_eof() {
    let root = tempfile::tempdir().unwrap();
    let state = root.path().join("state");
    let work = root.path().join("work");
    std::fs::create_dir(&work).unwrap();
    let runner = root.path().join("runner");
    runner_fixture(&runner);
    let bundle = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../examples/assistant");
    let mut child = tokio::process::Command::new(env!("CARGO_BIN_EXE_openworker"))
        .args(["serve", "--stdio", "--scripted", "--state-dir"])
        .arg(&state)
        .arg("--bundle")
        .arg(bundle)
        .arg("--runner")
        .arg(runner)
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::inherit())
        .kill_on_drop(true)
        .spawn()
        .unwrap();
    let mut stdin = child.stdin.take().unwrap();
    let mut stdout = BufReader::new(child.stdout.take().unwrap());
    async fn rpc(
        input: &mut tokio::process::ChildStdin,
        output: &mut BufReader<tokio::process::ChildStdout>,
        v: Value,
    ) -> Value {
        input.write_all(format!("{v}\n").as_bytes()).await.unwrap();
        let mut line = String::new();
        tokio::time::timeout(
            std::time::Duration::from_secs(5),
            output.read_line(&mut line),
        )
        .await
        .unwrap()
        .unwrap();
        serde_json::from_str(&line).unwrap()
    }
    let first = rpc(
        &mut stdin,
        &mut stdout,
        json!({"jsonrpc":"2.0","id":1,"method":"session/list","params":{}}),
    )
    .await;
    assert!(first.get("error").is_some());
    let init =
        json!({"jsonrpc":"2.0","id":2,"method":"initialize","params":{"protocol_version":"0.1"}});
    let ready = rpc(&mut stdin, &mut stdout, init.clone()).await;
    assert_eq!(ready["result"]["implementation"], "native-rust");
    assert!(rpc(&mut stdin, &mut stdout, init)
        .await
        .get("error")
        .is_some());
    let invalid=rpc(&mut stdin,&mut stdout,json!({"jsonrpc":"2.0","id":3,"method":"session/create","params":{"name":"No","coworker":"assistant-example","workspace":state}})).await;
    assert!(invalid.get("error").is_some());
    let created=rpc(&mut stdin,&mut stdout,json!({"jsonrpc":"2.0","id":4,"method":"session/create","params":{"name":"Transport","coworker":"assistant-example","workspace":work}})).await;
    let sid = created["result"]["session"]["id"].as_str().unwrap();
    let unsupported=rpc(&mut stdin,&mut stdout,json!({"jsonrpc":"2.0","id":40,"method":"session/create","params":{"name":"Invalid setting","coworker":"assistant-example","workspace":work,"unattended":true}})).await;
    assert!(unsupported.get("error").is_some());
    let snap = rpc(
        &mut stdin,
        &mut stdout,
        json!({"jsonrpc":"2.0","id":5,"method":"session/attach","params":{"session_id":sid}}),
    )
    .await;
    assert_eq!(snap["result"]["session"]["name"], "Transport");
    assert_eq!(
        snap["result"]["session"]["permissions"],
        json!({"mode":"ask","auto_skip_questions":false})
    );
    assert!(snap["result"]["cursor"].as_i64().unwrap() > 0);
    drop(stdin);
    assert!(
        tokio::time::timeout(std::time::Duration::from_secs(5), child.wait())
            .await
            .unwrap()
            .unwrap()
            .success()
    );
}

#[tokio::test]
async fn oversized_stdio_frame_fails_closed() {
    let root = tempfile::tempdir().unwrap();
    let runner = root.path().join("runner");
    runner_fixture(&runner);
    let bundle = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../examples/assistant");
    let mut child = tokio::process::Command::new(env!("CARGO_BIN_EXE_openworker"))
        .args(["serve", "--stdio", "--scripted", "--state-dir"])
        .arg(root.path().join("state"))
        .arg("--bundle")
        .arg(bundle)
        .arg("--runner")
        .arg(runner)
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::piped())
        .kill_on_drop(true)
        .spawn()
        .unwrap();
    let mut input = child.stdin.take().unwrap();
    let mut output = BufReader::new(child.stdout.take().unwrap());
    input
        .write_all(&vec![b'x'; openworker_runtime::MAX_FRAME + 1])
        .await
        .unwrap();
    let mut line = String::new();
    tokio::time::timeout(
        std::time::Duration::from_secs(5),
        output.read_line(&mut line),
    )
    .await
    .unwrap()
    .unwrap();
    let v: Value = serde_json::from_str(&line).unwrap();
    assert_eq!(v["error"]["code"], -32600);
    drop(input);
    tokio::time::timeout(std::time::Duration::from_secs(5), child.wait())
        .await
        .unwrap()
        .unwrap();
}

#[tokio::test]
async fn sigint_exits_with_stdin_still_open() {
    let root = tempfile::tempdir().unwrap();
    let runner = root.path().join("runner");
    runner_fixture(&runner);
    let bundle = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../examples/assistant");
    let mut child = tokio::process::Command::new(env!("CARGO_BIN_EXE_openworker"))
        .args(["serve", "--stdio", "--scripted", "--state-dir"])
        .arg(root.path().join("state"))
        .arg("--bundle")
        .arg(bundle)
        .arg("--runner")
        .arg(runner)
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::piped())
        .kill_on_drop(true)
        .spawn()
        .unwrap();
    let mut input = child.stdin.take().unwrap();
    let mut output = BufReader::new(child.stdout.take().unwrap());
    input.write_all(b"{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"initialize\",\"params\":{\"protocol_version\":\"0.1\"}}\n").await.unwrap();
    let mut line = String::new();
    tokio::time::timeout(
        std::time::Duration::from_secs(5),
        output.read_line(&mut line),
    )
    .await
    .unwrap()
    .unwrap();
    unsafe {
        libc::kill(child.id().unwrap() as i32, libc::SIGINT);
    }
    assert!(
        tokio::time::timeout(std::time::Duration::from_secs(5), child.wait())
            .await
            .unwrap()
            .unwrap()
            .success()
    );
    drop(input);
}
