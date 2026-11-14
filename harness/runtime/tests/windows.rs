#![cfg(windows)]
use openworker_runtime::{direct, execution::Backend, model::Model, platform, runtime::Runtime};
use serde_json::{json, Value};
use std::{
    path::Path,
    process::{Command, Stdio},
    time::Duration,
};
use tokio::io::AsyncWriteExt;

#[tokio::test]
async fn supervisor_handoff_preserves_coalesced_start_and_first_mcp_frame() {
    use tokio::io::{AsyncBufReadExt, AsyncReadExt, BufReader};
    let tmp = tempfile::tempdir().unwrap();
    let token = openworker_runtime::id("supervisor");
    let mut child = tokio::process::Command::new(binary())
        .args([
            "direct-supervisor",
            "--owner",
            &std::process::id().to_string(),
            "--token",
            &token,
            "--workspace",
        ])
        .arg(tmp.path())
        .arg("--")
        .arg(binary())
        .args(["mcp-example", "--workspace"])
        .arg(tmp.path())
        .env(
            "OPENWORKER_OWNER_START",
            platform::windows::process(std::process::id())
                .unwrap()
                .creation_time()
                .unwrap()
                .to_string(),
        )
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .kill_on_drop(true)
        .spawn()
        .unwrap();
    let mut input = child.stdin.take().unwrap();
    let mut output = BufReader::new(child.stdout.take().unwrap());
    let mut ready = [0; 6];
    tokio::time::timeout(Duration::from_secs(5), output.read_exact(&mut ready))
        .await
        .unwrap()
        .unwrap();
    assert_eq!(&ready, b"READY\n");
    // One write deliberately puts the handoff marker and first frame in the same
    // pipe read. Buffered Stdin in the supervisor must not swallow the MCP frame.
    input
        .write_all(
            b"START\n{\"jsonrpc\":\"2.0\",\"id\":7,\"method\":\"initialize\",\"params\":{}}\n",
        )
        .await
        .unwrap();
    let mut response = String::new();
    let result =
        tokio::time::timeout(Duration::from_secs(5), output.read_line(&mut response)).await;
    drop(input);
    let status = tokio::time::timeout(Duration::from_secs(5), child.wait())
        .await
        .unwrap()
        .unwrap();
    assert!(status.success());
    result
        .expect("first MCP frame was consumed by the supervisor")
        .unwrap();
    let response: Value = serde_json::from_str(&response).unwrap();
    assert_eq!(response["id"], 7);
}
fn binary() -> &'static Path {
    Path::new(env!("CARGO_BIN_EXE_openworker"))
}

#[test]
fn coworker_skills_and_package_identity_use_portable_relative_paths() {
    use sha2::{Digest, Sha256};
    let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("../examples/skilled");
    let bundle = openworker_runtime::bundle::Bundle::load(&root).unwrap();
    let loaded = bundle
        .skill_call(
            &["report-guide".into()],
            "load_skill",
            &json!({"name":"report-guide"}),
            Backend::Off,
        )
        .unwrap();
    assert!(loaded["resources"]
        .get("references/report-template.md")
        .is_some());
    let read = bundle
        .skill_call(
            &["report-guide".into()],
            "read_skill_resource",
            &json!({"name":"report-guide","path":"references/report-template.md"}),
            Backend::Off,
        )
        .unwrap();
    assert!(read["content"].as_str().unwrap().len() > 10);
    let mut hash = Sha256::new();
    for name in [
        "coworker/manifest.md",
        "coworker/skills/optional-guide/SKILL.md",
        "coworker/skills/report-guide/SKILL.md",
        "coworker/skills/report-guide/references/report-template.md",
        "tools.json",
    ] {
        let bytes = std::fs::read(root.join(name)).unwrap();
        hash.update((name.len() as u64).to_le_bytes());
        hash.update(name.as_bytes());
        hash.update((bytes.len() as u64).to_le_bytes());
        hash.update(bytes);
    }
    assert_eq!(bundle.digest, format!("{:x}", hash.finalize()));
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
#[tokio::test]
async fn native_stdio_mcp_and_owned_cleanup_with_spaces_unicode_and_quotes() {
    let tmp = tempfile::Builder::new()
        .prefix("ow test space ")
        .tempdir()
        .unwrap();
    for _ in 0..8 {
        let mut c = client(tmp.path()).await;
        let message = "quote \" backslash \\ Unicode ✓";
        let r = c
            .request(
                "tools/call",
                json!({"name":"example_echo","arguments":{"text":message}}),
            )
            .await
            .unwrap();
        let r: Value = serde_json::from_str(r["content"][0]["text"].as_str().unwrap()).unwrap();
        assert_eq!(r["text"], message);
        let mut wrong = c.identity.clone();
        wrong.start_ticks += 1;
        direct::cleanup(&wrong).await.unwrap();
        c.request("tools/list", json!({})).await.unwrap();
        c.close().await.unwrap();
        if let Ok(p) = platform::windows::process(c.identity.pid) {
            assert!(p.exited().unwrap());
        }
    }
}
#[tokio::test]
async fn powershell_descendants_end_with_the_command_and_environment_is_scrubbed() {
    let tmp = tempfile::tempdir().unwrap();
    let mut c = client(tmp.path()).await;
    let command="$p = Start-Process -FilePath ($env:SystemRoot + '\\System32\\WindowsPowerShell\\v1.0\\powershell.exe') -ArgumentList '-NoProfile -NonInteractive -Command Start-Sleep 60' -PassThru; @{pid=$p.Id;started=$p.StartTime.ToFileTimeUtc().ToString()} | ConvertTo-Json -Compress; if ($env:OPENAI_API_KEY) { Write-Output 'LEAKED' }";
    let r = c
        .request(
            "tools/call",
            json!({"name":"run_shell","arguments":{"command":command}}),
        )
        .await
        .unwrap();
    assert_eq!(r["isError"], false, "{r}");
    let r: Value = serde_json::from_str(r["content"][0]["text"].as_str().unwrap()).unwrap();
    let output = r["stdout"].as_str().unwrap();
    assert!(!output.contains("LEAKED"));
    let recorded: Value = serde_json::from_str(output.trim()).unwrap();
    let pid = recorded["pid"].as_u64().unwrap() as u32;
    let created = recorded["started"]
        .as_str()
        .unwrap()
        .parse::<u64>()
        .unwrap();
    if let Ok(p) = platform::windows::process(pid) {
        assert!(
            p.creation_time().unwrap() != created || p.exited().unwrap(),
            "original child is still running"
        );
    }
    c.close().await.unwrap();
}
#[test]
fn private_state_and_windows_paths_are_not_posix_permission_noops() {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path().canonicalize().unwrap();
    let private = root.join("private");
    platform::private_directory(&private).unwrap();
    let output = Command::new("icacls.exe").arg(&private).output().unwrap();
    assert!(output.status.success());
    let acl = String::from_utf8_lossy(&output.stdout);
    assert!(acl.contains("SYSTEM"), "{acl}");
    assert!(!acl.contains("BUILTIN\\Users"), "{acl}");
    assert!(!acl.contains("Everyone"), "{acl}");
    assert!(!acl.contains("(I)"), "{acl}");
    for p in [
        "NUL",
        "con.txt",
        "a:stream",
        "trailing.",
        "trailing ",
        "COM1.log",
        r"\\server\share\x",
    ] {
        assert!(platform::validate_path(Path::new(p)).is_err(), "{p}");
    }
    let target = root.join("target");
    std::fs::create_dir(&target).unwrap();
    let junction = root.join("junction");
    let result = Command::new("cmd.exe")
        .args(["/d", "/c", "mklink", "/J"])
        .arg(&junction)
        .arg(&target)
        .output()
        .unwrap();
    assert!(
        result.status.success(),
        "{}",
        String::from_utf8_lossy(&result.stderr)
    );
    assert!(openworker_runtime::resource_policy::checked_path(&junction).is_err());
    assert!(openworker_runtime::runner::path(&root, "junction/file.txt").is_err());
    assert!(platform::private_directory(&junction.join("child")).is_err());
    std::fs::remove_dir(&junction).unwrap();
}
#[test]
fn executable_checks_and_isolation_requests_are_explicit() {
    platform::executable(&std::fs::read(binary()).unwrap()).unwrap();
    assert!(platform::executable(b"\x7fELF").is_err());
    let tmp = tempfile::tempdir().unwrap();
    let result = Runtime::open_with_backend(
        tmp.path(),
        Path::new("missing-bundle"),
        binary(),
        Model::new("fixture".into(), true),
        Backend::Openshell,
    );
    assert!(result.err().unwrap().to_string().contains("No fallback"));
}
#[tokio::test]
async fn headless_eof_exits_without_a_blocking_stdin_worker() {
    let tmp = tempfile::tempdir().unwrap();
    let bundle = Path::new(env!("CARGO_MANIFEST_DIR")).join("../examples/assistant");
    let mut child = tokio::process::Command::new(binary())
        .args([
            "serve",
            "--stdio",
            "--sandbox",
            "off",
            "--scripted",
            "--state-dir",
        ])
        .arg(tmp.path())
        .arg("--bundle")
        .arg(bundle)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .kill_on_drop(true)
        .spawn()
        .unwrap();
    let mut input = child.stdin.take().unwrap();
    input.write_all(b"{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"initialize\",\"params\":{\"protocol_version\":\"0.1\"}}\n").await.unwrap();
    drop(input);
    let result = tokio::time::timeout(Duration::from_secs(5), child.wait_with_output())
        .await
        .unwrap()
        .unwrap();
    assert!(result.status.success(), "{result:?}");
    let init: Value = serde_json::from_slice(&result.stdout).unwrap();
    assert_eq!(
        init["result"]["capabilities"]["sandbox_backends"],
        json!(["off"])
    );
    assert_eq!(
        init["result"]["capabilities"]["credential_delivery"],
        json!([])
    );
}
