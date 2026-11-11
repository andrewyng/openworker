//! Public entry points must reject unsupported configuration before cloud tools.
use std::{fs, process::Command};
const BIN: &str = env!("CARGO_BIN_EXE_ows");
#[test]
fn both_entry_points_explain_review_resume_and_private_access() {
    for cloud in ["aws", "azure"] {
        let output = Command::new(BIN)
            .args(["deploy", cloud, "up", "--help"])
            .output()
            .unwrap();
        assert!(output.status.success());
        let help = String::from_utf8(output.stdout).unwrap();
        for option in [
            "--config",
            "--plan-only",
            "--accept-plan",
            "--accept-bootstrap",
            "--keep-access",
        ] {
            assert!(help.contains(option), "{cloud}: {option}");
        }
    }
}
#[test]
fn inline_secret_or_wrong_cloud_fails_without_login_or_secret_echo() {
    let temp = tempfile::tempdir().unwrap();
    let config = temp.path().join("deployment.yaml");
    let base="name: example\ncloud: {provider: aws, accountId: '123456789012', region: us-west-2}\nnodes: {count: 2, instanceType: t4g.large}\nbudget: {limitUSD: 100}\n";
    for (cloud, text, expected) in [
        ("azure", base.to_owned(), "differs"),
        (
            "aws",
            format!("{base}models: {{openai: {{apiKey: NEVER_ECHO_THIS_SECRET}}}}"),
            "schema",
        ),
    ] {
        fs::write(&config, text).unwrap();
        let r = Command::new(BIN)
            .env_clear()
            .env("HOME", temp.path())
            .env("PATH", temp.path())
            .args(["deploy", cloud, "up", "--config", config.to_str().unwrap()])
            .output()
            .unwrap();
        assert!(!r.status.success());
        let stderr = String::from_utf8_lossy(&r.stderr);
        assert!(stderr.contains(expected), "{stderr}");
        assert!(!stderr.contains("NEVER_ECHO_THIS_SECRET"));
    }
}
#[test]
fn absent_account_profile_produces_prerequisite_instructions_without_cloud_calls() {
    let temp = tempfile::tempdir().unwrap();
    let config = temp.path().join("deployment.yaml");
    fs::write(&config,"name: example\ncloud: {provider: azure, subscriptionId: '11111111-2222-4333-8444-555555555555', region: westus2}\nnodes: {count: 2, instanceType: Standard_D4as_v7}\nbudget: {limitUSD: 100}\n").unwrap();
    let r = Command::new(BIN)
        .env_clear()
        .env("HOME", temp.path())
        .env("PATH", temp.path())
        .args([
            "deploy",
            "azure",
            "up",
            "--config",
            config.to_str().unwrap(),
            "--plan-only",
        ])
        .output()
        .unwrap();
    assert!(!r.status.success());
    let error = String::from_utf8_lossy(&r.stderr);
    assert!(error.contains("setup profile is missing"), "{error}");
    assert!(!temp.path().join(".ows").exists());
}
