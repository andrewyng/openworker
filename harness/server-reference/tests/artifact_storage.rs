//! Real API processes, PostgreSQL and configured storage. Fixture completed turn;
//! no model, worker or tool action is executed by this storage qualification.
#![cfg(unix)]
use futures_util::stream;
use openworker_artifacts::{hash, CHUNK};
use openworker_runtime::store::{Session, StateStore};
use openworker_server_reference::{ArtifactConfig, Config};
use openworker_store_postgres::{PostgresStore, TurnSubmission};
use serde_json::{json, Value};
use std::{path::Path, process::Stdio, time::Duration};
use tokio::{
    io::{AsyncBufReadExt, BufReader},
    process::{Child, Command},
};
use tokio_util::sync::CancellationToken;

async fn api(path: &Path, node: &str) -> (Child, String) {
    let mut child = Command::new(env!("CARGO_BIN_EXE_openworker-server-reference"))
        .arg("--config")
        .arg(path)
        .args(["api", "--node", node, "--listen", "127.0.0.1:0"])
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit())
        .kill_on_drop(true)
        .spawn()
        .unwrap();
    let mut line = String::new();
    tokio::time::timeout(
        Duration::from_secs(30),
        BufReader::new(child.stdout.take().unwrap()).read_line(&mut line),
    )
    .await
    .unwrap()
    .unwrap();
    let url = line.split_whitespace().last().unwrap().to_owned();
    assert!(url.starts_with("http://127.0.0.1:"), "{line}");
    (child, url)
}
async fn stop(child: &mut Child) {
    assert!(Command::new("kill")
        .args(["-TERM", &child.id().unwrap().to_string()])
        .status()
        .await
        .unwrap()
        .success());
    assert!(tokio::time::timeout(Duration::from_secs(30), child.wait())
        .await
        .unwrap()
        .unwrap()
        .success());
}
async fn metadata(response: reqwest::Response) -> Value {
    let status = response.status();
    let value: Value = response.json().await.unwrap();
    assert!(status.is_success(), "{status}: {value}");
    value
}
async fn download(client: &reqwest::Client, url: &str, token: &str, expected: &[u8]) {
    let response = client
        .get(url)
        .bearer_auth(token)
        .send()
        .await
        .unwrap()
        .error_for_status()
        .unwrap();
    assert_eq!(response.headers()["cache-control"], "no-store");
    assert_eq!(response.headers()["x-content-type-options"], "nosniff");
    let bytes = response.bytes().await.unwrap();
    assert_eq!(bytes.len(), expected.len());
    assert_eq!(hash(&bytes), hash(expected));
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
#[ignore = "requires disposable OW_PG_TEST_URL and optional OW_TEST_ARTIFACT_CONFIG S3 configuration"]
async fn streamed_artifacts_reconcile_and_survive_api_process_replacement() {
    tokio::time::timeout(Duration::from_secs(180), qualify())
        .await
        .unwrap();
}
async fn qualify() {
    let root = tempfile::tempdir().unwrap();
    let bytes: Vec<u8> = (0..CHUNK * 2 + 73).map(|n| (n % 251) as u8).collect();
    let token = uuid::Uuid::new_v4().simple().to_string().repeat(2);
    let object_root = root.path().join("objects");
    std::fs::create_dir(&object_root).unwrap();
    let artifacts: Value = std::env::var("OW_TEST_ARTIFACT_CONFIG").map(|s| serde_json::from_str(&s).unwrap())
        .unwrap_or_else(|_| json!({"kind":"filesystem","root":object_root,"deployment":"storage-test","shared_durable":true}));
    let artifact_config: ArtifactConfig = serde_json::from_value(artifacts.clone()).unwrap();
    let config = json!({
        "service":{"id":"storage-service","namespace":"storage-test","agent":"fixture","legacy_compatibility":false},
        "database_env":"OW_PG_TEST_URL","schema":format!("ow_storage_{}",uuid::Uuid::new_v4().simple()),
        "database_loopback_plaintext":true,"state_root":root.path().join("state"),
        "bundle":Path::new(env!("CARGO_MANIFEST_DIR")).join("../examples/assistant"),
        "runner":std::env::var("OW_PROBE_RUNNER").expect("Existing Linux runner required for profile validation; this test never executes it"),
        "model":"fixture","scripted":true,"api_token_file":root.path().join("unused-token"),
        "actor":"storage-test","openshell":{"identity":"unused-storage-fixture","endpoint":"https://127.0.0.1:1",
            "certificates":root.path(),"workspace":"default","image":format!("runner@sha256:{}","a".repeat(64))},
        "artifacts":artifacts
    });
    let path = root.path().join("config.json");
    std::fs::write(&path, serde_json::to_vec_pretty(&config).unwrap()).unwrap();
    let config: Config = serde_json::from_value(config).unwrap();
    let db = config.database().unwrap();
    PostgresStore::migrate(&db).unwrap();
    let store = PostgresStore::open(db.clone()).unwrap();
    store.auth_bootstrap(&token, "storage-test").unwrap();
    let (sid, turn) = tokio::task::spawn_blocking({let mut owner=store.clone();move || {
        let s:Session=serde_json::from_value(json!({"id":format!("s_{}",uuid::Uuid::new_v4().simple()),
            "name":"Artifact storage fixture","workspace":"/unused","coworker":"test","bundle_digest":"test",
            "schema_digest":null,"interactions":[],"turn":null,"sandbox":null,"closed":false,
            "resource_policy":openworker_runtime::resource_policy::Resolved::default()})).unwrap();
        owner.save(&s,"session_created",json!({})).unwrap();
        owner.submit_turn(TurnSubmission{
            limits: None,session_id:s.id.clone(),submission_id:"storage-turn".into(),text:"fixture completed output".into()}).unwrap();
        let (mut active,_,_)=owner.accept(&s.id,"storage-turn","fixture completed output").unwrap();
        let turn=active.turn.as_ref().unwrap().id.clone();
        active.turn.as_mut().unwrap().status="completed".into();
        owner.save(&active,"turn_completed",json!({"status":"completed"})).unwrap();
        (s.id,turn)
    }}).await.unwrap();
    let mut headers = reqwest::header::HeaderMap::new();
    headers.insert("x-openworker-namespace", "storage-test".parse().unwrap());
    let client = reqwest::Client::builder()
        .default_headers(headers)
        .timeout(Duration::from_secs(30))
        .build()
        .unwrap();
    let (mut a, base_a) = api(&path, "storage-a").await;
    let (mut b, base_b) = api(&path, "storage-b").await;
    assert_eq!(
        client
            .get(format!("{base_a}/sessions/{sid}/artifacts"))
            .bearer_auth(&token)
            .send()
            .await
            .unwrap()
            .status(),
        404
    );
    assert_eq!(
        client
            .get(format!("{base_a}/v1/sessions/{sid}/artifacts"))
            .bearer_auth(&token)
            .header("x-openworker-namespace", "wrong-namespace")
            .send()
            .await
            .unwrap()
            .status(),
        403
    );
    let registration = json!({"request_id":"storage-upload","turn_id":turn,"name":"proof.bin",
        "content_type":"application/octet-stream","size":bytes.len(),"sha256":hash(&bytes)});
    let registered = metadata(
        client
            .post(format!("{base_a}/v1/sessions/{sid}/artifacts"))
            .bearer_auth(&token)
            .json(&registration)
            .send()
            .await
            .unwrap(),
    )
    .await;
    let id = registered["id"].as_str().unwrap();
    let route = format!("/v1/sessions/{sid}/artifacts/{id}/content");
    assert_eq!(
        client
            .get(format!("{base_b}{route}"))
            .bearer_auth(&token)
            .send()
            .await
            .unwrap()
            .status(),
        409
    );
    // EOF before the expected size leaves immutable parts, but cannot publish ready.
    let partial = stream::iter([Ok::<_, std::io::Error>(bytes[..CHUNK + 23].to_vec())]);
    assert_eq!(
        client
            .put(format!("{base_a}{route}"))
            .bearer_auth(&token)
            .body(reqwest::Body::wrap_stream(partial))
            .send()
            .await
            .unwrap()
            .status(),
        409
    );
    assert_eq!(
        client
            .get(format!("{base_b}{route}"))
            .bearer_auth(&token)
            .send()
            .await
            .unwrap()
            .status(),
        409
    );
    let retry = metadata(
        client
            .post(format!("{base_b}/v1/sessions/{sid}/artifacts"))
            .bearer_auth(&token)
            .json(&registration)
            .send()
            .await
            .unwrap(),
    )
    .await;
    assert_eq!(retry["id"], registered["id"]);
    let ready = metadata(
        client
            .put(format!("{base_b}{route}"))
            .bearer_auth(&token)
            .body(bytes.clone())
            .send()
            .await
            .unwrap(),
    )
    .await;
    assert_eq!(ready["status"], "ready");
    download(&client, &format!("{base_a}{route}"), &token, &bytes).await;
    assert_eq!(
        client
            .get(format!("{base_a}{route}"))
            .send()
            .await
            .unwrap()
            .status(),
        401
    );
    // Complete object bytes without the PG ready transaction model response loss.
    let mut lost_registration = registration.clone();
    lost_registration["request_id"] = json!("lost-ready-commit");
    let lost = metadata(
        client
            .post(format!("{base_a}/v1/sessions/{sid}/artifacts"))
            .bearer_auth(&token)
            .json(&lost_registration)
            .send()
            .await
            .unwrap(),
    )
    .await;
    let lost_id = lost["id"].as_str().unwrap();
    let catalog = store.artifact(&sid, lost_id).unwrap();
    artifact_config
        .build()
        .unwrap()
        .put(&catalog, bytes.as_slice(), &CancellationToken::new())
        .await
        .unwrap();
    assert_eq!(
        client
            .get(format!(
                "{base_b}/v1/sessions/{sid}/artifacts/{lost_id}/content"
            ))
            .bearer_auth(&token)
            .send()
            .await
            .unwrap()
            .status(),
        409
    );
    let reconciled = metadata(
        client
            .post(format!(
                "{base_b}/v1/sessions/{sid}/artifacts/{lost_id}/reconcile"
            ))
            .bearer_auth(&token)
            .send()
            .await
            .unwrap(),
    )
    .await;
    assert_eq!(reconciled["status"], "ready");
    stop(&mut a).await;
    stop(&mut b).await;
    let (mut replacement, base_c) = api(&path, "storage-replacement").await;
    download(&client, &format!("{base_c}{route}"), &token, &bytes).await;
    download(
        &client,
        &format!("{base_c}/v1/sessions/{sid}/artifacts/{lost_id}/content"),
        &token,
        &bytes,
    )
    .await;
    let replay = metadata(
        client
            .post(format!("{base_c}/v1/sessions/{sid}/artifacts"))
            .bearer_auth(&token)
            .json(&registration)
            .send()
            .await
            .unwrap(),
    )
    .await;
    assert_eq!(replay, ready);
    stop(&mut replacement).await;
    assert_eq!(
        store
            .read_events(&sid, 0)
            .unwrap()
            .iter()
            .filter(|e| e.kind == "artifact_ready")
            .count(),
        2
    );
    tokio::task::spawn_blocking(move || {
        postgres::Client::connect(&db.connection, postgres::NoTls)
            .unwrap()
            .batch_execute(&format!("DROP SCHEMA {} CASCADE", db.schema))
            .unwrap();
    })
    .await
    .unwrap();
    eprintln!("PASS artifact storage: truncated upload, immutable retry, lost-ready reconciliation, authenticated cross-API retrieval and process replacement; no tool replay");
}
