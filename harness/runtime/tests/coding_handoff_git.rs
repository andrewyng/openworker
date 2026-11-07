#![cfg(unix)]
//! Real Git/filesystem contracts. These do not publish artifacts or provision
//! provider environments; the later C4 controller suite must qualify that path.
use openworker_runtime::coding_handoff::{
    git::{export, validate, Export},
    ObjectFormat,
};
use std::{
    path::{Path, PathBuf},
    process::Command,
};
fn git(root: &Path, args: &[&str]) -> String {
    let out = Command::new("/usr/bin/git")
        .args([
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=f@example.invalid",
        ])
        .args(args)
        .current_dir(root)
        .env("GIT_CONFIG_NOSYSTEM", "1")
        .env("GIT_CONFIG_GLOBAL", "/dev/null")
        .env("GIT_TERMINAL_PROMPT", "0")
        .output()
        .unwrap();
    assert!(
        out.status.success(),
        "{}",
        String::from_utf8_lossy(&out.stderr)
    );
    String::from_utf8(out.stdout).unwrap().trim().into()
}
struct Fixture {
    root: tempfile::TempDir,
    repo: PathBuf,
    request: Export,
}
fn fixture(format: ObjectFormat) -> Fixture {
    let root = tempfile::tempdir().unwrap();
    let repo = root.path().join("source");
    std::fs::create_dir(&repo).unwrap();
    git(
        &repo,
        &[
            "init",
            "-q",
            &format!("--object-format={}", format.git_name()),
        ],
    );
    std::fs::write(repo.join("old.txt"), "original\n").unwrap();
    std::fs::write(repo.join("gone.txt"), "remove me\n").unwrap();
    git(&repo, &["add", "old.txt", "gone.txt"]);
    git(&repo, &["commit", "-qm", "base"]);
    let base = git(&repo, &["rev-parse", "HEAD"]);
    std::fs::rename(repo.join("old.txt"), repo.join("renamed.txt")).unwrap();
    std::fs::remove_file(repo.join("gone.txt")).unwrap();
    std::fs::write(repo.join("binary.bin"), [0, 255, 1, 0, 200]).unwrap();
    std::fs::write(repo.join("space name.txt"), "new\n").unwrap();
    git(&repo, &["add", "-A"]);
    git(&repo, &["commit", "-qm", "submission"]);
    let submitted = git(&repo, &["rev-parse", "HEAD"]);
    Fixture {
        repo,
        root,
        request: Export {
            operation_id: "handoff_one".into(),
            scope_digest: "a".repeat(64),
            repository: "org/game".into(),
            directory: ".".into(),
            object_format: format,
            base_commit: base,
            submitted_commit: submitted,
        },
    }
}
#[tokio::test]
async fn bundles_preserve_exact_revisions_binary_renamed_deleted_and_new_files() {
    for format in [ObjectFormat::Sha1, ObjectFormat::Sha256] {
        let f = fixture(format);
        let original_head = git(&f.repo, &["rev-parse", "HEAD"]);
        let receipt = export(&f.repo, f.request.clone(), false, None)
            .await
            .unwrap();
        assert!(receipt.size > 0);
        assert_eq!(git(&f.repo, &["rev-parse", "HEAD"]), original_head);
        let bundle = f.repo.join(&receipt.path);
        let target = f.root.path().join("receiver");
        std::fs::create_dir(&target).unwrap();
        git(
            &target,
            &[
                "init",
                "-q",
                &format!("--object-format={}", format.git_name()),
            ],
        );
        git(
            &target,
            &[
                "-c",
                "protocol.file.allow=always",
                "fetch",
                "--no-tags",
                "--no-write-fetch-head",
                bundle.to_str().unwrap(),
                &format!("{}:refs/heads/work", receipt.advertised_ref),
            ],
        );
        git(
            &target,
            &["checkout", "--detach", &f.request.submitted_commit],
        );
        assert_eq!(
            std::fs::read(target.join("binary.bin")).unwrap(),
            [0, 255, 1, 0, 200]
        );
        assert_eq!(
            std::fs::read_to_string(target.join("renamed.txt")).unwrap(),
            "original\n"
        );
        assert!(!target.join("old.txt").exists() && !target.join("gone.txt").exists());
        assert!(target.join("space name.txt").exists());
        assert!(target.join(".git").is_dir());
        assert_ne!(
            git(&target, &["rev-parse", "--absolute-git-dir"]),
            git(&f.repo, &["rev-parse", "--absolute-git-dir"])
        );
        // Re-entry inspects a stable original artifact; HEAD may subsequently
        // advance without changing the original submitted revision or ref.
        std::fs::write(f.repo.join("later.txt"), "later").unwrap();
        git(&f.repo, &["add", "later.txt"]);
        git(&f.repo, &["commit", "-qm", "later"]);
        assert_eq!(
            export(&f.repo, f.request.clone(), true, None)
                .await
                .unwrap(),
            receipt
        );
        assert_eq!(
            export(&f.repo, f.request.clone(), false, None)
                .await
                .unwrap(),
            receipt
        );
    }
}
#[tokio::test]
async fn inspection_never_creates_missing_markers_or_replays_incomplete_export() {
    let f = fixture(ObjectFormat::Sha1);
    assert!(export(&f.repo, f.request.clone(), true, None)
        .await
        .is_err());
    let op = f.repo.join(".openworker/handoffs/handoff_one");
    assert!(!op.exists());
    std::fs::create_dir_all(&op).unwrap();
    assert!(export(&f.repo, f.request.clone(), true, None)
        .await
        .is_err());
    assert!(!op.join("request.json").exists());
    let good = export(&f.repo, f.request.clone(), false, None)
        .await
        .unwrap();
    let proof = std::fs::read(op.join("receipt.json")).unwrap();
    std::fs::remove_file(f.repo.join(&good.path)).unwrap();
    assert!(export(&f.repo, f.request.clone(), true, None)
        .await
        .is_err());
    assert!(export(&f.repo, f.request.clone(), false, None)
        .await
        .is_err());
    assert!(!f.repo.join(good.path).exists());
    assert_eq!(std::fs::read(op.join("receipt.json")).unwrap(), proof);
}
#[tokio::test]
async fn wrong_intent_corrupt_bytes_and_marker_symlinks_cannot_reuse_an_export() {
    let f = fixture(ObjectFormat::Sha1);
    let receipt = export(&f.repo, f.request.clone(), false, None)
        .await
        .unwrap();
    let mut changed = f.request.clone();
    changed.base_commit = changed.submitted_commit.clone();
    assert!(export(&f.repo, changed, false, None).await.is_err());
    let path = f.repo.join(&receipt.path);
    let mut bytes = std::fs::read(&path).unwrap();
    bytes.truncate(bytes.len() / 2);
    std::fs::write(&path, &bytes).unwrap();
    assert!(export(&f.repo, f.request.clone(), true, None)
        .await
        .is_err());
    let mut other = f.request.clone();
    other.operation_id = "handoff_two".into();
    let op = f.repo.join(".openworker/handoffs/handoff_two");
    std::fs::create_dir(&op).unwrap();
    let outside = f.root.path().join("outside.json");
    std::fs::write(&outside, "do not read/adopt me").unwrap();
    std::os::unix::fs::symlink(&outside, op.join("request.json")).unwrap();
    assert!(export(&f.repo, other, false, None).await.is_err());
    assert_eq!(
        std::fs::read_to_string(outside).unwrap(),
        "do not read/adopt me"
    );
}
#[tokio::test]
async fn source_helpers_alternates_and_replacement_objects_reject_before_export_mutation() {
    for mode in ["helper", "alternates", "replace"] {
        let f = fixture(ObjectFormat::Sha1);
        match mode {
            "helper" => {
                git(
                    &f.repo,
                    &["config", "filter.bad.clean", "touch ../helper-ran"],
                );
            }
            "alternates" => {
                std::fs::write(
                    f.repo.join(".git/objects/info/alternates"),
                    "/outside/objects\n",
                )
                .unwrap();
            }
            _ => {
                git(
                    &f.repo,
                    &[
                        "replace",
                        &f.request.base_commit,
                        &f.request.submitted_commit,
                    ],
                );
            }
        }
        assert!(export(&f.repo, f.request.clone(), false, None)
            .await
            .is_err());
        assert!(!f
            .repo
            .join(".openworker/handoffs/handoff_one/request.json")
            .exists());
        assert!(!f.root.path().join("helper-ran").exists());
    }
}
#[tokio::test]
async fn incremental_prerequisites_extra_refs_and_unreachable_objects_are_rejected() {
    let f = fixture(ObjectFormat::Sha1);
    let receipt = export(&f.repo, f.request.clone(), false, None)
        .await
        .unwrap();
    let incremental = f.root.path().join("incremental.bundle");
    git(
        &f.repo,
        &[
            "bundle",
            "create",
            incremental.to_str().unwrap(),
            "HEAD",
            &format!("^{}", f.request.base_commit),
        ],
    );
    assert!(validate(&incremental, &f.request, "HEAD").await.is_err());
    // Build an unrelated branch, include it in a malicious bundle, then strip
    // only its advertised ref. Its extra objects must still be detected.
    git(
        &f.repo,
        &["checkout", "-qb", "unrelated", &f.request.base_commit],
    );
    std::fs::write(f.repo.join("unrelated.txt"), "not submitted").unwrap();
    git(&f.repo, &["add", "unrelated.txt"]);
    git(&f.repo, &["commit", "-qm", "unrelated"]);
    git(
        &f.repo,
        &["checkout", "--detach", &f.request.submitted_commit],
    );
    let all = f.root.path().join("all.bundle");
    git(
        &f.repo,
        &["bundle", "create", all.to_str().unwrap(), "--all"],
    );
    assert!(validate(&all, &f.request, &receipt.advertised_ref)
        .await
        .is_err());
    let bytes = std::fs::read(&all).unwrap();
    let boundary = bytes.windows(2).position(|p| p == b"\n\n").unwrap();
    let header = std::str::from_utf8(&bytes[..boundary]).unwrap();
    let mut stripped = header
        .lines()
        .filter(|line| {
            line.starts_with('#')
                || line.starts_with('@')
                || line.ends_with(&format!(" {}", receipt.advertised_ref))
        })
        .collect::<Vec<_>>()
        .join("\n")
        .into_bytes();
    stripped.extend_from_slice(b"\n\n");
    stripped.extend_from_slice(&bytes[boundary + 2..]);
    let hidden = f.root.path().join("hidden-objects.bundle");
    std::fs::write(&hidden, stripped).unwrap();
    assert!(validate(&hidden, &f.request, &receipt.advertised_ref)
        .await
        .is_err());
}

#[tokio::test]
async fn replaced_repository_and_changed_original_ref_cannot_be_adopted() {
    let f = fixture(ObjectFormat::Sha1);
    let receipt = export(&f.repo, f.request.clone(), false, None)
        .await
        .unwrap();
    git(
        &f.repo,
        &[
            "update-ref",
            &receipt.advertised_ref,
            &f.request.base_commit,
            &f.request.submitted_commit,
        ],
    );
    assert!(export(&f.repo, f.request.clone(), true, None)
        .await
        .is_err());
    git(
        &f.repo,
        &[
            "update-ref",
            &receipt.advertised_ref,
            &f.request.submitted_commit,
            &f.request.base_commit,
        ],
    );
    let old = f.root.path().join("original-source");
    std::fs::rename(&f.repo, &old).unwrap();
    std::fs::create_dir(&f.repo).unwrap();
    git(&f.repo, &["init", "-q"]);
    git(
        &f.repo,
        &[
            "-c",
            "protocol.file.allow=always",
            "fetch",
            old.join(&receipt.path).to_str().unwrap(),
            &format!("{}:refs/heads/work", receipt.advertised_ref),
        ],
    );
    git(&f.repo, &["checkout", "-q", "work"]);
    let op = f.repo.join(".openworker/handoffs/handoff_one");
    std::fs::create_dir_all(&op).unwrap();
    std::fs::copy(
        old.join(".openworker/handoffs/handoff_one/request.json"),
        op.join("request.json"),
    )
    .unwrap();
    assert!(export(&f.repo, f.request.clone(), true, None)
        .await
        .is_err());
    assert!(!op.join("source.bundle").exists());
}

#[tokio::test]
async fn oversized_stream_stops_without_receipt_and_inspection_never_restarts_export() {
    use std::io::Write;
    let mut f = fixture(ObjectFormat::Sha1);
    let mut file = std::fs::File::create(f.repo.join("large.bin")).unwrap();
    let mut state = 0x1234567812345678u64;
    let mut chunk = vec![0; 65536];
    // Deterministic incompressible fixture, not credential/randomness generation.
    for _ in 0..(65 * 1024 * 1024 / chunk.len()) {
        for part in chunk.chunks_exact_mut(8) {
            state ^= state << 13;
            state ^= state >> 7;
            state ^= state << 17;
            part.copy_from_slice(&state.to_le_bytes());
        }
        file.write_all(&chunk).unwrap();
    }
    file.sync_all().unwrap();
    drop(file);
    git(&f.repo, &["add", "large.bin"]);
    git(&f.repo, &["commit", "-qm", "large source"]);
    f.request.submitted_commit = git(&f.repo, &["rev-parse", "HEAD"]);
    let error = export(&f.repo, f.request.clone(), false, None)
        .await
        .unwrap_err();
    assert!(error.to_string().contains("limit"), "{error:#}");
    let op = f.repo.join(".openworker/handoffs/handoff_one");
    assert!(!op.join("receipt.json").exists() && !op.join("source.bundle").exists());
    assert!(std::fs::metadata(op.join("source.part")).unwrap().len() <= 64 * 1024 * 1024);
    assert!(export(&f.repo, f.request.clone(), false, None)
        .await
        .is_err());
    assert!(!op.join("source.bundle").exists());
}

use openworker_runtime::coding_handoff::{
    transfer::{self, Transfer, CHUNK},
    ArtifactRef, Import, Kind, Manifest, Producer,
};
async fn exported(f: &Fixture, directory: &str) -> Transfer {
    let b = export(&f.repo, f.request.clone(), false, None)
        .await
        .unwrap();
    let manifest = Manifest {
        version: 1,
        kind: Kind::Submission,
        repository: f.request.repository.clone(),
        producer: Producer {
            team_id: "at_one".into(),
            agent_id: "builder_one".into(),
            session_id: "s_one".into(),
        },
        object_format: f.request.object_format,
        base_commit: f.request.base_commit.clone(),
        submitted_commit: f.request.submitted_commit.clone(),
        artifact: ArtifactRef {
            session_id: "s_one".into(),
            artifact_id: "a_one".into(),
            size: b.size,
            sha256: b.sha256.clone(),
        },
        reports: vec![],
    };
    Transfer {
        request: Import {
            operation_id: "receive_one".into(),
            scope_digest: "b".repeat(64),
            manifest_digest: manifest.digest().unwrap(),
            directory: directory.into(),
        },
        manifest,
        bundle: b,
    }
}
async fn stage(f: &Fixture, target: &Path, t: &Transfer) {
    let status = transfer::prepare(target, t.clone(), false).await.unwrap();
    assert_eq!(status.received, 0);
    let mut offset = 0;
    while offset < t.bundle.size {
        let bytes = transfer::read_chunk(&f.repo, &t.bundle, offset)
            .await
            .unwrap();
        assert!(bytes.len() <= CHUNK);
        let status = transfer::write_chunk(target, &t.handle(), offset, &bytes)
            .await
            .unwrap();
        let replay = transfer::write_chunk(target, &t.handle(), offset, &bytes)
            .await
            .unwrap();
        assert_eq!(status, replay); // Immutable chunk receipt after a lost reply.
        offset += bytes.len() as u64;
        assert_eq!(status.received, offset);
    }
}
#[tokio::test]
async fn private_chunks_and_independent_checkout_preserve_both_object_formats_and_lost_ack() {
    for format in [ObjectFormat::Sha1, ObjectFormat::Sha256] {
        let f = fixture(format);
        let t = exported(&f, "project").await;
        let target = tempfile::tempdir().unwrap();
        stage(&f, target.path(), &t).await;
        assert!(transfer::finish(target.path(), &t.handle(), true)
            .await
            .is_err());
        assert!(!target.path().join("project").exists());
        let r = transfer::finish(target.path(), &t.handle(), false)
            .await
            .unwrap();
        assert_eq!(
            r,
            transfer::finish(target.path(), &t.handle(), true)
                .await
                .unwrap()
        );
        assert_eq!(r.commit, t.manifest.submitted_commit);
        assert_eq!(
            git(&r.path, &["symbolic-ref", "HEAD"]),
            "refs/heads/openworker/import"
        );
        assert_eq!(
            git(&r.path, &["rev-parse", "--show-object-format"]),
            format.git_name()
        );
        assert_eq!(
            git(&r.path, &["rev-parse", "--absolute-git-dir"]),
            r.path.join(".git").to_str().unwrap()
        );
        assert_eq!(
            std::fs::read(r.path.join("binary.bin")).unwrap(),
            [0, 255, 1, 0, 200]
        );
        assert!(r.path.join("renamed.txt").is_file());
        assert!(!r.path.join("gone.txt").exists());
        assert!(!r.path.join("old.txt").exists());
        assert_eq!(git(&r.path, &["remote"]), "");
        assert_eq!(
            transfer::inspect(target.path(), &t.handle())
                .await
                .unwrap()
                .receipt,
            Some(r.clone())
        );
        git(&r.path, &["checkout", "--quiet", &t.manifest.base_commit]);
        assert!(transfer::finish(target.path(), &t.handle(), false)
            .await
            .is_err());
        assert_eq!(git(&r.path, &["rev-parse", "HEAD"]), t.manifest.base_commit);
        // Never reset it.
    }
}
#[tokio::test]
async fn private_transfer_refuses_changed_identity_chunks_holes_and_symlink_markers() {
    let f = fixture(ObjectFormat::Sha1);
    let t = exported(&f, "project").await;
    let target = tempfile::tempdir().unwrap();
    assert!(transfer::prepare(target.path(), t.clone(), true)
        .await
        .is_err());
    assert!(!target.path().join(".openworker").exists());
    transfer::prepare(target.path(), t.clone(), false)
        .await
        .unwrap();
    let mut changed = t.clone();
    changed.request.directory = "other".into();
    assert!(transfer::prepare(target.path(), changed, false)
        .await
        .is_err());
    let mut handle = t.handle();
    handle.scope_digest = "c".repeat(64);
    assert!(transfer::inspect(target.path(), &handle).await.is_err());
    let bytes = transfer::read_chunk(&f.repo, &t.bundle, 0).await.unwrap();
    assert!(transfer::write_chunk(target.path(), &t.handle(), 1, &bytes)
        .await
        .is_err());
    assert!(
        transfer::write_chunk(target.path(), &t.handle(), 0, &bytes[..bytes.len() - 1])
            .await
            .is_err()
    );
    assert!(
        transfer::write_chunk(target.path(), &t.handle(), CHUNK as u64, &bytes)
            .await
            .is_err()
    );
    transfer::write_chunk(target.path(), &t.handle(), 0, &bytes)
        .await
        .unwrap();
    let mut changed = bytes;
    changed[0] ^= 1;
    assert!(
        transfer::write_chunk(target.path(), &t.handle(), 0, &changed)
            .await
            .is_err()
    );
    let marker = target
        .path()
        .join(".openworker/imports/receive_one/request.json");
    let original = std::fs::read(&marker).unwrap();
    std::fs::remove_file(&marker).unwrap();
    let foreign = target.path().join("foreign");
    std::fs::write(&foreign, original).unwrap();
    std::os::unix::fs::symlink(&foreign, &marker).unwrap();
    assert!(transfer::inspect(target.path(), &t.handle()).await.is_err());
    assert!(transfer::finish(target.path(), &t.handle(), false)
        .await
        .is_err());
    assert!(!target.path().join("project").exists());
}
#[tokio::test]
async fn original_checkout_dispatch_is_not_replayed_and_existing_folders_survive() {
    let f = fixture(ObjectFormat::Sha1);
    let t = exported(&f, "project").await;
    let target = tempfile::tempdir().unwrap();
    stage(&f, target.path(), &t).await;
    let marker = target
        .path()
        .join(".openworker/imports/receive_one/checkout.json");
    std::fs::write(&marker, "dispatch persisted, acknowledgement lost").unwrap();
    assert!(
        transfer::inspect(target.path(), &t.handle())
            .await
            .unwrap()
            .checkout_dispatched
    );
    assert!(transfer::finish(target.path(), &t.handle(), false)
        .await
        .is_err());
    assert!(!target.path().join("project").exists());
    let target = tempfile::tempdir().unwrap();
    stage(&f, target.path(), &t).await;
    std::fs::create_dir(target.path().join("project")).unwrap();
    std::fs::write(target.path().join("project/keep"), "leave me").unwrap();
    assert!(transfer::finish(target.path(), &t.handle(), false)
        .await
        .is_err());
    assert_eq!(
        std::fs::read_to_string(target.path().join("project/keep")).unwrap(),
        "leave me"
    );
    assert!(!target.path().join("project/.git").exists());
}
#[tokio::test]
async fn transferred_bytes_and_original_repository_identity_are_checked() {
    let f = fixture(ObjectFormat::Sha1);
    let t = exported(&f, "project").await;
    let target = tempfile::tempdir().unwrap();
    stage(&f, target.path(), &t).await;
    let chunk = target
        .path()
        .join(".openworker/imports/receive_one/chunk-00000000");
    let mut bytes = std::fs::read(&chunk).unwrap();
    let middle = bytes.len() / 2;
    bytes[middle] ^= 1;
    std::fs::write(chunk, bytes).unwrap();
    assert!(transfer::finish(target.path(), &t.handle(), false)
        .await
        .unwrap_err()
        .to_string()
        .contains("Imported bytes differ"));
    assert!(!target.path().join("project").exists());
    std::fs::rename(f.repo.join(".git"), f.repo.join("saved-git")).unwrap();
    git(&f.repo, &["init", "-q"]);
    assert!(transfer::read_chunk(&f.repo, &t.bundle, 0).await.is_err());
    let mut changed = t.clone();
    changed.bundle.sha256 = "c".repeat(64);
    assert!(changed.validate().is_err());
    for path in [
        ".openworker",
        ".openworker/imports/other",
        "../outside",
        "C:\\host",
    ] {
        let mut changed = t.clone();
        changed.request.directory = path.into();
        assert!(changed.validate().is_err());
    }
}
#[tokio::test]
async fn expanded_checkout_limit_stops_compressible_bundles_before_materializing_files() {
    let mut f = fixture(ObjectFormat::Sha1);
    let file = std::fs::File::create(f.repo.join("huge.bin")).unwrap();
    file.set_len(transfer::MAX_CHECKOUT_BYTES + 1).unwrap();
    git(&f.repo, &["add", "huge.bin"]);
    git(&f.repo, &["commit", "-qm", "compressible"]);
    f.request.submitted_commit = git(&f.repo, &["rev-parse", "HEAD"]);
    let t = exported(&f, "project").await;
    assert!(t.bundle.size < 1024 * 1024);
    let target = tempfile::tempdir().unwrap();
    stage(&f, target.path(), &t).await;
    let e = transfer::finish(target.path(), &t.handle(), false)
        .await
        .unwrap_err();
    assert!(e.to_string().contains("Expanded checkout exceeds"), "{e}");
    assert!(!target.path().join("project/huge.bin").exists());
    assert!(
        transfer::inspect(target.path(), &t.handle())
            .await
            .unwrap()
            .checkout_dispatched
    );
    assert!(transfer::finish(target.path(), &t.handle(), false)
        .await
        .is_err());
}

async fn private_rpc(root: &Path, method: &str, params: serde_json::Value) -> serde_json::Value {
    use tokio::io::AsyncWriteExt;
    let mut child = tokio::process::Command::new(env!("CARGO_BIN_EXE_openworker"))
        .args(["workspace-stdio", "--root"])
        .arg(root)
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        .kill_on_drop(true)
        .spawn()
        .unwrap();
    let frame = serde_json::json!({"jsonrpc":"2.0","id":1,"method":method,"params":params});
    let mut input = child.stdin.take().unwrap();
    input
        .write_all(format!("{frame}\n").as_bytes())
        .await
        .unwrap();
    input.shutdown().await.unwrap();
    drop(input);
    let out = tokio::time::timeout(std::time::Duration::from_secs(20), child.wait_with_output())
        .await
        .unwrap()
        .unwrap();
    assert!(
        out.status.success(),
        "{}",
        String::from_utf8_lossy(&out.stderr)
    );
    assert!(out.stdout.len() <= 64000);
    serde_json::from_slice(&out.stdout).unwrap()
}
#[tokio::test]
async fn private_stdio_transfers_chunks_across_processes_without_discovering_model_tools() {
    let f = fixture(ObjectFormat::Sha1);
    let t = exported(&f, "project").await;
    let target = tempfile::tempdir().unwrap();
    use serde_json::json;
    let p = private_rpc(target.path(), "handoff/prepare", json!(t)).await;
    assert_eq!(p["result"]["received"], 0, "{p}");
    let mut offset = 0;
    while offset < t.bundle.size {
        let r = private_rpc(
            &f.repo,
            "handoff/read",
            json!({"bundle":t.bundle,"offset":offset}),
        )
        .await;
        assert!(r.get("error").is_none(), "{r}");
        let w = private_rpc(
            target.path(),
            "handoff/write",
            json!({"handle":t.handle(),"offset":offset,"data":r["result"]["data"]}),
        )
        .await;
        assert!(w.get("error").is_none(), "{w}");
        offset = w["result"]["received"].as_u64().unwrap();
    }
    let r = private_rpc(target.path(), "handoff/finish", json!(t.handle())).await;
    assert_eq!(r["result"]["commit"], t.manifest.submitted_commit, "{r}");
    let recovered = private_rpc(target.path(), "handoff/inspect_checkout", json!(t.handle())).await;
    assert_eq!(recovered["result"], r["result"]);
    let workspace_request = openworker_runtime::team_workspace::Request {
        operation_id: t.request.operation_id.clone(),
        scope_digest: t.request.scope_digest.clone(),
        selection: openworker_runtime::team_workspace::Selection::Directory {
            path: t.request.directory.clone(),
        },
    };
    let ready = private_rpc(
        target.path(),
        "handoff/workspace",
        json!({"handle":t.handle(),"request":workspace_request}),
    )
    .await;
    let receipt: openworker_runtime::team_workspace::Receipt =
        serde_json::from_value(ready["result"].clone()).unwrap();
    receipt.verify().unwrap();
    assert!(receipt.independent_git.is_some());
    assert_eq!(
        private_rpc(target.path(), "workspace/verify", json!(receipt)).await["result"],
        ready["result"]
    );
    assert!(private_rpc(target.path(), "tools/list", json!({}))
        .await
        .get("error")
        .is_some());
    let bad = private_rpc(
        target.path(),
        "handoff/write",
        json!({"handle":t.handle(),"offset":0,"data":"AA==","host":"/tmp/other"}),
    )
    .await;
    assert!(bad.get("error").is_some());
    assert_eq!(
        std::fs::read(target.path().join("project/binary.bin")).unwrap(),
        [0, 255, 1, 0, 200]
    );
}

#[tokio::test]
async fn imported_workspace_pins_own_git_metadata_across_child_commits_and_launch_checks() {
    let f = fixture(ObjectFormat::Sha1);
    let t = exported(&f, "project").await;
    let target = tempfile::tempdir().unwrap();
    stage(&f, target.path(), &t).await;
    transfer::finish(target.path(), &t.handle(), false)
        .await
        .unwrap();
    let request = openworker_runtime::team_workspace::Request {
        operation_id: t.request.operation_id.clone(),
        scope_digest: t.request.scope_digest.clone(),
        selection: openworker_runtime::team_workspace::Selection::Directory {
            path: t.request.directory.clone(),
        },
    };
    let receipt = transfer::workspace_receipt(target.path(), &t.handle(), request.clone())
        .await
        .unwrap();
    assert!(receipt.independent_git.is_some());
    assert!(receipt.git_dir.is_none());
    receipt.verify().unwrap();
    let mut tools =
        openworker_runtime::coding::CodingTools::with_workspace(receipt.clone()).unwrap();
    tools
        .call(&receipt.path, "git_status", &serde_json::json!({}))
        .await
        .unwrap();
    // Builder changes to its own HEAD are normal after the import was admitted.
    std::fs::write(receipt.path.join("builder.txt"), "builder work").unwrap();
    git(&receipt.path, &["add", "builder.txt"]);
    git(&receipt.path, &["commit", "-qm", "builder work"]);
    receipt.verify().unwrap();
    tools
        .call(&receipt.path, "git_log", &serde_json::json!({"limit":1}))
        .await
        .unwrap();
    let mut changed = request;
    changed.scope_digest = "c".repeat(64);
    assert!(
        transfer::workspace_receipt(target.path(), &t.handle(), changed)
            .await
            .is_err()
    );
    let dot = receipt.path.join(".git");
    std::fs::rename(&dot, receipt.path.join("old-git")).unwrap();
    git(&receipt.path, &["init", "-q"]);
    assert!(receipt.verify().is_err());
    assert!(tools
        .call(
            &receipt.path,
            "read_file",
            &serde_json::json!({"path":"builder.txt"})
        )
        .await
        .is_err());
    tools.shutdown().await;
}
#[tokio::test]
async fn independent_workspace_cannot_acquire_external_git_objects_or_linked_metadata() {
    let f = fixture(ObjectFormat::Sha1);
    let t = exported(&f, "project").await;
    let target = tempfile::tempdir().unwrap();
    stage(&f, target.path(), &t).await;
    transfer::finish(target.path(), &t.handle(), false)
        .await
        .unwrap();
    let request = openworker_runtime::team_workspace::Request {
        operation_id: t.request.operation_id.clone(),
        scope_digest: t.request.scope_digest.clone(),
        selection: openworker_runtime::team_workspace::Selection::Directory {
            path: "project".into(),
        },
    };
    let receipt = transfer::workspace_receipt(target.path(), &t.handle(), request)
        .await
        .unwrap();
    let dot = receipt.path.join(".git");
    std::fs::write(dot.join("commondir"), f.repo.join(".git").to_str().unwrap()).unwrap();
    assert!(receipt.verify().is_err());
    std::fs::remove_file(dot.join("commondir")).unwrap();
    std::fs::write(
        dot.join("objects/info/alternates"),
        f.repo.join(".git/objects").to_str().unwrap(),
    )
    .unwrap();
    assert!(receipt.verify().is_err());
    std::fs::remove_file(dot.join("objects/info/alternates")).unwrap();
    std::fs::rename(dot.join("objects"), dot.join("saved-objects")).unwrap();
    std::os::unix::fs::symlink(f.repo.join(".git/objects"), dot.join("objects")).unwrap();
    assert!(receipt.verify().is_err());
    let mut mixed = receipt.clone();
    mixed.git_dir = Some(f.repo.join(".git"));
    assert!(mixed.verify().is_err());
}

#[tokio::test]
async fn root_repository_bookkeeping_is_excluded_from_normal_git_staging_without_changing_tracked_ignore_rules(
) {
    let f = fixture(ObjectFormat::Sha1);
    let exclude = f.repo.join(".git/info/exclude");
    std::fs::write(&exclude, b"user-pattern\n!/.openworker/\n").unwrap();
    std::fs::write(f.repo.join(".gitignore"), "user-build/\n").unwrap();
    git(&f.repo, &["add", ".gitignore"]);
    git(&f.repo, &["commit", "-qm", "user ignore"]);
    let before = std::fs::read(f.repo.join(".gitignore")).unwrap();
    let exported = export(&f.repo, f.request.clone(), false, None)
        .await
        .unwrap();
    assert!(!git(&f.repo, &["status", "--porcelain"]).contains(".openworker"));
    git(&f.repo, &["add", "-A"]);
    assert!(git(&f.repo, &["diff", "--cached", "--name-only"]).is_empty());
    assert_eq!(std::fs::read(f.repo.join(".gitignore")).unwrap(), before);
    let rules = std::fs::read(&exclude).unwrap();
    assert!(rules.starts_with(b"user-pattern\n!/.openworker/\n"));
    export(&f.repo, f.request.clone(), true, None)
        .await
        .unwrap();
    assert_eq!(std::fs::read(&exclude).unwrap(), rules); // Inspection cannot append.
    assert!(f.repo.join(&exported.path).exists());
    let g = fixture(ObjectFormat::Sha1);
    let foreign = g.root.path().join("foreign-exclude");
    std::fs::write(&foreign, b"keep me").unwrap();
    let target = g.repo.join(".git/info/exclude");
    std::fs::remove_file(&target).unwrap();
    std::os::unix::fs::symlink(&foreign, &target).unwrap();
    assert!(export(&g.repo, g.request.clone(), false, None)
        .await
        .is_err());
    assert_eq!(std::fs::read(&foreign).unwrap(), b"keep me");
}

async fn integration_fixture(
    root_repository: bool,
    format: ObjectFormat,
) -> (
    Fixture,
    PathBuf,
    openworker_runtime::coding_handoff::integrate::Request,
) {
    let f = fixture(format);
    let root = if root_repository {
        f.repo.clone()
    } else {
        f.root.path().canonicalize().unwrap()
    };
    let t = exported(&f, ".openworker-received/team/submission").await;
    stage(&f, &root, &t).await;
    transfer::finish(&root, &t.handle(), false).await.unwrap();
    let workspace = transfer::workspace_receipt(
        &root,
        &t.handle(),
        openworker_runtime::team_workspace::Request {
            operation_id: t.request.operation_id.clone(),
            scope_digest: t.request.scope_digest.clone(),
            selection: openworker_runtime::team_workspace::Selection::Directory {
                path: t.request.directory.clone(),
            },
        },
    )
    .await
    .unwrap();
    git(
        &f.repo,
        &["checkout", "-qb", "integrate-main", &f.request.base_commit],
    );
    let request = openworker_runtime::coding_handoff::integrate::Request {
        export: Export {
            operation_id: "integrate_one".into(),
            scope_digest: "d".repeat(64),
            directory: if root_repository {
                ".".into()
            } else {
                "source".into()
            },
            ..f.request.clone()
        },
        expected_head: f.request.base_commit.clone(),
        received: workspace,
    };
    (f, root, request)
}
#[tokio::test]
async fn explicit_integration_cas_preserves_exact_tree_and_lost_receipt_never_replays() {
    use openworker_runtime::coding_handoff::integrate::integrate;
    for (root_repo, format) in [(false, ObjectFormat::Sha1), (true, ObjectFormat::Sha256)] {
        let (f, root, request) = integration_fixture(root_repo, format).await;
        let result = integrate(&root, request.clone(), false).await.unwrap();
        assert_eq!(result.commit, f.request.submitted_commit);
        assert_eq!(git(&f.repo, &["rev-parse", "HEAD"]), result.commit);
        assert_eq!(
            std::fs::read(f.repo.join("binary.bin")).unwrap(),
            [0, 255, 1, 0, 200]
        );
        assert!(!f.repo.join("old.txt").exists() && !f.repo.join("gone.txt").exists());
        assert_eq!(git(&f.repo, &["status", "--porcelain"]), "");
        let original_log = git(
            &f.repo,
            &["reflog", "--format=%H", "refs/heads/integrate-main"],
        );
        let marker = root.join(".openworker/integrations/integrate_one/receipt.json");
        std::fs::remove_file(&marker).unwrap();
        assert_eq!(
            integrate(&root, request.clone(), true).await.unwrap(),
            result
        );
        assert!(!marker.exists()); // Inspection observes; it does not write a new acknowledgement.
        assert_eq!(
            git(
                &f.repo,
                &["reflog", "--format=%H", "refs/heads/integrate-main"]
            ),
            original_log
        );
        let mut changed = request;
        changed.expected_head = f.request.submitted_commit.clone();
        assert!(integrate(&root, changed, true).await.is_err());
    }
}
#[tokio::test]
async fn integration_refuses_dirty_stale_divergent_and_rewritten_sources_without_reset() {
    use openworker_runtime::coding_handoff::integrate::integrate;
    for mode in ["dirty", "stale", "divergent", "rewrite"] {
        let (f, root, mut request) = integration_fixture(false, ObjectFormat::Sha1).await;
        match mode {
            "dirty" => std::fs::write(f.repo.join("old.txt"), "preserve my work").unwrap(),
            "stale" => request.expected_head = f.request.submitted_commit.clone(),
            "divergent" => {
                std::fs::write(f.repo.join("divergent"), "mine").unwrap();
                git(&f.repo, &["add", "divergent"]);
                git(&f.repo, &["commit", "-qm", "other line"]);
                request.expected_head = git(&f.repo, &["rev-parse", "HEAD"]);
            }
            "rewrite" => {
                git(
                    &f.repo,
                    &["config", "url.https://example.invalid/.insteadOf", "/"],
                );
            }
            _ => unreachable!(),
        }
        let before = git(&f.repo, &["rev-parse", "HEAD"]);
        assert!(
            integrate(&root, request.clone(), false).await.is_err(),
            "{mode}"
        );
        assert_eq!(git(&f.repo, &["rev-parse", "HEAD"]), before);
        assert!(integrate(&root, request, true).await.is_err(), "{mode}");
        assert_eq!(git(&f.repo, &["rev-parse", "HEAD"]), before);
        if mode == "dirty" {
            assert_eq!(
                std::fs::read_to_string(f.repo.join("old.txt")).unwrap(),
                "preserve my work"
            )
        }
    }
}

#[tokio::test]
async fn integration_inspection_preserves_partial_index_files_when_branch_cas_was_not_acknowledged()
{
    use openworker_runtime::coding_handoff::integrate::{integrate, rpc};
    let (f, root, request) = integration_fixture(false, ObjectFormat::Sha1).await;
    integrate(&root, request.clone(), false).await.unwrap();
    // Recreate the observable crash boundary: index/files at submitted tree,
    // original branch ref at expected HEAD, no final acknowledgement receipt.
    git(
        &f.repo,
        &[
            "update-ref",
            "refs/heads/integrate-main",
            &request.expected_head,
            &request.export.submitted_commit,
        ],
    );
    let receipt = root.join(".openworker/integrations/integrate_one/receipt.json");
    std::fs::remove_file(&receipt).unwrap();
    let status = git(&f.repo, &["status", "--porcelain"]);
    assert!(!status.is_empty());
    assert!(rpc(&root, request.clone(), true).await.is_err());
    assert_eq!(git(&f.repo, &["rev-parse", "HEAD"]), request.expected_head);
    assert_eq!(git(&f.repo, &["status", "--porcelain"]), status);
    assert_eq!(
        std::fs::read(f.repo.join("binary.bin")).unwrap(),
        [0, 255, 1, 0, 200]
    );
    assert!(!receipt.exists());
}
#[tokio::test]
async fn known_integration_rejection_survives_lost_reply_and_is_not_retried_after_source_changes() {
    use openworker_runtime::coding_handoff::integrate::{rpc, Observation};
    let (f, root, mut request) = integration_fixture(false, ObjectFormat::Sha1).await;
    request.expected_head = "f".repeat(40);
    let rejected = rpc(&root, request.clone(), false).await.unwrap();
    assert!(matches!(&rejected,Observation::Rejected{reason,..} if reason.contains("stale")));
    std::fs::write(f.repo.join("old.txt"), "work after rejection").unwrap();
    let before = git(&f.repo, &["status", "--porcelain"]);
    assert_eq!(rpc(&root, request, true).await.unwrap(), rejected);
    assert_eq!(git(&f.repo, &["status", "--porcelain"]), before);
    assert_eq!(
        std::fs::read_to_string(f.repo.join("old.txt")).unwrap(),
        "work after rejection"
    );
}
