//! Offline export only. Upstream core controller + explicit trusted-component policies.
use super::*;
pub(super) const CONTROLLER:&str="registry.k8s.io/agent-sandbox/agent-sandbox-controller@sha256:14c479c9e89ca87d05dfcf597840473c0196d6e6eec622a7959e72108d75b24e";
pub(super) fn export(
    plan: &Path,
    out: &Path,
    shell_namespace: Option<&str>,
    shell_release: Option<&str>,
) -> Result<()> {
    let (p, m) = inspect(plan)?;
    ensure!(
        p.schema_version == 3 && p.infrastructure.is_some(),
        "AWS infrastructure plan required"
    );
    let source = include_bytes!("../../../deploy/service/platform/agent-sandbox-v1.0.6.yaml");
    ensure!(
        hash(source) == "b995db5839f2bdca3a24b8bbefdec405df662d6dd223eec8f5dc74098c974231",
        "Bundled upstream manifest changed; review a new pin"
    );
    let mut resources = vec![];
    for document in serde_yaml::Deserializer::from_slice(source) {
        let mut v = Value::deserialize(document)?;
        if v.is_null() {
            continue;
        }
        if v["kind"] == "Deployment" && v["metadata"]["name"] == "agent-sandbox-controller" {
            let containers = v["spec"]["template"]["spec"]["containers"]
                .as_array_mut()
                .context("Upstream controller missing")?;
            ensure!(
                containers.len() == 1
                    && containers[0]["image"]
                        == "registry.k8s.io/agent-sandbox/agent-sandbox-controller:v1.0.6",
                "Unexpected upstream image"
            );
            containers[0]["image"] = json!(CONTROLLER);
        }
        resources.push(v);
    }
    resources.push(json!({"apiVersion":"storage.k8s.io/v1","kind":"StorageClass","metadata":{"name":"ows-workspaces","annotations":{"storageclass.kubernetes.io/is-default-class":"true"}},"provisioner":"ebs.csi.aws.com","parameters":{"type":"gp3","encrypted":"true","tagSpecification_1":format!("ows:deployment-id={}",p.deployment_id)},"reclaimPolicy":"Delete","volumeBindingMode":"WaitForFirstConsumer","allowVolumeExpansion":true}));
    // Only trusted control components get these rules. OpenShell sandbox/tool pods
    // are in a separate namespace and retain the provider's per-sandbox policies.
    for (namespace, name, selector) in [
        (
            "kube-system",
            "ows-coredns",
            json!({"matchLabels":{"k8s-app":"kube-dns"}}),
        ),
        (
            "kube-system",
            "ows-ebs-controller",
            json!({"matchLabels":{"app":"ebs-csi-controller"}}),
        ),
        (
            "agent-sandbox-system",
            "ows-controller",
            json!({"matchLabels":{"app":"agent-sandbox-controller"}}),
        ),
        (
            "ow-service",
            "ows-trusted-service",
            json!({"matchExpressions":[{"key":"app","operator":"In","values":["ows-api","ows-worker","ows-controller","ows-platform","ows-ui","ows-bootstrap"]}]}),
        ),
    ] {
        resources.push(json!({"apiVersion":"networking.k8s.io/v1","kind":"NetworkPolicy","metadata":{"name":name,"namespace":namespace},"spec":{"podSelector":selector,"policyTypes":["Ingress","Egress"],"ingress":[{}],"egress":[{}]}}));
    }
    if let (Some(ns), Some(release)) = (shell_namespace, shell_release) {
        ensure!(
            [ns, release].iter().all(|s| !s.is_empty()
                && s.len() <= 63
                && s.bytes()
                    .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-')),
            "Invalid explicit gateway namespace/release"
        );
        ensure!(
            !["kube-system", "ow-service", "agent-sandbox-system"].contains(&ns),
            "Dedicated gateway namespace required"
        );
        resources.push(json!({"apiVersion":"networking.k8s.io/v1","kind":"NetworkPolicy","metadata":{"name":"ows-trusted-gateway","namespace":ns},"spec":{"podSelector":{"matchLabels":{"app.kubernetes.io/name":"openshell","app.kubernetes.io/instance":release}},"policyTypes":["Ingress","Egress"],"ingress":[{}],"egress":[{}]}}));
        // The pinned pre-install hook has different labels from the gateway.
        // It only needs Kubernetes Secret creation and DNS, not unrestricted egress.
        resources.push(json!({"apiVersion":"networking.k8s.io/v1","kind":"NetworkPolicy","metadata":{"name":"ows-gateway-certgen","namespace":ns},"spec":{"podSelector":{"matchLabels":{"app.kubernetes.io/name":"openshell-certgen","app.kubernetes.io/instance":release,"app.kubernetes.io/component":"certgen"}},"policyTypes":["Ingress","Egress"],"ingress":[],"egress":[{"ports":[{"protocol":"TCP","port":443}]},{"to":[{"namespaceSelector":{"matchLabels":{"kubernetes.io/metadata.name":"kube-system"}},"podSelector":{"matchLabels":{"k8s-app":"kube-dns"}}}],"ports":[{"protocol":"UDP","port":53},{"protocol":"TCP","port":53}]}]}}));
    }
    let artifact = json!({"apiVersion":"v1","kind":"List","items":resources});
    let bytes = json_bytes(&artifact)?;
    let mut options = fs::OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let mut file = options
        .open(out)
        .context("Fresh platform artifact output required")?;
    file.write_all(&bytes)?;
    file.sync_all()?;
    crate::output(
        json!({"file":out,"sha256":hash(&bytes),"deploymentId":p.deployment_id,"bundleDigest":m.bundle_digest,"upstreamVersion":"1.0.6","controller":CONTROLLER,"cloudCalls":false,"installed":false,"requiresCniAndFunctionalNetworkQualification":true}),
    )
}
