//! Transport selection is independent of a provider's display name.
use anyhow::{bail, Result};
use serde::{Deserialize, Serialize};

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum Api {
    Responses,
    ChatCompletions,
    Anthropic,
}
impl Api {
    pub fn kind(self) -> crate::provider::Kind {
        match self {
            Self::Anthropic => crate::provider::Kind::Anthropic,
            _ => crate::provider::Kind::Openai,
        }
    }
    pub fn default_url(self) -> &'static str {
        match self {
            Self::Anthropic => "https://api.anthropic.com/v1",
            _ => "https://api.openai.com/v1",
        }
    }
    pub fn path(self) -> &'static str {
        match self {
            Self::Responses => "responses",
            Self::ChatCompletions => "chat/completions",
            Self::Anthropic => "messages",
        }
    }
}

/// Never permits URL credentials, query secrets, redirects, or non-local HTTP.
pub fn base_url(value: &str) -> Result<String> {
    let url = reqwest::Url::parse(value).map_err(|_| anyhow::anyhow!("Invalid model endpoint"))?;
    let host = url.host_str().unwrap_or("");
    let loopback = host == "localhost"
        || host
            .trim_matches(['[', ']'])
            .parse::<std::net::IpAddr>()
            .is_ok_and(|ip| ip.is_loopback());
    if !url.username().is_empty()
        || url.password().is_some()
        || url.query().is_some()
        || url.fragment().is_some()
        || url.host().is_none()
        || !(url.scheme() == "https" || (url.scheme() == "http" && loopback))
        || value.len() > 2048
    {
        bail!("Model endpoint requires HTTPS (or loopback HTTP), without URL credentials, query or fragment");
    }
    Ok(url.as_str().trim_end_matches('/').into())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn validates_endpoints_without_echoing_secrets() {
        for url in [
            "http://127.0.0.1:11434/v1",
            "http://[::1]:8000/v1",
            "https://api.example.com/v1/",
        ] {
            assert!(base_url(url).is_ok());
        }
        for url in [
            "http://example.com/v1",
            "https://user:secret@example.com",
            "https://example.com?key=secret",
            "file:///tmp/model",
        ] {
            let error = base_url(url).unwrap_err().to_string();
            assert!(!error.contains("secret"));
        }
    }
}
