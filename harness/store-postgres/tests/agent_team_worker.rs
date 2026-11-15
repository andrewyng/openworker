#![cfg(feature = "test-support")]
//! Real PostgreSQL worker pool: a parked lead cannot occupy the child's only slot.
use openworker_runtime::{execution::Backend, model::Model, runtime::Runtime, store::StateStore};
use openworker_store_postgres::{
    worker::{Factory, Options, Selector, Worker},
    Config, PostgresStore, Tls, TurnSubmission,
};
use serde_json::json;
use std::{
    path::{Path, PathBuf},
    sync::Arc,
    time::Duration,
};
use tokio_util::sync::CancellationToken;
fn plan(steps: serde_json::Value) -> String {
    format!("TEST_RESOURCE_GRANTS {steps}")
}
fn runtime(root: &Path, kind: &str, db: PostgresStore) -> Arc<Runtime> {
    Runtime::open_instrumented(
        &root.join(format!("rt-{kind}-{}", uuid::Uuid::new_v4().simple())),
        &root.join(kind),
        &std::env::current_exe().unwrap(),
        Model::new("fixture".into(), true),
        Backend::Off,
        None,
        None,
        Default::default(),
        Some(Box::new(db)),
        Default::default(),
    )
    .unwrap()
}
fn selector(rt: &Runtime) -> Selector {
    Selector {
        coworker: rt.coworker().into(),
        bundle_digest: rt.execution_digest().into(),
        backend: Backend::Off,
        scripted: true,
        fallback_model: "fixture".into(),
        model_provider: Default::default(),
        model_connections: Default::default(),
        environment_provider: None,
        environment_identity: None,
        environment_profile: None,
    }
}
async fn scenario(retire_mode: Option<openworker_runtime::team_lifecycle::Mode>) {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path().canonicalize().unwrap();
    std::fs::create_dir(root.join("work")).unwrap();
    for kind in ["lead", "builder"] {
        std::fs::create_dir_all(root.join(kind).join("coworker")).unwrap();
        std::fs::write(
            root.join(kind).join("coworker/manifest.md"),
            format!(
                "---\nid: {kind}\nname: {kind}\nplanning: true\n---\nCoordinate using named teams."
            ),
        )
        .unwrap();
        std::fs::write(
            root.join(kind).join("tools.json"),
            r#"{"version":3,"tools":[]}"#,
        )
        .unwrap();
    }
    let mut config = Config::new(
        std::env::var("OW_PG_TEST_URL").unwrap(),
        format!("ow_one_slot_{}", uuid::Uuid::new_v4().simple()),
    );
    config.tls = Tls::LoopbackPlaintext;
    PostgresStore::migrate(&config).unwrap();
    let mut seed = PostgresStore::open(config.clone()).unwrap();
    let lead_rt = runtime(&root, "lead", seed.clone());
    let builder_rt = runtime(&root, "builder", seed.clone());
    let lead = lead_rt
        .prepare_session(json!({"coworker":"lead","workspace":root.join("work"),"name":"Lead"}))
        .unwrap();
    seed.save(&lead, "created", json!({})).unwrap();
    let doc = seed
        .agent_team(
            &lead.id,
            "create",
            &json!({"agent_id":"lead","agent_types":["builder"]}),
            None,
        )
        .unwrap();
    let reserved = seed
        .agent_team(
            &lead.id,
            "team_spawn",
            &json!({"agent_type":"builder","agent_id":"builder-ui","message":"Work"}),
            None,
        )
        .unwrap();
    let mut child = builder_rt
        .prepare_session(
            json!({"coworker":"builder","workspace":root.join("work"),"name":"Builder"}),
        )
        .unwrap();
    child.id = reserved["instance"]["session_id"].as_str().unwrap().into();
    child.agent_team = Some(openworker_runtime::agent_team::Membership {
        team_id: doc["id"].as_str().unwrap().into(),
        agent_id: "builder-ui".into(),
        inbox_cursor: 0,
        last_output: None,
        notices: vec![],
        waiting: None,
    });
    seed.save(&child, "child", json!({})).unwrap();
    seed.agent_team(&lead.id, "ready", &json!({"agent_id":"builder-ui"}), None)
        .unwrap();
    let selectors = [selector(&lead_rt), selector(&builder_rt)];
    // These seed runtimes never execute; release before a one-slot pool takes over.
    seed.release(&lead.id).unwrap();
    seed.release(&child.id).unwrap();
    drop(lead_rt);
    drop(builder_rt);
    let mut factories = vec![];
    for kind in ["lead", "builder"] {
        let root: PathBuf = root.clone();
        let kind = kind.to_owned();
        let factory: Factory = Arc::new(move |_, db| Ok(runtime(&root, &kind, db)));
        factories.push(factory);
    }
    let catalog: Vec<_> = selectors
        .iter()
        .cloned()
        .zip(factories.iter().cloned())
        .collect();
    let stop = CancellationToken::new();
    let worker = Worker::new(
        config.clone(),
        selectors[0].clone(),
        Options {
            slots: 1,
            poll_interval: Duration::from_millis(50),
            ..Default::default()
        },
        factories[0].clone(),
    )
    .unwrap()
    .with_catalog(Arc::new(move || Ok(catalog.clone())));
    seed.submit_turn(TurnSubmission{session_id:lead.id.clone(),submission_id:"lead-task".into(),text:plan(json!([{"name":"team_wait_agent","arguments":{"agent_ids":["builder-ui"],"timeout_ms":60000}}])),limits:None}).unwrap();

    let token = stop.clone();
    let task = tokio::spawn(async move {
        let result = worker.run(token).await;
        if let Err(error) = &result {
            eprintln!("isolated worker failed: {error:#}");
        }
        result
    });
    // Establish the test's ordering, rather than assuming a fair scheduler will
    // choose the parent first when both sessions already have queued work.
    tokio::time::timeout(Duration::from_secs(30), async {
        loop {
            if seed.read_snapshot(&lead.id).unwrap()["turn"]["status"] == "waiting_agents" {
                break;
            }
            assert!(!task.is_finished());
            tokio::time::sleep(Duration::from_millis(50)).await;
        }
    })
    .await
    .expect("Lead must park before child admission");
    let child_steps = if let Some(mode) = retire_mode {
        json!([{"name":"team_wait_agent","arguments":{"agent_ids":["lead"],"timeout_ms":if mode==openworker_runtime::team_lifecycle::Mode::Cancel {60000}else{1500}}},{"name":"team_post_board","arguments":{"message":"@lead verified child proof"}}])
    } else {
        json!([{"name":"team_post_board","arguments":{"message":"@lead verified child proof"}}])
    };
    seed.submit_turn(TurnSubmission {
        session_id: child.id.clone(),
        submission_id: "child-task".into(),
        text: plan(child_steps),
        limits: None,
    })
    .unwrap();
    if let Some(mode) = retire_mode {
        tokio::time::timeout(Duration::from_secs(30), async {
            loop {
                if seed.read_snapshot(&child.id).unwrap()["turn"]["status"] == "waiting_agents" {
                    break;
                }
                assert!(!task.is_finished());
                tokio::time::sleep(Duration::from_millis(25)).await;
            }
        })
        .await
        .expect("Child must park before retirement");
        seed.submit_turn(TurnSubmission {
            session_id: child.id.clone(),
            submission_id: "queued-later".into(),
            text: "Never start after retirement".into(),
            limits: None,
        })
        .unwrap();
        seed.agent_team_api(&lead.id,"command",json!({"request_id":"retire-child","operation":"team_manage","arguments":{"action":"retire","agent_id":"builder-ui","mode":mode}})).unwrap();
        tokio::time::timeout(Duration::from_secs(10), async {
            loop {
                seed.reconcile_stopped_agent_team().unwrap();
                let status = seed.submission(&child.id, "queued-later").unwrap().status;
                assert_ne!(
                    status,
                    openworker_store_postgres::SubmissionStatus::Started,
                    "Retirement cannot promote queued followups"
                );
                if status == openworker_store_postgres::SubmissionStatus::Cancelled {
                    break;
                }
                tokio::time::sleep(Duration::from_millis(25)).await;
            }
        })
        .await
        .expect("Controller must reconcile even when a worker temporarily holds the row lock");
        assert!(seed
            .submit_turn(TurnSubmission {
                session_id: child.id.clone(),
                submission_id: "too-late".into(),
                text: "Blocked".into(),
                limits: None
            })
            .is_err());
        tokio::time::timeout(Duration::from_secs(30), async {
            loop {
                seed.reconcile_stopped_agent_team().unwrap();
                let s = seed.read_snapshot(&child.id).unwrap();
                let doc = seed
                    .agent_team_api(&lead.id, "snapshot", json!({}))
                    .unwrap();
                if s["session"]["closed"] == true
                    && doc["roster"]["instances"]["builder-ui"]["phase"] == "closed"
                {
                    break;
                }
                assert!(!task.is_finished());
                tokio::time::sleep(Duration::from_millis(50)).await;
            }
        })
        .await
        .expect("Owning worker must settle retirement");
        assert_eq!(
            seed.submission(&child.id, "queued-later").unwrap().status,
            openworker_store_postgres::SubmissionStatus::Cancelled
        );
        assert_eq!(
            seed.read_snapshot(&child.id).unwrap()["turn"]["status"],
            if mode == openworker_runtime::team_lifecycle::Mode::Cancel {
                "cancelled"
            } else {
                "completed"
            }
        );
    }

    let result = tokio::time::timeout(Duration::from_secs(45), async {
        loop {
            let s = seed.read_snapshot(&lead.id).unwrap();
            if s["turn"]["status"] == "completed" {
                break s;
            }
            assert!(!task.is_finished(), "Worker must remain healthy");
            tokio::time::sleep(Duration::from_millis(50)).await;
        }
    })
    .await;
    stop.cancel();
    task.await.unwrap().unwrap();
    let result = result.expect("A parent must not starve its child");
    assert_eq!(result["turn"]["status"], "completed");
    let events = seed.read_events(&lead.id, 0).unwrap();
    assert!(events
        .iter()
        .any(|e| e.kind == "agent_team" && e.data["operation"] == "team_wait_agent"));
    assert!(
        events.iter().filter(|e| e.kind == "owner_claimed").count() >= 2,
        "Lead must release and reacquire ownership"
    );
    if retire_mode != Some(openworker_runtime::team_lifecycle::Mode::Cancel) {
        assert!(result["messages"]
            .to_string()
            .contains("verified child proof"));
    }
    assert!(!result["messages"]
        .to_string()
        .contains("\\\"timeout\\\":true"));
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
#[ignore = "requires isolated OW_PG_TEST_URL"]
async fn one_slot_wait_releases_and_resumes_across_worker_ownership() {
    scenario(None).await;
}
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
#[ignore = "requires isolated OW_PG_TEST_URL"]
async fn finish_current_retirement_resumes_parked_work_and_cancels_queued_followups() {
    scenario(Some(
        openworker_runtime::team_lifecycle::Mode::FinishCurrent,
    ))
    .await;
}
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
#[ignore = "requires isolated OW_PG_TEST_URL"]
async fn cancel_retirement_settles_parked_work_and_releases_its_slot() {
    scenario(Some(openworker_runtime::team_lifecycle::Mode::Cancel)).await;
}
