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
