//! Reconcile a guard-initiated deletion from durable AWS evidence, never a new delete.
//! A vanished cluster cannot provide a Kubernetes teardown receipt. Require the
//! original guard/control record and stack-level expiry token instead of guessing.
use super::*;
fn control_version(
    body: &Value,
    version: u64,
    id: &str,
    expiry: u64,
    arn: &str,
    at: u64,
) -> Result<()> {
    ensure!(at >= expiry, "Expiry has not elapsed");
    ensure!(
        version > 0
            && body["version"] == version
            && body["id"] == id
            && body["expiry"] == expiry
            && body["stackArn"] == arn,
        "Guard control record identity/version mismatch"
    );
    let intent = &body["deleteIntent"];
    ensure!(
        intent["token"] == format!("{id}-expiry")
            && intent["at"]
                .as_u64()
                .is_some_and(|t| t >= expiry && t <= at),
        "Original expiry delete intent not confirmed"
    );
    Ok(())
}
pub(super) async fn reconcile(root: &Path, j: &mut Journal, p: &Plan, api: &mut Aws) -> Result<()> {
    let at = now()?;
    ensure!(at >= p.expires_at_unix, "Expiry has not elapsed");
    let original = j
        .operations
        .get("create:infra")
        .context("No original infrastructure operation")?;
    let arn = original
        .arn
        .as_ref()
        .context("Original infrastructure ARN was not bound")?
        .clone();
    let guard = j
        .operations
        .get("create:guard")
        .context("No original guard operation")?;
    let guard_stack = api
        .stack(guard.arn.as_deref().context("Original guard ARN missing")?)
        .await?
        .context("Retain the original guard until expiry reconciliation")?;
    state::verify(p, j, "guard", guard, &guard_stack)?;
    ensure!(
        guard_stack.status == "CREATE_COMPLETE",
        "Original guard must remain active"
    );
    api.guard_health().await?;
    let item = api.json("dynamodb", "get-item", json!({"TableName":format!("{}-control",p.prefix()),"Key":{"deploymentId":{"S":p.deployment_id}},"ConsistentRead":true})).await?;
    ensure!(
        item["Item"]["deploymentId"]["S"] == p.deployment_id,
        "Guard control key mismatch"
    );
    let version: u64 = item["Item"]["version"]["N"]
        .as_str()
        .context("Guard control version missing")?
        .parse()
        .context("Invalid guard control version")?;
    let body: Value = serde_json::from_str(
        item["Item"]["body"]["S"]
            .as_str()
            .context("Guard control record missing")?,
    )
    .context("Invalid guard control record")?;
    control_version(
        &body,
        version,
        &p.deployment_id,
        p.expires_at_unix,
        &arn,
        at,
    )?;
    let stack = api
        .stack(&arn)
        .await?
        .context("Original deleted stack receipt unavailable; inspect before reconciliation")?;
    state::verify(p, j, "infra", original, &stack)?;
    let token = format!("{}-expiry", p.deployment_id);
    ensure!(
        stack.status == "DELETE_COMPLETE" && stack.tokens.contains(&token),
        "Original stack expiry deletion has not completed"
    );
    if let Some(delete) = j.operations.get("delete:infra") {
        ensure!(
            delete.token == token
                && delete.arn.as_ref() == Some(&arn)
                && delete.status == "observed-expiry-delete",
            "A different delete intent cannot be relabeled as expiry cleanup"
        );
    }
    let Target::Aws { account, .. } = &p.target else {
        unreachable!()
    };
    ensure!(
        api.call(
            "s3api",
            "get-bucket-tagging",
            json!({"Bucket":format!("{}-{account}",p.prefix()),"ExpectedBucketOwner":account}),
            None
        )
        .await?
        .is_none(),
        "Owned bucket still exists; expiry cleanup is incomplete"
    );
    let volumes = api.json("ec2","describe-volumes",json!({"Filters":[{"Name":"tag:ows:deployment-id","Values":[p.deployment_id]}],"MaxResults":100})).await?;
    ensure!(
        volumes.get("NextToken").is_none_or(Value::is_null)
            && volumes["Volumes"].as_array().is_some_and(Vec::is_empty),
        "Tagged volumes remain or inventory is incomplete"
    );
    // Record an observed external request with both AWS receipts. Never fabricate
    // a local mutation intent, invoke DeleteStack, or depend on a vanished API pod.
    j.operations.insert(
        "delete:infra".into(),
        state::Operation {
            token: token.clone(),
            arn: Some(arn.clone()),
            status: "observed-expiry-delete".into(),
        },
    );
    j.resources.insert("expiryTeardown".into(),json!({"stackArn":arn,"deleteToken":token,"controlVersion":version,"expiresAtUnix":p.expires_at_unix,"observedAtUnix":at,"bucketAbsent":true,"taggedVolumesAbsent":true}));
    j.phase = "infrastructure_deleted".into();
    state::save(root, j)?;
    crate::output(
        json!({"deploymentId":p.deployment_id,"phase":j.phase,"expiryDeletionVerified":true,"taggedVolumesAbsent":true,"bucketAbsent":true,"guardRetained":true,"bootstrapRetained":true,"remoteMutation":false}),
    )
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn expiry_receipt_binds_original_identity_clock_and_actual_guard_intent() {
        let id = "ows-0123456789abcdef0123456789abcdef";
        let arn = "arn:aws:cloudformation:us-west-2:123456789012:stack/original/uid";
        let body = json!({"id":id,"expiry":100,"version":3,"stackArn":arn,"deleteIntent":{"token":format!("{id}-expiry"),"at":101}});
        assert!(control_version(&body, 3, id, 100, arn, 102).is_ok());
        for field in ["id", "expiry", "version", "stackArn", "deleteIntent"] {
            let mut changed = body.clone();
            changed[field] = Value::Null;
            assert!(control_version(&changed, 3, id, 100, arn, 102).is_err());
        }
        for (version, expiry, at) in [
            (0, 100, 102),
            (2, 100, 102),
            (3, 99, 102),
            (3, 100, 99),
            (3, 100, 100),
        ] {
            assert!(control_version(&body, version, id, expiry, arn, at).is_err());
        }
        for intent in [
            json!({"token":"manual-delete","at":101}),
            json!({"token":format!("{id}-expiry"),"at":99}),
            json!({"token":format!("{id}-expiry"),"at":103}),
        ] {
            let mut changed = body.clone();
            changed["deleteIntent"] = intent;
            assert!(control_version(&changed, 3, id, 100, arn, 102).is_err());
        }
    }
}
