//! Explicit identity-stack bootstrap with original request and template receipts.
//! This runs only after a separate digest-bound approval for IAM changes.
use super::*;
#[derive(Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Receipt {
    deployment_id: String,
    bundle_digest: String,
    request_id: String,
    arn: Option<String>,
    complete: bool,
}
pub(super) async fn bootstrap(
    plan: &Path,
    root: &Path,
    profile: &str,
    accept: &str,
    approve: bool,
) -> Result<()> {
    let _lock = super::super::up::operation_lock(root, "bootstrap.lock")?;
    let (p, m) = inspect(plan)?;
    ensure!(
        m.bundle_digest == accept && approve,
        "Explicit approval of the exact identity bundle is required"
    );
    let Target::Aws { account, region } = &p.target else {
        bail!("AWS bundle required")
    };
    let api = Aws {
        p: p.clone(),
        profile: profile.into(),
        root: root.into(),
        preflight: None,
    };
    let who = api.json("sts", "get-caller-identity", json!({})).await?;
    let expected = &p
        .bootstrap
        .as_ref()
        .context("Bootstrap identity missing")?
        .operator_arn;
    let arn = who["Arn"].as_str().context("AWS caller ARN missing")?;
    let role = expected
        .strip_prefix(&format!("arn:aws:iam::{account}:role/"))
        .map(|s| s.rsplit('/').next().unwrap());
    ensure!(
        who["Account"] == *account
            && (arn == expected
                || role.is_some_and(|r| arn
                    .strip_prefix(&format!("arn:aws:sts::{account}:assumed-role/{r}/"))
                    .is_some_and(|s| !s.is_empty() && !s.contains('/')))),
        "Bootstrap requires the exact reviewed operator/account"
    );
    let expected_template: Value = serde_json::from_slice(&read_file(plan, "bootstrap.cfn.json")?)?;
    let file = root.join("bootstrap-receipt.json");
    let mut receipt: Option<Receipt> = if file.exists() {
        Some(serde_json::from_slice(&fs::read(&file)?)?)
    } else {
        None
    };
    if let Some(r) = &receipt {
        ensure!(
            r.deployment_id == p.deployment_id && r.bundle_digest == m.bundle_digest,
            "Bootstrap receipt belongs to another deployment"
        );
    }
    let name = format!("{}-bootstrap", p.prefix());
    let selector = receipt
        .as_ref()
        .and_then(|r| r.arn.as_deref())
        .unwrap_or(&name);
    let found = api
        .call(
            "cloudformation",
            "describe-stacks",
            json!({"StackName":selector}),
            None,
        )
        .await?;
    if let Some(value) = found {
        let row = value["Stacks"]
            .as_array()
            .filter(|a| a.len() == 1)
            .map(|a| &a[0])
            .context("Ambiguous bootstrap stack")?;
        let r = receipt
            .as_mut()
            .context("Existing identity stack has no original local intent; refuse adoption")?;
        let observed = row["StackId"].as_str().context("Missing stack ARN")?;
        ensure!(
            row["StackName"] == name
                && observed.starts_with(&format!(
                    "arn:aws:cloudformation:{region}:{account}:stack/{name}/"
                ))
                && r.arn.as_ref().is_none_or(|a| a == observed),
            "Bootstrap stack replaced or out of scope"
        );
        ensure!(
            row["Tags"].as_array().is_some_and(|a| a
                .iter()
                .any(|t| t["Key"] == "ows:deployment-id" && t["Value"] == p.deployment_id)),
            "Bootstrap ownership tag mismatch"
        );
        let remote = api
            .json(
                "cloudformation",
                "get-template",
                json!({"StackName":observed,"TemplateStage":"Original"}),
            )
            .await?;
        let template = match &remote["TemplateBody"] {
            Value::String(s) => serde_json::from_str(s)?,
            v @ Value::Object(_) => v.clone(),
            _ => bail!("Original bootstrap template missing"),
        };
        ensure!(
            template == expected_template,
            "Bootstrap template differs from review"
        );
        let events = api
            .json(
                "cloudformation",
                "describe-stack-events",
                json!({"StackName":observed}),
            )
            .await?;
        ensure!(
            events["StackEvents"]
                .as_array()
                .is_some_and(|a| a.iter().any(|e| e["StackId"] == observed
                    && e["PhysicalResourceId"] == observed
                    && e["ResourceType"] == "AWS::CloudFormation::Stack"
                    && e["ClientRequestToken"] == r.request_id)),
            "Original bootstrap request token not observed; inspect before continuing"
        );
        r.arn = Some(observed.into());
        r.complete = row["StackStatus"] == "CREATE_COMPLETE";
        super::super::up::write_private(&file, r)?;
        ensure!(
            r.complete || row["StackStatus"] == "CREATE_IN_PROGRESS",
            "Identity stack failed; inspect original stack without recreating it"
        );
        return crate::output(
            json!({"phase":if r.complete{"ready"}else{"creating"},"stackArn":r.arn,"requestId":r.request_id}),
        );
    }
    ensure!(receipt.is_none(),"Original bootstrap request not observed; outcome remains unknown. Do not replay CreateStack.");
    let mut r = Receipt {
        deployment_id: p.deployment_id.clone(),
        bundle_digest: m.bundle_digest,
        request_id: format!("{}-bootstrap", p.deployment_id),
        arn: None,
        complete: false,
    };
    super::super::up::write_private(&file, &r)?;
    let result=api.json("cloudformation","create-stack",json!({"StackName":name,"TemplateBody":serde_json::to_string(&expected_template)?,"Capabilities":["CAPABILITY_NAMED_IAM"],"ClientRequestToken":r.request_id,"Tags":[{"Key":"ows:deployment-id","Value":p.deployment_id},{"Key":"ows:expires-at","Value":p.expires_at_unix.to_string()}]})).await?;
    let arn = result["StackId"]
        .as_str()
        .context("Bootstrap create outcome unknown: stack ARN missing")?;
    ensure!(
        arn.starts_with(&format!(
            "arn:aws:cloudformation:{region}:{account}:stack/{name}/"
        )),
        "Unexpected bootstrap stack ARN"
    );
    r.arn = Some(arn.into());
    super::super::up::write_private(&file, &r)?;
    crate::output(json!({"phase":"creating","stackArn":r.arn,"requestId":r.request_id}))
}
