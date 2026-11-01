//! Operator-installed trusted Git capabilities. Registry values are non-secret;
//! registration/API nodes do not load the controller's credential environment.
use crate::Config;
use anyhow::{ensure, Context, Result};
use openworker_runtime::coding_handoff::publication::{Access, Catalog, Grant};
use openworker_store_postgres::{service_catalog::Profile, PostgresStore};
use std::collections::BTreeMap;
impl Config {
    #[cfg(feature = "trusted-git")]
    pub(crate) fn repository_broker(&self, grant: &Grant) -> Result<openworker_git_broker::Broker> {
        let broker = openworker_git_broker::Broker::new(
            &self.state_root.canonicalize()?.join("repository-broker"),
            grant.clone(),
        )?;
        #[cfg(feature = "trusted-git-test")]
        {
            let mut broker = broker;
            for (id, path) in &self.repository_test_remotes {
                if grant.repositories.contains_key(id) {
                    broker = broker.with_fixture(id, path)?;
                }
            }
            Ok(broker)
        }
        #[cfg(not(feature = "trusted-git-test"))]
        Ok(broker)
    }
    pub(crate) fn validate_repository_grants(&self) -> Result<()> {
        ensure!(
            self.repository_grants.is_empty() || cfg!(feature = "trusted-git"),
            "Repository grants require a service built with trusted-git support"
        );
        ensure!(
            self.repository_grants.len() <= 16,
            "Too many repository grants"
        );
        for (name, grant) in &self.repository_grants {
            Access::from_grant(name, grant)?;
        }
        Ok(())
    }
    pub(crate) fn repository_grant(&self, profile: &Profile) -> Result<Option<&Grant>> {
        self.validate_repository_grants()?;
        let Some(name) = &profile.repository_access else {
            return Ok(None);
        };
        let grant = self
            .repository_grants
            .get(name)
            .context("Selected repository grant is not installed")?;
        // Expiry is checked at broker I/O, independently of ordinary file tools.
        Ok(Some(grant))
    }
    pub(crate) fn repository_catalog(
        &self,
        store: &PostgresStore,
        lead: &Profile,
        delegation: Option<&openworker_runtime::team_definition::Delegation>,
    ) -> Result<Option<Catalog>> {
        self.validate_repository_grants()?;
        if self.repository_grant(lead)?.is_none() {
            return Ok(None);
        }
        let mut profiles = BTreeMap::new();
        let ids = std::iter::once(lead.id.clone()).chain(
            delegation
                .into_iter()
                .flat_map(|d| d.profiles.keys().cloned()),
        );
        for id in ids {
            let entry = store.catalog_get("profile", &id)?;
            ensure!(
                entry.activated,
                "Repository access profile must be activated"
            );
            let profile: Profile = serde_json::from_value(entry.definition)?;
            profile.require_delegatable_from(lead)?;
            if let Some(grant) = self.repository_grant(&profile)? {
                profiles.insert(
                    id,
                    Access::from_grant(profile.repository_access.as_deref().unwrap(), grant)?,
                );
            }
        }
        Ok((!profiles.is_empty()).then_some(Catalog {
            lead_profile: lead.id.clone(),
            profiles,
        }))
    }
}

impl Config {
    pub(crate) fn tool_registry(
        &self,
        store: Option<&PostgresStore>,
    ) -> Result<openworker_tools::ToolRegistry> {
        self.validate_repository_grants()?;
        let mut registry = self.registry.clone();
        if registry.mcp("coding").is_none()
            && registry.http("coding").is_none()
            && registry.native_catalog("coding").is_empty()
        {
            registry.register_mcp(openworker_tools::McpServer {
                namespace: "coding".into(),
                program: "$OPENWORKER".into(),
                args: vec![
                    "mcp-coding".into(),
                    "--workspace".into(),
                    "$WORKSPACE".into(),
                ],
            })?;
        }
        #[cfg(feature = "trusted-git")]
        if !self.repository_grants.is_empty() {
            registry.register(Publisher {
                store: store.cloned(),
            })?;
        }
        #[cfg(not(feature = "trusted-git"))]
        let _ = store;
        Ok(registry)
    }
    pub fn load_bundle(&self) -> Result<openworker_runtime::bundle::Bundle> {
        openworker_runtime::bundle::Bundle::load_with_registry(
            &self.bundle,
            &self.tool_registry(None)?,
        )
    }
}
#[cfg(feature = "trusted-git")]
struct Publisher {
    store: Option<PostgresStore>,
}
#[cfg(feature = "trusted-git")]
impl openworker_tools::Tool for Publisher {
    fn descriptor(&self) -> openworker_tools::ToolDescriptor {
        openworker_tools::ToolDescriptor{qualified_name:openworker_runtime::coding_handoff::publication::TOOL.into(),version:"1".into(),description:"Publish an immutable submitted coding revision through the configured trusted GitHub broker. Expected head null is create-only; existing refs require the exact old tip and fast-forward ancestry. No PR, merge, force overwrite or fallback credential mode. Returns an operation ID; inspect original publication status before any new action after connection loss.".into(),input_schema:serde_json::json!({"type":"object","properties":{"submission_id":{"type":"string"},"branch":{"type":"string"},"expected_head":{"type":["string","null"]}},"required":["submission_id","branch","expected_head"],"additionalProperties":false})}
    }
    fn call(
        &self,
        context: openworker_tools::ToolContext,
        arguments: serde_json::Value,
    ) -> openworker_tools::ToolFuture<'_> {
        Box::pin(async move {
            if context.cancellation.is_cancelled() {
                return Err(openworker_tools::ToolError::cancelled());
            }
            let store = self.store.as_ref().ok_or_else(|| {
                openworker_tools::ToolError::new(
                    "execution_unavailable",
                    "This API node cannot execute tools",
                )
            })?;
            store.queue_registered_publication(&context.session_id,&context.turn_id,&context.call_id,arguments).map(openworker_tools::ToolOutput::json).map_err(|_|openworker_tools::ToolError::new("publication_not_accepted","Publication intent was not acknowledged. Inspect original submission status before choosing a new action."))
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    #[test]
    fn grants_are_profile_owned_and_not_sandbox_cli_credentials() {
        let mut p:Profile=serde_json::from_value(json!({"id":"p@1","provider":"openshell","cpu":1,"memoryMB":512,"sharing":"shared","repositoryAccess":"github-publish@1"})).unwrap();
        p.validate().unwrap();
        assert!(p.delegated_grants().network.is_empty());
        assert!(p.delegated_grants().credentials.is_empty());
        assert!(p.require_delegatable_from(&p).is_ok());
        let lead = p.clone();
        p.repository_access = None;
        assert!(p.require_delegatable_from(&lead).is_ok());
        assert!(lead.require_delegatable_from(&p).is_err());
        p.repository_access = Some("different@1".into());
        assert!(p.require_delegatable_from(&lead).is_err());
    }
}
