use openworker_store_postgres::{Config, PostgresStore, Tls};

#[test]
#[ignore = "requires disposable OW_PG_TEST_URL"]
fn persisted_binding_cannot_change_and_restore_verification_does_not_write() {
    let mut config = Config::new(
        std::env::var("OW_PG_TEST_URL").unwrap(),
        format!("ow_svc_id_{}", uuid::Uuid::new_v4().simple()),
    );
    config.tls = Tls::LoopbackPlaintext;
    PostgresStore::migrate(&config).unwrap();
    let store = PostgresStore::open(config.clone()).unwrap();
    assert!(store.verify_service("cluster", "namespace").is_err());
    store.bind_service("cluster", "namespace").unwrap();
    store.bind_service("cluster", "namespace").unwrap();
    assert!(store.bind_service("other", "namespace").is_err());
    assert!(store.bind_service("cluster", "other").is_err());
    let mut db = postgres::Client::connect(&config.connection, postgres::NoTls).unwrap();
    db.batch_execute(&format!("SET search_path={},pg_catalog", config.schema))
        .unwrap();
    db.batch_execute("CREATE FUNCTION reject_identity_writes() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'read-only fixture'; END $$; CREATE TRIGGER reject_identity_writes BEFORE INSERT OR UPDATE ON service_identity FOR EACH ROW EXECUTE FUNCTION reject_identity_writes()").unwrap();
    assert!(store.bind_service("cluster", "namespace").is_err());
    // A database error poisons the connection by design; restoration opens a
    // fresh connection, then verifies identity using reads only.
    drop(store);
    let store = PostgresStore::open(config.clone()).unwrap();
    store.verify_service("cluster", "namespace").unwrap();
    assert!(store.verify_service("cluster", "wrong").is_err());
    drop(store);
    db.batch_execute(&format!("DROP SCHEMA {} CASCADE", config.schema))
        .unwrap();
}
