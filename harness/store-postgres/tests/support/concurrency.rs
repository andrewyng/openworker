//! Bounded logical isolation tests, not an OS sandbox or throughput benchmark.
use super::*;
use std::collections::BTreeSet;
use std::sync::{Arc, Barrier};
fn pool(root: &Path, schema: &str, name: &str, sid: &str) -> Child {
    Command::new(env!("CARGO_BIN_EXE_pg-worker-fixture"))
        .arg(root)
        .args([schema, name, sid, "service", "2"])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::inherit())
        .kill_on_drop(true)
        .spawn()
        .unwrap()
}
pub(super) async fn sessions(
    root: &Path,
    seeder: &mut Worker,
    count: usize,
    bypass: bool,
) -> Vec<String> {
    let mut ids = vec![];
    for n in 0..count {
        let workspace = root.join(format!("work{n}"));
        std::fs::create_dir(&workspace).unwrap();
        let mut params =
            json!({"name":format!("concurrent-{n}"),"coworker":"pg-test","workspace":workspace});
        if bypass {
            params["permissions"] = json!({"mode":"bypass"});
        }
        let value = seeder.rpc("session/create", params).await;
        ids.push(value["session"]["id"].as_str().unwrap().to_owned());
    }
    ids
}
pub(super) fn effects(root: &Path, n: usize) -> Vec<Value> {
    match std::fs::read_to_string(root.join(format!("work{n}/effects.jsonl"))) {
        Ok(s) => s
            .split_inclusive('\n')
            .filter(|l| l.ends_with('\n'))
            .map(|l| serde_json::from_str(l).unwrap())
            .collect(),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => vec![],
        Err(e) => panic!("effect read failed: {e}"),
    }
}
pub(super) async fn wait(mut condition: impl FnMut() -> bool) {
    tokio::time::timeout(Duration::from_secs(35), async {
        while !condition() {
            tokio::time::sleep(Duration::from_millis(50)).await;
        }
    })
    .await
    .unwrap();
}
pub(super) fn submit(
    api: &PostgresStore,
    sid: &str,
    id: &str,
) -> openworker_store_postgres::SubmissionReceipt {
    api.submit_turn(TurnSubmission {
        limits: None,
        session_id: sid.into(),
        submission_id: id.into(),
        text: plan("write_gate"),
    })
    .unwrap()
}
pub(super) fn cancel(api: &PostgresStore, sid: &str, id: &str) -> Control {
    let command = Control {
        session_id: sid.into(),
        request_id: id.into(),
        turn_id: api.inspect(sid).unwrap().turn.unwrap().id,
        actor: "concurrency-test".into(),
        action: Action::Cancel,
    };
    api.submit_control(command.clone()).unwrap();
    command
}
pub(super) fn check_effects(api: &PostgresStore, root: &Path, ids: &[String], expected: &[usize]) {
    for (n, sid) in ids.iter().enumerate() {
        let records = effects(root, n);
        assert_eq!(
            records.len(),
            expected[n],
            "unexpected effect count for {sid}"
        );
        let mut identities = BTreeSet::new();
        for effect in records {
            assert_eq!(effect["session_id"], *sid, "cross-session tool context");
            assert!(
                identities.insert((effect["turn_id"].to_string(), effect["call_id"].to_string())),
                "tool effect replayed"
            );
            assert!(api
                .read_events(sid, 0)
                .unwrap()
                .iter()
                .any(|e| e.kind == "tool_started"
                    && e.turn_id.as_deref() == effect["turn_id"].as_str()
                    && e.data["call_id"] == effect["call_id"]));
        }
        let events = api.read_events(sid, 0).unwrap();
        assert!(
            events.len() < 500,
            "test must page before exceeding one event page"
        );
        for (i, e) in events.iter().enumerate() {
            assert_eq!(e.session_id, *sid);
            assert_eq!(e.event_id, i as i64 + 1);
        }
        assert_eq!(
            events.len() as i64,
            api.read_snapshot(sid).unwrap()["cursor"].as_i64().unwrap()
        );
    }
}
pub(super) fn evidence(config: &Config, name: &str, value: Value) {
    if let Some(root) = std::env::var_os("OW_CONCURRENCY_REPORT_DIR") {
        let root = std::path::PathBuf::from(root);
        assert!(root.is_absolute());
        use std::os::unix::fs::DirBuilderExt;
        std::fs::DirBuilder::new()
            .recursive(true)
            .mode(0o700)
            .create(&root)
            .unwrap();
        let path = root.join(format!("{name}-{}.json", config.schema));
        use std::io::Write;
        use std::os::unix::fs::OpenOptionsExt;
        let mut f = std::fs::OpenOptions::new()
            .create_new(true)
            .write(true)
            .mode(0o600)
            .open(path)
            .unwrap();
        writeln!(f, "{value}").unwrap();
    }
}
fn effect_evidence(api: &PostgresStore, root: &Path, ids: &[String]) -> Value {
    json!(ids.iter().enumerate().map(|(n,sid)|json!({"session_id":sid,"cursor":api.read_snapshot(sid).unwrap()["cursor"],"effects":effects(root,n)})).collect::<Vec<_>>())
}
pub(super) fn remove_owned_schema(config: &Config) {
    assert!(
        config.schema.starts_with("ow_service_")
            && config.schema[11..].len() == 32
            && config.schema[11..].bytes().all(|c| c.is_ascii_hexdigit())
    );
    tokio::task::block_in_place(|| {
        let mut db = postgres::Client::connect(&config.connection, postgres::NoTls).unwrap();
        db.batch_execute(&format!("DROP SCHEMA {} CASCADE", config.schema))
            .unwrap();
    });
}
async fn multi_slot_fault(database_loss: bool) {
    let (root, config, api) = service_fixture();
    let mut seeder = Worker::start(root.path(), &config.schema, "seed", None).await;
    let ids = sessions(root.path(), &mut seeder, 8, true).await;
    // Two independent API-store incarnations submit the same id/text in parallel.
    let barrier = Arc::new(Barrier::new(3));
    let mut producers = vec![];
    for _ in 0..2 {
        let c = config.clone();
        let ids = ids.clone();
        let b = barrier.clone();
        producers.push(std::thread::spawn(move || {
            let a = PostgresStore::open(c).unwrap();
            b.wait();
            ids.iter()
                .map(|sid| submit(&a, sid, "first").turn_id)
                .collect::<Vec<_>>()
        }));
    }
    barrier.wait();
    let first = producers.remove(0).join().unwrap();
    assert_eq!(first, producers.remove(0).join().unwrap());
    seeder.kill().await;
    let mut a = pool(root.path(), &config.schema, "a", &ids[0]);
    let apid = a.id().unwrap();
    wait(|| {
        (0..8)
            .filter(|&n| !effects(root.path(), n).is_empty())
            .count()
            == 2
    })
    .await;
    let mut b = pool(root.path(), &config.schema, "b", &ids[0]);
    let bpid = b.id().unwrap();
    wait(|| {
        (0..8)
            .filter(|&n| !effects(root.path(), n).is_empty())
            .count()
            == 4
    })
    .await;
    let dead: Vec<_> = (0..8)
        .filter(|&n| {
            effects(root.path(), n)
                .first()
                .is_some_and(|v| v["pid"] == apid)
        })
        .collect();
    let alive: Vec<_> = (0..8)
        .filter(|&n| {
            effects(root.path(), n)
                .first()
                .is_some_and(|v| v["pid"] == bpid)
        })
        .collect();
    assert_eq!(dead.len(), 2);
    assert_eq!(alive.len(), 2);
    // Saturation holds across >2 lease periods. No fifth effect starts and no
    // duplicate effects are hidden by overwriting a file.
    tokio::time::sleep(Duration::from_secs(13)).await;
    assert!(a.try_wait().unwrap().is_none() && b.try_wait().unwrap().is_none());
    assert_eq!(
        (0..8).map(|n| effects(root.path(), n).len()).sum::<usize>(),
        4
    );
    let follow: Vec<_> = ids
        .iter()
        .map(|sid| submit(&api, sid, "follow").turn_id)
        .collect();
    let sibling = alive[1];
    let sibling_turn = api.inspect(&ids[sibling]).unwrap().turn.unwrap().id;
    let api_b = PostgresStore::open(config.clone()).unwrap();
    let cancellation = cancel(&api_b, &ids[alive[0]], "cancel-active");
    terminal_receipt(&api, &ids[alive[0]], "first", SubmissionStatus::Interrupted).await;
    assert_eq!(
        api_b.submit_control(cancellation).unwrap().status,
        ControlStatus::Applied
    );
    assert_eq!(
        api.inspect(&ids[sibling]).unwrap().turn.unwrap().id,
        sibling_turn
    );
    assert!(b.try_wait().unwrap().is_none());
    if database_loss {
        tokio::task::block_in_place(|| {
            let mut db = postgres::Client::connect(&config.connection, postgres::NoTls).unwrap();
            let owner: String = db
                .query_one(
                    &format!("SELECT owner FROM {}.sessions WHERE id=$1", config.schema),
                    &[&ids[dead[0]]],
                )
                .unwrap()
                .get(0);
            let terminated = db
            .query(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE application_name=$1",
                &[&format!("ow-store-{owner}")],
            )
            .unwrap();
            assert_eq!(
                terminated.len(),
                1,
                "fault must target exactly one owned DB connection"
            );
        });
        assert!(!tokio::time::timeout(Duration::from_secs(15), a.wait())
            .await
            .unwrap()
            .unwrap()
            .success());
    } else {
        a.kill().await.unwrap();
    }
    assert!(b.try_wait().unwrap().is_none(), "unrelated worker died");
    assert_eq!(
        api.inspect(&ids[sibling]).unwrap().turn.unwrap().id,
        sibling_turn
    );
    assert_eq!(effects(root.path(), sibling).len(), 1);
    let mut replacement = pool(root.path(), &config.schema, "replacement", &ids[0]);
    // Release all test-controlled barriers; new explicit follow-ups may proceed,
    // but crashed/uncertain original effects must never be dispatched again.
    for n in 0..8 {
        std::fs::write(root.path().join(format!("work{n}/release")), b"release").unwrap();
    }
    for (n, sid) in ids.iter().enumerate() {
        let expected = if dead.contains(&n) || n == alive[0] {
            SubmissionStatus::Interrupted
        } else {
            SubmissionStatus::Completed
        };
        terminal_receipt(&api, sid, "first", expected).await;
        terminal_receipt(&api, sid, "follow", SubmissionStatus::Completed).await;
        assert_eq!(submit(&api_b, sid, "first").turn_id, first[n]);
        assert_eq!(submit(&api_b, sid, "follow").turn_id, follow[n]);
        let snapshot = api.inspect(sid).unwrap();
        let unknown = snapshot
            .history
            .iter()
            .filter(|v| {
                v["type"] == "function_call_output"
                    && v["output"]
                        .as_str()
                        .is_some_and(|t| t.contains("tool_outcome_unknown"))
            })
            .count();
        assert_eq!(
            unknown,
            usize::from(dead.contains(&n) || n == alive[0]),
            "unknown result leaked/missing"
        );
        let lines = effects(root.path(), n);
        assert_eq!(lines.len(), 2);
        assert_eq!(lines[0]["turn_id"], first[n]);
        assert_eq!(lines[1]["turn_id"], follow[n]);
    }
    stop_service(&mut b).await;
    stop_service(&mut replacement).await;
    check_effects(&api, root.path(), &ids, &[2; 8]);
    evidence(
        &config,
        if database_loss {
            "database-loss"
        } else {
            "process-death"
        },
        json!({"sessions":8,"slots_per_worker":2,"workers":2,"effects":16,"duplicate_effects":0,"interrupted_originals":3,"completed_followups":8,"per_session_fifo":true,"sibling_worker_survived":true,"synthetic_native_tools":true,"records":effect_evidence(&api,root.path(),&ids)}),
    );
    remove_owned_schema(&config);
}
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
#[ignore = "requires isolated OW_PG_TEST_URL and native Linux"]
async fn eight_sessions_four_slots_process_death_preserves_fifo_and_siblings() {
    multi_slot_fault(false).await;
}
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
#[ignore = "requires isolated OW_PG_TEST_URL and native Linux"]
async fn eight_sessions_four_slots_database_loss_is_scoped_and_not_replayed() {
    multi_slot_fault(true).await;
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
#[ignore = "requires isolated OW_PG_TEST_URL and native Linux"]
async fn parked_approvals_do_not_consume_slots_or_share_session_grants() {
    let (root, config, api) = service_fixture();
    let mut seeder = Worker::start(root.path(), &config.schema, "seed", None).await;
    let ids = sessions(root.path(), &mut seeder, 6, false).await;
    for sid in &ids {
        submit(&api, sid, "first");
    }
    seeder.kill().await;
    let mut a = pool(root.path(), &config.schema, "a", &ids[0]);
    let mut b = pool(root.path(), &config.schema, "b", &ids[0]);
    wait(|| {
        ids.iter().all(|sid| {
            api.inspect(sid)
                .unwrap()
                .interactions
                .iter()
                .any(|i| i.response.is_none())
        })
    })
    .await;
    assert_eq!(
        (0..6).map(|n| effects(root.path(), n).len()).sum::<usize>(),
        0
    );
    let pending: Vec<_> = ids
        .iter()
        .map(|sid| api.read_snapshot(sid).unwrap())
        .collect();
    let api_b = PostgresStore::open(config.clone()).unwrap();
    // Use another session's interaction while targeting this session/turn: reject.
    let mut wrong = decision(&ids[4], &pending[0], "wrong-session", "once");
    wrong.turn_id = pending[4]["turn"]["id"].as_str().unwrap().into();
    assert!(api_b.submit_control(wrong).is_err());
    let granted = decision(&ids[0], &pending[0], "allow-session", "session");
    let aa = api.clone();
    let bb = api_b.clone();
    let x = granted.clone();
    let y = granted.clone();
    let (left, right) = tokio::join!(
        tokio::task::spawn_blocking(move || aa.submit_control(x).unwrap()),
        tokio::task::spawn_blocking(move || bb.submit_control(y).unwrap())
    );
    assert_eq!(left.unwrap().command, right.unwrap().command);
    api_b
        .submit_control(decision(&ids[1], &pending[1], "allow-once", "once"))
        .unwrap();
    api_b
        .submit_control(decision(&ids[2], &pending[2], "deny", "deny"))
        .unwrap();
    cancel(&api_b, &ids[3], "cancel-waiting");
    for n in 0..6 {
        std::fs::write(root.path().join(format!("work{n}/release")), b"release").unwrap();
    }
    for sid in &ids[..3] {
        terminal_receipt(&api, sid, "first", SubmissionStatus::Completed).await;
    }
    terminal_receipt(&api, &ids[3], "first", SubmissionStatus::Cancelled).await;
    submit(&api_b, &ids[0], "second");
    submit(&api_b, &ids[1], "second");
    terminal_receipt(&api, &ids[0], "second", SubmissionStatus::Completed).await;
    wait(|| {
        api.inspect(&ids[1])
            .unwrap()
            .interactions
            .iter()
            .any(|i| i.response.is_none())
    })
    .await;
    assert_eq!(api.inspect(&ids[0]).unwrap().approval_grants.len(), 1);
    for sid in &ids[1..] {
        assert!(api.inspect(sid).unwrap().approval_grants.is_empty());
    }
    for n in [4, 5] {
        assert_eq!(
            api.read_snapshot(&ids[n]).unwrap()["interactions"],
            pending[n]["interactions"]
        );
    }
    api_b
        .submit_control(decision(
            &ids[1],
            &api.read_snapshot(&ids[1]).unwrap(),
            "deny-second",
            "deny",
        ))
        .unwrap();
    terminal_receipt(&api, &ids[1], "second", SubmissionStatus::Completed).await;
    for sid in &ids[4..] {
        cancel(&api_b, sid, "cleanup-cancel");
        terminal_receipt(&api, sid, "first", SubmissionStatus::Cancelled).await;
    }
    stop_service(&mut a).await;
    stop_service(&mut b).await;
    assert_eq!(
        api_b.submit_control(granted).unwrap().status,
        ControlStatus::Applied
    );
    check_effects(&api, root.path(), &ids, &[2, 1, 0, 0, 0, 0]);
    evidence(
        &config,
        "approval-isolation",
        json!({"sessions":6,"execution_slots":4,"all_six_parked":true,"session_grants":1,"cross_session_grants":0,"duplicate_decision_applied_once":true,"effects":3,"pending_identity_preserved":true,"synthetic_native_tools":true,"records":effect_evidence(&api,root.path(),&ids)}),
    );
    remove_owned_schema(&config);
}
