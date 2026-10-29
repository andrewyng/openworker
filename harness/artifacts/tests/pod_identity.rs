//! Exercise the actual dependency credential chain in a separate process. All
//! endpoints and credentials here are synthetic loopback fixtures, never AWS.
#![cfg(feature = "s3")]
use axum::{
    body::Bytes,
    extract::{Request, State},
    http::{header, HeaderMap, StatusCode},
    response::{IntoResponse, Response},
    routing::any,
    Json, Router,
};
use futures_util::TryStreamExt;
use openworker_artifacts::*;
use serde_json::json;
use std::{
    collections::HashMap,
    path::PathBuf,
    sync::{
        atomic::{AtomicBool, AtomicUsize, Ordering},
        Arc, Mutex,
    },
    time::Duration,
};
use tokio_util::sync::CancellationToken;

#[derive(Clone)]
struct Fixture {
    token: PathBuf,
    blocked: Arc<AtomicBool>,
    credentials: Arc<AtomicUsize>,
    objects: Arc<AtomicUsize>,
    rotated: Arc<AtomicBool>,
    content: Arc<Mutex<HashMap<String, Bytes>>>,
}
async fn credentials(State(f): State<Fixture>, headers: HeaderMap) -> Response {
    f.credentials.fetch_add(1, Ordering::SeqCst);
    let expected = std::fs::read_to_string(&f.token).unwrap();
    if f.blocked.load(Ordering::SeqCst)
        || headers
            .get(header::AUTHORIZATION)
            .and_then(|h| h.to_str().ok())
            != Some(expected.as_str())
    {
        return StatusCode::UNAUTHORIZED.into_response();
    }
    let generation = if expected == "synthetic-projected-token-two" {
        f.rotated.store(true, Ordering::SeqCst);
        2
    } else {
        1
    };
    Json(json!({
        "AccessKeyId":format!("OWTESTKEY{generation}"),
        "SecretAccessKey":"synthetic-secret-never-use-on-aws",
        "Token":format!("synthetic-session-{generation}"),
        "Expiration":(chrono::Utc::now()+chrono::Duration::seconds(1)).to_rfc3339()
    }))
    .into_response()
}
async fn object(State(f): State<Fixture>, request: Request) -> Response {
    f.objects.fetch_add(1, Ordering::SeqCst);
    let headers = request.headers();
    let auth = headers
        .get(header::AUTHORIZATION)
        .unwrap()
        .to_str()
        .unwrap();
    let session = headers
        .get("x-amz-security-token")
        .unwrap()
        .to_str()
        .unwrap();
    assert!(
        auth.contains("Credential=OWTESTKEY1/") && session == "synthetic-session-1"
            || auth.contains("Credential=OWTESTKEY2/") && session == "synthetic-session-2"
    );
    let path = request.uri().path().to_owned();
    if request.method() == "PUT" {
        assert_eq!(headers[header::IF_NONE_MATCH], "*");
        let bytes = axum::body::to_bytes(request.into_body(), CHUNK + 1)
            .await
            .unwrap();
        let mut objects = f.content.lock().unwrap();
        if objects.contains_key(&path) {
            return (
                StatusCode::PRECONDITION_FAILED,
                "<Error><Code>PreconditionFailed</Code></Error>",
            )
                .into_response();
        }
        let etag = format!("\"{}\"", hash(&bytes));
        objects.insert(path, bytes);
        return (StatusCode::OK, [(header::ETAG, etag)]).into_response();
    }
    match f.content.lock().unwrap().get(&path) {
        Some(bytes) => (
            StatusCode::OK,
            [
                (header::ETAG, format!("\"{}\"", hash(bytes))),
                (
                    header::LAST_MODIFIED,
                    "Wed, 21 Oct 2015 07:28:00 GMT".into(),
                ),
            ],
            bytes.clone(),
        )
            .into_response(),
        None => StatusCode::NOT_FOUND.into_response(),
    }
}
async fn serve(router: Router) -> (String, tokio::task::JoinHandle<()>) {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url = format!("http://{}", listener.local_addr().unwrap());
    let task = tokio::spawn(async move { axum::serve(listener, router).await.unwrap() });
    (url, task)
}
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn projected_token_refresh_and_failures_never_use_ambient_credentials() {
    let root = tempfile::tempdir().unwrap();
    let token = root.path().join("projected-token");
    std::fs::write(&token, "synthetic-projected-token-one").unwrap();
    let fixture = Fixture {
        token: token.clone(),
        blocked: Arc::default(),
        credentials: Arc::default(),
        objects: Arc::default(),
        rotated: Arc::default(),
        content: Arc::default(),
    };
    let (credential_url, credential_task) = serve(
        Router::new()
            .route("/", any(credentials))
            .with_state(fixture.clone()),
    )
    .await;
    let (object_url, object_task) = serve(
        Router::new()
            .route("/*key", any(object))
            .with_state(fixture.clone()),
    )
    .await;
    for mode in [
        "roundtrip",
        "denied",
        "missing-file",
        "incomplete",
        "incomplete-token",
    ] {
        fixture.blocked.store(mode == "denied", Ordering::SeqCst);
        let before = fixture.objects.load(Ordering::SeqCst);
        let credential_before = fixture.credentials.load(Ordering::SeqCst);
        let mut child = tokio::process::Command::new(std::env::current_exe().unwrap());
        child.args(["--exact", "pod_identity_probe", "--ignored", "--nocapture"]);
        for (key, _) in std::env::vars_os() {
            if key.to_string_lossy().starts_with("AWS_") {
                child.env_remove(key);
            }
        }
        child
            .env("OW_POD_IDENTITY_CHILD", mode)
            .env("OW_POD_S3_ENDPOINT", &object_url)
            .env("AWS_CONTAINER_CREDENTIALS_FULL_URI", &credential_url)
            .env("NO_PROXY", "127.0.0.1,localhost")
            .kill_on_drop(true);
        if mode == "incomplete-token" {
            child.env_remove("AWS_CONTAINER_CREDENTIALS_FULL_URI");
        }
        if mode != "incomplete" {
            child.env(
                "AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE",
                if mode == "missing-file" {
                    root.path().join("missing-token")
                } else {
                    token.clone()
                },
            );
        }
        let output = tokio::time::timeout(Duration::from_secs(30), child.output())
            .await
            .unwrap()
            .unwrap();
        assert!(
            output.status.success(),
            "probe {mode} failed: {}",
            String::from_utf8_lossy(&output.stderr)
        );
        if mode == "roundtrip" {
            assert!(fixture.rotated.load(Ordering::SeqCst));
            assert!(fixture.objects.load(Ordering::SeqCst) > before);
        } else {
            assert_eq!(
                fixture.objects.load(Ordering::SeqCst),
                before,
                "credential failure sent an S3 request"
            );
            if mode != "denied" {
                assert_eq!(
                    fixture.credentials.load(Ordering::SeqCst),
                    credential_before
                );
            }
        }
    }
    credential_task.abort();
    object_task.abort();
}

#[tokio::test]
#[ignore = "subprocess fixture; invoked only by its parent with synthetic endpoints"]
async fn pod_identity_probe() {
    let Ok(mode) = std::env::var("OW_POD_IDENTITY_CHILD") else {
        return;
    };
    assert!(std::env::var_os("AWS_ACCESS_KEY_ID").is_none());
    assert!(std::env::var_os("AWS_SECRET_ACCESS_KEY").is_none());
    let config = s3::Config {
        deployment: "pod-fixture".into(),
        bucket: "fixture-artifacts".into(),
        region: "us-east-1".into(),
        endpoint: Some(std::env::var("OW_POD_S3_ENDPOINT").unwrap()),
        ca_file: None,
    };
    if mode.starts_with("incomplete") {
        assert_eq!(
            config.build(None).err().unwrap().downcast_ref::<Error>(),
            Some(&Error::Invalid)
        );
        return;
    }
    let store = config.build(None).unwrap();
    let bytes = vec![37; CHUNK + 73];
    let a = Artifact {
        id: "a_pod".into(),
        registration: Registration {
            request_id: "pod-upload".into(),
            session_id: "s_pod".into(),
            turn_id: "t_pod".into(),
            name: "proof.bin".into(),
            content_type: "application/octet-stream".into(),
            expected: Expected {
                size: bytes.len() as u64,
                sha256: hash(&bytes),
            },
        },
        binding: store.binding().clone(),
        key: "pod-fixture/s_pod/a_pod".into(),
        status: Status::Pending,
        proof: None,
    };
    let cancel = CancellationToken::new();
    let result = store.put(&a, bytes.as_slice(), &cancel).await;
    if mode != "roundtrip" {
        let error = result.unwrap_err();
        assert_eq!(error.downcast_ref::<Error>(), Some(&Error::Unavailable));
        assert!(!error.to_string().contains("synthetic"));
        return;
    }
    let proof = result.unwrap();
    tokio::time::sleep(Duration::from_millis(1100)).await;
    std::fs::write(
        std::env::var("AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE").unwrap(),
        "synthetic-projected-token-two",
    )
    .unwrap();
    assert_eq!(store.verify(&a, &cancel).await.unwrap(), proof);
    let content = store
        .get(&a, &proof, cancel)
        .await
        .unwrap()
        .try_collect::<Vec<_>>()
        .await
        .unwrap()
        .concat();
    assert_eq!(content, bytes);
}
