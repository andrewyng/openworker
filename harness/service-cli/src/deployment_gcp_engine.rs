//! Shared CLI/independent-runner protocol. Persist intent before remote effects.
use super::{control::*, *};

pub(super) trait Cloud {
    async fn deployment(&mut self) -> Result<Option<Value>>;
    async fn operation(&mut self, name: &str) -> Result<Value>;
    async fn revision(&mut self, name: &str) -> Result<Value>;
    async fn operations(&mut self) -> Result<Vec<Value>>;
    async fn source(&mut self) -> Result<()>;
    async fn guard(&mut self) -> Result<String>;
    async fn authorize_create(&mut self) -> Result<()>;
    async fn create(&mut self) -> Result<Value>;
    async fn delete(&mut self, request: &str) -> Result<Value>;
    async fn save(&mut self, generation: &str, checkpoint: &Checkpoint) -> Result<String>;
    async fn named_resources_absent(&mut self) -> Result<bool>;
    async fn dependent_assets(&mut self) -> Result<Vec<Asset>>;
    async fn assets_absent(&mut self, assets: &[Asset]) -> Result<bool>;
    async fn scheduler_paused(&mut self) -> Result<bool>;
    async fn pause_scheduler(&mut self) -> Result<()>;
}
#[derive(Clone, Copy)]
pub(super) enum Mode {
    Apply,
    Reconcile,
    Destroy,
    Tick,
}

fn op_valid(c: &Control, v: &Value, verb: &str) -> Result<String> {
    let name = v["name"].as_str().context("Missing operation receipt")?;
    c.operation(name)?;
    ensure!(
        v["metadata"]["target"] == c.deployment() && v["metadata"]["verb"] == verb,
        "Operation target/verb mismatch"
    );
    unix(
        v["metadata"]["createTime"]
            .as_str()
            .context("Missing operation creation time")?,
    )?;
    Ok(name.into())
}
fn intent_operation(c: &Control, j: &Checkpoint, v: &Value, verb: &str) -> Result<String> {
    let name = op_valid(c, v, verb)?;
    let at = if verb == "create" {
        j.create_intent_at
    } else {
        j.delete_intent_at
    }
    .context("Missing original intent")?;
    let created = unix(v["metadata"]["createTime"].as_str().unwrap())?;
    ensure!(
        created >= at.saturating_sub(30) && created <= at.saturating_add(180),
        "Operation receipt outside intent window"
    );
    Ok(name)
}
async fn revision_valid(api: &mut impl Cloud, c: &Control, j: &Checkpoint) -> Result<()> {
    if let Some(rev) = &j.revision {
        let v = api.revision(rev).await?;
        ensure!(
            v["name"] == *rev
                && v["serviceAccount"] == c.executor()
                && v["terraformBlueprint"] == c.request()["terraformBlueprint"],
            "Revision receipt drift"
        );
    }
    Ok(())
}
fn deployment_valid(c: &Control, j: &mut Checkpoint, v: &Value) -> Result<()> {
    let expected = c.request();
    ensure!(
        v["name"] == c.deployment()
            && v["serviceAccount"] == expected["serviceAccount"]
            && v["terraformBlueprint"] == expected["terraformBlueprint"]
            && v["tfVersionConstraint"] == expected["tfVersionConstraint"]
            && v.get("importExistingResources")
                .is_none_or(|x| x.as_bool() == Some(false)),
        "Deployment source/executor drift"
    );
    for key in [
        "openworker.dev/bundle",
        "openworker.dev/request",
        "openworker.dev/expiry",
    ] {
        ensure!(
            v["annotations"][key] == expected["annotations"][key],
            "Deployment intent annotation drift"
        );
    }
    let time = v["createTime"]
        .as_str()
        .context("Missing deployment creation time")?;
    let at = unix(time)?;
    let intent = j
        .create_intent_at
        .context("Missing durable create intent")?;
    ensure!(
        at >= intent.saturating_sub(30) && at <= intent + 180,
        "Deployment creation outside intent window"
    );
    if let Some(old) = &j.create_time {
        ensure!(old == time, "Deployment replaced at original name");
    } else {
        ensure!(
            j.create_operation.is_some(),
            "No acknowledged/discovered create operation"
        );
        j.create_time = Some(time.into());
    }
    if let Some(rev) = v["latestRevision"].as_str().filter(|v| !v.is_empty()) {
        c.revision(rev)?;
        if let Some(old) = &j.revision {
            ensure!(old == rev, "Deployment revision changed");
        } else {
            j.revision = Some(rev.into());
        }
    }
    Ok(())
}
async fn save(api: &mut impl Cloud, g: &mut String, j: &Checkpoint) -> Result<()> {
    *g = api.save(g, j).await?;
    ensure!(decimal(g), "Invalid checkpoint generation receipt");
    Ok(())
}
async fn discover(
    api: &mut impl Cloud,
    c: &Control,
    j: &Checkpoint,
    verb: &str,
) -> Result<Option<String>> {
    let at = if verb == "create" {
        j.create_intent_at
    } else {
        j.delete_intent_at
    }
    .context("Missing durable intent")?;
    let mut candidates = vec![];
    for op in api.operations().await? {
        if op["metadata"]["target"] == c.deployment() && op["metadata"]["verb"] == verb {
            let name = op_valid(c, &op, verb)?;
            let time = unix(op["metadata"]["createTime"].as_str().unwrap())?;
            if time >= at.saturating_sub(30) && time <= at.saturating_add(180) {
                candidates.push(name);
            }
        }
    }
    ensure!(
        candidates.len() <= 1,
        "Ambiguous operation history; explicit review required"
    );
    Ok(candidates.pop())
}

pub(super) async fn drive(
    api: &mut impl Cloud,
    c: &Control,
    j: &mut Checkpoint,
    g: &mut String,
    mode: Mode,
    at: u64,
) -> Result<Value> {
    c.validate()?;
    j.validate(c)?;
    ensure!(
        decimal(g) && at >= c.plan.created_at_unix,
        "Invalid observation clock/generation"
    );
    // Every action verifies the immutable source. No credentials are in this graph.
    api.source().await?;
    if matches!(mode, Mode::Tick | Mode::Apply)
        && !matches!(
            j.phase,
            Phase::PauseIntent | Phase::Complete | Phase::UnusedPauseIntent | Phase::UnusedComplete
        )
    {
        let uid = api.guard().await?;
        ensure!(uuid(&uid), "Invalid guard UID");
        if let Some(old) = &j.guard_uid {
            ensure!(old == &uid, "Independent guard replaced");
        }
        if matches!(mode, Mode::Tick) {
            j.guard_uid = Some(uid);
            j.heartbeat_at = Some(at);
            save(api, g, j).await?;
        } else {
            ensure!(
                j.guard_uid.as_ref() == Some(&uid)
                    && j.heartbeat_at.is_some_and(|t| t <= at && at - t <= 180),
                "Independent cleanup heartbeat missing/stale"
            );
        }
    }
    let expired = at >= c.plan.expires_at_unix;
    let cleanup = matches!(mode, Mode::Destroy) || matches!(mode, Mode::Tick) && expired;
    match j.phase {
        Phase::Prepared => {
            ensure!(
                api.deployment().await?.is_none(),
                "Existing deployment has no ownership receipt"
            );
            if expired && matches!(mode, Mode::Tick) {
                j.phase = Phase::Unused;
                save(api, g, j).await?;
            } else if matches!(mode, Mode::Apply) {
                ensure!(!expired, "Plan expired");
                // Production remains closed until complete cost/IAM/access qualification.
                api.authorize_create().await?;
                j.phase = Phase::CreateIntent;
                j.create_intent_at = Some(at);
                save(api, g, j).await?;
                // A lost reply leaves the committed intent. No implicit replay, even
                // after a 404. Reconciliation obtains original provider receipts first.
                let op = api.create().await?;
                j.create_operation = Some(intent_operation(c, j, &op, "create")?);
                j.phase = Phase::Creating;
                save(api, g, j).await?;
            }
        }
        Phase::CreateIntent | Phase::Creating => {
            if j.create_operation.is_none() {
                j.create_operation = discover(api, c, j, "create").await?;
                ensure!(
                    j.create_operation.is_some(),
                    "Create outcome unknown; check status before any retry"
                );
            }
            let op = api.operation(j.create_operation.as_ref().unwrap()).await?;
            ensure!(
                intent_operation(c, j, &op, "create")? == *j.create_operation.as_ref().unwrap(),
                "Operation replaced"
            );
            let d = api
                .deployment()
                .await?
                .context("Create outcome unresolved; deployment absent")?;
            deployment_valid(c, j, &d)?;
            revision_valid(api, c, j).await?;
            if op["done"] == true {
                if op.get("error").is_some() {
                    j.phase = Phase::CreateFailed;
                } else {
                    ensure!(
                        d["state"] == "ACTIVE" && j.revision.is_some(),
                        "Creation not ACTIVE with revision"
                    );
                    j.phase = Phase::Active;
                }
            } else {
                j.phase = Phase::Creating;
            }
            save(api, g, j).await?;
        }
        Phase::Active | Phase::CreateFailed => {
            let original = api
                .operation(
                    j.create_operation
                        .as_ref()
                        .context("Missing original create receipt")?,
                )
                .await?;
            ensure!(
                intent_operation(c, j, &original, "create")?
                    == *j.create_operation.as_ref().unwrap()
                    && original["done"] == true,
                "Original creation acknowledgement is not complete"
            );
            let d = api
                .deployment()
                .await?
                .context("Owned deployment unexpectedly absent")?;
            deployment_valid(c, j, &d)?;
            revision_valid(api, c, j).await?;
            if cleanup {
                ensure!(
                    !["CREATING", "UPDATING", "DELETING"]
                        .iter()
                        .any(|s| d["state"] == *s),
                    "Provider operation still in progress"
                );
                if j.assets.is_none() {
                    j.assets = Some(api.dependent_assets().await?);
                }
                j.phase = Phase::DeleteIntent;
                j.delete_intent_at = Some(at);
                save(api, g, j).await?;
                let op = api.delete(&j.delete_request_id).await?;
                j.delete_operation = Some(intent_operation(c, j, &op, "delete")?);
                j.phase = Phase::Deleting;
                save(api, g, j).await?;
            }
        }
        Phase::DeleteIntent | Phase::Deleting => {
            if j.delete_operation.is_none() {
                j.delete_operation = discover(api, c, j, "delete").await?;
                ensure!(
                    j.delete_operation.is_some(),
                    "Delete outcome unknown; check status before any retry"
                );
            }
            let op = api.operation(j.delete_operation.as_ref().unwrap()).await?;
            ensure!(
                intent_operation(c, j, &op, "delete")? == *j.delete_operation.as_ref().unwrap(),
                "Delete operation replaced"
            );
            if let Some(d) = api.deployment().await? {
                deployment_valid(c, j, &d)?;
            } else {
                ensure!(
                    op["done"] == true && op.get("error").is_none(),
                    "Absence is not a successful deletion receipt"
                );
            }
            if op["done"] == true {
                j.phase = if op.get("error").is_some() {
                    Phase::DeleteFailed
                } else {
                    Phase::ProviderDeleted
                };
            } else {
                j.phase = Phase::Deleting;
            }
            save(api, g, j).await?;
        }
        Phase::ProviderDeleted => {
            ensure!(
                api.deployment().await?.is_none(),
                "Deletion acknowledged but deployment still visible"
            );
            if api.named_resources_absent().await?
                && api
                    .assets_absent(
                        j.assets
                            .as_ref()
                            .context("Dependent inventory missing; explicit recovery required")?,
                    )
                    .await?
            {
                j.phase = Phase::NamedResourcesAbsent;
                save(api, g, j).await?;
            }
        }
        Phase::DeleteFailed => {
            bail!("Deletion failed; preserve receipts and review before another write")
        }
        Phase::NamedResourcesAbsent | Phase::Unused => {
            let unused = j.phase == Phase::Unused;
            ensure!(
                !api.scheduler_paused().await?,
                "Scheduler already paused without this intent; reconcile explicitly"
            );
            j.phase = if unused {
                Phase::UnusedPauseIntent
            } else {
                Phase::PauseIntent
            };
            save(api, g, j).await?;
            api.pause_scheduler().await?;
            ensure!(
                api.scheduler_paused().await?,
                "Scheduler pause not yet confirmed"
            );
            j.phase = if unused {
                Phase::UnusedComplete
            } else {
                Phase::Complete
            };
            save(api, g, j).await?;
        }
        Phase::PauseIntent | Phase::UnusedPauseIntent => {
            let unused = j.phase == Phase::UnusedPauseIntent;
            ensure!(
                api.scheduler_paused().await?,
                "Pause outcome unknown; inspect scheduler before deciding to retry"
            );
            j.phase = if unused {
                Phase::UnusedComplete
            } else {
                Phase::Complete
            };
            save(api, g, j).await?;
        }
        Phase::Complete | Phase::UnusedComplete => {}
    }
    j.validate(c)?;
    Ok(
        json!({"phase":j.phase,"checkpointGeneration":g,"cloudObservation":"observed","automaticReplay":false,"providerDeletionConfirmed":matches!(j.phase,Phase::ProviderDeleted|Phase::NamedResourcesAbsent|Phase::PauseIntent|Phase::Complete),"namedResourcesAbsent":matches!(j.phase,Phase::NamedResourcesAbsent|Phase::PauseIntent|Phase::Complete),"capturedDependentsAbsent":matches!(j.phase,Phase::NamedResourcesAbsent|Phase::PauseIntent|Phase::Complete),"schedulerPaused":matches!(j.phase,Phase::Complete|Phase::UnusedComplete),"managedDependentAbsenceQualified":false,"allCleanupComplete":false,"controlStateRetained":true}),
    )
}
