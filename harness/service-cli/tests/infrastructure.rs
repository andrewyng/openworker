//! Offline artifact contract tests, not an AWS IAM simulation or cloud qualification.
use serde_json::{json, Value};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    path::Path,
    process::{Command, Output},
};
const BIN: &str = env!("CARGO_BIN_EXE_ows");
fn input() -> Value {
    serde_json::from_str(include_str!("../examples/aws-infrastructure.json")).unwrap()
}
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
fn generate(home: &Path, name: &str, config: &Value) -> (std::path::PathBuf, Value) {
    let file = home.join(format!("{name}.json"));
    fs::write(&file, serde_json::to_vec(config).unwrap()).unwrap();
    let out = home.join(name);
    let r = call(
        home,
        &[
            "deploy",
            "aws",
            "plan",
            "--account",
            "123456789012",
            "--region",
            "us-west-2",
            "--operator-arn",
            "arn:aws:iam::123456789012:role/operator",
            "--infrastructure-config",
            file.to_str().unwrap(),
            "--name",
            "offline-infra",
            "--out",
            out.to_str().unwrap(),
        ],
    );
    assert!(r.status.success(), "{}", String::from_utf8_lossy(&r.stderr));
    (out, serde_json::from_slice(&r.stdout).unwrap())
}
fn read(root: &Path, name: &str) -> Value {
    serde_json::from_slice(&fs::read(root.join(name)).unwrap()).unwrap()
}
fn list(v: &Value) -> Vec<&str> {
    if let Some(a) = v.as_array() {
        a.iter().map(|s| s.as_str().unwrap()).collect()
    } else {
        vec![v.as_str().unwrap()]
    }
}
fn references(v: &Value, out: &mut BTreeSet<String>) {
    match v {
        Value::Array(a) => {
            for x in a {
                references(x, out)
            }
        }
        Value::Object(m) => {
            if let Some(r) = m.get("Ref").and_then(Value::as_str) {
                out.insert(r.into());
            }
            if let Some(a) = m.get("Fn::GetAtt").and_then(Value::as_array) {
                out.insert(a[0].as_str().unwrap().into());
            }
            if let Some(s) = m.get("Fn::Sub").and_then(Value::as_str) {
                for part in s.split("${").skip(1) {
                    out.insert(
                        part.split('}')
                            .next()
                            .unwrap()
                            .split('.')
                            .next()
                            .unwrap()
                            .into(),
                    );
                }
            }
            if let Some(d) = m.get("DependsOn") {
                for name in list(d) {
                    out.insert(name.into());
                }
            }
            for value in m.values() {
                references(value, out)
            }
        }
        _ => {}
    }
}
fn template_consistent(t: &Value) {
    let resources = t["Resources"].as_object().unwrap();
    let mut graph = BTreeMap::new();
    for (name, r) in resources {
        let mut refs = BTreeSet::new();
        references(r, &mut refs);
        for target in &refs {
            assert!(
                resources.contains_key(target),
                "{name} references missing {target}"
            );
        }
        graph.insert(name.clone(), refs);
    }
    let mut seen = BTreeSet::new();
    while !graph.is_empty() {
        let ready: Vec<_> = graph
            .iter()
            .filter(|(_, deps)| deps.iter().all(|d| seen.contains(d)))
            .map(|(id, _)| id.clone())
            .collect();
        assert!(!ready.is_empty(), "dependency cycle: {graph:?}");
        for id in ready {
            graph.remove(&id);
            seen.insert(id);
        }
    }
    let mut outputs = BTreeSet::new();
    references(&t["Outputs"], &mut outputs);
    for target in outputs {
        assert!(
            resources.contains_key(&target),
            "missing output dependency {target}"
        );
    }
}
#[test]
fn complete_templates_are_private_bounded_and_have_valid_dependency_graphs() {
    let home = tempfile::tempdir().unwrap();
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        for name in [
            "aws",
            "gcloud",
            "az",
            "helm",
            "kubectl",
            "docker",
            "terraform",
        ] {
            let file = home.path().join(name);
            fs::write(
                &file,
                "#!/bin/sh\nprintf called > \"$HOME/cloud-called\"\nexit 88\n",
            )
            .unwrap();
            fs::set_permissions(file, fs::Permissions::from_mode(0o700)).unwrap();
        }
    }
    let (out, meta) = generate(home.path(), "review", &input());
    assert_eq!(meta["applyReady"], false);
    assert_eq!(
        meta["identityBootstrap"],
        "exported_generated_execution_roles"
    );
    assert_eq!(read(&out, "deployment-plan.json")["schemaVersion"], 3);
    let infra = read(&out, "infrastructure.cfn.json");
    let boot = read(&out, "bootstrap.cfn.json");
    let guard = read(&out, "guard.cfn.json");
    for t in [&infra, &boot, &guard] {
        template_consistent(t);
    }
    let r = &infra["Resources"];
    assert_eq!(
        r["Cluster"]["Properties"]["ResourcesVpcConfig"]["EndpointPublicAccess"],
        false
    );
    assert_eq!(
        r["Cluster"]["Properties"]["ResourcesVpcConfig"]["EndpointPrivateAccess"],
        true
    );
    assert_eq!(
        r["Cluster"]["Properties"]["AccessConfig"]["BootstrapClusterCreatorAdminPermissions"],
        false
    );
    assert_eq!(
        r["Nodes"]["Properties"]["Subnets"],
        json!([{"Ref":"PrivateSubnet0"},{"Ref":"PrivateSubnet1"}])
    );
    assert_eq!(
        r["Nodes"]["Properties"]["ScalingConfig"],
        json!({"MinSize":2,"MaxSize":2,"DesiredSize":2})
    );
    for subnet in ["PublicSubnet", "PrivateSubnet0", "PrivateSubnet1"] {
        assert_eq!(r[subnet]["Properties"]["MapPublicIpOnLaunch"], false);
    }
    assert_eq!(r["Database"]["Properties"]["PubliclyAccessible"], false);
    assert_eq!(r["Database"]["Properties"]["StorageEncrypted"], true);
    assert!(r["Database"]["Properties"]["MasterUserPassword"]["Fn::Sub"]
        .as_str()
        .unwrap()
        .starts_with("{{resolve:secretsmanager:"));
    assert_eq!(r["Database"]["DeletionPolicy"], "Delete");
    assert_eq!(r["Database"]["UpdateReplacePolicy"], "Delete");
    assert_eq!(
        r["DatabaseParameters"]["Properties"]["Parameters"]["rds.force_ssl"],
        "1"
    );
    assert!(r
        .as_object()
        .unwrap()
        .values()
        .all(|v| v["Type"] != "AWS::IAM::Role"));
    assert_eq!(
        guard["Resources"]["Cleanup"]["Properties"]["Code"]["S3ObjectVersion"],
        input()["guardCode"]["versionId"]
    );
    assert_eq!(
        guard["Resources"]["Schedule"]["Properties"]["Targets"][0]["RetryPolicy"]
            ["MaximumRetryAttempts"],
        0
    );
    assert!(
        guard["Resources"]["Cleanup"]["Properties"]
            .get("VpcConfig")
            .is_none(),
        "Guard must survive target VPC/cluster loss"
    );
    assert_eq!(read(&out, "guard-contract.json")["qualified"], false);
    let coverage = read(&out, "permission-review.json");
    assert_eq!(
        coverage["coverage"].as_object().unwrap().len(),
        infra["Resources"].as_object().unwrap().len()
            + guard["Resources"].as_object().unwrap().len()
    );
    for id in infra["Resources"].as_object().unwrap().keys() {
        assert!(coverage["coverage"]
            .get(format!("infrastructure/{id}"))
            .is_some());
    }
    for verb in ["inspect", "validate", "status"] {
        let o = call(
            home.path(),
            &["deploy", verb, "--plan", out.to_str().unwrap()],
        );
        assert!(o.status.success());
    }
    let refused = call(
        home.path(),
        &[
            "deploy",
            "apply",
            "--plan",
            out.to_str().unwrap(),
            "--accept-plan",
            meta["bundleDigest"].as_str().unwrap(),
        ],
    );
    assert!(!refused.status.success());
    assert!(String::from_utf8_lossy(&refused.stderr).contains("not implemented"));
    assert!(!home.path().join("cloud-called").exists());
}
#[test]
fn generated_executor_cannot_mutate_identities_and_cleanup_cannot_provision() {
    let home = tempfile::tempdir().unwrap();
    let (out, meta) = generate(home.path(), "scopes", &input());
    let boot = read(&out, "bootstrap.cfn.json");
    let resources = boot["Resources"].as_object().unwrap();
    let executor = &resources["ExecutionRole"]["Properties"];
    assert_eq!(executor["ManagedPolicyArns"].as_array().unwrap().len(), 5);
    assert_eq!(
        executor["AssumeRolePolicyDocument"]["Statement"][0]["Principal"]["Service"],
        "cloudformation.amazonaws.com"
    );
    assert!(executor.get("Policies").is_none());
    let mut policy_count = 0;
    for r in resources
        .values()
        .filter(|r| r["Type"] == "AWS::IAM::ManagedPolicy")
    {
        policy_count += 1;
        let doc = &r["Properties"]["PolicyDocument"];
        assert!(serde_json::to_vec(doc).unwrap().len() <= 6144);
        for s in doc["Statement"].as_array().unwrap() {
            for action in list(&s["Action"]) {
                assert!(!action.contains('*'));
                if action.starts_with("iam:") {
                    assert!(["iam:GetRole", "iam:PassRole"].contains(&action));
                }
                if action == "iam:PassRole" {
                    for arn in list(&s["Resource"]) {
                        assert!(!arn.contains('*'));
                        assert!(
                            arn.starts_with("arn:aws:iam::123456789012:role/ows-offline-infra-")
                        );
                    }
                    assert!(s["Condition"]["StringEquals"]
                        .get("iam:PassedToService")
                        .is_some());
                }
            }
            if list(&s["Resource"]).contains(&"*") {
                assert!([
                    "NetworkObservation",
                    "CreateTaggedPrivateCluster",
                    "GenerateDatabasePassword",
                    "ObserveLogGroups"
                ]
                .contains(&s["Sid"].as_str().unwrap()));
                assert_eq!(
                    s["Condition"]["StringEquals"]["aws:RequestedRegion"],
                    "us-west-2"
                );
            }
            if s["Sid"] == "RemoveOtherOwnedTags" {
                assert_eq!(
                    s["Condition"]["Null"]["aws:TagKeys"], "false",
                    "Reject deleting all tags through an omitted tag list"
                );
            }
            if s["Sid"] == "CreateTaggedPrivateCluster" {
                assert_eq!(
                    s["Condition"]["StringEquals"]["aws:RequestTag/ows:deployment-id"],
                    meta["deploymentId"]
                );
            }
            if s["Sid"] == "NamedDatabaseSecret" {
                assert!(list(&s["Resource"])[0].ends_with("-database-??????"));
            }
        }
    }
    assert_eq!(policy_count, 5);
    let cleanup = read(&out, "cleanup-policy.json");
    for s in cleanup["Statement"].as_array().unwrap() {
        for action in list(&s["Action"]) {
            assert!(![
                "cloudformation:CreateStack",
                "cloudformation:UpdateStack",
                "iam:CreateRole",
                "secretsmanager:GetSecretValue",
                "s3:GetObject",
                "s3:DeleteBucket",
                "ec2:RunInstances"
            ]
            .contains(&action));
        }
        if list(&s["Action"]).contains(&"cloudformation:DeleteStack") {
            assert!(list(&s["Resource"])[0].ends_with("-infra/*"));
            assert!(!list(&s["Resource"])[0].contains("bootstrap"));
            assert_eq!(
                s["Condition"]["StringEquals"]["cloudformation:RoleArn"],
                executor["RoleName"]
                    .as_str()
                    .map(|name| format!("arn:aws:iam::123456789012:role/{name}"))
                    .unwrap()
            );
        }
        if list(&s["Action"]).contains(&"s3:DeleteObject") {
            assert!(list(&s["Resource"])[0]
                .ends_with(&format!("/{}/*", meta["deploymentId"].as_str().unwrap())));
        }
        if list(&s["Action"]).contains(&"ec2:DeleteVolume") {
            assert_eq!(
                s["Condition"]["StringEquals"]["ec2:ResourceTag/ows:deployment-id"],
                meta["deploymentId"]
            );
        }
    }
    assert_eq!(
        resources["CleanupRole"]["Properties"]["Policies"][0]["PolicyDocument"],
        cleanup
    );
    // All IAM role references from infrastructure are pre-created by bootstrap.
    let role_names: BTreeSet<_> = resources
        .values()
        .filter(|r| r["Type"] == "AWS::IAM::Role")
        .map(|r| {
            format!(
                "arn:aws:iam::123456789012:role/{}",
                r["Properties"]["RoleName"].as_str().unwrap()
            )
        })
        .collect();
    fn check(v: &Value, names: &BTreeSet<String>) {
        match v {
            Value::String(s) if s.starts_with("arn:aws:iam::123456789012:role/") => {
                assert!(names.contains(s), "missing {s}")
            }
            Value::Object(m) => {
                for v in m.values() {
                    check(v, names)
                }
            }
            Value::Array(a) => {
                for v in a {
                    check(v, names)
                }
            }
            _ => {}
        }
    }
    check(&read(&out, "infrastructure.cfn.json"), &role_names);
    check(&read(&out, "guard.cfn.json"), &role_names);
}
#[test]
fn rejects_unsafe_or_unpinned_inputs_before_creating_any_bundle() {
    let home = tempfile::tempdir().unwrap();
    let config = home.path().join("input.json");
    let out = home.path().join("bad");
    for (pointer, bad) in [
        ("/schemaVersion", json!(22)),
        ("/vpcCidr", json!("8.8.0.0/16")),
        ("/vpcCidr", json!("10.94.1.0/16")),
        ("/availabilityZones/1", json!("us-west-2a")),
        ("/availabilityZones/0", json!("us-east-1a")),
        ("/kubernetesVersion", json!("latest")),
        ("/nodeReleaseVersion", json!("1.35.4-20261007")),
        ("/nodeReleaseVersion", json!("1.36.foo-bar")),
        ("/postgresVersion", json!("latest")),
        ("/addons/vpcCni", json!("vlatest-eksbuild.1")),
        ("/addons/ebsCsi", json!("latest")),
        ("/guardCode/versionId", json!("null")),
        ("/guardCode/sha256", json!("unknown")),
        ("/acknowledgeExperimentDataDeletion", json!(false)),
    ] {
        let mut i = input();
        *i.pointer_mut(pointer).unwrap() = bad;
        fs::write(&config, serde_json::to_vec(&i).unwrap()).unwrap();
        let o = call(
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
                "--infrastructure-config",
                config.to_str().unwrap(),
                "--out",
                out.to_str().unwrap(),
            ],
        );
        assert!(!o.status.success(), "accepted {pointer}");
        assert!(!out.exists());
    }
    let mut i = input();
    i["apiKey"] = json!("should-not-be-a-config-field");
    fs::write(&config, serde_json::to_vec(&i).unwrap()).unwrap();
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
        "arn:aws:iam::123456789012:role/operator",
        "--infrastructure-config",
        config.to_str().unwrap(),
        "--out",
        out.to_str().unwrap(),
    ];
    let o = call(home.path(), &args);
    assert!(!o.status.success());
    assert!(!String::from_utf8_lossy(&o.stderr).contains("should-not-be-a-config-field"));
    fs::write(&config, serde_json::to_vec(&input()).unwrap()).unwrap();
    let mut conflicting = args.to_vec();
    conflicting.extend([
        "--execution-role-arn",
        "arn:aws:iam::123456789012:role/executor",
    ]);
    assert!(!call(home.path(), &conflicting).status.success());
    assert!(!out.exists());
}
#[test]
fn every_exported_artifact_is_pinned_and_legacy_plans_keep_their_shape() {
    let home = tempfile::tempdir().unwrap();
    let (out, _) = generate(home.path(), "integrity", &input());
    let manifest = read(&out, "manifest.json");
    for name in manifest["artifacts"].as_object().unwrap().keys() {
        let path = out.join(name);
        let original = fs::read(&path).unwrap();
        fs::write(&path, "{}").unwrap();
        assert!(
            !call(
                home.path(),
                &["deploy", "validate", "--plan", out.to_str().unwrap()]
            )
            .status
            .success(),
            "ignored {name}"
        );
        fs::write(path, original).unwrap();
    }
    assert!(call(
        home.path(),
        &["deploy", "validate", "--plan", out.to_str().unwrap()]
    )
    .status
    .success());
    let legacy = home.path().join("legacy");
    let o = call(
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
            "old-style",
            "--out",
            legacy.to_str().unwrap(),
        ],
    );
    assert!(o.status.success());
    assert_eq!(read(&legacy, "deployment-plan.json")["schemaVersion"], 2);
    assert_eq!(fs::read_dir(legacy).unwrap().count(), 5);
}

#[cfg(unix)]
#[test]
fn generated_identity_bootstrap_does_not_require_a_preexisting_execution_role() {
    use std::os::unix::fs::{symlink, PermissionsExt};
    let home = tempfile::tempdir().unwrap();
    let (out, _) = generate(home.path(), "new-roles", &input());
    symlink(BIN, home.path().join("ows")).unwrap();
    let fake = home.path().join("aws");
    fs::write(&fake,r#"#!/bin/bash
set -eu
printf '%s\n' "$*" >> "$HOME/aws-calls"
case "$1 $2" in
 'sts get-caller-identity')
   case "$*" in *'--query Account'*) echo 123456789012;; *) echo arn:aws:sts::123456789012:assumed-role/operator/session;; esac;;
 'cloudformation create-stack')
   [ "$AWS_MAX_ATTEMPTS" = 1 ]
   [ "$AWS_CLI_AUTO_PROMPT" = off ]
   [ -z "$AWS_PAGER" ]
   if [ "${FAIL_CREATE:-no}" = yes ]; then echo 'unknown outcome' >&2; exit 50; fi
   echo '{"StackId":"fake-identity-stack"}';;
 'cloudformation wait') exit 0;;
 *) echo 'Unexpected AWS operation, including looking up the not-yet-created executor' >&2; exit 99;;
esac
"#).unwrap();
    fs::set_permissions(fake, fs::Permissions::from_mode(0o700)).unwrap();
    let run = |fail: &str| {
        Command::new("/bin/bash")
            .arg(out.join("bootstrap.sh"))
            .arg("--apply-identities")
            .env_clear()
            .env("HOME", home.path())
            .env("PATH", format!("{}:/usr/bin:/bin", home.path().display()))
            .env("FAIL_CREATE", fail)
            .output()
            .unwrap()
    };
    let ok = run("no");
    assert!(
        ok.status.success(),
        "{}",
        String::from_utf8_lossy(&ok.stderr)
    );
    let calls = fs::read_to_string(home.path().join("aws-calls")).unwrap();
    assert!(!calls.contains("iam get-role"));
    assert_eq!(calls.matches("cloudformation create-stack").count(), 1);
    assert!(!calls.contains("-infra "));
    assert!(!calls.contains("-guard "));
    fs::write(home.path().join("aws-calls"), "").unwrap();
    let fail = run("yes");
    assert!(!fail.status.success());
    assert!(String::from_utf8_lossy(&fail.stderr).contains("before any retry"));
    let calls = fs::read_to_string(home.path().join("aws-calls")).unwrap();
    assert_eq!(calls.matches("cloudformation create-stack").count(), 1);
    assert!(!calls.contains("cloudformation wait"));
}
