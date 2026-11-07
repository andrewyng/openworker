//! Fixed-host observation and create-only uploads for the installation journal.
use super::*;
impl Google {
    pub async fn initial_object(&self, name: &str, bytes: &[u8], mime: &str) -> Result<String> {
        ensure!(bytes.len() <= 1024 * 1024, "Installation object size limit");
        let immutable_control = format!(
            "{}/control-{}.json",
            self.control.plan.deployment_id,
            hash(bytes)
        );
        let is_source = name == self.control.source_object();
        ensure!(
            (is_source && mime == "application/zip")
                || (!is_source
                    && mime == "application/json"
                    && (name == immutable_control || name == self.control.checkpoint_object())),
            "Installation upload scope/content type mismatch"
        );
        if is_source {
            ensure!(
                hash(bytes) == self.control.source_sha256,
                "Source upload digest mismatch"
            );
        }
        let path = format!("/upload/storage/v1/b/{}/o", self.control.bucket());
        let left = self
            .deadline
            .saturating_duration_since(std::time::Instant::now());
        ensure!(!left.is_zero(), "Google operation deadline exhausted");
        // The source ZIP is binary, not a JSON request. Explicit MIME and generation
        // zero are part of the effect; never use an SDK upload retry here.
        let r = self
            .client
            .post(self.url("storage.googleapis.com", &path)?)
            .bearer_auth(&*self.token)
            .header("x-goog-user-project", project(&self.control.plan))
            .header("Content-Type", mime)
            .query(&[
                ("uploadType", "media"),
                ("ifGenerationMatch", "0"),
                ("name", name),
            ])
            .timeout(left.min(Duration::from_secs(30)))
            .body(bytes.to_vec())
            .send()
            .await
            .map_err(|_| anyhow::anyhow!("google_upload_outcome_unknown"))?;
        ensure!(
            r.status().is_success(),
            "google_upload_http_status_{}",
            r.status().as_u16()
        );
        let v: Value = serde_json::from_slice(&body(r, 65536).await?)?;
        let g = v["generation"]
            .as_str()
            .context("Upload generation missing")?;
        ensure!(
            v["bucket"] == self.control.bucket() && v["name"] == name && decimal(g),
            "Upload receipt scope mismatch"
        );
        Ok(g.into())
    }
    async fn enabled_install_apis(&self) -> Result<Value> {
        let n = &self
            .control
            .plan
            .gcp_bootstrap
            .as_ref()
            .unwrap()
            .project_number;
        let v = self
            .json(
                Method::GET,
                "serviceusage.googleapis.com",
                &format!("/v1/projects/{n}/services"),
                &[
                    ("filter", "state:ENABLED".into()),
                    ("pageSize", "200".into()),
                ],
                None,
            )
            .await?
            .context("Enabled API inventory unavailable")?;
        ensure!(
            v.get("nextPageToken")
                .is_none_or(|v| v.as_str() == Some("")),
            "Enabled API inventory truncated"
        );
        let xs = v["services"]
            .as_array()
            .filter(|a| a.len() <= 200)
            .context("Invalid enabled API inventory")?;
        let required = [
            "storage.googleapis.com",
            "artifactregistry.googleapis.com",
            "run.googleapis.com",
            "cloudscheduler.googleapis.com",
            "config.googleapis.com",
            "compute.googleapis.com",
            "container.googleapis.com",
            "sqladmin.googleapis.com",
            "servicenetworking.googleapis.com",
            "cloudbuild.googleapis.com",
        ];
        let missing: Vec<_> = required
            .into_iter()
            .filter(|api| {
                !xs.iter().any(|x| {
                    x["config"]["name"] == *api
                        && x["state"] == "ENABLED"
                        && x["name"] == format!("projects/{n}/services/{api}")
                })
            })
            .collect();
        Ok(json!({"allPresent":missing.is_empty(),"missing":missing,"observedCount":xs.len()}))
    }
    async fn setup_bucket_permissions(&self) -> Result<Value> {
        let required = [
            "storage.buckets.get",
            "storage.objects.get",
            "storage.objects.create",
        ];
        let qs: Vec<_> = required
            .iter()
            .map(|p| ("permissions", p.to_string()))
            .collect();
        let v = self
            .json(
                Method::GET,
                "storage.googleapis.com",
                &format!(
                    "/storage/v1/b/{}/iam/testPermissions",
                    self.control.bucket()
                ),
                &qs,
                None,
            )
            .await?
            .context("Permission observation unavailable")?;
        let xs = v
            .get("permissions")
            .map(|v| {
                v.as_array()
                    .context("Invalid effective permission response")
            })
            .transpose()?
            .cloned()
            .unwrap_or_default();
        let missing: Vec<_> = required
            .into_iter()
            .filter(|p| !xs.iter().any(|x| x == *p))
            .collect();
        Ok(
            json!({"allPresent":missing.is_empty(),"missing":missing,"identity":accounts(&self.control.plan)["setup"],"scope":self.control.bucket(),"grantsApplied":false}),
        )
    }
    async fn pinned_image(&self) -> Result<Value> {
        let c = &self.control;
        let image = c
            .guard_image
            .rsplit('/')
            .next()
            .context("Invalid image pin")?;
        let path = format!(
            "/v1/{}/repositories/{}-control-images/dockerImages/{image}",
            c.parent(),
            c.prefix()
        );
        let v = self
            .json(
                Method::GET,
                "artifactregistry.googleapis.com",
                &path,
                &[],
                None,
            )
            .await?
            .context("Pinned cleanup image missing")?;
        ensure!(
            v["name"] == path.trim_start_matches("/v1/") && v["uri"] == c.guard_image,
            "Cleanup image digest/scope mismatch"
        );
        let size = v["imageSizeBytes"]
            .as_str()
            .and_then(|s| s.parse::<u64>().ok())
            .context("Image size missing")?;
        ensure!(
            size > 0 && size <= 1024 * 1024 * 1024,
            "Cleanup image exceeds installation bound"
        );
        Ok(json!({"image":c.guard_image,"sizeBytes":size,"architectureRuntimeQualified":false}))
    }
    async fn graph_versions(&self) -> Result<Value> {
        let c = &self.control;
        let i = c.plan.gcp_infrastructure.as_ref().unwrap();
        let v = self
            .json(
                Method::GET,
                "container.googleapis.com",
                &format!(
                    "/v1/projects/{}/locations/{}/serverConfig",
                    project(&c.plan),
                    i.zone
                ),
                &[],
                None,
            )
            .await?
            .context("GKE server configuration missing")?;
        let includes = |key: &str, wanted: &str| {
            v[key]
                .as_array()
                .is_some_and(|a| a.iter().any(|x| x == wanted))
        };
        let ok = includes("validMasterVersions", &i.kubernetes_version)
            && includes("validNodeVersions", &i.kubernetes_version)
            && includes("validImageTypes", "UBUNTU_CONTAINERD");
        Ok(
            json!({"available":ok,"zone":i.zone,"pinnedVersion":i.kubernetes_version,"nodeImage":"UBUNTU_CONTAINERD","openshellKernelAndNetworkPolicyQualified":false}),
        )
    }
    async fn graph_quota(&self) -> Result<Value> {
        let c = &self.control;
        let i = c.plan.gcp_infrastructure.as_ref().unwrap();
        let v = self
            .json(
                Method::GET,
                "compute.googleapis.com",
                &format!(
                    "/compute/v1/projects/{}/regions/{}",
                    project(&c.plan),
                    c.region()
                ),
                &[],
                None,
            )
            .await?
            .context("Regional quota missing")?;
        ensure!(v["name"] == c.region(), "Quota region mismatch");
        let xs = v["quotas"].as_array().context("Invalid quota response")?;
        let available = |name: &str| -> Result<f64> {
            let q = xs
                .iter()
                .find(|x| x["metric"] == name)
                .context("Required regional quota missing")?;
            let limit = q["limit"]
                .as_f64()
                .filter(|v| v.is_finite() && *v >= 0.)
                .context("Invalid quota limit")?;
            let used = q["usage"]
                .as_f64()
                .filter(|v| v.is_finite() && *v >= 0. && *v <= limit)
                .context("Invalid quota usage")?;
            Ok(limit - used)
        };
        // The default GKE pool exists transiently alongside the two final nodes.
        let cpu = if i.node_machine_type == "e2-standard-2" {
            6.
        } else {
            12.
        };
        let disk = f64::from(i.node_disk_gb) * 3.;
        let cpus = available("CPUS")?;
        let e2 = available("E2_CPUS")?;
        let ssd = available("SSD_TOTAL_GB")?;
        Ok(
            json!({"sufficient":cpus>=cpu&&e2>=cpu&&ssd>=disk,"requiredCpuIncludingTransientPool":cpu,"requiredBalancedDiskGb":disk,"cpuRemaining":cpus,"e2CpuRemaining":e2,"balancedDiskQuotaRemainingGb":ssd,"capacityReserved":false,"sqlNatAddressAndOtherQuotasQualified":false}),
        )
    }
    pub async fn installation_preflight(&self, at: u64) -> Value {
        fn record(
            rows: &mut Vec<Value>,
            name: &str,
            result: Result<Value>,
            required: bool,
            flag: Option<&str>,
        ) {
            let (ok, detail, code) = match result {
                Ok(v) => (flag.is_none_or(|key| v[key] == true), Some(v), None),
                Err(e) => (false, None, Some(e.to_string())),
            };
            rows.push(json!({"name":name,"status":if ok{"ok"}else{"blocked"},"requiredForArtifactUpload":required,"detail":detail,"code":code}));
        }
        let c = &self.control;
        let mut rows = vec![];
        record(
            &mut rows,
            "plan_fresh",
            Ok(json!({"fresh":c.plan.created_at_unix<=at&&at<c.plan.expires_at_unix})),
            true,
            Some("fresh"),
        );
        let identity=self.json(Method::GET,"cloudresourcemanager.googleapis.com",&format!("/v3/projects/{}",project(&c.plan)),&[],None).await.and_then(|v|{
            let v=v.context("Project missing")?;ensure!(v["projectId"]==project(&c.plan)&&v["name"]==format!("projects/{}",c.plan.gcp_bootstrap.as_ref().unwrap().project_number)&&v["state"]=="ACTIVE","Project identity/state mismatch");Ok(json!({"project":project(&c.plan),"number":c.plan.gcp_bootstrap.as_ref().unwrap().project_number}))
        });
        record(&mut rows, "project_identity", identity, true, None);
        record(
            &mut rows,
            "installation_apis",
            self.enabled_install_apis().await,
            true,
            Some("allPresent"),
        );
        record(
            &mut rows,
            "control_bucket_privacy",
            self.bucket()
                .await
                .map(|_| json!({"bucket":c.bucket(),"projectPrivacyAndVersioningMatched":true})),
            true,
            None,
        );
        record(
            &mut rows,
            "setup_effective_bucket_permissions",
            self.setup_bucket_permissions().await,
            true,
            Some("allPresent"),
        );
        record(
            &mut rows,
            "pinned_cleanup_image",
            self.pinned_image().await,
            true,
            None,
        );
        record(
            &mut rows,
            "pinned_gke_version_and_image",
            self.graph_versions().await,
            false,
            Some("available"),
        );
        record(
            &mut rows,
            "transient_topology_quota",
            self.graph_quota().await,
            false,
            Some("sufficient"),
        );
        let ready = rows
            .iter()
            .filter(|r| r["requiredForArtifactUpload"] == true)
            .all(|r| r["status"] == "ok");
        json!({"schemaVersion":1,"scope":"installation_artifacts_and_partial_workload_observation","deploymentId":c.plan.deployment_id,"bundleDigest":c.bundle_digest,"observedAtUnix":at,"validUntilUnix":(at+180).min(c.plan.expires_at_unix),"artifactUploadReady":ready,"controlInfrastructureReady":false,"applyReady":false,"cloudMutations":0,"checks":rows,"budget":{"authorizedCumulativeUsd":c.plan.budget.authorized_usd,"verifiedActualSpendUsd":null,"verifiedEstimateUsd":null,"hardCap":false},"remaining":["reviewed control foundation and image installation","effective executor/node/cleanup and Cloud Run/Scheduler service-agent authority","SQL/NAT/address quotas and private installer route","fresh conservative prices and cumulative billing evidence","live OpenShell kernel/NetworkPolicy and independent cleanup qualification"],"limits":["only bounded control artifacts may be uploaded by the installation upload command","this observer does not create resources, grant roles, enable APIs or execute Terraform","successful artifact checks do not authorize paid workload creation"]})
    }
}
