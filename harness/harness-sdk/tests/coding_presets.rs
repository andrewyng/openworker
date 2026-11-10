#![cfg(all(feature = "test-support", unix))]
use futures_util::FutureExt;
use openworker_sdk::agents::*;
use serde_json::{json, Value};
use std::{path::Path, time::Duration};
fn git(root: &Path, args: &[&str]) -> String {
    let o = std::process::Command::new("/usr/bin/git")
        .args(args)
        .current_dir(root)
        .output()
        .unwrap();
    assert!(o.status.success(), "{}", String::from_utf8_lossy(&o.stderr));
    String::from_utf8(o.stdout).unwrap().trim().into()
}
fn plan(name: &str, args: Value) -> String {
    format!(
        "TEST_RESOURCE_GRANTS {}",
        json!([{"name":name,"arguments":args}])
    )
}
async fn host(
    root: &Path,
    managed: bool,
    storage: Storage,
    preset: &coding::SharedPreset,
) -> Harness {
    let executable = root.join("test-host");
    // Executable is immutable while subprocesses use it, including if another
    // test command rebuilds sdk-test-host in the shared Cargo target.
    if !executable.exists() {
        std::fs::copy(env!("CARGO_BIN_EXE_sdk-test-host"), &executable).unwrap();
    }
    let mut b = Harness::builder()
        .state_dir(root.join("state"))
        .executable(executable.clone())
        .sandbox(Sandbox::Off)
        .storage(storage)
        .scripted(true)
        .agent(coding::single("solo").approvals(Approvals::Never));
    for a in preset.agents() {
        b = b.agent(a.approvals(Approvals::Never));
    }
    if managed {
        b.managed(executable, Default::default()).await.unwrap()
    } else {
        b.embedded().await.unwrap()
    }
}
async fn scenario(managed: bool, storage: Storage) {
    let temp = tempfile::tempdir().unwrap();
    let root = temp.path().canonicalize().unwrap();
    let work = root.join("work");
    let source = work.join("source");
    std::fs::create_dir_all(&source).unwrap();
    git(&source, &["init", "--quiet"]);
    std::fs::write(source.join("proof.txt"), "base").unwrap();
    git(&source, &["add", "proof.txt"]);
    git(
        &source,
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
    let sha = git(&source, &["rev-parse", "HEAD"]);
    let preset = coding::shared("game", "source", &sha).unwrap();
    storage.migrate().unwrap();
    let h = host(&root, managed, storage.clone(), &preset).await;
    let solo = h.session("solo", &work).await.unwrap();
    assert_eq!(solo.run(&plan("coding__write_file",json!({"path":"index.html","content":"<!doctype html><title>Small game</title><button>Play</button>"}))).await.unwrap().status,RunStatus::Completed);
    assert!(std::fs::read_to_string(work.join("index.html"))
        .unwrap()
        .contains("<button>Play</button>"));
    let team = h
        .new_team(preset.lead.id(), &work, preset.team.clone())
        .await
        .unwrap();
    assert_eq!(
        team.snapshot().await.unwrap()["roster"]["instances"]
            .as_object()
            .unwrap()
            .len(),
        1
    );
    let mut children = vec![];
    for id in ["ui", "api"] {
        let task = plan(
            "coding__write_file",
            json!({"path":"proof.txt","content":id}),
        );
        let r = team
            .spawn(id, preset.builder.id(), id, &task)
            .await
            .unwrap();
        assert_eq!(
            team.spawn(id, preset.builder.id(), id, &task)
                .await
                .unwrap(),
            r
        );
        assert_eq!(r["workspace"]["base"], sha);
        assert_eq!(r["workspace"]["mode"], "worktree");
        children.push((id, r));
    }
    let wait = format!(
        "TEST_RESOURCE_GRANTS {}",
        json!([
            {"name":"team_wait_agent","arguments":{"agent_ids":["ui"],"timeout_ms":20000}},
            {"name":"team_wait_agent","arguments":{"agent_ids":["api"],"timeout_ms":20000}}
        ])
    );
    let result = tokio::time::timeout(Duration::from_secs(60), team.lead().run(&wait))
        .await
        .unwrap()
        .unwrap();
    assert_eq!(result.status, RunStatus::Completed);
    for (id, r) in &children {
        let path = r["workspace"]["path"].as_str().unwrap();
        assert_eq!(
            std::fs::read_to_string(work.join(path).join("proof.txt")).unwrap(),
            *id
        );
        let child = h
            .client()
            .snapshot(r["instance"]["session_id"].as_str().unwrap())
            .await
            .unwrap();
        assert_eq!(
            child["session"]["workspace"],
            work.join(path).to_str().unwrap()
        );
        assert_eq!(git(&work.join(path), &["rev-parse", "HEAD"]), sha);
    }
    assert_ne!(
        children[0].1["workspace"]["path"],
        children[1].1["workspace"]["path"]
    );
    assert_eq!(
        std::fs::read_to_string(source.join("proof.txt")).unwrap(),
        "base"
    );
    let mut changed = preset.team.clone();
    changed.workspace_defaults.as_mut().unwrap().base = "f".repeat(40);
    assert!(h.attach_team(team.lead().clone(), changed).await.is_err());
    let reference = team.reference();
    h.shutdown().await.unwrap();
    let h = host(&root, managed, storage, &preset).await;
    let resumed = h.resume_team(reference).await.unwrap();
    assert_eq!(
        resumed.snapshot().await.unwrap()["roster"]["instances"]
            .as_object()
            .unwrap()
            .len(),
        3
    );
    h.shutdown().await.unwrap();
}
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn sqlite_embedded() {
    scenario(false, Storage::Sqlite).await
}
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn sqlite_managed() {
    scenario(true, Storage::Sqlite).await
}
fn pg() -> Storage {
    Storage::Postgres {
        connection_env: "OW_PG_TEST_URL".into(),
        schema: format!("ow_presets_{}", openworker_runtime::id("x")),
        ca_file: None,
        loopback_plaintext: true,
    }
}
async fn postgres_scenario(managed: bool) {
    let storage = pg();
    let Storage::Postgres { schema, .. } = &storage else {
        unreachable!()
    };
    let schema = schema.clone();
    let outcome = std::panic::AssertUnwindSafe(scenario(managed, storage))
        .catch_unwind()
        .await;
    tokio::task::spawn_blocking(move || {
        // This helper generates the name; never interpolate application input.
        assert!(
            schema.starts_with("ow_presets_x_")
                && schema
                    .bytes()
                    .all(|b| b.is_ascii_alphanumeric() || b == b'_')
        );
        let mut pg =
            postgres::Client::connect(&std::env::var("OW_PG_TEST_URL").unwrap(), postgres::NoTls)
                .unwrap();
        pg.batch_execute(&format!("DROP SCHEMA IF EXISTS {schema} CASCADE"))
            .unwrap();
    })
    .await
    .unwrap();
    if let Err(panic) = outcome {
        std::panic::resume_unwind(panic);
    }
}
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
#[ignore = "requires disposable OW_PG_TEST_URL"]
async fn postgres_embedded() {
    postgres_scenario(false).await
}
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
#[ignore = "requires disposable OW_PG_TEST_URL"]
async fn postgres_managed() {
    postgres_scenario(true).await
}
#[test]
fn preset_configuration_is_safe_customizable_and_service_conversion_is_explicit() {
    let agent = coding::single("solo");
    let data = serde_json::to_value(&agent).unwrap();
    assert_eq!(data["approvals"]["mode"], "always");
    assert!(data.get("subagents").is_none());
    assert!(data.get("model").is_none());
    let profile: openworker_sdk::service::RuntimeProfile = serde_json::from_value(
        json!({"id":"shared@1","provider":"openshell","cpu":1,"memoryMB":512,"sharing":"shared"}),
    )
    .unwrap();
    let agent = agent.default_model(ModelChoice::new("Local", "chosen"));
    let r = coding::service_release(&agent, "1", &profile).unwrap();
    assert_eq!(r.default_model.unwrap().model, "chosen");
    assert!(
        coding::service_release(&agent.clone().approvals(Approvals::Never), "1", &profile).is_err()
    );
    assert!(coding::service_release(&agent.memory(true), "1", &profile).is_err());
    assert!(coding::shared("game", "source", "HEAD").is_err());
    assert!(coding::shared("../game", "source", &"a".repeat(40)).is_err());
    assert!(coding::shared("1game", "source", &"a".repeat(40)).is_err());
    let mut auto = profile;
    auto.approval_mode = openworker_runtime::permissions::Mode::AutoApprove;
    let separate = coding::single("reviewed")
        .default_model(ModelChoice::new("Work", "coding-model"))
        .approvals(Approvals::Auto(ModelChoice::new("Review", "review-model")));
    let release = coding::service_release(&separate, "1", &auto).unwrap();
    assert_eq!(release.default_model.unwrap().connection, "Work");
    assert_eq!(release.reviewer_model.unwrap().connection, "Review");
}

#[test]
fn isolated_service_preset_uses_artifact_defaults_and_no_linked_worktree() {
    let source:openworker_sdk::service::CodingSource=serde_json::from_value(json!({"repository":"org/game","directory":"source","object_format":"sha1","commit":"a".repeat(40)})).unwrap();
    let sandbox =
        serde_json::from_value(json!({"mode":"new","profile":"small@1","cpu":1,"memory_mb":512}))
            .unwrap();
    let delegation=serde_json::from_value(json!({"tools":["coding.read_file"],"placements":["team","new"],"profiles":{"small@1":{"files":[],"network":[],"credentials":[],"cpu":1,"memory_mb":512}}})).unwrap();
    let p = coding::isolated_service("game", source, sandbox, delegation, "1").unwrap();
    assert!(p.team.workspace_defaults.is_none());
    let defaults = p.team.coding.as_ref().unwrap();
    assert_eq!(
        defaults.agent_types["game-verifier"].source,
        openworker_runtime::coding_handoff::config::DefaultSource::RequireSubmission
    );
    assert_eq!(
        defaults.agent_types["game-builder"].source,
        openworker_runtime::coding_handoff::config::DefaultSource::Initial
    );
    assert!(serde_json::to_value(p.lead).unwrap()["instructions"]
        .as_str()
        .unwrap()
        .contains("team_receive_submission"));
    let options: ChildOptions =
        serde_json::from_value(json!({"source":{"kind":"submission","id":"hs_original"}})).unwrap();
    assert_eq!(
        serde_json::to_value(options).unwrap()["source"]["id"],
        "hs_original"
    );
}
