//! Real processes and PostgreSQL; no live models or OS-sandbox claims.
use super::concurrency::{
    cancel, check_effects, effects, evidence, remove_owned_schema, sessions, submit, wait,
};
use super::*;

pub(super) fn pool(
    root: &Path,
    schema: &str,
    name: &str,
    sid: &str,
    slots: usize,
    seconds: u64,
) -> Child {
    pool_command(root, schema, name, sid, slots, seconds)
        .spawn()
        .unwrap()
}
pub(super) fn pool_command(
    root: &Path,
    schema: &str,
    name: &str,
    sid: &str,
    slots: usize,
    seconds: u64,
) -> Command {
    let mut command = Command::new(env!("CARGO_BIN_EXE_pg-worker-fixture"));
    command
        .arg(root)
        .args([
            schema,
            name,
            sid,
            "drain-service",
            &slots.to_string(),
            &seconds.to_string(),
        ])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::inherit())
        .kill_on_drop(true);
    command
}
async fn signal(child: &Child) {
    assert!(Command::new("kill")
        .args(["-TERM", &child.id().unwrap().to_string()])
        .status()
        .await
        .unwrap()
        .success());
}
async fn exit(child: &mut Child, success: bool) {
    let status = tokio::time::timeout(Duration::from_secs(18), child.wait())
        .await
        .unwrap()
        .unwrap();
    assert_eq!(status.success(), success);
}
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
#[ignore = "requires isolated OW_PG_TEST_URL and Linux"]
async fn drain_finishes_current_turn_renews_lease_and_leaves_followups_for_replacement() {
    let (root, c, api) = service_fixture();
    let mut seed = Worker::start(root.path(), &c.schema, "seed", None).await;
    let ids = sessions(root.path(), &mut seed, 2, true).await;
    seed.kill().await;
    for sid in &ids {
        submit(&api, sid, "first");
    }
    let mut a = pool(root.path(), &c.schema, "a", &ids[0], 1, 20);
    wait(|| (0..2).map(|i| effects(root.path(), i).len()).sum::<usize>() == 1).await;
    let n = usize::from(effects(root.path(), 0).is_empty());
    submit(&api, &ids[n], "follow");
    signal(&a).await;
    wait(|| root.path().join("a-draining").exists()).await;
    tokio::time::sleep(Duration::from_secs(7)).await;
    assert!(
        a.try_wait().unwrap().is_none(),
        "drain interrupted current tool"
    );
    assert_eq!(
        api.submission(&ids[n], "first").unwrap().status,
        SubmissionStatus::Started
    );
    assert_eq!(
        api.submission(&ids[n], "follow").unwrap().status,
        SubmissionStatus::Queued
    );
    std::fs::write(root.path().join(format!("work{n}/release")), "").unwrap();
    exit(&mut a, true).await;
    terminal_receipt(&api, &ids[n], "first", SubmissionStatus::Completed).await;
    assert_eq!(effects(root.path(), n).len(), 1);
    assert!(effects(root.path(), 1 - n).is_empty());
    assert_eq!(
        api.submission(&ids[n], "follow").unwrap().status,
        SubmissionStatus::Queued
    );
    std::fs::write(root.path().join(format!("work{}/release", 1 - n)), "").unwrap();
    let mut b = pool(root.path(), &c.schema, "b", &ids[0], 2, 20);
    for sid in &ids {
        terminal_receipt(&api, sid, "first", SubmissionStatus::Completed).await;
    }
    terminal_receipt(&api, &ids[n], "follow", SubmissionStatus::Completed).await;
    stop_service(&mut b).await;
    let mut expected = [1, 1];
    expected[n] = 2;
    check_effects(&api, root.path(), &ids, &expected);
    evidence(
        &c,
        "drain-finish",
        json!({"current_turn_completed":true,"lease_renewed_beyond_two_periods":true,"followup_left_queued":true,"replacement_effects":3,"duplicates":0}),
    );
    remove_owned_schema(&c);
}
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
#[ignore = "requires isolated OW_PG_TEST_URL and Linux"]
async fn draining_preserves_approval_and_defers_resume_but_honors_active_cancellation() {
    let (root, c, api) = service_fixture();
    let mut seed = Worker::start(root.path(), &c.schema, "seed", None).await;
    let ids = sessions(root.path(), &mut seed, 2, false).await;
    seed.kill().await;
    for sid in &ids {
        submit(&api, sid, "first");
    }
    let mut a = pool(root.path(), &c.schema, "a", &ids[0], 2, 20);
    wait(|| {
        ids.iter().all(|sid| {
            api.read_snapshot(sid).unwrap()["interactions"]
                .as_array()
                .is_some_and(|a| !a.is_empty())
        })
    })
    .await;
    let pending: Vec<_> = ids
        .iter()
        .map(|sid| api.read_snapshot(sid).unwrap())
        .collect();
    api.submit_control(decision(&ids[0], &pending[0], "allow-active", "once"))
        .unwrap();
    wait(|| effects(root.path(), 0).len() == 1).await;
    signal(&a).await;
    wait(|| root.path().join("a-draining").exists()).await;
    let answer = decision(&ids[1], &pending[1], "answer-while-draining", "session");
    api.submit_control(answer.clone()).unwrap();
    tokio::time::sleep(Duration::from_millis(500)).await;
    assert_eq!(
        api.read_snapshot(&ids[1]).unwrap()["interactions"],
        pending[1]["interactions"]
    );
    assert_eq!(
        api.submit_control(answer.clone()).unwrap().status,
        ControlStatus::Pending
    );
    let cancellation = cancel(&api, &ids[0], "cancel-during-drain");
    exit(&mut a, true).await;
    terminal_receipt(&api, &ids[0], "first", SubmissionStatus::Interrupted).await;
    assert_eq!(
        api.submit_control(cancellation).unwrap().status,
        ControlStatus::Applied
    );
    assert!(effects(root.path(), 1).is_empty());
    std::fs::write(root.path().join("work1/release"), "").unwrap();
    let mut b = pool(root.path(), &c.schema, "b", &ids[0], 2, 20);
    terminal_receipt(&api, &ids[1], "first", SubmissionStatus::Completed).await;
    assert_eq!(
        api.submit_control(answer.clone()).unwrap().status,
        ControlStatus::Applied
    );
    stop_service(&mut b).await;
    check_effects(&api, root.path(), &ids, &[1, 1]);
    evidence(
        &c,
        "drain-controls",
        json!({"approval_identity_preserved":true,"resume_deferred":true,"active_cancel_applied":true,"duplicates":0}),
    );
    remove_owned_schema(&c);
}
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
#[ignore = "requires isolated OW_PG_TEST_URL and Linux"]
async fn drain_deadline_interrupts_without_replaying_an_uncertain_effect() {
    let (root, c, api) = service_fixture();
    let mut seed = Worker::start(root.path(), &c.schema, "seed", None).await;
    let ids = sessions(root.path(), &mut seed, 1, true).await;
    seed.kill().await;
    submit(&api, &ids[0], "first");
    let mut a = pool(root.path(), &c.schema, "a", &ids[0], 1, 2);
    wait(|| effects(root.path(), 0).len() == 1).await;
    signal(&a).await;
    exit(&mut a, false).await;
    terminal_receipt(&api, &ids[0], "first", SubmissionStatus::Interrupted).await;
    assert!(api
        .read_events(&ids[0], 0)
        .unwrap()
        .iter()
        .any(|e| e.data["outcome_unknown_count"].as_u64().unwrap_or(0) > 0));
    std::fs::write(root.path().join("work0/release"), "").unwrap();
    submit(&api, &ids[0], "explicit-followup");
    let mut b = pool(root.path(), &c.schema, "b", &ids[0], 1, 20);
    terminal_receipt(
        &api,
        &ids[0],
        "explicit-followup",
        SubmissionStatus::Completed,
    )
    .await;
    stop_service(&mut b).await;
    check_effects(&api, root.path(), &ids, &[2]);
    evidence(
        &c,
        "drain-deadline",
        json!({"deadline_seconds":2,"interrupted":true,"unknown_recorded":true,"only_explicit_followup_executed":true,"duplicates":0}),
    );
    remove_owned_schema(&c);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
#[ignore = "requires isolated PG, Linux and OW_BASELINE_WORKER retained executable"]
async fn mixed_versions_keep_native_identity_pinned_and_old_pool_can_resume() {
    let Some(baseline) = std::env::var_os("OW_BASELINE_WORKER") else {
        return;
    };
    let (root, c, api) = service_fixture();
    let baseline = std::path::PathBuf::from(baseline);
    let mut seed =
        Worker::start_binary(&baseline, root.path(), &c.schema, "old-seed", None, false).await;
    let workspace = root.path().join("old-work");
    std::fs::create_dir(&workspace).unwrap();
    let old = seed
        .rpc(
            "session/create",
            json!({"name":"old","coworker":"pg-test","workspace":workspace}),
        )
        .await["session"]["id"]
        .as_str()
        .unwrap()
        .to_owned();
    seed.kill().await;
    submit(&api, &old, "first");
    let old_pool = |name: &str| {
        Command::new(&baseline)
            .arg(root.path())
            .args([&c.schema, name, &old, "service", "1"])
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::inherit())
            .kill_on_drop(true)
            .spawn()
            .unwrap()
    };
    let mut old_worker = old_pool("old-worker");
    wait(|| {
        !api.read_snapshot(&old).unwrap()["interactions"]
            .as_array()
            .unwrap()
            .is_empty()
    })
    .await;
    let pending = api.read_snapshot(&old).unwrap();
    stop_service(&mut old_worker).await;
    let mut seed = Worker::start(root.path(), &c.schema, "new-seed", None).await;
    let ids = sessions(root.path(), &mut seed, 1, true).await;
    seed.kill().await;
    assert_ne!(
        api.inspect(&old).unwrap().bundle_digest,
        api.inspect(&ids[0]).unwrap().bundle_digest,
        "different host versions must not silently share native authority"
    );
    std::fs::write(root.path().join("work0/release"), "").unwrap();
    submit(&api, &ids[0], "new-turn");
    let mut new_worker = pool(root.path(), &c.schema, "new-worker", &ids[0], 1, 20);
    terminal_receipt(&api, &ids[0], "new-turn", SubmissionStatus::Completed).await;
    let answer = decision(&old, &pending, "old-approval", "once");
    api.submit_control(answer.clone()).unwrap();
    tokio::time::sleep(Duration::from_millis(700)).await;
    assert_eq!(
        api.submit_control(answer.clone()).unwrap().status,
        ControlStatus::Pending
    );
    assert!(!workspace.join("effects.jsonl").exists());
    assert_eq!(
        api.read_snapshot(&old).unwrap()["interactions"],
        pending["interactions"]
    );
    stop_service(&mut new_worker).await;
    std::fs::write(workspace.join("release"), "").unwrap();
    let mut restored = old_pool("restored-old");
    terminal_receipt(&api, &old, "first", SubmissionStatus::Completed).await;
    stop_service(&mut restored).await;
    assert_eq!(
        std::fs::read_to_string(workspace.join("effects.jsonl"))
            .unwrap()
            .lines()
            .count(),
        1
    );
    check_effects(&api, root.path(), &ids, &[1]);
    evidence(
        &c,
        "mixed-version-pins",
        json!({"new_native_binary_cannot_consume_old_session":true,"approval_identity_preserved":true,"pinned_baseline_pool_completed_old_work":true,"automatic_cross_version_migration":false}),
    );
    remove_owned_schema(&c);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
#[ignore = "requires isolated OW_PG_TEST_URL and Linux"]
async fn database_loss_during_drain_interrupts_without_waiting_for_drain_deadline() {
    let (root, c, api) = service_fixture();
    let mut seed = Worker::start(root.path(), &c.schema, "seed", None).await;
    let ids = sessions(root.path(), &mut seed, 1, true).await;
    seed.kill().await;
    submit(&api, &ids[0], "first");
    let mut a = pool(root.path(), &c.schema, "a", &ids[0], 1, 30);
    wait(|| effects(root.path(), 0).len() == 1).await;
    signal(&a).await;
    wait(|| root.path().join("a-draining").exists()).await;
    tokio::task::block_in_place(|| {
        let mut db = postgres::Client::connect(&c.connection, postgres::NoTls).unwrap();
        let owner: String = db
            .query_one(
                &format!("SELECT owner FROM {}.sessions WHERE id=$1", c.schema),
                &[&ids[0]],
            )
            .unwrap()
            .get(0);
        let rows = db
            .query(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE application_name=$1",
                &[&format!("ow-store-{owner}")],
            )
            .unwrap();
        assert_eq!(rows.len(), 1);
    });
    let start = std::time::Instant::now();
    exit(&mut a, false).await;
    assert!(start.elapsed() < Duration::from_secs(15));
    let mut b = pool(root.path(), &c.schema, "b", &ids[0], 1, 20);
    terminal_receipt(&api, &ids[0], "first", SubmissionStatus::Interrupted).await;
    stop_service(&mut b).await;
    check_effects(&api, root.path(), &ids, &[1]);
    evidence(
        &c,
        "drain-database-loss",
        json!({"drain_seconds":30,"failure_exit_seconds":start.elapsed().as_secs_f64(),"effects":1,"replayed":false}),
    );
    remove_owned_schema(&c);
}
