//! Native adapters run on the host that owns the provider connection.
use crate::{
    connections::{Manager, Selection},
    model_api::Api,
    provider::Error,
};
use anyhow::Result;
use serde_json::Value;
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
    pub api: Api,
    pub url: &'a str,
    pub key: &'a str,
    pub client: &'a reqwest::Client,
}
pub trait Attempt: Send {
    fn ready(&self) -> bool;
    fn comment(&mut self, line: &str) -> Result<(), Error>;
    fn data(&mut self, event: &mut Value) -> Result<bool, Error>;
    fn finish(&mut self) -> Result<(), Error>;
}

#[derive(Clone)]
pub struct Binding {
    pub sid: String,
    pub seq: i64,
}
