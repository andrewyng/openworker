//! Human-authored desired state. Secret values and resolved cloud observations
//! never belong in this document. Unknown options fail before any provider call.
use super::*;

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct Config {
    #[serde(default = "schema")]
    pub api_version: String,
    pub name: String,
    pub cloud: Cloud,
    pub nodes: Nodes,
    /// Compatibility with earlier experimental YAML only. New configs omit budget.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub budget: Option<Budget>,
    #[serde(default)]
    pub trial: Option<Trial>,
    #[serde(default)]
    pub models: BTreeMap<String, Secret>,
    #[serde(default)]
    pub advanced: Advanced,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[serde(rename = "managementUI")]
    pub management_ui: Option<super::super::local::features::ManagementUI>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub observability: Option<super::super::local::features::Observability>,
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct Trial {
    pub expires_after_hours: u16,
    pub delete_data: bool,
}
fn schema() -> String {
    "ows/v1alpha1".into()
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(tag = "provider", rename_all = "lowercase", deny_unknown_fields)]
pub(super) enum Cloud {
    #[serde(rename_all = "camelCase")]
    Aws {
        account_id: String,
        region: String,
        #[serde(default = "profile")]
        profile: String,
    },
    #[serde(rename_all = "camelCase")]
    Azure {
        subscription_id: String,
        region: String,
    },
}
fn profile() -> String {
    "default".into()
}
impl Cloud {
    pub fn provider(&self) -> &'static str {
        match self {
            Self::Aws { .. } => "aws",
            Self::Azure { .. } => "azure",
        }
    }
    pub fn account(&self) -> &str {
        match self {
            Self::Aws { account_id, .. } => account_id,
            Self::Azure {
                subscription_id, ..
            } => subscription_id,
        }
    }
    pub fn region(&self) -> &str {
        match self {
            Self::Aws { region, .. } | Self::Azure { region, .. } => region,
        }
    }
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct Nodes {
    pub count: u16,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub instance_type: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub requirements: Option<Requirements>,
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct Requirements {
    pub cpu: Range,
    #[serde(rename = "memoryGiB")]
    pub memory_gi_b: Range,
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub(super) struct Range {
    pub min: u32,
    pub max: u32,
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct Budget {
    #[serde(rename = "limitUSD", alias = "alertUSD")]
    pub alert_usd: u32,
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(crate) struct Secret {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub env: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub file: Option<PathBuf>,
}
#[derive(Clone, Debug, Default, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct Advanced {
    /// A reviewed release package can replace locally packaged repository assets.
    pub release: Option<PathBuf>,
    /// Legacy profile import. New YAML embeds these fields below.
    pub setup_profile: Option<PathBuf>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub infrastructure: Option<Value>,
    pub network_cidr: Option<String>,
    pub kubernetes_version: Option<String>,
    pub azure: Option<AzureOptions>,
    pub aws: Option<AwsOptions>,
}
#[derive(Clone, Debug, Default, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct AzureOptions {
    pub tenant_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub setup_object_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub setup_principal_type: Option<String>,
    pub resource_group: Option<String>,
    pub control_resource_group: Option<String>,
    #[serde(default)]
    pub admin_group_object_ids: Vec<String>,
    pub ssh_public_key_file: Option<PathBuf>,
    pub ssh_private_key_file: Option<PathBuf>,
}
#[derive(Clone, Debug, Default, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct AwsOptions {
    pub operator_arn: Option<String>,
}

pub(super) fn safe_name(s: &str) -> bool {
    !s.is_empty()
        && s.len() <= 63
        && s.as_bytes()[0].is_ascii_alphanumeric()
        && s.bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"_-".contains(&b))
}
impl Config {
    pub fn load(path: &Path, provider: &str) -> Result<Self> {
        let bytes = fs::read(path).context("Read deployment.yaml")?;
        ensure!(bytes.len() <= 64 * 1024, "deployment.yaml exceeds 64 KiB");
        // Serde's raw error may echo an inline credential; never relay input text.
        let mut config:Self=serde_yaml::from_slice(&bytes).map_err(|_|anyhow::anyhow!(
            "Invalid deployment.yaml schema. Check field names/types; credentials must use env or file references."))?;
        let base = path
            .canonicalize()?
            .parent()
            .context("Configuration directory missing")?
            .to_path_buf();
        let resolve = |p: &mut Option<PathBuf>| {
            if let Some(value) = p {
                if value.is_relative() {
                    *value = base.join(&*value);
                }
            }
        };
        resolve(&mut config.advanced.setup_profile);
        resolve(&mut config.advanced.release);
        if let Some(a) = &mut config.advanced.azure {
            resolve(&mut a.ssh_public_key_file);
            resolve(&mut a.ssh_private_key_file);
        }
        for reference in config.models.values_mut() {
            resolve(&mut reference.file);
        }
        config.validate(provider)?;
        Ok(config)
    }
    pub fn validate(&self, provider: &str) -> Result<()> {
        if let Some(ui) = &self.management_ui {
            ui.validate()?;
        }
        if let Some(obs) = &self.observability {
            obs.validate()?;
        }
        if let Some(t) = &self.trial {
            ensure!((1..=72).contains(&t.expires_after_hours)&&t.delete_data,"A disposable trial requires 1..72 hours and explicit deleteData:true; omit trial for persistent deployment");
        }
        ensure!(
            self.api_version == "ows/v1alpha1",
            "Unsupported deployment apiVersion"
        );
        ensure!(super::super::name_ok(&self.name),"Use a lowercase deployment name (3..30 characters), starting with a letter and ending with a letter or digit");
        ensure!(
            self.cloud.provider() == provider,
            "Cloud in deployment.yaml differs from the selected command"
        );
        ensure!(
            safe_name(self.cloud.region())
                && self
                    .cloud
                    .region()
                    .bytes()
                    .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-'),
            "Invalid cloud region"
        );
        match &self.cloud {
            Cloud::Aws {
                account_id,
                profile,
                ..
            } => ensure!(
                account_id.len() == 12
                    && account_id.bytes().all(|b| b.is_ascii_digit())
                    && safe_name(profile),
                "Use an exact AWS account ID and a named CLI profile"
            ),
            Cloud::Azure {
                subscription_id, ..
            } => ensure!(
                uuid::Uuid::parse_str(subscription_id)
                    .is_ok_and(|id| !id.is_nil() && id.to_string() == *subscription_id),
                "Use the exact lowercase Azure subscription UUID"
            ),
        }
        ensure!(
            (1..=16).contains(&self.nodes.count),
            "Fixed node count must be between 1 and 16; autoscaling is not supported yet"
        );
        ensure!(
            self.nodes.instance_type.is_some() != self.nodes.requirements.is_some(),
            "Choose exactly one of nodes.instanceType or nodes.requirements"
        );
        if let Some(size) = &self.nodes.instance_type {
            ensure!(
                !size.is_empty()
                    && size.len() <= 80
                    && size
                        .bytes()
                        .all(|b| b.is_ascii_alphanumeric() || b"._-".contains(&b)),
                "Invalid instanceType"
            );
        }
        if let Some(r) = &self.nodes.requirements {
            for range in [&r.cpu, &r.memory_gi_b] {
                ensure!(
                    range.min > 0 && range.min <= range.max && range.max <= 1024,
                    "Node size ranges must be positive, ordered and bounded"
                );
            }
        }
        if let Some(b) = &self.budget {
            ensure!((1..=100_000).contains(&b.alert_usd), "Legacy budget.limitUSD must be positive; omit it and use plan-only --max-estimated-cost-usd instead");
        }
        ensure!(
            self.advanced.setup_profile.is_none() || self.advanced.infrastructure.is_none(),
            "Choose inline advanced.infrastructure, not both it and a legacy setupProfile"
        );
        ensure!(
            self.advanced.azure.is_none() || provider == "azure",
            "Azure options cannot be used with AWS"
        );
        ensure!(
            self.advanced.aws.is_none() || provider == "aws",
            "AWS options cannot be used with Azure"
        );
        ensure!(
            provider == "aws" || self.advanced.network_cidr.is_none(),
            "Azure network CIDR override is not supported by the current fixed network template"
        );
        if let Some(a) = &self.advanced.azure {
            ensure!(
                a.admin_group_object_ids.len() <= 4
                    && a.admin_group_object_ids
                        .iter()
                        .all(|s| uuid::Uuid::parse_str(s)
                            .is_ok_and(|id| !id.is_nil() && id.to_string() == *s)),
                "Use at most four exact Entra administrator group IDs"
            );
            for name in [&a.resource_group, &a.control_resource_group]
                .into_iter()
                .flatten()
            {
                ensure!(safe_name(name), "Invalid Azure resource group name");
            }
            if let Some(id) = &a.tenant_id {
                ensure!(
                    uuid::Uuid::parse_str(id).is_ok_and(|v| !v.is_nil() && v.to_string() == *id),
                    "Invalid Azure tenant UUID"
                );
            }
        }
        for (name, key) in &self.models {
            ensure!(
                ["anthropic", "openai"].contains(&name.as_str()),
                "Unsupported model credential name"
            );
            ensure!(
                key.env.is_some() != key.file.is_some(),
                "Model credentials require exactly one env or file reference"
            );
            if let Some(env) = &key.env {
                ensure!(
                    !env.is_empty()
                        && env.len() <= 128
                        && env
                            .bytes()
                            .all(|b| b.is_ascii_uppercase() || b.is_ascii_digit() || b == b'_'),
                    "Use an uppercase environment variable name, not a credential value"
                );
            }
            if let Some(path) = &key.file {
                ensure!(
                    !path.as_os_str().is_empty(),
                    "Credential file path is empty"
                );
            }
        }
        Ok(())
    }
    pub fn digest(&self) -> Result<String> {
        Ok(hash(&serde_json::to_vec(self)?))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    pub(crate) fn yaml() -> &'static str {
        "name: example\ncloud:\n  provider: azure\n  subscriptionId: 11111111-2222-4333-8444-555555555555\n  region: westus2\nnodes:\n  count: 2\n  requirements:\n    cpu: {min: 4, max: 8}\n    memoryGiB: {min: 16, max: 32}\nbudget:\n  limitUSD: 100\n"
    }
    #[test]
    fn minimal_config_is_strict_and_contains_no_credentials() {
        let c: Config = serde_yaml::from_str(yaml()).unwrap();
        c.validate("azure").unwrap();
        assert!(c.validate("aws").is_err());
        assert!(serde_yaml::from_str::<Config>(
            &yaml().replace("count: 2", "count: 2\n  autoscaling: true")
        )
        .is_err());
        let mut v = serde_json::to_value(&c).unwrap();
        v["nodes"]["instanceType"] = json!("Standard_D4s_v5");
        assert!(serde_json::from_value::<Config>(v)
            .unwrap()
            .validate("azure")
            .is_err());
        let mut c = c;
        c.nodes.count = 0;
        assert!(c.validate("azure").is_err());
    }
    #[test]
    fn inline_secret_errors_do_not_echo_input() {
        let dir = tempfile::tempdir().unwrap();
        let p = dir.path().join("deployment.yaml");
        fs::write(
            &p,
            format!(
                "{}models:\n  openai:\n    apiKey: secret-test-value\n",
                yaml()
            ),
        )
        .unwrap();
        let error = Config::load(&p, "azure").unwrap_err().to_string();
        assert!(!error.contains("secret-test-value"));
        let mut c: Config = serde_yaml::from_str(yaml()).unwrap();
        c.models.insert(
            "openai".into(),
            Secret {
                env: Some("OPENAI_API_KEY".into()),
                file: None,
            },
        );
        c.validate("azure").unwrap();
    }
}

#[cfg(test)]
mod feature_config_tests {
    use super::*;
    #[test]
    fn cloud_settings_accept_independent_features_and_reject_unknown_inline_options() {
        let base = tests::yaml();
        for options in [
            "managementUI: {enabled: true}\n",
            "observability: {enabled: true, retentionDays: 14, databaseSecret: recording-db}\n",
        ] {
            let c: Config = serde_yaml::from_str(&format!("{base}{options}")).unwrap();
            c.validate("azure").unwrap();
        }
        let c: Config = serde_yaml::from_str(base).unwrap();
        let value = serde_json::to_value(c).unwrap();
        assert!(value.get("managementUI").is_none() && value.get("observability").is_none());
        assert!(serde_yaml::from_str::<Config>(&format!(
            "{base}observability: {{enabled: true, connection: secret}}\n"
        ))
        .is_err());
    }
}

#[cfg(test)]
mod reference_examples {
    use super::*;
    #[test]
    fn cloud_reference_files_match_cli_schema_and_account_profiles() {
        let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../deploy/service/examples");
        for provider in ["azure", "aws"] {
            let c = Config::load(&root.join(provider).join("deployment.yaml"), provider).unwrap();
            assert!(c.budget.is_none());
            assert!(c.trial.is_none());
            assert!(
                c.management_ui.as_ref().unwrap().enabled
                    && c.observability.as_ref().unwrap().enabled
            );
            assert!(c.advanced.setup_profile.is_none());
            let infrastructure = c.advanced.infrastructure.clone().unwrap();
            assert!(c.advanced.release.as_ref().unwrap().is_absolute());
            if provider == "azure" {
                let _: super::super::super::azure::Inputs =
                    serde_json::from_value(infrastructure).unwrap();
            } else {
                let _: super::super::super::infrastructure::Inputs =
                    serde_json::from_value(infrastructure).unwrap();
            }
        }
    }
}
