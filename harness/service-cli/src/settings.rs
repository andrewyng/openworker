use super::Args;
use anyhow::{ensure, Context as _, Result};
use openworker_sdk::service::{ConnectOptions, Credential, Service};
use serde::{Deserialize, Serialize};
use std::{
    collections::BTreeMap,
    io::Write,
    path::{Path, PathBuf},
    sync::Arc,
};
#[derive(Default, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Data {
    pub selected: Option<String>,
    pub contexts: BTreeMap<String, Context>,
}
#[derive(Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Context {
    url: String,
    namespace: String,
    token_file: Option<PathBuf>,
    token_env: Option<String>,
    ca_file: Option<PathBuf>,
    service_id: Option<String>,
}
pub struct Settings {
    path: PathBuf,
    pub data: Data,
}
fn private_file(path: &Path) -> Result<()> {
    let m = std::fs::symlink_metadata(path).context("Private file unavailable")?;
    ensure!(
        m.is_file() && !m.file_type().is_symlink(),
        "Private file must be a regular file, not a symlink"
    );
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        ensure!(
            m.permissions().mode() & 0o077 == 0,
            "Private file permissions must be 0600 or stricter"
        );
    }
    Ok(())
}
pub(crate) fn token(path: &Path) -> Result<zeroize::Zeroizing<String>> {
    private_file(path)?;
    let b = std::fs::read(path)?;
    ensure!(b.len() <= 4096, "Credential file too large");
    let s = zeroize::Zeroizing::new(String::from_utf8(b).context("Credential file must be UTF-8")?);
    Ok(s.trim().to_owned().into())
}
impl Settings {
    pub fn load(path: Option<&PathBuf>) -> Result<Self> {
        let path = match path {
            Some(p) => std::path::absolute(p)?,
            None => PathBuf::from(
                std::env::var_os("HOME")
                    .or_else(|| std::env::var_os("USERPROFILE"))
                    .context("Home directory unavailable; use --config")?,
            )
            .join(".config/openworker/ows/config.json"),
        };
        let data = if path.exists() {
            private_file(&path)?;
            let bytes = std::fs::read(&path)?;
            ensure!(bytes.len() <= 64000, "Configuration too large");
            serde_json::from_slice(&bytes)
                .map_err(|_| anyhow::anyhow!("Invalid ows configuration"))?
        } else {
            Data::default()
        };
        Ok(Self { path, data })
    }
    pub fn save(&self) -> Result<()> {
        let parent = self.path.parent().context("Configuration parent absent")?;
        if !parent.exists() {
            std::fs::create_dir_all(parent)?;
            #[cfg(unix)]
            {
                use std::os::unix::fs::PermissionsExt;
                std::fs::set_permissions(parent, std::fs::Permissions::from_mode(0o700))?;
            }
        }
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            ensure!(
                std::fs::symlink_metadata(parent)?.permissions().mode() & 0o077 == 0,
                "Configuration directory must be private (0700)"
            );
        }
        if self.path.exists() {
            private_file(&self.path)?;
        }
        let tmp = parent.join(format!(
            ".{}.tmp",
            openworker_sdk::service::new_idempotency_key()
        ));
        let mut opts = std::fs::OpenOptions::new();
        opts.write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            opts.mode(0o600);
        }
        let mut f = opts.open(&tmp)?;
        f.write_all(&serde_json::to_vec_pretty(&self.data)?)?;
        f.sync_all()?;
        drop(f);
        std::fs::rename(&tmp, &self.path)?;
        Ok(())
    }
    fn selected(&self, args: &Args) -> Option<&Context> {
        args.context
            .as_ref()
            .or(self.data.selected.as_ref())
            .and_then(|n| self.data.contexts.get(n))
    }
    fn resolve(&self, args: &Args) -> Result<Context> {
        if let Some(n) = &args.context {
            ensure!(self.data.contexts.contains_key(n), "Unknown context");
        }
        let c = self.selected(args);
        Ok(Context {
            url: openworker_sdk::service::endpoint(
                args.url
                    .as_deref()
                    .or(c.map(|c| c.url.as_str()))
                    .context("Provide --url or select a context")?,
            )?,
            namespace: args
                .namespace
                .clone()
                .or_else(|| c.map(|c| c.namespace.clone()))
                .context("Provide --namespace or select a context")?,
            token_file: if args.token_env.is_some() {
                None
            } else {
                args.token_file
                    .clone()
                    .or_else(|| c.and_then(|c| c.token_file.clone()))
            },
            token_env: if args.token_file.is_some() {
                None
            } else {
                args.token_env
                    .clone()
                    .or_else(|| c.and_then(|c| c.token_env.clone()))
            },
            ca_file: args
                .ca_file
                .clone()
                .or_else(|| c.and_then(|c| c.ca_file.clone())),
            service_id: c.and_then(|c| c.service_id.clone()),
        })
    }
    pub fn add(&mut self, name: &str, args: &Args) -> Result<()> {
        super::component(name)?;
        ensure!(
            !self.data.contexts.contains_key(name),
            "Context already exists"
        );
        let url = openworker_sdk::service::endpoint(
            args.url.as_deref().context("context add requires --url")?,
        )?;
        let namespace = args
            .namespace
            .clone()
            .context("context add requires --namespace")?;
        self.data.contexts.insert(
            name.into(),
            Context {
                url,
                namespace,
                token_file: None,
                token_env: None,
                ca_file: args.ca_file.as_ref().map(std::path::absolute).transpose()?,
                service_id: None,
            },
        );
        if self.data.selected.is_none() {
            self.data.selected = Some(name.into());
        }
        Ok(())
    }
    pub async fn connect(&self, args: &Args) -> Result<Service> {
        let c = self.resolve(args)?;
        let mut options = ConnectOptions::new(
            &c.url,
            &c.namespace,
            c.token_env.as_deref().unwrap_or("OPENWORKER_TOKEN"),
        );
        if let Some(path) = c.token_file {
            let path = std::path::absolute(path)?;
            options.credentials = Credential::Callback(Arc::new(move |_| {
                let path = path.clone();
                Box::pin(async move {
                    token(&path).map_err(|_| openworker_sdk::service::Error {
                        code: "credential_unavailable",
                        status: 0,
                        idempotency_key: None,
                        operation_id: None,
                    })
                })
            }));
        }
        if let Some(path) = c.ca_file {
            let bytes = std::fs::read(path).context("CA file unavailable")?;
            ensure!(bytes.len() <= 256000, "CA file too large");
            options.ca_pem = Some(bytes);
        }
        let s = Service::connect(options).await?;
        if let Some(id) = c.service_id {
            ensure!(
                s.capabilities()["serviceId"] == id,
                "Context service identity changed; use a new context for a different service"
            );
        }
        Ok(s)
    }
    pub fn login(&mut self, args: &Args, service: &Service) -> Result<()> {
        let mut c = self.resolve(args)?;
        ensure!(
            c.token_file.is_some() || c.token_env.is_some(),
            "login requires --token-file or --token-env"
        );
        if let Some(path) = c.token_file {
            private_file(&path)?;
            c.token_file = Some(std::fs::canonicalize(path)?);
        }
        c.service_id = Some(
            service.capabilities()["serviceId"]
                .as_str()
                .context("Service identity missing")?
                .into(),
        );
        let name = args
            .context
            .as_ref()
            .or(self.data.selected.as_ref())
            .context("Add/select a context before login")?
            .clone();
        self.data.contexts.insert(name, c);
        Ok(())
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn private_config_roundtrip() {
        let d = tempfile::tempdir().unwrap();
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(d.path(), std::fs::Permissions::from_mode(0o700)).unwrap();
        }
        let p = d.path().join("config.json");
        let s = Settings::load(Some(&p)).unwrap();
        s.save().unwrap();
        assert!(Settings::load(Some(&p)).unwrap().data.contexts.is_empty());
    }
    #[cfg(unix)]
    #[test]
    fn rejects_world_readable_credentials() {
        use std::os::unix::fs::PermissionsExt;
        let d = tempfile::tempdir().unwrap();
        let p = d.path().join("key");
        std::fs::write(&p, "synthetic").unwrap();
        std::fs::set_permissions(&p, std::fs::Permissions::from_mode(0o644)).unwrap();
        assert!(token(&p).is_err());
    }
}
