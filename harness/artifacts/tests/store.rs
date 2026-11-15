use futures_util::TryStreamExt;
use openworker_artifacts::*;
use tokio_util::sync::CancellationToken;
fn artifact(store: &Store, bytes: &[u8]) -> Artifact {
    let id = format!("a_{}", uuid::Uuid::new_v4().simple());
    Artifact {
        id: id.clone(),
        registration: Registration {
            origin: Default::default(),
            request_id: "request".into(),
            session_id: "s_test".into(),
            turn_id: "t_test".into(),
            name: "test.bin".into(),
            content_type: "application/octet-stream".into(),
            expected: Expected {
                size: bytes.len() as u64,
                sha256: hash(bytes),
            },
        },
        binding: store.binding().clone(),
        key: format!("{}/s_test/{id}", store.binding().deployment),
        status: Status::Pending,
        proof: None,
    }
}
async fn contract(store: Store) {
    let bytes: Vec<u8> = (0..CHUNK * 3 + 73).map(|n| (n % 251) as u8).collect();
    let a = artifact(&store, &bytes);
    let cancel = CancellationToken::new();
    assert!(store.put(&a, &bytes[..CHUNK + 24], &cancel).await.is_err());
    assert_eq!(
        store
            .head(&a, &cancel)
            .await
            .unwrap_err()
            .downcast_ref::<Error>(),
        Some(&Error::NotFound)
    );
    let (p, q) = tokio::join!(
        store.put(&a, bytes.as_slice(), &cancel),
        store.put(&a, bytes.as_slice(), &cancel)
    );
    let p = p.unwrap();
    assert_eq!(p, q.unwrap());
    assert_eq!(p.expected, a.registration.expected);
    assert_eq!(store.verify(&a, &cancel).await.unwrap(), p);
    let mut ready = a.clone();
    ready.status = Status::Ready;
    ready.proof = Some(p.clone());
    let chunks = store
        .get(&ready, &p, cancel.clone())
        .await
        .unwrap()
        .try_collect::<Vec<_>>()
        .await
        .unwrap();
    assert!(chunks.iter().all(|c| c.len() <= CHUNK));
    assert_eq!(chunks.concat(), bytes);
    let mut wrong = bytes.clone();
    wrong[0] ^= 1;
    assert_eq!(
        store
            .put(&a, wrong.as_slice(), &cancel)
            .await
            .unwrap_err()
            .downcast_ref::<Error>(),
        Some(&Error::Conflict)
    );
    assert_eq!(store.verify(&ready, &cancel).await.unwrap(), p);
    let mut other = a.clone();
    other.binding.identity = hash(b"wrong-root");
    assert!(store.head(&other, &cancel).await.is_err());
    let cancelled = CancellationToken::new();
    cancelled.cancel();
    assert_eq!(
        store
            .put(&a, bytes.as_slice(), &cancelled)
            .await
            .unwrap_err()
            .downcast_ref::<Error>(),
        Some(&Error::Cancelled)
    );
    let empty = artifact(&store, b"");
    let p = store.put(&empty, &b""[..], &cancel).await.unwrap();
    assert_eq!(store.verify(&empty, &cancel).await.unwrap(), p);
}
#[tokio::test(flavor = "multi_thread")]
async fn filesystem_atomic_streaming_resume_and_conflicts() {
    let root = tempfile::tempdir().unwrap();
    let store = filesystem::Filesystem::open(root.path(), "test", false, false).unwrap();
    let reopened = filesystem::Filesystem::open(root.path(), "test", true, true).unwrap();
    assert_eq!(store.binding(), reopened.binding());
    assert!(filesystem::Filesystem::open(root.path(), "test", false, true).is_err());
    contract(store).await;
}
#[tokio::test(flavor = "multi_thread")]
async fn corrupt_manifest_and_chunks_are_not_served() {
    let root = tempfile::tempdir().unwrap();
    let store = filesystem::Filesystem::open(root.path(), "test", false, false).unwrap();
    let a = artifact(&store, b"original bytes");
    let cancel = CancellationToken::new();
    let p = store
        .put(&a, &b"original bytes"[..], &cancel)
        .await
        .unwrap();
    let mut ready = a.clone();
    ready.status = Status::Ready;
    ready.proof = Some(p.clone());
    let file = root
        .path()
        .join(filesystem::Filesystem::object_filename(&format!(
            "{}/manifest",
            a.key
        )));
    let bytes = std::fs::read(&file).unwrap();
    let mut modified = bytes.clone();
    modified.push(b'\n');
    std::fs::write(&file, modified).unwrap();
    assert!(store.get(&ready, &p, cancel.clone()).await.is_err());
    std::fs::write(&file, bytes).unwrap();
    let part = root
        .path()
        .join(filesystem::Filesystem::object_filename(&format!(
            "{}/part-00",
            a.key
        )));
    std::fs::write(&part, b"modified bytes").unwrap();
    let mut stream = store.get(&ready, &p, cancel.clone()).await.unwrap();
    assert!(stream.try_next().await.is_err());
    std::fs::remove_file(&part).unwrap();
    assert!(store.verify(&ready, &cancel).await.is_err());
}
#[tokio::test(flavor = "multi_thread")]
async fn mismatched_checksum_and_limits_never_publish() {
    let root = tempfile::tempdir().unwrap();
    let store = filesystem::Filesystem::open(root.path(), "test", false, false).unwrap();
    let a = artifact(&store, b"right");
    let cancel = CancellationToken::new();
    assert!(store.put(&a, &b"wrong"[..], &cancel).await.is_err());
    assert!(store.head(&a, &cancel).await.is_err());
    let mut big = a.clone();
    big.registration.expected.size = MAX_SIZE + 1;
    assert!(store.put(&big, &b""[..], &cancel).await.is_err());
    let mut bad = a.clone();
    bad.registration.name = "../secret".into();
    assert!(store.put(&bad, &b"right"[..], &cancel).await.is_err());
}
#[cfg(unix)]
#[tokio::test(flavor = "multi_thread")]
async fn capability_root_refuses_symlink_escape_and_host_paths() {
    let root = tempfile::tempdir().unwrap();
    let outside = tempfile::tempdir().unwrap();
    let secret = outside.path().join("private");
    std::fs::write(&secret, b"host secret").unwrap();
    let store = filesystem::Filesystem::open(root.path(), "test", false, false).unwrap();
    let a = artifact(&store, b"host secret");
    let file = root
        .path()
        .join(filesystem::Filesystem::object_filename(&format!(
            "{}/manifest",
            a.key
        )));
    std::os::unix::fs::symlink(secret, &file).unwrap();
    assert!(store.head(&a, &CancellationToken::new()).await.is_err());
    let mut traversal = a.clone();
    traversal.key = "../../private".into();
    assert!(store
        .head(&traversal, &CancellationToken::new())
        .await
        .is_err());
    let link = outside.path().join("linked-root");
    std::os::unix::fs::symlink(root.path(), &link).unwrap();
    assert!(filesystem::Filesystem::open(&link, "test", false, false).is_err());
}
#[tokio::test(flavor = "multi_thread")]
async fn cancellation_during_partial_input_does_not_create_manifest() {
    use tokio::io::AsyncWriteExt;
    let root = tempfile::tempdir().unwrap();
    let store = filesystem::Filesystem::open(root.path(), "test", false, false).unwrap();
    let bytes = vec![7; CHUNK * 2];
    let a = artifact(&store, &bytes);
    let (mut writer, reader) = tokio::io::duplex(CHUNK);
    let cancel = CancellationToken::new();
    let c = cancel.clone();
    tokio::spawn(async move {
        writer.write_all(&vec![7; CHUNK + 100]).await.unwrap();
        c.cancel();
    });
    assert!(store.put(&a, reader, &cancel).await.is_err());
    assert!(store.head(&a, &CancellationToken::new()).await.is_err());
    assert!(store
        .put(&a, bytes.as_slice(), &CancellationToken::new())
        .await
        .is_ok());
}
#[cfg(feature = "s3")]
#[tokio::test(flavor = "multi_thread")]
#[ignore = "requires isolated OW_TEST_S3_ENDPOINT/bucket and synthetic AWS credentials"]
async fn s3_atomic_streaming_resume_and_conflicts() {
    let config = s3::Config {
        deployment: format!("test-{}", uuid::Uuid::new_v4().simple()),
        bucket: "openworker-artifacts".into(),
        region: "us-east-1".into(),
        endpoint: Some(std::env::var("OW_TEST_S3_ENDPOINT").unwrap()),
        ca_file: None,
    };
    contract(config.build(None).unwrap()).await;
    for endpoint in [
        "http://example.org",
        "https://secret:password@example.org",
        "https://example.org/path",
        "https://example.org?token=private",
    ] {
        let mut c = config.clone();
        c.endpoint = Some(endpoint.into());
        assert!(c.build(None).is_err());
    }
}

#[cfg(feature = "s3")]
#[test]
fn private_ca_is_explicit_bounded_and_does_not_enable_plaintext() {
    let root = tempfile::tempdir().unwrap();
    let path = root.path().join("ca.pem");
    let mut config = s3::Config {
        deployment: "tls-test".into(),
        bucket: "openworker-artifacts".into(),
        region: "us-east-1".into(),
        endpoint: Some("https://objects.example".into()),
        ca_file: Some(path.clone()),
    };
    assert!(config.build(None).is_err());
    for contents in [
        Vec::new(),
        b"private-marker-invalid-ca".to_vec(),
        vec![b'x'; 65_537],
    ] {
        std::fs::write(&path, contents).unwrap();
        let error = config.build(None).err().expect("invalid CA accepted");
        assert!(!error.to_string().contains("private-marker"));
    }
    config.ca_file = None;
    config.endpoint = Some("http://objects.example".into());
    assert!(config.build(None).is_err());
}

#[test]
fn setup_provenance_is_explicit_without_fabricating_or_changing_ordinary_turn_receipts() {
    let old = serde_json::json!({"request_id":"one","session_id":"s_one","turn_id":"t_one","name":"source.bundle","content_type":"application/x-git-bundle","expected":{"size":5,"sha256":hash(b"hello")}});
    let turn: Registration = serde_json::from_value(old.clone()).unwrap();
    turn.validate().unwrap();
    assert!(turn.origin.is_turn());
    assert_eq!(serde_json::to_value(&turn).unwrap(), old);
    let mut setup = turn.clone();
    setup.origin = Origin::TeamSetup {
        team_id: "at_one".into(),
        setup_id: "source_one".into(),
    };
    assert!(setup.validate().is_err());
    setup.turn_id.clear();
    setup.validate().unwrap();
    let encoded = serde_json::to_value(&setup).unwrap();
    assert_eq!(encoded["origin"]["kind"], "team_setup");
    assert_eq!(encoded["turn_id"], "");
    assert_eq!(
        serde_json::from_value::<Registration>(encoded.clone()).unwrap(),
        setup
    );
    let mut injected = encoded;
    injected["origin"]["credentials"] = serde_json::json!(["secret"]);
    assert!(serde_json::from_value::<Registration>(injected).is_err());
}
