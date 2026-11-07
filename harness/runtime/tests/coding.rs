use openworker_runtime::coding::CodingTools;
use serde_json::json;

#[test]
fn coding_classification_keeps_file_guards_independent() {
    use openworker_runtime::{
        bundle::Bundle,
        permissions::{self, Category},
    };
    let tmp = tempfile::tempdir().unwrap();
    std::fs::create_dir(tmp.path().join("coworker")).unwrap();
    std::fs::write(
        tmp.path().join("coworker/manifest.md"),
        "---\nid: coding-test\nname: Coding test\n---\nCode.\n",
    )
    .unwrap();
    std::fs::write(tmp.path().join("tools.json"),json!({"version":3,"tools":["coding.edit_file","coding.list_files","coding.process_start"]}).to_string()).unwrap();
    let mut registry = openworker_tools::ToolRegistry::default();
    openworker_runtime::coding::register(&mut registry).unwrap();
    let bundle = Bundle::load_with_registry(tmp.path(), &registry).unwrap();
    assert_eq!(
        permissions::classify(&bundle, "coding__edit_file"),
        Category::WriteLocal
    );
    assert_eq!(
        permissions::classify(&bundle, "coding__list_files"),
        Category::Read
    );
    assert_eq!(
        permissions::classify(&bundle, "coding__process_start"),
        Category::Execute
    );
    assert!(permissions::hard_check(
        &bundle,
        tmp.path().to_str().unwrap(),
        "coding__edit_file",
        &json!({"path":"../outside"})
    )
    .is_err());
}

#[cfg(not(target_os = "linux"))]
#[tokio::test]
async fn unsupported_process_platform_does_not_fall_back() {
    let dir = tempfile::tempdir().unwrap();
    let mut t = CodingTools::default();
    let e = t
        .call(
            dir.path(),
            "process_start",
            &json!({"command":"echo must-not-run"}),
        )
        .await
        .unwrap_err();
    assert!(e.to_string().contains("currently require Linux"));
}

#[cfg(target_os = "linux")]
#[test]
#[ignore = "HTTP fixture child invoked only by managed_preview_works"]
fn preview_fixture() {
    use std::io::{Read, Write};
    let Ok(port_file) = std::env::var("OW_CODING_PREVIEW_PORT") else {
        return;
    };
    let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
    std::fs::write(port_file, listener.local_addr().unwrap().port().to_string()).unwrap();
    for stream in listener.incoming() {
        let mut stream = stream.unwrap();
        let mut input = [0; 1024];
        let _ = stream.read(&mut input);
        stream
            .write_all(b"HTTP/1.1 200 OK\r\nContent-Length: 8\r\nConnection: close\r\n\r\nplayable")
            .unwrap();
    }
}

#[cfg(target_os = "linux")]
#[tokio::test]
async fn managed_preview_works_until_explicit_stop() {
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    let dir = tempfile::tempdir().unwrap();
    let mut t = CodingTools::default();
    let quote = |s: &str| format!("'{}'", s.replace('\'', "'\\''"));
    let command = format!(
        "OW_CODING_PREVIEW_PORT=preview.port {} --exact preview_fixture --ignored",
        quote(std::env::current_exe().unwrap().to_str().unwrap())
    );
    let r = t
        .call(
            dir.path(),
            "process_start",
            &json!({"command":command,"timeout_seconds":10}),
        )
        .await
        .unwrap();
    let port = tokio::time::timeout(std::time::Duration::from_secs(5), async {
        loop {
            if let Ok(p) = std::fs::read_to_string(dir.path().join("preview.port")) {
                break p;
            }
            tokio::time::sleep(std::time::Duration::from_millis(20)).await;
        }
    })
    .await
    .unwrap();
    let mut socket = tokio::net::TcpStream::connect(format!("127.0.0.1:{port}"))
        .await
        .unwrap();
    socket
        .write_all(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")
        .await
        .unwrap();
    let mut response = String::new();
    socket.read_to_string(&mut response).await.unwrap();
    assert!(response.ends_with("playable"));
    t.call(
        dir.path(),
        "process_stop",
        &json!({"process_id":r["process_id"]}),
    )
    .await
    .unwrap();
    assert!(tokio::net::TcpStream::connect(format!("127.0.0.1:{port}"))
        .await
        .is_err());
    t.shutdown().await;
}

#[tokio::test]
async fn atomic_edits_reject_stale_ambiguous_and_escaping_paths() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path();
    let mut t = CodingTools::default();
    let v = t
        .call(
            root,
            "write_file",
            &json!({"path":"src/a.txt","content":"one two one","create_only":true}),
        )
        .await
        .unwrap();
    assert!(t
        .call(
            root,
            "write_file",
            &json!({"path":"src/a.txt","content":"lost","create_only":true})
        )
        .await
        .is_err());
    for (old, hash) in [("one", v["sha256"].clone()), ("two", json!("stale"))] {
        assert!(t.call(root,"edit_file",&json!({"path":"src/a.txt","old_text":old,"new_text":"three","expected_sha256":hash})).await.is_err());
    }
    t.call(root,"edit_file",&json!({"path":"src/a.txt","old_text":"two","new_text":"three","expected_sha256":v["sha256"]})).await.unwrap();
    assert_eq!(
        std::fs::read_to_string(root.join("src/a.txt")).unwrap(),
        "one three one"
    );
    assert!(t
        .call(
            root,
            "write_file",
            &json!({"path":"../outside","content":"no"})
        )
        .await
        .is_err());
    assert!(t
        .call(
            root,
            "write_file",
            &json!({"path":".openworker-coding.lock","content":"no"})
        )
        .await
        .is_err());
    assert!(t
        .call(root, "read_file", &json!({"path":"src/a.txt","limit":0}))
        .await
        .is_err());
}

#[tokio::test]
async fn bounded_search_and_read_report_truncation() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path();
    let mut t = CodingTools::default();
    std::fs::write(root.join("a.txt"), "needle\n".repeat(600)).unwrap();
    std::fs::write(root.join("b.txt"), "x".repeat(50000)).unwrap();
    std::fs::write(root.join("binary"), [0, 255]).unwrap();
    let r = t
        .call(root, "search_files", &json!({"query":"needle","limit":2}))
        .await
        .unwrap();
    assert_eq!(r["matches"].as_array().unwrap().len(), 2);
    assert_eq!(r["truncated"], true);
    let r = t
        .call(root, "read_file", &json!({"path":"b.txt"}))
        .await
        .unwrap();
    assert_eq!(r["long_lines_truncated"], true);
    assert_eq!(r["lines"][0].as_str().unwrap().len(), 4000);
    let r = t
        .call(root, "list_files", &json!({"limit":1}))
        .await
        .unwrap();
    assert_eq!(r["files"][0], "a.txt");
    assert_eq!(r["truncated"], true);
}

#[cfg(unix)]
#[tokio::test]
async fn symlinks_cannot_read_or_edit_outside_workspace() {
    let dir = tempfile::tempdir().unwrap();
    let outside = tempfile::tempdir().unwrap();
    std::fs::write(outside.path().join("secret"), "untouched").unwrap();
    std::os::unix::fs::symlink(outside.path(), dir.path().join("link")).unwrap();
    let mut t = CodingTools::default();
    for name in ["read_file", "write_file"] {
        let args = if name == "read_file" {
            json!({"path":"link/secret"})
        } else {
            json!({"path":"link/secret","content":"changed"})
        };
        assert!(t.call(dir.path(), name, &args).await.is_err());
    }
    assert_eq!(
        t.call(dir.path(), "list_files", &json!({})).await.unwrap()["files"],
        json!([])
    );
    assert_eq!(
        std::fs::read_to_string(outside.path().join("secret")).unwrap(),
        "untouched"
    );
}

#[cfg(target_os = "linux")]
#[tokio::test]
async fn process_handles_are_scoped_bounded_and_stoppable() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path();
    let mut t = CodingTools::default();
    let mut other = CodingTools::default();
    let r = t
        .call(
            root,
            "process_start",
            &json!({"command":"printf ready; sleep 30","timeout_seconds":45}),
        )
        .await
        .unwrap();
    let args = json!({"process_id":r["process_id"]});
    assert!(other.call(root, "process_stop", &args).await.is_err());
    tokio::time::sleep(std::time::Duration::from_millis(100)).await;
    assert_eq!(
        t.call(root, "process_logs", &args).await.unwrap()["stdout"],
        "ready"
    );
    assert_eq!(
        t.call(root, "process_status", &args).await.unwrap()["status"],
        "running"
    );
    assert_eq!(
        t.call(root, "process_stop", &args).await.unwrap()["status"],
        "stopped"
    );
    assert_eq!(
        t.call(root, "process_stop", &args).await.unwrap()["status"],
        "stopped"
    );
    t.shutdown().await;
}

#[cfg(target_os = "linux")]
#[tokio::test]
async fn command_timeout_and_output_limits_are_enforced() {
    let dir = tempfile::tempdir().unwrap();
    let mut t = CodingTools::default();
    let r = t
        .call(
            dir.path(),
            "run_shell",
            &json!({"command":"sleep 10","timeout_seconds":1,"yield_ms":3000}),
        )
        .await
        .unwrap();
    assert_eq!(r["status"], "timed_out");
    let r=t.call(dir.path(),"run_shell",&json!({"command":"i=0; while [ $i -lt 9000 ]; do printf abcdefgh; i=$((i+1)); done","yield_ms":10000})).await.unwrap();
    assert_eq!(r["status"], "exited");
    assert_eq!(r["exit_code"], 0);
    assert_eq!(r["stdout_truncated"], true);
    assert!(r.to_string().len() < 50000);
    assert!(t
        .call(
            dir.path(),
            "run_shell",
            &json!({"command":"true","timeout_seconds":999999})
        )
        .await
        .is_err());
    t.shutdown().await;
}

#[cfg(target_os = "linux")]
#[tokio::test]
async fn shutdown_kills_background_descendants() {
    let dir = tempfile::tempdir().unwrap();
    let mut t = CodingTools::default();
    let r = t
        .call(
            dir.path(),
            "process_start",
            &json!({"command":"(sleep 2; echo leaked > escaped) & wait"}),
        )
        .await
        .unwrap();
    t.shutdown().await;
    assert_eq!(
        t.call(
            dir.path(),
            "process_status",
            &json!({"process_id":r["process_id"]})
        )
        .await
        .unwrap()["status"],
        "stopped"
    );
    tokio::time::sleep(std::time::Duration::from_millis(2200)).await;
    assert!(!dir.path().join("escaped").exists());
}
