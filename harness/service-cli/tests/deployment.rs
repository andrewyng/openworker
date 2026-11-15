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

fn cloud_plan(home: &Path, out: &Path, target: &[&str]) -> Value {
    let mut args = vec!["deploy"];
    args.extend_from_slice(target);
    args.extend_from_slice(&["--name", "portable", "--out", out.to_str().unwrap()]);
    parsed(call(home, &args))
}
#[test]
fn gcp_and_azure_plans_validate_offline_and_refuse_cloud_mutation() {
    let home = tempfile::tempdir().unwrap();
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        for name in [
            "aws",
            "gcloud",
            "az",
            "terraform",
            "helm",
            "kubectl",
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
    for (provider, args, engine) in [
        (
            "gcp",
            vec![
                "gcp",
                "plan",
                "--project",
                "example-project",
                "--location",
                "us-central1",
            ],
            "Infrastructure Manager / Terraform",
        ),
        (
            "azure",
            vec![
                "azure",
                "plan",
                "--tenant",
                "11111111-2222-4333-8444-555555555555",
                "--subscription",
                "66666666-7777-4888-8999-aaaaaaaaaaaa",
                "--location",
                "westus2",
            ],
            "Bicep / ARM",
        ),
    ] {
        let out = home.path().join(provider);
        let p = cloud_plan(home.path(), &out, &args);
        assert_eq!(p["target"]["provider"], provider);
        assert_eq!(p["applyReady"], false);
        assert_eq!(fs::read_dir(&out).unwrap().count(), 5);
        for verb in ["inspect", "validate", "status"] {
            let result = parsed(call(
                home.path(),
                &["deploy", verb, "--plan", out.to_str().unwrap()],
            ));
            assert_eq!(result["bundleDigest"], p["bundleDigest"]);
        }
        let graph: Value =
            serde_json::from_slice(&fs::read(out.join("resource-graph.json")).unwrap()).unwrap();
        assert_eq!(graph["engine"], engine);
        assert_eq!(graph["resolved"], false);
        // The graph is topologically ordered; every dependency must already exist.
        let mut seen = std::collections::HashSet::new();
        for node in graph["nodes"].as_array().unwrap() {
            for dep in node["dependsOn"].as_array().unwrap() {
                assert!(seen.contains(dep.as_str().unwrap()));
            }
            assert!(seen.insert(node["id"].as_str().unwrap()));
        }
        assert!(!out.join("bootstrap.sh").exists());
        for mut_args in [
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
        ] {
            let o = call(home.path(), &mut_args);
            assert!(!o.status.success());
            assert!(String::from_utf8_lossy(&o.stderr).contains("not implemented"));
        }
        assert!(!home.path().join("cloud-called").exists());
        fs::write(out.join("resource-graph.json"), "{}").unwrap();
        assert!(!call(
            home.path(),
            &["deploy", "validate", "--plan", out.to_str().unwrap()]
        )
        .status
        .success());
    }
}
#[test]
fn provider_scope_and_identity_validation_happens_before_writing() {
    let home = tempfile::tempdir().unwrap();
    let out = home.path().join("bad");
    for target in [
        vec![
            "gcp",
            "plan",
            "--project",
            "../oops",
            "--location",
            "us-central1",
        ],
        vec![
            "gcp",
            "plan",
            "--project",
            "example-project",
            "--location",
            "us-central1-a",
        ],
        vec![
            "azure",
            "plan",
            "--tenant",
            "00000000-0000-0000-0000-000000000000",
            "--subscription",
            "66666666-7777-4888-8999-aaaaaaaaaaaa",
            "--location",
            "westus2",
        ],
        vec![
            "azure",
            "plan",
            "--tenant",
            "11111111-2222-4333-8444-555555555555",
            "--subscription",
            "bad",
            "--location",
            "westus2",
        ],
    ] {
        let mut args = vec!["deploy"];
        args.extend(target);
        args.extend(["--name", "bad-input", "--out", out.to_str().unwrap()]);
        assert!(!call(home.path(), &args).status.success());
        assert!(!out.exists());
    }
    for (operator, executor) in [
        (
            "arn:aws:iam::123456789012:root",
            "arn:aws:iam::123456789012:role/executor",
        ),
        (
            "arn:aws:sts::123456789012:assumed-role/operator/session",
            "arn:aws:iam::123456789012:role/executor",
        ),
        (
            "arn:aws:iam::999999999999:role/operator",
            "arn:aws:iam::123456789012:role/executor",
        ),
        (
            "arn:aws:iam::123456789012:role/operator",
            "arn:aws:iam::123456789012:role/operator",
        ),
        (
            "arn:aws:iam::123456789012:role/operator",
            "arn:aws:iam::123456789012:role/*",
        ),
        (
            "arn:aws:iam::123456789012:role/op';echo hi",
            "arn:aws:iam::123456789012:role/executor",
        ),
        (
            "arn:aws:iam::123456789012:role/operator",
            "arn:aws:iam::123456789012:user/executor",
        ),
    ] {
        let args = [
            "deploy",
            "aws",
            "plan",
            "--account",
            "123456789012",
            "--region",
            "us-west-2",
            "--name",
            "bad-input",
            "--operator-arn",
            operator,
            "--execution-role-arn",
            executor,
            "--out",
            out.to_str().unwrap(),
        ];
        assert!(!call(home.path(), &args).status.success());
        assert!(!out.exists());
    }
    assert!(!call(
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
            "bad-input",
            "--operator-arn",
            "arn:aws:iam::123456789012:role/operator",
            "--out",
            out.to_str().unwrap()
        ]
    )
    .status
    .success());
}
fn bootstrap_plan(home: &Path, out: &Path, operator: &str) -> Value {
    cloud_plan(
        home,
        out,
        &[
            "aws",
            "plan",
            "--account",
            "123456789012",
            "--region",
            "us-west-2",
            "--operator-arn",
            operator,
            "--execution-role-arn",
            "arn:aws:iam::123456789012:role/reviewed/executor",
        ],
    )
}
#[test]
fn aws_identity_artifacts_have_scoped_authority_and_are_integrity_checked() {
    let home = tempfile::tempdir().unwrap();
    let out = home.path().join("plan");
    let p = bootstrap_plan(
        home.path(),
        &out,
        "arn:aws:iam::123456789012:role/operators/deployer",
    );
    assert_eq!(fs::read_dir(&out).unwrap().count(), 10);
    let read = |name| serde_json::from_slice::<Value>(&fs::read(out.join(name)).unwrap()).unwrap();
    let template = read("bootstrap.cfn.json");
    let setup = read("setup-policy.json");
    let workload = read("workload-policy.json");
    let resources = template["Resources"].as_object().unwrap();
    assert_eq!(resources.len(), 2);
    for r in resources.values() {
        assert_eq!(r["Type"], "AWS::IAM::Role");
        assert!(r["Properties"].get("ManagedPolicyArns").is_none());
    }
    assert_eq!(
        resources["SetupRole"]["Properties"]["AssumeRolePolicyDocument"]["Statement"][0]
            ["Principal"]["AWS"],
        "arn:aws:iam::123456789012:role/operators/deployer"
    );
    assert_eq!(
        resources["SetupRole"]["Properties"]["Policies"][0]["PolicyDocument"],
        setup
    );
    assert_eq!(
        resources["ArtifactRole"]["Properties"]["Policies"][0]["PolicyDocument"],
        workload
    );
    for permissions in [&setup, &workload] {
        for st in permissions["Statement"].as_array().unwrap() {
            assert_eq!(st["Effect"], "Allow");
            for field in ["Resource", "Action"] {
                let values = st[field]
                    .as_array()
                    .cloned()
                    .unwrap_or(vec![st[field].clone()]);
                for value in values {
                    assert_ne!(value, "*");
                    if field == "Action" {
                        assert!(!value.as_str().unwrap().contains('*'));
                    }
                }
            }
        }
    }
    let sts = setup["Statement"].as_array().unwrap();
    let pass = sts
        .iter()
        .find(|v| v["Sid"] == "PassExactExecutorOnly")
        .unwrap();
    assert_eq!(
        pass["Resource"],
        "arn:aws:iam::123456789012:role/reviewed/executor"
    );
    assert_eq!(
        pass["Condition"]["StringEquals"]["iam:PassedToService"],
        "cloudformation.amazonaws.com"
    );
    let create = sts
        .iter()
        .find(|v| v["Sid"] == "OwnedStacksBeforeExpiry")
        .unwrap();
    assert_eq!(
        create["Condition"]["NumericLessThan"]["aws:EpochTime"],
        p["expiresAtUnix"]
    );
    assert!(sts
        .iter()
        .find(|v| v["Sid"] == "OwnedStackCleanup")
        .unwrap()["Condition"]
        .get("NumericLessThan")
        .is_none());
    let trust = &resources["ArtifactRole"]["Properties"]["AssumeRolePolicyDocument"]["Statement"]
        [0]["Condition"]["StringEquals"];
    assert_eq!(trust["aws:RequestTag/kubernetes-namespace"], "ow-service");
    assert_eq!(
        trust["aws:RequestTag/kubernetes-service-account"],
        "ows-artifacts"
    );
    assert!(trust["aws:RequestTag/eks-cluster-arn"]
        .as_str()
        .unwrap()
        .starts_with("arn:aws:eks:us-west-2:123456789012:cluster/ows-"));
    assert!(workload["Statement"][0]["Resource"]
        .as_str()
        .unwrap()
        .contains(p["deploymentId"].as_str().unwrap()));
    for name in [
        "bootstrap.sh",
        "bootstrap.cfn.json",
        "setup-policy.json",
        "workload-policy.json",
        "BOOTSTRAP.md",
    ] {
        let original = fs::read(out.join(name)).unwrap();
        fs::write(out.join(name), "edited").unwrap();
        assert!(!call(
            home.path(),
            &["deploy", "validate", "--plan", out.to_str().unwrap()]
        )
        .status
        .success());
        fs::write(out.join(name), original).unwrap();
    }
    parsed(call(
        home.path(),
        &["deploy", "validate", "--plan", out.to_str().unwrap()],
    ));
}

#[cfg(unix)]
#[test]
fn generated_bootstrap_runs_only_against_fake_aws_and_never_retries_unknown_outcomes() {
    use std::os::unix::fs::{symlink, PermissionsExt};
    let home = tempfile::tempdir().unwrap();
    let out = home.path().join("plan with spaces");
    bootstrap_plan(
        home.path(),
        &out,
        "arn:aws:iam::123456789012:role/operators/deployer",
    );
    symlink(BIN, home.path().join("ows")).unwrap();
    let fake = home.path().join("aws");
    fs::write(&fake,r#"#!/bin/bash
set -eu
printf '%s\n' "$*" >> "$HOME/calls"
case "$1 $2" in
  'sts get-caller-identity')
    case "$*" in *'--query Account'*) printf '%s\n' "${TEST_ACCOUNT:-123456789012}";; *) printf '%s\n' "${TEST_CALLER:-arn:aws:sts::123456789012:assumed-role/deployer/session}";; esac;;
  'iam get-role') printf '%s\n' "${TEST_EXECUTOR:-arn:aws:iam::123456789012:role/reviewed/executor}";;
  'cloudformation create-stack')
    [ "$AWS_MAX_ATTEMPTS" = 1 ]
    [ "$AWS_CLI_AUTO_PROMPT" = off ]
    [ -z "$AWS_PAGER" ]
    case "${TEST_CREATE:-success}" in unknown) echo 'network response lost' >&2; exit 51;; exists) echo AlreadyExists >&2; exit 52;; esac
    echo '{"StackId":"fake-stack"}';;
  'cloudformation wait') [ "${TEST_WAIT:-success}" = success ];;
  *) echo 'unexpected AWS operation' >&2; exit 99;;
esac
"#).unwrap();
    fs::set_permissions(&fake, fs::Permissions::from_mode(0o700)).unwrap();
    let execute = |args: &[&str], extra: Option<(&str, &str)>| {
        let mut c = Command::new("/bin/bash");
        c.arg(out.join("bootstrap.sh"))
            .args(args)
            .env_clear()
            .env("HOME", home.path())
            .env("PATH", format!("{}:/usr/bin:/bin", home.path().display()))
            .env("AWS_EC2_METADATA_DISABLED", "true");
        if let Some((k, v)) = extra {
            c.env(k, v);
        }
        c.output().unwrap()
    };
    assert!(Command::new("/bin/bash")
        .arg("-n")
        .arg(out.join("bootstrap.sh"))
        .status()
        .unwrap()
        .success());
    assert!(!execute(&[], None).status.success());
    assert!(!home.path().join("calls").exists());
    let original = fs::read(out.join("bootstrap.cfn.json")).unwrap();
    fs::write(out.join("bootstrap.cfn.json"), "{}").unwrap();
    assert!(!execute(&["--apply-identities"], None).status.success());
    assert!(!home.path().join("calls").exists());
    fs::write(out.join("bootstrap.cfn.json"), original).unwrap();
    for (key, value) in [
        ("TEST_ACCOUNT", "999999999999"),
        (
            "TEST_CALLER",
            "arn:aws:sts::123456789012:assumed-role/other/session",
        ),
        ("TEST_EXECUTOR", "arn:aws:iam::123456789012:role/wrong"),
    ] {
        fs::write(home.path().join("calls"), "").unwrap();
        assert!(!execute(&["--apply-identities"], Some((key, value)))
            .status
            .success());
        assert!(!fs::read_to_string(home.path().join("calls"))
            .unwrap()
            .contains("cloudformation"));
    }
    for failure in ["unknown", "exists"] {
        fs::write(home.path().join("calls"), "").unwrap();
        let result = execute(&["--apply-identities"], Some(("TEST_CREATE", failure)));
        assert!(!result.status.success());
        assert!(String::from_utf8_lossy(&result.stderr).contains("before any retry"));
        let log = fs::read_to_string(home.path().join("calls")).unwrap();
        assert_eq!(log.matches("cloudformation create-stack").count(), 1);
        assert!(!log.contains("cloudformation wait"));
        assert!(!log.contains("update-stack"));
    }
    fs::write(home.path().join("calls"), "").unwrap();
    assert!(
        !execute(&["--apply-identities"], Some(("TEST_WAIT", "fail")))
            .status
            .success()
    );
    let result = execute(&["--apply-identities"], None);
    assert!(
        result.status.success(),
        "{}",
        String::from_utf8_lossy(&result.stderr)
    );
    assert!(String::from_utf8_lossy(&result.stdout).contains("Identity stack created"));
    // IAM user branch also requires the exact caller, not just the account.
    let user = home.path().join("user-plan");
    bootstrap_plan(
        home.path(),
        &user,
        "arn:aws:iam::123456789012:user/operators/alice",
    );
    for caller in [
        "arn:aws:iam::123456789012:user/operators/alice",
        "arn:aws:iam::123456789012:user/bob",
    ] {
        let result = Command::new("/bin/bash")
            .arg(user.join("bootstrap.sh"))
            .arg("--apply-identities")
            .env_clear()
            .env("HOME", home.path())
            .env("PATH", format!("{}:/usr/bin:/bin", home.path().display()))
            .env("TEST_CALLER", caller)
            .output()
            .unwrap();
        assert_eq!(result.status.success(), caller.ends_with("/alice"));
    }
}
