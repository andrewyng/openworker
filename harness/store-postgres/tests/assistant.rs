#[path = "../../tests/assistant_contract.rs"]
mod contract;
use openworker_runtime::store::StateStore;
use openworker_store_postgres::{Config, PostgresStore, Tls};
use serde_json::json;
fn config() -> Config {
    let mut c = Config::new(
        std::env::var("OW_PG_TEST_URL").unwrap(),
        format!("ow_assistant_{}", uuid::Uuid::new_v4().simple()),
    );
    c.tls = Tls::LoopbackPlaintext;
    c
}
#[test]
#[ignore = "requires isolated OW_PG_TEST_URL"]
fn postgres_memory_and_schedule_contract() {
    let c = config();
    PostgresStore::migrate(&c).unwrap();
    PostgresStore::migrate(&c).unwrap();
    let mut s = PostgresStore::open(c).unwrap();
    contract::exercise(&mut s);
}
#[test]
#[ignore = "requires isolated OW_PG_TEST_URL"]
fn postgres_atomic_memory_rollback() {
    let c = config();
    PostgresStore::migrate(&c).unwrap();
    let mut s = PostgresStore::open(c.clone()).unwrap();
    s.save(&contract::session("a", Some("scope")), "created", json!({}))
        .unwrap();
    s.accept("a", "first", "Remember a fact").unwrap();
    let args = json!({"summary":"Fact","content":"must roll back"});
    let (tid, call) = contract::prepare(&mut s, "a", "memory_remember", &args);
    let mut db = postgres::Client::connect(&c.connection, postgres::NoTls).unwrap();
    db.batch_execute(&format!(
        "SET ow.runtime_schema='7'; SET search_path={},pg_catalog",
        c.schema
    ))
    .unwrap();
    db.batch_execute("CREATE FUNCTION reject_memory_event() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'fixture'; END $$; CREATE TRIGGER reject_memory_event BEFORE INSERT ON events FOR EACH ROW EXECUTE FUNCTION reject_memory_event();").unwrap();
    assert!(s.assistant_call("a", &tid, &call, &args).is_err());
    assert_eq!(
        db.query_one("SELECT count(*) FROM assistant_memories", &[])
            .unwrap()
            .get::<_, i64>(0),
        0
    );
    assert!(
        s.get("a").is_err(),
        "Failed transaction poisons the connection"
    );
    let snapshot = PostgresStore::open(c.clone())
        .unwrap()
        .inspect("a")
        .unwrap();
    assert_eq!(snapshot.turn.unwrap().queue, vec![call.clone()]);
    db.batch_execute("DROP TRIGGER reject_memory_event ON events")
        .unwrap();
    db.execute(
        "UPDATE sessions SET expires=clock_timestamp()-interval '1 second' WHERE id='a'",
        &[],
    )
    .unwrap();
    s = PostgresStore::open(c).unwrap();
    s.claim("a").unwrap();
    s.assistant_call("a", &tid, &call, &args).unwrap();
}

#[test]
#[ignore = "requires isolated OW_PG_TEST_URL"]
fn concurrent_sessions_share_memory_without_lost_updates() {
    let c = config();
    PostgresStore::migrate(&c).unwrap();
    let barrier = std::sync::Arc::new(std::sync::Barrier::new(2));
    let handles = ["coder", "evaluator"].map(|id| {
        let c = c.clone();
        let barrier = barrier.clone();
        std::thread::spawn(move || {
            let mut store = PostgresStore::open(c).unwrap();
            store
                .save(
                    &contract::session(id, Some("learner")),
                    "created",
                    json!({}),
                )
                .unwrap();
            store.accept(id, "first", "Save a fact").unwrap();
            let args = json!({"summary":id,"content":"shared fact"});
            let (tid, call) = contract::prepare(&mut store, id, "memory_remember", &args);
            barrier.wait();
            store.assistant_call(id, &tid, &call, &args).unwrap();
            store
        })
    });
    let [a, b] = handles;
    let mut a = a.join().unwrap();
    let _b = b.join().unwrap();
    let found = contract::call(&mut a, "coder", "memory_search", json!({"query":"shared"}));
    let found = found["memories"].as_array().unwrap();
    assert_eq!(found.len(), 2);
    assert_ne!(found[0]["revision"], found[1]["revision"]);
}

#[test]
#[ignore = "requires isolated OW_PG_TEST_URL"]
fn fenced_owner_cannot_mutate_memory() {
    let c = config();
    PostgresStore::migrate(&c).unwrap();
    let mut old = PostgresStore::open(c.clone()).unwrap();
    old.save(
        &contract::session("a", Some("learner")),
        "created",
        json!({}),
    )
    .unwrap();
    old.accept("a", "first", "Remember").unwrap();
    let args = json!({"summary":"fenced","content":"must not commit"});
    let (tid, call) = contract::prepare(&mut old, "a", "memory_remember", &args);
    let mut db = postgres::Client::connect(&c.connection, postgres::NoTls).unwrap();
    db.batch_execute(&format!("SET ow.runtime_schema='7'; SET search_path={},pg_catalog; UPDATE sessions SET expires=clock_timestamp()-interval '1 second' WHERE id='a'", c.schema)).unwrap();
    let mut next = PostgresStore::open(c).unwrap();
    next.claim("a").unwrap();
    assert!(old.assistant_call("a", &tid, &call, &args).is_err());
    assert_eq!(
        db.query_one("SELECT count(*) FROM assistant_memories", &[])
            .unwrap()
            .get::<_, i64>(0),
        0
    );
    next.assistant_call("a", &tid, &call, &args).unwrap();
}
