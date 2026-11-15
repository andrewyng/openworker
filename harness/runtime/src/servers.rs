//! Trusted startup configuration, separate from coworker tool selection.
use anyhow::{bail, Result};
use openworker_tools::{HttpServer, McpServer, ToolRegistry};
use serde::Deserialize;
use std::{collections::BTreeSet, path::Path};
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Config {
    version: u32,
    servers: Vec<Server>,
}
#[derive(Deserialize)]
#[serde(tag = "transport", rename_all = "snake_case", deny_unknown_fields)]
enum Server {
    Stdio {
        namespace: String,
        program: String,
        #[serde(default)]
        args: Vec<String>,
    },
    StreamableHttp {
        namespace: String,
        url: String,
    },
}
pub fn load(path: &Path, bundle: &Path, registry: &mut ToolRegistry) -> Result<()> {
    let mut resolved = registry.clone();
    crate::resource_policy::checked_path(path)?;
    let path = path.canonicalize()?;
    if path.starts_with(bundle.canonicalize()?) {
        bail!("servers.json must be app-owned, outside the coworker bundle");
    }
    let config: Config = serde_json::from_slice(&crate::bundle::read_limited(&path, 64000)?)?;
    if config.version != 1 || config.servers.is_empty() || config.servers.len() > 8 {
        bail!("Unsupported server configuration version/count");
    }
    let mut selected = BTreeSet::new();
    for server in config.servers {
        match server {
            Server::Stdio {
                namespace,
                program,
                args,
            } => {
                if !selected.insert(namespace.clone()) || program == "$NATIVE" || program == "$HTTP"
                {
                    bail!("Duplicate or reserved provider");
                }
                resolved.register_mcp(McpServer {
                    namespace,
                    program,
                    args,
                })?;
            }
            Server::StreamableHttp { namespace, url } => {
                if !selected.insert(namespace.clone()) {
                    bail!("Duplicate provider");
                }
                crate::http_mcp::validate_url(&url)?;
                resolved.register_http(HttpServer { namespace, url })?;
            }
        }
    }
    resolved.protect_config(path);
    *registry = resolved;
    Ok(())
}
