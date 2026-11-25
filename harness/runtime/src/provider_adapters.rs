//! Native adapters run on the host that owns the provider connection.
use crate::{
    connections::{Manager, Selection},
    model_api::Api,
    provider::Error,
};
use anyhow::{ensure, Context, Result};
use serde_json::Value;
use std::sync::{Arc, Mutex};
pub type Delivery<'a> = futures_util::future::BoxFuture<'a, Result<()>>;
pub trait Transport: Send + Sync {
    fn prepare(
        &self,
        body: &mut Value,
        endpoint: Endpoint<'_>,
        binding: Option<Binding>,
    ) -> Result<Box<dyn Attempt>>;
    fn notify<'a>(
        &'a self,
        manager: &'a Manager,
        selection: &'a Selection,
        event: crate::integrations::Event<'a>,
    ) -> Delivery<'a>;
}

pub struct Endpoint<'a> {
    /// Stable instance identity, supplied by the runtime (never model content).
    pub agent_context: Option<&'a AgentContext>,
    pub api: Api,
    pub url: &'a str,
    pub key: &'a str,
    pub client: &'a reqwest::Client,
}
#[derive(Clone, Debug, Default, serde::Serialize, serde::Deserialize)]
pub struct AttemptAnnotations {
    pub request_id: Option<String>,
    pub route_locus: Option<String>,
    pub response_model: Option<String>,
}
impl AttemptAnnotations {
    pub(crate) fn sanitized(&self) -> Self {
        let keep = |key, value: &Option<String>| {
            value
                .as_ref()
                .filter(|v| openworker_telemetry::export_attribute(key, v))
                .cloned()
        };
        Self {
            request_id: keep(
                openworker_telemetry::attributes::MODEL_REQUEST_ID,
                &self.request_id,
            ),
            route_locus: keep(
                openworker_telemetry::attributes::MODEL_ROUTE_LOCUS,
                &self.route_locus,
            ),
            response_model: keep(
                openworker_telemetry::attributes::RESPONSE_MODEL,
                &self.response_model,
            ),
        }
    }
}

pub trait Attempt: Send {
    /// Return identifiers only. Route and model fields are used after successful
    /// receipt validation and stream completion.
    fn annotations(&self) -> AttemptAnnotations {
        AttemptAnnotations::default()
    }
    fn ready(&self) -> bool;
    fn comment(&mut self, line: &str) -> Result<(), Error>;
    fn data(&mut self, event: &mut Value) -> Result<bool, Error>;
    fn finish(&mut self) -> Result<(), Error>;
}

/// Durable instance identities shared by inference and lifecycle integrations.
/// Names, definitions and conversation content are intentionally excluded.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct AgentContext {
    pub agent_id: String,
    pub parent_agent_id: Option<String>,
    pub root_agent_id: String,
}

impl AgentContext {
    pub(crate) fn resolve(
        store: &crate::store::SharedStore,
        session: &crate::store::Session,
    ) -> Result<Self> {
        let id = &session.id;
        let Some(member) = &session.agent_team else {
            return Ok(Self {
                agent_id: id.clone(),
                root_agent_id: id.clone(),
                parent_agent_id: None,
            });
        };
        let doc: crate::agent_team::Document = serde_json::from_value(
            store
                .lock()
                .unwrap()
                .agent_team(id, "snapshot", &serde_json::json!({}), None)?,
        )?;
        Self::from_team(id, member, &doc)
    }

    fn from_team(
        id: &str,
        member: &crate::agent_team::Membership,
        doc: &crate::agent_team::Document,
    ) -> Result<Self> {
        let lead = doc
            .roster
            .instance(&doc.lead)
            .context("Missing agent context root")?;
        let current = doc
            .roster
            .instance(&member.agent_id)
            .context("Missing agent context member")?;
        ensure!(
            doc.id == member.team_id && current.session_id == id,
            "Session agent context binding changed"
        );
        // Legacy/static team members have the team lead as parent. Dynamic
        // children retain their actual creator in durable spawn provenance.
        let parent = |instance: &crate::team_coordination::Instance| {
            doc.trace_origins.get(&instance.agent_id).map_or_else(
                || lead.session_id.clone(),
                |origin| origin.parent_session_id.clone(),
            )
        };
        let child = lead.session_id != id;
        let parent_agent_id = child.then(|| parent(current));
        let mut ancestor = current;
        let mut seen = std::collections::BTreeSet::new();
        while ancestor.session_id != lead.session_id {
            ensure!(
                seen.insert(ancestor.session_id.clone()),
                "Agent ancestry contains a cycle"
            );
            let parent_id = parent(ancestor);
            ancestor = doc
                .roster
                .instances()
                .find(|i| i.session_id == parent_id)
                .context("Agent ancestry references a missing parent")?;
        }
        Ok(Self {
            agent_id: id.to_owned(),
            parent_agent_id,
            root_agent_id: lead.session_id.clone(),
        })
    }
}

pub(crate) type AnnotationSlot = Arc<Mutex<AttemptAnnotations>>;
tokio::task_local! { pub(crate) static ATTEMPT_ANNOTATIONS: AnnotationSlot; }

pub(crate) fn track(attempt: Box<dyn Attempt>) -> Box<dyn Attempt> {
    let Ok(slot) = ATTEMPT_ANNOTATIONS.try_with(Arc::clone) else {
        return attempt;
    };
    slot.lock().unwrap().request_id = attempt.annotations().request_id;
    Box::new(TrackedAttempt { attempt, slot })
}

struct TrackedAttempt {
    attempt: Box<dyn Attempt>,
    slot: AnnotationSlot,
}
impl Attempt for TrackedAttempt {
    fn ready(&self) -> bool {
        self.attempt.ready()
    }
    fn comment(&mut self, line: &str) -> Result<(), Error> {
        self.attempt.comment(line)
    }
    fn data(&mut self, event: &mut Value) -> Result<bool, Error> {
        self.attempt.data(event)
    }
    fn finish(&mut self) -> Result<(), Error> {
        self.attempt.finish()?;
        let annotations = self.attempt.annotations();
        let mut saved = self.slot.lock().unwrap();
        if saved.request_id.is_none() {
            saved.request_id = annotations.request_id;
        }
        saved.route_locus = annotations.route_locus;
        saved.response_model = annotations.response_model;
        Ok(())
    }
}

#[derive(Clone)]
pub struct Binding {
    pub sid: String,
    pub seq: i64,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn agent_context_preserves_root_parent_and_recovery_without_names() {
        use serde_json::json;
        let mut doc: crate::agent_team::Document = serde_json::from_value(json!({
            "id":"team", "lead":"lead", "tasks":{}, "sequence":0, "commands":{},
            "budget":crate::budget::State::new(None, None).unwrap(), "limited":false,
            "charges":{}, "stopped":false,
            "roster": {
                "policy":{"communication":{"board":true,"directMessages":false},"max_instances":32},
                "spawn_types":["private-business-role"], "requests":{},
                "instances": {
                    "lead":{"agent_type":"private-business-role","agent_id":"lead","session_id":"s_root","phase":"ready"},
                    "child":{"agent_type":"private-business-role","agent_id":"child","session_id":"s_child","phase":"ready"},
                    "nested":{"agent_type":"private-business-role","agent_id":"nested","session_id":"s_nested","phase":"ready"}
                }
            },
            "trace_origins": {
                "child":{"parent_session_id":"s_root","parent_turn_id":"t1","parent_call_id":"c1"},
                "nested":{"parent_session_id":"s_child","parent_turn_id":"t2","parent_call_id":"c2"}
            }
        })).unwrap();
        let member = |agent: &str| {
            serde_json::from_value::<crate::agent_team::Membership>(
                json!({"team_id":"team","agent_id":agent}),
            )
            .unwrap()
        };
        for (name, parent) in [
            ("lead", None),
            ("child", Some("s_root")),
            ("nested", Some("s_child")),
        ] {
            let id = doc.roster.instance(name).unwrap().session_id.clone();
            let context = AgentContext::from_team(&id, &member(name), &doc).unwrap();
            assert_eq!(context.agent_id, id);
            assert_eq!(context.root_agent_id, "s_root");
            assert_eq!(context.parent_agent_id.as_deref(), parent);
            let recovered = serde_json::from_slice(&serde_json::to_vec(&doc).unwrap()).unwrap();
            assert_eq!(
                AgentContext::from_team(&id, &member(name), &recovered).unwrap(),
                context
            );
            assert!(!format!("{context:?}").contains("private-business-role"));
        }
        assert!(AgentContext::from_team("wrong", &member("child"), &doc).is_err());
        doc.trace_origins
            .get_mut("child")
            .unwrap()
            .parent_session_id = "s_nested".into();
        assert!(AgentContext::from_team("s_child", &member("child"), &doc).is_err());
        doc.trace_origins
            .get_mut("child")
            .unwrap()
            .parent_session_id = "missing".into();
        assert!(AgentContext::from_team("s_child", &member("child"), &doc).is_err());
    }

    struct ReceiptAttempt {
        valid: bool,
    }

    impl Attempt for ReceiptAttempt {
        fn annotations(&self) -> AttemptAnnotations {
            AttemptAnnotations {
                request_id: Some("request-7".into()),
                route_locus: Some("local".into()),
                response_model: Some("fixture".into()),
            }
        }

        fn ready(&self) -> bool {
            self.valid
        }

        fn comment(&mut self, _: &str) -> Result<(), Error> {
            Ok(())
        }

        fn data(&mut self, _: &mut Value) -> Result<bool, Error> {
            Ok(true)
        }

        fn finish(&mut self) -> Result<(), Error> {
            if self.valid {
                Ok(())
            } else {
                Err(Error::new(crate::provider::Failure::InvalidResponse))
            }
        }
    }

    #[tokio::test]
    async fn attempt_id_survives_drop_but_route_requires_valid_receipt() {
        for finish in [None, Some(false), Some(true)] {
            let annotations: AnnotationSlot = Default::default();
            ATTEMPT_ANNOTATIONS
                .scope(annotations.clone(), async {
                    let mut attempt = track(Box::new(ReceiptAttempt {
                        valid: finish == Some(true),
                    }));
                    {
                        let current = annotations.lock().unwrap();
                        assert_eq!(current.request_id.as_deref(), Some("request-7"));
                        assert!(current.route_locus.is_none());
                        assert!(current.response_model.is_none());
                    }
                    if let Some(valid) = finish {
                        assert_eq!(attempt.finish().is_ok(), valid);
                    }
                })
                .await;
            let result = annotations.lock().unwrap();
            assert_eq!(result.request_id.as_deref(), Some("request-7"));
            assert_eq!(
                result.route_locus.as_deref(),
                (finish == Some(true)).then_some("local")
            );
            assert_eq!(
                result.response_model.as_deref(),
                (finish == Some(true)).then_some("fixture")
            );
        }
    }
}
