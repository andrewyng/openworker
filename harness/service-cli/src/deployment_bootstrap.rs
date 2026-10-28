//! Export-only AWS identity bootstrap. The ows process never runs this script.
//! A supplied CloudFormation execution role is an explicit trust boundary: stack
//! naming alone cannot constrain what its policies allow CloudFormation to create.
use super::*;

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct AwsIdentity {
    pub operator_arn: String,
    pub execution_role_arn: String,
}
fn principal<'a>(arn: &'a str, account: &str, role_only: bool) -> Result<(&'a str, &'a str)> {
    let prefix = format!("arn:aws:iam::{account}:");
    let tail = arn
        .strip_prefix(&prefix)
        .context("Identity must be a same-account IAM user/role ARN")?;
    let (kind, path) = tail.split_once('/').context("IAM identity path required")?;
    ensure!(
        kind == "role" || (!role_only && kind == "user"),
        "Use an IAM user or role, not root, account, wildcard or STS session ARN"
    );
    ensure!(
        !path.is_empty()
            && path.len() <= 512
            && path.split('/').all(|s| !s.is_empty())
            && path
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b"/+=,.@_-".contains(&b)),
        "Invalid IAM identity path"
    );
    let name = path.rsplit('/').next().unwrap();
    ensure!(name.len() <= 64, "IAM identity name too long");
    Ok((kind, name))
}
impl AwsIdentity {
    pub(super) fn validate(&self, p: &Plan) -> Result<()> {
        let Target::Aws { account, .. } = &p.target else {
            bail!("AWS bootstrap requires AWS target")
        };
        principal(&self.operator_arn, account, false)?;
        principal(&self.execution_role_arn, account, true)?;
        ensure!(
            self.operator_arn != self.execution_role_arn,
            "Operator and CloudFormation executor must be separate identities"
        );
        for role in [&self.operator_arn, &self.execution_role_arn] {
            ensure!(
                !role.contains(&format!("role/{}-", p.prefix())),
                "Existing identities cannot be generated bootstrap roles"
            );
        }
        Ok(())
    }
}
fn policy(statements: Value) -> Value {
    json!({"Version":"2012-10-17","Statement":statements})
}

pub(super) fn render(p: &Plan) -> Result<BTreeMap<String, Vec<u8>>> {
    let identity = p.bootstrap.as_ref().context("Bootstrap identity missing")?;
    identity.validate(p)?;
    let Target::Aws { account, region } = &p.target else {
        bail!("AWS target required")
    };
    let prefix = p.prefix();
    let stack_arns: Vec<_> = ["infra", "guard"]
        .iter()
        .map(|suffix| {
            format!("arn:aws:cloudformation:{region}:{account}:stack/{prefix}-{suffix}/*")
        })
        .collect();
    let setup_policy = policy(json!([
        {"Sid":"OwnedStacksBeforeExpiry","Effect":"Allow","Action":["cloudformation:CreateStack","cloudformation:UpdateStack"],"Resource":stack_arns,
          "Condition":{"StringEquals":{"cloudformation:RoleArn":identity.execution_role_arn},"NumericLessThan":{"aws:EpochTime":p.expires_at_unix}}},
        {"Sid":"OwnedStackCleanup","Effect":"Allow","Action":"cloudformation:DeleteStack","Resource":stack_arns,
          "Condition":{"StringEquals":{"cloudformation:RoleArn":identity.execution_role_arn}}},
        {"Sid":"OwnedStackObservation","Effect":"Allow","Action":["cloudformation:DescribeStacks","cloudformation:DescribeStackEvents","cloudformation:DescribeStackResources","cloudformation:ListStackResources","cloudformation:GetTemplate"],"Resource":stack_arns},
        {"Sid":"PassExactExecutorOnly","Effect":"Allow","Action":"iam:PassRole","Resource":identity.execution_role_arn,"Condition":{"StringEquals":{"iam:PassedToService":"cloudformation.amazonaws.com"}}},
        {"Sid":"ReadOwnCluster","Effect":"Allow","Action":"eks:DescribeCluster","Resource":format!("arn:aws:eks:{region}:{account}:cluster/{prefix}")}
    ]));
    let bucket = format!("{prefix}-{account}");
    let object_prefix = format!("{}/", p.deployment_id);
    let workload_policy = policy(json!([
        {"Sid":"ArtifactObjects","Effect":"Allow","Action":["s3:GetObject","s3:PutObject","s3:DeleteObject","s3:AbortMultipartUpload","s3:ListMultipartUploadParts"],"Resource":format!("arn:aws:s3:::{bucket}/{object_prefix}*")},
        {"Sid":"ListArtifactPrefix","Effect":"Allow","Action":"s3:ListBucket","Resource":format!("arn:aws:s3:::{bucket}"),"Condition":{"StringLike":{"s3:prefix":[object_prefix,format!("{object_prefix}*")]}}},
        {"Sid":"BucketRegion","Effect":"Allow","Action":"s3:GetBucketLocation","Resource":format!("arn:aws:s3:::{bucket}")}
    ]));
    let setup_trust = policy(
        json!([{"Effect":"Allow","Principal":{"AWS":identity.operator_arn},"Action":"sts:AssumeRole"}]),
    );
    let workload_trust = policy(
        json!([{"Effect":"Allow","Principal":{"Service":"pods.eks.amazonaws.com"},"Action":["sts:AssumeRole","sts:TagSession"],"Condition":{"StringEquals":{
            "aws:RequestTag/eks-cluster-arn":format!("arn:aws:eks:{region}:{account}:cluster/{prefix}"),
            "aws:RequestTag/kubernetes-namespace":"ow-service",
            "aws:RequestTag/kubernetes-service-account":"ows-artifacts"
        }}}]),
    );
    let tags = json!([{"Key":"ows:deployment-id","Value":p.deployment_id},{"Key":"ows:expires-at","Value":p.expires_at_unix.to_string()}]);
    let role = |suffix: &str, trust: &Value, permissions: &Value| {
        json!({"Type":"AWS::IAM::Role","Properties":{
            "RoleName":format!("{prefix}-{suffix}"),"MaxSessionDuration":3600,"AssumeRolePolicyDocument":trust,"Tags":tags,
            "Policies":[{"PolicyName":"deployment-scope","PolicyDocument":permissions}]
        }})
    };
    let template = json!({"AWSTemplateFormatVersion":"2010-09-09","Description":"OpenWorker identity bootstrap only; existing execution role required; no cluster or billable infrastructure",
        "Resources":{"SetupRole":role("setup",&setup_trust,&setup_policy),"ArtifactRole":role("artifacts",&workload_trust,&workload_policy)},
        "Outputs":{"SetupRoleArn":{"Value":{"Fn::GetAtt":["SetupRole","Arn"]}},"ArtifactRoleArn":{"Value":{"Fn::GetAtt":["ArtifactRole","Arn"]}}}});
    let (kind, operator_name) = principal(&identity.operator_arn, account, false)?;
    let caller_check = if kind == "role" {
        format!("case \"$caller\" in 'arn:aws:sts::{account}:assumed-role/{operator_name}/'?*) ;; *) echo 'Wrong bootstrap caller role' >&2; exit 1;; esac")
    } else {
        format!(
            "[ \"$caller\" = '{}' ] || {{ echo 'Wrong bootstrap caller' >&2; exit 1; }}",
            identity.operator_arn
        )
    };
    let (_, execution_name) = principal(&identity.execution_role_arn, account, true)?;
    let script = format!(
        r#"#!/usr/bin/env bash
set -euo pipefail
# Explicit customer-run identity mutation. Never invoked by ows plan/validate.
[ "${{1:-}}" = '--apply-identities' ] && [ "$#" = 1 ] || {{ echo 'Review BOOTSTRAP.md, then run: bash bootstrap.sh --apply-identities' >&2; exit 2; }}
cd -- "$(dirname -- "${{BASH_SOURCE[0]}}")"
ows deploy validate --plan . >/dev/null
export AWS_PAGER='' AWS_CLI_AUTO_PROMPT=off AWS_MAX_ATTEMPTS=1
account=$(aws sts get-caller-identity --query Account --output text --region '{region}')
[ "$account" = '{account}' ] || {{ echo 'Wrong AWS account' >&2; exit 1; }}
caller=$(aws sts get-caller-identity --query Arn --output text --region '{region}')
{caller_check}
executor=$(aws iam get-role --role-name '{execution_name}' --query Role.Arn --output text --region '{region}')
[ "$executor" = '{execution_role}' ] || {{ echo 'Execution role ARN mismatch' >&2; exit 1; }}
# A lost response has unknown outcome. Never automatically retry a mutation.
# Inspect this exact stack and client request token before deciding what to do.
if ! aws cloudformation create-stack --region '{region}' \
    --stack-name '{prefix}-bootstrap' --template-body file://bootstrap.cfn.json \
    --capabilities CAPABILITY_NAMED_IAM --client-request-token '{deployment_id}-bootstrap' \
    --tags Key=ows:deployment-id,Value='{deployment_id}' Key=ows:expires-at,Value='{expiry}' >/dev/null; then
    echo 'Bootstrap did not confirm success. Inspect stack {prefix}-bootstrap before any retry. Nothing was overwritten or automatically retried.' >&2
    exit 1
fi
aws cloudformation wait stack-create-complete --region '{region}' --stack-name '{prefix}-bootstrap'
echo 'Identity stack created. Cluster deployment is not implemented or authorized by this command.'
"#,
        execution_role = identity.execution_role_arn,
        deployment_id = p.deployment_id,
        expiry = p.expires_at_unix
    );
    let review = format!(
        r#"# AWS identity bootstrap review

Scope: `{}` in account `{account}`, region `{region}`.

This bundle can create **two IAM roles only** when the customer explicitly runs
`bash bootstrap.sh --apply-identities`. Generation and validation are offline.
The script has not been qualified against live AWS. No cloud execution is part of
local testing. `ows deploy apply` remains disabled.

## Required customer authority

The existing login must match `{operator}` (IAM user or assumed role) in this account.
It needs STS identity lookup, IAM GetRole on `{executor}`, CloudFormation CreateStack /
DescribeStacks on `{prefix}-bootstrap`, and IAM CreateRole, GetRole, PutRolePolicy,
GetRolePolicy, ListRolePolicies, TagRole, UntagRole, DeleteRolePolicy and DeleteRole
on exactly `{prefix}-setup` and `{prefix}-artifacts`. Rollback requires cleanup
permissions. Organization SCPs and permission boundaries may impose extra constraints.
The script does not grant that bootstrap authority and does not assume an administrator.

## Execution role is a prerequisite, not generated here

`{executor}` must already exist, trust CloudFormation, and have customer-reviewed
permissions for the future infrastructure/cleanup templates. Only its ARN/existence
is checked by this script; its policies are NOT certified as safe or sufficient.
CloudFormation can use everything this role permits. Restricting stack names does
not constrain resources that an overprivileged execution role can create. Do not
supply an administrator role. Generation of the complete execution policy remains
part of the infrastructure milestone.

## Exported identities

- Setup: only `{prefix}-infra` and `{prefix}-guard` stack operations, exact executor
  PassRole to CloudFormation, and DescribeCluster for `{prefix}`. No IAM edits,
  ECR upload, secret reads, Kubernetes access grant or authority over bootstrap roles.
- New stack creation/update expires at Unix {}. Observation and cleanup remain
  possible afterward; this is not a cloud cleanup scheduler or spending cap.
- Artifacts: bucket `{bucket}`, object prefix `{object_prefix}`. Trust requires the
  exact EKS cluster ARN, namespace `ow-service`, service account `ows-artifacts`.
  Session tags must remain enabled. No model keys, setup authority or other buckets.
  The service account and Pod Identity association are NOT created here; later Helm
  wiring must assign it only to components that access artifacts, never the UI or
  untrusted agents. No bucket is created by this bootstrap.

The `*` suffixes cover generated stack IDs and objects within one deployment prefix;
there is no account-wide allow resource or wildcard action. Two exact IAM roles are
created via named-IAM capability. Existing roles/stacks are not adopted or updated.
Credentials are neither emitted nor persisted. Template, policy and script contents
are included in bundle integrity checks; hashes are not signatures.

## Interrupted or repeated execution

AWS CLI mutation retries are disabled. If the response is lost, inspect the exact
`{prefix}-bootstrap` stack and request token before retrying. Re-running will not
update an existing stack. A failed wait does not imply rollback or safe deletion.
After infrastructure teardown, an authorized customer administrator deletes this
bootstrap stack; the setup role cannot delete its own identity stack.
"#,
        p.deployment_id,
        p.expires_at_unix,
        operator = identity.operator_arn,
        executor = identity.execution_role_arn
    );
    Ok(BTreeMap::from([
        ("bootstrap.cfn.json".into(), json_bytes(&template)?),
        ("setup-policy.json".into(), json_bytes(&setup_policy)?),
        ("workload-policy.json".into(), json_bytes(&workload_policy)?),
        ("bootstrap.sh".into(), script.into_bytes()),
        ("BOOTSTRAP.md".into(), review.into_bytes()),
    ]))
}
