//! Real two-database qualification: configuration reuse is not connection reuse.
use openworker_observability::{postgres::Postgres, Store};
use openworker_server_reference::{recording, Config};
use openworker_store_postgres::PostgresStore;
use openworker_telemetry::{Operation, Outcome};
use serde_json::json;
use std::time::{Duration, Instant};

fn config(execution_env: &str, schema: &str, obs_schema: &str, obs_env: Option<&str>) -> Config {
    let mut c:Config=serde_json::from_value(json!({
        "database_env":execution_env,"schema":schema,"database_loopback_plaintext":true,
        "state_root":"unused","bundle":"unused","runner":"unused","model":"fixture","scripted":true,
        "api_token_file":"unused","actor":"recording-test",
        "openshell":{"identity":"fixture","endpoint":"https://127.0.0.1:1","certificates":"unused","workspace":"unused","image":"unused"},
        "observability":{"schema":obs_schema,"queue_capacity":32,"retention_days":30}
    })).unwrap();
    if let Some(env) = obs_env {
        c.observability.as_mut().unwrap().database = Some(
            serde_json::from_value(
                json!({"database_env":env,"database_loopback_plaintext":true,"timeout_ms":200}),
            )
            .unwrap(),
        );
    }
    c
}
fn finish(started: recording::Started) {
    if let Some(c) = started.telemetry_controller {
        assert!(c.shutdown(Duration::from_secs(2)));
    }
    if let Some(c) = started.recorder_controller {
        assert!(c.shutdown(Duration::from_secs(2)));
    }
}
#[test]
fn recording_options_are_explicit_bounded_and_optional() {
    let mut c = config(
        "OW_RECORDING_TEST_UNUSED",
        "ow_execution",
        "ow_observability",
        None,
    );
    assert!(c.observability.as_ref().unwrap().database(&c).is_err());
    c.observability.as_mut().unwrap().schema = "ow_execution".into();
    assert!(c.observability.as_ref().unwrap().database(&c).is_err());
    c.observability = None;
    let disabled = recording::start(&c, "test", true).unwrap();
    assert!(!disabled.telemetry.enabled());
    assert!(disabled.statistics.is_none());
    c.observability = Some(serde_json::from_value(json!({"queue_capacity":0})).unwrap());
    assert!(c.observability.as_ref().unwrap().database(&c).is_err());
    assert!(serde_json::from_value::<recording::Options>(json!({"unrecognized":true})).is_err());
}
#[test]
#[ignore = "two disposable loopback PostgreSQL databases required"]
fn independent_recording_targets_outage_and_actual_service_migration() {
    let execution = std::env::var("OW_EXECUTION_TEST_URL").expect("Set execution fixture variable");
    let observations =
        std::env::var("OW_OBSERVABILITY_TEST_URL").expect("Set observation fixture variable");
    assert_ne!(
        execution, observations,
        "Fixture must use two different databases"
    );
    let id = uuid::Uuid::new_v4().simple().to_string();
    let exec_env = format!("OW_EXECUTION_{}", id.to_uppercase());
    let obs_env = format!("OW_RECORDING_{}", id.to_uppercase());
    std::env::set_var(&exec_env, &execution);
    std::env::set_var(&obs_env, &observations);
    let exec_schema = format!("ow_state_{}", &id[..12]);
    let same_schema = format!("ow_obs_same_{}", &id[..12]);
    let separate_schema = format!("ow_obs_separate_{}", &id[..12]);
    let same = config(&exec_env, &exec_schema, &same_schema, None);
    let db = same.database().unwrap();
    PostgresStore::migrate(&db).unwrap();
    let execution_store = PostgresStore::open(db).unwrap();
    let recording_db = same
        .observability
        .as_ref()
        .unwrap()
        .database(&same)
        .unwrap();
    assert_eq!(&*recording_db.connection, &execution);
    Postgres::migrate(&recording_db).unwrap();
    let started = recording::start(&same, "test", true).unwrap();
    let mut span =
        started
            .telemetry
            .span(Operation::Model, Some("local-session"), Some("attempt-1"));
    span.metadata("gen_ai.request.model", "fixture-model");
    span.usage(42, 7);
    span.first_output();
    span.finish(Outcome::Ok);
    let statistics = started.statistics.as_ref().unwrap().clone();
    let deadline = Instant::now() + Duration::from_secs(3);
    while statistics.snapshot().persisted == 0 && Instant::now() < deadline {
        std::thread::sleep(Duration::from_millis(10));
    }
    assert_eq!(statistics.snapshot().persisted, 1);
    let mut same_inspector = postgres::Client::connect(&execution, postgres::NoTls).unwrap();
    let mut same_lock = same_inspector.transaction().unwrap();
    same_lock
        .batch_execute(&format!(
            "LOCK TABLE {same_schema}.observation_steps IN ACCESS EXCLUSIVE MODE"
        ))
        .unwrap();
    started
        .telemetry
        .span(
            Operation::Tool,
            Some("local-session"),
            Some("same-db-blocked"),
        )
        .finish(Outcome::Ok);
    execution_store.ready().unwrap();
    let deadline = Instant::now() + Duration::from_secs(3);
    while statistics.snapshot().failed_writes == 0 && Instant::now() < deadline {
        std::thread::sleep(Duration::from_millis(10));
    }
    assert_eq!(statistics.snapshot().failed_writes, 1);
    execution_store.ready().unwrap();
    same_lock.rollback().unwrap();
    finish(started);
    assert_eq!(statistics.snapshot().persisted, 1);
    let mut read = Postgres::open(&recording_db).unwrap();
    let rows = read.list(&exec_schema, None, 0, 100).unwrap();
    assert_eq!(rows.len(), 1);
    assert_eq!(rows[0].step.model.as_deref(), Some("fixture-model"));
    drop(read);
    let separate = config(&exec_env, &exec_schema, &separate_schema, Some(&obs_env));
    let other = separate
        .observability
        .as_ref()
        .unwrap()
        .database(&separate)
        .unwrap();
    assert_eq!(&*other.connection, &observations);
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("config.json");
    let config_json = json!({"database_env":exec_env,"schema":exec_schema,"database_loopback_plaintext":true,"state_root":"unused","bundle":"unused","runner":"unused","model":"fixture","scripted":true,"api_token_file":"unused","actor":"test","openshell":{"identity":"fixture","endpoint":"https://127.0.0.1:1","certificates":"unused","workspace":"unused","image":"unused"},"observability":{"schema":separate_schema,"database":{"database_env":obs_env,"database_loopback_plaintext":true,"timeout_ms":200},"queue_capacity":32,"retention_days":30}});
    std::fs::write(&path, serde_json::to_vec(&config_json).unwrap()).unwrap();
    let output = std::process::Command::new(env!("CARGO_BIN_EXE_openworker-server-reference"))
        .args(["--config", path.to_str().unwrap(), "migrate"])
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "Actual service migration failed: {}",
        String::from_utf8_lossy(&output.stderr)
            .replace(&execution, "[execution fixture]")
            .replace(&observations, "[recording fixture]")
    );
    let mut inspector = postgres::Client::connect(&observations, postgres::NoTls).unwrap();
    let mut state_inspector = postgres::Client::connect(&execution, postgres::NoTls).unwrap();
    assert!(!state_inspector
        .query_one(
            "SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_schema=$1)",
            &[&separate_schema]
        )
        .unwrap()
        .get::<_, bool>(0));
    let started = recording::start(&separate, "test", true).unwrap();
    let statistics = started.statistics.as_ref().unwrap().clone();
    let mut lock = inspector.transaction().unwrap();
    lock.batch_execute(&format!(
        "LOCK TABLE {separate_schema}.observation_steps IN ACCESS EXCLUSIVE MODE"
    ))
    .unwrap();
    let at = Instant::now();
    let span = started
        .telemetry
        .span(Operation::Tool, Some("session-2"), Some("call-1"));
    span.finish(Outcome::Ok);
    assert!(at.elapsed() < Duration::from_millis(100));
    execution_store.ready().unwrap(); // Real execution adapter, independent SQL.
    let deadline = Instant::now() + Duration::from_secs(3);
    while statistics.snapshot().failed_writes == 0 && Instant::now() < deadline {
        std::thread::sleep(Duration::from_millis(10));
    }
    assert_eq!(statistics.snapshot().failed_writes, 1);
    assert_eq!(statistics.snapshot().persisted, 0);
    execution_store.ready().unwrap();
    lock.rollback().unwrap();
    // New work may still be recorded after a lock-timeout; no failed action is replayed.
    let span = started
        .telemetry
        .span(Operation::Tool, Some("session-2"), Some("call-2"));
    span.finish(Outcome::Ok);
    finish(started);
    assert_eq!(statistics.snapshot().persisted, 1);
    let mut read = Postgres::open(&other).unwrap();
    assert_eq!(read.list(&exec_schema, None, 0, 100).unwrap().len(), 1);
    drop(read);
    // Explicit bad target fails closed at startup, never silently writes to state DB.
    let bad_env = format!("OW_RECORDING_BAD_{}", id.to_uppercase());
    std::env::set_var(&bad_env, "postgresql://unused@127.0.0.1:1/unavailable");
    let bad = config(&exec_env, &exec_schema, &separate_schema, Some(&bad_env));
    assert!(recording::start(&bad, "test", true).is_err());
    execution_store.ready().unwrap();
    drop(execution_store);
    state_inspector
        .batch_execute(&format!(
            "DROP SCHEMA {same_schema} CASCADE; DROP SCHEMA {exec_schema} CASCADE"
        ))
        .unwrap();
    inspector
        .batch_execute(&format!("DROP SCHEMA {separate_schema} CASCADE"))
        .unwrap();
    for name in [exec_env, obs_env, bad_env] {
        std::env::remove_var(name);
    }
}
