//! Reviewed, pinned platform resources. Create and reconcile; never overwrite/adopt.
use super::*;
#[derive(Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct Artifact {
    pub file: PathBuf,
    pub sha256: String,
}
pub(super) fn validate(artifact: &Artifact) -> Result<()> {
    ensure!(
        artifact.sha256.len() == 64 && hash_path(&artifact.file)? == artifact.sha256,
        "Platform artifact digest mismatch"
    );
    let bytes = fs::read(&artifact.file)?;
    let mut count = 0;
    for document in serde_yaml::Deserializer::from_slice(&bytes) {
        let value = Value::deserialize(document).context("Invalid platform manifest")?;
        if value.is_null() {
            continue;
        }
        for object in objects(value)? {
            check(&object)?;
            count += 1;
            ensure!(count <= 64, "Platform resource inventory limit");
        }
    }
    ensure!(count > 0, "Empty platform artifact");
    Ok(())
}
fn objects(value: Value) -> Result<Vec<Value>> {
    if value["kind"] == "List" {
        Ok(value["items"]
            .as_array()
            .context("Invalid manifest List")?
            .clone())
    } else {
        Ok(vec![value])
    }
}
fn check(v: &Value) -> Result<()> {
    let kind = v["kind"].as_str().context("Platform kind missing")?;
    ensure!(
        [
            "Namespace",
            "CustomResourceDefinition",
            "ClusterRole",
            "ClusterRoleBinding",
            "Role",
            "RoleBinding",
            "ServiceAccount",
            "Deployment",
            "ConfigMap",
            "Service",
            "NetworkPolicy",
            "StorageClass"
        ]
        .contains(&kind),
        "Unsupported platform resource kind"
    );
    let name = v["metadata"]["name"]
        .as_str()
        .context("Platform resource name missing")?;
    ensure!(
        !name.is_empty()
            && name.len() <= 253
            && name
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b".-".contains(&b)),
        "Invalid platform name"
    );
    let ns = v["metadata"]["namespace"].as_str().unwrap_or("");
    ensure!(
        ns.is_empty() || bounded_name(ns),
        "Invalid platform namespace"
    );
    ensure!(
        v["metadata"].get("generateName").is_none()
            && v["metadata"].get("ownerReferences").is_none(),
        "Generated/externally-owned platform resources forbidden"
    );
    if kind == "Namespace" {
        ensure!(
            name == "agent-sandbox-system",
            "Only the upstream controller namespace may be created by platform artifact"
        );
    }
    if kind == "Deployment" {
        let spec = &v["spec"]["template"]["spec"];
        ensure!(
            spec["hostNetwork"] != true && spec["hostPID"] != true && spec["hostIPC"] != true,
            "Controller cannot share host namespaces"
        );
        for container in spec["containers"]
            .as_array()
            .context("Controller containers missing")?
        {
            ensure!(
                container["image"].as_str().is_some_and(pinned)
                    && container["securityContext"]["privileged"] != true,
                "Controller images require digest pins and no privileged mode"
            );
        }
    }
    if kind == "StorageClass" {
        ensure!(
            v["provisioner"] == "ebs.csi.aws.com"
                && v["reclaimPolicy"] == "Delete"
                && v["volumeBindingMode"] == "WaitForFirstConsumer"
                && v["parameters"]["encrypted"] == "true",
            "Platform storage must be encrypted EBS with Delete reclaim and deferred binding"
        );
    }
    Ok(())
}
fn subset(expected: &Value, actual: &Value) -> bool {
    match expected {
        Value::Object(o) => actual.as_object().is_some_and(|a| {
            o.iter()
                .all(|(k, v)| a.get(k).is_some_and(|x| subset(v, x)))
        }),
        Value::Array(a) => actual
            .as_array()
            .is_some_and(|b| a.len() == b.len() && a.iter().zip(b).all(|(a, b)| subset(a, b))),
        _ => expected == actual,
    }
}
fn platform_matches(expected: &Value, actual: &Value) -> bool {
    let mut expected = expected.clone();
    // API-server omitempty drops empty NetworkPolicy rule lists. With the
    // corresponding policyType present, absence and [] both deny that direction.
    // Do not generalize this to selectors or allow rules (where {} means allow).
    if expected["kind"] == "NetworkPolicy" {
        for (field, direction) in [("ingress", "Ingress"), ("egress", "Egress")] {
            if expected["spec"][field] == json!([])
                && actual["spec"].get(field).is_none()
                && expected["spec"]["policyTypes"]
                    .as_array()
                    .is_some_and(|a| a.contains(&json!(direction)))
            {
                expected["spec"].as_object_mut().unwrap().remove(field);
            }
        }
    }
    subset(&expected, actual)
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn omitted_empty_network_rules_are_equivalent_only_for_explicit_deny_direction() {
        let expected = json!({"kind":"NetworkPolicy","spec":{"policyTypes":["Ingress"],"ingress":[],"podSelector":{"matchLabels":{"app":"hook"}}}});
        let mut actual = expected.clone();
        actual["spec"].as_object_mut().unwrap().remove("ingress");
        assert!(platform_matches(&expected, &actual));
        actual["spec"]["ingress"] = json!([{}]);
        assert!(!platform_matches(&expected, &actual));
        actual = expected.clone();
        actual["spec"]["podSelector"] = json!({});
        assert!(!platform_matches(&expected, &actual));
        actual = expected.clone();
        actual["spec"]["policyTypes"] = json!(["Egress"]);
        assert!(!platform_matches(&expected, &actual));
    }
}
pub(super) async fn apply(
    client: &Client<'_>,
    p: &Plan,
    root: &Path,
    j: &mut Journal,
    allow_cloud: bool,
) -> Result<()> {
    ensure!(
        (p.config.cloud.is_none() && p.config.azure.is_none())
            || p.config.platform.is_none()
            || allow_cloud,
        "Cloud platform preparation requires --allow-experimental-cloud"
    );
    namespace(
        client,
        p,
        root,
        j,
        &p.config.service.namespace,
        read_json(&p.config.service.values_file)?["config"]["service"]["id"]
            .as_str()
            .unwrap(),
    )
    .await?;
    if let Shell::Install { namespace: ns, .. } = &p.config.openshell {
        namespace(
            client,
            p,
            root,
            j,
            ns,
            read_json(&p.config.service.values_file)?["config"]["service"]["id"]
                .as_str()
                .unwrap(),
        )
        .await?;
    }
    if let Some(artifact) = &p.config.platform {
        validate(artifact)?;
        let bytes = fs::read(&artifact.file)?;
        for document in serde_yaml::Deserializer::from_slice(&bytes) {
            let value = Value::deserialize(document)?;
            if value.is_null() {
                continue;
            }
            for mut object in objects(value)? {
                check(&object)?;
                ensure!(
                    [
                        "",
                        "agent-sandbox-system",
                        "kube-system",
                        "ow-service",
                        p.config.openshell.scope().0
                    ]
                    .contains(&object["metadata"]["namespace"].as_str().unwrap_or("")),
                    "Platform namespace outside bound gateway/service bootstrap scope"
                );
                let kind = object["kind"].as_str().unwrap().to_string();
                let name = object["metadata"]["name"].as_str().unwrap().to_string();
                let ns = object["metadata"]["namespace"].as_str().map(str::to_owned);
                let key = format!("platform:{kind}:{}:{name}", ns.as_deref().unwrap_or(""));
                if kind == "StorageClass" && p.config.cloud.is_some() {
                    let scope = cloud::install_scope(p.config.cloud.as_ref().unwrap())?;
                    ensure!(
                        object["parameters"]["tagSpecification_1"]
                            == format!("ows:deployment-id={}", scope.0),
                        "EBS volumes require the original cloud deployment ownership tag"
                    );
                }
                let existing = client.get(&kind, &name, ns.as_deref()).await?;
                if existing.is_none() {
                    ensure!(!j.operations.contains_key(&key),"Platform create outcome unknown; inspect original object, no automatic replay");
                    begin(root, j, &key)?;
                }
                let op=j.operations.get(&key).context("Platform resource already exists; select existing-platform mode, no adoption/overwrite")?;
                object["metadata"]["labels"][OWNER] = json!(p.id);
                object["metadata"]["annotations"]["openworker.io/operation-id"] =
                    json!(op.request_id);
                let actual = if let Some(v) = existing {
                    v
                } else {
                    client
                        .kube(
                            args(&["create", "-f", "-"]),
                            Some(serde_json::to_vec(&object)?),
                        )
                        .await?;
                    client
                        .get(&kind, &name, ns.as_deref())
                        .await?
                        .context("Platform creation not confirmed")?
                };
                ensure!(
                    platform_matches(&object, &actual),
                    "Platform content/ownership drift; no automatic overwrite"
                );
                if let Some(receipt) = &op.receipt {
                    ensure!(
                        receipt["uid"] == actual["metadata"]["uid"],
                        "Platform resource identity changed"
                    );
                }
                complete(root, j, &key, json!({"uid":actual["metadata"]["uid"]}))?;
            }
        }
        client
            .kube(
                args(&[
                    "-n",
                    "agent-sandbox-system",
                    "rollout",
                    "status",
                    "deployment/agent-sandbox-controller",
                    "--timeout=180s",
                ]),
                None,
            )
            .await?;
        if let Some(binding) = &p.config.cloud {
            ensure!(
                allow_cloud,
                "Changing the cloud CNI requires explicit --allow-experimental-cloud"
            );
            if !cloud::enable_strict_network(binding).await? {
                j.phase = "platform_configuring".into();
                save(root, "journal.json", j)?;
                return crate::output(
                    json!({"phase":j.phase,"ready":false,"message":"Observe the original CNI update by rerunning platform; no second update will be sent"}),
                );
            }
        }
    }
    prerequisites(client, p.config.cloud.is_some()).await?;
    j.phase = "platform_ready".into();
    save(root, "journal.json", j)?;
    crate::output(
        json!({"phase":j.phase,"functionalNetworkQualification":"required_separately","sharedPlatformRetainedOnServiceTeardown":true}),
    )
}
