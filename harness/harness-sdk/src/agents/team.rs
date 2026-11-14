//! Typed team board operations, replacing stringly `team_command` calls.
//! The application acts as the trusted host with lead privileges.
use crate::{Error, HarnessClient, Result, TeamCommand};
use serde_json::{json, Value};

pub use openworker_runtime::teams::Status as ItemStatus;

/// A new work item.
#[derive(Clone, Debug, Default)]
pub struct ItemSpec {
    pub title: String,
    pub description: String,
    /// Observable acceptance criteria. Required.
    pub criteria: String,
    /// IDs of existing items that must be done first.
    pub dependencies: Vec<String>,
}
impl ItemSpec {
    pub fn new(title: impl Into<String>, criteria: impl Into<String>) -> Self {
        Self {
            title: title.into(),
            criteria: criteria.into(),
            ..Self::default()
        }
    }
    pub fn description(mut self, description: impl Into<String>) -> Self {
        self.description = description.into();
        self
    }
    pub fn depends_on(mut self, item_id: impl Into<String>) -> Self {
        self.dependencies.push(item_id.into());
        self
    }
}

#[derive(Clone)]
pub struct Team {
    client: HarnessClient,
    id: String,
}

impl Team {
    pub(crate) fn new(client: HarnessClient, id: String) -> Self {
        Self { client, id }
    }
    pub fn id(&self) -> &str {
        &self.id
    }
    /// Members and work items.
    pub async fn board(&self) -> Result<Value> {
        let snap = self.client.team_snapshot(&self.id).await?;
        Ok(snap.get("board").cloned().unwrap_or(snap))
    }
    /// Durable team events after `cursor`.
    pub async fn events(&self, after: i64) -> Result<Value> {
        self.client.team_events(&self.id, after).await
    }
    /// Create an open, unassigned item; returns it (with `id` and `revision`).
    pub async fn create_item(&self, spec: ItemSpec) -> Result<Value> {
        self.item(
            "team_create_item",
            json!({"title":spec.title,"description":spec.description,
                   "criteria":spec.criteria,"dependencies":spec.dependencies}),
        )
        .await
    }
    /// Assign an item to a worker session at the item's current revision.
    pub async fn assign(
        &self,
        item_id: &str,
        revision: i64,
        worker_session: &str,
    ) -> Result<Value> {
        self.item(
            "team_assign",
            json!({"item_id":item_id,"expected_revision":revision,"session_id":worker_session}),
        )
        .await
    }
    /// Move an item to `status` at its current revision.
    pub async fn transition(
        &self,
        item_id: &str,
        revision: i64,
        status: ItemStatus,
    ) -> Result<Value> {
        self.item(
            "team_transition",
            json!({"item_id":item_id,"expected_revision":revision,"status":status}),
        )
        .await
    }
    /// Post a message, optionally on an item and/or waking one member.
    pub async fn post_message(
        &self,
        body: &str,
        item_id: Option<&str>,
        recipient: Option<&str>,
    ) -> Result<Value> {
        self.run(
            "team_post_message",
            json!({"body":body,"item_id":item_id.unwrap_or(""),"recipient":recipient.unwrap_or("")}),
        )
        .await
    }
    /// Cancel queued deliveries and active member turns.
    pub async fn cancel(&self) -> Result<Value> {
        self.run("team_cancel", json!({})).await
    }

    async fn item(&self, operation: &str, arguments: Value) -> Result<Value> {
        let r = self.run(operation, arguments).await?;
        r["data"]["item"]
            .is_object()
            .then(|| r["data"]["item"].clone())
            .ok_or_else(|| Error::Remote(format!("{operation}: missing item in result")))
    }
    async fn run(&self, operation: &str, arguments: Value) -> Result<Value> {
        self.client
            .team_command(
                &self.id,
                TeamCommand {
                    request_id: openworker_runtime::id("cmd"),
                    operation: operation.into(),
                    arguments,
                },
            )
            .await
    }
}
