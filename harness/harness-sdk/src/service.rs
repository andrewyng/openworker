//! Remote REST/SSE client. No worker ownership, agent loop or tool replay.
use futures_util::{Stream, StreamExt};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{
    pin::Pin,
    sync::{
        atomic::{AtomicUsize, Ordering},
        Arc,
    },
    time::Duration,
};
use tokio_util::sync::CancellationToken;
use zeroize::Zeroizing;

pub type Result<T> = std::result::Result<T, Error>;
#[derive(Debug)]
pub struct Error {
    pub code: &'static str,
    pub status: u16,
    pub idempotency_key: Option<String>,
}
impl std::fmt::Display for Error {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "OpenWorker service: {}", self.code)
    }
}
impl std::error::Error for Error {}
fn error(code: &'static str) -> Error {
    Error {
        code,
        status: 0,
        idempotency_key: None,
    }
}
pub type TokenFuture = Pin<Box<dyn std::future::Future<Output = Result<Zeroizing<String>>> + Send>>;
#[derive(Clone)]
pub enum Credential {
    Environment(String),
    Callback(Arc<dyn Fn(bool) -> TokenFuture + Send + Sync>),
}
impl Credential {
    async fn token(&self, rejected: bool) -> Result<Zeroizing<String>> {
        let token = match self {
            Self::Environment(name) => {
                Zeroizing::new(std::env::var(name).map_err(|_| error("credential_unavailable"))?)
            }
            Self::Callback(f) => f(rejected).await?,
        };
        if token.len() < 32 || token.len() > 4096 || !token.bytes().all(|b| b.is_ascii_graphic()) {
            return Err(error("credential_unavailable"));
        }
        Ok(token)
    }
}
#[derive(Clone)]
pub struct ConnectOptions {
    pub url: String,
    pub fallback_urls: Vec<String>,
    pub namespace: String,
    pub credentials: Credential,
    pub ca_pem: Option<Vec<u8>>,
}
impl ConnectOptions {
    pub fn new(
        url: impl Into<String>,
        namespace: impl Into<String>,
        token_env: impl Into<String>,
    ) -> Self {
        Self {
            url: url.into(),
            fallback_urls: vec![],
            namespace: namespace.into(),
            credentials: Credential::Environment(token_env.into()),
            ca_pem: None,
        }
    }
}
struct Inner {
    options: ConnectOptions,
    urls: Vec<String>,
    client: reqwest::Client,
    endpoint: AtomicUsize,
    stop: CancellationToken,
    capabilities: Value,
    identity: Value,
}
#[derive(Clone)]
pub struct Service(Arc<Inner>);
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SessionRef {
    pub service_id: String,
    pub namespace: String,
    pub session_id: String,
    pub agent: String,
    pub environment_id: String,
}
#[derive(Clone)]
pub struct Agent {
    service: Service,
    pub id: String,
}
#[derive(Clone)]
pub struct Session {
    service: Service,
    reference: SessionRef,
}
#[derive(Clone)]
pub struct Run {
    session: Session,
    pub id: String,
    pub submission_id: String,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RunResult {
    pub status: String,
    pub session_id: String,
    pub run_id: String,
    pub submission_id: String,
    pub cursor: i64,
    pub final_output: Option<String>,
    pub failure: Option<String>,
    pub interruptions: Vec<Value>,
}
pub type Events = Pin<Box<dyn Stream<Item = Result<openworker_runtime::store::Event>> + Send>>;
fn id(value: &str) -> Result<&str> {
    if value.is_empty()
        || value.len() > 256
        || !value
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"-_.@".contains(&b))
    {
        Err(error("invalid_identifier"))
    } else {
        Ok(value)
    }
}
pub fn endpoint(raw: &str) -> Result<String> {
    let u = reqwest::Url::parse(raw).map_err(|_| error("invalid_service_url"))?;
    let local = matches!(u.host_str(), Some("localhost" | "127.0.0.1" | "[::1]"));
    if (u.scheme() != "https" && !(u.scheme() == "http" && local))
        || !u.username().is_empty()
        || u.password().is_some()
        || u.query().is_some()
        || u.fragment().is_some()
    {
        return Err(error("invalid_service_url"));
    }
    Ok(u.to_string().trim_end_matches('/').into())
}
impl Service {
    pub async fn connect(options: ConnectOptions) -> Result<Self> {
        id(&options.namespace)?;
        let urls = std::iter::once(&options.url)
            .chain(&options.fallback_urls)
            .map(|u| endpoint(u))
            .collect::<Result<Vec<_>>>()?;
        if urls.len() > 8 {
            return Err(error("too_many_endpoints"));
        }
        let mut client = reqwest::Client::builder()
            .no_proxy()
            .redirect(reqwest::redirect::Policy::none())
            .connect_timeout(Duration::from_secs(10))
            .read_timeout(Duration::from_secs(45));
        if let Some(pem) = &options.ca_pem {
            client = client.add_root_certificate(
                reqwest::Certificate::from_pem(pem).map_err(|_| error("invalid_ca"))?,
            );
        }
        let mut service = Self(Arc::new(Inner {
            options,
            urls,
            client: client.build().map_err(|_| error("client_setup"))?,
            endpoint: AtomicUsize::new(0),
            stop: CancellationToken::new(),
            capabilities: Value::Null,
            identity: Value::Null,
        }));
        for i in 0..service.0.urls.len() {
            service.0.endpoint.store(i, Ordering::Relaxed);
            let cap = service.json("GET", "/v1/capabilities", None, None).await?;
            if cap["apiVersion"] != "1"
                || cap["namespace"] != service.0.options.namespace
                || !cap["serviceId"].is_string()
            {
                return Err(error("incompatible_service"));
            }
            if i == 0 {
                Arc::get_mut(&mut service.0).unwrap().capabilities = cap;
            } else if cap["serviceId"] != service.0.capabilities["serviceId"] {
                return Err(error("service_identity_mismatch"));
            }
        }
        service.0.endpoint.store(0, Ordering::Relaxed);
        let identity = service.json("GET", "/v1/auth/me", None, None).await?;
        Arc::get_mut(&mut service.0).unwrap().identity = identity;
        Ok(service)
    }
    pub fn capabilities(&self) -> &Value {
        &self.0.capabilities
    }
    pub fn identity(&self) -> &Value {
        &self.0.identity
    }
    pub fn disconnect(&self) {
        self.0.stop.cancel();
    }
    async fn request(
        &self,
        method: &str,
        path: &str,
        body: Option<Value>,
        key: Option<&str>,
        cursor: Option<i64>,
    ) -> Result<reqwest::Response> {
        if self.0.stop.is_cancelled() {
            return Err(error("disconnected"));
        }
        if let Some(k) = key {
            if k.is_empty() || k.len() > 100 || !k.bytes().all(|b| b.is_ascii_graphic()) {
                return Err(error("invalid_idempotency_key"));
            }
        }
        for attempt in 0..2 {
            let token = self.0.options.credentials.token(attempt > 0).await?;
            let index = self.0.endpoint.load(Ordering::Relaxed) % self.0.urls.len();
            let mut r = self
                .0
                .client
                .request(
                    method.parse().map_err(|_| error("invalid_method"))?,
                    format!("{}{}", self.0.urls[index], path),
                )
                .bearer_auth(&*token)
                .header("x-openworker-namespace", &self.0.options.namespace);
            if let Some(identity) = self.0.capabilities["serviceId"].as_str() {
                r = r.header("x-openworker-service-id", identity);
            }
            if let Some(body) = &body {
                r = r.json(body);
            }
            if let Some(key) = key {
                r = r.header("idempotency-key", key);
            }
            if let Some(c) = cursor {
                r = r.header("last-event-id", c);
            } else {
                r = r.timeout(Duration::from_secs(30));
            }
            let response = tokio::select! {_=self.0.stop.cancelled()=>return Err(error("disconnected")),r=r.send()=>r.map_err(|_|Error{code:if method=="GET"{"unavailable"}else{"outcome_unknown"},status:0,idempotency_key:key.map(String::from)})?};
            if response.status().as_u16() == 401
                && attempt == 0
                && matches!(self.0.options.credentials, Credential::Callback(_))
            {
                continue;
            }
            if !response.status().is_success() {
                return Err(Error {
                    code: match response.status().as_u16() {
                        401 => "unauthorized",
                        403 => "forbidden",
                        409 => "conflict",
                        _ => "request_rejected",
                    },
                    status: response.status().as_u16(),
                    idempotency_key: key.map(String::from),
                });
            }
            if response
                .headers()
                .get("x-openworker-namespace")
                .and_then(|v| v.to_str().ok())
                != Some(self.0.options.namespace.as_str())
            {
                return Err(error("namespace_mismatch"));
            }
            if let Some(identity) = self.0.capabilities["serviceId"].as_str() {
                if response
                    .headers()
                    .get("x-openworker-service-id")
                    .and_then(|v| v.to_str().ok())
                    != Some(identity)
                {
                    return Err(error("service_identity_mismatch"));
                }
            }
            return Ok(response);
        }
        Err(error("unauthorized"))
    }
    pub async fn json(
        &self,
        method: &str,
        path: &str,
        body: Option<Value>,
        key: Option<&str>,
    ) -> Result<Value> {
        let mut stream = self
            .request(method, path, body, key, None)
            .await?
            .bytes_stream();
        let mut bytes = vec![];
        loop {
            let next = tokio::select! {_=self.0.stop.cancelled()=>return Err(error("disconnected")),v=stream.next()=>v};
            let Some(chunk) = next else { break };
            let chunk = chunk.map_err(|_| Error {
                code: if method == "GET" {
                    "invalid_response"
                } else {
                    "outcome_unknown"
                },
                status: 0,
                idempotency_key: key.map(String::from),
            })?;
            if bytes.len() + chunk.len() > 1048576 {
                return Err(error("response_too_large"));
            }
            bytes.extend(chunk);
        }
        serde_json::from_slice(&bytes).map_err(|_| Error {
            code: if method == "GET" {
                "invalid_response"
            } else {
                "outcome_unknown"
            },
            status: 0,
            idempotency_key: key.map(String::from),
        })
    }
    pub async fn agent(&self, name: &str) -> Result<Agent> {
        let a = self
            .json("GET", &format!("/v1/agents/{}", id(name)?), None, None)
            .await?;
        if a["id"] != name {
            return Err(error("invalid_agent"));
        }
        Ok(Agent {
            service: self.clone(),
            id: name.into(),
        })
    }
    pub async fn resume(&self, reference: SessionRef) -> Result<Session> {
        if reference.service_id != self.0.capabilities["serviceId"]
            || reference.namespace != self.0.options.namespace
        {
            return Err(error("reference_identity_mismatch"));
        }
        let s = Session {
            service: self.clone(),
            reference,
        };
        let snap = s.snapshot().await?;
        if snap["session"]["environment"]["environment_id"] != s.reference.environment_id {
            return Err(error("environment_identity_mismatch"));
        }
        Ok(s)
    }
    pub fn events(&self, sid: String, mut after: i64) -> Events {
        let service = self.clone();
        Box::pin(async_stream::try_stream! {
            id(&sid)?;if after<0{Err(error("invalid_cursor"))?;}let mut failures=0;
            loop {
                if service.0.stop.is_cancelled(){break;}
                let response=service.request("GET",&format!("/v1/sessions/{sid}/events"),None,None,Some(after)).await;
                match response {
                    Ok(r)=>{
                        if !r.headers().get("content-type").and_then(|v|v.to_str().ok()).is_some_and(|s|s.contains("text/event-stream")){Err(error("invalid_event_stream"))?;}
                        let mut bytes=r.bytes_stream();let mut decoder=Sse::default();
                        loop{let chunk=tokio::select!{_=service.0.stop.cancelled()=>None,c=bytes.next()=>c};let Some(Ok(chunk))=chunk else{break};
                            let decoded=match decoder.push(&chunk){Ok(v)=>v,Err(e) if e.code=="stream_reconcile"=>break,Err(e)=>Err(e)?};for event in decoded{if event.session_id!=sid{Err(error("invalid_event"))?;}if event.event_id<=after{continue;}after=event.event_id;failures=0;yield event;}
                        }
                    }
                    Err(e) if e.code=="unavailable"=>{},
                    Err(e)=>Err(e)?,
                }
                if service.0.stop.is_cancelled(){break;}
                failures+=1;if failures>5{Err(error("reconnect_exhausted"))?;}
                service.0.endpoint.fetch_add(1,Ordering::Relaxed);
                tokio::select!{_=service.0.stop.cancelled()=>break,_=tokio::time::sleep(Duration::from_millis(200*failures))=>{}}
            }
        })
    }
}
impl Agent {
    pub async fn new_session(&self, environment_id: &str, key: Option<&str>) -> Result<Session> {
        let generated = openworker_runtime::id("idem");
        let value = self
            .service
            .json(
                "POST",
                "/v1/sessions",
                Some(json!({"agent":self.id,"environmentId":environment_id})),
                Some(key.unwrap_or(&generated)),
            )
            .await?;
        let reference = serde_json::from_value(value).map_err(|_| error("invalid_reference"))?;
        Ok(Session {
            service: self.service.clone(),
            reference,
        })
    }
}
impl Session {
    pub fn id(&self) -> &str {
        &self.reference.session_id
    }
    pub fn environment_id(&self) -> &str {
        &self.reference.environment_id
    }
    pub fn reference(&self) -> SessionRef {
        self.reference.clone()
    }
    pub async fn snapshot(&self) -> Result<Value> {
        self.service
            .json(
                "GET",
                &format!("/v1/sessions/{}", id(self.id())?),
                None,
                None,
            )
            .await
    }
    pub async fn send(&self, text: &str, key: Option<&str>) -> Result<Run> {
        let generated = openworker_runtime::id("idem");
        let r = self
            .service
            .json(
                "POST",
                &format!("/v1/sessions/{}/turns", id(self.id())?),
                Some(json!({"text":text})),
                Some(key.unwrap_or(&generated)),
            )
            .await?;
        Ok(Run {
            session: self.clone(),
            id: r["runId"]
                .as_str()
                .ok_or_else(|| error("invalid_receipt"))?
                .into(),
            submission_id: r["submissionId"]
                .as_str()
                .ok_or_else(|| error("invalid_receipt"))?
                .into(),
        })
    }
    pub async fn resume_run(&self, submission: &str) -> Result<Run> {
        let r = self
            .service
            .json(
                "GET",
                &format!("/v1/sessions/{}/turns/{}", id(self.id())?, id(submission)?),
                None,
                None,
            )
            .await?;
        Ok(Run {
            session: self.clone(),
            id: r["runId"]
                .as_str()
                .ok_or_else(|| error("invalid_receipt"))?
                .into(),
            submission_id: submission.into(),
        })
    }
    pub async fn control(&self, run: &str, action: Value, key: Option<&str>) -> Result<Value> {
        let generated = openworker_runtime::id("idem");
        self.service
            .json(
                "POST",
                &format!("/v1/sessions/{}/controls", id(self.id())?),
                Some(json!({"runId":run,"action":action})),
                Some(key.unwrap_or(&generated)),
            )
            .await
    }
    pub async fn control_receipt(&self, receipt: &str) -> Result<Value> {
        self.service
            .json(
                "GET",
                &format!("/v1/sessions/{}/controls/{}", id(self.id())?, id(receipt)?),
                None,
                None,
            )
            .await
    }
}
impl Run {
    pub async fn snapshot(&self) -> Result<RunResult> {
        serde_json::from_value(
            self.session
                .service
                .json(
                    "GET",
                    &format!(
                        "/v1/sessions/{}/turns/{}",
                        id(self.session.id())?,
                        id(&self.submission_id)?
                    ),
                    None,
                    None,
                )
                .await?,
        )
        .map_err(|_| error("invalid_result"))
    }
    pub fn events(&self, after: i64) -> Events {
        let run = self.clone();
        Box::pin(async_stream::try_stream! {
            let initial = run.snapshot().await?;
            if initial.status != "running" && after >= initial.cursor { return; }
            let mut events = run.session.service.events(run.session.id().into(), after);
            while let Some(event) = events.next().await {
                let event = event?;
                let cursor = event.event_id;
                if event.turn_id.as_deref() == Some(&run.id) {
                    // A worker claim/recovery can continue this same turn after
                    // applying a durable approval. Recovery alone is not completion.
                    let end = event.kind == "turn_completed" ||
                        (event.kind == "turn_recovered" && matches!(event.data["status"].as_str(),
                            Some("failed" | "cancelled" | "interrupted" | "truncated")));
                    let check = event.kind == "interaction_required";
                    yield event;
                    if end { break; }
                    if check && run.snapshot().await?.status == "interrupted" { break; }
                }
                if cursor >= initial.cursor && initial.status != "running" { break; }
            }
        })
    }
    pub async fn result(&self) -> Result<RunResult> {
        let state = self.snapshot().await?;
        if state.status != "running" {
            return Ok(state);
        }
        let mut events = self.events(state.cursor);
        while let Some(event) = events.next().await {
            event?;
        }
        self.snapshot().await
    }
}
#[derive(Default)]
pub struct Sse {
    pending: Vec<u8>,
    data: String,
    id: String,
    event: String,
    size: usize,
}
impl Sse {
    pub fn push(&mut self, chunk: &[u8]) -> Result<Vec<openworker_runtime::store::Event>> {
        self.pending.extend_from_slice(chunk);
        if self.pending.len() > 1048576 {
            return Err(error("event_too_large"));
        }
        let mut out = vec![];
        while let Some(n) = self.pending.iter().position(|b| *b == b'\n') {
            let bytes: Vec<_> = self.pending.drain(..=n).collect();
            let line = std::str::from_utf8(&bytes)
                .map_err(|_| error("invalid_event"))?
                .trim_end_matches(['\n', '\r']);
            self.size += line.len();
            if self.size > 1048576 {
                return Err(error("event_too_large"));
            }
            if line.is_empty() {
                if !self.data.is_empty() {
                    if self.event == "stream_error" {
                        return Err(Error {
                            code: "stream_reconcile",
                            status: 503,
                            idempotency_key: None,
                        });
                    }
                    if self.event != "progress" {
                        return Err(error("invalid_event"));
                    }
                    let e: openworker_runtime::store::Event =
                        serde_json::from_str(&self.data).map_err(|_| error("invalid_event"))?;
                    if self.id.parse::<i64>().ok() != Some(e.event_id) || e.event_id < 0 {
                        return Err(error("invalid_event"));
                    }
                    out.push(e);
                }
                self.data.clear();
                self.id.clear();
                self.event.clear();
                self.size = 0;
                continue;
            }
            if line.starts_with(':') {
                continue;
            }
            let (field, value) = line.split_once(':').unwrap_or((line, ""));
            let value = value.strip_prefix(' ').unwrap_or(value);
            match field {
                "data" => {
                    if !self.data.is_empty() {
                        self.data.push('\n');
                    }
                    self.data.push_str(value)
                }
                "id" => self.id = value.into(),
                "event" => self.event = value.into(),
                _ => {}
            }
        }
        Ok(out)
    }
}

/// Both the existing local-client constructor and remote options use Harness::connect.
pub trait ConnectTarget {
    type Connected;
    type Error;
    fn connect(
        self,
    ) -> Pin<
        Box<
            dyn std::future::Future<Output = std::result::Result<Self::Connected, Self::Error>>
                + Send,
        >,
    >;
}
impl ConnectTarget for ConnectOptions {
    type Connected = Service;
    type Error = Error;
    fn connect(self) -> Pin<Box<dyn std::future::Future<Output = Result<Service>> + Send>> {
        Box::pin(Service::connect(self))
    }
}
impl ConnectTarget for crate::HarnessClient {
    type Connected = crate::agents::Harness;
    type Error = crate::Error;
    fn connect(
        self,
    ) -> Pin<Box<dyn std::future::Future<Output = crate::Result<crate::agents::Harness>> + Send>>
    {
        Box::pin(crate::agents::Harness::connect_client(self))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn endpoints_require_tls_except_exact_loopback() {
        for value in [
            "http://remote.example",
            "https://user:secret@host",
            "https://host/?key=secret",
            "https://host/#fragment",
        ] {
            assert!(endpoint(value).is_err());
        }
        assert_eq!(
            endpoint("http://127.0.0.1:123/").unwrap(),
            "http://127.0.0.1:123"
        );
    }
    #[test]
    fn sse_decodes_bytewise_unicode_and_checks_cursor() {
        let mut decoder = Sse::default();
        let mut events = vec![];
        let wire = format!(
            ": ping\r\n\r\nevent: progress\r\nid: 1\r\ndata: {}\r\n\r\n",
            json!({"event_id":1,"session_id":"s","turn_id":"t","type":"text","data":{"text":"😀"}})
        );
        for byte in wire.bytes() {
            events.extend(decoder.push(&[byte]).unwrap());
        }
        assert_eq!(events.len(), 1);
        assert_eq!(events[0].data["text"], "😀");
        let invalid = wire.replace("id: 1", "id: 2");
        assert!(Sse::default().push(invalid.as_bytes()).is_err());
        assert!(Sse::default().push(&vec![b'x'; 1048577]).is_err());
    }
}
