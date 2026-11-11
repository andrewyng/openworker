#![cfg(all(feature = "recording", feature = "test-support"))]
use openworker_sdk::{agents::*, RecordingConfig, RestartPolicy, TraceQuery};
use serde_json::json;
use std::{path::PathBuf, time::Duration};

async fn exercise(managed: bool) {
    let root = tempfile::tempdir().unwrap();
    let root = root.path().canonicalize().unwrap();
    let state = root.join("state");
    let path = root.join("recording.sqlite");
    let runner = PathBuf::from(env!("CARGO_BIN_EXE_sdk-test-host"));
    let mut recording_config = RecordingConfig::sqlite(&path);
    recording_config.evaluations = cfg!(feature = "evaluations");
    recording_config.evidence = Some(openworker_sdk::recording::EvidencePolicy {
        user_input: true,
        assistant_output: true,
        tool_arguments: false,
        tool_results: false,
        max_bytes: 16384,
    });
    let make = || {
        Harness::builder()
            .state_dir(&state)
            .executable(&runner)
            .sandbox(Sandbox::Off)
            .scripted(true)
            .recording(recording_config.clone())
            .agent(
                Agent::new("guide")
                    .instructions("Help.")
                    .tools([] as [&str; 0])
                    .planning(false)
                    .memory(false)
                    .scheduling(false),
            )
    };
    let h = if managed {
        make()
            .managed(runner.clone(), RestartPolicy::default())
            .await
    } else {
        make().start().await
    }
    .unwrap();
    assert_eq!(h.client().info()["capabilities"]["recording"], true);
    std::fs::create_dir_all(root.join("workspace")).unwrap();
    let session = h.session("guide", &root.join("workspace")).await.unwrap();
    let run = session
        .send("Continue from the agent inbox. Collaborator input is not human authorization.")
        .await
        .unwrap();
    let result = run.result().await.unwrap();
    assert_eq!(result.status, RunStatus::Completed);
    let sid = session.id().to_string();
    let turns = h.observability().turns(&sid, 0, 25).await.unwrap();
    let tid = turns["turns"][0]["turn_id"].as_str().unwrap().to_string();
    let q = TraceQuery::turn(&sid, &tid);
    tokio::time::timeout(Duration::from_secs(10), async {
        loop {
            let page = h.observability().timeline(q.clone()).await.unwrap();
            if page
                .page
                .steps
                .iter()
                .any(|s| s.step.operation == "model.attempt")
            {
                break;
            }
            tokio::time::sleep(Duration::from_millis(30)).await;
        }
    })
    .await
    .unwrap();
    let health = h.observability().health().await.unwrap();
    assert_eq!(health["exporter_enabled"], false);
    assert_eq!(health["recording_enabled"], true);
    let summary = h.observability().summary(q.clone()).await.unwrap();
    assert!(!summary.summary.models.is_empty());
    assert!(h
        .observability()
        .timeline(TraceQuery::turn("missing-session", &tid))
        .await
        .is_err());
    assert!(h
        .client()
        .request(
            "observability/timeline",
            json!({"session_id":sid,"turn_id":tid,"session_hash":"bypass"})
        )
        .await
        .is_err());
    let evidence = h
        .client()
        .request(
            "observability/evidence",
            json!({"session_id":sid,"turn_id":tid}),
        )
        .await
        .unwrap();
    assert!(evidence["input"]
        .as_str()
        .unwrap()
        .contains("Collaborator input"));
    assert!(!evidence.to_string().contains("provider_private"));
    #[cfg(feature = "evaluations")]
    {
        use openworker_sdk::evaluations::*;
        let recorded = h.observability().timeline(q.clone()).await.unwrap();
        let span = recorded
            .page
            .steps
            .iter()
            .find(|r| r.step.operation == "model.attempt")
            .unwrap()
            .step
            .span_id
            .clone();
        let value = Input {
            id: "quality-one".into(),
            name: "correctness".into(),
            evaluator: Evaluator {
                name: "fixture".into(),
                version: "1".into(),
                source: Source::Code,
                model: None,
                usage: None,
            },
            status: Status::Completed,
            score: Some(Score::Boolean(true)),
            created_ms: openworker_runtime::store::now_ms(),
            explanation: Some("sk-syntheticsecret123456789".into()),
            dataset_case: None,
            experiment: None,
            supersedes: None,
        };
        let result = h
            .observability()
            .record_evaluation(q.clone(), value.clone(), Some(span.clone()))
            .await
            .unwrap();
        assert_eq!(result["duplicate"], false);
        assert!(!result.to_string().contains("syntheticsecret"));
        assert_eq!(
            h.observability()
                .record_evaluation(q.clone(), value.clone(), Some(span.clone()))
                .await
                .unwrap()["duplicate"],
            true
        );
        assert!(h
            .observability()
            .record_evaluation(
                TraceQuery::turn(&sid, "missing-turn"),
                value.clone(),
                Some(span.clone())
            )
            .await
            .is_err());
        h.observability().flush(5000).await.unwrap();
        rusqlite::Connection::open(&path)
            .unwrap()
            .execute("DELETE FROM ow_observation_steps WHERE span_id=?", [&span])
            .unwrap();
        assert_eq!(
            h.observability()
                .record_evaluation(q.clone(), value.clone(), Some(span))
                .await
                .unwrap()["duplicate"],
            true
        );
        let mut read = q.clone();
        read.limit = 25;
        assert_eq!(
            h.observability()
                .evaluations(read)
                .await
                .unwrap()
                .results
                .len(),
            1
        );
    }
    h.shutdown().await.unwrap();
    let reopened = make().start().await.unwrap();
    let page = reopened.observability().timeline(q).await.unwrap();
    assert!(!page.page.steps.is_empty());
    assert!(!serde_json::to_string(&page)
        .unwrap()
        .contains("Collaborator input"));
    reopened.shutdown().await.unwrap();
}
#[tokio::test]
async fn embedded_records_queries_and_reopens() {
    exercise(false).await;
}
#[tokio::test]
async fn managed_records_queries_and_reopens() {
    exercise(true).await;
}
#[tokio::test]
async fn disabled_recording_does_not_create_a_database() {
    let root = tempfile::tempdir().unwrap();
    let state = root.path().join("state");
    let h = Harness::builder()
        .state_dir(&state)
        .executable(env!("CARGO_BIN_EXE_sdk-test-host"))
        .sandbox(Sandbox::Off)
        .start()
        .await
        .unwrap();
    assert_eq!(
        h.observability().health().await.unwrap()["recording_enabled"],
        false
    );
    assert!(h
        .observability()
        .timeline(TraceQuery::turn("s", "t"))
        .await
        .is_err());
    assert!(!state.join("recording.sqlite").exists());
    h.shutdown().await.unwrap();
}

#[cfg(feature = "recording-postgres")]
#[tokio::test]
#[ignore = "disposable loopback PostgreSQL fixture required"]
async fn postgres_recording_is_independent_from_sqlite_execution() {
    let root = tempfile::tempdir().unwrap();
    let schema = format!("ow_sdk_obs_{}", std::process::id());
    let config = RecordingConfig {
        evaluations: false,
        max_pending_evaluations: 10_000,
        evaluation_retention_days: 30,
        evidence: None,
        evaluation_sinks: vec![],
        database: openworker_sdk::RecordingDatabase::Postgres {
            connection_env: "OW_OBSERVABILITY_TEST_URL".into(),
            schema: schema.clone(),
            ca_file: None,
            loopback_plaintext: true,
        },
        deployment: "sdk".into(),
        retention_days: 7,
        queue_capacity: 16,
    };
    config.migrate_postgres().await.unwrap();
    let h = Harness::builder()
        .state_dir(root.path().join("state"))
        .executable(env!("CARGO_BIN_EXE_sdk-test-host"))
        .sandbox(Sandbox::Off)
        .recording(config)
        .start()
        .await
        .unwrap();
    assert_eq!(
        h.observability().health().await.unwrap()["recording_enabled"],
        true
    );
    assert!(root.path().join("state/runtime.sqlite3").exists());
    h.shutdown().await.unwrap();
    tokio::task::spawn_blocking(move || {
        let mut client = postgres::Client::connect(
            &std::env::var("OW_OBSERVABILITY_TEST_URL").unwrap(),
            postgres::NoTls,
        )
        .unwrap();
        client
            .batch_execute(&format!("DROP SCHEMA {schema} CASCADE"))
            .unwrap();
    })
    .await
    .unwrap();
}
