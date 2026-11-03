#![cfg(feature = "compliance")]
use openworker_runtime::{
    compliance::Query,
    store::{Session, StateStore},
};
use openworker_store_postgres::{
    auth::{Role, Scope, Spec},
    Config, PostgresStore, Tls,
};
use serde_json::json;
const ADMIN: &str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
const READER: &str = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
const AUDITOR: &str = "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc";
const OTHER: &str = "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd";
fn session(id: &str) -> Session {
    serde_json::from_value(json!({"id":id,"name":"Test","workspace":"/work","coworker":"test","bundle_digest":"b","schema_digest":null,"interactions":[],"turn":null,"sandbox":null,"closed":false,"compliance":{},"resource_policy":openworker_runtime::resource_policy::Resolved::default()})).unwrap()
}
#[test]
#[ignore = "requires isolated OW_PG_TEST_URL"]
fn durable_projection_content_scope_and_read_audit() {
    let mut c = Config::new(
        std::env::var("OW_PG_TEST_URL").unwrap(),
        format!("ow_compliance_{}", uuid::Uuid::new_v4().simple()),
    );
    c.tls = Tls::LoopbackPlaintext;
    PostgresStore::migrate(&c).unwrap();
    PostgresStore::migrate(&c).unwrap();
    let mut root = PostgresStore::open(c.clone()).unwrap();
    root.auth_bootstrap(ADMIN, "admin").unwrap();
    let admin = root.authorized(ADMIN).unwrap();
    let mut s = session("s_one");
    s.history
        .push(json!({"role":"user","content":"PRIVATE_ACCEPTED_TEXT"}));
    s.history
        .push(json!({"type":"reasoning","encrypted_content":"OPAQUE_REASONING"}));
    root.save(
        &s,
        "session_created",
        json!({"text":"PRIVATE_ACCEPTED_TEXT"}),
    )
    .unwrap();
    root.save(&session("s_two"), "session_created", json!({}))
        .unwrap();
    for (i, (key, role, scope)) in [
        (
            READER,
            Role::ComplianceReader,
            Scope::Sessions {
                ids: vec!["s_one".into()],
            },
        ),
        (
            AUDITOR,
            Role::ComplianceAuditor,
            Scope::Sessions {
                ids: vec!["s_one".into()],
            },
        ),
        (OTHER, Role::Observer, Scope::All),
    ]
    .into_iter()
    .enumerate()
    {
        admin
            .auth_issue(
                &format!("credential-{i}"),
                Spec {
                    principal: format!("principal-{i}"),
                    role,
                    scope,
                    environments: vec![],
                    expires_at: None,
                },
                key,
            )
            .unwrap();
    }
    let e=root.append_event("s_one","safety_evaluation",json!({"phase":"model_output","outcome":"blocked","code":"PII","text":"PRIVATE_REJECTED_TEXT"})).unwrap();
    root.append_event("s_one", "assistant_delta", json!({"text":"PRIVATE_DELTA"}))
        .unwrap();
    let reader = root.authorized(READER).unwrap();
    let auditor = root.authorized(AUDITOR).unwrap();
    let observer = root.authorized(OTHER).unwrap();
    let page = reader.compliance_page("s_one", Query::default()).unwrap();
    assert_eq!(page["activities"].as_array().unwrap().len(), 2);
    assert!(!page.to_string().contains("PRIVATE"));
    assert!(!page.to_string().contains("OPAQUE"));
    assert_eq!(page["nextAfter"], e.event_id);
    assert!(reader.compliance_page("s_two", Query::default()).is_err());
    assert!(reader.compliance_content("s_one", 0).is_err());
    assert!(reader.read_snapshot("s_one").is_err());
    assert!(observer.compliance_page("s_one", Query::default()).is_err());
    let content = auditor.compliance_content("s_one", 0).unwrap();
    assert!(content.to_string().contains("PRIVATE_ACCEPTED_TEXT"));
    assert!(!content.to_string().contains("PRIVATE_REJECTED_TEXT"));
    assert!(!content.to_string().contains("OPAQUE"));
    let mut db = postgres::Client::connect(&c.connection, postgres::NoTls).unwrap();
    db.batch_execute(&format!("SET search_path={},pg_catalog", c.schema))
        .unwrap();
    let reads:i64=db.query_one("SELECT count(*) FROM api_security_audit WHERE action IN ('compliance.read','compliance.content.read')",&[]).unwrap().get(0);
    assert_eq!(reads, 2);
    db.batch_execute("CREATE FUNCTION reject_evidence() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'audit storage failed'; END $$; CREATE TRIGGER reject_evidence BEFORE INSERT ON compliance_events FOR EACH ROW EXECUTE FUNCTION reject_evidence();").unwrap();
    assert!(root
        .append_event("s_one", "tool_started", json!({"call_id":"call-1"}))
        .is_err());
    assert_eq!(
        db.query_one(
            "SELECT count(*) FROM events WHERE session_id='s_one' AND kind='tool_started'",
            &[]
        )
        .unwrap()
        .get::<_, i64>(0),
        0
    );
    db.batch_execute("DROP TRIGGER reject_evidence ON compliance_events")
        .unwrap();
    let reopened = PostgresStore::open(c.clone())
        .unwrap()
        .authorized(READER)
        .unwrap();
    assert_eq!(
        reopened.compliance_page("s_one", Query::default()).unwrap()["activities"],
        page["activities"]
    );
    let admin = PostgresStore::open(c.clone())
        .unwrap()
        .authorized(ADMIN)
        .unwrap();
    let mut exporter = admin.clone();
    let first = exporter
        .claim_compliance_export("s_one", "siem-v1", "binding", "one")
        .unwrap()
        .unwrap();
    assert!(exporter
        .claim_compliance_export("s_one", "siem-v1", "binding", "two")
        .unwrap()
        .is_none());
    exporter
        .settle_compliance_export("s_one", "siem-v1", "one", false)
        .unwrap();
    assert_eq!(
        admin.compliance_delivery_health("s_one").unwrap()["sinks"][0]["cursor"],
        0
    );
    db.execute(
        "UPDATE compliance_delivery SET data=jsonb_set(data,'{nextAtMs}','0')",
        &[],
    )
    .unwrap();
    let mut reopened = PostgresStore::open(c.clone())
        .unwrap()
        .authorized(ADMIN)
        .unwrap();
    let retry = reopened
        .claim_compliance_export("s_one", "siem-v1", "binding", "retry")
        .unwrap()
        .unwrap();
    assert_eq!(retry.activities, first.activities);
    assert!(reopened
        .settle_compliance_export("s_one", "siem-v1", "one", true)
        .is_err());
    reopened
        .settle_compliance_export("s_one", "siem-v1", "retry", true)
        .unwrap();
    assert_eq!(
        admin.compliance_delivery_health("s_one").unwrap()["sinks"][0]["pending"],
        0
    );
    let mut reader = PostgresStore::open(c.clone())
        .unwrap()
        .authorized(READER)
        .unwrap();
    assert!(reader
        .claim_compliance_export("s_one", "unapproved", "binding", "forged")
        .is_err());
    db.batch_execute(&format!("DROP SCHEMA {} CASCADE", c.schema))
        .unwrap();
}

#[test]
#[ignore = "requires isolated OW_PG_TEST_URL"]
fn schema_fence_blocks_old_clients_and_preserves_existing_records() {
    let mut c = Config::new(
        std::env::var("OW_PG_TEST_URL").unwrap(),
        format!("ow_cupgrade_{}", uuid::Uuid::new_v4().simple()),
    );
    c.tls = Tls::LoopbackPlaintext;
    PostgresStore::migrate(&c).unwrap();
    let mut store = PostgresStore::open(c.clone()).unwrap();
    store
        .save(&session("upgrade"), "session_created", json!({}))
        .unwrap();
    let mut db = postgres::Client::connect(&c.connection, postgres::NoTls).unwrap();
    db.batch_execute(&format!("SET search_path={},pg_catalog", c.schema))
        .unwrap();
    assert!(db
        .execute("UPDATE sessions SET data=data WHERE id='upgrade'", &[])
        .is_err());
    assert!(db.execute("INSERT INTO events(session_id,cursor,kind,data) VALUES('upgrade',99,'tool_started','{}')",&[]).is_err());
    db.batch_execute("UPDATE version SET version=5 WHERE singleton")
        .unwrap();
    assert!(
        PostgresStore::migrate(&c).is_err(),
        "Do not upgrade under a live owner"
    );
    db.batch_execute("UPDATE version SET version=6 WHERE singleton")
        .unwrap();
    // Emulate a drained old schema without altering its session payload.
    store.release("upgrade").unwrap();
    drop(store);
    db.batch_execute("UPDATE version SET version=5 WHERE singleton")
        .unwrap();
    PostgresStore::migrate(&c).unwrap();
    assert_eq!(
        db.query_one("SELECT version FROM version", &[])
            .unwrap()
            .get::<_, i32>(0),
        6
    );
    let store = PostgresStore::open(c.clone()).unwrap();
    assert_eq!(
        store.read_snapshot("upgrade").unwrap()["session"]["id"],
        "upgrade"
    );
    db.batch_execute(&format!("DROP SCHEMA {} CASCADE", c.schema))
        .unwrap();
}

#[test]
#[ignore = "requires isolated OW_PG_TEST_URL"]
fn export_backpressure_leaves_the_connection_available_for_delivery() {
    let mut c = Config::new(
        std::env::var("OW_PG_TEST_URL").unwrap(),
        format!("ow_cpressure_{}", uuid::Uuid::new_v4().simple()),
    );
    c.tls = Tls::LoopbackPlaintext;
    PostgresStore::migrate(&c).unwrap();
    let mut store = PostgresStore::open(c.clone()).unwrap();
    let mut s = session("pressure");
    s.compliance.as_mut().unwrap().max_pending_events = 100;
    store.save(&s, "session_created", json!({})).unwrap();
    let first = store
        .claim_compliance_export(&s.id, "sink", "binding", "first")
        .unwrap()
        .unwrap();
    store
        .settle_compliance_export(&s.id, "sink", &first.token, false)
        .unwrap();
    for _ in 0..99 {
        store
            .append_event(&s.id, "tool_finished", json!({}))
            .unwrap();
    }
    assert!(store
        .append_event(&s.id, "tool_started", json!({}))
        .is_err());
    store
        .append_event(&s.id, "turn_completed", json!({"status":"cancelled"}))
        .unwrap();
    let mut db = postgres::Client::connect(&c.connection, postgres::NoTls).unwrap();
    db.batch_execute(&format!("SET search_path={},pg_catalog; UPDATE compliance_delivery SET data=jsonb_set(data,'{{nextAtMs}}','0'::jsonb)",c.schema)).unwrap();
    let retry = store
        .claim_compliance_export(&s.id, "sink", "binding", "retry")
        .unwrap()
        .unwrap();
    store
        .settle_compliance_export(&s.id, "sink", &retry.token, true)
        .unwrap();
    store
        .append_event(&s.id, "tool_started", json!({}))
        .unwrap();
    db.batch_execute(&format!("DROP SCHEMA {} CASCADE", c.schema))
        .unwrap();
}

#[test]
#[cfg(feature = "guardrails")]
#[ignore = "requires isolated OW_PG_TEST_URL"]
fn observer_receipts_survive_reconnect_and_reject_stale_owners() {
    use openworker_runtime::safety::{HandlerRef, Hook, HookPhase};
    let mut c = Config::new(
        std::env::var("OW_PG_TEST_URL").unwrap(),
        format!("ow_cobserve_{}", uuid::Uuid::new_v4().simple()),
    );
    c.tls = Tls::LoopbackPlaintext;
    PostgresStore::migrate(&c).unwrap();
    let mut owner = PostgresStore::open(c.clone()).unwrap();
    let mut s = session("observed");
    s.safety.hooks.push(Hook::new(
        "audit",
        HookPhase::OnEvent,
        HandlerRef {
            name: "audit".into(),
            version: "1".into(),
        },
    ));
    owner
        .save(&s, "session_created", json!({"text":"HIDDEN_SENTINEL"}))
        .unwrap();
    let receipt = owner.pending_observers(&s.id).unwrap().remove(0);
    assert!(!receipt.to_string().contains("HIDDEN_SENTINEL"));
    owner.settle_observer(&s.id, &receipt, false).unwrap();
    owner.release(&s.id).unwrap();
    let mut replacement = PostgresStore::open(c.clone()).unwrap();
    replacement.claim(&s.id).unwrap();
    assert!(owner.settle_observer(&s.id, &receipt, true).is_err());
    let resumed = replacement
        .pending_observers(&s.id)
        .unwrap()
        .into_iter()
        .find(|v| v["cursor"] == receipt["cursor"])
        .unwrap();
    assert_eq!(resumed["attempts"], 1);
    assert_eq!(resumed["hook"], receipt["hook"]);
    replacement.settle_observer(&s.id, &resumed, true).unwrap();
    assert!(!replacement
        .pending_observers(&s.id)
        .unwrap()
        .iter()
        .any(|v| v["cursor"] == receipt["cursor"]));
    let mut db = postgres::Client::connect(&c.connection, postgres::NoTls).unwrap();
    db.batch_execute(&format!("DROP SCHEMA {} CASCADE", c.schema))
        .unwrap();
}
