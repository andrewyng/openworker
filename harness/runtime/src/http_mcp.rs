//! Explicit local-service MCP client. No subprocess ownership, auth or redirects.
use anyhow::{bail, Context, Result};
use futures_util::StreamExt;
use serde_json::{json, Value};
use std::time::Duration;
pub fn validate_url(raw: &str) -> Result<reqwest::Url> {
    let u = reqwest::Url::parse(raw)?;
    if raw.len() > 4096
        || u.scheme() != "http"
        || !u.username().is_empty()
        || u.password().is_some()
        || u.query().is_some()
        || u.fragment().is_some()
        || !matches!(u.host_str(), Some("127.0.0.1" | "[::1]"))
    {
        bail!("Local MCP requires an explicit http://127.0.0.1 or http://[::1] endpoint without credentials, query or fragment");
    }
    if u.port_or_known_default() == Some(0) {
        bail!("Invalid local MCP port");
    }
    Ok(u)
}
pub struct Mcp {
    client: reqwest::Client,
    url: reqwest::Url,
    session: Option<String>,
    version: String,
    sequence: u64,
    failed: bool,
    active: Option<u64>,
}
impl Mcp {
    pub async fn connect(url: &str) -> Result<Self> {
        let mut m = Self {
            client: reqwest::Client::builder()
                .no_proxy()
                .redirect(reqwest::redirect::Policy::none())
                .connect_timeout(Duration::from_secs(5))
                .build()?,
            url: validate_url(url)?,
            session: None,
            version: "2025-11-25".into(),
            sequence: 0,
            failed: false,
            active: None,
        };
        let init=m.request("initialize",json!({"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"openworker-native","version":"0.1.0"}})).await?;
        if !["2025-11-25", "2025-06-18", "2025-03-26"]
            .contains(&init["protocolVersion"].as_str().unwrap_or(""))
        {
            bail!("Unsupported HTTP MCP version");
        }
        m.version = init["protocolVersion"].as_str().unwrap().into();
        tokio::time::timeout(
            Duration::from_secs(10),
            m.exchange(
                json!({"jsonrpc":"2.0","method":"notifications/initialized","params":{}}),
                None,
            ),
        )
        .await
        .context("MCP initialization timed out")??;
        Ok(m)
    }
    fn headers(&self, b: reqwest::RequestBuilder) -> reqwest::RequestBuilder {
        let b = b
            .header("Accept", "application/json, text/event-stream")
            .header("MCP-Protocol-Version", &self.version);
        if let Some(id) = &self.session {
            b.header("Mcp-Session-Id", id)
        } else {
            b
        }
    }
    pub async fn request(&mut self, method: &str, params: Value) -> Result<Value> {
        if self.failed {
            bail!("HTTP MCP connection is unusable; do not replay an uncertain action");
        }
        self.sequence += 1;
        let id = self.sequence;
        self.active = Some(id);
        let r = tokio::time::timeout(
            Duration::from_secs(75),
            self.exchange(
                json!({"jsonrpc":"2.0","id":id,"method":method,"params":params}),
                Some(id),
            ),
        )
        .await
        .context("HTTP MCP timed out; effects unknown; no automatic retry")
        .and_then(|r| r);
        if r.is_err() {
            self.failed = true;
        } else {
            self.active = None;
        }
        r
    }
    async fn exchange(&mut self, message: Value, id: Option<u64>) -> Result<Value> {
        if message.to_string().len() > crate::MAX_FRAME {
            bail!("MCP request too large");
        }
        let response = self
            .headers(self.client.post(self.url.clone()))
            .json(&message)
            .send()
            .await
            .context("Local MCP connection failed")?;
        if !response.status().is_success() {
            bail!("Local MCP returned HTTP {}; auth and redirects are unsupported; no automatic retry",response.status().as_u16());
        }
        if let Some(v) = response.headers().get("Mcp-Session-Id") {
            let v = v.to_str()?;
            if v.is_empty() || v.len() > 256 || !v.bytes().all(|c| (0x21..=0x7e).contains(&c)) {
                bail!("Invalid MCP session ID");
            }
            if self.session.as_ref().is_some_and(|old| old != v) {
                bail!("MCP session identity changed");
            }
            self.session = Some(v.into());
        }
        if id.is_none() {
            return Ok(Value::Null);
        }
        let event_stream = response
            .headers()
            .get("Content-Type")
            .and_then(|v| v.to_str().ok())
            .is_some_and(|s| s.split(';').next() == Some("text/event-stream"));
        if !event_stream
            && response
                .headers()
                .get("Content-Type")
                .and_then(|v| v.to_str().ok())
                .is_none_or(|s| s.split(';').next() != Some("application/json"))
        {
            bail!("Unsupported MCP response content type");
        }
        let mut body = response.bytes_stream();
        let mut buffer = Vec::new();
        let mut total = 0;
        while let Some(chunk) = body.next().await {
            let chunk = chunk?;
            total += chunk.len();
            if total > crate::MAX_FRAME {
                bail!("HTTP MCP response exceeds bound; effects unknown");
            }
            buffer.extend(chunk);
            if event_stream {
                // SSE accepts LF and CRLF. Decode only complete UTF-8 records.
                loop {
                    let split = buffer
                        .windows(2)
                        .position(|x| x == b"\n\n")
                        .map(|n| (n, 2))
                        .or_else(|| {
                            buffer
                                .windows(4)
                                .position(|x| x == b"\r\n\r\n")
                                .map(|n| (n, 4))
                        });
                    let Some((n, len)) = split else {
                        break;
                    };
                    let frame = buffer.drain(..n + len).collect::<Vec<_>>();
                    let text = std::str::from_utf8(&frame)?;
                    let data = text
                        .lines()
                        .filter_map(|l| {
                            l.strip_prefix("data:")
                                .map(|s| s.strip_prefix(' ').unwrap_or(s))
                        })
                        .collect::<Vec<_>>()
                        .join("\n");
                    if !data.is_empty() {
                        if let Some(result) =
                            response_value(serde_json::from_str(&data)?, id.unwrap())?
                        {
                            return Ok(result);
                        }
                    }
                }
            }
        }
        if event_stream {
            bail!("MCP stream ended without correlated response; no automatic retry");
        }
        response_value(serde_json::from_slice(&buffer)?, id.unwrap())?
            .context("MCP response missing")
    }
    pub async fn close(&mut self) -> Result<()> {
        self.failed = true;
        if let Some(id) = self.active.take() {
            // MCP 2025 cancellation is advisory. Never claim rollback or that an
            // externally owned action stopped merely because its HTTP stream closed.
            let _=tokio::time::timeout(Duration::from_secs(2),self.exchange(json!({"jsonrpc":"2.0","method":"notifications/cancelled","params":{"requestId":id,"reason":"Client stopped this request"}}),None)).await;
        }
        // Delete only this logical session, never stop/reconfigure the service.
        if self.session.is_some() {
            let r = tokio::time::timeout(
                Duration::from_secs(5),
                self.headers(self.client.delete(self.url.clone())).send(),
            )
            .await;
            self.session = None;
            match r {
                Ok(Ok(r))
                    if r.status().is_success() || matches!(r.status().as_u16(), 404 | 405) => {}
                _ => bail!("Local MCP session disconnect unconfirmed; service was not stopped"),
            }
        }
        Ok(())
    }
}
fn response_value(v: Value, id: u64) -> Result<Option<Value>> {
    if v["jsonrpc"] != "2.0" {
        bail!("Invalid MCP envelope");
    }
    if v.get("method").is_some() {
        if v.get("id").is_some() {
            bail!("Server-initiated HTTP MCP requests unsupported");
        }
        return Ok(None);
    }
    if v["id"] != id {
        bail!("MCP response correlation mismatch");
    }
    if v.get("error").is_some() {
        bail!("MCP returned an error; no automatic retry");
    }
    let result = v.get("result").context("MCP result missing")?;
    if result.to_string().len() > 256000 {
        bail!("MCP result exceeds limit; effects unknown");
    }
    Ok(Some(result.clone()))
}
