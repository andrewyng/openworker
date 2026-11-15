//! GCP offline exports, bootstrap observations and local lifecycle journals.
//! Mutation commands require explicit scope acceptance; offline exports never mutate cloud state.
use super::*;
#[path = "deployment_gcp_preflight.rs"]
mod observation;
pub(super) use observation::{preflight, Preflight};
#[path = "deployment_gcp_installation.rs"]
mod installation;
pub(super) use installation::Command as Installation;
pub(super) async fn install(command: &Installation) -> Result<()> {
    installation::run(command).await
}
#[path = "deployment_gcp_api.rs"]
mod api;
#[path = "deployment_gcp_control.rs"]
mod control;
#[path = "deployment_gcp_control_export.rs"]
mod control_export;
#[path = "deployment_gcp_engine.rs"]
mod engine;
#[path = "deployment_gcp_recovery.rs"]
mod recovery;
#[path = "deployment_gcp_infrastructure.rs"]
mod resources;
pub(super) use control_export::Command as Cleanup;
pub(super) async fn cleanup(command: &Cleanup) -> Result<()> {
    control_export::run(command).await
}
pub(super) use recovery::Lifecycle;
pub(super) use resources::{
    artifacts as infrastructure_artifacts, export as infrastructure, Infrastructure, Inputs,
};
pub(super) async fn lifecycle(command: &Lifecycle) -> Result<()> {
    recovery::run_async(command).await
}
#[cfg(test)]
#[path = "deployment_gcp_lifecycle_tests.rs"]
mod lifecycle_tests;

pub(super) const BOOTSTRAP_APIS: &[&str] = &[
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "config.googleapis.com",
    "cloudbuild.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "cloudbilling.googleapis.com",
    "serviceusage.googleapis.com",
];
pub(super) const INFRASTRUCTURE_APIS: &[&str] = &[
    "compute.googleapis.com",
    "container.googleapis.com",
    "sqladmin.googleapis.com",
    "storage.googleapis.com",
    "artifactregistry.googleapis.com",
    "servicenetworking.googleapis.com",
];
#[derive(Args)]
pub struct Bootstrap {
    #[arg(long)]
    plan: PathBuf,
    #[arg(long)]
    out: PathBuf,
    /// Explicit project NUMBER, verified online by preflight and the script.
    #[arg(long)]
    project_number: String,
    /// Exact user:email or serviceAccount:email; no groups, domains or wildcards.
    #[arg(long)]
    operator_member: String,
}
#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct Identity {
    pub project_number: String,
    pub operator_member: String,
}
impl Identity {
    pub(super) fn email(&self) -> &str {
        self.operator_member.split_once(':').unwrap().1
    }
    pub(super) fn validate(&self, p: &Plan) -> Result<()> {
        ensure!(
            matches!(p.target, Target::Gcp { .. }) && p.schema_version == 2,
            "GCP identity requires a GCP schema-2 plan"
        );
        ensure!(
            (6..=20).contains(&self.project_number.len())
                && !self.project_number.starts_with('0')
                && self.project_number.bytes().all(|b| b.is_ascii_digit()),
            "Invalid GCP project number"
        );
        let (kind, email) = self
            .operator_member
            .split_once(':')
            .context("Use user:email or serviceAccount:email")?;
        ensure!(
            ["user", "serviceAccount"].contains(&kind)
                && (3..=254).contains(&email.len())
                && email.split('@').count() == 2
                && email
                    .bytes()
                    .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b"@._+-".contains(&b))
                && email
                    .split('@')
                    .all(|s| !s.is_empty() && !s.starts_with('-')),
            "Invalid exact operator member"
        );
        ensure!(
            kind != "serviceAccount" || email.ends_with(".gserviceaccount.com"),
            "Invalid operator service account"
        );
        ensure!(
            !accounts(p).values().any(|a| a == email),
            "Operator must be separate from generated accounts"
        );
        Ok(())
    }
}
pub(super) fn project(p: &Plan) -> &str {
    match &p.target {
        Target::Gcp { project, .. } => project,
        _ => unreachable!(),
    }
}
pub(super) fn accounts(p: &Plan) -> BTreeMap<String, String> {
    ["setup", "executor", "artifacts", "cleanup", "nodes"]
        .into_iter()
        .map(|role| {
            (
                role.into(),
                format!(
                    "ow-{}-{role}@{}.iam.gserviceaccount.com",
                    &p.deployment_id[4..16],
                    project(p)
                ),
            )
        })
        .collect()
}
pub(super) fn bootstrap(o: &Bootstrap) -> Result<()> {
    let (mut p, _) = inspect(&o.plan)?;
    let at = now()?;
    ensure!(
        p.created_at_unix <= at && at < p.expires_at_unix,
        "Plan expired or future-dated"
    );
    ensure!(matches!(p.target, Target::Gcp { .. }), "GCP plan required");
    ensure!(
        p.gcp_bootstrap.is_none(),
        "Identity already exported; inspect the original bundle"
    );
    p.gcp_bootstrap = Some(Identity {
        project_number: o.project_number.clone(),
        operator_member: o.operator_member.clone(),
    });
    p.validate()?;
    let manifest = write_bundle(&o.out, &p)?;
    crate::output(result(&p, &manifest))
}
pub(super) fn artifacts(p: &Plan) -> Result<BTreeMap<String, Vec<u8>>> {
    let i = p.gcp_bootstrap.as_ref().context("GCP identity required")?;
    i.validate(p)?;
    let a = accounts(p);
    let target = project(p);
    let setup = &a["setup"];
    let executor = &a["executor"];
    let bindings = json!([
        {"resource":format!("projects/{target}"),"role":"roles/config.admin","member":format!("serviceAccount:{setup}")},
        {"resource":format!("projects/{target}"),"role":"roles/serviceusage.serviceUsageConsumer","member":format!("serviceAccount:{setup}")},
        {"resource":format!("projects/{target}"),"role":"roles/config.agent","member":format!("serviceAccount:{executor}")},
        {"resource":setup,"role":"roles/iam.serviceAccountTokenCreator","member":i.operator_member},
        {"resource":executor,"role":"roles/iam.serviceAccountUser","member":format!("serviceAccount:{setup}")},
        {"resource":executor,"role":"roles/iam.serviceAccountUser","member":format!("serviceAccount:service-{}@gcp-sa-config.iam.gserviceaccount.com",i.project_number)}
    ]);
    let mut script = format!(
        r#"#!/usr/bin/env bash
set -euo pipefail
if [ "$#" != 2 ] || [ "$1" != '--apply-identities' ]; then
  echo 'Review GCP-BOOTSTRAP.md, then run: bash gcp-bootstrap.sh --apply-identities /private/fresh-journal' >&2
  exit 2
fi
root=$(cd -- "$(dirname -- "$0")" && pwd)
ows deploy validate --plan "$root" >/dev/null
export CLOUDSDK_CORE_DISABLE_PROMPTS=1 CLOUDSDK_CORE_LOG_HTTP=false
unset CLOUDSDK_AUTH_ACCESS_TOKEN CLOUDSDK_AUTH_ACCESS_TOKEN_FILE CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE
if env | grep -q '^CLOUDSDK_API_ENDPOINT_OVERRIDES_'; then echo 'Endpoint override not allowed' >&2; exit 1; fi
export CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT=''
g() {{ gcloud "$@" --project='{target}' --billing-project='{target}' --account='{operator}' --quiet --verbosity=error; }}
overrides=$(g config list --format='value(api_endpoint_overrides)')
[ -z "$overrides" ] || {{ echo 'Endpoint override not allowed' >&2; exit 1; }}
credential_override=$(g config list --format='value(auth.credential_file_override,auth.access_token_file)')
[ -z "$(printf '%s' "$credential_override" | tr -d '[:space:]')" ] || {{ echo 'Credential override not allowed' >&2; exit 1; }}
universe=$(g config list --format='value(core.universe_domain)')
[ -z "$universe" ] || [ "$universe" = googleapis.com ] || {{ echo 'Unsupported universe' >&2; exit 1; }}
caller=$(g auth list --filter=status:ACTIVE --format='value(account)')
[ "$caller" = '{operator}' ] || {{ echo 'Wrong active bootstrap account' >&2; exit 1; }}
number=$(g projects describe '{target}' --format='value(projectNumber)')
[ "$number" = '{number}' ] || {{ echo 'Project number mismatch' >&2; exit 1; }}
state=$(g projects describe '{target}' --format='value(lifecycleState)')
[ "$state" = ACTIVE ] || {{ echo 'Project is not active' >&2; exit 1; }}
enabled=$(g services list --enabled --limit=1001 --format='value(config.name)')
[ "$(printf '%s\n' "$enabled" | wc -l)" -le 1000 ] || {{ echo 'Incomplete API inventory' >&2; exit 1; }}
"#,
        operator = i.email(),
        number = i.project_number
    );
    for api in BOOTSTRAP_APIS {
        script.push_str(&format!("printf '%s\\n' \"$enabled\" | grep -Fxq '{api}' || {{ echo 'Required API missing: {api}. Enable it separately after review.' >&2; exit 1; }}\n"));
    }
    script.push_str("existing=$(g iam service-accounts list --limit=1001 --format='value(email)')\n[ \"$(printf '%s\\n' \"$existing\" | wc -l)\" -le 1000 ] || { echo 'Incomplete identity inventory' >&2; exit 1; }\n");
    for email in a.values() {
        script.push_str(&format!("if printf '%s\\n' \"$existing\" | grep -Fxq '{email}'; then echo 'A generated account already exists. Reconcile the prior journal; this script never adopts or overwrites it.' >&2; exit 1; fi\n"));
    }
    script.push_str("umask 077\nmkdir -- \"$2\"\njournal=$(cd -- \"$2\" && pwd)\ncase \"$journal/\" in \"$root/\"*) echo 'Journal must be outside the immutable bundle' >&2; exit 1;; esac\ntrap 'echo \"Stopped: inspect the private journal before retrying. No script-level mutation retry was attempted.\" >&2' ERR\n# A lost connection can leave an unknown outcome. Inspect the exact account and\n# journal before retrying; fixed IDs also prevent duplicate service accounts.\nmutate() { ows deploy validate --plan \"$root\" >/dev/null; g \"$@\"; }\n");
    script.push_str("# Explicitly initialize the Google-managed identity; enabling the API alone may not create it.\nmutate beta services identity create --service=config.googleapis.com --format=json > \"$journal/service-agent.json\"\n");
    for (role, email) in &a {
        let id = email.split('@').next().unwrap();
        script.push_str(&format!("mutate iam service-accounts create '{id}' --display-name='OpenWorker {} {role}' --description='Identity-only bootstrap; deployment {}' --format=json > \"$journal/{role}.json\"\n",p.deployment_id,p.deployment_id));
    }
    for (n, b) in bindings.as_array().unwrap().iter().enumerate() {
        let role = b["role"].as_str().unwrap();
        let member = b["member"].as_str().unwrap();
        let resource = b["resource"].as_str().unwrap();
        let command = if resource.starts_with("projects/") {
            format!("projects add-iam-policy-binding '{target}'")
        } else {
            format!("iam service-accounts add-iam-policy-binding '{resource}'")
        };
        script.push_str(&format!("mutate {command} --member='{member}' --role='{role}' --condition=None --format=json > \"$journal/binding-{n}.json\"\n"));
    }
    script.push_str("echo 'Identity-only bootstrap complete. No infrastructure permissions, workload data grants or expiry guard installed. Run ows deploy gcp preflight; do not launch infrastructure yet.'\n");
    let review=format!("# GCP identity-only bootstrap\n\nProject: {target}; verified project number required: {}.\nOperator: {}. Deployment: {}.\n\nReview gcp-identities.json and the script, then explicitly run:\n\n    bash gcp-bootstrap.sh --apply-identities /private/fresh-journal\n\nows export/validate/preflight never invokes this script. Required APIs must already\nbe enabled by the customer; the script explicitly initializes the Google-managed
Infra Manager identity using the official gcloud beta component before creating
customer accounts. It will not enable APIs or grant its caller\nbootstrap authority. Five new same-project accounts separate setup, Terraform\nexecution, artifact access, cleanup and nodes. No service-account keys are created.\n\nThe operator can impersonate only the exact setup account. Setup and the Infra\nManager service agent can act as only the exact executor account. Setup has\nproject-wide Infra Manager administration in this explicitly selected project;\nnames/labels are not an IAM security boundary. Terraform's config.agent platform\nrole is not GKE/Compute/SQL provisioning authority. Additional resource-specific\nexecutor permissions require the resolved G2 graph. No Owner/Editor, project-wide\nToken Creator, workload bucket grant or automatic cleanup authority is granted.\n\nThe plan expiry prevents starting more script steps; it does not automatically\nrevoke previously created identities/bindings. Retain the private journal and\nreconcile partial operations. Existing generated accounts cause a refusal, not\nan adoption or overwrite. Fixed service-account IDs prevent duplicate creation.\nNo script-level automatic mutation retry is performed; gcloud may perform its\nown transport retries. G2/G3 must qualify lifecycle recovery and cleanup before\npaid infrastructure. A digest is integrity checking, not a digital signature.\n\nThe script uses your trusted gcloud installation/configuration. Preflight additionally\nrejects endpoint overrides and implicit impersonation before cloud observation.\n\nSources: https://docs.cloud.google.com/infrastructure-manager/docs/configure-service-account\nand https://docs.cloud.google.com/infrastructure-manager/docs/access-control\n",i.project_number,i.operator_member,p.deployment_id);
    Ok(BTreeMap::from([
        (
            "gcp-identities.json".into(),
            json_bytes(
                &json!({"schemaVersion":1,"deploymentId":p.deployment_id,"project":target,"projectNumber":i.project_number,"accounts":a,"bindings":bindings,"requiredBootstrapApis":BOOTSTRAP_APIS,"requiredInfrastructureApis":INFRASTRUCTURE_APIS,"identityOnly":true,"infrastructurePermissionsReady":false,"automaticExpiryInstalled":false,"applyReady":false}),
            )?,
        ),
        ("gcp-bootstrap.sh".into(), script.into_bytes()),
        ("GCP-BOOTSTRAP.md".into(), review.into_bytes()),
    ]))
}
