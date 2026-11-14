//! Azure setup uses the same fixed resource graph and exact role payloads as
//! the reviewed exporters. Every additional write has its own durable intent.
use super::*;
use api::Azure;
use reqwest::Method;
#[derive(Clone, Copy, clap::ValueEnum)]
pub enum UpPhase {
    Setup,
    Control,
    Foundation,
    Federation,
}
fn save(path: &Path, v: &Value) -> Result<()> {
    super::super::up::write_private(path, v)
}
fn expected_properties(v: &Value) -> Value {
    let mut props = v.get("properties").cloned().unwrap_or(Value::Null);
    if let Some(o) = props.as_object_mut() {
        for k in [
            "createdOn",
            "updatedOn",
            "createdBy",
            "updatedBy",
            "provisioningState",
            "scope",
        ] {
            o.remove(k);
        }
    }
    props
}
async fn put_exact(
    api: &mut Azure,
    dir: &Path,
    label: &str,
    id: &str,
    version: &str,
    body: Value,
) -> Result<()> {
    let path = dir.join(format!("{label}.json"));
    let old: Option<Value> = if path.exists() {
        Some(serde_json::from_slice(&fs::read(&path)?)?)
    } else {
        None
    };
    let body_hash = hash(&json_bytes(&body)?);
    if let Some(r) = &old {
        ensure!(
            r["id"] == id && r["bodyHash"] == body_hash,
            "Original role/grant request changed"
        );
    }
    let current = api
        .arm(
            Method::GET,
            id,
            version,
            None,
            &uuid::Uuid::new_v4().to_string(),
        )
        .await?;
    if current.status == 200 {
        let observed = current.body.context("Role observation missing")?;
        let expected = expected_properties(&body);
        let actual = expected_properties(&observed);
        let good = expected
            .as_object()
            .is_some_and(|m| m.iter().all(|(k, v)| actual.get(k) == Some(v)));
        ensure!(
            good,
            "Existing role/grant differs from reviewed payload; refusing overwrite"
        );
        let mut receipt =
            old.unwrap_or_else(|| json!({"id":id,"bodyHash":body_hash,"preexisting":true}));
        receipt["phase"] = json!("observed");
        save(&path, &receipt)?;
        return Ok(());
    }
    ensure!(
        current.status == 404,
        "Unable to prove role/grant absence; no write attempted"
    );
    if path.exists() {
        let old: Value = serde_json::from_slice(&fs::read(&path)?)?;
        ensure!(
            old["phase"] == "rejected" && [401, 403].contains(&old["status"].as_u64().unwrap_or(0)),
            "Prior role/grant outcome unknown; observe original request before retrying"
        );
        ensure!(
            old["id"] == id && old["bodyHash"] == hash(&json_bytes(&body)?),
            "Role/grant request changed"
        );
    }
    let request = uuid::Uuid::new_v4().to_string();
    let mut receipt =
        json!({"id":id,"bodyHash":hash(&json_bytes(&body)?),"requestId":request,"phase":"intent"});
    if let Some(previous) = old {
        receipt["previousAttempts"] = json!([previous]);
    }
    save(&path, &receipt)?;
    let response = api
        .arm(Method::PUT, id, version, Some(&body), &request)
        .await?;
    if [401, 403].contains(&response.status) {
        receipt["phase"] = json!("rejected");
        receipt["status"] = json!(response.status);
        save(&path, &receipt)?;
        bail!("Administrator permission is missing for an exact reviewed role/grant; no automatic retry");
    }
    ensure!(
        [200, 201].contains(&response.status),
        "Role/grant outcome not confirmed; inspect original intent"
    );
    receipt["phase"] = json!("accepted");
    save(&path, &receipt)?;
    Ok(())
}
async fn deploy(
    api: &mut Azure,
    dir: &Path,
    identity: (&Plan, &str, &str),
    scope: &str,
    label: &str,
    mut template: Value,
    parameters: Value,
) -> Result<bool> {
    let (p, nonce, bundle) = identity;
    let path = dir.join(format!("deployment-{label}.json"));
    let name = format!("{}-up-{label}", graph::names(p).prefix);
    let id = format!("{scope}/providers/Microsoft.Resources/deployments/{name}");
    for field in ["owsDeployment", "owsNonce", "owsBundle"] {
        template["parameters"][field] = json!({"type":"string"});
    }
    let mut params = parameters;
    params["owsDeployment"] = json!({"value":p.deployment_id});
    params["owsNonce"] = json!({"value":nonce});
    params["owsBundle"] = json!({"value":bundle});
    let body = json!({"properties":{"mode":"Incremental","template":template,"parameters":params}});
    let before = api
        .arm(
            Method::GET,
            &id,
            "2022-09-01",
            None,
            &uuid::Uuid::new_v4().to_string(),
        )
        .await?;
    if before.status == 200 {
        ensure!(
            path.exists(),
            "Existing ARM deployment lacks original local intent; refusing adoption"
        );
        let mut receipt: Value = serde_json::from_slice(&fs::read(&path)?)?;
        ensure!(
            receipt["bodyHash"] == hash(&json_bytes(&body)?),
            "Original ARM deployment template changed"
        );
        let live = before.body.context("ARM deployment observation missing")?;
        ensure!(
            parameter_values(&live["properties"]["parameters"])? == parameter_values(&params)?,
            "Original ARM deployment parameters changed"
        );
        let exported = api
            .arm(
                Method::POST,
                &format!("{id}/exportTemplate"),
                "2022-09-01",
                None,
                &uuid::Uuid::new_v4().to_string(),
            )
            .await?;
        ensure!(
            exported.status == 200
                && exported
                    .body
                    .as_ref()
                    .is_some_and(|v| v["template"] == body["properties"]["template"]),
            "Original ARM template differs or could not be verified; do not advance"
        );
        let correlation = live["properties"]["correlationId"]
            .as_str()
            .context("ARM correlation missing")?;
        ensure!(
            receipt
                .get("correlationId")
                .is_none_or(|old| old == correlation),
            "ARM deployment replaced"
        );
        receipt["correlationId"] = json!(correlation);
        save(&path, &receipt)?;
        match live["properties"]["provisioningState"].as_str() {
            Some("Succeeded") => return Ok(true),
            Some("Failed" | "Canceled") => bail!(
                "Reviewed ARM deployment failed; inspect the original deployment before continuing"
            ),
            _ => return Ok(false),
        }
    }
    ensure!(
        before.status == 404 && !path.exists(),
        "ARM deployment outcome unknown; do not resubmit the original request"
    );
    let request = uuid::Uuid::new_v4().to_string();
    save(
        &path,
        &json!({"id":id,"requestId":request,"bodyHash":hash(&json_bytes(&body)?),"phase":"intent"}),
    )?;
    let response = api
        .arm(Method::PUT, &id, "2022-09-01", Some(&body), &request)
        .await?;
    ensure!(
        [200, 201, 202].contains(&response.status),
        "ARM deployment outcome not confirmed; inspect original request"
    );
    Ok(false)
}
fn file(root: &Path, name: &str) -> Result<Value> {
    Ok(serde_json::from_slice(&super::super::read_file(
        root, name,
    )?)?)
}
fn path(url: &str) -> Result<&str> {
    url.strip_prefix("https://management.azure.com")
        .and_then(|s| s.split('?').next())
        .context("Invalid generated role URL")
}
fn principal_id(body: &Value) -> Result<&str> {
    body["properties"]["principalId"]
        .as_str()
        .filter(|s| uuid::Uuid::parse_str(s).is_ok())
        .context("Managed identity principal missing")
}
fn resolve_references(v: &mut Value, identity: &str) {
    match v {
        Value::String(s) if s.starts_with("[reference(") && s.ends_with(".principalId]") => {
            *s = identity.into()
        }
        Value::Object(o) => {
            for v in o.values_mut() {
                resolve_references(v, identity)
            }
        }
        Value::Array(a) => {
            for v in a {
                resolve_references(v, identity)
            }
        }
        _ => {}
    }
}
pub(super) async fn run(
    plan: &Path,
    state: &Path,
    phase: UpPhase,
    accept: &str,
    approved: bool,
) -> Result<()> {
    let (p, m) = super::super::inspect(plan)?;
    ensure!(
        approved && accept == m.bundle_digest,
        "Explicit approval of the exact Azure identity/control bundle is required"
    );
    let (bound, bundle, nonce) = recovery::up_local(state)?;
    ensure!(
        bound == p && bundle == m.bundle_digest,
        "Azure local lifecycle identity mismatch"
    );
    let dir = state.join("up-operations");
    super::super::up::directory(&dir)?;
    let _lock = super::super::up::operation_lock(&dir, "bootstrap.lock")?;
    let mut api = Azure::new(&p, None)?;
    api.check_setup_identity(&p).await?;
    let work = scope(&p);
    let control = graph::control_scope(&p);
    let names = graph::names(&p);
    match phase {
        UpPhase::Setup => {
            put_exact(
                &mut api,
                &dir,
                "setup-role",
                path(&role_url(&p))?,
                "2022-04-01",
                file(plan, "azure-setup-role.json")?,
            )
            .await?;
            put_exact(
                &mut api,
                &dir,
                "setup-assignment",
                path(&assignment_url(&p))?,
                "2022-04-01",
                file(plan, "azure-setup-assignment.json")?,
            )
            .await?;
            let executor = file(plan, "azure-executor-identity.json")?;
            put_exact(
                &mut api,
                &dir,
                "executor-role",
                path(
                    executor["roleUrl"]
                        .as_str()
                        .context("Executor role URL missing")?,
                )?,
                "2022-04-01",
                file(plan, "azure-executor-role.json")?,
            )
            .await?;
            put_exact(
                &mut api,
                &dir,
                "executor-assignment",
                path(
                    executor["assignmentUrl"]
                        .as_str()
                        .context("Executor assignment URL missing")?,
                )?,
                "2022-04-01",
                json!({"properties":executor["assignment"]["properties"]}),
            )
            .await?;
            apply_grants(&mut api, &dir, plan, "setup").await?;
            crate::output(json!({"phase":"ready","scope":work}))
        }
        UpPhase::Control => {
            if dir.join("control-init.json").exists() {
                let ready = recovery::up_initialize_control(state).await?;
                return crate::output(json!({"phase":if ready{"ready"}else{"waiting_heartbeat"}}));
            }
            let out = state.join("control-package");
            if !out.exists() {
                recovery::up_package(state, &out)?;
            }
            let mut identity_template = file(&out, "azure-control.arm.json")?;
            identity_template["resources"]
                .as_array_mut()
                .context("Control resources missing")?
                .retain(|r| r["type"] == "Microsoft.ManagedIdentity/userAssignedIdentities");
            if !deploy(
                &mut api,
                &dir,
                (&p, &nonce, &bundle),
                &control,
                "control-identity",
                identity_template,
                json!({}),
            )
            .await?
            {
                return crate::output(json!({"phase":"creating_identity"}));
            }
            apply_grants(&mut api, &dir, plan, "control-identity").await?;
            let ready = deploy(
                &mut api,
                &dir,
                (&p, &nonce, &bundle),
                &control,
                "control",
                file(&out, "azure-control.arm.json")?,
                json!({}),
            )
            .await?;
            if !ready {
                return crate::output(json!({"phase":"creating"}));
            }
            let identity_path = format!(
                "{control}/providers/Microsoft.ManagedIdentity/userAssignedIdentities/{}-cleanup",
                names.prefix
            );
            let identity = api
                .arm(Method::GET, &identity_path, "2023-01-31", None, &nonce)
                .await?;
            ensure!(identity.status == 200, "Cleanup identity not ready");
            let identity = identity.body.context("Cleanup identity missing")?;
            let principal = principal_id(&identity)?;
            let bindings = file(&out, "azure-control-bindings.json")?;
            for (i, assignment) in bindings["assignments"]
                .as_array()
                .context("Control bindings missing")?
                .iter()
                .enumerate()
            {
                let mut body = json!({"properties":{"principalId":assignment["principalId"],"principalType":assignment["principalType"],"roleDefinitionId":bindings["roleDefinitionId"]}});
                resolve_references(&mut body, principal);
                let id = format!(
                    "{}/providers/Microsoft.Authorization/roleAssignments/{}",
                    bindings["scope"].as_str().unwrap(),
                    assignment["guid"].as_str().unwrap()
                );
                put_exact(
                    &mut api,
                    &dir,
                    &format!("control-blob-{i}"),
                    &id,
                    "2022-04-01",
                    body,
                )
                .await?;
            }
            for (label, role, assignment) in [
                (
                    "workload",
                    "azure-cleanup-role.json",
                    "azure-cleanup-assignment.json",
                ),
                ("stop", "azure-cleanup-stop-role.json", ""),
            ] {
                let role = file(&out, role)?;
                let role_guid = guid(
                    &p,
                    if label == "workload" {
                        "cleanup-role"
                    } else {
                        "cleanup-stop-role"
                    },
                );
                let role_scope = if label == "workload" { &work } else { &control };
                let role_path = format!(
                    "{role_scope}/providers/Microsoft.Authorization/roleDefinitions/{role_guid}"
                );
                put_exact(
                    &mut api,
                    &dir,
                    &format!("cleanup-{label}-role"),
                    &role_path,
                    "2022-04-01",
                    json!({"properties":role["properties"]}),
                )
                .await?;
                if !assignment.is_empty() {
                    let mut body = file(&out, assignment)?;
                    resolve_references(&mut body, principal);
                    let id = format!(
                        "{work}/providers/Microsoft.Authorization/roleAssignments/{}",
                        guid(&p, "cleanup-workload")
                    );
                    put_exact(
                        &mut api,
                        &dir,
                        "cleanup-workload-assignment",
                        &id,
                        "2022-04-01",
                        body,
                    )
                    .await?;
                }
            }
            let stop = file(&out, "azure-cleanup-stop-role.json")?;
            let Target::Azure { subscription, .. } = &p.target else {
                unreachable!()
            };
            let stop_assignment = format!(
                "{}/providers/Microsoft.Authorization/roleAssignments/{}",
                stop["assignmentScope"].as_str().unwrap(),
                stop["assignmentGuid"].as_str().unwrap()
            );
            let stop_role=format!("/subscriptions/{subscription}/providers/Microsoft.Authorization/roleDefinitions/{}",stop["roleDefinitionGuid"].as_str().unwrap());
            put_exact(&mut api,&dir,"cleanup-stop-assignment",&stop_assignment,"2022-04-01",json!({"properties":{"principalId":principal,"principalType":"ServicePrincipal","roleDefinitionId":stop_role}})).await?;
            let registry = file(&out, "azure-control-registry-binding.json")?;
            if let Some(scope) = registry["scope"].as_str() {
                let id = format!(
                    "{scope}/providers/Microsoft.Authorization/roleAssignments/{}",
                    registry["assignmentGuid"].as_str().unwrap()
                );
                put_exact(&mut api,&dir,"cleanup-registry-pull",&id,"2022-04-01",json!({"properties":{"principalId":principal,"principalType":"ServicePrincipal","roleDefinitionId":registry["roleDefinitionId"]}})).await?;
            }
            let ready = recovery::up_initialize_control(state).await?;
            crate::output(json!({"phase":if ready{"ready"}else{"waiting_heartbeat"}}))
        }
        UpPhase::Foundation | UpPhase::Federation => {
            apply_grants(
                &mut api,
                &dir,
                plan,
                if matches!(phase, UpPhase::Foundation) {
                    "foundation"
                } else {
                    "federation"
                },
            )
            .await?;
            let label = if matches!(phase, UpPhase::Foundation) {
                "bindings"
            } else {
                "federation"
            };
            let ready = deploy(
                &mut api,
                &dir,
                (&p, &nonce, &bundle),
                &work,
                label,
                file(plan, &format!("azure-{label}.arm.json"))?,
                json!({}),
            )
            .await?;
            crate::output(json!({"phase":if ready{"ready"}else{"creating"}}))
        }
    }
}

/// Complete, reviewable supplemental authority. No subscription-wide roles,
/// role-assignment writes or secret enumeration are granted to the executor.
pub(super) fn grants(p: &Plan) -> Result<Value> {
    let Target::Azure { subscription, .. } = &p.target else {
        bail!("Azure required")
    };
    let i = p
        .azure_bootstrap
        .as_ref()
        .context("Setup identity missing")?;
    let n = graph::names(p);
    let control = graph::control_scope(p);
    let work = scope(p);
    let input = serde_json::to_value(p.azure_infrastructure.as_ref().unwrap())?;
    let vault = input["postgresPassword"]["keyVaultId"].as_str().unwrap();
    let secret = format!(
        "{vault}/secrets/{}",
        input["postgresPassword"]["secretName"].as_str().unwrap()
    );
    let mut out = vec![];
    let mut custom = |label: &str,
                      phase: &str,
                      scope: &str,
                      actions: Vec<&str>,
                      data: Vec<&str>| {
        let role = guid(p, &format!("up-{label}-role"));
        let assignment = guid(p, &format!("up-{label}-grant"));
        let definition_scope = scope.split("/providers/").next().unwrap();
        let role_id=format!("/subscriptions/{subscription}/providers/Microsoft.Authorization/roleDefinitions/{role}");
        out.push(json!({"label":format!("{label}-role"),"phase":phase,"id":format!("{definition_scope}/providers/Microsoft.Authorization/roleDefinitions/{role}"),"body":{"properties":{"roleName":format!("{}-{label}",n.prefix),"type":"CustomRole","assignableScopes":[definition_scope],"permissions":[{"actions":actions,"notActions":[],"dataActions":data,"notDataActions":[]}]}}}));
        out.push(json!({"label":format!("{label}-grant"),"phase":phase,"id":format!("{scope}/providers/Microsoft.Authorization/roleAssignments/{assignment}"),"body":{"properties":{"principalId":i.setup_object_id,"principalType":i.setup_principal_type.azure(),"roleDefinitionId":role_id}}}));
    };
    custom(
        "control-setup",
        "setup",
        &control,
        vec![
            "Microsoft.Resources/deployments/read",
            "Microsoft.Resources/deployments/write",
            "Microsoft.Resources/deployments/operations/read",
            "Microsoft.Resources/deployments/operationStatuses/read",
            "Microsoft.Resources/deployments/exportTemplate/action",
            "Microsoft.ManagedIdentity/userAssignedIdentities/read",
            "Microsoft.ManagedIdentity/userAssignedIdentities/write",
            "Microsoft.Storage/storageAccounts/read",
            "Microsoft.Storage/storageAccounts/write",
            "Microsoft.Storage/storageAccounts/blobServices/containers/read",
            "Microsoft.Storage/storageAccounts/blobServices/containers/write",
            "Microsoft.App/managedEnvironments/read",
            "Microsoft.App/managedEnvironments/write",
            "Microsoft.App/managedEnvironments/join/action",
            "Microsoft.App/jobs/read",
            "Microsoft.App/jobs/write",
        ],
        vec![],
    );
    custom(
        "vault-deploy",
        "setup",
        vault,
        vec![
            "Microsoft.KeyVault/vaults/read",
            "Microsoft.KeyVault/vaults/deploy/action",
        ],
        vec![],
    );
    custom(
        "workload-inspection",
        "setup",
        &work,
        vec!["Microsoft.Resources/deployments/exportTemplate/action"],
        vec![],
    );
    custom(
        "cluster-credentials",
        "federation",
        &format!(
            "{work}/providers/Microsoft.ContainerService/managedClusters/{}-aks",
            n.prefix
        ),
        vec![
            "Microsoft.ContainerService/managedClusters/read",
            "Microsoft.ContainerService/managedClusters/listClusterUserCredential/action",
        ],
        vec![],
    );
    custom(
        "artifact-federation",
        "foundation",
        &format!(
            "{work}/providers/Microsoft.ManagedIdentity/userAssignedIdentities/{}-artifacts",
            n.prefix
        ),
        vec![
            "Microsoft.ManagedIdentity/userAssignedIdentities/federatedIdentityCredentials/read",
            "Microsoft.ManagedIdentity/userAssignedIdentities/federatedIdentityCredentials/write",
        ],
        vec![],
    );
    // Secret-level assignment: no permission to enumerate/read other secrets.
    out.push(json!({"label":"database-secret-read","phase":"setup","id":format!("{secret}/providers/Microsoft.Authorization/roleAssignments/{}",guid(p,"up-secret-read")),"body":{"properties":{"principalId":i.setup_object_id,"principalType":i.setup_principal_type.azure(),"roleDefinitionId":format!("/subscriptions/{subscription}/providers/Microsoft.Authorization/roleDefinitions/4633458b-17de-408a-b874-0445c86b69e6")}}}));
    for (label, phase, id) in [
        (
            "cleanup",
            "control-identity",
            format!(
                "{control}/providers/Microsoft.ManagedIdentity/userAssignedIdentities/{}-cleanup",
                n.prefix
            ),
        ),
        (
            "cluster",
            "foundation",
            format!(
                "{work}/providers/Microsoft.ManagedIdentity/userAssignedIdentities/{}-cluster",
                n.prefix
            ),
        ),
        (
            "kubelet",
            "foundation",
            format!(
                "{work}/providers/Microsoft.ManagedIdentity/userAssignedIdentities/{}-kubelet",
                n.prefix
            ),
        ),
    ] {
        out.push(json!({"label":format!("{label}-assign"),"phase":phase,"id":format!("{id}/providers/Microsoft.Authorization/roleAssignments/{}",guid(p,&format!("up-{label}-assign"))),"body":{"properties":{"principalId":i.setup_object_id,"principalType":i.setup_principal_type.azure(),"roleDefinitionId":format!("/subscriptions/{subscription}/providers/Microsoft.Authorization/roleDefinitions/f1a07417-d97a-45cb-824c-7a7467783830")}}}));
    }
    out.push(json!({"label":"registry-push","phase":"foundation","id":format!("{work}/providers/Microsoft.ContainerRegistry/registries/{}/providers/Microsoft.Authorization/roleAssignments/{}",n.registry,guid(p,"up-registry-push")),"body":{"properties":{"principalId":i.setup_object_id,"principalType":i.setup_principal_type.azure(),"roleDefinitionId":format!("/subscriptions/{subscription}/providers/Microsoft.Authorization/roleDefinitions/8311e382-0749-4cb8-b61a-304f252e45ec")}}}));
    Ok(json!({"schemaVersion":1,"deploymentId":p.deployment_id,"grants":out}))
}
async fn apply_grants(api: &mut Azure, dir: &Path, plan: &Path, phase: &str) -> Result<()> {
    let v = file(plan, "azure-up-grants.json")?;
    for g in v["grants"]
        .as_array()
        .context("Reviewed grants missing")?
        .iter()
        .filter(|g| g["phase"] == phase)
    {
        put_exact(
            api,
            dir,
            g["label"].as_str().context("Grant label missing")?,
            g["id"].as_str().context("Grant scope missing")?,
            "2022-04-01",
            g["body"].clone(),
        )
        .await?;
    }
    Ok(())
}

fn parameter_values(parameters: &Value) -> Result<BTreeMap<String, Value>> {
    parameters
        .as_object()
        .context("ARM parameter map missing")?
        .iter()
        .map(|(name, value)| {
            Ok((
                name.clone(),
                value
                    .get("value")
                    .cloned()
                    .context("ARM parameter value missing")?,
            ))
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    fn response(status: u16, body: Value) -> String {
        let body = body.to_string();
        format!("HTTP/1.1 {status} fixture\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",body.len())
    }
    const ID:&str="/subscriptions/66666666-7777-4888-8999-aaaaaaaaaaaa/resourceGroups/workload/providers/Microsoft.Authorization/roleAssignments/original";
    #[tokio::test]
    async fn lost_grant_response_is_never_replayed_on_absence() {
        let (mut api, log, thread) = api::tests::fixture(vec![
            Some(response(404, json!({}))),
            None,
            Some(response(404, json!({}))),
        ]);
        let root = tempfile::tempdir().unwrap();
        let body = json!({"properties":{"principalId":"same","roleDefinitionId":"exact"}});
        assert!(put_exact(
            &mut api,
            root.path(),
            "grant",
            ID,
            "2022-04-01",
            body.clone()
        )
        .await
        .is_err());
        let first: Value =
            serde_json::from_slice(&fs::read(root.path().join("grant.json")).unwrap()).unwrap();
        assert_eq!(first["phase"], "intent");
        assert!(
            put_exact(&mut api, root.path(), "grant", ID, "2022-04-01", body)
                .await
                .is_err()
        );
        thread.join().unwrap();
        assert_eq!(
            log.lock()
                .unwrap()
                .iter()
                .filter(|r| r.starts_with("PUT "))
                .count(),
            1
        );
    }
    #[tokio::test]
    async fn observed_grant_preserves_original_request_and_refuses_drift() {
        let body = json!({"properties":{"principalId":"same","roleDefinitionId":"exact"}});
        let (mut api, _, thread) = api::tests::fixture(vec![Some(response(200, body.clone()))]);
        let root = tempfile::tempdir().unwrap();
        let receipt = json!({"id":ID,"bodyHash":hash(&json_bytes(&body).unwrap()),"requestId":"original-request","phase":"accepted","previousAttempts":[{"requestId":"rejected-request"}]});
        save(&root.path().join("grant.json"), &receipt).unwrap();
        put_exact(&mut api, root.path(), "grant", ID, "2022-04-01", body)
            .await
            .unwrap();
        thread.join().unwrap();
        let r: Value =
            serde_json::from_slice(&fs::read(root.path().join("grant.json")).unwrap()).unwrap();
        assert_eq!(r["requestId"], "original-request");
        assert_eq!(r["previousAttempts"], receipt["previousAttempts"]);
        assert!(put_exact(
            &mut api,
            root.path(),
            "grant",
            ID,
            "2022-04-01",
            json!({"properties":{"principalId":"other"}})
        )
        .await
        .is_err());
    }
}
