use super::*;
use openworker_runtime::coding_handoff::ObjectFormat;
fn grant() -> Grant {
    Grant {
        credential_env: "OW_TEST_GITHUB_TOKEN".into(),
        repositories: [(
            "org/game".into(),
            Repository {
                github: "org/game".into(),
                read_refs: ["refs/heads/main".into()].into(),
                write_prefixes: ["agents/".into()].into(),
            },
        )]
        .into(),
        expires_at_ms: None,
        revoked: false,
    }
}
#[test]
fn scopes_are_exact_and_credentials_are_only_references() {
    let g = grant();
    g.validate().unwrap();
    assert!(!g.digest().unwrap().is_empty());
    for b in [
        "../main",
        "/main",
        "agents/../../main",
        "x.lock",
        "x//y",
        "a b",
        "x@{y}",
    ] {
        assert!(branch(b).is_err(), "{b}");
    }
    let mut v = serde_json::to_value(&g).unwrap();
    v["token"] = serde_json::json!("secret");
    assert!(serde_json::from_value::<Grant>(v).is_err());
    let mut invalid = g.clone();
    invalid.repositories.get_mut("org/game").unwrap().github = "https://evil.invalid/repo".into();
    assert!(invalid.validate().is_err());
    let mut expired = g.clone();
    expired.expires_at_ms = Some(10);
    assert!(expired.live(9).is_ok());
    assert!(expired.live(10).is_err());
    expired.expires_at_ms = None;
    expired.revoked = true;
    assert!(expired.live(0).is_err());
    let temp = tempfile::tempdir().unwrap();
    assert!(Broker::new(&temp.path().canonicalize().unwrap(), g).is_ok());
}

fn git(root: &Path, args: &[&str]) -> String {
    let output = std::process::Command::new("/usr/bin/git")
        .current_dir(root)
        .args([
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=f@example.invalid",
            "-c",
            "core.hooksPath=/dev/null",
        ])
        .args(args)
        .env("GIT_CONFIG_NOSYSTEM", "1")
        .env("GIT_CONFIG_GLOBAL", "/dev/null")
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    String::from_utf8(output.stdout).unwrap().trim().into()
}
struct Fixture {
    _temp: tempfile::TempDir,
    root: PathBuf,
    source: PathBuf,
    remote: PathBuf,
    base: String,
    head: String,
    bundle: Bundle,
    path: PathBuf,
}
async fn fixture(format: ObjectFormat) -> Fixture {
    let temp = tempfile::tempdir().unwrap();
    let root = temp.path().canonicalize().unwrap();
    let source = root.join("source");
    std::fs::create_dir(&source).unwrap();
    git(
        &source,
        &[
            "init",
            "-q",
            &format!("--object-format={}", format.git_name()),
            "-b",
            "main",
        ],
    );
    std::fs::write(source.join("proof.txt"), "base").unwrap();
    git(&source, &["add", "proof.txt"]);
    git(&source, &["commit", "-qm", "base"]);
    let base = git(&source, &["rev-parse", "HEAD"]);
    let remote = root.join("upstream.git");
    git(
        &root,
        &[
            "clone",
            "--bare",
            "-q",
            source.to_str().unwrap(),
            remote.to_str().unwrap(),
        ],
    );
    std::fs::write(source.join("proof.txt"), "submitted").unwrap();
    std::fs::write(source.join("binary.bin"), [0, 255, 3]).unwrap();
    git(&source, &["add", "proof.txt", "binary.bin"]);
    git(&source, &["commit", "-qm", "change"]);
    let head = git(&source, &["rev-parse", "HEAD"]);
    let request = Export {
        operation_id: "submission-one".into(),
        scope_digest: "a".repeat(64),
        repository: "org/game".into(),
        directory: "source".into(),
        object_format: format,
        base_commit: base.clone(),
        submitted_commit: head.clone(),
    };
    let bundle = openworker_runtime::coding_handoff::git::export(&root, request, false, None)
        .await
        .unwrap();
    let path = root.join(&bundle.path);
    Fixture {
        _temp: temp,
        root,
        source,
        remote,
        base,
        head,
        bundle,
        path,
    }
}
impl Fixture {
    fn broker(&self) -> Broker {
        Broker::new(&self.root.join("broker"), grant())
            .unwrap()
            .with_fixture("org/game", &self.remote)
            .unwrap()
    }
    fn publication(&self, id: &str, expected: Option<&str>) -> Publication {
        Publication {
            id: id.into(),
            grant_digest: grant().digest().unwrap(),
            bundle: self.bundle.clone(),
            branch: "agents/result".into(),
            expected_head: expected.map(Into::into),
        }
    }
    fn now(&self) -> u64 {
        openworker_runtime::store::now_ms()
    }
    fn tip(&self) -> String {
        git(&self.remote, &["rev-parse", "refs/heads/agents/result"])
    }
}
#[tokio::test]
async fn pinned_remote_source_and_publication_preserve_both_object_formats_without_checkout_execution(
) {
    for format in [ObjectFormat::Sha1, ObjectFormat::Sha256] {
        let f = fixture(format).await;
        let broker = f.broker();
        let stop = CancellationToken::new();
        let src = Source {
            id: "source-setup".into(),
            grant_digest: grant().digest().unwrap(),
            reference: "refs/heads/main".into(),
            export: Export {
                operation_id: "source-setup".into(),
                base_commit: f.base.clone(),
                submitted_commit: f.base.clone(),
                ..f.bundle.request.clone()
            },
        };
        let staged = broker
            .stage(src.clone(), true, f.now(), &stop)
            .await
            .unwrap();
        assert_eq!(staged.request.submitted_commit, f.base);
        assert!(broker.exported_path(&src.id, &staged).unwrap().is_file());
        assert_eq!(
            broker.stage(src, false, f.now(), &stop).await.unwrap(),
            staged
        );
        assert!(
            !broker.root.join("source-setup/source/proof.txt").exists(),
            "Trusted source staging must not materialize project files"
        );
        let r = f.publication("publish-one", None);
        let out = broker
            .publish(r.clone(), &f.path, true, f.now(), &stop)
            .await
            .unwrap();
        assert!(matches!(out, Outcome::Published { .. }));
        assert_eq!(f.tip(), f.head);
        assert_eq!(git(&f.remote, &["rev-parse", "refs/heads/main"]), f.base);
        let config = git(
            &broker.root.join("publish-one/repository.git"),
            &["config", "--local", "--list"],
        );
        assert!(
            !config.contains("extraheader")
                && !config.contains("credential")
                && !config.contains("url=")
        );
    }
}
#[tokio::test]
async fn lost_publication_receipt_only_inspects_original_ref_and_changed_tip_stays_unknown() {
    let f = fixture(ObjectFormat::Sha1).await;
    let broker = f.broker();
    let stop = CancellationToken::new();
    let r = f.publication("lost-reply", None);
    assert!(matches!(
        broker
            .publish(r.clone(), &f.path, true, f.now(), &stop)
            .await
            .unwrap(),
        Outcome::Published { .. }
    ));
    let dir = broker.root.join(&r.id);
    std::fs::remove_file(dir.join("result.json")).unwrap();
    // A remote hook would fail any replay. Inspection still succeeds because it
    // sends only ls-remote, never receive-pack/push.
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let hook = f.remote.join("hooks/pre-receive");
        std::fs::write(&hook, "#!/bin/sh\nexit 99\n").unwrap();
        std::fs::set_permissions(hook, std::fs::Permissions::from_mode(0o700)).unwrap();
    }
    assert!(matches!(
        broker
            .publish(r.clone(), &f.path, false, f.now(), &stop)
            .await
            .unwrap(),
        Outcome::Published { .. }
    ));
    assert!(
        !dir.join("result.json").exists(),
        "Inspection must not create an effect receipt"
    );
    git(
        &f.remote,
        &["update-ref", "refs/heads/agents/result", &f.base, &f.head],
    );
    assert!(
        matches!(
            broker
                .publish(r, &f.path, true, f.now(), &stop)
                .await
                .unwrap(),
            Outcome::Unknown { .. }
        ),
        "Even an erroneous dispatch=true cannot replay an existing intent"
    );
    assert_eq!(f.tip(), f.base);
}
#[tokio::test]
async fn create_only_stale_and_non_fast_forward_publication_preserve_remote_work() {
    let f = fixture(ObjectFormat::Sha1).await;
    let b = f.broker();
    let stop = CancellationToken::new();
    git(
        &f.remote,
        &["update-ref", "refs/heads/agents/result", &f.base],
    );
    assert!(matches!(
        b.publish(
            f.publication("create-conflict", None),
            &f.path,
            true,
            f.now(),
            &stop
        )
        .await
        .unwrap(),
        Outcome::Rejected { .. }
    ));
    assert_eq!(f.tip(), f.base);
    assert!(matches!(
        b.publish(
            f.publication("stale-tip", Some(&f.head)),
            &f.path,
            true,
            f.now(),
            &stop
        )
        .await
        .unwrap(),
        Outcome::Rejected { .. }
    ));
    assert_eq!(f.tip(), f.base);
    assert!(matches!(
        b.publish(
            f.publication("fast-forward", Some(&f.base)),
            &f.path,
            true,
            f.now(),
            &stop
        )
        .await
        .unwrap(),
        Outcome::Published { .. }
    ));
    assert_eq!(f.tip(), f.head);
    git(&f.source, &["checkout", "-qb", "other", &f.base]);
    std::fs::write(f.source.join("other.txt"), "other").unwrap();
    git(&f.source, &["add", "other.txt"]);
    git(&f.source, &["commit", "-qm", "other"]);
    let other = git(&f.source, &["rev-parse", "HEAD"]);
    git(
        &f.remote,
        &[
            "fetch",
            "-q",
            f.source.to_str().unwrap(),
            "other:refs/heads/other",
        ],
    );
    // Explicit fixture setup simulates an independently edited remote.
    git(
        &f.remote,
        &["update-ref", "refs/heads/agents/result", &other, &f.head],
    );
    let mut request = f.publication("divergent", Some(&other));
    request.bundle.request.submitted_commit = f.head.clone();
    assert!(matches!(
        b.publish(request, &f.path, true, f.now(), &stop)
            .await
            .unwrap(),
        Outcome::Rejected { .. }
    ));
    assert_eq!(f.tip(), other);
}
#[tokio::test]
async fn denied_scope_expiry_corrupt_artifact_and_absent_intent_never_publish() {
    let f = fixture(ObjectFormat::Sha1).await;
    let b = f.broker();
    let stop = CancellationToken::new();
    let mut r = f.publication("deny-scope", None);
    r.branch = "main".into();
    assert!(b.publish(r, &f.path, true, f.now(), &stop).await.is_err());
    assert!(!b.root.join("deny-scope").exists());
    let mut r = f.publication("deny-repo", None);
    r.bundle.request.repository = "other/repo".into();
    assert!(b.publish(r, &f.path, true, f.now(), &stop).await.is_err());
    let mut g = grant();
    g.revoked = true;
    let revoked = Broker::new(&f.root.join("revoked"), g)
        .unwrap()
        .with_fixture("org/game", &f.remote)
        .unwrap();
    assert!(revoked
        .publish(
            f.publication("revoked", None),
            &f.path,
            true,
            f.now(),
            &stop
        )
        .await
        .is_err());
    let r = f.publication("absent", None);
    let dir = b.root.join(&r.id);
    std::fs::create_dir(&dir).unwrap();
    assert!(matches!(
        b.publish(r, &f.path, false, f.now(), &stop).await.unwrap(),
        Outcome::Unknown {
            observed_head: None,
            ..
        }
    ));
    assert!(!dir.join("request.json").exists());
    let corrupt = f.root.join("corrupt.bundle");
    let mut bytes = std::fs::read(&f.path).unwrap();
    bytes[0] ^= 1;
    std::fs::write(&corrupt, bytes).unwrap();
    assert!(b
        .publish(
            f.publication("corrupt", None),
            &corrupt,
            true,
            f.now(),
            &stop
        )
        .await
        .is_err());
    assert_eq!(
        git(&f.remote, &["for-each-ref", "--format=%(refname)"]),
        "refs/heads/main"
    );
}
#[tokio::test]
async fn cancelled_and_failed_commands_do_not_expose_raw_diagnostics() {
    let tmp = tempfile::tempdir().unwrap();
    let root = tmp.path().canonicalize().unwrap();
    let stop = CancellationToken::new();
    let e = process::run(
        &root,
        &["definitely-not-a-command-synthetic-secret".into()],
        None,
        "https://github.com/",
        None,
        &stop,
        Duration::from_secs(2),
    )
    .await
    .unwrap_err();
    assert!(!e.to_string().contains("synthetic-secret"));
    stop.cancel();
    assert!(process::run(
        &root,
        &["version".into()],
        None,
        "https://github.com/",
        None,
        &stop,
        Duration::from_secs(2)
    )
    .await
    .is_err());
}
#[tokio::test]
async fn readonly_staging_denies_unlisted_ref_and_incomplete_export_is_not_replayed() {
    let f = fixture(ObjectFormat::Sha1).await;
    let b = f.broker();
    let stop = CancellationToken::new();
    let source = Source {
        id: "read-only".into(),
        grant_digest: grant().digest().unwrap(),
        reference: "refs/heads/main".into(),
        export: Export {
            operation_id: "read-only".into(),
            base_commit: f.base.clone(),
            submitted_commit: f.base.clone(),
            ..f.bundle.request.clone()
        },
    };
    let mut wrong = source.clone();
    wrong.reference = "refs/heads/private".into();
    assert!(b.stage(wrong, true, f.now(), &stop).await.is_err());
    assert!(!b.root.join("read-only").exists());
    let dir = b.root.join("read-only");
    std::fs::create_dir(&dir).unwrap();
    atomic_new(&dir.join("source.json"), &source).unwrap();
    assert!(b.stage(source, false, f.now(), &stop).await.is_err());
    assert!(
        !dir.join("source").exists(),
        "Read recovery must not create a replacement checkout"
    );
}
#[tokio::test]
async fn expired_grant_and_changed_operation_identity_preserve_original_state() {
    let f = fixture(ObjectFormat::Sha1).await;
    let stop = CancellationToken::new();
    let mut g = grant();
    g.expires_at_ms = Some(f.now() - 1);
    let b = Broker::new(&f.root.join("expired"), g.clone())
        .unwrap()
        .with_fixture("org/game", &f.remote)
        .unwrap();
    let mut r = f.publication("expired", None);
    r.grant_digest = g.digest().unwrap();
    assert!(b.publish(r, &f.path, true, f.now(), &stop).await.is_err());
    assert!(!b.root.join("expired").exists());
    let b = f.broker();
    let r = f.publication("original", None);
    let outcome = b
        .publish(r.clone(), &f.path, true, f.now(), &stop)
        .await
        .unwrap();
    outcome.require_request(&r).unwrap();
    let mut changed = r.clone();
    changed.branch = "agents/other".into();
    assert!(outcome.require_request(&changed).is_err());
    assert!(b
        .publish(changed, &f.path, true, f.now(), &stop)
        .await
        .is_err());
    assert_eq!(f.tip(), f.head);
}
#[tokio::test]
async fn repository_state_symlinks_and_competing_publication_owners_reject() {
    let f = fixture(ObjectFormat::Sha1).await;
    let b = f.broker();
    let stop = CancellationToken::new();
    let r = f.publication("owned", None);
    let dir = b.directory(&r.id).unwrap();
    std::fs::create_dir(&dir).unwrap();
    let held = std::fs::OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .open(dir.join("operation.lock"))
        .unwrap();
    fs2::FileExt::try_lock_exclusive(&held).unwrap();
    assert!(b.publish(r, &f.path, true, f.now(), &stop).await.is_err());
    assert!(!dir.join("request.json").exists());
    fs2::FileExt::unlock(&held).unwrap();
    #[cfg(unix)]
    {
        let path = b.root.join("linked");
        std::os::unix::fs::symlink(&dir, &path).unwrap();
        assert!(b
            .publish(f.publication("linked", None), &f.path, true, f.now(), &stop)
            .await
            .is_err());
    }
    assert_eq!(
        git(&f.remote, &["for-each-ref", "--format=%(refname)"]),
        "refs/heads/main"
    );
}
#[tokio::test]
async fn failover_reads_original_remote_without_requiring_the_old_node_cache() {
    let f = fixture(ObjectFormat::Sha1).await;
    let broker = f.broker();
    let r = f.publication("failover", None);
    let stop = CancellationToken::new();
    assert!(matches!(
        broker
            .publish(r.clone(), &f.path, true, f.now(), &stop)
            .await
            .unwrap(),
        Outcome::Published { .. }
    ));
    let replacement = Broker::new(&f.root.join("replacement-node"), grant())
        .unwrap()
        .with_fixture("org/game", &f.remote)
        .unwrap();
    assert!(matches!(
        replacement
            .publish(r, &f.root.join("absent-artifact"), false, f.now(), &stop)
            .await
            .unwrap(),
        Outcome::Published { .. }
    ));
    assert_eq!(
        std::fs::read_dir(replacement.root).unwrap().count(),
        0,
        "Inspection must not fabricate a local dispatch marker"
    );
}
#[tokio::test]
async fn revoked_authority_at_the_last_checkpoint_prevents_push_and_retry_only_observes() {
    let f = fixture(ObjectFormat::Sha1).await;
    let broker = f.broker();
    let r = f.publication("last-check", None);
    let stop = CancellationToken::new();
    let count = std::sync::atomic::AtomicUsize::new(0);
    let result = broker
        .publish_checked(r.clone(), &f.path, true, f.now(), &stop, || {
            ensure!(
                count.fetch_add(1, std::sync::atomic::Ordering::SeqCst) == 0,
                "Synthetic authorization revoked"
            );
            Ok(())
        })
        .await;
    assert!(result.is_err());
    assert_eq!(count.load(std::sync::atomic::Ordering::SeqCst), 2);
    assert_eq!(
        git(&f.remote, &["for-each-ref", "--format=%(refname)"]),
        "refs/heads/main"
    );
    assert!(matches!(
        broker
            .publish(r, &f.path, false, f.now(), &stop)
            .await
            .unwrap(),
        Outcome::Unknown {
            observed_head: None,
            ..
        }
    ));
}

/// Real TLS, Git HTTP and ephemeral auth; no production GitHub token is used.
#[tokio::test]
#[ignore = "requires loopback networking, Python and openssl fixture"]
async fn authenticated_https_staging_and_publication_use_exact_origin_and_verified_tls() {
    use tokio::io::{AsyncBufReadExt, BufReader};
    let f = fixture(ObjectFormat::Sha1).await;
    let remote_root = f.root.join("https");
    std::fs::create_dir_all(remote_root.join("org")).unwrap();
    git(
        &f.root,
        &[
            "clone",
            "--bare",
            "-q",
            f.remote.to_str().unwrap(),
            remote_root.join("org/game.git").to_str().unwrap(),
        ],
    );
    git(
        &remote_root,
        &[
            "--git-dir=org/game.git",
            "config",
            "http.receivepack",
            "true",
        ],
    );
    let cert = f.root.join("server.crt");
    let key = f.root.join("server.key");
    let conf = f.root.join("openssl.cnf");
    std::fs::write(&conf,"[req]\ndistinguished_name=dn\nx509_extensions=ext\nprompt=no\n[dn]\nCN=127.0.0.1\n[ext]\nsubjectAltName=IP:127.0.0.1\nbasicConstraints=critical,CA:TRUE\nkeyUsage=critical,digitalSignature,keyEncipherment,keyCertSign\n").unwrap();
    assert!(std::process::Command::new("/usr/bin/openssl")
        .args(["req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-config"])
        .arg(&conf)
        .arg("-out")
        .arg(&cert)
        .arg("-keyout")
        .arg(&key)
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .status()
        .unwrap()
        .success());
    let token = "synthetic-loopback-only-broker-fixture";
    let cfg = f.root.join("https.json");
    std::fs::write(
        &cfg,
        serde_json::to_vec(
            &serde_json::json!({"root":remote_root,"cert":cert,"key":key,"token":token}),
        )
        .unwrap(),
    )
    .unwrap();
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        for p in [&cfg, &key] {
            std::fs::set_permissions(p, std::fs::Permissions::from_mode(0o600)).unwrap();
        }
    }
    let mut server = tokio::process::Command::new("python3")
        .arg(Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/https.py"))
        .arg(cfg)
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::null())
        .kill_on_drop(true)
        .spawn()
        .unwrap();
    let mut line = String::new();
    tokio::time::timeout(
        Duration::from_secs(10),
        BufReader::new(server.stdout.take().unwrap()).read_line(&mut line),
    )
    .await
    .unwrap()
    .unwrap();
    let port = line.trim().parse::<u16>().unwrap();
    let mut grant = grant();
    grant.credential_env = "OW_BROKER_SYNTHETIC_TLS_TEST_ONLY".into();
    std::env::set_var(&grant.credential_env, token);
    let broker = Broker::new(&f.root.join("tls-broker"), grant.clone())
        .unwrap()
        .with_https_fixture(port, &cert)
        .unwrap();
    let source = Source {
        id: "https-source".into(),
        grant_digest: grant.digest().unwrap(),
        reference: "refs/heads/main".into(),
        export: Export {
            operation_id: "https-source".into(),
            base_commit: f.base.clone(),
            submitted_commit: f.base.clone(),
            ..f.bundle.request.clone()
        },
    };
    let stop = CancellationToken::new();
    let staged = broker
        .stage(source.clone(), true, f.now(), &stop)
        .await
        .unwrap();
    assert_eq!(staged.request.submitted_commit, f.base);
    assert_eq!(
        broker.stage(source, false, f.now(), &stop).await.unwrap(),
        staged
    );
    let mut request = f.publication("https-publish", None);
    request.grant_digest = grant.digest().unwrap();
    assert!(matches!(
        broker
            .publish(request.clone(), &f.path, true, f.now(), &stop)
            .await
            .unwrap(),
        Outcome::Published { .. }
    ));
    assert_eq!(
        git(
            &remote_root,
            &[
                "--git-dir=org/game.git",
                "rev-parse",
                "refs/heads/agents/result"
            ]
        ),
        f.head
    );
    let status = || {
        let out = std::process::Command::new("/usr/bin/curl")
            .args(["--fail", "--silent", "--cacert"])
            .arg(&cert)
            .arg(format!("https://127.0.0.1:{port}/status"))
            .output()
            .unwrap();
        assert!(out.status.success());
        serde_json::from_slice::<serde_json::Value>(&out.stdout).unwrap()
    };
    let before = status();
    assert!(before["authorized"].as_u64().unwrap() > 0);
    assert_eq!(before["receive"], 1);
    assert!(matches!(
        broker
            .publish(request, &f.path, false, f.now(), &stop)
            .await
            .unwrap(),
        Outcome::Published { .. }
    ));
    assert_eq!(status()["receive"], 1);
    std::env::set_var(&grant.credential_env, "synthetic-invalid");
    let failed = broker
        .stage(
            Source {
                id: "bad-auth".into(),
                grant_digest: grant.digest().unwrap(),
                reference: "refs/heads/main".into(),
                export: Export {
                    operation_id: "bad-auth".into(),
                    base_commit: f.base.clone(),
                    submitted_commit: f.base.clone(),
                    ..f.bundle.request.clone()
                },
            },
            true,
            f.now(),
            &stop,
        )
        .await
        .unwrap_err();
    assert!(!failed.to_string().contains(token));
    assert!(status()["denied"].as_u64().unwrap() > 0);
    std::env::remove_var(&grant.credential_env);
    server.kill().await.unwrap();
}
