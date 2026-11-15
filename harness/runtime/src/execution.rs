//! Execution location is separate from authorization. Off is never a fallback.
use anyhow::Result;
use serde::{Deserialize, Serialize};
use serde_json::Value;

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize, Deserialize, clap::ValueEnum)]
#[serde(rename_all = "lowercase")]
pub enum Backend {
    #[default]
    Openshell,
    Off,
}
impl Backend {
    #[allow(non_upper_case_globals)]
    pub const OpenShell: Self = Self::Openshell;
    pub fn name(self) -> &'static str {
        match self {
            Self::Openshell => "openshell",
            Self::Off => "off",
        }
    }
    pub fn enforcement(self) -> &'static str {
        match self {
            Self::Openshell => "full",
            Self::Off => "none",
        }
    }
    pub fn network(self) -> &'static str {
        match self {
            Self::Openshell => "deny-all",
            Self::Off => "host-access-unrestricted-by-harness",
        }
    }
}

pub enum Client {
    Remote(Box<crate::environment_client::RemoteClient>),
    Openshell(Box<crate::sandbox::Mcp>),
    Direct(Box<crate::direct::Mcp>),
    Http(Box<crate::http_mcp::Mcp>),
}
impl Client {
    pub async fn request(&mut self, method: &str, params: Value) -> Result<Value> {
        match self {
            Self::Remote(c) => c.request(method, params).await,
            Self::Openshell(c) => c.request(method, params).await,
            Self::Direct(c) => c.request(method, params).await,
            Self::Http(c) => c.request(method, params).await,
        }
    }
    pub async fn close(&mut self) -> Result<()> {
        match self {
            Self::Remote(c) => c.close().await,
            Self::Openshell(_) => Ok(()),
            Self::Direct(c) => c.close().await,
            Self::Http(c) => c.close().await,
        }
    }
}
