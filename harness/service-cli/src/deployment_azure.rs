//! Azure identity, infrastructure and lifecycle contracts. Normal deployment authority never includes RBAC
//! writes, credential generation, or implicit managed-identity impersonation.
use super::*;
#[path = "deployment_azure_infrastructure.rs"]
mod graph;
#[path = "deployment_azure_preflight.rs"]
mod observation;
pub(super) use graph::{
    artifacts as infrastructure_artifacts, infrastructure, Infrastructure, Inputs,
};
pub(super) use observation::{preflight, Preflight};

#[derive(Args)]
pub struct Bootstrap {
    #[arg(long)]
    plan: PathBuf,
    #[arg(long)]
    out: PathBuf,
    /// Existing, customer-owned dedicated resource group. Not created/adopted here.
    #[arg(long)]
    resource_group: String,
    /// Exact existing Entra object ID, not client/application ID or email.
    #[arg(long)]
    setup_object_id: String,
    #[arg(long, value_enum, default_value = "service-principal")]
    setup_principal_type: PrincipalType,
}
#[derive(Clone, Debug, clap::ValueEnum, Deserialize, Serialize, PartialEq)]
pub(super) enum PrincipalType {
    User,
    ServicePrincipal,
}
impl PrincipalType {
    fn azure(&self) -> &'static str {
        match self {
            Self::User => "User",
            Self::ServicePrincipal => "ServicePrincipal",
        }
    }
}
#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct Identity {
    resource_group: String,
    setup_object_id: String,
    setup_principal_type: PrincipalType,
}
pub(super) fn scope(p: &Plan) -> String {
    let Target::Azure { subscription, .. } = &p.target else {
        unreachable!()
    };
    format!(
        "/subscriptions/{subscription}/resourceGroups/{}",
        p.azure_bootstrap.as_ref().unwrap().resource_group
    )
}
fn guid(p: &Plan, purpose: &str) -> String {
    let hex = hash(format!("{}:{purpose}", p.deployment_id).as_bytes());
    format!(
        "{}-{}-{}-{}-{}",
        &hex[..8],
        &hex[8..12],
        &hex[12..16],
        &hex[16..20],
        &hex[20..32]
    )
}
pub(super) fn definition_id(p: &Plan) -> String {
    let Target::Azure { subscription, .. } = &p.target else {
        unreachable!()
    };
    format!(
        "/subscriptions/{subscription}/providers/Microsoft.Authorization/roleDefinitions/{}",
        guid(p, "setup-role")
    )
}
pub(super) fn role_url(p: &Plan) -> String {
    format!("https://management.azure.com{}/providers/Microsoft.Authorization/roleDefinitions/{}?api-version=2022-04-01",scope(p),guid(p,"setup-role"))
}
pub(super) fn assignment_url(p: &Plan) -> String {
    format!("https://management.azure.com{}/providers/Microsoft.Authorization/roleAssignments/{}?api-version=2022-04-01",scope(p),guid(p,"setup-assignment"))
}
const ACTIONS: &[&str] = &[
    "Microsoft.Resources/subscriptions/resourceGroups/read",
    "Microsoft.Resources/deployments/read",
    "Microsoft.Resources/deployments/write",
    "Microsoft.Resources/deployments/delete",
    "Microsoft.Resources/deployments/cancel/action",
    "Microsoft.Resources/deployments/validate/action",
    "Microsoft.Resources/deployments/whatIf/action",
    "Microsoft.Resources/deployments/operations/read",
    "Microsoft.Resources/deployments/operationstatuses/read",
    "Microsoft.Authorization/roleDefinitions/read",
    "Microsoft.Authorization/roleAssignments/read",
];
pub(super) fn role(p: &Plan) -> Value {
    json!({"properties":{"roleName":format!("{}-setup",p.prefix()),"type":"CustomRole",
      "description":format!("OpenWorker {} ARM deployment metadata only; no infrastructure or RBAC write authority",p.deployment_id),
      "assignableScopes":[scope(p)],"permissions":[{"actions":ACTIONS,"notActions":[],"dataActions":[],"notDataActions":[]}]}})
}
pub(super) fn assignment(p: &Plan) -> Value {
    let i = p.azure_bootstrap.as_ref().unwrap();
    json!({"properties":{"principalId":i.setup_object_id,"principalType":i.setup_principal_type.azure(),"roleDefinitionId":definition_id(p),"description":format!("OpenWorker {} setup metadata grant",p.deployment_id)}})
}
impl Identity {
    pub(super) fn validate(&self, p: &Plan) -> Result<()> {
        ensure!(
            matches!(p.target, Target::Azure { .. }) && p.schema_version == 2,
            "Azure identity requires Azure schema-2 plan"
        );
        ensure!(
            (3..=63).contains(&self.resource_group.len())
                && self.resource_group.as_bytes()[0].is_ascii_lowercase()
                && self
                    .resource_group
                    .bytes()
                    .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || b"_-".contains(&c)),
            "Use an existing dedicated lowercase resource group (3..63 letters/digits/_/-)"
        );
        ensure!(
            uuid::Uuid::parse_str(&self.setup_object_id)
                .is_ok_and(|u| !u.is_nil() && u.hyphenated().to_string() == self.setup_object_id),
            "Setup object ID must be an exact lowercase nonzero UUID"
        );
        Ok(())
    }
}
pub(super) fn bootstrap(o: &Bootstrap) -> Result<()> {
    let (mut p, _) = inspect(&o.plan)?;
    let at = now()?;
    ensure!(
        p.created_at_unix <= at && at < p.expires_at_unix,
        "Plan expired or future-dated"
    );
    ensure!(
        p.azure_bootstrap.is_none(),
        "Identity already exported; inspect the original bundle"
    );
    p.azure_bootstrap = Some(Identity {
        resource_group: o.resource_group.clone(),
        setup_object_id: o.setup_object_id.clone(),
        setup_principal_type: o.setup_principal_type.clone(),
    });
    p.validate()?;
    let m = write_bundle(&o.out, &p)?;
    crate::output(result(&p, &m))
}
pub(super) fn artifacts(p: &Plan) -> Result<BTreeMap<String, Vec<u8>>> {
    let i = p
        .azure_bootstrap
        .as_ref()
        .context("Azure identity required")?;
    i.validate(p)?;
    let Target::Azure {
        tenant,
        subscription,
        ..
    } = &p.target
    else {
        unreachable!()
    };
    let resource = scope(p);
    let rid = guid(p, "setup-role");
    let aid = guid(p, "setup-assignment");
    let script = format!(
        r#"#!/usr/bin/env bash
set -euo pipefail
# Export only: ows never invokes this script. Run as a reviewed bootstrap admin.
[ "$#" = 2 ] && [ "$1" = '--apply-identities' ] || {{ echo 'Review AZURE-BOOTSTRAP.md; usage: bash azure-bootstrap.sh --apply-identities /private/fresh-journal' >&2; exit 2; }}
root=$(cd -- "$(dirname -- "$0")" && pwd)
ows deploy validate --plan "$root" >/dev/null
[ -z "${{AZURE_CORE_DISABLE_CONNECTION_VERIFICATION:-}}" ] || {{ echo 'TLS verification override refused' >&2; exit 1; }}
export AZURE_CORE_ONLY_SHOW_ERRORS=true AZURE_CORE_COLLECT_TELEMETRY=false AZURE_EXTENSION_USE_DYNAMIC_INSTALL=no AZURE_CORE_LOG_LEVEL=error
a() {{ az "$@" --subscription '{subscription}' --only-show-errors; }}
[ "$(az cloud show --query name -o tsv --only-show-errors)" = AzureCloud ] || {{ echo 'Public Azure cloud required' >&2; exit 1; }}
[ "$(az cloud show --query endpoints.resourceManager -o tsv --only-show-errors)" = https://management.azure.com/ ] || {{ echo 'Unexpected Azure endpoint' >&2; exit 1; }}
[ "$(a account show --query tenantId -o tsv)" = '{tenant}' ] || {{ echo 'Tenant mismatch' >&2; exit 1; }}
[ "$(a account show --query id -o tsv)" = '{subscription}' ] || {{ echo 'Subscription mismatch' >&2; exit 1; }}
[ "$(a account show --query state -o tsv)" = Enabled ] || {{ echo 'Subscription is not enabled' >&2; exit 1; }}
[ "$(a group show --name '{rg}' --query id -o tsv | tr '[:upper:]' '[:lower:]')" = '{scope_lower}' ] || {{ echo 'Existing resource group mismatch' >&2; exit 1; }}
[ "$(a role definition list --name '{rid}' --scope '{resource}' --query 'length(@)' -o tsv)" = 0 ] || {{ echo 'Generated role already exists; reconcile the original journal, never overwrite it' >&2; exit 1; }}
[ "$(a role assignment list --scope '{resource}' --all --query 'length([?name==`"{aid}"`])' -o tsv)" = 0 ] || {{ echo 'Generated assignment already exists; reconcile first' >&2; exit 1; }}
journal_parent=$(cd -- "$(dirname -- "$2")" && pwd)
case "$journal_parent/" in "$root/"*) echo 'Journal must be outside the immutable bundle' >&2; exit 1;; esac
umask 077
mkdir -- "$2"
journal=$(cd -- "$2" && pwd)
case "$journal/" in "$root/"*) echo 'Journal must be outside the immutable bundle' >&2; exit 1;; esac
trap 'echo "Stopped. Inspect exact role/assignment IDs and private journal before any retry; unknown outcome is not retry permission." >&2' ERR
# Fixed, reviewed recipient and scope. No role widening or automatic mutation retry.
ows deploy validate --plan "$root" >/dev/null
a rest --method put --url '{role_url}' --body "@$root/azure-setup-role.json" -o json > "$journal/setup-role.json"
ows deploy validate --plan "$root" >/dev/null
a rest --method put --url '{assignment_url}' --body "@$root/azure-setup-assignment.json" -o json > "$journal/setup-assignment.json"
echo 'Identity-only grant submitted. Run ows deploy azure preflight. No AKS/resources, workload identity, automatic expiry or cloud installer is ready.'
"#,
        rg = i.resource_group,
        scope_lower = resource.to_ascii_lowercase(),
        role_url = role_url(p),
        assignment_url = assignment_url(p)
    );
    let review=format!("# Azure identity-only bootstrap\n\nDeployment: `{}`. Tenant: `{tenant}`. Subscription: `{subscription}`.\nExisting customer-owned resource group: `{resource}`.\nExisting setup principal: `{}` (`{}`). This is an Entra **object ID**, not client ID.\n\nReview the two JSON payloads and explicitly run:\n\n```sh\nbash azure-bootstrap.sh --apply-identities /private/fresh-journal\n```\n\nThe script uses an existing trusted Azure CLI login with roleDefinitions/write\non all assignable scopes and roleAssignments/write at this resource group. It\ndoes not create a resource group, Entra application, principal, client secret or\nmanaged identity. A CLI user cannot impersonate an arbitrary managed identity:\ninteractive users log in normally; a service principal needs customer-provided\nfederation or an identity-enabled compute host. Those login flows are not created\nby this grant. The bootstrap administrator and grant recipient may differ.\n\nThe custom role manages ARM deployment **metadata only** inside the exact RG.\nIt has no resource creation permissions, role-assignment writes, data actions,\nsubscription Owner/Contributor, credential export or identity-assignment authority.\nDeployment metadata delete is not resource cleanup. Existing inherited grants are\nnot removed or proven absent. Preflight checks the exact grant, not all effective\npermissions, deny assignments, conditional access or Azure Policy.\n\nThe caller creates the dedicated resource group separately and retains ownership.\nNo OpenWorker cleanup command may assume it owns/deletes that group. Workload\nOIDC federation, container-scoped Blob data access, AKS/kubelet identities, resolved\ninfrastructure policy, Bicep/ARM graph, cost/quota checks and independent cleanup\nare later milestones. Bootstrap success **never** means apply-ready.\n\nGenerated role/assignment IDs are stable for reconciliation. Existing IDs cause\nrefusal. A lost response is unknown: inspect the original IDs and journal; do not\nblindly rerun or overwrite. The script has no mutation retry loop, although Azure\nCLI/SDK may perform transport retries. Plan expiry gates new steps but does not\nautomatically revoke existing grants. A digest is integrity checking, not signing.\n\nPrerequisites: trusted Bash, Azure CLI, `ows`, existing principal/RG, reviewed\nbootstrap admin authority. No cloud call occurs during export/inspect/validate.\n\nSources: [custom roles](https://learn.microsoft.com/en-us/azure/role-based-access-control/custom-roles),\n[role assignment](https://learn.microsoft.com/en-us/azure/role-based-access-control/role-assignments-cli),\n[AKS workload identity](https://learn.microsoft.com/en-us/azure/aks/workload-identity-overview).\n",p.deployment_id,i.setup_object_id,i.setup_principal_type.azure());
    Ok(BTreeMap::from([
        ("azure-setup-role.json".into(), json_bytes(&role(p))?),
        (
            "azure-setup-assignment.json".into(),
            json_bytes(&assignment(p))?,
        ),
        ("azure-bootstrap.sh".into(), script.into_bytes()),
        ("AZURE-BOOTSTRAP.md".into(), review.into_bytes()),
        (
            "azure-identities.json".into(),
            json_bytes(
                &json!({"schemaVersion":1,"deploymentId":p.deployment_id,"target":p.target,"resourceGroupScope":resource,"setupObjectId":i.setup_object_id,"setupPrincipalType":i.setup_principal_type,"roleDefinitionId":definition_id(p),"roleDefinitionGuid":rid,"roleAssignmentGuid":aid,"roleUrl":role_url(p),"assignmentUrl":assignment_url(p),"identityOnly":true,"applyReady":false,"infrastructurePermissionsReady":false,"automaticExpiryInstalled":false,"workloadIdentityReady":false}),
            )?,
        ),
    ]))
}
