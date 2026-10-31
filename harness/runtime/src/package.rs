//! Explicit native coworker profile, not implicit compatibility with every Python trait.
use anyhow::{bail, Context, Result};
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Manifest {
    #[serde(default = "one")]
    pub schema_version: u32,
    pub id: String,
    #[serde(default)]
    pub name: String,
    #[serde(default)]
    pub version: String,
    #[serde(default)]
    pub description: String,
    #[serde(default)]
    pub tagline: String,
    #[serde(default)]
    pub icon: String,
    #[serde(default = "yes")]
    pub requires_folder: bool,
    #[serde(default)]
    pub skills: Vec<String>,
    /// Opt-in session bookkeeping, not permission to execute planned actions.
    #[serde(default, skip_serializing_if = "std::ops::Not::not")]
    pub planning: bool,
    #[serde(default, skip_serializing_if = "std::ops::Not::not")]
    pub memory: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub memory_scope: Option<String>,
    #[serde(default, skip_serializing_if = "std::ops::Not::not")]
    pub scheduling: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub team_role: Option<crate::teams::Role>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub default_model: Option<crate::connections::Choice>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub limits: Option<crate::budget::Limits>,
}
fn one() -> u32 {
    1
}
fn yes() -> bool {
    true
}

/// Delimiters occupy complete lines. Markdown horizontal rules in the body are preserved.
pub fn frontmatter(text: &str) -> Result<(&str, &str)> {
    let first = text
        .find('\n')
        .context("Frontmatter opening line required")?;
    if text[..first].trim_end_matches('\r') != "---" {
        bail!("Expected opening --- line");
    }
    let start = first + 1;
    let mut offset = start;
    for line in text[start..].split_inclusive('\n') {
        if line.trim_end_matches(['\r', '\n']) == "---" {
            return Ok((&text[start..offset], text[offset + line.len()..].trim()));
        }
        offset += line.len();
    }
    bail!("Missing closing --- line")
}
pub fn parse(text: &str) -> Result<(Manifest, String)> {
    let (header, body) = frontmatter(text)?;
    let mut m: Manifest =
        serde_yaml::from_str(header).context("Invalid/unsupported native coworker manifest")?;
    if let Some(l) = &m.limits {
        l.validate()?;
    }
    if m.schema_version != 1 {
        bail!("Unsupported coworker schema_version; expected 1");
    }
    if let Some(scope) = &m.memory_scope {
        if !m.memory
            || scope.is_empty()
            || scope.len() > 128
            || !scope
                .bytes()
                .all(|c| c.is_ascii_alphanumeric() || b"-_:./".contains(&c))
        {
            bail!("memory_scope requires enabled memory and a bounded application-owned ASCII identifier");
        }
    }
    if !crate::bundle::safe_name(&m.id) || m.id.len() > 64 || body.is_empty() {
        bail!("Coworker requires a bounded safe ID and nonempty instructions");
    }
    if m.name.is_empty() {
        m.name = m.id.clone();
    }
    for (value, limit) in [
        (&m.name, 120),
        (&m.version, 80),
        (&m.description, 2000),
        (&m.tagline, 240),
        (&m.icon, 80),
    ] {
        if value.len() > limit || value.chars().any(char::is_control) {
            bail!("Invalid coworker display metadata");
        }
    }
    if !m.requires_folder {
        bail!("This native profile requires a workspace; requires_folder:false is unsupported");
    }
    if let Some(choice) = &m.default_model {
        choice.validate()?;
    }
    let mut seen = std::collections::BTreeSet::new();
    if m.skills.len() > 32
        || m.skills
            .iter()
            .any(|name| !crate::skills::valid_name(name) || !seen.insert(name))
    {
        bail!("Invalid or duplicate coworker skill selection");
    }
    Ok((m, body.into()))
}
