//! Optional service features over the original installer identity and journal.
//! Unknown Helm outcomes are observed, never automatically retried.
use super::*;

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(crate) struct ManagementUI {
    #[serde(default)]
    pub enabled: bool,
    #[serde(default = "origin")]
    pub public_origin: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub ca_secret: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub ca_key: Option<String>,
}
fn origin() -> String {
    "https://localhost:8444".into()
}
impl Default for ManagementUI {
    fn default() -> Self {
        Self {
            enabled: false,
            public_origin: origin(),
            ca_secret: None,
            ca_key: None,
        }
    }
}
impl ManagementUI {
    pub(crate) fn validate(&self) -> Result<()> {
        if let Some(s) = &self.ca_secret {
            ensure!(dns_name(s), "Invalid UI CA Secret name");
        }
        if let Some(s) = &self.ca_key {
            ensure!(
                !s.is_empty()
                    && s.len() <= 253
                    && s.bytes()
                        .all(|b| b.is_ascii_alphanumeric() || b"._-".contains(&b)),
                "Invalid UI CA Secret key"
            );
        }
        let u = reqwest::Url::parse(&self.public_origin).context("Invalid management UI origin")?;
        ensure!(u.scheme()=="https" && u.host_str().is_some() && u.username().is_empty() && u.password().is_none() && u.path()=="/" && u.query().is_none() && u.fragment().is_none(), "Management UI publicOrigin must be an HTTPS origin without credentials, path, query or fragment");
        Ok(())
    }
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(crate) struct Observability {
    #[serde(default)]
    pub enabled: bool,
    #[serde(default = "retention")]
    pub retention_days: u16,
    /// Existing Kubernetes Secret containing database-url and ca.crt. No raw credentials.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub database_secret: Option<String>,
}
fn retention() -> u16 {
    30
}
impl Default for Observability {
    fn default() -> Self {
        Self {
            enabled: false,
            retention_days: retention(),
            database_secret: None,
        }
    }
}
impl Observability {
    pub(crate) fn validate(&self) -> Result<()> {
        ensure!(
            (1..=365).contains(&self.retention_days),
            "Trace retention must be 1..365 days"
        );
        if let Some(s) = &self.database_secret {
            ensure!(dns_name(s), "Invalid recording database Secret name");
        }
        Ok(())
    }
}
fn dns_name(s: &str) -> bool {
    !s.is_empty()
        && s.len() <= 63
        && s.starts_with(|c: char| c.is_ascii_lowercase() || c.is_ascii_digit())
        && s.ends_with(|c: char| c.is_ascii_lowercase() || c.is_ascii_digit())
        && s.bytes()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-')
}
#[derive(clap::Args)]
pub(crate) struct Target {
    /// Original installer journal (local-plan.json and journal.json), not cloud lifecycle state.
    #[arg(long)]
    state: PathBuf,
}
#[derive(Subcommand)]
pub(crate) enum UICommand {
    Enable {
        #[command(flatten)]
        target: Target,
        /// Digest-pinned UI image. Omit when already recorded in Helm values.
        #[arg(long)]
        image: Option<String>,
        #[arg(long)]
        public_origin: Option<String>,
        /// Existing Secret containing the CA that signs the service API certificate.
        #[arg(long)]
        ca_secret: Option<String>,
        #[arg(long, requires = "ca_secret")]
        ca_key: Option<String>,
    },
    Disable {
        #[command(flatten)]
        target: Target,
    },
    Status {
        #[command(flatten)]
        target: Target,
    },
}
#[derive(Subcommand)]
pub(crate) enum ObservationCommand {
    Enable {
        #[command(flatten)]
        target: Target,
        #[arg(long, value_parser=clap::value_parser!(u16).range(1..=365))]
        retention_days: Option<u16>,
        /// Existing Secret with database-url and ca.crt; omit to reuse execution PostgreSQL.
        #[arg(long)]
        database_secret: Option<String>,
    },
    Disable {
        #[command(flatten)]
        target: Target,
    },
    Status {
        #[command(flatten)]
        target: Target,
    },
}
#[derive(Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct Update {
    request_id: String,
    before: Value,
    values: Value,
    #[serde(default)]
    receipt: Option<Value>,
}
pub(super) fn latest_receipt(j: &Journal) -> Option<&Value> {
    j.feature_updates.last().and_then(|u| u.receipt.as_ref())
}
pub(super) fn effective_values(p: &Plan, j: &Journal) -> Result<Value> {
    j.feature_updates
        .last()
        .map(|u| Ok(u.values.clone()))
        .unwrap_or_else(|| read_json(&p.config.service.values_file))
}
fn confirmed_receipt(j: &Journal) -> Result<&Value> {
    latest_receipt(j)
        .or_else(|| {
            j.operations
                .get("service-release")
                .and_then(|o| o.receipt.as_ref())
        })
        .context("Original service installation receipt missing")
}
fn same_receipt(a: &Value, b: &Value) -> bool {
    a["uid"] == b["uid"] && a["revision"] == b["revision"] && a["operationId"] == b["operationId"]
}
async fn live_values(client: &Client<'_>, p: &Plan) -> Result<Value> {
    serde_json::from_slice(
        &client
            .helm(args(&[
                "get",
                "values",
                &p.config.service.release,
                "-n",
                &p.config.service.namespace,
                "-o",
                "json",
            ]))
            .await?,
    )
    .context("Invalid release values")
}
pub(super) async fn verify_current(client: &Client<'_>, p: &Plan, j: &Journal) -> Result<Value> {
    ensure!(
        j.feature_updates.last().is_none_or(|u| u.receipt.is_some()),
        "Feature upgrade outcome unknown; rerun status to reconcile, never replay the upgrade"
    );
    let r = client
        .release(&p.config.service.namespace, &p.config.service.release)
        .await?
        .context("Original service release absent")?;
    ensure!(
        same_receipt(&r, confirmed_receipt(j)?) && r["status"] == "deployed",
        "Service Helm release identity/revision changed; refusing feature mutation"
    );
    Ok(r)
}
pub(super) async fn reconcile(
    client: &Client<'_>,
    p: &Plan,
    root: &Path,
    j: &mut Journal,
) -> Result<()> {
    let Some(update) = j
        .feature_updates
        .last()
        .filter(|u| u.receipt.is_none())
        .cloned()
    else {
        return Ok(());
    };
    ensure!(
        client.cluster().await? == p.cluster_uid,
        "Cluster identity changed"
    );
    owned(client, p, j, &p.config.service.namespace)
        .await?
        .context("Owned namespace missing")?;
    let r = client
        .release(&p.config.service.namespace, &p.config.service.release)
        .await?
        .context("Upgrade outcome unknown: original service release absent")?;
    ensure!(r["installerId"]==p.id && r["operationId"]==update.request_id && r["revision"].as_u64()==update.before["revision"].as_u64().and_then(|n|n.checked_add(1)) && r["status"]=="deployed", "Feature upgrade outcome unknown/pending/failed; inspect the original Helm operation. It will not be retried automatically");
    ensure!(
        live_values(client, p).await? == update.values,
        "Upgraded release values differ from the saved intent"
    );
    ready_service(client, p, j).await?;
    j.feature_updates.last_mut().unwrap().receipt = Some(r);
    save(root, "journal.json", j)
}
/// Pure rendering shared by initial cloud deployment and later CLI controls.
pub(crate) fn configure_ui(
    values: &mut Value,
    ui: &ManagementUI,
    image: Option<&str>,
    release: &str,
    namespace: &str,
) -> Result<()> {
    ui.validate()?;
    if values["managementUI"].is_null() {
        values["managementUI"] = json!({});
    }
    values["managementUI"]["enabled"] = json!(ui.enabled);
    if let Some(image) = image {
        values["managementUI"]["image"] = json!(image);
    }
    values["managementUI"]["publicOrigin"] = json!(ui.public_origin);
    values["managementUI"]["serviceUrl"] = json!(format!("https://{release}-api.{namespace}.svc"));
    if ui.enabled {
        let image = values["managementUI"]["image"]
            .as_str()
            .context("Provide --image with a digest-pinned management UI image")?;
        ensure!(
            image
                .rsplit_once("@sha256:")
                .is_some_and(|(name, d)| !name.is_empty()
                    && d.len() == 64
                    && d.bytes()
                        .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))),
            "Management UI image must use a sha256 digest"
        );
        let api_tls = values["secrets"]["apiTLS"]
            .as_str()
            .unwrap_or("ow-service-tls")
            .to_string();
        let ca_secret = ui
            .ca_secret
            .clone()
            .or_else(|| {
                values["managementUI"]["caSecret"]
                    .as_str()
                    .filter(|s| !s.is_empty())
                    .map(str::to_owned)
            })
            .unwrap_or_else(|| api_tls.clone());
        let ca_key = ui
            .ca_key
            .clone()
            .or_else(|| {
                if ui.ca_secret.is_none() {
                    values["managementUI"]["caKey"].as_str().map(str::to_owned)
                } else {
                    None
                }
            })
            .unwrap_or_else(|| {
                if ui.ca_secret.is_some() {
                    "ca.crt".into()
                } else {
                    "tls.crt".into()
                }
            });
        values["managementUI"]["caSecret"] = json!(ca_secret);
        values["managementUI"]["caKey"] = json!(ca_key);
        // Private loopback preview is self-contained. Public origins require the
        // operator's TLS ingress; never create public exposure implicitly.
        let u = reqwest::Url::parse(&ui.public_origin)?;
        values["managementUI"]["tlsSecret"] =
            if matches!(u.host_str(), Some("localhost" | "127.0.0.1")) {
                json!(api_tls)
            } else {
                json!("")
            };
    }
    Ok(())
}
pub(crate) fn configure_observability(values: &mut Value, obs: &Observability) -> Result<()> {
    obs.validate()?;
    if values["secrets"].is_null() {
        values["secrets"] = json!({});
    }
    if obs.enabled {
        if values["config"]["observability"].is_null() {
            values["config"]["observability"] = json!({"schema":"ow_observability"});
        }
        values["config"]["observability"]["retention_days"] = json!(obs.retention_days);
        if let Some(s) = &obs.database_secret {
            values["secrets"]["observability"] = json!(s);
        }
    } else {
        values["config"]
            .as_object_mut()
            .context("Service configuration missing")?
            .remove("observability");
        values["secrets"]["observability"] = json!("");
    }
    Ok(())
}
async fn change(
    root: &Path,
    mutation: impl FnOnce(&mut Value, &Plan) -> Result<()>,
    status_only: bool,
) -> Result<()> {
    let (p, mut j, _lock) = load(root)?;
    ensure!(
        j.phase == "installed",
        "Feature controls require a completed service installation"
    );
    ensure!(
        inputs(&p.config)? == p.hashes,
        "Original installer inputs changed"
    );
    let client = Client { c: &p.config };
    ensure!(
        client.cluster().await? == p.cluster_uid,
        "Cluster identity changed"
    );
    owned(&client, &p, &j, &p.config.service.namespace)
        .await?
        .context("Owned namespace missing")?;
    reconcile(&client, &p, root, &mut j).await?;
    let before = verify_current(&client, &p, &j).await?;
    let mut values = effective_values(&p, &j)?;
    // First update reads the live explicit values to preserve defaults/previous
    // operator settings, but refuses any unreviewed drift.
    ensure!(
        live_values(&client, &p).await? == values,
        "Service values changed outside the installer"
    );
    if !status_only {
        mutation(&mut values, &p)?;
        if values != effective_values(&p, &j)? {
            // Never rerun first-admin bootstrap on a feature upgrade.
            values["bootstrapEnabled"] = json!(false);
            let id = uuid::Uuid::new_v4().to_string();
            let file = format!("feature-values-{id}.json");
            save(root, &file, &values)?;
            // Render validation happens before recording/sending any mutation.
            client
                .helm(args(&[
                    "template",
                    &p.config.service.release,
                    p.config
                        .service
                        .chart
                        .to_str()
                        .context("Invalid chart path")?,
                    "-n",
                    &p.config.service.namespace,
                    "-f",
                    root.join(&file)
                        .to_str()
                        .context("Invalid feature values path")?,
                ]))
                .await?;
            j.feature_updates.push(Update {
                request_id: id.clone(),
                before,
                values: values.clone(),
                receipt: None,
            });
            save(root, "journal.json", &j)?;
            let result = client
                .helm(args(&[
                    "upgrade",
                    &p.config.service.release,
                    p.config.service.chart.to_str().unwrap(),
                    "-n",
                    &p.config.service.namespace,
                    "-f",
                    root.join(&file).to_str().unwrap(),
                    "--labels",
                    &format!("{OWNER}={},openworker.io/operation-id={id}", p.id),
                    "--description",
                    &format!("ows:{}:{id}", p.id),
                    "--wait",
                    "--timeout",
                    "300s",
                ]))
                .await;
            if result.is_err() {
                // A response can be lost after success. Only observation of this
                // exact revision and intent may recover it; never replay Helm.
                reconcile(&client, &p, root, &mut j).await.context(
                    "Helm response failed; saved feature intent retained for status reconciliation",
                )?;
            } else {
                reconcile(&client, &p, root, &mut j).await?;
            }
        }
    }
    let values = effective_values(&p, &j)?;
    let ui = &values["managementUI"];
    let obs = &values["config"]["observability"];
    let preview = if ui["enabled"] == true
        && ui["tlsSecret"].as_str().is_some_and(|s| !s.is_empty())
    {
        let origin = ui["publicOrigin"]
            .as_str()
            .unwrap_or("https://localhost:8444");
        let u = reqwest::Url::parse(origin)?;
        json!({"url":origin,"command":"kubectl","arguments":["--kubeconfig",p.config.kubeconfig,"--context",p.config.context,"-n",p.config.service.namespace,"port-forward",format!("service/{}-ui",p.config.service.release),format!("{}:443",u.port_or_known_default().unwrap()),"--address=127.0.0.1"],"caFile":"Use the API certificate from the deployment credentials; do not disable certificate verification","login":"existing service token; UI does not hold a bootstrap credential"})
    } else {
        Value::Null
    };
    crate::output(
        json!({"deploymentId":p.id,"managementUI":{"enabled":ui["enabled"]==true,"publicOrigin":ui["publicOrigin"],"privatePreview":preview},"observability":{"enabled":!obs.is_null(),"configuration":obs,"databaseSecret":values["secrets"]["observability"],"storedData":"retained; no deletion performed","exporters":"configured independently"},"savedConfiguration":root.join("journal.json"),"helmRevision":confirmed_receipt(&j)?["revision"]}),
    )
}
pub(crate) async fn ui(command: &UICommand) -> Result<()> {
    match command {
        UICommand::Status { target } => change(&target.state, |_, _| Ok(()), true).await,
        UICommand::Disable { target } => {
            change(
                &target.state,
                |v, _| {
                    if v["managementUI"].is_null() {
                        v["managementUI"] = json!({});
                    }
                    v["managementUI"]["enabled"] = json!(false);
                    Ok(())
                },
                false,
            )
            .await
        }
        UICommand::Enable {
            target,
            image,
            public_origin,
            ca_secret,
            ca_key,
        } => {
            change(
                &target.state,
                |v, p| {
                    let ui = ManagementUI {
                        enabled: true,
                        public_origin: public_origin
                            .clone()
                            .or_else(|| {
                                v["managementUI"]["publicOrigin"]
                                    .as_str()
                                    .map(str::to_owned)
                            })
                            .unwrap_or_else(origin),
                        ca_secret: ca_secret.clone(),
                        ca_key: ca_key.clone(),
                    };
                    configure_ui(
                        v,
                        &ui,
                        image.as_deref(),
                        &p.config.service.release,
                        &p.config.service.namespace,
                    )
                },
                false,
            )
            .await
        }
    }
}
pub(crate) async fn observability(command: &ObservationCommand) -> Result<()> {
    match command {
        ObservationCommand::Status { target } => change(&target.state, |_, _| Ok(()), true).await,
        ObservationCommand::Disable { target } => {
            change(
                &target.state,
                |v, _| configure_observability(v, &Observability::default()),
                false,
            )
            .await
        }
        ObservationCommand::Enable {
            target,
            retention_days,
            database_secret,
        } => {
            change(
                &target.state,
                |v, _| {
                    configure_observability(
                        v,
                        &Observability {
                            enabled: true,
                            retention_days: retention_days.unwrap_or_else(|| {
                                v["config"]["observability"]["retention_days"]
                                    .as_u64()
                                    .unwrap_or(30) as u16
                            }),
                            database_secret: database_secret.clone(),
                        },
                    )
                },
                false,
            )
            .await
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn rendering_is_opt_in_independent_and_preserves_other_settings() {
        let original = json!({"image":"service","config":{"schema":"execution","telemetry":{"enabled":true}},"secrets":{"apiTLS":"api-cert"}});
        let mut values = original.clone();
        let ui = ManagementUI {
            enabled: true,
            ..Default::default()
        };
        configure_ui(
            &mut values,
            &ui,
            Some(&format!("ui@sha256:{}", "b".repeat(64))),
            "ows",
            "example",
        )
        .unwrap();
        assert!(values["config"].get("observability").is_none());
        assert_eq!(values["managementUI"]["tlsSecret"], "api-cert");
        assert_eq!(
            values["managementUI"]["serviceUrl"],
            "https://ows-api.example.svc"
        );
        configure_observability(
            &mut values,
            &Observability {
                enabled: true,
                retention_days: 14,
                database_secret: Some("recording-db".into()),
            },
        )
        .unwrap();
        assert_eq!(values["secrets"]["observability"], "recording-db");
        configure_observability(&mut values, &Observability::default()).unwrap();
        assert!(values["config"].get("observability").is_none());
        assert_eq!(
            values["config"]["telemetry"],
            original["config"]["telemetry"]
        );
        assert_eq!(values["image"], original["image"]);
        assert_eq!(values["managementUI"]["enabled"], true);
    }
    #[test]
    fn invalid_origins_retention_and_inline_database_values_fail_before_mutation() {
        for origin in [
            "http://localhost:8444",
            "https://user:secret@example.com",
            "https://example.com/path",
            "https://example.com/?token=secret",
        ] {
            assert!(ManagementUI {
                enabled: true,
                public_origin: origin.into(),
                ..Default::default()
            }
            .validate()
            .is_err());
        }
        for retention_days in [0, 366] {
            assert!(Observability {
                enabled: true,
                retention_days,
                database_secret: None
            }
            .validate()
            .is_err());
        }
        assert!(Observability {
            enabled: true,
            retention_days: 14,
            database_secret: Some("postgres://secret".into())
        }
        .validate()
        .is_err());
    }
}
