use anyhow::{ensure, Result};
use serde::{Deserialize, Serialize};
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(tag = "transport", rename_all = "kebab-case", deny_unknown_fields)]
pub enum McpServer {
    StreamableHttp {
        name: String,
        url: String,
    },
    Stdio {
        name: String,
        program: String,
        #[serde(default)]
        args: Vec<String>,
    },
}
impl McpServer {
    pub fn streamable_http(name: impl Into<String>, url: impl Into<String>) -> Self {
        Self::StreamableHttp {
            name: name.into(),
            url: url.into(),
        }
    }
    pub fn stdio(name: impl Into<String>, program: impl Into<String>, args: Vec<String>) -> Self {
        Self::Stdio {
            name: name.into(),
            program: program.into(),
            args,
        }
    }
    pub(crate) fn name(&self) -> &str {
        match self {
            Self::StreamableHttp { name, .. } | Self::Stdio { name, .. } => name,
        }
    }
    pub(crate) fn register(&self, registry: &mut openworker_tools::ToolRegistry) -> Result<()> {
        match self {
            Self::StreamableHttp { name, url } => {
                openworker_runtime::http_mcp::validate_url(url)?;
                registry.register_http(openworker_tools::HttpServer {
                    namespace: name.clone(),
                    url: url.clone(),
                })
            }
            Self::Stdio {
                name,
                program,
                args,
            } => {
                ensure!(
                    std::path::Path::new(program).is_absolute() || program == "$OPENWORKER",
                    "MCP executable must be an absolute trusted path"
                );
                ensure!(
                    program.len() <= 4096
                        && args.len() <= 64
                        && args.iter().all(|a| a.len() <= 4096 && !a.contains('\0')),
                    "MCP launch configuration exceeds limits"
                );
                registry.register_mcp(openworker_tools::McpServer {
                    namespace: name.clone(),
                    program: program.clone(),
                    args: args.clone(),
                })
            }
        }
    }
}
