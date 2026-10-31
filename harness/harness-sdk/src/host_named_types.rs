//! Scoped definitions are compiled privately, never inserted into the app catalog.
//! Restart reconstructs the same runtime ID/bundle from the approved receipt.
use super::*;
use openworker_runtime::{
    agent_team::Document,
    store::Session,
    team_definition::{Delegation, Placement},
    team_type_catalog::Status,
};
pub(super) fn scoped_agent(
    lead: &Session,
    record: &openworker_runtime::team_type_catalog::Record,
    source: &crate::agents::Agent,
) -> Result<crate::agents::Agent> {
    ensure!(
        source.approvals.mode() == lead.permissions.mode,
        "Restore the original lead approval configuration before compiling its custom types"
    );
    let definition = record.binding.definition();
    let mut agent = source.clone();
    agent.id = record.runtime_id();
    agent.name = Some(definition.name.clone());
    agent.instructions = definition.instructions.clone();
    agent.description.clear();
    agent.tools = definition.tools.iter().cloned().collect();
    agent.subagents.clear();
    agent.planning = false;
    agent.memory = false;
    agent.memory_scope = None;
    agent.scheduling = false;
    agent.auto_skip_questions = lead.permissions.auto_skip_questions;
    agent.model = definition.model.clone().or_else(|| {
        lead.model_selection
            .as_ref()
            .map(|m| openworker_runtime::connections::Choice::new(&m.connection, &m.model))
    });
    if let Some(m) = &lead.reviewer_selection {
        agent.approvals = crate::agents::Approvals::Auto(
            openworker_runtime::connections::Choice::new(&m.connection, &m.model),
        );
    }
    agent.fallback_model = lead.model.clone();
    agent.provider = lead.model_provider;
    Ok(agent)
}
impl Host {
    pub(super) fn validate_named_delegation(
        &self,
        lead: &Session,
        parameters: &Value,
    ) -> Result<()> {
        let Some(value) = parameters.get("delegation").filter(|v| !v.is_null()) else {
            return Ok(());
        };
        let delegation: Delegation = serde_json::from_value(value.clone())?;
        delegation.bind(lead)?;
        for model in &delegation.models {
            self.connections.resolve(model)?;
        }
        // Fail closed until the placement adapter is qualified. These are not
        // alternate host paths or a silent fallback from a requested sandbox.
        ensure!(
            delegation.placements == std::collections::BTreeSet::from([Placement::Team])
                && delegation.profiles.is_empty(),
            "This adapter currently supports custom types only in the team's existing environment"
        );
        ensure!(
            self.registry
                .read()
                .unwrap()
                .definitions
                .contains_key(&lead.coworker),
            "Custom types require an SDK agent definition as their trusted template"
        );
        let source = self.registry.read().unwrap().definitions[&lead.coworker].clone();
        let mut registry = self.tools.read().unwrap().clone();
        if let Some(path) = &source.servers {
            // MCP launcher configuration is inherited only from the trusted app.
            openworker_runtime::servers::load(
                path,
                &self.config.state_dir.join("agents").join(&lead.coworker),
                &mut registry,
            )?;
        }
        for tool in &delegation.tools {
            let (namespace, _) = openworker_tools::qualified_parts(tool)?;
            ensure!(
                registry.native(tool).is_some()
                    || registry.mcp(namespace).is_some()
                    || registry.http(namespace).is_some(),
                "Delegated tool is not in the trusted registry: {tool}"
            );
        }
        Ok(())
    }
    pub(super) async fn refresh_named_types(&self, sessions: &[Session]) -> Result<()> {
        for lead in sessions.iter().filter(|s| s.agent_team.is_some()) {
            let doc: Document = serde_json::from_value(self.store.lock().unwrap().agent_team(
                &lead.id,
                "snapshot",
                &json!({}),
                None,
            )?)?;
            if doc
                .roster
                .instance(&doc.lead)
                .is_none_or(|i| i.session_id != lead.id)
            {
                continue;
            }
            let Some(catalog) = &doc.type_catalog else {
                continue;
            };
            for record in catalog
                .records
                .values()
                .flatten()
                .filter(|r| r.status == Status::Approved)
            {
                record
                    .binding
                    .revalidate(&catalog.ceiling, &catalog.prebuilt)?;
                let id = record.runtime_id();
                {
                    let registry = self.registry.read().unwrap();
                    if registry.private_profiles.contains(&id) {
                        continue;
                    }
                    ensure!(
                        !registry.profiles.contains_key(&id),
                        "Scoped runtime identity collides with an application profile"
                    );
                    ensure!(
                        registry.private_profiles.len() < 128,
                        "Scoped runtime capacity reached"
                    );
                }
                let source = self
                    .registry
                    .read()
                    .unwrap()
                    .definitions
                    .get(&lead.coworker)
                    .cloned()
                    .context("Missing trusted scoped-type template")?;
                let agent = scoped_agent(lead, record, &source)?;
                // Approval mode/reviewer stays the app's lead configuration.
                let config = self.config.clone();
                let store = self.store.clone();
                let tools = self.tools.read().unwrap().clone();
                let telemetry = self.instrumentation.clone();
                let connections = self.connections.clone();
                let prepared = agent.clone();
                let (runtime, mut profile) = tokio::task::spawn_blocking(move || -> Result<_> {
                    let (configs, profiles) =
                        crate::agents::compile::compile(&config.state_dir, &[prepared])?;
                    let c = configs
                        .into_iter()
                        .next()
                        .context("Missing compiled type")?;
                    let mut registry = tools;
                    if let Some(path) = &c.servers {
                        openworker_runtime::servers::load(path, &c.bundle, &mut registry)?;
                    }
                    let bundle = openworker_runtime::bundle::Bundle::load_with_registry(
                        &c.bundle, &registry,
                    )?;
                    let mut runtime = Runtime::open_instrumented(
                        &config.state_dir.join("profiles").join(&bundle.id),
                        &c.bundle,
                        &config.runner,
                        Model::new(c.model, config.scripted).with_provider(c.provider),
                        config.sandbox,
                        c.tool_policy.as_deref(),
                        c.runtime_policy.as_deref(),
                        registry,
                        Some(Box::new(ScopedStore {
                            inner: store,
                            coworker: bundle.id.clone(),
                        })),
                        telemetry,
                    )?;
                    Arc::get_mut(&mut runtime)
                        .context("Scoped runtime already published")?
                        .connections = connections;
                    Ok((
                        runtime,
                        profiles
                            .get(&bundle.id)
                            .cloned()
                            .context("Missing scoped profile")?,
                    ))
                })
                .await
                .context("Scoped compilation task failed")??;
                profile.worker_only = true;
                self.forward_events(&runtime);
                let mut registry = self.registry.write().unwrap();
                registry.profiles.insert(id.clone(), runtime);
                registry.agents.insert(id.clone(), profile);
                registry.definitions.insert(id.clone(), agent);
                registry.private_profiles.insert(id);
            }
        }
        Ok(())
    }
}
