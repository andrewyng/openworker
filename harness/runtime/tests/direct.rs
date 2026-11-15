#![cfg(target_os = "linux")]
use openworker_runtime::{direct, execution::Backend, model::Model, permissions, runtime::Runtime};
use serde_json::{json, Value};
use std::{path::Path, process::Stdio, time::Duration};
use tokio::io::{AsyncReadExt, AsyncWriteExt};

fn binary() -> &'static Path {
    Path::new(env!("CARGO_BIN_EXE_openworker"))
}
async fn client(work: &Path) -> direct::Mcp {
    let mut c = direct::Mcp::launch(
        binary(),
        work,
        &[
            binary().to_str().unwrap().into(),
            "mcp-example".into(),
            "--workspace".into(),
            work.to_str().unwrap().into(),
        ],
    )
    .await
    .unwrap();
    c.start().await.unwrap();
    c
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn repeated_eof_cleanup_keeps_owned_identity_checks_race_safe() {
    let tmp = tempfile::tempdir().unwrap();
    for step in 0..256 {
        let mut c = client(tmp.path()).await;
        c.request(
            "tools/call",
            json!({"name":"example_echo","arguments":{"text":"cleanup stress"}}),
        )
        .await
        .unwrap();
        if let Err(e) = c.close().await {
            panic!("close iteration {step}: {e:#}");
        }
        gone(c.identity.pid).await;
    }
}

#[tokio::test]
async fn empty_cmdline_waits_for_actual_thread_group_exit_without_signaling() {
    use tokio::io::{AsyncBufReadExt, BufReader};
    let tmp = tempfile::tempdir().unwrap();
    let helper = tmp.path().join("exit-group");
    let source = Path::new(env!("CARGO_MANIFEST_DIR")).join("../tests/fixtures/exit-group.c");
    assert!(std::process::Command::new("cc")
        .arg("-pthread")
        .arg(source)
        .arg("-o")
        .arg(&helper)
        .status()
        .unwrap()
        .success());
    let mut child = tokio::process::Command::new(&helper)
        .arg("owned-exit-fixture")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .kill_on_drop(true)
        .spawn()
        .unwrap();
    let pid = child.id().unwrap();
    let mut out = BufReader::new(child.stdout.take().unwrap());
    let mut ready = String::new();
    tokio::time::timeout(Duration::from_secs(5), out.read_line(&mut ready))
        .await
        .unwrap()
        .unwrap();
    assert_eq!(ready, "READY\n");
    let identity = direct::identity(pid, "owned-exit-fixture").unwrap();
    let mut input = child.stdin.take().unwrap();
    input.write_all(b"1").await.unwrap();
    tokio::time::timeout(Duration::from_secs(5), async {
        while !std::fs::read(format!("/proc/{pid}/cmdline"))
            .unwrap()
            .is_empty()
        {
            tokio::time::sleep(Duration::from_millis(5)).await;
        }
    })
    .await
    .unwrap();
    let cleanup = direct::cleanup(&identity);
    tokio::pin!(cleanup);
    tokio::select! {biased;
        result=&mut cleanup=>panic!("Cleanup returned before confirmed group exit: {result:?}"),
        _=tokio::time::sleep(Duration::from_millis(30))=>{}
    }
    input.write_all(b"2").await.unwrap();
    cleanup.await.unwrap();
    assert!(
        child.wait().await.unwrap().success(),
        "Cleanup must wait, not send SIGTERM to an unverifiable process"
    );
}
async fn wait_file(path: &Path) -> String {
    tokio::time::timeout(Duration::from_secs(5), async {
        loop {
            if let Ok(s) = std::fs::read_to_string(path) {
                if !s.trim().is_empty() {
                    return s;
                }
            }
            tokio::time::sleep(Duration::from_millis(20)).await;
        }
    })
    .await
    .expect("fixture file never appeared")
}
async fn gone(pid: u32) {
    tokio::time::timeout(Duration::from_secs(5), async {
        while Path::new(&format!("/proc/{pid}")).exists() {
            tokio::time::sleep(Duration::from_millis(20)).await;
        }
    })
    .await
    .expect("owned process was not reaped");
}

#[tokio::test]
async fn supervisor_waits_for_durable_start_and_closes_before_execution() {
    let tmp = tempfile::tempdir().unwrap();
    let marker = tmp.path().join("unexpected");
    let mut c = direct::Mcp::launch(
        binary(),
        tmp.path(),
        &[
            "/bin/sh".into(),
            "-c".into(),
            format!("touch {}; sleep 30", marker.display()),
        ],
    )
    .await
    .unwrap();
    tokio::time::sleep(Duration::from_millis(50)).await;
    assert!(!marker.exists());
    let pid = c.identity.pid;
    c.close().await.unwrap();
    gone(pid).await;
    assert!(!marker.exists());
}

#[tokio::test]
async fn direct_mcp_roundtrip_is_not_filesystem_isolation() {
    let tmp = tempfile::tempdir().unwrap();
    let work = tmp.path().join("work");
    std::fs::create_dir(&work).unwrap();
    std::fs::write(tmp.path().join("outside-marker"), "synthetic-no-secret").unwrap();
    let mut c = client(&work).await;
    let denied = c
        .request(
            "tools/call",
            json!({"name":"read_file","arguments":{"path":"../outside-marker"}}),
        )
        .await
        .unwrap();
    assert_eq!(denied["isError"], true); // structured guard remains
    let shell = c
        .request(
            "tools/call",
            json!({"name":"run_shell","arguments":{"command":"cat ../outside-marker"}}),
        )
        .await
        .unwrap();
    assert!(shell.to_string().contains("synthetic-no-secret")); // but shell has host access
    let write = c
        .request(
            "tools/call",
            json!({"name":"write_file","arguments":{"path":"proof","content":"native"}}),
        )
        .await
        .unwrap();
    assert_eq!(write["isError"], false);
    assert_eq!(
        std::fs::read_to_string(work.join("proof")).unwrap(),
        "native"
    );
    c.close().await.unwrap();
}

#[tokio::test]
async fn timeout_reaps_detached_descendants_and_poisons_connection() {
    let tmp = tempfile::tempdir().unwrap();
    let mut c = client(tmp.path()).await;
    // A session-detached grandchild defeats process-group-only cleanup.
    let command = "setsid sh -c 'echo $$ > detached.pid; sleep 30' & wait";
    let result = c
        .request_timeout(
            "tools/call",
            json!({"name":"run_shell","arguments":{"command":command}}),
            Duration::from_millis(500),
        )
        .await;
    assert!(result.is_err());
    let pid = wait_file(&tmp.path().join("detached.pid"))
        .await
        .trim()
        .parse()
        .unwrap();
    gone(pid).await;
    assert!(c.request("tools/list", json!({})).await.is_err());
}

#[tokio::test]
async fn eof_reaps_owned_server_but_leaves_unrelated_process_alive() {
    let tmp = tempfile::tempdir().unwrap();
    let c = client(tmp.path()).await;
    let pid = c.identity.pid;
    let mut unrelated = tokio::process::Command::new("/bin/sleep")
        .arg("30")
        .kill_on_drop(true)
        .spawn()
        .unwrap();
    drop(c);
    gone(pid).await;
    assert!(unrelated.try_wait().unwrap().is_none());
    unrelated.kill().await.unwrap();
    unrelated.wait().await.unwrap();
}

#[tokio::test]
async fn cleanup_never_signals_a_reused_or_unowned_pid() {
    let mut id = direct::identity(std::process::id(), "not-our-supervisor").unwrap();
    assert!(direct::cleanup(&id).await.is_err()); // no matching ownership token
    id.start_ticks += 1;
    direct::cleanup(&id).await.unwrap(); // mismatched lifetime: no signal
    id.start_ticks -= 1;
    id.boot_id = "different-boot".into();
    direct::cleanup(&id).await.unwrap();
}

#[tokio::test]
async fn owner_sigkill_cleans_descendants_even_when_stdin_stays_open() {
    let tmp = tempfile::tempdir().unwrap();
    // The wrapper stays the supervisor's parent. Our test holds the stdin pipe
    // open after killing it, so this specifically tests PDEATHSIG, not EOF.
    let script="\"$1\" direct-supervisor --owner $$ --token owner-death-test --workspace \"$2\" -- /bin/sh -c \"$3\"; :";
    let mut owner = tokio::process::Command::new("/bin/sh")
        .args(["-c", script, "owner-wrapper"])
        .arg(binary())
        .arg(tmp.path())
        .arg("setsid sh -c 'echo $$ > detached.pid; sleep 30' & wait")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit())
        .kill_on_drop(true)
        .spawn()
        .unwrap();
    let mut stdin = owner.stdin.take().unwrap();
    let mut stdout = owner.stdout.take().unwrap();
    let mut ready = [0; 6];
    tokio::time::timeout(Duration::from_secs(5), stdout.read_exact(&mut ready))
        .await
        .unwrap()
        .unwrap();
    assert_eq!(&ready, b"READY\n");
    stdin.write_all(b"START\n").await.unwrap();
    let pid = wait_file(&tmp.path().join("detached.pid"))
        .await
        .trim()
        .parse()
        .unwrap();
    owner.kill().await.unwrap();
    owner.wait().await.unwrap();
    gone(pid).await;
    drop(stdin);
}

#[tokio::test]
async fn supervisor_scrubs_server_environment() {
    let tmp = tempfile::tempdir().unwrap();
    let mut child = tokio::process::Command::new(binary())
        .args([
            "direct-supervisor",
            "--owner",
            &std::process::id().to_string(),
            "--token",
            "env-test",
            "--workspace",
        ])
        .arg(tmp.path())
        .args(["--", "/bin/sh", "-c", "env > environment.txt; sleep 30"])
        .env("OPENAI_API_KEY", "synthetic-key-not-a-secret")
        .env("TEST_PRIVATE_TOKEN", "synthetic-token")
        .env(
            "HTTP_PROXY",
            "http://synthetic-controller-proxy.invalid:1234",
        )
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .kill_on_drop(false)
        .spawn()
        .unwrap();
    let id = direct::identity(child.id().unwrap(), "env-test").unwrap();
    let mut input = child.stdin.take().unwrap();
    let mut output = child.stdout.take().unwrap();
    let mut ready = [0; 6];
    output.read_exact(&mut ready).await.unwrap();
    input.write_all(b"START\n").await.unwrap();
    let environment = wait_file(&tmp.path().join("environment.txt")).await;
    assert!(!environment.contains("OPENAI_API_KEY"));
    assert!(!environment.contains("TEST_PRIVATE_TOKEN"));
    assert!(!environment.contains("HTTP_PROXY"));
    drop(input);
    direct::cleanup(&id).await.unwrap();
    child.wait().await.unwrap();
}

#[tokio::test]
async fn backend_identity_is_persisted_and_not_an_approval_mode() {
    let tmp = tempfile::tempdir().unwrap();
    let work = tmp.path().join("work");
    std::fs::create_dir(&work).unwrap();
    let bundle = Path::new(env!("CARGO_MANIFEST_DIR")).join("../examples/assistant");
    let state = tmp.path().join("state");
    let rt = Runtime::open_with_backend(
        &state,
        &bundle,
        binary(),
        Model::new("fixture".into(), true),
        Backend::Off,
    )
    .unwrap();
    let init = rt
        .dispatch("initialize", json!({"protocol_version":"0.1"}))
        .await
        .unwrap();
    assert_eq!(init["execution"]["enforcement"], "none");
    let created = rt
        .dispatch(
            "session/create",
            json!({"name":"off","coworker":"assistant-example","workspace":work}),
        )
        .await
        .unwrap();
    let sid = created["session"]["id"].as_str().unwrap().to_string();
    let s = rt.store.lock().unwrap().get(&sid).unwrap();
    assert_eq!(s.permissions.mode, permissions::Mode::Ask);
    let context: Value = serde_json::from_str(
        &permissions::review_context(
            &s,
            &rt.bundle,
            &json!({"name":"run_shell","arguments":"{}"}),
        )
        .unwrap(),
    )
    .unwrap();
    assert_eq!(context["runtime_facts"]["sandbox"], "off");
    assert_ne!(context["runtime_facts"]["network"], "deny-all");
    drop(rt);
    let rt = Runtime::open(
        &state,
        &bundle,
        binary(),
        Model::new("fixture".into(), true),
    )
    .unwrap();
    assert!(rt
        .dispatch(
            "turn/start",
            json!({"session_id":sid,"submission_id":"no","text":"should not run"})
        )
        .await
        .is_err());
    assert!(rt.store.lock().unwrap().get(&sid).unwrap().turn.is_none());
}
