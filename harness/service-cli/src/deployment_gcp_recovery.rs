//! Local GCP lifecycle preparation and recovery contract. No provider mutations.
//! Unknown outcome from connection loss must be returned to the caller/LLM. It
//! must read/check status before deciding to retry non-idempotent tool actions.
use super::*;

#[derive(Subcommand)]
pub enum Lifecycle {
    /// Persist a fixed request identity before any future provider create call.
    Prepare {
        #[arg(long)]
        plan: PathBuf,
        #[arg(long)]
        out: PathBuf,
    },
    /// Show local journal state only. Does not observe Google Cloud.
    Status {
        #[arg(long)]
        state: PathBuf,
    },
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "snake_case")]
enum Phase {
    Prepared,
    CreateIntent,
    CreateUnknown,
    Creating,
    Active,
    DeleteIntent,
    DeleteUnknown,
    Deleting,
    Destroyed,
}

#[derive(Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Journal {
    schema_version: u16,
    plan: PathBuf,
    bundle_digest: String,
    deployment_id: String,
    resource_name: String,
    request_id: String,
    phase: Phase,
    create_operation: Option<String>,
    delete_operation: Option<String>,
    create_time: Option<String>,
    revision: Option<String>,
}

#[derive(Serialize)]
#[serde(rename_all = "snake_case")]
enum Decision {
    AwaitQualification,
    ObserveCreate,
    ObserveDelete,
    ObserveResources,
    RequireOwnershipReceipt,
    RequireReview,
    AlreadyDestroyed,
}

impl Journal {
    fn validate(&self, p: &Plan, digest: &str) -> Result<()> {
        ensure!(
            self.schema_version == 1
                && p.gcp_infrastructure.is_some()
                && self.bundle_digest == digest
                && self.deployment_id == p.deployment_id
                && self.resource_name == resource_name(p),
            "Lifecycle/graph identity mismatch"
        );
        let id = uuid::Uuid::parse_str(&self.request_id)?;
        ensure!(
            !id.is_nil() && id.to_string() == self.request_id,
            "Invalid persisted request UUID"
        );
        let region = self.resource_name.split("/deployments/").next().unwrap();
        for op in [&self.create_operation, &self.delete_operation]
            .into_iter()
            .flatten()
        {
            let suffix = op
                .strip_prefix(&format!("{region}/operations/"))
                .context("Operation outside target scope")?;
            ensure!(
                !suffix.is_empty()
                    && suffix.len() <= 128
                    && suffix
                        .bytes()
                        .all(|b| b.is_ascii_alphanumeric() || b"-_".contains(&b)),
                "Invalid provider operation"
            );
        }
        if let Some(revision) = &self.revision {
            let suffix = revision
                .strip_prefix(&format!("{}/revisions/", self.resource_name))
                .context("Revision outside target scope")?;
            ensure!(
                !suffix.is_empty()
                    && suffix.len() <= 128
                    && suffix
                        .bytes()
                        .all(|b| b.is_ascii_alphanumeric() || b"-_".contains(&b)),
                "Invalid provider revision"
            );
        }
        if let Some(t) = &self.create_time {
            ensure!(
                !t.is_empty()
                    && t.len() <= 40
                    && t.bytes()
                        .all(|b| b.is_ascii_digit() || b"-:.TZ+".contains(&b)),
                "Invalid provider creation receipt"
            );
        }
        if matches!(
            self.phase,
            Phase::Creating
                | Phase::Active
                | Phase::DeleteIntent
                | Phase::DeleteUnknown
                | Phase::Deleting
                | Phase::Destroyed
        ) {
            ensure!(
                self.create_operation.is_some()
                    && self.create_time.is_some()
                    && self.revision.is_some(),
                "Acknowledged creation receipts required"
            );
        }
        if matches!(self.phase, Phase::Deleting | Phase::Destroyed) {
            ensure!(
                self.delete_operation.is_some(),
                "Delete acknowledgement required"
            );
        }
        Ok(())
    }

    fn next(&self) -> Decision {
        // Intent/unknown never authorizes another write, even when a read is 404.
        // A matching deployment name/label cannot substitute for a creation receipt.
        match self.phase {
            Phase::Prepared => Decision::AwaitQualification,
            Phase::CreateIntent | Phase::CreateUnknown if self.create_operation.is_none() => {
                Decision::RequireOwnershipReceipt
            }
            Phase::CreateIntent | Phase::CreateUnknown | Phase::Creating => Decision::ObserveCreate,
            Phase::Active => Decision::ObserveResources,
            Phase::DeleteIntent | Phase::DeleteUnknown if self.delete_operation.is_none() => {
                Decision::RequireReview
            }
            Phase::DeleteIntent | Phase::DeleteUnknown | Phase::Deleting => Decision::ObserveDelete,
            Phase::Destroyed => Decision::AlreadyDestroyed,
        }
    }
}

fn resource_name(p: &Plan) -> String {
    let Target::Gcp { project, location } = &p.target else {
        unreachable!()
    };
    format!(
        "projects/{project}/locations/{location}/deployments/{}",
        p.deployment_id
    )
}

fn private_file(path: &Path, bytes: &[u8]) -> Result<()> {
    let mut opts = fs::OpenOptions::new();
    opts.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        opts.mode(0o600);
    }
    let mut f = opts.open(path)?;
    f.write_all(bytes)?;
    f.sync_all()?;
    Ok(())
}

pub(super) fn run(command: &Lifecycle) -> Result<()> {
    match command {
        Lifecycle::Prepare { plan, out } => {
            let (p, manifest) = inspect(plan)?;
            ensure!(
                p.gcp_infrastructure.is_some(),
                "GCP infrastructure export required"
            );
            let at = now()?;
            ensure!(
                p.created_at_unix <= at && at < p.expires_at_unix,
                "Plan expired or future-dated"
            );
            let parent = out
                .parent()
                .filter(|v| !v.as_os_str().is_empty())
                .unwrap_or(Path::new("."))
                .canonicalize()?;
            let plan = plan.canonicalize()?;
            ensure!(
                !parent.starts_with(&plan),
                "Lifecycle state must be outside immutable plan"
            );
            let mut dir = fs::DirBuilder::new();
            #[cfg(unix)]
            {
                use std::os::unix::fs::DirBuilderExt;
                dir.mode(0o700);
            }
            dir.create(out)
                .context("Lifecycle directory must be fresh")?;
            let j = Journal {
                schema_version: 1,
                plan,
                bundle_digest: manifest.bundle_digest,
                deployment_id: p.deployment_id.clone(),
                resource_name: resource_name(&p),
                request_id: uuid::Uuid::new_v4().to_string(),
                phase: Phase::Prepared,
                create_operation: None,
                delete_operation: None,
                create_time: None,
                revision: None,
            };
            j.validate(&p, &j.bundle_digest)?;
            private_file(&out.join("gcp-journal.json"), &json_bytes(&j)?)?;
            #[cfg(unix)]
            fs::File::open(out)?.sync_all()?;
            summary(&j)
        }
        Lifecycle::Status { state } => {
            let m = fs::symlink_metadata(state)?;
            ensure!(
                m.is_dir() && !m.file_type().is_symlink(),
                "Invalid lifecycle directory"
            );
            // A future transport must hold this same lock throughout read/intent/write.
            let lock_path = state.join("gcp.lock");
            if let Ok(m) = fs::symlink_metadata(&lock_path) {
                ensure!(
                    m.is_file() && !m.file_type().is_symlink(),
                    "Invalid lifecycle lock"
                );
            }
            let mut opts = fs::OpenOptions::new();
            opts.read(true).write(true).create(true).truncate(false);
            #[cfg(unix)]
            {
                use std::os::unix::fs::OpenOptionsExt;
                opts.mode(0o600);
            }
            let lock = opts.open(lock_path)?;
            lock.try_lock()
                .context("Lifecycle state locked by another process")?;
            let j: Journal = serde_json::from_slice(&read_file(state, "gcp-journal.json")?)?;
            let (p, m) = inspect(&j.plan)?;
            j.validate(&p, &m.bundle_digest)?;
            summary(&j)
        }
    }
}

fn summary(j: &Journal) -> Result<()> {
    crate::output(
        json!({"deploymentId":j.deployment_id,"bundleDigest":j.bundle_digest,"resourceName":j.resource_name,"phase":j.phase,"requestId":j.request_id,"next":j.next(),"cloudObservation":"not_checked","cloudMutations":0,"applyReady":false,"automaticRetry":false}),
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    fn journal(phase: Phase) -> Journal {
        Journal {
            schema_version: 1,
            plan: "unused".into(),
            bundle_digest: "digest".into(),
            deployment_id: "unused".into(),
            resource_name: "projects/p/locations/r/deployments/d".into(),
            request_id: uuid::Uuid::new_v4().to_string(),
            phase,
            create_operation: None,
            delete_operation: None,
            create_time: None,
            revision: None,
        }
    }
    #[test]
    fn unknown_or_persisted_intents_never_choose_a_mutation() {
        for phase in [Phase::CreateIntent, Phase::CreateUnknown] {
            let mut j = journal(phase);
            assert!(matches!(j.next(), Decision::RequireOwnershipReceipt));
            j.create_operation = Some("acknowledged-operation".into());
            assert!(matches!(j.next(), Decision::ObserveCreate));
        }
        for phase in [Phase::DeleteIntent, Phase::DeleteUnknown] {
            let mut j = journal(phase);
            assert!(matches!(j.next(), Decision::RequireReview));
            j.delete_operation = Some("acknowledged-operation".into());
            assert!(matches!(j.next(), Decision::ObserveDelete));
        }
        assert!(matches!(
            journal(Phase::Prepared).next(),
            Decision::AwaitQualification
        ));
        assert!(matches!(
            journal(Phase::Creating).next(),
            Decision::ObserveCreate
        ));
        assert!(matches!(
            journal(Phase::Active).next(),
            Decision::ObserveResources
        ));
        assert!(matches!(
            journal(Phase::Deleting).next(),
            Decision::ObserveDelete
        ));
        assert!(matches!(
            journal(Phase::Destroyed).next(),
            Decision::AlreadyDestroyed
        ));
    }
}
