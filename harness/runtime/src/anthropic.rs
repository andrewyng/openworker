//! Claude Messages adapter over the runtime's canonical items. No execution or retries.
use crate::{
    model::{validate_output, Progress},
    provider::{Completion, Error, Failure, Usage},
};
use futures_util::StreamExt;
use serde_json::{json, Value};
use std::collections::{BTreeMap, BTreeSet};
use tokio::sync::mpsc;

fn invalid() -> Error {
    Error::new(Failure::InvalidResponse)
}
fn config() -> Error {
    Error::new(Failure::Configuration)
}

fn append(messages: &mut Vec<Value>, role: &str, blocks: Vec<Value>) {
    if blocks.is_empty() {
        return;
    }
    if let Some(last) = messages.last_mut().filter(|m| m["role"] == role) {
        last["content"].as_array_mut().unwrap().extend(blocks);
    } else {
        messages.push(json!({"role":role,"content":blocks}));
    }
}

fn content(value: &Value) -> Result<Vec<Value>, Error> {
    if let Some(s) = value.as_str() {
        return Ok(vec![json!({"type":"text","text":s})]);
    }
    value
        .as_array()
        .ok_or_else(config)?
        .iter()
        .map(|v| match v["type"].as_str() {
            Some("text" | "input_text" | "output_text") => {
                Ok(json!({"type":"text","text":v["text"].as_str().ok_or_else(config)?}))
            }
            Some("input_image") => {
                let url = v["image_url"].as_str().ok_or_else(config)?;
                let (header, data) = url.split_once(",").ok_or_else(config)?;
                let mime = header
                    .strip_prefix("data:")
                    .and_then(|s| s.strip_suffix(";base64"))
                    .ok_or_else(config)?;
                if !["image/png", "image/jpeg", "image/gif", "image/webp"].contains(&mime) {
                    return Err(config());
                }
                Ok(json!({"type":"image","source":{"type":"base64","media_type":mime,"data":data}}))
            }
            _ => Err(config()),
        })
        .collect()
}

pub fn body(
    model: &str,
    instructions: &str,
    history: &[Value],
    tools: &[Value],
    max_tokens: usize,
    format: Option<&Value>,
) -> Result<Value, Error> {
    let mut messages: Vec<Value> = vec![];
    let mut pending = BTreeSet::new();
    let mut calls = BTreeSet::new();
    for item in history {
        match item["type"].as_str() {
            Some("reasoning") => {
                // Provider-private blocks never cross provider boundaries.
                if item["provider"] != "anthropic"
                    || (!pending.is_empty()
                        && messages.last().is_none_or(|m| m["role"] != "assistant"))
                {
                    return Err(config());
                }
                append(&mut messages, "assistant", vec![item["block"].clone()]);
            }
            Some("function_call") => {
                let id = item["call_id"].as_str().ok_or_else(config)?;
                if !calls.insert(id.to_owned()) {
                    return Err(config());
                }
                pending.insert(id.to_owned());
                let input: Value =
                    serde_json::from_str(item["arguments"].as_str().ok_or_else(config)?)
                        .map_err(|_| config())?;
                if !input.is_object() {
                    return Err(config());
                }
                append(
                    &mut messages,
                    "assistant",
                    vec![
                        json!({"type":"tool_use","id":id,"name":item["name"].as_str().ok_or_else(config)?,"input":input}),
                    ],
                );
            }
            Some("function_call_output") => {
                let id = item["call_id"].as_str().ok_or_else(config)?;
                if !pending.remove(id) {
                    return Err(config());
                }
                let result = &item["output"];
                let is_error = result
                    .as_str()
                    .and_then(|s| serde_json::from_str::<Value>(s).ok())
                    .is_some_and(|v| v["isError"] == true || v["denied"] == true);
                append(
                    &mut messages,
                    "user",
                    vec![
                        json!({"type":"tool_result","tool_use_id":id,"content":content(result)?,"is_error":is_error}),
                    ],
                );
            }
            Some("message") | None => {
                let role = item["role"].as_str().ok_or_else(config)?;
                if !pending.is_empty()
                    && (role != "assistant"
                        || messages.last().is_none_or(|m| m["role"] != "assistant"))
                {
                    return Err(config());
                }
                if !["assistant", "user"].contains(&role) {
                    return Err(config());
                }
                append(&mut messages, role, content(&item["content"])?);
            }
            _ => return Err(config()),
        }
    }
    if !pending.is_empty() || messages.first().is_none_or(|m| m["role"] != "user") {
        return Err(config());
    }
    let definitions = tools.iter().map(|t| {
        if t["type"] != "function" || !t["parameters"].is_object() { return Err(config()); }
        Ok(json!({"name":t["name"].as_str().ok_or_else(config)?,"description":t["description"].as_str().unwrap_or(""),"input_schema":t["parameters"]}))
    }).collect::<Result<Vec<_>,Error>>()?;
    let mut body = json!({"model":model,"system":instructions,"messages":messages,"max_tokens":max_tokens,"stream":true});
    if !definitions.is_empty() {
        body["tools"] = json!(definitions);
        body["tool_choice"] = json!({"type":"auto","disable_parallel_tool_use":true});
    }
    if let Some(f) = format {
        if f["type"] != "json_schema" || !f["schema"].is_object() {
            return Err(config());
        }
        body["output_config"] = json!({"format":{"type":"json_schema","schema":f["schema"]}});
    }
    Ok(body)
}

struct Block {
    value: Value,
    input: String,
    closed: bool,
}
#[derive(Default)]
pub struct Decoder {
    started: bool,
    stopped: bool,
    failed: bool,
    reason: Option<String>,
    blocks: BTreeMap<u64, Block>,
    usage: Usage,
    output_known: bool,
    input_known: bool,
}
impl Decoder {
    fn usage_report(&self) -> Option<crate::telemetry::TokenUsage> {
        self.input_known.then(|| {
            let mut u = self.usage.reported();
            if !self.output_known {
                u.output = None;
            }
            u
        })
    }
    fn output_started(&self) -> bool {
        self.blocks.values().any(|b| {
            b.value["type"] == "tool_use"
                || b.value["type"] == "text"
                    && b.value["text"].as_str().is_some_and(|s| !s.is_empty())
        })
    }
    pub fn event(&mut self, event: &Value) -> Result<Option<String>, Error> {
        let result = self.accept(event);
        if result.is_err() {
            self.failed = true;
        }
        result
    }
    fn accept(&mut self, e: &Value) -> Result<Option<String>, Error> {
        if self.failed || self.stopped {
            return Err(invalid());
        }
        match e["type"].as_str() {
            Some("ping") => {}
            Some("error") => return Err(Error::new(classify(0, &e["error"]))),
            Some("message_start") => {
                if self.started
                    || e["message"]["role"] != "assistant"
                    || e["message"]["content"]
                        .as_array()
                        .is_none_or(|v| !v.is_empty())
                {
                    return Err(invalid());
                }
                self.started = true;
                let u = &e["message"]["usage"];
                let input = u["input_tokens"].as_u64().ok_or_else(invalid)?;
                let cache_read = u["cache_read_input_tokens"].as_u64().unwrap_or(0);
                let cache_write = u["cache_creation_input_tokens"].as_u64().unwrap_or(0);
                self.usage.input_tokens = input
                    .checked_add(cache_read)
                    .and_then(|n| n.checked_add(cache_write))
                    .ok_or_else(invalid)?;
                self.input_known = true;
                if let Some(output) = u["output_tokens"].as_u64() {
                    self.usage.output_tokens = output;
                    self.output_known = true;
                }
                self.usage.cached_input_tokens = cache_read;
                self.usage.cache_read_input_tokens = u["cache_read_input_tokens"].as_u64();
                self.usage.cache_write_input_tokens = u["cache_creation_input_tokens"].as_u64();
            }
            Some("content_block_start") => {
                if !self.started || self.reason.is_some() || self.blocks.values().any(|b| !b.closed)
                {
                    return Err(invalid());
                }
                let index = e["index"].as_u64().ok_or_else(invalid)?;
                if index != self.blocks.len() as u64 || index >= 64 {
                    return Err(invalid());
                }
                let v = e["content_block"].clone();
                match v["type"].as_str() {
                    Some("text") if v["text"].is_string() => {}
                    Some("tool_use")
                        if v["id"].is_string()
                            && v["name"].is_string()
                            && v["input"].is_object() => {}
                    Some("thinking") if v["thinking"].is_string() => {}
                    Some("redacted_thinking") if v["data"].is_string() => {}
                    _ => return Err(invalid()),
                }
                self.blocks.insert(
                    index,
                    Block {
                        value: v,
                        input: String::new(),
                        closed: false,
                    },
                );
            }
            Some("content_block_delta") => {
                let block = self
                    .blocks
                    .get_mut(&e["index"].as_u64().ok_or_else(invalid)?)
                    .ok_or_else(invalid)?;
                if block.closed || self.reason.is_some() {
                    return Err(invalid());
                }
                let d = &e["delta"];
                let (field, fragment) = match (block.value["type"].as_str(), d["type"].as_str()) {
                    (Some("text"), Some("text_delta")) => {
                        ("text", d["text"].as_str().ok_or_else(invalid)?)
                    }
                    (Some("tool_use"), Some("input_json_delta")) => {
                        ("input", d["partial_json"].as_str().ok_or_else(invalid)?)
                    }
                    (Some("thinking"), Some("thinking_delta")) => {
                        ("thinking", d["thinking"].as_str().ok_or_else(invalid)?)
                    }
                    (Some("thinking"), Some("signature_delta")) => {
                        ("signature", d["signature"].as_str().ok_or_else(invalid)?)
                    }
                    _ => return Err(invalid()),
                };
                if field == "input" {
                    block.input.push_str(fragment);
                } else {
                    let mut value = block.value[field].as_str().unwrap_or("").to_owned();
                    value.push_str(fragment);
                    block.value[field] = json!(value);
                }
                if block.input.len() + block.value.to_string().len() > 512000 {
                    return Err(invalid());
                }
                if field == "text" {
                    return Ok(Some(fragment.into()));
                }
            }
            Some("content_block_stop") => {
                let block = self
                    .blocks
                    .get_mut(&e["index"].as_u64().ok_or_else(invalid)?)
                    .ok_or_else(invalid)?;
                if block.closed {
                    return Err(invalid());
                }
                // A closed block can still be incomplete when the message hit
                // its token limit. Validate content only after the message-level
                // outcome; otherwise truncation is misreported as invalid JSON
                // (or a missing thinking signature) and cannot be recovered.
                block.closed = true;
            }
            Some("message_delta") => {
                if !self.started || self.reason.is_some() {
                    return Err(invalid());
                }
                self.usage.output_tokens =
                    e["usage"]["output_tokens"].as_u64().ok_or_else(invalid)?;
                self.output_known = true;
                let reason = e["delta"]["stop_reason"].as_str().ok_or_else(invalid)?;
                match reason {
                    "end_turn" | "tool_use" => {
                        if self.blocks.values().any(|b| !b.closed) {
                            return Err(invalid());
                        }
                        self.reason = Some(reason.into());
                    }
                    "refusal" => return Err(Error::new(Failure::SafetyBlocked)),
                    "max_tokens" | "model_context_window_exceeded" => {
                        // A token-limited tool stream may omit content_block_stop.
                        // Its unfinished blocks are discarded, never completed.
                        return Err(Error::new(Failure::Truncated));
                    }
                    _ => return Err(invalid()),
                }
            }
            Some("message_stop") => {
                if !self.started || self.reason.is_none() || self.blocks.values().any(|b| !b.closed)
                {
                    return Err(invalid());
                }
                self.stopped = true;
            }
            _ => return Err(invalid()),
        }
        Ok(None)
    }
    pub fn finish(self) -> Result<Completion, Error> {
        if self.failed {
            return Err(invalid());
        }
        if !self.stopped {
            return Err(Error::new(Failure::IncompleteStream));
        }
        let mut output = vec![];
        let mut tool = false;
        for b in self.blocks.into_values() {
            let mut v = b.value;
            if v["type"] == "tool_use" && !b.input.is_empty() {
                v["input"] = serde_json::from_str(&b.input).map_err(|_| invalid())?;
                if !v["input"].is_object() {
                    return Err(invalid());
                }
            }
            if v["type"] == "thinking" && v["signature"].as_str().is_none_or(str::is_empty) {
                return Err(invalid());
            }
            match v["type"].as_str() {
                Some("text") => output.push(json!({"type":"message","role":"assistant","content":[{"type":"output_text","text":v["text"]}]})),
                Some("tool_use") => {
                    tool = true;
                    output.push(json!({"type":"function_call","call_id":v["id"],"name":v["name"],"arguments":v["input"].to_string()}));
                }
                Some("thinking" | "redacted_thinking") => output.push(json!({"type":"reasoning","provider":"anthropic","block":v})),
                _ => return Err(invalid()),
            }
        }
        if output.is_empty()
            || tool != (self.reason.as_deref() == Some("tool_use"))
            || validate_output(&output).is_err()
        {
            return Err(invalid());
        }
        Ok(Completion {
            output,
            usage: Some(self.usage),
        })
    }
}

pub fn classify(status: u16, error: &Value) -> Failure {
    match error["type"].as_str().unwrap_or("") {
        "authentication_error" | "permission_error" => Failure::Authentication,
        "rate_limit_error" => Failure::RateLimit,
        "overloaded_error" | "api_error" => Failure::Transient,
        "invalid_request_error"
            if error["message"]
                .as_str()
                .is_some_and(|s| s.to_ascii_lowercase().contains("prompt is too long")) =>
        {
            Failure::ContextOverflow
        }
        _ if status == 529 => Failure::Transient,
        _ => crate::provider::classify(status, ""),
    }
}

pub async fn request(
    client: &reqwest::Client,
    url: &str,
    key: &str,
    body: Value,
    tx: mpsc::Sender<Progress>,
) -> Result<(), Error> {
    let response = client
        .post(url)
        .header("x-api-key", key)
        .header("anthropic-version", "2023-06-01")
        .json(&body)
        .send()
        .await
        .map_err(|_| Error::new(Failure::Transport))?;
    let status = response.status().as_u16();
    let retry_after = response
        .headers()
        .get("retry-after")
        .and_then(|v| v.to_str().ok())
        .and_then(|v| v.parse().ok());
    let mut stream = response.bytes_stream();
    if !(200..300).contains(&status) {
        let mut bytes = vec![];
        while let Some(chunk) = stream.next().await {
            let chunk = chunk.map_err(|_| Error::new(classify(status, &Value::Null)))?;
            if bytes.len() + chunk.len() > 16384 {
                break;
            }
            bytes.extend_from_slice(&chunk);
        }
        let error: Value = serde_json::from_slice(&bytes).unwrap_or(Value::Null);
        return Err(Error {
            http_status: Some(status),
            kind: classify(status, &error["error"]),
            retry_after,
        });
    }
    let mut decoder = Decoder::default();
    let mut buffer = vec![];
    let mut total = 0;
    let mut previous_usage = None;
    let mut first_sent = false;
    while let Some(chunk) = stream.next().await {
        let chunk = chunk.map_err(|_| Error::new(Failure::Transport))?;
        total += chunk.len();
        if total > 8 * 1024 * 1024 {
            return Err(invalid());
        }
        buffer.extend_from_slice(&chunk);
        while let Some(index) = buffer.iter().position(|b| *b == b'\n') {
            let line: Vec<_> = buffer.drain(..=index).collect();
            let line = std::str::from_utf8(&line).map_err(|_| invalid())?.trim();
            if let Some(data) = line.strip_prefix("data:") {
                let event: Value = serde_json::from_str(data).map_err(|_| invalid())?;
                let decoded = decoder.event(&event);
                if let Some(usage) = decoder
                    .usage_report()
                    .filter(|u| u.present() && Some(*u) != previous_usage)
                {
                    tx.send(Progress::Usage(usage))
                        .await
                        .map_err(|_| Error::new(Failure::Transport))?;
                    previous_usage = Some(usage);
                }
                let decoded = decoded?;
                if !first_sent && decoder.output_started() {
                    tx.send(Progress::OutputStarted)
                        .await
                        .map_err(|_| Error::new(Failure::Transport))?;
                    first_sent = true;
                }
                if let Some(text) = decoded {
                    tx.send(Progress::Delta(text))
                        .await
                        .map_err(|_| Error::new(Failure::Transport))?;
                }
            }
        }
    }
    if !buffer.iter().all(u8::is_ascii_whitespace) {
        return Err(invalid());
    }
    tx.send(Progress::Complete(decoder.finish()?))
        .await
        .map_err(|_| Error::new(Failure::Transport))?;
    Ok(())
}
