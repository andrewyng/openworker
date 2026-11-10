//! Explicit sandbox Git credential plumbing. Only OpenShell placeholders may be
//! forwarded; real tokens fail closed. Network/operation policy remains enforced
//! independently by OpenShell. This is not the offline handoff Git path.
use anyhow::{ensure, Context, Result};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

/// Trusted application configuration. No credential value is stored here.
/// Pinning is explicit setup, never inferred from a model-supplied server name.
#[derive(Clone, Debug, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ProviderPin {
    pub name: String,
    pub provider_id: String,
    pub profile_id: String,
    pub profile_workspace: String,
    pub profile_digest: String,
    /// None means the original provider has empty non-secret configuration.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub configuration_digest: Option<String>,
}
impl ProviderPin {
    pub fn validate(&self) -> Result<()> {
        anyhow::ensure!(
            self.configuration_digest
                .as_ref()
                .is_none_or(|d| d.len() == 64
                    && d.bytes()
                        .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))),
            "Invalid provider configuration digest"
        );
        for id in [&self.name, &self.provider_id, &self.profile_id] {
            anyhow::ensure!(
                !id.is_empty()
                    && id.len() <= 128
                    && id
                        .bytes()
                        .all(|b| b.is_ascii_alphanumeric() || b"_-".contains(&b)),
                "Invalid pinned credential provider identity"
            );
        }
        anyhow::ensure!(
            self.profile_workspace.len() <= 128
                && self
                    .profile_workspace
                    .bytes()
                    .all(|b| b.is_ascii_alphanumeric() || b"_-".contains(&b)),
            "Invalid provider profile workspace"
        );
        anyhow::ensure!(
            self.profile_digest.len() == 64
                && self
                    .profile_digest
                    .bytes()
                    .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b)),
            "Credential provider profile digest required"
        );
        Ok(())
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Settings {
    /// Exact HTTPS authority, optionally with a non-default port.
    pub host: String,
    /// Canonical owner/repository paths, without .git suffix.
    pub repositories: Vec<String>,
}
impl Settings {
    pub fn validate(&self) -> Result<()> {
        let url = reqwest::Url::parse(&format!("https://{}/", self.host))?;
        ensure!(
            url.host_str().is_some()
                && url.username().is_empty()
                && url.password().is_none()
                && url.path() == "/"
                && url.query().is_none()
                && url.fragment().is_none()
                && self.host.len() <= 255
                && self
                    .host
                    .bytes()
                    .all(|b| b.is_ascii_alphanumeric() || b".-:".contains(&b)),
            "Git authority must be an exact HTTPS host, without credentials or path"
        );
        ensure!(
            (1..=16).contains(&self.repositories.len()),
            "Configure 1..16 Git repositories"
        );
        let mut seen = std::collections::BTreeSet::new();
        for r in &self.repositories {
            let parts: Vec<_> = r.split('/').collect();
            ensure!(
                r.len() <= 200
                    && parts.len() == 2
                    && parts.iter().all(|p| !p.is_empty()
                        && *p != "."
                        && *p != ".."
                        && !p.starts_with('-')
                        && p.bytes()
                            .all(|b| b.is_ascii_alphanumeric() || b"._-".contains(&b)))
                    && !r.ends_with(".git")
                    && seen.insert(r),
                "Git repositories must be unique owner/name references"
            );
        }
        Ok(())
    }
    /// Called by the trusted MCP launcher, not from tool arguments. A new tool
    /// connection supplies its current scoped reference to each new process.
    pub(crate) fn environment(&self) -> Result<Environment> {
        self.read_values(|key| std::env::var(key).ok())
    }
    fn read_values(&self, mut read: impl FnMut(&str) -> Option<String>) -> Result<Environment> {
        self.validate()?;
        ensure!(
            cfg!(target_os = "linux"),
            "OpenShell Git execution is qualified on Linux only"
        );
        let mut token = None;
        for key in ["GITHUB_TOKEN", "GH_TOKEN"] {
            if let Some(value) = read(key).filter(|v| !v.is_empty()) {
                placeholder(&value)?;
                if token.is_none() {
                    token = Some(value)
                }
            }
        }
        let env = Environment {
            settings: self.clone(),
            reference: token.context("OpenShell did not supply a Git credential placeholder")?,
        };
        env.validate()?;
        Ok(env)
    }
    fn matches(&self, fields: &BTreeMap<String, String>) -> bool {
        if fields.get("protocol").map(String::as_str) != Some("https") {
            return false;
        }
        let Some(host) = fields.get("host") else {
            return false;
        };
        let authority = |h: &str| h.strip_suffix(":443").unwrap_or(h).to_ascii_lowercase();
        if authority(host) != authority(&self.host) {
            return false;
        }
        let Some(path) = fields.get("path") else {
            return false;
        };
        let path = path.strip_suffix(".git").unwrap_or(path);
        self.repositories.iter().any(|r| r == path)
    }
}
fn placeholder(value: &str) -> Result<()> {
    let rest = value
        .strip_prefix("openshell:resolve:env:")
        .context("Refusing real or unrecognized Git credential; OpenShell placeholder required")?;
    ensure!(
        !rest.is_empty()
            && value.len() <= 4096
            && rest
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b"_-:.".contains(&b))
            && (rest.ends_with("GITHUB_TOKEN") || rest.ends_with("GH_TOKEN")),
        "Invalid OpenShell Git placeholder"
    );
    Ok(())
}
/// Private local-manager payload. Never Debug/log this object or put it in a
/// tool response, saved Git configuration or telemetry.
#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Environment {
    settings: Settings,
    reference: String,
}
impl Environment {
    pub(crate) fn validate(&self) -> Result<()> {
        self.settings.validate()?;
        placeholder(&self.reference)
    }
    #[cfg(target_os = "linux")]
    pub(crate) fn apply(&self, command: &mut tokio::process::Command) -> Result<()> {
        self.validate()?;
        let exe = std::env::current_exe()?;
        ensure!(
            exe.is_absolute(),
            "Credential helper requires an installed executable"
        );
        let quote = |s: &str| format!("'{}'", s.replace('\'', "'\\''"));
        let helper = format!(
            "!exec {} git-credential-openshell",
            quote(exe.to_str().context("Non UTF-8 credential helper path")?)
        );
        command
            .env(
                "OPENWORKER_GIT_SETTINGS",
                serde_json::to_string(&self.settings)?,
            )
            .env("GITHUB_TOKEN", &self.reference)
            .env("GIT_CONFIG_NOSYSTEM", "1")
            .env("GIT_CONFIG_GLOBAL", "/dev/null")
            .env("GIT_TERMINAL_PROMPT", "0")
            .env("GIT_CONFIG_COUNT", "3")
            // Empty helper resets inherited repository/global helper lists.
            .env("GIT_CONFIG_KEY_0", "credential.helper")
            .env("GIT_CONFIG_VALUE_0", "")
            .env("GIT_CONFIG_KEY_1", "credential.helper")
            .env("GIT_CONFIG_VALUE_1", helper)
            .env("GIT_CONFIG_KEY_2", "credential.useHttpPath")
            .env("GIT_CONFIG_VALUE_2", "true");
        Ok(())
    }
}
/// Git invokes this private helper via pipes. It returns an opaque reference
/// only to an exact configured repository. store/erase never persist anything.
pub async fn credential_helper(action: &str) -> Result<()> {
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    if matches!(action, "store" | "erase") {
        return Ok(());
    }
    ensure!(action == "get", "Unsupported Git credential operation");
    let mut input = vec![];
    tokio::io::stdin()
        .take(8193)
        .read_to_end(&mut input)
        .await?;
    ensure!(input.len() <= 8192, "Git credential request too large");
    let raw = std::env::var("OPENWORKER_GIT_SETTINGS").context("Git credential scope missing")?;
    ensure!(raw.len() <= 4096, "Git credential scope too large");
    let settings: Settings = serde_json::from_str(&raw)?;
    settings.validate()?;
    let fields = parse(&input)?;
    if !settings.matches(&fields) {
        return Ok(());
    }
    let env = settings.environment()?;
    tokio::io::stdout()
        .write_all(format!("username=x-access-token\npassword={}\n\n", env.reference).as_bytes())
        .await?;
    Ok(())
}
fn parse(input: &[u8]) -> Result<BTreeMap<String, String>> {
    let mut fields = BTreeMap::new();
    for line in std::str::from_utf8(input)?
        .lines()
        .take_while(|l| !l.is_empty())
    {
        let (key, value) = line
            .split_once('=')
            .context("Invalid Git credential request")?;
        ensure!(
            !value.chars().any(char::is_control)
                && fields.insert(key.into(), value.into()).is_none(),
            "Duplicate or invalid Git credential field"
        );
    }
    Ok(fields)
}
#[cfg(test)]
mod tests {
    use super::*;
    fn settings() -> Settings {
        Settings {
            host: "github.com".into(),
            repositories: vec!["org/project".into()],
        }
    }
    #[test]
    fn only_exact_https_repository_receives_a_reference() {
        let s = settings();
        s.validate().unwrap();
        for host in ["github.com", "github.com:443", "GITHUB.COM"] {
            assert!(s.matches(
                &parse(format!("protocol=https\nhost={host}\npath=org/project.git\n\n").as_bytes())
                    .unwrap()
            ));
        }
        for request in [
            "protocol=http\nhost=github.com\npath=org/project.git",
            "protocol=https\nhost=github.com.evil.test\npath=org/project.git",
            "protocol=https\nhost=github.com\npath=org/other.git",
            "protocol=https\nhost=github.com\npath=org/project.git/extra",
            "protocol=https\nhost=github.com\npath=org%2fproject.git",
        ] {
            assert!(!s.matches(&parse(request.as_bytes()).unwrap()));
        }
        assert!(parse(b"host=github.com\nhost=evil.test\n").is_err());
    }
    #[test]
    fn malformed_authorities_paths_and_raw_tokens_fail_closed() {
        for host in [
            "github.com/path",
            "user@github.com",
            "github.com?key=token",
            "*.github.com",
            "github.com\n",
        ] {
            let mut s = settings();
            s.host = host.into();
            assert!(s.validate().is_err());
        }
        for path in [
            "../project",
            "org/../project",
            "org/project.git",
            "org/project;echo",
            "org/project%2fother",
        ] {
            let mut s = settings();
            s.repositories = vec![path.into()];
            assert!(s.validate().is_err());
        }
        for value in [
            "ghp-real-secret",
            "openshell:resolve:env:",
            "openshell:resolve:env:v1_GITHUB_TOKEN\n",
            "openshell:resolve:env:v1_OTHER_KEY",
        ] {
            assert!(placeholder(value).is_err());
        }
        assert!(placeholder("openshell:resolve:env:v1_handle_GITHUB_TOKEN").is_ok());
        assert!(placeholder("openshell:resolve:env:s_handle_GH_TOKEN").is_ok());
    }
    #[cfg(target_os = "linux")]
    #[test]
    fn explicit_opt_in_requires_current_placeholders_and_never_falls_back_to_raw_key() {
        let s = settings();
        assert!(s.read_values(|_| None).is_err());
        assert!(s.read_values(|_| Some("ghp-real-secret".into())).is_err());
        let valid = "openshell:resolve:env:v1_handle_GITHUB_TOKEN";
        assert!(s
            .read_values(|key| if key == "GITHUB_TOKEN" {
                Some(valid.into())
            } else {
                Some("ghp-real-other".into())
            })
            .is_err());
        let e = s
            .read_values(|key| (key == "GITHUB_TOKEN").then(|| valid.into()))
            .unwrap();
        let mut command = tokio::process::Command::new("/bin/true");
        e.apply(&mut command).unwrap();
        let env: std::collections::BTreeMap<_, _> = command
            .as_std()
            .get_envs()
            .map(|(k, v)| {
                (
                    k.to_string_lossy().into_owned(),
                    v.map(|v| v.to_string_lossy().into_owned()),
                )
            })
            .collect();
        assert_eq!(env["GITHUB_TOKEN"].as_deref(), Some(valid));
        assert_eq!(env["GIT_TERMINAL_PROMPT"].as_deref(), Some("0"));
        assert!(!env["GIT_CONFIG_VALUE_1"].as_ref().unwrap().contains(valid));
    }
}
