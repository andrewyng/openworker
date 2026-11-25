//! Typed read-only service queries. Authorization remains on the service.
use super::{error, Result, Service};
use crate::recording::{Timeline, TurnSummary};
use crate::TraceQuery;
use serde::de::DeserializeOwned;
use serde_json::Value;

pub struct Observability<'a> {
    service: &'a Service,
}
impl Service {
    pub fn observability(&self) -> Observability<'_> {
        Observability { service: self }
    }
}
impl Observability<'_> {
    async fn read<T: DeserializeOwned>(&self, query: TraceQuery, kind: &str) -> Result<T> {
        query.validate().map_err(|_| error("InvalidInput"))?;
        let mut path = format!(
            "/v1/sessions/{}/observability/{}/{}",
            query.session_id, query.turn_id, kind
        );
        let mut params = Vec::new();
        if kind == "timeline" {
            params.push(format!("after={}&limit={}", query.after, query.limit));
        }
        if kind != "children" {
            if let Some(through) = query.through {
                params.push(format!("through={through}"));
            }
        }
        if !params.is_empty() {
            path.push('?');
            path.push_str(&params.join("&"));
        }
        let value = self.service.json("GET", &path, None, None).await?;
        serde_json::from_value(value).map_err(|_| error("InvalidResponse"))
    }
    pub async fn timeline(&self, query: TraceQuery) -> Result<Timeline> {
        self.read(query, "timeline").await
    }
    pub async fn summary(&self, query: TraceQuery) -> Result<TurnSummary> {
        self.read(query, "summary").await
    }
    pub async fn children(&self, query: TraceQuery) -> Result<Value> {
        self.read(query, "children").await
    }
    pub async fn subtree_summary(&self, query: TraceQuery) -> Result<Value> {
        self.read(query, "subtree-summary").await
    }
}
