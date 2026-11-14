#![cfg(feature = "evaluations")]
use openworker_observability::{evaluations::*, sqlite::Sqlite, Store, Write};
fn sample(id: &str) -> Evaluation {
    Evaluation {
        version: 1,
        deployment: "sdk".into(),
        target: Target {
            session_hash: format!("{:064x}", 1),
            turn_hash: format!("{:064x}", 2),
            trace_id: None,
            span_id: None,
        },
        result: Input {
            id: id.into(),
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
            created_ms: 1,
            explanation: None,
            dataset_case: None,
            experiment: None,
            supersedes: None,
        },
    }
}
#[test]
fn scores_survive_reopen_and_do_not_rewrite_prior_evidence() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("traces.sqlite");
    let mut store = Sqlite::open(&path).unwrap();
    let e = sample("e1");
    assert_eq!(store.record_evaluation(&e).unwrap(), Write::Inserted);
    assert_eq!(store.record_evaluation(&e).unwrap(), Write::Duplicate);
    let mut conflict = e.clone();
    conflict.result.score = Some(Score::Boolean(false));
    assert!(store.record_evaluation(&conflict).is_err());
    let mut correction = sample("e2");
    correction.result.supersedes = Some("e1".into());
    correction.result.score = Some(Score::Boolean(false));
    store.record_evaluation(&correction).unwrap();
    drop(store);
    let mut store = Sqlite::open(&path).unwrap();
    let mut query = Query {
        deployment: "sdk".into(),
        session_hash: e.target.session_hash.clone(),
        turn_hash: e.target.turn_hash.clone(),
        after: 0,
        limit: 1,
    };
    let page = store.evaluations(&query).unwrap();
    assert_eq!(page.results[0].evaluation, e);
    query.after = page.next.unwrap();
    let page = store.evaluations(&query).unwrap();
    assert_eq!(page.results[0].evaluation, correction);
    assert!(page.next.is_none());
    query.after = 0;
    query.turn_hash = format!("{:064x}", 3);
    assert!(store.evaluations(&query).unwrap().results.is_empty());
    assert_eq!(
        store.prune_evaluations("sdk", i64::MAX as u64, 1).unwrap(),
        1
    );
    assert!(store.evaluation("sdk", "e1").unwrap().is_none());
    assert_eq!(
        store.record_evaluation(&correction).unwrap(),
        Write::Duplicate
    );
}

#[cfg(feature = "postgres")]
#[test]
#[ignore = "disposable loopback PostgreSQL fixture required"]
fn postgres_evaluations_are_idempotent_scoped_and_durable() {
    use openworker_observability::postgres::{Config, Postgres, Tls};
    let url = std::env::var("OW_OBSERVABILITY_TEST_URL").unwrap();
    let schema = format!(
        "ow_eval_{}",
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos()
    );
    let mut c = Config::new(url.clone(), schema.clone());
    c.tls = Tls::LoopbackPlaintext;
    Postgres::migrate(&c).unwrap();
    let mut store = Postgres::open(&c).unwrap();
    let e = sample("pg-score");
    assert_eq!(store.record_evaluation(&e).unwrap(), Write::Inserted);
    assert_eq!(store.record_evaluation(&e).unwrap(), Write::Duplicate);
    drop(store);
    let mut store = Postgres::open(&c).unwrap();
    let mut q = Query {
        deployment: "sdk".into(),
        session_hash: e.target.session_hash.clone(),
        turn_hash: e.target.turn_hash.clone(),
        after: 0,
        limit: 25,
    };
    assert_eq!(store.evaluations(&q).unwrap().results[0].evaluation, e);
    q.turn_hash = format!("{:064x}", 3);
    assert!(store.evaluations(&q).unwrap().results.is_empty());
    let mut correction = e.clone();
    correction.result.id = "pg-correction".into();
    correction.result.supersedes = Some(e.result.id.clone());
    store.record_evaluation(&correction).unwrap();
    assert_eq!(
        store.prune_evaluations("sdk", i64::MAX as u64, 1).unwrap(),
        1
    );
    assert_eq!(
        store.record_evaluation(&correction).unwrap(),
        Write::Duplicate
    );
    drop(store);
    let mut outbox = Postgres::open(&c).unwrap();
    // A distinct deployment keeps the earlier storage contract out of this queue.
    outbox
        .prune_evaluations("sdk", i64::MAX as u64, 100)
        .unwrap();
    delivery_contract(&mut outbox);
    drop(outbox);
    let mut cleanup = postgres::Client::connect(&url, postgres::NoTls).unwrap();
    cleanup
        .batch_execute(&format!("DROP SCHEMA {schema} CASCADE"))
        .unwrap();
}

fn delivery_contract(store: &mut dyn Store) {
    let binding = "a".repeat(64);
    store
        .register_evaluation_sink("sdk", "langfuse", &binding)
        .unwrap();
    let first = sample("delivery-one");
    let second = sample("delivery-two");
    store.record_evaluation_bounded(&first, 1).unwrap();
    assert_eq!(
        store.record_evaluation_bounded(&first, 1).unwrap(),
        Write::Duplicate
    );
    assert!(store.record_evaluation_bounded(&second, 1).is_err());
    let claimed = store
        .claim_evaluation_export("sdk", "langfuse", &binding, "first", 100)
        .unwrap()
        .unwrap();
    assert_eq!(claimed.row.evaluation, first);
    assert!(store
        .claim_evaluation_export("sdk", "langfuse", &binding, "other", 101)
        .unwrap()
        .is_none());
    assert_eq!(
        store
            .prune_evaluations("sdk", i64::MAX as u64, 100)
            .unwrap(),
        0
    );
    assert!(store
        .settle_evaluation_export("sdk", "langfuse", "forged", 102, true)
        .is_err());
    store
        .settle_evaluation_export("sdk", "langfuse", "first", 102, false)
        .unwrap();
    assert!(store
        .claim_evaluation_export("sdk", "langfuse", &binding, "retry", 103)
        .unwrap()
        .is_none());
    let retry = store
        .claim_evaluation_export("sdk", "langfuse", &binding, "retry", 10000)
        .unwrap()
        .unwrap();
    assert_eq!(retry.row.evaluation, first);
    store
        .settle_evaluation_export("sdk", "langfuse", "retry", 10001, true)
        .unwrap();
    // Retained, delivered evaluations must not consume pending admission capacity.
    store.record_evaluation_bounded(&second, 1).unwrap();
    assert_eq!(
        store
            .prune_evaluations("sdk", i64::MAX as u64, 100)
            .unwrap(),
        1
    );
    assert!(store
        .register_evaluation_sink("sdk", "langfuse", &"b".repeat(64))
        .is_err());
}
#[test]
fn sqlite_outbox_backoff_binding_and_pending_retention() {
    let root = tempfile::tempdir().unwrap();
    let path = root.path().join("trace.sqlite");
    let mut store = Sqlite::open(&path).unwrap();
    delivery_contract(&mut store);
    drop(store);
    let mut reopened = Sqlite::open(&path).unwrap();
    let next = reopened
        .claim_evaluation_export("sdk", "langfuse", &"a".repeat(64), "restart", 20000)
        .unwrap()
        .unwrap();
    assert_eq!(next.row.evaluation.result.id, "delivery-two");
}
