//! Real processes, PostgreSQL and remote OpenShell; scripted model, real tools.
#![cfg(all(feature = "openshell-test", unix))]
use futures_util::StreamExt;
use openworker_runtime::environment::{Management, Provider, Sharing};
use openworker_server_reference::Config;
use openworker_store_postgres::PostgresStore;
use serde_json::{json, Value};
use std::{path::Path, process::Stdio, time::Duration};
use tokio::{
    io::{AsyncBufReadExt, BufReader},
    process::{Child, Command},
};
const BIN: &str = env!("CARGO_BIN_EXE_openworker-server-reference");
async fn api(config: &Path, node: &str) -> (Child, String) {
    let mut child = Command::new(BIN)
        .arg("--config")
        .arg(config)
        .args(["api", "--node", node, "--listen", "127.0.0.1:0"])
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit())
        .kill_on_drop(true)
        .spawn()
        .unwrap();
    let mut reader = BufReader::new(child.stdout.take().unwrap());
    let mut line = String::new();
    tokio::time::timeout(Duration::from_secs(30), reader.read_line(&mut line))
        .await
        .unwrap()
        .unwrap();
    let url = line.split_whitespace().last().unwrap().to_owned();
    assert!(url.starts_with("http://127.0.0.1:"), "{line}");
    (child, url)
}
fn worker(config: &Path, node: &str) -> Child {
    Command::new(BIN)
        .arg("--config")
        .arg(config)
        .args(["worker", "--node", node, "--slots", "1"])
        .stdout(Stdio::null())
        .stderr(Stdio::inherit())
        .kill_on_drop(true)
        .spawn()
        .unwrap()
}
async fn stop(child: &mut Child) {
    assert!(child.try_wait().unwrap().is_none());
    assert!(Command::new("kill")
        .args(["-TERM", &child.id().unwrap().to_string()])
        .status()
        .await
        .unwrap()
        .success());
    assert!(tokio::time::timeout(Duration::from_secs(50), child.wait())
        .await
        .unwrap()
        .unwrap()
        .success());
}
async fn post(client: &reqwest::Client, base: &str, token: &str, path: &str, body: Value) -> Value {
    let response = client
        .post(format!("{base}{path}"))
        .bearer_auth(token)
        .json(&body)
        .send()
        .await
        .unwrap();
    let status = response.status();
    let value = response.json::<Value>().await.unwrap();
    assert!(status.is_success(), "{status}: {value}");
    value
}
async fn snapshot(client: &reqwest::Client, base: &str, token: &str, sid: &str) -> Value {
    client
        .get(format!("{base}/sessions/{sid}"))
        .bearer_auth(token)
        .send()
        .await
        .unwrap()
        .error_for_status()
        .unwrap()
        .json()
        .await
        .unwrap()
}
async fn wait_for(
    client: &reqwest::Client,
    base: &str,
    token: &str,
    sid: &str,
    state: &str,
) -> Value {
    tokio::time::timeout(Duration::from_secs(45), async {
        loop {
            let s = snapshot(client, base, token, sid).await;
            if s["turn"]["status"] == state {
                return s;
            }
            assert!(
                !matches!(s["turn"]["status"].as_str(), Some("failed" | "truncated")),
                "{s}"
            );
            tokio::time::sleep(Duration::from_millis(100)).await;
        }
    })
    .await
    .unwrap()
}
fn plan(tool: &str, args: Value) -> String {
    format!(
        "TEST_RESOURCE_GRANTS {}",
        json!([{"name":tool,"arguments":args}])
    )
}
async fn download(
    client: &reqwest::Client,
    base: &str,
    token: &str,
    sid: &str,
    id: &str,
    bytes: &[u8],
) {
    let r = client
        .get(format!("{base}/sessions/{sid}/artifacts/{id}/content"))
        .bearer_auth(token)
        .send()
        .await
        .unwrap()
        .error_for_status()
        .unwrap();
    assert_eq!(r.headers()["x-content-type-options"], "nosniff");
    assert!(r.headers()["content-disposition"]
        .to_str()
        .unwrap()
        .starts_with("attachment;"));
    assert_eq!(r.bytes().await.unwrap().as_ref(), bytes);
}
async fn authorization_contract(
    client: &reqwest::Client,
    a: &str,
    b: &str,
    admin: &str,
    sid: &str,
    artifact: &str,
    environment: &str,
) -> String {
    let observer = uuid::Uuid::new_v4().simple().to_string().repeat(2);
    let operator = uuid::Uuid::new_v4().simple().to_string().repeat(2);
    let viewer = post(
        client,
        a,
        admin,
        "/credentials",
        json!({"request_id":"observer-key",
        "token":observer,"spec":{"principal":"viewer","role":"observer",
        "scope":{"kind":"sessions","ids":[sid]}}}),
    )
    .await;
    post(
        client,
        b,
        admin,
        "/credentials",
        json!({"request_id":"operator-key",
        "token":operator,"spec":{"principal":"alice","role":"operator",
        "scope":{"kind":"owned"},"environments":[environment]}}),
    )
    .await;
    let me = client
        .get(format!("{b}/auth/me"))
        .bearer_auth(&observer)
        .send()
        .await
        .unwrap()
        .error_for_status()
        .unwrap()
        .json::<Value>()
        .await
        .unwrap();
    assert_eq!(me["role"], "observer");
    assert!(!me.to_string().contains(&observer));
    for (method, path) in [
        ("POST", "/sessions".into()),
        ("POST", format!("/sessions/{sid}/turns")),
        ("POST", format!("/sessions/{sid}/controls")),
        ("POST", format!("/sessions/{sid}/artifacts")),
        (
            "PUT",
            format!("/sessions/{sid}/artifacts/{artifact}/content"),
        ),
        ("POST", "/credentials".into()),
        ("GET", "/credentials".into()),
        ("GET", "/security/audit".into()),
        ("DELETE", format!("/sessions/{sid}")),
    ] {
        let r = client
            .request(method.parse().unwrap(), format!("{b}{path}"))
            .bearer_auth(&observer)
            .json(&json!({}))
            .send()
            .await
            .unwrap();
        assert_eq!(r.status(), 403, "{method} {path}");
    }
    for path in [
        format!("/sessions/{sid}"),
        format!("/sessions/{sid}/events"),
        format!("/sessions/{sid}/turns/read"),
        format!("/sessions/{sid}/controls/approve-read"),
        format!("/sessions/{sid}/artifacts/{artifact}/content"),
    ] {
        assert_eq!(
            client
                .get(format!("{a}{path}"))
                .bearer_auth(&operator)
                .send()
                .await
                .unwrap()
                .status(),
            403,
            "{path}"
        );
    }
    let body = json!({"request_id":"alice-session","parameters":{"name":"Owned",
        "coworker":"assistant-example","workspace":"/sandbox","environment_id":environment}});
    let owned = post(client, a, &operator, "/sessions", body).await;
    let owned_id = owned["session_id"].as_str().unwrap();
    assert_eq!(
        client
            .get(format!("{b}/sessions/{owned_id}"))
            .bearer_auth(&observer)
            .send()
            .await
            .unwrap()
            .status(),
        403
    );
    let directory = client
        .get(format!("{b}/sessions"))
        .bearer_auth(&operator)
        .send()
        .await
        .unwrap()
        .error_for_status()
        .unwrap()
        .json::<Value>()
        .await
        .unwrap();
    assert_eq!(directory["sessions"].as_array().unwrap().len(), 1);
    assert!(!directory.to_string().contains(sid));
    // Hold back half an upload while revoking its writer through the other API.
    // The already admitted byte transfer must never publish a ready artifact.
    let uploader = uuid::Uuid::new_v4().simple().to_string().repeat(2);
    let upload_key = post(
        client,
        a,
        admin,
        "/credentials",
        json!({"request_id":"upload-key",
        "token":uploader,"spec":{"principal":"uploader","role":"operator",
        "scope":{"kind":"sessions","ids":[sid]}}}),
    )
    .await;
    let original = client
        .get(format!("{a}/sessions/{sid}/artifacts/{artifact}"))
        .bearer_auth(admin)
        .send()
        .await
        .unwrap()
        .json::<Value>()
        .await
        .unwrap();
    let bytes = vec![9u8; 2 * 1024 * 1024];
    let pending = post(client, b, &uploader, &format!("/sessions/{sid}/artifacts"),
        json!({"request_id":"revoke-upload","turn_id":original["turn_id"],"name":"partial.bin",
        "content_type":"application/octet-stream","size":bytes.len(),"sha256":openworker_artifacts::hash(&bytes)})).await;
    let content = format!(
        "{b}/sessions/{sid}/artifacts/{}/content",
        pending["id"].as_str().unwrap()
    );
    let (started_tx, started_rx) = tokio::sync::oneshot::channel();
    let (release_tx, release_rx) = tokio::sync::oneshot::channel();
    let input = async_stream::stream! {
        yield Ok::<_,std::io::Error>(vec![9u8;1024*1024]);
        let _ = started_tx.send(());
        let _ = release_rx.await;
        yield Ok::<_,std::io::Error>(vec![9u8;1024*1024]);
    };
    let send = client
        .put(content)
        .bearer_auth(&uploader)
        .header("content-length", bytes.len())
        .body(reqwest::Body::wrap_stream(input))
        .send();
    let upload = tokio::spawn(send);
    tokio::time::timeout(Duration::from_secs(5), started_rx)
        .await
        .unwrap()
        .unwrap();
    post(
        client,
        a,
        admin,
        &format!("/credentials/{}/revoke", upload_key["id"].as_str().unwrap()),
        json!({}),
    )
    .await;
    let _ = release_tx.send(());
    if let Ok(r) = tokio::time::timeout(Duration::from_secs(10), upload)
        .await
        .unwrap()
        .unwrap()
    {
        assert!(!r.status().is_success());
    }
    let state = client
        .get(format!(
            "{a}/sessions/{sid}/artifacts/{}",
            pending["id"].as_str().unwrap()
        ))
        .bearer_auth(admin)
        .send()
        .await
        .unwrap()
        .json::<Value>()
        .await
        .unwrap();
    assert_eq!(state["status"], "pending");
    let mut stream = client
        .get(format!("{b}/sessions/{sid}/events"))
        .bearer_auth(&observer)
        .send()
        .await
        .unwrap()
        .error_for_status()
        .unwrap()
        .bytes_stream();
    // The connection remains open while another node revokes its credential.
    post(
        client,
        a,
        admin,
        &format!("/credentials/{}/revoke", viewer["id"].as_str().unwrap()),
        json!({}),
    )
    .await;
    tokio::time::timeout(Duration::from_secs(5), async {
        let mut text = String::new();
        while let Some(chunk) = stream.next().await {
            text.push_str(&String::from_utf8_lossy(&chunk.unwrap()));
        }
        assert!(text.contains("stream_error"));
    })
    .await
    .unwrap();
    for base in [a, b] {
        assert_eq!(
            client
                .get(format!("{base}/auth/me"))
                .bearer_auth(&observer)
                .send()
                .await
                .unwrap()
                .status(),
            401
        );
    }
    let audit = client
        .get(format!("{a}/security/audit"))
        .bearer_auth(admin)
        .send()
        .await
        .unwrap()
        .error_for_status()
        .unwrap()
        .json::<Value>()
        .await
        .unwrap()
        .to_string();
    assert!(audit.contains("alice") && audit.contains("denied"));
    assert!(!audit.contains(&observer) && !audit.contains(&operator));
    eprintln!("PASS scoped authorization, cross-node revoke and SSE closure");
    owned_id.to_owned()
}
async fn artifact_contract(
    client: &reqwest::Client,
    a: &str,
    b: &str,
    token: &str,
    sid: &str,
    store: &PostgresStore,
    config: &Config,
) -> (String, Vec<u8>) {
    // Publish actual generated output, not a second independently fabricated copy.
    let bytes = store
        .inspect(sid)
        .unwrap()
        .history
        .iter()
        .filter_map(|v| {
            let output: Value = serde_json::from_str(v["output"].as_str()?).ok()?;
            let inner: Value = serde_json::from_str(output["content"][0]["text"].as_str()?).ok()?;
            let text = inner["text"].as_str()?;
            (inner["path"] == "http-proof.txt").then(|| text.as_bytes().to_vec())
        })
        .next()
        .expect("real read_file output");
    assert_eq!(bytes, b"original file across replicas");
    let tid = store.submission(sid, "read").unwrap().turn_id;
    let path = format!("/sessions/{sid}/artifacts");
    let body = json!({"request_id":"actual-output","turn_id":tid,"name":"http-proof.txt","content_type":"text/plain","size":bytes.len(),"sha256":openworker_artifacts::hash(&bytes)});
    let (one, two) = tokio::join!(
        post(client, a, token, &path, body.clone()),
        post(client, b, token, &path, body.clone())
    );
    assert_eq!(one, two);
    let id = one["id"].as_str().unwrap().to_owned();
    let content = format!("{b}{path}/{id}/content");
    assert_eq!(client.get(&content).send().await.unwrap().status(), 401);
    assert_eq!(
        client
            .get(&content)
            .bearer_auth(token)
            .send()
            .await
            .unwrap()
            .status(),
        409
    );
    assert_eq!(
        client
            .put(&content)
            .bearer_auth(token)
            .body("short")
            .send()
            .await
            .unwrap()
            .status(),
        409
    );
    let response = client
        .put(format!("{a}{path}/{id}/content"))
        .bearer_auth(token)
        .body(bytes.clone())
        .send()
        .await
        .unwrap();
    assert!(
        response.status().is_success(),
        "{}",
        response.text().await.unwrap()
    );
    download(client, b, token, sid, &id, &bytes).await;
    // >160KB ensures raw-body transfers do not inherit the JSON request cap.
    let large = vec![7u8; 2 * 1024 * 1024 + 23];
    let mut body = body;
    body["request_id"] = json!("large-output");
    body["size"] = json!(large.len());
    body["sha256"] = json!(openworker_artifacts::hash(&large));
    let pending = post(client, b, token, &path, body).await;
    let large_id = pending["id"].as_str().unwrap();
    let r = client
        .put(format!("{a}{path}/{large_id}/content"))
        .bearer_auth(token)
        .body(large.clone())
        .send()
        .await
        .unwrap();
    assert!(r.status().is_success(), "{}", r.text().await.unwrap());
    download(client, b, token, sid, large_id, &large).await;
    // Lost metadata commit: bytes exist, but pending cannot be downloaded.
    let mut registration = store.artifact(sid, &id).unwrap().registration;
    registration.request_id = "lost-commit".into();
    let storage = config.artifacts.as_ref().unwrap().build().unwrap();
    let pending = store
        .register_artifact(registration, storage.binding().clone(), "reference-user")
        .unwrap();
    storage
        .put(
            &pending,
            bytes.as_slice(),
            &tokio_util::sync::CancellationToken::new(),
        )
        .await
        .unwrap();
    assert_eq!(
        client
            .get(format!("{a}{path}/{}/content", pending.id))
            .bearer_auth(token)
            .send()
            .await
            .unwrap()
            .status(),
        409
    );
    let ready = post(
        client,
        b,
        token,
        &format!("{path}/{}/reconcile", pending.id),
        json!({}),
    )
    .await;
    assert_eq!(ready["status"], "ready");
    download(client, a, token, sid, &pending.id, &bytes).await;
    if let Ok(node) = std::env::var("OW_PROBE_NODE") {
        let root = tempfile::tempdir().unwrap();
        let file = root.path().join("client.bin");
        std::fs::write(&file, &large).unwrap();
        for base in [a, b] {
            let output = tokio::time::timeout(
                Duration::from_secs(30),
                Command::new(&node)
                    .arg(Path::new(env!("CARGO_MANIFEST_DIR")).join("artifact-client.mjs"))
                    .args([
                        "--session",
                        sid,
                        "--turn",
                        &tid,
                        "--request-id",
                        "node-artifact-proof",
                        "--file",
                    ])
                    .arg(&file)
                    .env("OW_API_URL", base)
                    .env("OW_API_TOKEN_FILE", &config.api_token_file)
                    .stdin(Stdio::null())
                    .output(),
            )
            .await
            .unwrap()
            .unwrap();
            assert!(
                output.status.success(),
                "{}",
                String::from_utf8_lossy(&output.stderr)
            );
            assert!(String::from_utf8_lossy(&output.stdout).contains("\"status\": \"ready\""));
        }
        eprintln!("PASS shipped Node artifact uploader and exact replay through other API");
    }
    eprintln!("PASS cross-API artifact streaming, pending denial and lost-commit reconciliation");
    (id, bytes)
}
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
#[ignore = "requires isolated OW_PG_TEST_URL and OW_PROBE_ENDPOINT/CERTS/IMAGE/RUNNER"]
async fn two_apis_two_workers_creation_approval_restart_and_sse_replay() {
    let root = tempfile::tempdir().unwrap();
    let token = uuid::Uuid::new_v4().simple().to_string().repeat(2);
    let token_file = root.path().join("api-token");
    std::fs::write(&token_file, &token).unwrap();
    let path = root.path().join("config.json");
    let config = json!({"database_env":"OW_PG_TEST_URL","schema":format!("ow_http_{}",uuid::Uuid::new_v4().simple()),"database_loopback_plaintext":true,"state_root":root.path().join("state"),"bundle":Path::new(env!("CARGO_MANIFEST_DIR")).join("../examples/assistant").canonicalize().unwrap(),"runner":std::env::var("OW_PROBE_RUNNER").unwrap(),"model":"fixture","scripted":true,"api_token_file":token_file,"actor":"reference-user","openshell":{"identity":"reference-qualification","endpoint":std::env::var("OW_PROBE_ENDPOINT").unwrap(),"certificates":std::env::var("OW_PROBE_CERTS").unwrap(),"tls_server_name":std::env::var("OW_PROBE_TLS_NAME").ok(),"workspace":"default","image":std::env::var("OW_PROBE_IMAGE").unwrap()}});
    let mut config = config;
    let artifact_root = root.path().join("artifacts");
    std::fs::create_dir(&artifact_root).unwrap();
    let deployment = format!("test-{}", uuid::Uuid::new_v4().simple());
    config["artifacts"] = match std::env::var("OW_TEST_S3_ENDPOINT") {
        Ok(endpoint) => {
            json!({"kind":"s3","deployment":deployment,"bucket":"openworker-artifacts","region":"us-east-1","endpoint":endpoint})
        }
        Err(_) => {
            json!({"kind":"filesystem","root":artifact_root,"deployment":deployment,"shared_durable":true})
        }
    };
    if let Ok(endpoint) = std::env::var("OW_TEST_OTLP_ENDPOINT") {
        config["telemetry"] = json!({"endpoint":endpoint});
    }
    std::fs::write(&path, serde_json::to_vec_pretty(&config).unwrap()).unwrap();
    let config: Config = serde_json::from_value(config).unwrap();
    let db = config.database().unwrap();
    PostgresStore::migrate(&db).unwrap();
    let store = PostgresStore::open(db).unwrap();
    store.auth_bootstrap(&token, "reference-user").unwrap();
    let provider = config.provider().unwrap();
    let (env, fresh) = store
        .begin_environment(
            "environment",
            "operator",
            provider.spec(Sharing::Shared, Management::Harness),
        )
        .unwrap();
    assert!(fresh);
    eprintln!(
        "HTTP qualification environment {} schema {}",
        env.id, config.schema
    );
    let binding = provider.create(env.clone()).await.unwrap();
    store
        .complete_environment(&env.id, env.revision, Some(binding.clone()))
        .unwrap();
    eprintln!(
        "HTTP qualification remote {} {}",
        binding.remote_name, binding.remote_id
    );
    let (mut a, url_a) = api(&path, "api-a").await;
    let (mut b, url_b) = api(&path, "api-b").await;
    let client = reqwest::Client::new();
    assert_eq!(
        client
            .get(format!("{url_a}/readyz"))
            .bearer_auth(&token)
            .send()
            .await
            .unwrap()
            .status(),
        200
    );
    assert_eq!(
        client
            .get(format!("{url_a}/healthz"))
            .send()
            .await
            .unwrap()
            .status(),
        401
    );
    assert_eq!(
        client
            .get(format!("{url_a}/healthz"))
            .bearer_auth(&token)
            .header("Origin", "https://untrusted.invalid")
            .send()
            .await
            .unwrap()
            .status(),
        403
    );
    let body = json!({"request_id":"create-once","parameters":{"name":"Replica proof","coworker":"assistant-example","workspace":"/sandbox","environment_id":env.id}});
    let (one, two) = tokio::join!(
        post(&client, &url_a, &token, "/sessions", body.clone()),
        post(&client, &url_b, &token, "/sessions", body.clone())
    );
    assert_eq!(one["session_id"], two["session_id"]);
    assert_ne!(one["duplicate"], two["duplicate"]);
    let sid = one["session_id"].as_str().unwrap();
    assert_eq!(
        client
            .get(format!("{url_b}/sessions/{sid}/events?after=-1"))
            .bearer_auth(&token)
            .send()
            .await
            .unwrap()
            .status(),
        400
    );
    let mut changed = body.clone();
    changed["parameters"]["name"] = json!("conflict");
    assert_eq!(
        client
            .post(format!("{url_b}/sessions"))
            .bearer_auth(&token)
            .json(&changed)
            .send()
            .await
            .unwrap()
            .status(),
        409
    );
    let submit=post(&client,&url_a,&token,&format!("/sessions/{sid}/turns"),json!({"submission_id":"write","text":plan("write_file",json!({"path":"http-proof.txt","content":"original file across replicas"}))})).await;
    assert_eq!(submit["status"], "queued");
    let mut w1 = worker(&path, "worker-a");
    let mut w2 = worker(&path, "worker-b");
    let pending = wait_for(&client, &url_b, &token, sid, "waiting").await;
    let cursor = pending["cursor"].as_i64().unwrap();
    // Replace both workers and one API while the durable approval is parked.
    stop(&mut w1).await;
    stop(&mut w2).await;
    stop(&mut a).await;
    let (mut a, new_url_a) = api(&path, "api-a").await;
    let replay = post(&client, &new_url_a, &token, "/sessions", body).await;
    assert_eq!(replay["session_id"], sid);
    assert_eq!(replay["duplicate"], true);
    let restored = snapshot(&client, &new_url_a, &token, sid).await;
    assert_eq!(
        restored["interactions"][0]["id"],
        pending["interactions"][0]["id"]
    );
    let mut w1 = worker(&path, "worker-c");
    let mut w2 = worker(&path, "worker-d");
    let i = &restored["interactions"][0];
    let action = json!({"kind":"response","interaction_id":i["id"],"response":"session","binding":{"bundle_digest":i["bundle_digest"],"schema_digest":i["schema_digest"],"policy_digest":i["policy_digest"],"resource_policy_digest":i["resource_policy_digest"]}});
    let mut stream = client
        .get(format!("{url_b}/sessions/{sid}/events"))
        .bearer_auth(&token)
        .header("Last-Event-ID", cursor.to_string())
        .send()
        .await
        .unwrap()
        .error_for_status()
        .unwrap()
        .bytes_stream();
    post(
        &client,
        &new_url_a,
        &token,
        &format!("/sessions/{sid}/controls"),
        json!({"request_id":"approve","turn_id":i["turn_id"],"action":action}),
    )
    .await;
    let observed = tokio::time::timeout(Duration::from_secs(45), async {
        let mut buffer = String::new();
        let mut expected = cursor + 1;
        let mut count = 0;
        while let Some(chunk) = stream.next().await {
            buffer.push_str(std::str::from_utf8(&chunk.unwrap()).unwrap());
            while let Some(end) = buffer.find("\n\n") {
                let frame = buffer[..end].to_owned();
                buffer.drain(..end + 2);
                if let Some(data) = frame.lines().find_map(|l| l.strip_prefix("data: ")) {
                    let event: Value = serde_json::from_str(data).unwrap();
                    assert_eq!(event["event_id"], expected, "{event}");
                    expected += 1;
                    count += 1;
                    if event["type"] == "turn_completed" && event["data"]["status"] == "completed" {
                        return count;
                    }
                }
            }
        }
        panic!("SSE ended before completion")
    })
    .await
    .unwrap();
    assert!(observed > 3);
    drop(stream);
    wait_for(&client, &new_url_a, &token, sid, "completed").await;
    assert!(store
        .inspect(sid)
        .unwrap()
        .history
        .iter()
        .any(|v| v["type"] == "function_call_output" && v.to_string().contains("written")));
    post(
        &client,
        &url_b,
        &token,
        &format!("/sessions/{sid}/turns"),
        json!({"submission_id":"read","text":plan("read_file",json!({"path":"http-proof.txt"}))}),
    )
    .await;
    let read_pending = wait_for(&client, &new_url_a, &token, sid, "waiting").await;
    let i = &read_pending["interactions"][0];
    post(&client,&url_b,&token,&format!("/sessions/{sid}/controls"),json!({"request_id":"approve-read","turn_id":i["turn_id"],"action":{"kind":"response","interaction_id":i["id"],"response":"once","binding":{"bundle_digest":i["bundle_digest"],"schema_digest":i["schema_digest"],"policy_digest":i["policy_digest"],"resource_policy_digest":i["resource_policy_digest"]}}})).await;
    // Receipt identity disambiguates a prior completed turn while the next is queued.
    tokio::time::timeout(Duration::from_secs(30), async {
        loop {
            let s = store.submission(sid, "read").unwrap();
            if s.status == openworker_store_postgres::SubmissionStatus::Completed {
                break;
            }
            tokio::time::sleep(Duration::from_millis(100)).await;
        }
    })
    .await
    .unwrap();
    assert!(store
        .inspect(sid)
        .unwrap()
        .history
        .iter()
        .any(|v| v["type"] == "function_call_output"
            && v.to_string().contains("original file across replicas")));
    let (artifact_id, artifact_bytes) =
        artifact_contract(&client, &new_url_a, &url_b, &token, sid, &store, &config).await;
    let auth_session = authorization_contract(
        &client,
        &new_url_a,
        &url_b,
        &token,
        sid,
        &artifact_id,
        &env.id,
    )
    .await;
    // Two real remote tool calls overlap: each service has only one slot.
    // Cancel through the other API and verify the sibling stream completes.
    let mut extra = vec![];
    for index in 0..2 {
        let made=post(&client,&new_url_a,&token,"/sessions",json!({"request_id":format!("parallel-{index}"),"parameters":{"name":format!("parallel-{index}"),"coworker":"assistant-example","workspace":"/sandbox","environment_id":env.id,"permissions":{"mode":"bypass"}}})).await;
        let id = made["session_id"].as_str().unwrap().to_owned();
        let command = if index == 0 {
            "sleep 20; printf uncertain"
        } else {
            "sleep 10; printf sibling-completed > /sandbox/sibling.txt"
        };
        post(
            &client,
            &url_b,
            &token,
            &format!("/sessions/{id}/turns"),
            json!({"submission_id":"parallel","text":plan("run_shell",json!({"command":command}))}),
        )
        .await;
        extra.push(id);
    }
    assert_eq!(
        client
            .get(format!(
                "{url_b}/sessions/{}/artifacts/{artifact_id}",
                extra[0]
            ))
            .bearer_auth(&token)
            .send()
            .await
            .unwrap()
            .status(),
        404
    );
    tokio::time::timeout(Duration::from_secs(15), async {
        loop {
            if extra.iter().all(|id| {
                store
                    .read_events(id, 0)
                    .unwrap()
                    .iter()
                    .any(|e| e.kind == "tool_started")
                    && store
                        .inspect(id)
                        .unwrap()
                        .turn
                        .is_some_and(|t| t.status == "executing")
            }) {
                break;
            }
            tokio::time::sleep(Duration::from_millis(100)).await;
        }
    })
    .await
    .unwrap();
    let running = snapshot(&client, &url_b, &token, &extra[0]).await;
    post(
        &client,
        &new_url_a,
        &token,
        &format!("/sessions/{}/controls", extra[0]),
        json!({"request_id":"cancel","turn_id":running["turn"]["id"],"action":{"kind":"cancel"}}),
    )
    .await;
    wait_for(&client, &url_b, &token, &extra[0], "interrupted").await;
    wait_for(&client, &new_url_a, &token, &extra[1], "completed").await;
    assert!(store
        .inspect(&extra[0])
        .unwrap()
        .history
        .iter()
        .any(|v| v["type"] == "function_call_output"
            && v.to_string().contains("tool_outcome_unknown")));
    provider.inspect(binding.clone()).await.unwrap();
    if let Ok(node) = std::env::var("OW_PROBE_NODE") {
        let output = tokio::time::timeout(
            Duration::from_secs(45),
            Command::new(node)
                .arg(Path::new(env!("CARGO_MANIFEST_DIR")).join("client.mjs"))
                .args([
                    "--session",
                    &extra[1],
                    "--request-id",
                    "node-client-proof",
                    "--fixture",
                ])
                .env("OW_API_URLS", format!("{url_b},{new_url_a}"))
                .env("OW_API_TOKEN_FILE", &config.api_token_file)
                .stdin(Stdio::null())
                .output(),
        )
        .await
        .unwrap()
        .unwrap();
        assert!(
            output.status.success(),
            "{}",
            String::from_utf8_lossy(&output.stderr)
        );
        assert!(String::from_utf8_lossy(&output.stdout).contains("\"status\": \"completed\""));
        eprintln!("PASS shipped Node client against real API/worker/tool");
    }
    stop(&mut w1).await;
    stop(&mut w2).await;
    stop(&mut a).await;
    stop(&mut b).await;
    let (mut replacement, replacement_url) = api(&path, "api-artifact-replacement").await;
    download(
        &client,
        &replacement_url,
        &token,
        sid,
        &artifact_id,
        &artifact_bytes,
    )
    .await;
    stop(&mut replacement).await;
    eprintln!("PASS artifact download after both workers and API nodes stopped/replaced");
    store.claim(sid).unwrap();
    extra.push(auth_session);
    for id in &extra {
        store.claim(id).unwrap();
    }
    let cleanup = config.runtime("cleanup", store.clone()).unwrap();
    cleanup
        .dispatch("session/close", json!({"session_id":sid}))
        .await
        .unwrap();
    for id in &extra {
        cleanup
            .dispatch("session/close", json!({"session_id":id}))
            .await
            .unwrap();
    }
    cleanup.shutdown().await.unwrap();
    let op = uuid::Uuid::new_v4().to_string();
    let (deleting, fresh) = store
        .begin_environment_delete(&env.id, &op, "operator")
        .unwrap();
    assert!(fresh);
    provider.delete(binding.clone(), op.clone()).await.unwrap();
    assert!(provider.confirm_deleted(binding).await.unwrap());
    store
        .complete_environment_delete(&env.id, &op, deleting.revision)
        .unwrap();
}
