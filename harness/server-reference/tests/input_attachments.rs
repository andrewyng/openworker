//! Real PostgreSQL + two authenticated HTTP API replicas, no cloud or model keys.
#![cfg(all(feature = "attachments", feature = "guardrails", not(windows)))]
use base64::{engine::general_purpose::STANDARD, Engine};
use openworker_runtime::{
    attachments::{Metadata, Part, Upload},
    execution::Backend,
    model::Model,
    runtime::Runtime,
    store::{Session, StateStore},
    turn_options::Context,
};
use openworker_server_reference::{http::App, Config};
use openworker_store_postgres::{
    auth::{Role, Scope, Spec},
    PostgresStore,
};
use serde_json::{json, Value};
use tokio_util::sync::CancellationToken;
const ADMIN: &str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
const ALICE: &str = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
const BOB: &str = "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc";
const READER: &str = "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd";
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
#[ignore = "requires disposable OW_PG_TEST_URL"]
async fn uploads_cross_api_replicas_are_scoped_and_recoverable() {
    let root = tempfile::tempdir().unwrap();
    let id = uuid::Uuid::new_v4().simple().to_string();
    let key = format!("OW_ATTACH_DB_{}", id.to_uppercase());
    std::env::set_var(&key, std::env::var("OW_PG_TEST_URL").unwrap());
    let config:Config=serde_json::from_value(json!({"database_env":key,"schema":format!("ow_att_{}",&id[..20]),"database_loopback_plaintext":true,"state_root":root.path().join("state"),"bundle":"unused","runner":"unused","model":"fixture","scripted":true,"api_token_file":"unused","actor":"admin","openshell":{"identity":"fixture","endpoint":"https://127.0.0.1:1","certificates":"unused","workspace":"unused","image":"unused"},"service":{"id":"attachment-test","namespace":"test","agent":"test","legacy_compatibility":true}})).unwrap();
    let db = config.database().unwrap();
    PostgresStore::migrate(&db).unwrap();
    let mut store = PostgresStore::open(db.clone()).unwrap();
    store.auth_bootstrap(ADMIN, "admin").unwrap();
    let admin = store.authorized(ADMIN).unwrap();
    for sid in ["s_one", "s_two", "s_three"] {
        let mut session:Session=serde_json::from_value(json!({"id":sid,"name":"Input","workspace":"/work","coworker":"test","bundle_digest":"bundle","schema_digest":null,"interactions":[],"turn":null,"sandbox":null,"closed":false,"resource_policy":openworker_runtime::resource_policy::Resolved::default()})).unwrap();
        if sid == "s_two" {
            session
                .safety
                .guardrails
                .user_input
                .push(openworker_runtime::safety::Rule::regex(
                    "secret",
                    "SECRET_",
                    openworker_runtime::safety::Action::Block,
                ));
        }
        if sid == "s_three" {
            session
                .safety
                .guardrails
                .user_input
                .push(openworker_runtime::safety::Rule::regex(
                    "redact-secret",
                    "SECRET_[0-9]+",
                    openworker_runtime::safety::Action::Redact {
                        replacement: "[redacted]".into(),
                        keep_last: 0,
                    },
                ));
        }
        store.save(&session, "session_created", json!({})).unwrap();
    }
    for (token, principal, sid, role) in [
        (ALICE, "alice", "s_one", Role::Operator),
        (BOB, "bob", "s_two", Role::Operator),
        (READER, "reader", "s_one", Role::Observer),
    ] {
        admin
            .auth_issue(
                principal,
                Spec {
                    principal: principal.into(),
                    role,
                    scope: Scope::Sessions {
                        ids: vec![sid.into()],
                    },
                    environments: vec![],
                    expires_at: None,
                },
                token,
            )
            .unwrap();
    }
    let bundle = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../examples/assistant");
    let runner = std::env::current_exe().unwrap();
    let runtime = Runtime::open_with_store(
        &root.path().join("runtime"),
        &bundle,
        &runner,
        Model::new("fixture".into(), true),
        Backend::Off,
        None,
        None,
        Default::default(),
        // An API replica does not own the worker's session leases. Sharing the
        // worker store here concealed admission's accidental lease dependency.
        Some(Box::new(PostgresStore::open(db.clone()).unwrap())),
    )
    .unwrap();
    let mut urls = vec![];
    let mut servers = vec![];
    for _ in 0..2 {
        let app = App::new(
            &config,
            runtime.clone(),
            store.clone(),
            CancellationToken::new(),
        )
        .unwrap();
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        urls.push(format!("http://{}", listener.local_addr().unwrap()));
        servers.push(tokio::spawn(async move {
            axum::serve(listener, app.router()).await.unwrap();
        }));
    }
    let client = reqwest::Client::builder().no_proxy().build().unwrap();
    let post = |node: usize, token: &'static str, path: String, body: Value, idem: &'static str| {
        let url = format!("{}{path}", urls[node]);
        client
            .post(url)
            .header("x-openworker-namespace", "test")
            .header("idempotency-key", idem)
            .bearer_auth(token)
            .json(&body)
            .send()
    };
    let data = vec![65u8; 600000];
    let m = Metadata {
        id: "notes".into(),
        name: "notes.txt".into(),
        media_type: "text/plain".into(),
        size: data.len(),
        sha256: openworker_runtime::bundle::digest(&data),
    };
    let path = "/v1/sessions/s_one/attachments/notes";
    let begin = json!({"operation":"begin","metadata":m});
    for token in [BOB, READER] {
        assert_eq!(
            post(0, token, path.into(), begin.clone(), "unused")
                .await
                .unwrap()
                .status(),
            403
        );
    }
    assert_eq!(
        post(0, ALICE, path.into(), begin.clone(), "unused")
            .await
            .unwrap()
            .status(),
        200
    );
    for (i, chunk) in data.chunks(262144).enumerate() {
        let body = json!({"operation":"chunk","offset":i*262144,"data":STANDARD.encode(chunk)});
        for _ in 0..2 {
            assert_eq!(
                post(i % 2, ALICE, path.into(), body.clone(), "unused")
                    .await
                    .unwrap()
                    .status(),
                200
            );
        }
    }
    assert_eq!(
        post(
            1,
            ALICE,
            path.into(),
            json!({"operation":"commit"}),
            "unused"
        )
        .await
        .unwrap()
        .status(),
        200
    );
    let receipt: Value = post(0, ALICE, path.into(), json!({"operation":"stat"}), "unused")
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    assert_eq!(receipt["sealed"], true);
    assert!(!receipt.to_string().contains(&STANDARD.encode(&data)));
    let parts = vec![
        Part::Text {
            text: "Before".into(),
        },
        Part::Document {
            attachment_id: "notes".into(),
        },
        Part::Text {
            text: "After".into(),
        },
    ];
    let body = json!({"content":parts});
    let turn_path = "/v1/sessions/s_one/turns";
    let response = post(0, ALICE, turn_path.into(), body.clone(), "ordered")
        .await
        .unwrap();
    let status = response.status();
    let receipt: Value = response.json().await.unwrap();
    assert_eq!(status, 202, "{receipt}");
    let submission = receipt["submissionId"].as_str().unwrap();
    let replay = post(1, ALICE, turn_path.into(), body.clone(), "ordered")
        .await
        .unwrap();
    assert_eq!(replay.status(), 202);
    assert_eq!(
        store.submission_input("s_one", submission).unwrap(),
        Some(parts.clone())
    );
    let changed = post(
        1,
        ALICE,
        turn_path.into(),
        json!({"content":[parts[1].clone(),parts[0].clone()]}),
        "ordered",
    )
    .await
    .unwrap();
    assert_eq!(changed.status(), 409);
    let next = store.next_turn("s_one").unwrap().unwrap();
    let (s, _, _) = store
        .accept_with_context(
            "s_one",
            &next.submission_id,
            &next.text,
            next.limits,
            None,
            Context {
                content: Some(parts),
                ..Default::default()
            },
        )
        .unwrap();
    assert_eq!(s.history[0]["content"][1]["id"], "notes");
    let mut reopened = PostgresStore::open(db.clone()).unwrap();
    let hydrated = openworker_runtime::attachments::hydrate(
        &mut reopened,
        "s_one",
        &store.get("s_one").unwrap().history,
    )
    .unwrap();
    assert_eq!(hydrated[0]["content"][0]["text"], "Before");
    assert!(hydrated[0]["content"][1]["text"]
        .as_str()
        .unwrap()
        .ends_with(&"A".repeat(data.len())));
    // No bypass through a reference: binary input policies fail closed on ingress.
    let pdf = b"%PDF-1.4\n%%EOF";
    let meta = Metadata {
        id: "pdf".into(),
        name: "a.pdf".into(),
        media_type: "application/pdf".into(),
        size: pdf.len(),
        sha256: openworker_runtime::bundle::digest(pdf),
    };
    for upload in [
        Upload::Begin { metadata: meta },
        Upload::Chunk {
            offset: 0,
            data: STANDARD.encode(pdf),
        },
        Upload::Commit,
    ] {
        store.input_attachment("s_two", "pdf", upload).unwrap();
    }
    let denied = post(
        0,
        BOB,
        "/v1/sessions/s_two/turns".into(),
        json!({"content":[{"type":"document","attachment_id":"pdf"}]}),
        "binary",
    )
    .await
    .unwrap();
    assert_eq!(denied.status(), 422);
    assert_eq!(
        denied.json::<Value>().await.unwrap()["error"],
        "attachment_inspection_unsupported"
    );
    assert!(store
        .authorized(BOB)
        .unwrap()
        .input_attachment("s_one", "notes", Upload::Stat)
        .is_err());
    // Guardrails still inspect documents and produce immutable replacement
    // references without acquiring or changing the worker's session lease.
    let original = b"Document includes SECRET_12345.";
    let metadata = Metadata {
        id: "original".into(),
        name: "secret.txt".into(),
        media_type: "text/plain".into(),
        size: original.len(),
        sha256: openworker_runtime::bundle::digest(original),
    };
    for upload in [
        Upload::Begin { metadata },
        Upload::Chunk {
            offset: 0,
            data: STANDARD.encode(original),
        },
        Upload::Commit,
    ] {
        store
            .input_attachment("s_three", "original", upload)
            .unwrap();
    }
    let redacted = post(
        1,
        ADMIN,
        "/v1/sessions/s_three/turns".into(),
        json!({"content":[{"type":"text","text":""},{"type":"document","attachment_id":"original"}]}),
        "redaction",
    )
    .await
    .unwrap();
    let status = redacted.status();
    let receipt: Value = redacted.json().await.unwrap();
    assert_eq!(status, 202, "{receipt}");
    let input = store
        .submission_input("s_three", receipt["submissionId"].as_str().unwrap())
        .unwrap()
        .unwrap();
    assert_eq!(
        input[0],
        Part::Text {
            text: String::new()
        }
    );
    let Part::Document { attachment_id } = &input[1] else {
        panic!("Document ordering changed")
    };
    assert_ne!(attachment_id, "original");
    let blob = admin
        .clone()
        .input_attachment("s_three", attachment_id, Upload::Stat)
        .unwrap();
    assert_eq!(blob.bytes, b"Document includes [redacted].");
    assert_eq!(
        store
            .input_attachment("s_three", "original", Upload::Stat)
            .unwrap()
            .bytes,
        original
    );
    assert!(
        store.get("s_three").is_ok(),
        "API inspection must not steal the worker lease"
    );
    for server in servers {
        server.abort();
        let _ = server.await;
    }
    runtime.shutdown().await.unwrap();
    drop(runtime);
    drop(reopened);
    drop(admin);
    drop(store);
    tokio::task::spawn_blocking(move || {
        let mut sql = postgres::Client::connect(&db.connection, postgres::NoTls).unwrap();
        sql.batch_execute(&format!(
            "SET ow.runtime_schema='6'; SET search_path={},pg_catalog",
            db.schema
        ))
        .unwrap();
        assert!(
            sql.execute(
                "UPDATE submissions SET text=text WHERE session_id='s_one'",
                &[]
            )
            .is_err(),
            "An older worker must not consume an attachment submission"
        );
        sql.batch_execute(&format!("DROP SCHEMA {} CASCADE", db.schema))
            .unwrap();
    })
    .await
    .unwrap();
}
