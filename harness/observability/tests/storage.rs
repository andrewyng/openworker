use openworker_observability::{sqlite::Sqlite, Conflict, Step, Store, Usage, Write};
#[cfg(feature = "postgres")]
use std::time::{SystemTime, UNIX_EPOCH};
fn sample(n: u64) -> Step {
    Step {
        version: 1,
        deployment: "development".into(),
        role: "worker".into(),
        trace_id: format!("{:032x}", 1),
        span_id: format!("{n:016x}"),
        parent_trace_id: None,
        parent_span_id: None,
        session_hash: Some(format!("{:064x}", 1)),
        action_hash: Some(format!("{:064x}", 2)),
        operation: "model.attempt".into(),
        outcome: "ok".into(),
        started_ms: 1000,
        ended_ms: 1100,
        duration_us: 100000,
        first_output_us: Some(40000),
        attempt: Some(1),
        model: Some("claude-sonnet".into()),
        tool: None,
        purpose: Some("main".into()),
        usage: Some(Usage {
            input: Some(100),
            output: Some(20),
            cache_read: Some(40),
            cache_write: None,
            reasoning: None,
        }),
    }
}
fn contract(store: &mut dyn Store) {
    let a = sample(1);
    assert_eq!(store.put(&a).unwrap(), Write::Inserted);
    assert_eq!(store.put(&a).unwrap(), Write::Duplicate);
    assert_eq!(
        store.get("development", &a.span_id).unwrap(),
        Some(a.clone())
    );
    let mut changed = a.clone();
    changed.duration_us += 1;
    assert!(store.put(&changed).unwrap_err().is::<Conflict>());
    assert_eq!(
        store.get("development", &a.span_id).unwrap(),
        Some(a.clone())
    );
    let mut b = sample(2);
    b.session_hash = Some(format!("{:064x}", 3));
    b.usage = None;
    store.put(&b).unwrap();
    let mut foreign = a.clone();
    foreign.deployment = "other_deployment".into();
    store.put(&foreign).unwrap();
    let first = store.list("development", None, 0, 1).unwrap();
    assert_eq!(first.len(), 1);
    assert_eq!(first[0].step, a);
    let second = store.list("development", None, first[0].cursor, 1).unwrap();
    assert_eq!(second[0].step, b);
    assert!(store
        .list("development", None, second[0].cursor, 1)
        .unwrap()
        .is_empty());
    assert_eq!(
        store
            .list("development", a.session_hash.as_deref(), 0, 100)
            .unwrap()
            .len(),
        1
    );
    assert!(store.list("not_present", None, 0, 100).unwrap().is_empty());
    assert!(store.list("development", None, 0, 0).is_err());
    assert!(store.list("development", None, 0, 1001).is_err());
    assert_eq!(store.prune("development", 1100, 100).unwrap(), 0);
    assert_eq!(store.prune("development", 1101, 1).unwrap(), 1);
    assert_eq!(store.list("development", None, 0, 100).unwrap().len(), 1);
    assert_eq!(
        store.list("other_deployment", None, 0, 100).unwrap().len(),
        1
    );
}
#[test]
fn sqlite_contract_restart_and_retention_isolation() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("observations.db");
    let mut store = Sqlite::open(&path).unwrap();
    contract(&mut store);
    drop(store);
    let mut reopened = Sqlite::open(&path).unwrap();
    assert_eq!(reopened.list("development", None, 0, 100).unwrap().len(), 1);
    let db = rusqlite::Connection::open(&path).unwrap();
    db.execute_batch(
        "CREATE TABLE execution_audit(id INTEGER); INSERT INTO execution_audit VALUES(1)",
    )
    .unwrap();
    reopened.prune("development", u64::MAX / 2, 1000).unwrap();
    assert_eq!(
        db.query_row("SELECT count(*) FROM execution_audit", [], |r| r
            .get::<_, i64>(0))
            .unwrap(),
        1
    );
    assert_eq!(
        reopened
            .list("other_deployment", None, 0, 100)
            .unwrap()
            .len(),
        1
    );
}
#[test]
fn validation_rejects_content_overflow_inconsistent_usage_and_unknown_fields() {
    sample(1).validate().unwrap();
    let invalid: Vec<Box<dyn Fn(&mut Step)>> = vec![
        Box::new(|s| s.version = 2),
        Box::new(|s| s.ended_ms = 999),
        Box::new(|s| s.span_id = "not-hex".into()),
        Box::new(|s| s.parent_span_id = Some("0000000000000001".into())),
        Box::new(|s| s.first_output_us = Some(100001)),
        Box::new(|s| s.model = Some("https://private.example".into())),
        Box::new(|s| s.model = Some("sk-testnotarealkey123456789".into())),
        Box::new(|s| s.model = Some("model".repeat(100))),
        Box::new(|s| s.usage.as_mut().unwrap().input = Some(u64::MAX)),
        Box::new(|s| s.usage.as_mut().unwrap().cache_write = Some(80)),
        Box::new(|s| s.operation = "tool.execute".into()),
    ];
    for mutate in invalid {
        let mut s = sample(1);
        mutate(&mut s);
        assert!(s.validate().is_err());
    }
    let mut raw = serde_json::to_value(sample(1)).unwrap();
    raw["prompt"] = serde_json::json!("private data");
    assert!(serde_json::from_value::<Step>(raw).is_err());
}
#[test]
fn unsupported_sqlite_schema_is_not_reset() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("observations.db");
    drop(Sqlite::open(&path).unwrap());
    let db = rusqlite::Connection::open(&path).unwrap();
    db.execute("UPDATE ow_observation_version SET version=9", [])
        .unwrap();
    assert!(Sqlite::open(&path).is_err());
    assert_eq!(
        db.query_row("SELECT version FROM ow_observation_version", [], |r| r
            .get::<_, i64>(0))
            .unwrap(),
        9
    );
}

#[cfg(unix)]
#[test]
fn sqlite_rejects_symlink_file_without_modifying_its_target() {
    use std::os::unix::fs::{symlink, PermissionsExt};
    let dir = tempfile::tempdir().unwrap();
    let target = dir.path().join("actual.db");
    drop(Sqlite::open(&target).unwrap());
    assert_eq!(
        std::fs::metadata(&target).unwrap().permissions().mode() & 0o777,
        0o600
    );
    let before = std::fs::read(&target).unwrap();
    let link = dir.path().join("linked.db");
    symlink(&target, &link).unwrap();
    assert!(Sqlite::open(&link).is_err());
    assert_eq!(std::fs::read(&target).unwrap(), before);
}

#[cfg(feature = "postgres")]
#[test]
#[ignore = "disposable loopback PostgreSQL fixture required"]
fn postgres_contract_restart_and_retention_isolation() {
    use openworker_observability::postgres::{Config, Postgres, Tls};
    let url = std::env::var("OW_OBSERVABILITY_TEST_URL").expect("Set disposable fixture variable");
    let schema = format!(
        "ow_obs_contract_{}",
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos()
    );
    let mut config = Config::new(url.clone(), schema.clone());
    config.tls = Tls::LoopbackPlaintext;
    assert!(Postgres::open(&config).is_err());
    Postgres::migrate(&config).unwrap();
    Postgres::migrate(&config).unwrap();
    let mut store = Postgres::open(&config).unwrap();
    contract(&mut store);
    drop(store);
    let mut store = Postgres::open(&config).unwrap();
    assert_eq!(store.list("development", None, 0, 100).unwrap().len(), 1);
    let mut independent = postgres::Client::connect(&url, postgres::NoTls).unwrap();
    independent.batch_execute(&format!("CREATE TABLE {schema}.execution_audit(id INTEGER); INSERT INTO {schema}.execution_audit VALUES(1)")).unwrap();
    store.prune("development", u64::MAX / 2, 1000).unwrap();
    assert_eq!(
        independent
            .query_one(
                &format!("SELECT count(*) FROM {schema}.execution_audit"),
                &[]
            )
            .unwrap()
            .get::<_, i64>(0),
        1
    );
    assert_eq!(
        store.list("other_deployment", None, 0, 100).unwrap().len(),
        1
    );
    let mut timings = Vec::new();
    let started = std::time::Instant::now();
    for n in 10..110 {
        let mut row = sample(n);
        row.deployment = "write_probe".into();
        let at = std::time::Instant::now();
        store.put(&row).unwrap();
        timings.push(at.elapsed().as_micros() as u64);
    }
    let seconds = started.elapsed().as_secs_f64();
    timings.sort_unstable();
    println!(
        "OBSERVABILITY_WRITE_PROBE {}",
        serde_json::json!({"records":100,"transaction_size":1,"elapsed_seconds":seconds,"records_per_second":100.0/seconds,"p50_write_us":timings[49],"p95_write_us":timings[94],"scope":"single local Docker connection, not a capacity guarantee"})
    );
    independent
        .batch_execute(&format!("DROP SCHEMA {schema} CASCADE"))
        .unwrap();
}

#[cfg(feature = "postgres")]
#[test]
fn postgres_connection_validation() {
    use openworker_observability::postgres::{Config, Postgres, Tls};
    let mut c = Config::new(
        "postgres://user:synthetic@192.0.2.1/db".into(),
        "ow_observability".into(),
    );
    c.tls = Tls::LoopbackPlaintext;
    assert!(Postgres::open(&c).is_err());
    c.connection = "host=127.0.0.1 hostaddr=192.0.2.1 dbname=db"
        .to_owned()
        .into();
    assert!(Postgres::open(&c).is_err());
    c.schema = "ow_obs;DROP SCHEMA public".into();
    assert!(c.validate().is_err());
}
