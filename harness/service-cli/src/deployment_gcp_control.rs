//! Versioned durable control data, kept outside the workload Terraform graph.
use super::super::artifacts as plan_artifacts;
use super::*;

#[derive(Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct Control {
    pub schema_version: u16,
    pub plan: Plan,
    pub bundle_digest: String,
    pub source_generation: String,
    pub source_sha256: String,
    pub request_id: String,
    pub guard_image: String,
}
pub(super) fn decimal(s: &str) -> bool {
    !s.is_empty() && s.len() <= 24 && !s.starts_with('0') && s.bytes().all(|b| b.is_ascii_digit())
}
pub(super) fn uuid(s: &str) -> bool {
    uuid::Uuid::parse_str(s).is_ok_and(|id| !id.is_nil() && id.to_string() == s)
}
pub(super) fn digest(s: &str) -> bool {
    s.len() == 64
        && s.bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
pub(super) fn unix(s: &str) -> Result<u64> {
    let t = chrono::DateTime::parse_from_rfc3339(s).context("Invalid provider timestamp")?;
    u64::try_from(t.timestamp()).context("Invalid provider timestamp")
}
impl Control {
    pub fn validate(&self) -> Result<()> {
        self.plan.validate()?;
        ensure!(
            self.schema_version == 1 && self.plan.gcp_infrastructure.is_some(),
            "Invalid control schema/graph"
        );
        let hashes: BTreeMap<_, _> = plan_artifacts(&self.plan)?
            .iter()
            .map(|(n, b)| (n.clone(), hash(b)))
            .collect();
        ensure!(
            self.bundle_digest == hash(&serde_json::to_vec(&hashes)?),
            "Control graph digest mismatch"
        );
        ensure!(
            decimal(&self.source_generation) && uuid(&self.request_id),
            "Invalid source generation/request identity"
        );
        ensure!(
            self.source_sha256 == hash(&source_zip(&self.plan)?),
            "Control source digest mismatch"
        );
        let prefix = format!(
            "{}-docker.pkg.dev/{}/{}-control-images/ows@sha256:",
            self.region(),
            project(&self.plan),
            self.prefix()
        );
        ensure!(
            self.guard_image.strip_prefix(&prefix).is_some_and(digest),
            "Guard image must be digest-pinned in the separate control repository"
        );
        Ok(())
    }
    pub fn region(&self) -> &str {
        match &self.plan.target {
            Target::Gcp { location, .. } => location,
            _ => unreachable!(),
        }
    }
    pub fn prefix(&self) -> String {
        format!("ow-{}", &self.plan.deployment_id[4..16])
    }
    pub fn bucket(&self) -> String {
        format!("{}-{}-control", project(&self.plan), self.prefix())
    }
    pub fn parent(&self) -> String {
        format!(
            "projects/{}/locations/{}",
            project(&self.plan),
            self.region()
        )
    }
    pub fn deployment(&self) -> String {
        format!("{}/deployments/{}", self.parent(), self.plan.deployment_id)
    }
    pub fn job(&self) -> String {
        format!("{}/jobs/{}-cleanup", self.parent(), self.prefix())
    }
    pub fn source_object(&self) -> String {
        format!(
            "{}/source-{}.zip",
            self.plan.deployment_id, self.source_sha256
        )
    }
    pub fn source_uri(&self) -> String {
        format!(
            "gs://{}/{}#{}",
            self.bucket(),
            self.source_object(),
            self.source_generation
        )
    }
    pub fn checkpoint_object(&self) -> String {
        format!("{}/checkpoint.json", self.plan.deployment_id)
    }
    pub fn sha(&self) -> Result<String> {
        Ok(hash(&json_bytes(self)?))
    }
    pub fn control_object(&self) -> Result<String> {
        Ok(format!(
            "{}/control-{}.json",
            self.plan.deployment_id,
            self.sha()?
        ))
    }
    pub fn control_uri(&self) -> Result<String> {
        Ok(format!("gs://{}/{}", self.bucket(), self.control_object()?))
    }
    pub fn executor(&self) -> String {
        format!(
            "projects/{}/serviceAccounts/{}",
            project(&self.plan),
            accounts(&self.plan)["executor"]
        )
    }
    pub fn request(&self) -> Value {
        json!({"serviceAccount":self.executor(),"terraformBlueprint":{"gcsSource":self.source_uri()},"tfVersionConstraint":format!("={}",self.plan.gcp_infrastructure.as_ref().unwrap().terraform_version),"quotaValidation":"ENFORCED","importExistingResources":false,
            "annotations":{"openworker.dev/bundle":self.bundle_digest,"openworker.dev/request":self.request_id,"openworker.dev/expiry":self.plan.expires_at_unix.to_string()}})
    }
    pub fn operation(&self, s: &str) -> Result<()> {
        suffix(s, &format!("{}/operations/", self.parent()))
    }
    pub fn revision(&self, s: &str) -> Result<()> {
        suffix(s, &format!("{}/revisions/", self.deployment()))
    }
}
fn suffix(s: &str, prefix: &str) -> Result<()> {
    let tail = s
        .strip_prefix(prefix)
        .context("Provider receipt outside deployment scope")?;
    ensure!(
        !tail.is_empty()
            && tail.len() <= 128
            && tail
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b"-_".contains(&b)),
        "Invalid provider receipt"
    );
    Ok(())
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub(super) enum Phase {
    Prepared,
    CreateIntent,
    Creating,
    Active,
    CreateFailed,
    DeleteIntent,
    Deleting,
    DeleteFailed,
    ProviderDeleted,
    NamedResourcesAbsent,
    PauseIntent,
    Complete,
    Unused,
    UnusedPauseIntent,
    UnusedComplete,
}
#[derive(Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct Checkpoint {
    pub schema_version: u16,
    pub control_sha256: String,
    pub phase: Phase,
    pub create_intent_at: Option<u64>,
    pub delete_intent_at: Option<u64>,
    pub delete_request_id: String,
    pub create_operation: Option<String>,
    pub delete_operation: Option<String>,
    pub create_time: Option<String>,
    pub revision: Option<String>,
    pub guard_uid: Option<String>,
    pub heartbeat_at: Option<u64>,
    #[serde(default)]
    pub assets: Option<Vec<Asset>>,
}
#[derive(Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct Asset {
    pub path: String,
    pub id: String,
}
impl Asset {
    pub fn validate(&self, c: &Control) -> Result<()> {
        let zone = &c.plan.gcp_infrastructure.as_ref().unwrap().zone;
        let global = format!(
            "/compute/v1/projects/{}/global/instanceTemplates/",
            project(&c.plan)
        );
        let zonal = format!("/compute/v1/projects/{}/zones/{zone}/", project(&c.plan));
        let tail = if let Some(t) = self.path.strip_prefix(&global) {
            t
        } else {
            let t = self
                .path
                .strip_prefix(&zonal)
                .context("Asset outside deployment zone/project")?;
            let (kind, name) = t.split_once('/').context("Invalid asset path")?;
            ensure!(
                [
                    "instances",
                    "disks",
                    "instanceGroups",
                    "instanceGroupManagers"
                ]
                .contains(&kind),
                "Unexpected dependent asset kind"
            );
            name
        };
        ensure!(
            !tail.is_empty()
                && tail.len() <= 128
                && tail
                    .bytes()
                    .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-')
                && decimal(&self.id),
            "Invalid immutable asset receipt"
        );
        Ok(())
    }
}
impl Checkpoint {
    pub fn new(c: &Control) -> Result<Self> {
        Ok(Self {
            schema_version: 1,
            control_sha256: c.sha()?,
            phase: Phase::Prepared,
            create_intent_at: None,
            delete_intent_at: None,
            delete_request_id: uuid::Uuid::new_v4().to_string(),
            create_operation: None,
            delete_operation: None,
            create_time: None,
            revision: None,
            guard_uid: None,
            heartbeat_at: None,
            assets: None,
        })
    }
    pub fn validate(&self, c: &Control) -> Result<()> {
        ensure!(
            self.schema_version == 1
                && self.control_sha256 == c.sha()?
                && uuid(&self.delete_request_id),
            "Checkpoint/control mismatch"
        );
        for op in [&self.create_operation, &self.delete_operation]
            .into_iter()
            .flatten()
        {
            c.operation(op)?;
        }
        if let Some(rev) = &self.revision {
            c.revision(rev)?;
        }
        if let Some(time) = &self.create_time {
            unix(time)?;
        }
        if let Some(uid) = &self.guard_uid {
            ensure!(uuid(uid), "Invalid pinned guard UID");
        }
        if matches!(
            self.phase,
            Phase::Creating | Phase::Active | Phase::CreateFailed
        ) {
            ensure!(
                self.create_operation.is_some(),
                "Missing acknowledged create operation"
            );
        }
        if matches!(self.phase, Phase::Active | Phase::CreateFailed) {
            ensure!(
                self.create_time.is_some(),
                "Missing deployment creation receipt"
            );
        }
        if self.phase == Phase::Active {
            ensure!(
                self.revision.is_some(),
                "Active checkpoint requires original revision receipt"
            );
        }
        if matches!(
            self.phase,
            Phase::Deleting
                | Phase::DeleteFailed
                | Phase::ProviderDeleted
                | Phase::NamedResourcesAbsent
                | Phase::PauseIntent
                | Phase::Complete
        ) {
            ensure!(
                self.delete_operation.is_some(),
                "Missing acknowledged delete operation"
            );
        }
        if let Some(assets) = &self.assets {
            ensure!(assets.len() <= 1000, "Dependent asset inventory limit");
            let mut paths = std::collections::BTreeSet::new();
            for asset in assets {
                asset.validate(c)?;
                ensure!(
                    paths.insert(&asset.path),
                    "Duplicate dependent asset receipt"
                );
            }
        }
        ensure!(
            !matches!(
                self.phase,
                Phase::CreateIntent | Phase::Creating | Phase::Active | Phase::CreateFailed
            ) || self.create_intent_at.is_some(),
            "Missing create intent"
        );
        if matches!(
            self.phase,
            Phase::DeleteIntent
                | Phase::Deleting
                | Phase::DeleteFailed
                | Phase::ProviderDeleted
                | Phase::NamedResourcesAbsent
                | Phase::PauseIntent
                | Phase::Complete
        ) {
            ensure!(
                self.create_operation.is_some()
                    && self.create_time.is_some()
                    && self.delete_intent_at.is_some(),
                "Missing ownership/deletion receipts"
            );
        }
        Ok(())
    }
}

// Small deterministic ZIP writer. Fixed file names only; no subprocess or archive dependency.
pub(super) fn source_zip(p: &Plan) -> Result<Vec<u8>> {
    let files = plan_artifacts(p)?;
    let entries = [
        (
            "main.tf.json",
            files
                .get("gcp-main.tf.json")
                .context("GCP graph required")?,
        ),
        (
            ".terraform.lock.hcl",
            files
                .get("gcp-provider.lock.hcl")
                .context("Provider lock required")?,
        ),
    ];
    let mut out = vec![];
    let mut central = vec![];
    for (name, bytes) in entries {
        let offset = out.len() as u32;
        let mut crc = !0u32;
        for b in bytes {
            crc ^= u32::from(*b);
            for _ in 0..8 {
                crc = (crc >> 1) ^ if crc & 1 == 1 { 0xedb88320 } else { 0 }
            }
        }
        crc = !crc;
        out.extend(0x04034b50u32.to_le_bytes());
        for x in [20u16, 0, 0, 0, 33] {
            out.extend(x.to_le_bytes())
        }
        for x in [crc, bytes.len() as u32, bytes.len() as u32] {
            out.extend(x.to_le_bytes())
        }
        out.extend((name.len() as u16).to_le_bytes());
        out.extend(0u16.to_le_bytes());
        out.extend(name.as_bytes());
        out.extend(bytes);
        central.extend(0x02014b50u32.to_le_bytes());
        for x in [0x0314u16, 20, 0, 0, 0, 33] {
            central.extend(x.to_le_bytes())
        }
        for x in [crc, bytes.len() as u32, bytes.len() as u32] {
            central.extend(x.to_le_bytes())
        }
        central.extend((name.len() as u16).to_le_bytes());
        for _ in 0..4 {
            central.extend(0u16.to_le_bytes())
        }
        central.extend((0o100600u32 << 16).to_le_bytes());
        central.extend(offset.to_le_bytes());
        central.extend(name.as_bytes());
    }
    let offset = out.len() as u32;
    let size = central.len() as u32;
    out.extend(central);
    out.extend(0x06054b50u32.to_le_bytes());
    for x in [0u16, 0, 2, 2] {
        out.extend(x.to_le_bytes())
    }
    out.extend(size.to_le_bytes());
    out.extend(offset.to_le_bytes());
    out.extend(0u16.to_le_bytes());
    Ok(out)
}
