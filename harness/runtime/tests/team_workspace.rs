#![cfg(unix)]
use openworker_runtime::team_workspace::*;
use std::path::Path;
fn request(selection: Selection) -> Request {
    Request {
        operation_id: "ws_original".into(),
        scope_digest: "a".repeat(64),
        selection,
    }
}
fn git(root: &Path, args: &[&str]) {
    assert!(std::process::Command::new("/usr/bin/git")
        .args(args)
        .current_dir(root)
        .status()
        .unwrap()
        .success());
}
#[tokio::test]
async fn directory_receipt_rejects_replacements_and_never_follows_symlinks() {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path().canonicalize().unwrap();
    std::fs::create_dir(root.join("ui")).unwrap();
    let r = prepare(
        &root,
        request(Selection::Directory { path: "ui".into() }),
        false,
    )
    .await
    .unwrap();
    r.verify().unwrap();
    std::fs::rename(root.join("ui"), root.join("old-ui")).unwrap();
    std::fs::create_dir(root.join("ui")).unwrap();
    assert!(r.verify().is_err());
    #[cfg(unix)]
    {
        std::os::unix::fs::symlink(root.join("old-ui"), root.join("alias")).unwrap();
        assert!(prepare(
            &root,
            request(Selection::Directory {
                path: "alias".into()
            }),
            false
        )
        .await
        .is_err());
    }
}
#[tokio::test]
async fn real_worktree_is_inspected_after_response_loss_and_never_recreated() {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path().canonicalize().unwrap();
    let repo = root.join("repo");
    std::fs::create_dir(&repo).unwrap();
    git(&repo, &["init", "--quiet"]);
    std::fs::write(repo.join("index.txt"), "base\n").unwrap();
    git(&repo, &["add", "index.txt"]);
    git(
        &repo,
        &[
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "-c",
            "core.hooksPath=/dev/null",
            "commit",
            "--quiet",
            "-m",
            "base",
        ],
    );
    let req = request(Selection::Worktree {
        path: "workers/ui".into(),
        repository: "repo".into(),
        branch: "builder-ui".into(),
        base: "HEAD".into(),
    });
    let receipt = prepare(&root, req.clone(), false).await.unwrap();
    assert!(receipt.path.join("index.txt").is_file());
    assert_eq!(prepare(&root, req.clone(), true).await.unwrap(), receipt);
    assert!(prepare(&root, req.clone(), false).await.is_err());
    std::fs::rename(root.join("workers/ui"), root.join("workers/moved")).unwrap();
    assert!(prepare(&root, req, true).await.is_err());
    assert!(!root.join("workers/ui").exists());
}
#[tokio::test]
async fn missing_original_evidence_and_repository_helpers_are_not_retry_permission() {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path().canonicalize().unwrap();
    git(&root, &["init", "--quiet"]);
    let req = request(Selection::Worktree {
        path: "workers/ui".into(),
        repository: ".".into(),
        branch: "builder-ui".into(),
        base: "HEAD".into(),
    });
    assert!(prepare(&root, req.clone(), true).await.is_err());
    assert!(!root.join("workers/ui").exists());
    git(&root, &["config", "filter.evil.process", "touch outside"]);
    assert!(prepare(&root, req, false).await.is_err());
    assert!(!root.join("outside").exists());
}
#[test]
fn workspace_selection_cannot_carry_host_paths_or_shell_fragments() {
    for value in [
        serde_json::json!({"mode":"directory","path":"../escape"}),
        serde_json::json!({"mode":"worktree","path":"ui","repository":".","branch":"--detach","base":"HEAD"}),
        serde_json::json!({"mode":"worktree","path":"ui","repository":".","branch":"x;curl","base":"HEAD"}),
    ] {
        let s: Selection = serde_json::from_value(value).unwrap();
        assert!(s.validate().is_err());
    }
    assert!(
        serde_json::from_value::<Selection>(serde_json::json!({"mode":"team","cwd":"/etc"}))
            .is_err()
    );
}
#[tokio::test]
async fn repository_pointer_outside_project_is_rejected_before_git_configuration() {
    let project = tempfile::tempdir().unwrap();
    let outside = tempfile::tempdir().unwrap();
    let root = project.path().canonicalize().unwrap();
    git(outside.path(), &["init", "--quiet"]);
    std::fs::write(
        root.join(".git"),
        format!(
            "gitdir: {}\n",
            outside
                .path()
                .canonicalize()
                .unwrap()
                .join(".git")
                .display()
        ),
    )
    .unwrap();
    let error = prepare(
        &root,
        request(Selection::Worktree {
            path: "child".into(),
            repository: ".".into(),
            branch: "child".into(),
            base: "HEAD".into(),
        }),
        false,
    )
    .await
    .unwrap_err();
    assert!(error.to_string().contains("metadata outside"), "{error}");
    assert!(!root.join(".openworker").exists());
}
#[test]
fn workspace_stdio_is_a_bounded_internal_request_channel() {
    use std::{
        io::Write,
        process::{Command, Stdio},
    };
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path().canonicalize().unwrap();
    std::fs::create_dir(root.join("child")).unwrap();
    let mut child = Command::new(env!("CARGO_BIN_EXE_openworker"))
        .args(["workspace-stdio", "--root"])
        .arg(&root)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    let req = request(Selection::Directory {
        path: "child".into(),
    });
    writeln!(
        child.stdin.take().unwrap(),
        "{}",
        serde_json::json!({"jsonrpc":"2.0","id":1,"method":"workspace/prepare","params":req})
    )
    .unwrap();
    let output = child.wait_with_output().unwrap();
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let response: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
    let receipt: Receipt = serde_json::from_value(response["result"].clone()).unwrap();
    receipt.verify().unwrap();
    assert_eq!(receipt.request, req);
    assert_eq!(receipt.path, root.join("child"));
    let mut verifier = Command::new(env!("CARGO_BIN_EXE_openworker"))
        .args(["workspace-stdio", "--root"])
        .arg(&root)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .spawn()
        .unwrap();
    {
        let mut input = verifier.stdin.take().unwrap();
        writeln!(
            input,
            "{}",
            serde_json::json!({"jsonrpc":"2.0","id":1,"method":"initialize","params":{}})
        )
        .unwrap();
        writeln!(
            input,
            "{}",
            serde_json::json!({"jsonrpc":"2.0","id":2,"method":"workspace/verify","params":receipt})
        )
        .unwrap();
    }
    let output = verifier.wait_with_output().unwrap();
    assert!(output.status.success());
    let responses = String::from_utf8(output.stdout)
        .unwrap()
        .lines()
        .map(|l| serde_json::from_str::<serde_json::Value>(l).unwrap())
        .collect::<Vec<_>>();
    assert_eq!(
        responses[0]["result"]["serverInfo"]["name"],
        "openworker-workspaces"
    );
    assert_eq!(
        responses[1]["result"],
        serde_json::to_value(receipt).unwrap()
    );
}

#[tokio::test]
async fn registered_worktrees_keep_file_scope_but_allow_only_original_git_metadata() {
    use openworker_runtime::coding::CodingTools;
    use serde_json::json;
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path().canonicalize().unwrap();
    let repo = root.join("source");
    std::fs::create_dir(&repo).unwrap();
    git(&repo, &["init", "--quiet"]);
    std::fs::write(repo.join("proof.txt"), "original").unwrap();
    git(&repo, &["add", "proof.txt"]);
    git(
        &repo,
        &[
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=f@example.invalid",
            "commit",
            "--quiet",
            "-m",
            "base",
        ],
    );
    let mut receipts = vec![];
    for id in ["ui", "api"] {
        let mut req = request(Selection::Worktree {
            path: format!("workers/{id}"),
            repository: "source".into(),
            branch: id.into(),
            base: "HEAD".into(),
        });
        req.operation_id = format!("ws_{id}");
        receipts.push(prepare(&root, req, false).await.unwrap());
    }
    let r = &receipts[0];
    let mut unregistered = CodingTools::default();
    assert!(unregistered
        .call(&r.path, "git_status", &json!({}))
        .await
        .is_err());
    let mut tools = CodingTools::with_workspace(r.clone()).unwrap();
    assert!(tools
        .call(&r.path, "git_log", &json!({}))
        .await
        .unwrap()
        .to_string()
        .contains("base"));
    tools
        .call(
            &r.path,
            "write_file",
            &json!({"path":"proof.txt","content":"ui only"}),
        )
        .await
        .unwrap();
    assert_eq!(
        std::fs::read_to_string(receipts[1].path.join("proof.txt")).unwrap(),
        "original"
    );
    for path in ["../api/proof.txt", "../../source/proof.txt"] {
        assert!(tools
            .call(
                &r.path,
                "write_file",
                &json!({"path":path,"content":"escape"})
            )
            .await
            .is_err());
    }
    std::os::unix::fs::symlink(&receipts[1].path, r.path.join("sibling")).unwrap();
    assert!(tools
        .call(&r.path, "read_file", &json!({"path":"sibling/proof.txt"}))
        .await
        .is_err());
    #[cfg(target_os = "linux")]
    {
        let cwd = tools
            .call(&r.path, "run_shell", &json!({"command":"pwd"}))
            .await
            .unwrap();
        assert!(cwd.to_string().contains(r.path.to_str().unwrap()), "{cwd}");
    }
    // Acknowledged worktrees may advance, but their branch and metadata cannot
    // be replaced with another child's pointer, even inside the shared project.
    git(&r.path, &["add", "proof.txt"]);
    git(
        &r.path,
        &[
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=f@example.invalid",
            "commit",
            "--quiet",
            "-m",
            "ui change",
        ],
    );
    r.verify().unwrap();
    assert!(tools.call(&r.path, "git_status", &json!({})).await.is_ok());
    std::fs::copy(receipts[1].path.join(".git"), r.path.join(".git")).unwrap();
    assert!(r.verify().is_err());
    assert!(tools
        .call(&r.path, "read_file", &json!({"path":"proof.txt"}))
        .await
        .is_err());
    tools.shutdown().await;
}

#[tokio::test]
async fn git_inspection_refuses_external_metadata_before_loading_config() {
    use openworker_runtime::coding::CodingTools;
    let tmp = tempfile::tempdir().unwrap();
    let outside = tempfile::tempdir().unwrap();
    git(outside.path(), &["init", "--quiet"]);
    std::fs::write(
        tmp.path().join(".git"),
        format!(
            "gitdir: {}\n",
            outside
                .path()
                .canonicalize()
                .unwrap()
                .join(".git")
                .display()
        ),
    )
    .unwrap();
    let error = CodingTools::default()
        .call(
            &tmp.path().canonicalize().unwrap(),
            "git_status",
            &serde_json::json!({}),
        )
        .await
        .unwrap_err();
    assert!(error.to_string().contains("metadata outside"), "{error}");
}
