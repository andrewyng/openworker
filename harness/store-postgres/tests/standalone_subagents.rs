#[path = "../../tests/standalone_subagents_contract.rs"]
mod contract;
fn exercise(sync: bool) {
    let url = std::env::var("OW_PG_TEST_URL").expect("Provide a disposable local PostgreSQL URL");
    let schema = format!("ow_standalone_{}", uuid::Uuid::new_v4().simple());
    let mut config = openworker_store_postgres::Config::new(url.clone(), schema.clone());
    config.tls = openworker_store_postgres::Tls::LoopbackPlaintext;
    openworker_store_postgres::PostgresStore::migrate(&config).unwrap();
    let result = std::panic::catch_unwind(|| {
        let mut db = openworker_store_postgres::PostgresStore::open(config).unwrap();
        if sync {
            contract::sync_and_budget(&mut db);
        } else {
            contract::exercise(&mut db);
        }
    });
    postgres::Client::connect(&url, postgres::NoTls)
        .unwrap()
        .batch_execute(&format!("DROP SCHEMA {schema} CASCADE"))
        .unwrap();
    if let Err(panic) = result {
        std::panic::resume_unwind(panic)
    }
}
#[test]
#[ignore = "requires isolated OW_PG_TEST_URL"]
fn postgres_messages_lifecycle_and_receipts() {
    exercise(false);
}
#[test]
#[ignore = "requires isolated OW_PG_TEST_URL"]
fn postgres_sync_and_shared_budget() {
    exercise(true);
}
