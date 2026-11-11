//! Release assets and account prerequisites are installed once, separately from
//! application desired state. Neither document can inject executable commands.
use super::*;
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct FilePin {
    pub file: PathBuf,
    pub sha256: String,
}
impl FilePin {
    fn validate(&self) -> Result<()> {
        let meta = fs::symlink_metadata(&self.file)?;
        ensure!(
            meta.is_file() && !meta.file_type().is_symlink() && meta.len() <= 16 * 1024 * 1024,
            "Release file must be a bounded regular file"
        );
        ensure!(
            hash(&fs::read(&self.file)?) == self.sha256,
            "Release artifact checksum mismatch"
        );
        Ok(())
    }
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct Images {
    pub harness: String,
    pub runner: String,
    pub service_ui: Option<String>,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct Release {
    pub schema_version: u16,
    pub version: String,
    pub architecture: String,
    pub images: Images,
    pub service_chart: PathBuf,
    pub service_chart_sha256: String,
    pub openshell_archive: FilePin,
    pub openshell_values: FilePin,
    pub platform_manifest: FilePin,
    pub database_ca: FilePin,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct Setup {
    pub schema_version: u16,
    pub provider: String,
    pub account: String,
    pub region: String,
    /// Existing provider input schema: pinned versions, scoped bootstrap identities
    /// and secret-manager references. Never plaintext database/model passwords.
    pub infrastructure: Value,
    pub operator_arn: Option<String>,
    pub setup_object_id: Option<String>,
    pub setup_principal_type: Option<String>,
    pub resource_group: Option<String>,
    pub release: PathBuf,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct Snapshot {
    pub setup: Setup,
    pub release: Release,
    pub digest: String,
}
fn image(s: &str) -> bool {
    s.len() < 300
        && s.split_once("@sha256:").is_some_and(|(name, d)| {
            name.contains('/')
                && name
                    .bytes()
                    .all(|b| b.is_ascii_alphanumeric() || b"/._-:".contains(&b))
                && d.len() == 64
                && d.bytes()
                    .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        })
}
fn tree(root: &Path) -> Result<String> {
    let meta = fs::symlink_metadata(root)?;
    ensure!(
        meta.is_dir() && !meta.file_type().is_symlink(),
        "Release chart root must be a real directory"
    );
    fn walk(root: &Path, dir: &Path, out: &mut BTreeMap<String, String>) -> Result<()> {
        ensure!(
            dir.strip_prefix(root)?.components().count() <= 16,
            "Release chart nesting too deep"
        );
        for p in fs::read_dir(dir)? {
            let p = p?;
            let meta = fs::symlink_metadata(p.path())?;
            ensure!(
                !meta.file_type().is_symlink(),
                "Release chart cannot contain symlinks"
            );
            if meta.is_dir() {
                walk(root, &p.path(), out)?;
            } else {
                ensure!(
                    meta.is_file() && meta.len() <= 1024 * 1024,
                    "Invalid chart file"
                );
                let name = p
                    .path()
                    .strip_prefix(root)?
                    .components()
                    .map(|c| c.as_os_str().to_string_lossy())
                    .collect::<Vec<_>>()
                    .join("/");
                out.insert(name, hash(&fs::read(p.path())?));
                ensure!(out.len() <= 128, "Release chart too large");
            }
        }
        Ok(())
    }
    let mut files = BTreeMap::new();
    walk(root, root, &mut files)?;
    ensure!(
        files.contains_key("Chart.yaml"),
        "Service chart missing Chart.yaml"
    );
    Ok(hash(&serde_json::to_vec(&files)?))
}
impl Snapshot {
    pub fn validate(&self, c: &Config) -> Result<()> {
        ensure!(
            self.setup.schema_version == 1
                && self.setup.provider == c.cloud.provider()
                && self.setup.account == c.cloud.account()
                && self.setup.region == c.cloud.region(),
            "Setup profile belongs to another cloud target"
        );
        ensure!(
            self.release.schema_version == 1
                && self.release.architecture
                    == if c.cloud.provider() == "aws" {
                        "arm64"
                    } else {
                        "amd64"
                    },
            "Release architecture/provider mismatch"
        );
        for i in [&self.release.images.harness, &self.release.images.runner]
            .into_iter()
            .chain(self.release.images.service_ui.iter())
        {
            ensure!(image(i), "Release images must be pinned by SHA-256");
        }
        for f in [
            &self.release.openshell_archive,
            &self.release.openshell_values,
            &self.release.platform_manifest,
            &self.release.database_ca,
        ] {
            f.validate()?;
        }
        ensure!(
            self.release.openshell_archive.sha256
                == "5ce3e63648d1a80f96f748063e44444069667ae9049457b8aded35cdb2a6171a",
            "New deployment requires the reviewed OpenShell 0.1.7 archive"
        );
        let values: Value =
            serde_json::from_slice(&fs::read(&self.release.openshell_values.file)?)?;
        ensure!(
            values["global"]["image"]["tag"] == "0.1.7"
                && values["server"]["auth"]["allowUnauthenticatedUsers"] == false,
            "OpenShell release/authentication mismatch"
        );
        let ca = fs::read(&self.release.database_ca.file)?;
        let certificates =
            reqwest::Certificate::from_pem_bundle(&ca).context("Invalid database CA bundle")?;
        ensure!(
            !certificates.is_empty() && certificates.len() <= 32,
            "Database CA bundle must contain 1..32 certificates"
        );
        ensure!(
            tree(&self.release.service_chart)? == self.release.service_chart_sha256,
            "Service chart changed since release packaging"
        );
        ensure!(
            self.digest == hash(&serde_json::to_vec(&(&self.setup, &self.release))?),
            "Release/setup review digest changed"
        );
        Ok(())
    }
}
pub(super) fn load(c: &Config) -> Result<Snapshot> {
    let path = if let Some(p) = &c.advanced.setup_profile {
        p.clone()
    } else {
        let home =
            std::env::var_os("HOME").context("HOME unavailable; set advanced.setupProfile")?;
        PathBuf::from(home)
            .join(".config/ows/deployments")
            .join(c.cloud.provider())
            .join(c.cloud.account())
            .join(format!("{}.json", c.cloud.region()))
    };
    ensure!(path.is_file(),"Account setup profile is missing at {}. Install the reviewed account/release prerequisites once (see deploy/service/up/README.md), then rerun the same up command. No cloud resources were created.",path.display());
    ensure!(
        fs::metadata(&path)?.len() <= 65536,
        "Setup profile exceeds 64 KiB"
    );
    let setup: Setup = serde_json::from_slice(&fs::read(&path)?).map_err(|_| {
        anyhow::anyhow!("Invalid account setup profile; use references, not inline credentials")
    })?;
    let release_path = c.advanced.release.as_ref().unwrap_or(&setup.release);
    ensure!(
        fs::metadata(release_path)?.len() <= 65536,
        "Release manifest exceeds 64 KiB"
    );
    let release: Release = serde_json::from_slice(&fs::read(release_path)?)
        .context("Read reviewed release manifest")?;
    let digest = hash(&serde_json::to_vec(&(&setup, &release))?);
    let s = Snapshot {
        setup,
        release,
        digest,
    };
    s.validate(c)?;
    Ok(s)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn changed_chart_or_file_changes_release_identity() {
        let d = tempfile::tempdir().unwrap();
        let p = d.path();
        fs::write(p.join("Chart.yaml"), "name: example").unwrap();
        let initial = tree(p).unwrap();
        fs::write(p.join("values.yaml"), "image: changed").unwrap();
        assert_ne!(tree(p).unwrap(), initial);
        let pin = FilePin {
            file: p.join("Chart.yaml"),
            sha256: hash(b"name: example"),
        };
        pin.validate().unwrap();
        fs::write(&pin.file, "changed").unwrap();
        assert!(pin.validate().is_err());
        assert!(!image("ghcr.io/example/service:latest"));
        assert!(image(&format!(
            "ghcr.io/example/service@sha256:{}",
            "a".repeat(64)
        )));
    }
    #[cfg(unix)]
    #[test]
    fn release_chart_cannot_follow_links() {
        let d = tempfile::tempdir().unwrap();
        fs::write(d.path().join("Chart.yaml"), "name: test").unwrap();
        std::os::unix::fs::symlink(d.path(), d.path().join("loop")).unwrap();
        assert!(tree(d.path()).is_err());
    }
}
