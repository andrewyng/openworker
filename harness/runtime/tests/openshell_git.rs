#![cfg(target_os = "linux")]
//! Real helper/process transport tests, without external service credentials.
//! These do not replace live OpenShell proxy/L7 qualification.
use serde_json::{json, Value};
use std::{path::Path, process::Stdio};
use tokio::{
    io::{AsyncBufReadExt, AsyncWriteExt, BufReader},
    process::Command,
};
const BIN: &str = env!("CARGO_BIN_EXE_openworker");
const REF: &str = "openshell:resolve:env:v1_fixture_GITHUB_TOKEN";
async fn helper(root: &Path, request: &str, token: &str) -> std::process::Output {
    let mut child = Command::new(BIN)
        .args(["git-credential-openshell", "get"])
        .env_clear()
        .env("GITHUB_TOKEN", token)
        .env(
            "OPENWORKER_GIT_SETTINGS",
            r#"{"host":"github.com","repositories":["org/project"]}"#,
        )
        .current_dir(root)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .kill_on_drop(true)
        .spawn()
        .unwrap();
    child
        .stdin
        .take()
        .unwrap()
        .write_all(request.as_bytes())
        .await
        .unwrap();
    child.wait_with_output().await.unwrap()
}
#[tokio::test]
async fn helper_returns_only_placeholder_for_exact_repo_and_never_persists_a_key() {
    let root = tempfile::tempdir().unwrap();
    let allowed = "protocol=https\nhost=github.com\npath=org/project.git\n\n";
    let out = helper(root.path(), allowed, REF).await;
    assert!(out.status.success());
    assert_eq!(
        String::from_utf8(out.stdout).unwrap(),
        format!("username=x-access-token\npassword={REF}\n\n")
    );
    for request in [
        "protocol=http\nhost=github.com\npath=org/project.git\n\n",
        "protocol=https\nhost=other.test\npath=org/project.git\n\n",
        "protocol=https\nhost=github.com\npath=org/other.git\n\n",
    ] {
        let out = helper(root.path(), request, REF).await;
        assert!(out.status.success());
        assert!(out.stdout.is_empty());
    }
    let raw = "synthetic-raw-key-must-not-leak";
    let out = helper(root.path(), allowed, raw).await;
    assert!(!out.status.success() && out.stdout.is_empty());
    assert!(!String::from_utf8_lossy(&out.stderr).contains(raw));
    assert_eq!(std::fs::read_dir(root.path()).unwrap().count(), 0);
}
async fn rpc(
    child: &mut tokio::process::Child,
    reader: &mut BufReader<tokio::process::ChildStdout>,
    id: u64,
    name: &str,
    args: Value,
) -> Value {
    let v = json!({"jsonrpc":"2.0","id":id,"method":name,"params":args});
    child
        .stdin
        .as_mut()
        .unwrap()
        .write_all(format!("{v}\n").as_bytes())
        .await
        .unwrap();
    let mut line = String::new();
    tokio::time::timeout(
        std::time::Duration::from_secs(20),
        reader.read_line(&mut line),
    )
    .await
    .unwrap()
    .unwrap();
    let reply: Value = serde_json::from_str(&line).unwrap();
    assert_eq!(reply["id"], id);
    reply
}
#[tokio::test]
#[ignore = "requires OW_TEST_DISPOSABLE_LINUX in a disposable Linux container"]
async fn default_shell_has_no_git_key_and_opt_in_manager_carries_only_current_reference() {
    assert_eq!(
        std::env::var("OW_TEST_DISPOSABLE_LINUX").as_deref(),
        Ok("true")
    );
    for managed in [false, true] {
        for enabled in [false, true] {
            let root = tempfile::tempdir().unwrap();
            // Reconnect to the SAME workspace/manager with a distinct current
            // reference. A manager that caches its launch environment must fail
            // this assertion, even if an upstream still accepts the old token.
            for reference in [REF, "openshell:resolve:env:v2_refreshed_GITHUB_TOKEN"] {
                let mut cmd = Command::new(BIN);
                cmd.args(["mcp-coding", "--workspace"]).arg(root.path());
                if managed {
                    cmd.arg("--environment-processes");
                }
                if enabled {
                    cmd.args([
                        "--openshell-git",
                        "--openshell-git-repository",
                        "org/project",
                    ]);
                }
                let mut child = cmd
                    .env("GITHUB_TOKEN", reference)
                    .env_remove("GH_TOKEN")
                    .stdin(Stdio::piped())
                    .stdout(Stdio::piped())
                    .stderr(Stdio::null())
                    .kill_on_drop(true)
                    .spawn()
                    .unwrap();
                let mut reader = BufReader::new(child.stdout.take().unwrap());
                let init=rpc(&mut child,&mut reader,1,"initialize",json!({"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"git-test","version":"1"}})).await;
                assert!(init["error"].is_null());
                child
                    .stdin
                    .as_mut()
                    .unwrap()
                    .write_all(b"{\"jsonrpc\":\"2.0\",\"method\":\"notifications/initialized\"}\n")
                    .await
                    .unwrap();
                let command = if enabled {
                    format!("test \"$GITHUB_TOKEN\" = '{reference}' && printf 'protocol=https\\nhost=github.com\\npath=org/project.git\\n\\n' | git credential fill | grep -Fxq 'password={reference}' && printf 'scoped helper works'")
                } else {
                    "test -z \"$GITHUB_TOKEN\" && test -z \"$GIT_CONFIG_COUNT\" && printf 'no credential inherited'".into()
                };
                let out = rpc(
                    &mut child,
                    &mut reader,
                    2,
                    "tools/call",
                    json!({"name":"run_shell","arguments":{"command":command,"yield_ms":10000}}),
                )
                .await;
                let data: Value =
                    serde_json::from_str(out["result"]["content"][0]["text"].as_str().unwrap())
                        .unwrap();
                assert_eq!(
                    data["exit_code"], 0,
                    "process failed; managed={managed}, enabled={enabled}"
                );
                assert_eq!(
                    data["stdout"],
                    if enabled {
                        "scoped helper works"
                    } else {
                        "no credential inherited"
                    }
                );
                assert!(!data.to_string().contains(REF));
                assert!(!data.to_string().contains(reference));
                child.stdin.take();
                child.wait().await.unwrap();
                // A manager is sandbox-owned, not tool-connection-owned. End only
                // this fixture's idle manager by dropping its process container in
                // the Linux Docker test; no process IDs outside the fixture are used.
            }
        }
    }
}

#[tokio::test]
#[ignore = "requires OW_TEST_DISPOSABLE_LINUX in a disposable Linux container"]
async fn long_workspace_process_manager_reuses_original_owner_across_tool_connections() {
    assert_eq!(
        std::env::var("OW_TEST_DISPOSABLE_LINUX").as_deref(),
        Ok("true")
    );
    let temp = tempfile::tempdir().unwrap();
    let root = temp.path().join("project".repeat(30));
    std::fs::create_dir(&root).unwrap();
    let mut handle = String::new();
    for connection in 0..2 {
        let mut child = Command::new(BIN)
            .args(["mcp-coding", "--environment-processes", "--workspace"])
            .arg(&root)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .kill_on_drop(true)
            .spawn()
            .unwrap();
        let mut reader = BufReader::new(child.stdout.take().unwrap());
        rpc(&mut child,&mut reader,1,"initialize",json!({"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"fixture","version":"1"}})).await;
        let args = if connection == 0 {
            json!({"name":"process_start","arguments":{"command":"sleep 30"}})
        } else {
            json!({"name":"process_status","arguments":{"process_id":handle}})
        };
        let reply = rpc(&mut child, &mut reader, 2, "tools/call", args).await;
        let data: Value =
            serde_json::from_str(reply["result"]["content"][0]["text"].as_str().unwrap()).unwrap();
        assert_ne!(reply["result"]["isError"], true, "{reply}");
        if connection == 0 {
            handle = data["process_id"].as_str().unwrap().into();
        } else {
            assert_eq!(data["process_id"], handle);
            assert_eq!(data["status"], "running");
            let stopped = rpc(
                &mut child,
                &mut reader,
                3,
                "tools/call",
                json!({"name":"process_stop","arguments":{"process_id":handle}}),
            )
            .await;
            assert_ne!(stopped["result"]["isError"], true, "{stopped}");
        }
        child.stdin.take();
        child.wait().await.unwrap();
    }
}
