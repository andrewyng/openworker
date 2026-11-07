//! Explicit operator-owned classification; never loaded from coworker/MCP content.
use crate::{
    bundle::{digest, read_limited, Bundle},
    permissions::{self, Category},
};
use anyhow::{bail, Result};
use serde::{Deserialize, Serialize};
use std::{
    collections::BTreeMap,
    path::{Path, PathBuf},
};

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct FilePolicy {
    version: u32,
    bundle_digest: String,
    overrides: Vec<Override>,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Override {
    server: String,
    tool: String,
    category: Category,
}

pub struct ToolPolicy {
    pub digest: String,
    path: Option<PathBuf>,
    overrides: BTreeMap<(String, String), Category>,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Classification {
    pub category: Category,
    pub base_category: Category,
    pub source: String,
}
#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct ResolvedPolicy {
    pub digest: String,
    pub tools: BTreeMap<String, Classification>,
}
impl ToolPolicy {
    pub fn path(&self) -> Option<&Path> {
        self.path.as_deref()
    }
    pub fn load(path: Option<&Path>, bundle: &Bundle) -> Result<Self> {
        let Some(path) = path else {
            return Ok(Self {
                digest: digest(b"no-user-tool-policy-v1"),
                path: None,
                overrides: BTreeMap::new(),
            });
        };
        #[cfg(windows)]
        crate::resource_policy::checked_path(path)?;
        let path = path.canonicalize()?;
        if path.starts_with(&bundle.root) {
            bail!("Tool policy must be outside the coworker bundle");
        }
        let bytes = read_limited(&path, 64000)?;
        let file: FilePolicy = serde_json::from_slice(&bytes)?;
        if file.version != 1 || file.bundle_digest != bundle.digest {
            bail!("Tool policy version or pinned bundle digest mismatch");
        }
        if file.overrides.len() > 64 {
            bail!("Too many tool policy overrides");
        }
        let mut overrides = BTreeMap::new();
        for rule in file.overrides {
            if !bundle
                .tools
                .bindings
                .values()
                .any(|b| b.server == rule.server && b.tool == rule.tool)
            {
                bail!("Policy targets an unbound server/tool");
            }
            if overrides
                .insert((rule.server, rule.tool), rule.category)
                .is_some()
            {
                bail!("Duplicate server/tool policy override");
            }
        }
        Ok(Self {
            digest: digest(&bytes),
            path: Some(path),
            overrides,
        })
    }
    pub fn verify(&self) -> Result<()> {
        if let Some(path) = &self.path {
            if digest(&read_limited(path, 64000)?) != self.digest {
                bail!("Tool policy changed; restart the runtime and create a new session");
            }
        }
        Ok(())
    }
    pub fn check_workspace(&self, workspace: &Path) -> Result<()> {
        let workspace = workspace.canonicalize()?;
        if self
            .path
            .as_ref()
            .is_some_and(|p| p.starts_with(&workspace))
        {
            bail!("Tool policy must not be inside the workspace");
        }
        Ok(())
    }
    pub fn resolved(&self, bundle: &Bundle) -> ResolvedPolicy {
        let tools = bundle
            .tools
            .bindings
            .iter()
            .map(|(alias, b)| {
                let base = permissions::classify(bundle, alias);
                let configured = self.overrides.get(&(b.server.clone(), b.tool.clone()));
                (
                    alias.clone(),
                    Classification {
                        category: configured.copied().unwrap_or(base),
                        base_category: base,
                        source: if configured.is_some() {
                            "host_policy"
                        } else if permissions::native_tool(bundle, alias).is_some() {
                            "native_builtin"
                        } else {
                            "unknown_external"
                        }
                        .into(),
                    },
                )
            })
            .collect();
        ResolvedPolicy {
            digest: self.digest.clone(),
            tools,
        }
    }
}
