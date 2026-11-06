use openworker_runtime::coding::CodingTools;
use serde_json::json;
#[tokio::test]
async fn regex_glob_and_ignore_rules() {
    let tmp = tempfile::tempdir().unwrap();
    let r = tmp.path();
    let mut t = CodingTools::default();
    std::fs::write(r.join(".gitignore"), "ignored.rs\n").unwrap();
    for (name, body) in [
        ("a.rs", "fn Hello() {}\n"),
        ("b.txt", "fn Hello() {}\n"),
        ("ignored.rs", "fn Hello() {}\n"),
    ] {
        std::fs::write(r.join(name), body).unwrap();
    }
    let v = t
        .call(
            r,
            "search_files",
            &json!({"query":"^fn h[a-z]+","regex":true,"case_sensitive":false,"glob":"*.rs"}),
        )
        .await
        .unwrap();
    assert_eq!(v["matches"].as_array().unwrap().len(), 1);
    assert_eq!(v["matches"][0]["path"], "a.rs");
    let v = t
        .call(
            r,
            "list_files",
            &json!({"glob":"*.rs","respect_gitignore":false}),
        )
        .await
        .unwrap();
    assert_eq!(v["files"], json!(["a.rs", "ignored.rs"]));
    assert!(t
        .call(r, "search_files", &json!({"query":"[","regex":true}))
        .await
        .is_err());
    std::fs::create_dir(r.join("src")).unwrap();
    std::fs::write(r.join("src/ignored.rs"), "fn Hidden() {}\n").unwrap();
    std::fs::write(r.join("src/visible.rs"), "fn Visible() {}\n").unwrap();
    let scoped = t
        .call(
            r,
            "search_files",
            &json!({"path":"src","query":"fn","glob":"*.rs"}),
        )
        .await
        .unwrap();
    assert_eq!(scoped["matches"].as_array().unwrap().len(), 1);
    assert_eq!(
        std::path::Path::new(scoped["matches"][0]["path"].as_str().unwrap()),
        std::path::Path::new("src").join("visible.rs")
    );
}
#[tokio::test]
async fn codex_patch_prevalidates_every_file_and_rejects_escape() {
    let tmp = tempfile::tempdir().unwrap();
    let r = tmp.path();
    let mut t = CodingTools::default();
    std::fs::write(r.join("a.txt"), "one\ntwo\n").unwrap();
    let bad="*** Begin Patch\n*** Update File: a.txt\n@@\n one\n-two\n+three\n*** Update File: missing\n@@\n-no\n+yes\n*** End Patch";
    assert!(t
        .call(r, "apply_patch", &json!({"patch":bad}))
        .await
        .is_err());
    assert_eq!(
        std::fs::read_to_string(r.join("a.txt")).unwrap(),
        "one\ntwo\n"
    );
    let patch="*** Begin Patch\n*** Update File: a.txt\n@@\n one\n-two\n+three\n*** Add File: src/b.txt\n+created\n*** End Patch";
    let result = t
        .call(r, "apply_patch", &json!({"patch":patch}))
        .await
        .unwrap();
    assert_eq!(result["files"].as_array().unwrap().len(), 2);
    assert_eq!(
        std::fs::read_to_string(r.join("a.txt")).unwrap(),
        "one\nthree\n"
    );
    assert_eq!(
        std::fs::read_to_string(r.join("src/b.txt")).unwrap(),
        "created\n"
    );
    assert!(t
        .call(
            r,
            "apply_patch",
            &json!({"patch":"*** Begin Patch\n*** Add File: ../outside\n+bad\n*** End Patch"})
        )
        .await
        .is_err());
    assert!(t
        .call(
            r,
            "apply_patch",
            &json!({"patch":"*** Begin Patch\n*** Update File: a.txt\n@@\névil\n*** End Patch"})
        )
        .await
        .is_err());
}
#[tokio::test]
async fn unified_patch_and_crlf_preservation() {
    let tmp = tempfile::tempdir().unwrap();
    let r = tmp.path();
    let mut t = CodingTools::default();
    std::fs::write(r.join("a.txt"), "one\r\ntwo\r\n").unwrap();
    let patch = "--- a/a.txt\n+++ b/a.txt\n@@ -1,2 +1,2 @@\n one\n-two\n+three\n";
    t.call(r, "apply_patch", &json!({"patch":patch}))
        .await
        .unwrap();
    assert_eq!(
        std::fs::read_to_string(r.join("a.txt")).unwrap(),
        "one\r\nthree\r\n"
    );
    let invalid = patch.replace("+1,2", "+10,2");
    assert!(t
        .call(r, "apply_patch", &json!({"patch":invalid}))
        .await
        .is_err());
    t.call(
        r,
        "apply_patch",
        &json!({"patch":"--- a/a.txt\n+++ /dev/null\n@@ -1,2 +0,0 @@\n-one\n-three\n"}),
    )
    .await
    .unwrap();
    assert!(!r.join("a.txt").exists());
}
#[cfg(unix)]
#[tokio::test]
async fn git_read_tools_do_not_execute_repository_helpers() {
    use std::process::Command;
    let tmp = tempfile::tempdir().unwrap();
    let r = tmp.path();
    let mut t = CodingTools::default();
    let git = |args: &[&str]| {
        let status = Command::new("git")
            .args([
                "-c",
                "core.hooksPath=/dev/null",
                "-c",
                "commit.gpgsign=false",
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.invalid",
            ])
            .args(args)
            .current_dir(r)
            .env("GIT_CONFIG_NOSYSTEM", "1")
            .env("GIT_CONFIG_GLOBAL", "/dev/null")
            .output()
            .unwrap();
        assert!(
            status.status.success(),
            "{}",
            String::from_utf8_lossy(&status.stderr)
        );
    };
    git(&["init", "-q"]);
    std::fs::write(r.join("a.txt"), "before\n").unwrap();
    git(&["add", "a.txt"]);
    git(&["commit", "-qm", "Initial"]);
    std::fs::write(r.join("a.txt"), "after\n").unwrap();
    git(&["config", "diff.external", "touch SHOULD_NOT_EXIST"]);
    git(&["config", "core.fsmonitor", "touch SHOULD_NOT_EXIST"]);
    let v = t.call(r, "git_diff", &json!({})).await.unwrap();
    assert!(v["text"].as_str().unwrap().contains("+after"));
    assert!(t.call(r, "git_status", &json!({})).await.unwrap()["text"]
        .as_str()
        .unwrap()
        .contains("a.txt"));
    assert!(
        t.call(r, "git_log", &json!({"limit":1})).await.unwrap()["text"]
            .as_str()
            .unwrap()
            .contains("Initial")
    );
    assert!(!r.join("SHOULD_NOT_EXIST").exists());
    assert!(t
        .call(r, "git_diff", &json!({"path":"../outside"}))
        .await
        .is_err());
    git(&["config", "filter.fixture.clean", "touch FILTER_RAN; cat"]);
    std::fs::write(r.join(".gitattributes"), "*.txt filter=fixture\n").unwrap();
    for name in ["git_status", "git_diff", "git_log"] {
        assert!(t.call(r, name, &json!({})).await.is_err());
    }
    assert!(!r.join("FILTER_RAN").exists());
    git(&["config", "--remove-section", "filter.fixture"]);
    git(&["config", "include.path", "../external.config"]);
    assert!(t.call(r, "git_status", &json!({})).await.is_err());
}
#[tokio::test]
async fn web_fetch_rejects_local_services_and_credentials() {
    let tmp = tempfile::tempdir().unwrap();
    let mut t = CodingTools::default();
    for url in [
        "https://127.0.0.1/",
        "https://169.254.169.254/",
        "https://user:secret@example.com/",
        "http://example.com/",
    ] {
        assert!(t
            .call(tmp.path(), "web_fetch", &json!({"url":url}))
            .await
            .is_err());
    }
}
