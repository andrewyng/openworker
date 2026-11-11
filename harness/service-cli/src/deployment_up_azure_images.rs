//! Managed-identity/OAuth registry publication. Intent is durable before push;
//! an absent tag after an uncertain push is not automatic retry permission.
use super::*;
use zeroize::Zeroizing;
struct Temporary(PathBuf);
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
        let mut auth_env = self.environment()?;
        auth_env.insert("DOCKER_CONFIG".into(), arg(&directory_path)?);
        let mut login = self
            .az_json(strings(&[
                "acr",
                "login",
                "--name",
                &registry,
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
                &host,
                "--username",
                "00000000-0000-0000-0000-000000000000",
                "--password-stdin",
            ]),
            Some(token.as_bytes().to_vec()),
            &auth_env,
        )
        .await?;
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
            self.tool(
                "docker",
                strings(&["pull", "--platform", "linux/amd64", source]),
                None,
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
            let manifest: Value = serde_json::from_slice(&raw)?;
            ensure!(
                manifest["config"]["digest"] == id
                    && (format!("sha256:{}", hash(&raw)) == digest
                        || raw
                            .strip_suffix(b"\n")
                            .is_some_and(|b| format!("sha256:{}", hash(b)) == digest)),
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
        let raw = Zeroizing::new(self.tool("az", std::mem::take(&mut args), None).await?);
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
