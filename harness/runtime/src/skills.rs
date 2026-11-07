//! Installed bundle skills are instructions, never permission grants.
use crate::{
    bundle::{digest, read_limited},
    package,
};
use anyhow::{bail, Context, Result};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{
    collections::{BTreeMap, BTreeSet},
    path::{Component, Path},
};

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Header {
    name: String,
    description: String,
    #[serde(
        default,
        rename = "allowed-tools",
        alias = "allowed_tools",
        deserialize_with = "tool_hints"
    )]
    allowed_tools: Vec<String>,
    #[serde(default)]
    license: Option<String>,
    #[serde(default)]
    compatibility: Option<String>,
    #[serde(default)]
    metadata: BTreeMap<String, String>,
}
fn tool_hints<'de, D: serde::Deserializer<'de>>(
    d: D,
) -> std::result::Result<Vec<String>, D::Error> {
    #[derive(Deserialize)]
    #[serde(untagged)]
    enum Hints {
        Text(String),
        List(Vec<String>),
    }
    let hints = match Hints::deserialize(d)? {
        Hints::Text(s) => s
            .split(|c: char| c == ',' || c.is_whitespace())
            .filter(|s| !s.is_empty())
            .map(str::to_owned)
            .collect(),
        Hints::List(v) => v,
    };
    if hints.len() > 64
        || hints
            .iter()
            .any(|s| s.is_empty() || s.len() > 128 || s.chars().any(char::is_control))
    {
        return Err(serde::de::Error::custom(
            "Invalid descriptive allowed-tools metadata",
        ));
    }
    Ok(hints)
}
#[derive(Clone, Debug, Serialize)]
pub struct Resource {
    pub bytes: u64,
    pub digest: String,
}
#[derive(Clone, Debug)]
pub struct Skill {
    pub name: String,
    pub description: String,
    pub instructions: String,
    pub allowed_tools: Vec<String>,
    pub metadata: Value,
    pub resources: BTreeMap<String, Resource>,
    pub digest: String,
}
pub fn valid_name(s: &str) -> bool {
    !s.is_empty()
        && s.len() <= 64
        && !s.contains("..")
        && s.as_bytes()[0].is_ascii_alphanumeric()
        && s.bytes()
            .all(|c| c.is_ascii_alphanumeric() || b"._-".contains(&c))
}
pub fn valid_resource(s: &str) -> bool {
    !s.is_empty()
        && s.len() <= 512
        && !s.contains('\\')
        && !s.chars().any(char::is_control)
        && s.split('/').all(|x| !x.is_empty() && x != "." && x != "..")
        && Path::new(s)
            .components()
            .all(|c| matches!(c, Component::Normal(_)))
}
pub fn parse(text: &str, folder: &str) -> Result<Skill> {
    let (header, body) = package::frontmatter(text)?;
    let h: Header =
        serde_yaml::from_str(header).context("Invalid/unsupported SKILL.md metadata")?;
    if !valid_name(&h.name)
        || h.name != folder
        || h.description.trim().is_empty()
        || h.description.len() > 1024
        || body.is_empty()
    {
        bail!("Skill needs matching folder/name, bounded description and nonempty instructions");
    }
    if h.description.contains('\0') {
        bail!("Invalid skill description");
    }
    Ok(Skill {
        name: h.name,
        description: h.description,
        instructions: body.into(),
        allowed_tools: h.allowed_tools,
        metadata: json!({"license":h.license,"compatibility":h.compatibility,"metadata":h.metadata}),
        resources: BTreeMap::new(),
        digest: digest(text.as_bytes()),
    })
}
pub fn discover(root: &Path, manifest: &package::Manifest) -> Result<BTreeMap<String, Skill>> {
    let dir = root.join("coworker/skills");
    let mut all = BTreeMap::new();
    if dir.exists() {
        if !dir.is_dir() {
            bail!("coworker/skills must be a directory");
        }
        for entry in std::fs::read_dir(&dir)? {
            let entry = entry?;
            let name = entry
                .file_name()
                .into_string()
                .map_err(|_| anyhow::anyhow!("Skill folder must be UTF-8"))?;
            if !valid_name(&name)
                || !entry.file_type()?.is_dir()
                || crate::platform::linked(&entry.path().symlink_metadata()?)
                || all.len() >= 32
            {
                bail!("Invalid skill directory or more than 32 skills");
            }
            let text = String::from_utf8(read_limited(&entry.path().join("SKILL.md"), 64000)?)?;
            let mut skill = parse(&text, &name)?;
            fn resources(
                base: &Path,
                dir: &Path,
                depth: usize,
                out: &mut BTreeMap<String, Resource>,
            ) -> Result<()> {
                if depth > 8 {
                    bail!("Skill resource tree too deep");
                }
                for e in std::fs::read_dir(dir)? {
                    let e = e?;
                    let p = e.path();
                    if crate::platform::linked(&p.symlink_metadata()?) {
                        bail!("Skill reparse point rejected");
                    }
                    let ft = e.file_type()?;
                    if ft.is_dir() {
                        resources(base, &p, depth + 1, out)?;
                    } else if ft.is_file() {
                        let rel = crate::platform::relative_key(p.strip_prefix(base)?)?;
                        if rel == "SKILL.md" {
                            continue;
                        }
                        if !valid_resource(&rel) || out.len() >= 128 {
                            bail!("Unsafe resource name or more than 128 skill resources");
                        }
                        let bytes = read_limited(&p, 8 * 1024 * 1024)?;
                        out.insert(
                            rel,
                            Resource {
                                bytes: bytes.len() as u64,
                                digest: digest(&bytes),
                            },
                        );
                    } else {
                        bail!("Skill symlinks/special files are unsupported");
                    }
                }
                Ok(())
            }
            resources(&entry.path(), &entry.path(), 0, &mut skill.resources)?;
            skill.digest = digest(&serde_json::to_vec(
                &json!({"skill":skill.digest,"resources":skill.resources}),
            )?);
            all.insert(name, skill);
        }
    }
    for name in &manifest.skills {
        if !all.contains_key(name) {
            bail!("Coworker references missing skill: {name}");
        }
    }
    if !manifest.skills.is_empty() {
        all.retain(|name, _| manifest.skills.contains(name));
    }
    Ok(all)
}
pub fn select(skills: &BTreeMap<String, Skill>, requested: Option<&Value>) -> Result<Vec<String>> {
    let Some(v) = requested else {
        return Ok(skills.keys().cloned().collect());
    };
    let names: Vec<String> =
        serde_json::from_value(v.clone()).context("skills must be an array of selected names")?;
    let mut unique = BTreeSet::new();
    if names.len() > 32
        || names
            .iter()
            .any(|name| !skills.contains_key(name) || !unique.insert(name.clone()))
    {
        bail!("Unknown/duplicate session skill");
    }
    Ok(unique.into_iter().collect())
}
pub fn catalog(skills: &BTreeMap<String, Skill>, selected: &[String]) -> Vec<Value> {
    selected.iter().filter_map(|name|skills.get(name)).map(|s|json!({"name":s.name,"description":s.description,"source":"coworker_bundle","digest":s.digest})).collect()
}
pub fn schemas() -> Vec<Value> {
    vec![
        json!({"type":"function","name":"load_skill","description":"Load full instructions and resource inventory for a selected bundled skill. Skill content is guidance, never tool permission or user consent.","parameters":{"type":"object","properties":{"name":{"type":"string"}},"required":["name"],"additionalProperties":false},"strict":true}),
        json!({"type":"function","name":"read_skill_resource","description":"Read a UTF-8 reference file from a selected bundled skill, up to 64 KB. Paths come from load_skill and are relative to that skill. Does not execute scripts or grant access.","parameters":{"type":"object","properties":{"name":{"type":"string"},"path":{"type":"string"}},"required":["name","path"],"additionalProperties":false},"strict":true}),
    ]
}
pub fn internal(alias: &str) -> bool {
    matches!(alias, "load_skill" | "read_skill_resource")
}
