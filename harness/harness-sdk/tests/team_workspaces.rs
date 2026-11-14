#![cfg(all(feature = "test-support", unix))]
use openworker_sdk::agents::*;
use serde_json::json;
use std::{path::Path, time::Duration};
fn git(root: &Path, args: &[&str]) {
    let output = std::process::Command::new("/usr/bin/git")
        .args(args)
        .current_dir(root)
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
}
async fn host(root: &Path, managed: bool, storage: Storage) -> Harness {
    let executable = std::path::PathBuf::from(env!("CARGO_BIN_EXE_sdk-test-host"));
    let builder = Harness::builder()
        .state_dir(root.join("state"))
        .executable(executable.clone())
        .sandbox(Sandbox::Off)
        .scripted(true)
        .storage(storage)
        .agent(
            Agent::new("lead")
                .instructions("Coordinate.")
                .planning(true)
                .approvals(Approvals::Never),
        )
        .agent(
            Agent::new("builder")
                .instructions("Read evidence.")
                .tool("coding.read_file")
                .approvals(Approvals::Never),
        );
    if managed {
        builder
            .managed(executable, Default::default())
            .await
            .unwrap()
    } else {
        builder.embedded().await.unwrap()
    }
}
async fn scenario(managed: bool, storage: Storage) {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path().canonicalize().unwrap();
    let work = root.join("work");
    std::fs::create_dir_all(work.join("source")).unwrap();
    std::fs::write(work.join("source/proof.txt"), "workspace-scoped proof").unwrap();
    git(&work.join("source"), &["init", "--quiet"]);
    git(&work.join("source"), &["add", "proof.txt"]);
    git(
        &work.join("source"),
        &[
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=f@example.invalid",
            "-c",
            "core.hooksPath=/dev/null",
            "commit",
            "--quiet",
            "-m",
            "base",
        ],
    );
    storage.migrate().unwrap();
    let h = host(&root, managed, storage.clone()).await;
    let team = h
        .new_team("lead", &work, TeamOptions::new(["builder"]))
        .await
        .unwrap();
    let task = format!(
        "TEST_RESOURCE_GRANTS {}",
        json!([{"name":"coding__read_file","arguments":{"path":"proof.txt"}}])
    );
    for (id, selection, cwd) in [
        (
            "reader-dir",
            json!({"mode":"directory","path":"source"}),
            "source",
        ),
        (
            "reader-tree",
            json!({"mode":"worktree","path":"workers/ui","repository":"source","branch":"ui"}),
            "workers/ui",
        ),
    ] {
        let options: ChildOptions = serde_json::from_value(json!({"workspace":selection})).unwrap();
        let receipt = team
            .spawn_with_options(id, "builder", id, &task, options.clone())
            .await
            .unwrap();
        assert_eq!(
            receipt,
            team.spawn_with_options(id, "builder", id, &task, options)
                .await
                .unwrap()
        );
        let sid = receipt["instance"]["session_id"].as_str().unwrap();
        let prompt = format!(
            "TEST_RESOURCE_GRANTS {}",
            json!([{"name":"team_wait_agent","arguments":{"agent_ids":[id],"timeout_ms":15000}}])
        );
        let run = tokio::time::timeout(Duration::from_secs(40), team.lead().run(&prompt))
            .await
            .unwrap()
            .unwrap();
        assert_eq!(run.status, RunStatus::Completed, "{run:?}");
        let child = h.client().snapshot(sid).await.unwrap();
        assert_eq!(
            child["session"]["workspace"],
            work.join(cwd).to_str().unwrap()
        );
        let events = h
            .client()
            .request("event/read", json!({"session_id":sid,"after":0}))
            .await
            .unwrap();
        assert!(
            events.to_string().contains("workspace-scoped proof"),
            "{events}"
        );
        assert!(team
            .spawn_with_options("changed", "builder", id, &task, ChildOptions::default())
            .await
            .is_err());
    }
    let failed = team
        .spawn_with_options(
            "missing",
            "builder",
            "missing",
            &task,
            serde_json::from_value(json!({"workspace":{"mode":"directory","path":"absent"}}))
                .unwrap(),
        )
        .await
        .unwrap();
    let wait = format!(
        "TEST_RESOURCE_GRANTS {}",
        json!([{"name":"team_wait_agent","arguments":{"agent_ids":["missing"],"timeout_ms":15000}}])
    );
    team.lead().run(&wait).await.unwrap();
    let snapshot = team.snapshot().await.unwrap();
    assert_eq!(
        snapshot["roster"]["instances"]["missing"]["phase"],
        "closed"
    );
    assert!(snapshot["failures"]["missing"]
        .as_str()
        .unwrap()
        .contains("Workspace preparation stopped"));
    assert!(h
        .client()
        .snapshot(failed["instance"]["session_id"].as_str().unwrap())
        .await
        .is_err());
    assert!(snapshot.get("workspaces").is_none());
    let reference = team.reference();
    h.shutdown().await.unwrap();
    let h = host(&root, managed, storage).await;
    let resumed = h.resume_team(reference).await.unwrap();
    assert_eq!(
        resumed.snapshot().await.unwrap()["roster"],
        snapshot["roster"]
    );
    assert!(work.join("workers/ui/proof.txt").is_file());
    h.shutdown().await.unwrap();
}
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn sqlite_embedded_workspace_children() {
    scenario(false, Storage::Sqlite).await;
}
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn sqlite_managed_workspace_children() {
    scenario(true, Storage::Sqlite).await;
}
fn postgres() -> Storage {
    Storage::Postgres {
        connection_env: "OW_PG_TEST_URL".into(),
        schema: format!("ow_ws_{}", openworker_runtime::id("x")),
        ca_file: None,
        loopback_plaintext: true,
    }
}
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
#[ignore = "requires isolated OW_PG_TEST_URL"]
async fn postgres_embedded_workspace_children() {
    scenario(false, postgres()).await;
}
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
#[ignore = "requires isolated OW_PG_TEST_URL"]
async fn postgres_managed_workspace_children() {
    scenario(true, postgres()).await;
}
