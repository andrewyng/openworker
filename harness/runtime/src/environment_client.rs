//! One session/server attachment. Reconnects only for a subsequent explicit
//! request after persisting release; never replays the request that lost delivery.
use crate::{
    environment::{Binding, CloseOutcome, Connection, Provider},
    store::SharedStore,
};
use anyhow::{bail, Context, Result};
use serde_json::{json, Value};
use std::sync::Arc;

struct Reservation {
    store: SharedStore,
    sid: String,
    id: String,
    settled: bool,
}
impl Reservation {
    fn release(&mut self, unknown: bool) -> Result<()> {
        self.settled = true; // No retry after an uncertain database commit.
        self.store
            .lock()
            .unwrap()
            .settle_attachment(&self.sid, &self.id, true, unknown)
    }
}
impl Drop for Reservation {
    fn drop(&mut self) {
        if !self.settled {
            self.settled = true;
            if let Ok(mut db) = self.store.lock() {
                let _ = db.settle_attachment(&self.sid, &self.id, true, true);
            }
        }
    }
}
struct Live {
    reservation: Reservation,
    connection: Option<Box<dyn Connection>>,
}
pub struct RemoteClient {
    store: SharedStore,
    provider: Arc<dyn Provider>,
    binding: Binding,
    sid: String,
    server: String,
    argv: Vec<String>,
    live: Option<Live>,
    catalog: Option<Value>,
    busy: bool,
}
impl RemoteClient {
    pub(crate) fn new(
        store: SharedStore,
        provider: Arc<dyn Provider>,
        binding: Binding,
        sid: String,
        server: String,
        argv: Vec<String>,
    ) -> Self {
        Self {
            store,
            provider,
            binding,
            sid,
            server,
            argv,
            live: None,
            catalog: None,
            busy: false,
        }
    }
    async fn prepare(&mut self) -> Result<()> {
        self.provider.inspect(self.binding.clone()).await?;
        if self.live.is_some() {
            return Ok(());
        }
        let (reservation, fresh) = self
            .store
            .lock()
            .unwrap()
            .reserve_attachment(&self.sid, &self.server)?;
        if !fresh {
            bail!("Existing attachment must be reconciled before another launch");
        }
        self.live = Some(Live {
            reservation: Reservation {
                store: self.store.clone(),
                sid: self.sid.clone(),
                id: reservation.id.clone(),
                settled: false,
            },
            connection: None,
        });
        let connection = self
            .provider
            .attach(
                self.binding.clone(),
                reservation.id.clone(),
                self.argv.clone(),
            )
            .await?;
        self.live.as_mut().unwrap().connection = Some(connection);
        self.store
            .lock()
            .unwrap()
            .settle_attachment(&self.sid, &reservation.id, false, false)?;
        if let Some(expected) = &self.catalog {
            let observed = self
                .live
                .as_mut()
                .unwrap()
                .connection
                .as_mut()
                .unwrap()
                .request("tools/list", json!({}))
                .await?;
            if expected != &observed {
                bail!("Reconnected tool catalog changed; new session required");
            }
        }
        Ok(())
    }
    async fn release(&mut self, unknown: bool) -> Result<()> {
        if let Some(mut live) = self.live.take() {
            let outcome = match live.connection.take() {
                Some(c) => c.close().await.unwrap_or(CloseOutcome::Unknown),
                None => CloseOutcome::Unknown,
            };
            live.reservation
                .release(unknown || outcome == CloseOutcome::Unknown)?;
        }
        Ok(())
    }
    pub async fn request(&mut self, method: &str, params: Value) -> Result<Value> {
        if self.busy {
            bail!("Previous remote request was interrupted; close before reuse");
        }
        self.busy = true;
        if self.prepare().await.is_err() {
            self.release(true).await?;
            self.busy = false;
            if method == "tools/call" {
                return Ok(
                    json!({"isError":true,"content":[{"type":"text","text":json!({"type":"tool_not_dispatched","attachment_outcome_unknown":true,"message":"Original environment or tool connection unavailable/changed. No tool method was sent; do not switch environments or silently weaken policy."}).to_string()}]}),
                );
            }
            bail!("Original environment or tool connection unavailable/changed");
        }
        let result = self
            .live
            .as_mut()
            .context("No remote attachment")?
            .connection
            .as_mut()
            .context("Remote attachment not initialized")?
            .request(method, params)
            .await;
        match result {
            Ok(value) => {
                if method == "tools/list" {
                    self.catalog = Some(value.clone());
                }
                self.busy = false;
                Ok(value)
            }
            Err(_) => {
                self.release(true).await?;
                self.busy = false;
                if method == "tools/call" {
                    Ok(
                        json!({"isError":true,"content":[{"type":"text","text":json!({"type":"tool_outcome_unknown","may_still_be_running":true,"message":"No confirmed result after possible delivery. Inspect the original environment before deciding how to continue; this is not permission to retry."}).to_string()}]}),
                    )
                } else {
                    bail!("Remote MCP discovery failed")
                }
            }
        }
    }
    pub async fn close(&mut self) -> Result<()> {
        let unknown = self.busy;
        self.busy = true;
        self.release(unknown).await
    }
}
