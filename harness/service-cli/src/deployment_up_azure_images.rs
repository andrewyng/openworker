//! Managed-identity/OAuth registry publication. Intent is durable before push;
//! an absent tag after an uncertain push is not automatic retry permission.
use super::*;
use zeroize::Zeroizing;
struct Temporary(PathBuf);
pub(super) fn private_password_file(path: &Path) -> Result<Zeroizing<Vec<u8>>> {
    use std::io::Read;
    let meta = fs::symlink_metadata(path).context("Private database password file unavailable")?;
    ensure!(
        meta.is_file() && !meta.file_type().is_symlink() && meta.len() > 0 && meta.len() <= 4098,
        "Use a bounded regular private password file"
    );
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        ensure!(
            meta.permissions().mode() & 0o077 == 0,
            "Database password file must be private (chmod 600)"
        );
    }
    let file = fs::File::open(path).context("Private database password read failed")?;
    let opened = file.metadata()?;
    ensure!(
        opened.is_file() && opened.len() == meta.len(),
        "Database password file changed while opening"
    );
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        ensure!(
            opened.dev() == meta.dev() && opened.ino() == meta.ino() && opened.mode() & 0o077 == 0,
            "Database password file identity changed"
        );
    }
    let mut bytes = Zeroizing::new(vec![]);
    file.take(4099)
        .read_to_end(&mut bytes)
        .context("Private database password read failed")?;
    ensure!(
        bytes.len() <= 4098 && bytes.len() as u64 == opened.len(),
        "Database password file changed while reading"
    );
    let text = std::str::from_utf8(&bytes)
        .context("Invalid database password encoding")?
        .trim_end_matches(['\r', '\n']);
    ensure!(
        !text.is_empty() && text.len() <= 4096 && !text.contains(['\r', '\n', '\0']),
        "Invalid private database password"
    );
    Ok(bytes)
}
fn source_acr(source: &str) -> Result<Option<(String, String)>> {
    let host = source.split('/').next().context("Image registry missing")?;
    let Some(name) = host.strip_suffix(".azurecr.io") else {
        return Ok(None);
    };
    ensure!(
        (5..=50).contains(&name.len())
            && name
                .bytes()
                .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit()),
        "Invalid private source registry"
    );
    Ok(Some((name.into(), host.into())))
}
fn verified_publication(source: &str, local_id: &str, digest: &str, raw: &[u8]) -> bool {
    let digest_matches = format!("sha256:{}", hash(raw)) == digest
        || raw
            .strip_suffix(b"\n")
            .is_some_and(|bytes| format!("sha256:{}", hash(bytes)) == digest);
    let Ok(manifest) = serde_json::from_slice::<Value>(raw) else {
        return false;
    };
    // Containerd image stores may expose an index digest as Docker's image Id.
    // Exact preservation of the reviewed source manifest/index is strongest;
    // legacy stores may instead publish a manifest with the same config Id.
    digest_matches
        && (source
            .rsplit_once('@')
            .is_some_and(|(_, pin)| pin == digest)
            || manifest["config"]["digest"] == local_id)
}
fn plugin_config(plugins: &Value) -> Result<Value> {
    let entries: Vec<_> = plugins
        .as_array()
        .context("Docker plugin inventory missing")?
        .iter()
        .filter(|p| p["Name"] == "buildx")
        .collect();
    ensure!(
        entries.len() == 1,
        "Install one working Docker Buildx plugin"
    );
    let path = Path::new(entries[0]["Path"].as_str().context("Buildx path missing")?);
    ensure!(path.is_absolute(), "Buildx plugin path must be absolute");
    Ok(json!({"cliPluginsExtraDirs":[path.parent().context("Buildx directory missing")?]}))
}
impl Drop for Temporary {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}
impl Native<'_> {
    async fn az_json(&self, args: Vec<String>) -> Result<Value> {
        let mut a = args;
        a.extend([
            "--subscription".into(),
            self.review.config.cloud.account().into(),
            "--only-show-errors".into(),
            "-o".into(),
            "json".into(),
        ]);
        let bytes = Zeroizing::new(self.tool("az", a, None).await?);
        serde_json::from_slice(&bytes).context("Invalid Azure CLI response")
    }
    async fn acr_tag(&self, registry: &str, repo: &str, tag: &str) -> Result<Option<String>> {
        let names = self
            .az_json(strings(&["acr", "repository", "list", "--name", registry]))
            .await?;
        ensure!(
            names
                .as_array()
                .is_some_and(|a| a.iter().all(Value::is_string)),
            "Invalid registry repository inventory"
        );
        if !names.as_array().unwrap().iter().any(|s| s == repo) {
            return Ok(None);
        }
        let tags = self
            .az_json(strings(&[
                "acr",
                "repository",
                "show-tags",
                "--name",
                registry,
                "--repository",
                repo,
                "--detail",
            ]))
            .await?;
        let values = tags.as_array().context("Invalid registry tag inventory")?;
        let matching: Vec<_> = values.iter().filter(|v| v["name"] == tag).collect();
        ensure!(matching.len() <= 1, "Ambiguous registry tag");
        let Some(t) = matching.first() else {
            return Ok(None);
        };
        let digest = t["digest"]
            .as_str()
            .context("Registry manifest digest missing")?;
        ensure!(
            digest.starts_with("sha256:")
                && digest.len() == 71
                && digest[7..]
                    .bytes()
                    .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b)),
            "Invalid registry manifest digest"
        );
        Ok(Some(digest.into()))
    }
    async fn login_acr(
        &self,
        registry: &str,
        host: &str,
        auth_env: &BTreeMap<String, String>,
    ) -> Result<()> {
        let mut login = self
            .az_json(strings(&[
                "acr",
                "login",
                "--name",
                registry,
                "--expose-token",
            ]))
            .await?;
        ensure!(
            login["loginServer"] == host,
            "Registry token audience mismatch"
        );
        let token = Zeroizing::new(
            login
                .as_object_mut()
                .context("Invalid registry login")?
                .remove("accessToken")
                .and_then(|v| {
                    if let Value::String(s) = v {
                        Some(s)
                    } else {
                        None
                    }
                })
                .context("Registry token missing")?,
        );
        super::super::super::local::exec_with_env(
            Path::new("docker"),
            &strings(&[
                "login",
                host,
                "--username",
                "00000000-0000-0000-0000-000000000000",
                "--password-stdin",
            ]),
            Some(token.as_bytes().to_vec()),
            auth_env,
        )
        .await?;
        Ok(())
    }
    pub(super) async fn azure_images(&self) -> Result<Observation> {
        let material = self.azure_material().await?;
        let registry = text(&material["registry"], "name")?;
        let host = text(&material["registry"], "loginServer")?;
        ensure!(
            host == format!("{registry}.azurecr.io")
                && material["registry"]["createdAt"].is_string(),
            "Owned ACR identity missing"
        );
        let directory_path = self
            .root
            .join(format!(".registry-auth-{}", uuid::Uuid::new_v4().simple()));
        directory(&directory_path)?;
        let _temporary = Temporary(directory_path.clone());
        // Private auth must not hide user-installed CLI plugins. Copy only the
        // discovered plugin directory, never the global Docker auth/config file.
        let plugins: Value = serde_json::from_slice(
            &self
                .tool(
                    "docker",
                    strings(&["info", "--format", "{{json .ClientInfo.Plugins}}"]),
                    None,
                )
                .await?,
        )?;
        write_private(
            &directory_path.join("config.json"),
            &plugin_config(&plugins)?,
        )?;
        let mut auth_env = self.environment()?;
        auth_env.insert("DOCKER_CONFIG".into(), arg(&directory_path)?);
        self.login_acr(&registry, &host, &auth_env).await?;
        let mut authenticated = std::collections::BTreeSet::from([host.clone()]);
        let mut results = BTreeMap::<String, Value>::new();
        for (role, source) in [
            ("harness", Some(&self.package.release.images.harness)),
            ("runner", Some(&self.package.release.images.runner)),
            (
                "service-ui",
                self.package.release.images.service_ui.as_ref(),
            ),
        ] {
            let Some(source) = source else { continue };
            if let Some((name, source_host)) = source_acr(source)? {
                if authenticated.insert(source_host.clone()) {
                    self.login_acr(&name, &source_host, &auth_env).await?;
                }
            }
            super::super::super::local::exec_with_env(
                Path::new("docker"),
                &strings(&["pull", "--platform", "linux/amd64", source]),
                None,
                &auth_env,
            )
            .await?;
            let local: Value = serde_json::from_slice(
                &self
                    .tool("docker", strings(&["image", "inspect", source]), None)
                    .await?,
            )?;
            let local = local
                .as_array()
                .filter(|a| a.len() == 1)
                .map(|a| &a[0])
                .context("Ambiguous local image")?;
            ensure!(
                local["Architecture"] == "amd64" && local["Os"] == "linux",
                "Release image platform mismatch"
            );
            let id = text(local, "Id")?;
            let tag = format!(
                "ows-{}",
                &hash(format!("{}:{role}:{id}", self.review.deployment_id).as_bytes())[..32]
            );
            let target = format!("{host}/{role}:{tag}");
            let path = self.root.join(format!("image-{role}.json"));
            let mut receipt = if path.exists() {
                self.document(&path)?
            } else {
                json!({"source":source,"configDigest":id,"registryCreatedAt":material["registry"]["createdAt"],"target":target,"dispatched":false})
            };
            ensure!(
                receipt["source"] == *source
                    && receipt["configDigest"] == id
                    && receipt["registryCreatedAt"] == material["registry"]["createdAt"]
                    && receipt["target"] == target,
                "Original image publication identity changed"
            );
            let mut remote = self.acr_tag(&registry, role, &tag).await?;
            if remote.is_none() {
                ensure!(
                    receipt["dispatched"] == false,
                    "Registry push outcome unknown; inspect original tag. No automatic push retry."
                );
                self.tool("docker", strings(&["tag", source, &target]), None)
                    .await?;
                receipt["dispatched"] = json!(true);
                write_private(&path, &receipt)?;
                super::super::super::local::exec_with_env(
                    Path::new("docker"),
                    &strings(&["push", &target]),
                    None,
                    &auth_env,
                )
                .await?;
                remote = self.acr_tag(&registry, role, &tag).await?;
            }
            let digest =
                remote.context("Original pushed tag not observed; inspect before retry")?;
            let pinned = format!("{host}/{role}@{digest}");
            let raw = super::super::super::local::exec_with_env(
                Path::new("docker"),
                &strings(&["buildx", "imagetools", "inspect", "--raw", &pinned]),
                None,
                &auth_env,
            )
            .await?;
            ensure!(
                verified_publication(source, &id, &digest, &raw),
                "Remote image content differs from the reviewed local image"
            );
            receipt["image"] = json!(pinned);
            write_private(&path, &receipt)?;
            results.insert(role.into(), receipt);
            write_private(&self.root.join("published-images.json"), &results)?;
        }
        Ok(Observation::Complete(json!(results)))
    }
    pub(super) async fn azure_database_url(&self) -> Result<Zeroizing<String>> {
        let (p, _) = self.plan()?;
        let value = serde_json::to_value(p)?;
        let secret = &value["azureInfrastructure"]["postgresPassword"];
        let vault = text(secret, "keyVaultId")?;
        let vault = vault.rsplit('/').next().context("Vault identity missing")?;
        let mut args = strings(&[
            "keyvault",
            "secret",
            "show",
            "--vault-name",
            vault,
            "--name",
            secret["secretName"].as_str().unwrap(),
            "--version",
            secret["secretVersion"].as_str().unwrap(),
            "--query",
            "value",
            "--output",
            "tsv",
            "--only-show-errors",
            "--subscription",
            self.review.config.cloud.account(),
        ]);
        let raw = if let Some(path) = &self.database_password_file {
            private_password_file(path)?
        } else {
            Zeroizing::new(self.tool("az", std::mem::take(&mut args), None).await?)
        };
        let password = std::str::from_utf8(&raw)
            .context("Invalid secret encoding")?
            .trim_end_matches(['\r', '\n']);
        ensure!(
            !password.is_empty() && password.len() <= 4096,
            "Invalid managed database secret"
        );
        let encoded = password
            .bytes()
            .map(|b| {
                if b.is_ascii_alphanumeric() || b"-._~".contains(&b) {
                    (b as char).to_string()
                } else {
                    format!("%{b:02X}")
                }
            })
            .collect::<String>();
        let host = text(&self.azure_material().await?, "postgresHost")?;
        Ok(Zeroizing::new(format!(
            "postgresql://owsadmin:{encoded}@{host}:5432/postgres?sslmode=require"
        )))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[cfg(unix)]
    #[test]
    fn password_file_requires_private_regular_bounded_input_without_echoing_values() {
        use std::os::unix::fs::PermissionsExt;
        let temp = tempfile::tempdir().unwrap();
        let path = temp.path().join("password");
        fs::write(&path, b"private-canary\n").unwrap();
        fs::set_permissions(&path, fs::Permissions::from_mode(0o600)).unwrap();
        assert!(private_password_file(&path).is_ok());
        fs::set_permissions(&path, fs::Permissions::from_mode(0o644)).unwrap();
        let error = private_password_file(&path).unwrap_err().to_string();
        assert!(!error.contains("private-canary"));
        fs::set_permissions(&path, fs::Permissions::from_mode(0o600)).unwrap();
        fs::write(&path, b"multiple\nlines").unwrap();
        assert!(private_password_file(&path).is_err());
        let link = temp.path().join("link");
        std::os::unix::fs::symlink(&path, &link).unwrap();
        assert!(private_password_file(&link).is_err());
    }
    #[test]
    fn isolated_auth_preserves_only_buildx_discovery_without_copying_global_credentials() {
        let value = plugin_config(&json!([{"Name":"buildx","Path":"/opt/docker/cli-plugins/docker-buildx","secret":"must-not-copy"}])).unwrap();
        assert_eq!(
            value,
            json!({"cliPluginsExtraDirs":["/opt/docker/cli-plugins"]})
        );
        assert!(plugin_config(&json!([])).is_err());
        assert!(plugin_config(&json!([{"Name":"buildx","Path":"relative/plugin"}])).is_err());
    }
    #[test]
    fn private_source_registry_is_exact_and_public_sources_need_no_login() {
        assert_eq!(
            source_acr("control123.azurecr.io/runtime@sha256:abc").unwrap(),
            Some(("control123".into(), "control123.azurecr.io".into()))
        );
        assert_eq!(
            source_acr("ghcr.io/nvidia/runtime@sha256:abc").unwrap(),
            None
        );
        assert!(source_acr("wrong.nested.azurecr.io/image").is_err());
        assert!(source_acr("user@control123.azurecr.io/image").is_err());
    }
    #[test]
    fn publication_accepts_exact_reviewed_index_or_verified_legacy_config_only() {
        let index = br#"{"schemaVersion":2,"manifests":[]}"#;
        let digest = format!("sha256:{}", hash(index));
        assert!(verified_publication(
            &format!("registry/image@{digest}"),
            &digest,
            &digest,
            index
        ));
        assert!(!verified_publication(
            "registry/image@sha256:other",
            &digest,
            &digest,
            index
        ));
        let manifest = br#"{"schemaVersion":2,"config":{"digest":"sha256:config"}}"#;
        let digest = format!("sha256:{}", hash(manifest));
        assert!(verified_publication(
            "registry/image@sha256:source",
            "sha256:config",
            &digest,
            manifest
        ));
        assert!(!verified_publication(
            "registry/image@sha256:source",
            "sha256:wrong",
            &digest,
            manifest
        ));
        assert!(!verified_publication(
            &format!("registry/image@{digest}"),
            "sha256:config",
            &digest,
            index
        ));
    }
}
