//! Exercise the shipped command boundary with no cloud credentials and trap CLIs.
use serde_json::Value;
use std::{
    fs,
    path::Path,
    process::{Command, Output},
};
const BIN: &str = env!("CARGO_BIN_EXE_ows");
fn call(home: &Path, args: &[&str]) -> Output {
    Command::new(BIN)
        .args(args)
        .env_clear()
        .env("HOME", home)
        .env("PATH", home)
        .env("AWS_EC2_METADATA_DISABLED", "true")
        .output()
        .unwrap()
}
fn parsed(o: Output) -> Value {
    assert!(o.status.success(), "{}", String::from_utf8_lossy(&o.stderr));
    serde_json::from_slice(&o.stdout).unwrap()
}
fn plan(home: &Path, out: &Path) -> Value {
    parsed(call(
        home,
        &[
            "--config",
            "/missing-service-config",
            "deploy",
            "aws",
            "plan",
            "--account",
            "123456789012",
            "--region",
            "us-west-2",
            "--name",
            "offline-test",
            "--budget",
            "100",
            "--out",
            out.to_str().unwrap(),
        ],
    ))
}
#[test]
fn offline_commands_never_invoke_cloud_clis_or_read_service_credentials() {
    let home = tempfile::tempdir().unwrap();
    let out = home.path().join("plan");
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        for name in [
            "aws",
            "helm",
            "kubectl",
            "gcloud",
            "az",
            "terraform",
            "docker",
        ] {
            let file = home.path().join(name);
            fs::write(
                &file,
                "#!/bin/sh\nprintf forbidden > \"$HOME/cloud-called\"\nexit 88\n",
            )
            .unwrap();
            fs::set_permissions(file, fs::Permissions::from_mode(0o700)).unwrap();
        }
    }
    let p = plan(home.path(), &out);
    assert_eq!(p["state"], "planned");
    assert_eq!(p["cloudObservation"], "not_checked");
    assert_eq!(p["applyReady"], false);
    assert!(p["budget"]["estimate"].is_null());
    for cmd in ["inspect", "status"] {
        let r = parsed(call(
            home.path(),
            &["deploy", cmd, "--plan", out.to_str().unwrap()],
        ));
        assert_eq!(r["deploymentId"], p["deploymentId"]);
        assert_eq!(r["expiresAtUnix"], p["expiresAtUnix"]);
    }
    let r = parsed(call(
        home.path(),
        &["deploy", "validate", "--plan", out.to_str().unwrap()],
    ));
    assert_eq!(r["validation"], "offline_only");
    assert_eq!(r["applyReady"], false);
    for args in [
        vec![
            "deploy",
            "apply",
            "--plan",
            out.to_str().unwrap(),
            "--accept-plan",
            p["bundleDigest"].as_str().unwrap(),
        ],
        vec![
            "deploy",
            "destroy",
            "--plan",
            out.to_str().unwrap(),
            "--deployment-id",
            p["deploymentId"].as_str().unwrap(),
        ],
        vec!["deploy", "gcp", "plan"],
        vec!["deploy", "azure", "plan"],
    ] {
        let r = call(home.path(), &args);
        assert!(!r.status.success());
        assert!(String::from_utf8_lossy(&r.stderr).contains("not implemented"));
    }
    assert!(!home.path().join("cloud-called").exists());
    assert!(
        !out.join("bootstrap.sh").exists(),
        "Never emit a fake deployable bootstrap script"
    );
    let permissions: Value =
        serde_json::from_slice(&fs::read(out.join("permissions-request.json")).unwrap()).unwrap();
    assert_eq!(permissions["format"], "ows.permission-requirements.v1");
    assert_eq!(permissions["setup"]["policyReady"], false);
}
#[test]
fn plans_have_distinct_identity_and_refuse_overwrite_and_tampering() {
    let home = tempfile::tempdir().unwrap();
    let out = home.path().join("one");
    let a = plan(home.path(), &out);
    let b = plan(home.path(), &home.path().join("two"));
    assert_ne!(a["deploymentId"], b["deploymentId"]);
    let old = fs::read(out.join("deployment-plan.json")).unwrap();
    let r = call(
        home.path(),
        &[
            "deploy",
            "aws",
            "plan",
            "--account",
            "123456789012",
            "--region",
            "us-west-2",
            "--name",
            "offline-test",
            "--out",
            out.to_str().unwrap(),
        ],
    );
    assert!(!r.status.success());
    assert_eq!(fs::read(out.join("deployment-plan.json")).unwrap(), old);
    fs::write(out.join("permissions-request.json"), "{}").unwrap();
    assert!(!call(
        home.path(),
        &["deploy", "validate", "--plan", out.to_str().unwrap()]
    )
    .status
    .success());
}
#[test]
fn invalid_plan_inputs_create_nothing() {
    let home = tempfile::tempdir().unwrap();
    let out = home.path().join("plan");
    for (name, value) in [
        ("--account", "bad"),
        ("--region", "us-west-2;echo"),
        ("--name", "../other"),
        ("--budget", "0"),
        ("--ttl-hours", "73"),
    ] {
        let mut args = vec![
            "deploy",
            "aws",
            "plan",
            "--account",
            "123456789012",
            "--region",
            "us-west-2",
            "--name",
            "offline-test",
            "--budget",
            "100",
            "--ttl-hours",
            "24",
            "--out",
            out.to_str().unwrap(),
        ];
        let i = args.iter().position(|v| *v == name).unwrap();
        args[i + 1] = value;
        assert!(!call(home.path(), &args).status.success());
        assert!(!out.exists());
    }
}
