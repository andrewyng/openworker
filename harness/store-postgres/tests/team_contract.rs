#[path = "../../tests/team_domain_contract.rs"]
mod contract;
#[test]
#[ignore = "requires isolated OW_PG_TEST_URL"]
fn postgres_shared_team_domain_contract() {
    let mut config = openworker_store_postgres::Config::new(
        std::env::var("OW_PG_TEST_URL").unwrap(),
        format!("ow_contract_{}", uuid::Uuid::new_v4().simple()),
    );
    config.tls = openworker_store_postgres::Tls::LoopbackPlaintext;
    openworker_store_postgres::PostgresStore::migrate(&config).unwrap();
    let mut store = openworker_store_postgres::PostgresStore::open(config).unwrap();
    contract::exercise(&mut store);
}
