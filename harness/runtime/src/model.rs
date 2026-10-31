use anyhow::{bail, Context, Result};
use serde_json::{json, Value};
use tokio::sync::mpsc;

#[derive(Clone)]
pub struct Model {
    pub provider: crate::provider::Kind,
    pub name: String,
    pub scripted: bool,
    pub observation: Option<CallContext>,
    pub connection: Option<(
        std::sync::Arc<crate::connections::Manager>,
        crate::connections::Selection,
    )>,
    #[cfg(test)]
    pub sample_hook: Option<SampleHook>,
    #[cfg(test)]
    pub summary_hook: Option<SummaryHook>,
    #[cfg(test)]
    pub review_hook: Option<SummaryHook>,
}
#[cfg(test)]
type TestFuture<T> = std::pin::Pin<Box<dyn std::future::Future<Output = Result<T>> + Send>>;
#[cfg(test)]
pub type SampleHook = std::sync::Arc<
    dyn Fn(String, Vec<Value>, mpsc::Sender<Progress>) -> TestFuture<()> + Send + Sync,
>;
#[cfg(test)]
pub type SummaryHook = std::sync::Arc<dyn Fn(String) -> TestFuture<String> + Send + Sync>;
pub enum Progress {
    Delta(String),
    /// Internal measurement only; never authorizes partial provider output.
    OutputStarted,
    /// Latest cumulative provider snapshot, not a token increment.
    Usage(crate::telemetry::TokenUsage),
    Complete(crate::provider::Completion),
}
#[derive(Clone)]
/// Explicit host binding for a model request's logical turn and purpose. The
/// runtime supplies this for main, auto-approver and compaction requests.
pub struct CallContext {
    pub telemetry: crate::telemetry::Telemetry,
    pub session: String,
    pub turn: String,
    pub action: String,
    pub purpose: crate::telemetry::ModelPurpose,
    pub attempt: u32,
}

impl Model {
    /// Explicit, bounded connectivity check. No tools, session or journal writes.
    pub async fn probe(&self) -> Result<()> {
        let (tx, mut rx) = mpsc::channel(16);
        let input = [json!({"role":"user","content":"Connection test."})];
        let request = self.request(
            "Reply briefly with OK. Do not call tools.",
            &input,
            &[],
            64,
            None,
            tx,
        );
        let collect = async {
            let mut completed = false;
            while let Some(progress) = rx.recv().await {
                if let Progress::Complete(result) = progress {
                    completed = !result.output.is_empty()
                        && result.output.iter().all(|v| v["type"] == "message");
                }
            }
            completed
        };
        let (result, completed) = tokio::time::timeout(std::time::Duration::from_secs(30), async {
            tokio::join!(request, collect)
        })
        .await
        .map_err(|_| anyhow::anyhow!("Provider connection test timed out"))?;
        result?;
        if !completed {
            bail!("Provider did not complete the connection test");
        }
        Ok(())
    }
    pub fn with_provider(mut self, provider: crate::provider::Kind) -> Self {
        self.provider = provider;
        self
    }
    pub fn new(name: String, scripted: bool) -> Self {
        Self {
            provider: crate::provider::Kind::Openai,
            name,
            scripted,
            observation: None,
            connection: None,
            #[cfg(test)]
            sample_hook: None,
            #[cfg(test)]
            summary_hook: None,
            #[cfg(test)]
            review_hook: None,
        }
    }
    pub async fn next_limited(
        &self,
        instructions: &str,
        history: &[Value],
        tools: &[Value],
        limit: u32,
        tx: mpsc::Sender<Progress>,
    ) -> Result<()> {
        if self.scripted {
            return self.next(instructions, history, tools, tx).await;
        }
        #[cfg(test)]
        if self.sample_hook.is_some() {
            return self.next(instructions, history, tools, tx).await;
        }
        self.request(instructions, history, tools, limit as usize, None, tx)
            .await
    }
    pub async fn next(
        &self,
        instructions: &str,
        history: &[Value],
        tools: &[Value],
        tx: mpsc::Sender<Progress>,
    ) -> Result<()> {
        #[cfg(test)]
        if let Some(hook) = &self.sample_hook {
            return self
                .observed(instructions, history, tx, |tx| {
                    hook(instructions.into(), history.to_vec(), tx)
                })
                .await;
        }
        if self.scripted {
            return self
                .observed(instructions, history, tx, |tx| self.fixture(history, tx))
                .await;
        }
        self.request(instructions, history, tools, 8192, None, tx)
            .await
    }
    async fn request(
        &self,
        instructions: &str,
        history: &[Value],
        tools: &[Value],
        max_tokens: usize,
        format: Option<Value>,
        tx: mpsc::Sender<Progress>,
    ) -> Result<()> {
        let tokens = match &self.connection {
            Some((manager, selection)) => manager.access_tokens(selection)?,
            None => None,
        };
        if let Some(tokens) = tokens {
            let auth_error =
                |_| crate::provider::Error::new(crate::provider::Failure::Authentication);
            let snapshot = tokens.get(None).await.map_err(auth_error)?;
            let result = self
                .request_with_key(
                    instructions,
                    history,
                    tools,
                    max_tokens,
                    format.clone(),
                    tx.clone(),
                    &snapshot.key,
                )
                .await;
            if result
                .as_ref()
                .err()
                .and_then(|e| e.downcast_ref::<crate::provider::Error>())
                .is_some_and(|e| {
                    e.http_status == Some(401) && e.kind == crate::provider::Failure::Authentication
                })
            {
                let next = tokens
                    .get(Some(snapshot.revision))
                    .await
                    .map_err(auth_error)?;
                return self
                    .request_with_key(
                        instructions,
                        history,
                        tools,
                        max_tokens,
                        format,
                        tx,
                        &next.key,
                    )
                    .await;
            }
            return result;
        }
        let provider = self
            .connection
            .as_ref()
            .map_or(self.provider, |(_, s)| s.provider);
        let key = match &self.connection {
            Some((manager, selection)) => manager.key(selection)?,
            None => zeroize::Zeroizing::new(
                std::env::var(provider.key_env())
                    .with_context(|| format!("{} is not configured", provider.key_env()))?,
            ),
        };
        self.request_with_key(instructions, history, tools, max_tokens, format, tx, &key)
            .await
    }
    #[allow(clippy::too_many_arguments)]
    async fn request_with_key(
        &self,
        instructions: &str,
        history: &[Value],
        tools: &[Value],
        max_tokens: usize,
        format: Option<Value>,
        tx: mpsc::Sender<Progress>,
        key: &str,
    ) -> Result<()> {
        self.observed(instructions, history, tx, |tx| {
            self.request_plain(instructions, history, tools, max_tokens, format, tx, key)
        })
        .await
    }
    pub fn accounted(mut self, context: CallContext) -> Self {
        self.observation = Some(context);
        self
    }
    async fn observed<F, Fut>(
        &self,
        instructions: &str,
        history: &[Value],
        tx: mpsc::Sender<Progress>,
        request: F,
    ) -> Result<()>
    where
        F: FnOnce(mpsc::Sender<Progress>) -> Fut,
        Fut: std::future::Future<Output = Result<()>>,
    {
        let Some(context) = self.observation.as_ref().filter(|c| c.telemetry.enabled()) else {
            return request(tx).await;
        };
        let mut span = context.telemetry.span(
            crate::telemetry::Operation::Model,
            Some(&context.session),
            Some(&context.action),
        );
        span.turn(&context.turn);
        span.purpose(context.purpose);
        span.attempt(context.attempt);
        span.metadata("gen_ai.operation.name", "chat");
        span.metadata("langfuse.observation.type", "generation");
        span.metadata("gen_ai.request.model", &self.name);
        span.model_started();
        span.content_tail("gen_ai.prompt",||serde_json::to_string(&json!({"instructions":instructions,"history":&history[history.len().saturating_sub(20)..]})).unwrap_or_default());
        let (inner, mut rx) = mpsc::channel(32);
        let future = request(inner);
        tokio::pin!(future);
        let mut finished = None;
        let mut closed = false;
        let mut completed = false;
        while finished.is_none() || !closed {
            let Some(progress) = (tokio::select! {
                result = &mut future, if finished.is_none() => { finished=Some(result); continue; },
                progress = rx.recv(), if !closed => progress,
            }) else {
                closed = true;
                continue;
            };
            match &progress {
                Progress::Delta(text) if !text.is_empty() => span.first_output(),
                Progress::OutputStarted => span.first_output(),
                Progress::Usage(usage) => span.reported_usage(*usage),
                Progress::Complete(completion) => {
                    if completed {
                        span.finish(crate::telemetry::Outcome::Error);
                        bail!("Duplicate provider completion");
                    }
                    completed = true;
                    if let Some(usage) = &completion.usage {
                        span.reported_usage(usage.reported());
                    }
                    if completion.output.iter().any(|item| {
                        item["type"] == "function_call"
                            || item["type"] == "message"
                                && item["content"].as_array().is_some_and(|parts| {
                                    parts.iter().any(|part| {
                                        part["text"].as_str().is_some_and(|text| !text.is_empty())
                                    })
                                })
                    }) {
                        span.first_output();
                    }
                    span.content("gen_ai.completion", || {
                        serde_json::to_string(&completion.output).unwrap_or_default()
                    });
                }
                _ => {}
            }
            if tx.send(progress).await.is_err() {
                span.finish(crate::telemetry::Outcome::Interrupted);
                bail!("Model consumer closed");
            }
        }
        let result = finished.unwrap().and_then(|()| {
            if completed {
                Ok(())
            } else {
                Err(anyhow::anyhow!("Missing provider completion"))
            }
        });
        span.finish(if result.is_ok() {
            crate::telemetry::Outcome::Ok
        } else {
            crate::telemetry::Outcome::Error
        });
        result
    }
    #[allow(clippy::too_many_arguments)]
    async fn request_plain(
        &self,
        instructions: &str,
        history: &[Value],
        tools: &[Value],
        max_tokens: usize,
        format: Option<Value>,
        tx: mpsc::Sender<Progress>,
        key: &str,
    ) -> Result<()> {
        let provider = self
            .connection
            .as_ref()
            .map_or(self.provider, |(_, s)| s.provider);
        let (api, endpoint) = match &self.connection {
            Some((manager, selection)) => manager.endpoint(selection)?,
            None => {
                let api = if provider == crate::provider::Kind::Anthropic {
                    crate::model_api::Api::Anthropic
                } else {
                    crate::model_api::Api::Responses
                };
                (api, format!("{}/{}", api.default_url(), api.path()))
            }
        };
        let client = reqwest::Client::builder()
            .no_proxy()
            .redirect(reqwest::redirect::Policy::none())
            .connect_timeout(std::time::Duration::from_secs(15))
            .timeout(std::time::Duration::from_secs(180))
            .build()?;
        if api == crate::model_api::Api::ChatCompletions {
            let body = crate::chat_completions::body(
                &self.name,
                instructions,
                history,
                tools,
                max_tokens,
                format.as_ref(),
            )?;
            crate::provider::request_decoded(
                &client,
                &endpoint,
                key,
                body,
                tx,
                crate::chat_completions::Decoder::default(),
            )
            .await?;
            return Ok(());
        }
        if api == crate::model_api::Api::Anthropic {
            let body = crate::anthropic::body(
                &self.name,
                instructions,
                history,
                tools,
                max_tokens,
                format.as_ref(),
            )?;
            crate::anthropic::request(&client, &endpoint, key, body, tx).await?;
            return Ok(());
        }
        if history.iter().any(|v| v["provider"] == "anthropic") {
            bail!("Provider-private history cannot be sent to another provider");
        }
        let input = history
            .iter()
            .cloned()
            .map(|mut v| {
                if let Some(o) = v.as_object_mut() {
                    o.remove("_runtime_notice");
                    o.remove("_team_delivery");
                }
                v
            })
            .collect::<Vec<_>>();
        let mut body = json!({"model":self.name,"instructions":instructions,"input":input,"tools":tools,"stream":true,"store":false,"include":["reasoning.encrypted_content"],"parallel_tool_calls":false,"max_output_tokens":max_tokens});
        if let Some(format) = format {
            body["text"] = json!({"format":format});
        }
        crate::provider::request(&client, &endpoint, key, body, tx).await?;
        Ok(())
    }
    pub async fn summarize(
        &self,
        instructions: &str,
        input: &str,
        max_tokens: usize,
    ) -> Result<String> {
        #[cfg(test)]
        if let Some(hook) = &self.summary_hook {
            return hook(input.into()).await;
        }
        if self.scripted {
            return Ok(crate::compaction::REFERENCE["cases"]
                .as_array()
                .unwrap()
                .iter()
                .find(|c| c["op"] == "quality" && c["expected"] == true)
                .unwrap()["text"]
                .as_str()
                .unwrap()
                .into());
        }
        self.text_response(instructions, input, max_tokens, None)
            .await
    }
    pub async fn summarize_with_usage(
        &self,
        instructions: &str,
        input: &str,
        max_tokens: usize,
    ) -> Result<(String, Option<u64>)> {
        #[cfg(test)]
        if self.summary_hook.is_some() {
            return Ok((
                self.summarize(instructions, input, max_tokens).await?,
                Some(0),
            ));
        }
        if self.scripted {
            return Ok((
                self.summarize(instructions, input, max_tokens).await?,
                Some(0),
            ));
        }
        self.text_response_usage(instructions, input, max_tokens, None)
            .await
    }
    pub async fn review_with_usage(
        &self,
        input: &str,
        max_tokens: usize,
    ) -> (crate::permissions::Verdict, Option<u64>) {
        #[cfg(test)]
        if self.review_hook.is_some() {
            return (self.review(input).await, Some(0));
        }
        if self.scripted {
            return (self.review(input).await, Some(0));
        }
        match tokio::time::timeout(
            std::time::Duration::from_secs(90),
            self.text_response_usage(
                crate::permissions::REVIEW_INSTRUCTIONS,
                input,
                max_tokens,
                Some(crate::permissions::review_format()),
            ),
        )
        .await
        {
            Ok(Ok((text, usage))) => (crate::permissions::Verdict::parse(&text), usage),
            _ => (
                crate::permissions::Verdict::unsure(
                    "Reviewer unavailable or timed out; human approval required.",
                ),
                None,
            ),
        }
    }
    async fn text_response(
        &self,
        instructions: &str,
        input: &str,
        max_tokens: usize,
        format: Option<Value>,
    ) -> Result<String> {
        Ok(self
            .text_response_usage(instructions, input, max_tokens, format)
            .await?
            .0)
    }
    async fn text_response_usage(
        &self,
        instructions: &str,
        input: &str,
        max_tokens: usize,
        format: Option<Value>,
    ) -> Result<(String, Option<u64>)> {
        let history = vec![json!({"role":"user","content":input})];
        let (tx, mut rx) = mpsc::channel(32);
        let request = self.request(instructions, &history, &[], max_tokens, format, tx);
        tokio::pin!(request);
        let mut response = None;
        let mut finished = false;
        let mut closed = false;
        while !finished || !closed {
            tokio::select! {
                r=&mut request,if !finished=>{r?;finished=true;},
                p=rx.recv(),if !closed=>match p {
                    Some(Progress::Complete(r))=>response=Some(r),
                    Some(Progress::Delta(_)|Progress::OutputStarted|Progress::Usage(_))=>{},None=>closed=true
                }
            }
        }
        let r = response.context("Summarizer response missing")?;
        if r.output.iter().any(|v| v["type"] == "function_call") {
            bail!("Summarizer attempted a tool call");
        }
        let mut text = String::new();
        for item in r.output {
            if item["type"] == "message" {
                if let Some(parts) = item["content"].as_array() {
                    for p in parts {
                        if let Some(s) = p["text"].as_str() {
                            text.push_str(s);
                        }
                    }
                }
            }
        }
        Ok((text, r.usage.as_ref().map(|u| u.output_tokens)))
    }
    pub async fn review(&self, input: &str) -> crate::permissions::Verdict {
        use crate::permissions::{Verdict, REVIEW_INSTRUCTIONS};
        #[cfg(test)]
        if let Some(hook) = &self.review_hook {
            return match hook(input.into()).await {
                Ok(text) => Verdict::parse(&text),
                Err(_) => Verdict::unsure("Reviewer failed; human approval required."),
            };
        }
        if self.scripted {
            return Verdict::unsure(
                "Scripted model has no live reviewer; human approval required.",
            );
        }
        match tokio::time::timeout(
            std::time::Duration::from_secs(90),
            self.text_response(
                REVIEW_INSTRUCTIONS,
                input,
                4000,
                Some(crate::permissions::review_format()),
            ),
        )
        .await
        {
            Ok(Ok(text)) => Verdict::parse(&text),
            _ => Verdict::unsure("Reviewer unavailable or timed out; human approval required."),
        }
    }
    async fn fixture(&self, history: &[Value], tx: mpsc::Sender<Progress>) -> Result<()> {
        let start = history
            .iter()
            .rposition(|v| {
                v["role"] == "user"
                    && !matches!(
                        v["_runtime_notice"].as_str(),
                        Some("agent_inbox" | "agent_wait")
                    )
            })
            .unwrap_or(0);
        let calls: Vec<_> = history[start..]
            .iter()
            .filter(|v| v["type"] == "function_call")
            .collect();
        let names: Vec<_> = calls.iter().filter_map(|v| v["name"].as_str()).collect();
        // Explicit scripted-only team integration. The real loop, store, board,
        // approvals and tools run normally; this branch never runs in live mode.
        let prompt = history
            .get(start)
            .and_then(|v| v["content"].as_str())
            .unwrap_or("");
        if self.name == "team-fixture" && prompt.starts_with("Team ") {
            let result = |name: &str| -> Value {
                let call = calls.iter().rev().find(|v| v["name"] == name);
                call.and_then(|c| {
                    history[start..].iter().find(|v| {
                        v["type"] == "function_call_output" && v["call_id"] == c["call_id"]
                    })
                })
                .and_then(|v| v["output"].as_str())
                .and_then(|v| serde_json::from_str(v).ok())
                .unwrap_or(Value::Null)
            };
            let board = result("team_get_board");
            let item = board["board"]["items"]
                .as_object()
                .and_then(|items| {
                    items
                        .values()
                        .find(|i| i["id"].as_str().is_some_and(|id| prompt.contains(id)))
                })
                .cloned()
                .unwrap_or(Value::Null);
            let next = if !names.contains(&"team_get_board") {
                Some(("team_get_board", json!({})))
            } else if prompt.starts_with("Team assignment") && !names.contains(&"team_transition") {
                Some((
                    "team_transition",
                    json!({"item_id":item["id"],"expected_revision":item["revision"],"status":"in_progress"}),
                ))
            } else if prompt.starts_with("Team assignment") && !names.contains(&"test__probe") {
                Some(("test__probe", json!({"action":"write_once"})))
            } else if prompt.starts_with("Team assignment") && !names.contains(&"team_post_message")
            {
                Some((
                    "team_post_message",
                    json!({"item_id":item["id"],"body":"Fixture tool returned; inspect proof.txt before acceptance.","recipient":""}),
                ))
            } else if prompt.starts_with("Team assignment")
                && names.iter().filter(|n| **n == "team_transition").count() == 1
            {
                let prior = result("team_transition");
                let effect = result("test__probe");
                Some((
                    "team_transition",
                    json!({"item_id":item["id"],"expected_revision":prior["data"]["item"]["revision"],"status":if effect["denied"]==true||effect["isError"]==true{"blocked"}else{"review"}}),
                ))
            } else if prompt.starts_with("Team worker turn ended")
                && item["status"] == "review"
                && !names.contains(&"team_transition")
            {
                Some((
                    "team_transition",
                    json!({"item_id":item["id"],"expected_revision":item["revision"],"status":"done"}),
                ))
            } else {
                None
            };
            let output = match next {
                Some((name, args)) => vec![
                    json!({"type":"function_call","call_id":crate::id("call"),"name":name,"arguments":args.to_string()}),
                ],
                None => vec![
                    json!({"type":"message","role":"assistant","content":[{"type":"output_text","text":"Scripted team step finished. Not a live model result."}]}),
                ],
            };
            tx.send(Progress::Complete(crate::provider::Completion {
                output,
                usage: None,
            }))
            .await?;
            return Ok(());
        }
        // Operator-only deterministic computer integration. Still uses real tool
        // registration, approvals, consent and Windows input; never a live mode.
        if history.get(start).is_some_and(|v| {
            matches!(
                v["content"].as_str(),
                Some("TEST_COMPUTER" | "TEST_COMPUTER_ACTIONS")
            )
        }) {
            let last = history
                .iter()
                .rev()
                .find(|v| v["type"] == "function_call_output");
            let data = last
                .and_then(|v| {
                    v["output"]
                        .as_str()
                        .map(str::to_owned)
                        .or_else(|| v["output"][0]["text"].as_str().map(str::to_owned))
                })
                .and_then(|s| serde_json::from_str::<Value>(&s).ok())
                .unwrap_or(Value::Null);
            let full = history[start]["content"] == "TEST_COMPUTER_ACTIONS";
            let args = if full {
                let observation_id = &data["structuredContent"]["observation_id"];
                match calls.len() {
                    0 | 2 | 4 | 6 | 8 | 10 => Some(json!({"action":"observe"})),
                    1 => Some(
                        json!({"action":"click","observation_id":observation_id,"x":120,"y":150}),
                    ),
                    3 => Some(
                        json!({"action":"type","observation_id":observation_id,"text":"Sample text"}),
                    ),
                    5 => Some(
                        json!({"action":"keypress","observation_id":observation_id,"keys":["CTRL","A"]}),
                    ),
                    7 => Some(
                        json!({"action":"type","observation_id":observation_id,"text":"Replaced by Luma"}),
                    ),
                    9 => Some(
                        json!({"action":"scroll","observation_id":observation_id,"direction":"down","amount":2}),
                    ),
                    _ => None,
                }
            } else {
                match calls.len() {
                    0 | 2 => Some(json!({"action":"observe"})),
                    1 if data["structuredContent"]["observation_id"].is_string() => Some(
                        json!({"action":"type","text":"Luma computer test","observation_id":data["structuredContent"]["observation_id"]}),
                    ),
                    _ => None,
                }
            };
            let output = if let Some(args) = args {
                vec![
                    json!({"type":"function_call","call_id":crate::id("call"),"name":"computer__use","arguments":args.to_string()}),
                ]
            } else {
                vec![
                    json!({"type":"message","role":"assistant","content":[{"type":"output_text","text":"Scripted computer fixture finished. Inspect actual Windows results; this is not a live model."}]}),
                ]
            };
            tx.send(Progress::Complete(crate::provider::Completion {
                output,
                usage: None,
            }))
            .await?;
            return Ok(());
        }
        // Opt-in test image only: the runner observes this delta before killing
        // its owned worker. No tool completion is emitted or accepted, and the
        // wait is bounded even if a qualification process disappears.
        #[cfg(any(test, feature = "test-fixtures"))]
        if history
            .get(start)
            .is_some_and(|v| v["content"] == "TEST_RESILIENCE_STREAM")
        {
            tx.send(Progress::Delta(
                "Resilience fixture streaming barrier".into(),
            ))
            .await?;
            tokio::time::sleep(std::time::Duration::from_secs(300)).await;
            anyhow::bail!("Resilience stream barrier expired without injected fault");
        }
        // Explicit scripted-only resource integration fixture; never a live-model path.
        if let Some(plan) = history
            .get(start)
            .and_then(|v| {
                let content = v["content"].as_str()?;
                let content = content
                    .strip_prefix(crate::agent_team::DELEGATION_PREFIX)
                    .unwrap_or(content);
                // Deterministic fixtures still exercise the real structured
                // briefing path; only the task drives their scripted steps.
                if let Some(briefing) = content.strip_prefix(crate::team_briefing::PREFIX) {
                    return serde_json::from_str::<Value>(briefing)
                        .ok()?
                        .get("task")?
                        .as_str()
                        .map(str::to_owned);
                }
                if v["_runtime_notice"] == "scheduled_task" {
                    content
                        .strip_prefix(crate::assistant_tools::SCHEDULE_PREFIX)
                        .map(str::to_owned)
                } else {
                    Some(content.to_owned())
                }
            })
            .and_then(|s| s.strip_prefix("TEST_RESOURCE_GRANTS ").map(str::to_owned))
        {
            let steps: Vec<Value> = serde_json::from_str(&plan)?;
            let output = if let Some(step) = steps.get(calls.len()) {
                vec![
                    json!({"type":"function_call","call_id":crate::id("call"),"name":step["name"],"arguments":step["arguments"].to_string()}),
                ]
            } else {
                vec![
                    json!({"type":"message","role":"assistant","content":[{"type":"output_text","text":"Resource fixture finished; inspect actual tool results."}]}),
                ]
            };
            tx.send(Progress::Complete(crate::provider::Completion {
                output,
                usage: None,
            }))
            .await?;
            return Ok(());
        }
        if history
            .get(start)
            .is_some_and(|v| v["content"] == "TEST_SKILLS")
        {
            let steps = [
                ("load_skill", json!({"name":"report-guide"})),
                (
                    "read_skill_resource",
                    json!({"name":"report-guide","path":"references/report-template.md"}),
                ),
                (
                    "write_file",
                    json!({"path":"skill-proof.txt","content":"Bundled skill loaded; reference read; local report created.\n"}),
                ),
            ];
            let output = if let Some((name, args)) = steps.get(calls.len()) {
                vec![
                    json!({"type":"function_call","call_id":crate::id("call"),"name":name,"arguments":args.to_string()}),
                ]
            } else {
                vec![
                    json!({"type":"message","role":"assistant","content":[{"type":"output_text","text":"Scripted skill fixture finished; inspect tool results for actual outcomes."}]}),
                ]
            };
            tx.send(Progress::Complete(crate::provider::Completion {
                output,
                usage: None,
            }))
            .await?;
            return Ok(());
        }
        if history
            .get(start)
            .is_some_and(|v| v["content"] == "TEST_MULTI_MCP")
        {
            let output = if let Some(name) =
                ["north_echo", "south_echo", "north_write"].get(calls.len())
            {
                let args = if *name == "north_write" {
                    json!({"path":"multi-proof.txt","content":"Two MCP servers, explicit routing.\n"})
                } else {
                    json!({"text":name})
                };
                vec![
                    json!({"type":"function_call","call_id":crate::id("call"),"name":name,"arguments":args.to_string()}),
                ]
            } else {
                vec![
                    json!({"type":"message","role":"assistant","content":[{"type":"output_text","text":"Scripted multi-server fixture finished; inspect actual tool results."}]}),
                ]
            };
            tx.send(Progress::Complete(crate::provider::Completion {
                output,
                usage: None,
            }))
            .await?;
            return Ok(());
        }
        // Explicit scripted-only lifecycle fixture. Never selected by the live provider.
        if history
            .get(start)
            .is_some_and(|v| v["content"] == "TEST_DIRECT_HANG")
        {
            let output = if calls.is_empty() {
                vec![
                    json!({"type":"function_call","call_id":crate::id("call"),"name":"run_shell",
                    "arguments":json!({"command":"setsid sh -c 'echo $$ > direct-child.pid; sleep 60' & wait"}).to_string()}),
                ]
            } else {
                vec![
                    json!({"type":"message","role":"assistant","content":[{"type":"output_text","text":"Scripted lifecycle fixture finished."}]}),
                ]
            };
            tx.send(Progress::Complete(crate::provider::Completion {
                output,
                usage: None,
            }))
            .await?;
            return Ok(());
        }
        let (text, tool) = if !names.contains(&"ask_user") {
            (
                "Scripted test: choose the content for the example file.",
                Some((
                    "ask_user",
                    json!({"question":"What should the example file say?","options":["Hello ExampleApp","Hello OpenWorker"]}),
                )),
            )
        } else if !names.contains(&"example_echo") {
            (
                "Calling the bundled ExampleApp MCP tool after your approval.",
                Some(("example_echo", json!({"text":"Hello ExampleApp"}))),
            )
        } else if !names.contains(&"write_file") {
            (
                "Requesting approval to write proof.txt inside the sandbox workspace.",
                Some((
                    "write_file",
                    json!({"path":"proof.txt","content":"Hello ExampleApp — written by a native Rust tool in the configured execution environment.\n"}),
                )),
            )
        } else if !names.contains(&"run_shell") {
            (
                "Checking the workspace through a sandboxed command.",
                Some(("run_shell", json!({"command":"pwd; cat proof.txt"}))),
            )
        } else {
            ("Scripted test finished. Inspect tool results for actual outcomes; this was not live AI generation.",None)
        };
        for chunk in text.as_bytes().chunks(24) {
            tx.send(Progress::Delta(String::from_utf8_lossy(chunk).into()))
                .await?;
            tokio::task::yield_now().await;
        }
        let mut output = vec![
            json!({"type":"message","role":"assistant","content":[{"type":"output_text","text":text}]}),
        ];
        if let Some((name, args)) = tool {
            output.push(json!({"type":"function_call","call_id":crate::id("call"),"name":name,"arguments":args.to_string()}));
        }
        tx.send(Progress::Complete(crate::provider::Completion {
            output,
            usage: None,
        }))
        .await?;
        Ok(())
    }
}

pub fn validate_output(output: &[Value]) -> Result<()> {
    if output.len() > 64 || serde_json::to_vec(output)?.len() > 512000 {
        bail!("Provider output exceeds checkpoint budget");
    }
    let mut ids = std::collections::HashSet::new();
    for v in output {
        if v["type"] == "function_call" {
            let id = v["call_id"].as_str().context("Tool call id missing")?;
            if id.is_empty() || !ids.insert(id) || v["name"].as_str().is_none() {
                bail!("Malformed or duplicate tool call");
            }
            let args: Value =
                serde_json::from_str(v["arguments"].as_str().context("Tool arguments missing")?)?;
            if !args.is_object() {
                bail!("Tool arguments must be an object");
            }
        }
    }
    Ok(())
}
