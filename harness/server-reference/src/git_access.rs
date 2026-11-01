//! Operator-owned OpenShell Git registry. No credential values or provider I/O.
use crate::Config;
use anyhow::{ensure, Context, Result};
use openworker_runtime::coding::openshell_git::{ProviderPin, Settings};
use openworker_store_postgres::service_catalog::Profile;
use serde::{Deserialize, Serialize};

#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Grant {
    pub provider: ProviderPin,
    pub scope: Settings,
}
#[derive(Debug)]
pub struct Unavailable;
impl std::fmt::Display for Unavailable {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("git_grant_unavailable")
    }
}
impl std::error::Error for Unavailable {}

#[cfg(all(test, unix))]
mod tests {
    use super::*;
    use openworker_runtime::environment::{Management, Sharing};
    use serde_json::json;
    pub(super) fn grant() -> Grant {
        serde_json::from_value(json!({"provider":{"name":"github","provider_id":"original-provider","profile_id":"github","profile_workspace":"","profile_digest":"a".repeat(64)},"scope":{"host":"github.com","repositories":["org/project"]}})).unwrap()
    }
    fn config() -> Config {
        let mut v: serde_json::Value =
            serde_json::from_str(include_str!("../config.example.json")).unwrap();
        v["openshell"]["image"] = json!(format!("runner@sha256:{}", "a".repeat(64)));
        serde_json::from_value(v).unwrap()
    }
    fn profile(git: Option<&str>) -> Profile {
        serde_json::from_value(json!({"id":"p@1","provider":"openshell","cpu":1,"memoryMB":512,"sharing":"shared","gitAccess":git})).unwrap()
    }
    #[test]
    fn installed_grants_are_explicit_and_scope_is_part_of_environment_identity() {
        let mut c = config();
        assert!(c.profile_config(&profile(Some("github@1"))).is_err());
        c.openshell.git_grants.insert("github@1".into(), grant());
        let p = c.profile_config(&profile(Some("github@1"))).unwrap();
        let with = p
            .provider()
            .unwrap()
            .spec(Sharing::Shared, Management::Harness);
        let without = p.profile_config(&profile(None)).unwrap();
        assert!(without.openshell.selected_git_grant.is_none());
        assert_ne!(
            with,
            without
                .provider()
                .unwrap()
                .spec(Sharing::Shared, Management::Harness)
        );
        let mut changed = p.clone();
        changed
            .openshell
            .git_grants
            .get_mut("github@1")
            .unwrap()
            .scope
            .repositories = vec!["org/another".into()];
        assert_ne!(
            with,
            changed
                .provider()
                .unwrap()
                .spec(Sharing::Shared, Management::Harness)
        );
        let mut wrong = p;
        wrong
            .openshell
            .git_grants
            .get_mut("github@1")
            .unwrap()
            .provider
            .profile_workspace = "another".into();
        assert!(wrong.provider().is_err());
    }
    #[test]
    fn sdk_profile_grants_and_delegation_are_exact_or_credential_free() {
        let p = profile(Some("github@1"));
        p.validate().unwrap();
        let g = p.delegated_grants();
        assert_eq!(g.network, ["git:github@1".into()].into());
        assert_eq!(g.credentials, g.network);
        assert!(profile(None).require_delegatable_from(&p).is_ok());
        assert!(p.require_delegatable_from(&p).is_ok());
        assert!(p.require_delegatable_from(&profile(None)).is_err());
        assert!(profile(Some("other@1"))
            .require_delegatable_from(&p)
            .is_err());
        assert!(profile(Some("unversioned")).validate().is_err());
        assert!(serde_json::to_value(profile(None))
            .unwrap()
            .get("gitAccess")
            .is_none());
    }
    #[test]
    fn secret_fields_and_config_selected_overrides_are_rejected() {
        let mut p = serde_json::to_value(profile(None)).unwrap();
        p["apiKey"] = json!("not-accepted");
        assert!(serde_json::from_value::<Profile>(p).is_err());
        let mut g = serde_json::to_value(grant()).unwrap();
        g["apiKey"] = json!("not-accepted");
        assert!(serde_json::from_value::<Grant>(g).is_err());
        let mut v: serde_json::Value =
            serde_json::from_str(include_str!("../config.example.json")).unwrap();
        v["openshell"]["selected_git_grant"] = json!("github@1");
        assert!(serde_json::from_value::<Config>(v).is_err());
    }
}
impl Config {
    pub(crate) fn validate_git_grants(&self) -> Result<()> {
        ensure!(
            self.openshell.git_grants.len() <= 16,
            "Too many installed Git grants"
        );
        for (name, grant) in &self.openshell.git_grants {
            openworker_store_postgres::service_catalog::name(name)?;
            let (id, version) = name
                .split_once('@')
                .context("Git grants require immutable versioned names")?;
            ensure!(
                !id.is_empty() && !version.is_empty() && !version.contains('@'),
                "Invalid Git grant name"
            );
            grant.provider.validate()?;
            grant.scope.validate()?;
            ensure!(
                grant.provider.profile_workspace.is_empty()
                    || grant.provider.profile_workspace == self.openshell.workspace,
                "Git grant profile belongs to another workspace"
            );
        }
        Ok(())
    }
    pub(crate) fn git_grant(&self) -> Result<Option<&Grant>> {
        self.validate_git_grants()?;
        self.openshell
            .selected_git_grant
            .as_ref()
            .map(|id| {
                self.openshell
                    .git_grants
                    .get(id)
                    .ok_or_else(|| anyhow::Error::new(Unavailable))
            })
            .transpose()
    }
    pub(crate) fn profile_config(&self, profile: &Profile) -> Result<Self> {
        profile.validate()?;
        let mut c = self.clone();
        c.openshell.cpu = Some(profile.cpu);
        c.openshell.memory_mb = Some(profile.memory_mb);
        // Always reset selection, including when a child chooses no Git access.
        c.openshell.selected_git_grant = profile.git_access.clone();
        c.git_grant()?;
        Ok(c)
    }
}
