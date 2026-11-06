//! OpenAI-compatible Chat Completions for local and hosted models.
//! Only a fully terminated, validated completion can authorize tool execution.
use crate::provider::{Completion, Error, Failure, StreamDecoder, Usage};
use serde_json::{json, Value};
use std::collections::{BTreeMap, BTreeSet};
fn invalid() -> Error {
    Error::new(Failure::InvalidResponse)
}
fn config() -> Error {
    Error::new(Failure::Configuration)
}

fn content(value: &Value) -> Result<Value, Error> {
    if value.is_string() {
        return Ok(value.clone());
    }
    let blocks = value.as_array().ok_or_else(config)?.iter().map(|p| {
        match p["type"].as_str() {
            Some("text" | "input_text" | "output_text") => Ok(json!({"type":"text","text":p["text"].as_str().ok_or_else(config)?})),
            Some("input_image") => Ok(json!({"type":"image_url","image_url":{"url":p["image_url"].as_str().ok_or_else(config)?}})),
            _ => Err(config()),
        }
    }).collect::<Result<Vec<_>,_>>()?;
    Ok(json!(blocks))
}

pub fn body(
    model: &str,
    instructions: &str,
    history: &[Value],
    tools: &[Value],
    max_tokens: usize,
    format: Option<&Value>,
) -> Result<Value, Error> {
    let mut messages = vec![json!({"role":"system","content":instructions})];
    let mut pending = BTreeSet::new();
    let mut seen = BTreeSet::new();
    for item in history {
        // Encrypted/signed reasoning must never cross to another API.
        if item.get("provider").is_some() || item["type"] == "reasoning" {
            return Err(config());
        }
        match item["type"].as_str() {
            Some("function_call") => {
                let id = item["call_id"].as_str().ok_or_else(config)?;
                if !seen.insert(id) {
                    return Err(config());
                }
                pending.insert(id);
                let arguments = item["arguments"].as_str().ok_or_else(config)?;
                if !serde_json::from_str::<Value>(arguments)
                    .map_err(|_| config())?
                    .is_object()
                {
                    return Err(config());
                }
                let call = json!({"id":id,"type":"function","function":{"name":item["name"].as_str().ok_or_else(config)?,"arguments":arguments}});
                if messages.last().is_some_and(|m| m["role"] == "assistant") {
                    let last = messages.last_mut().unwrap();
                    if last.get("tool_calls").is_none() {
                        last["tool_calls"] = json!([]);
                    }
                    last["tool_calls"].as_array_mut().unwrap().push(call);
                } else {
                    messages.push(json!({"role":"assistant","content":null,"tool_calls":[call]}));
                }
            }
            Some("function_call_output") => {
                let id = item["call_id"].as_str().ok_or_else(config)?;
                if !pending.remove(id) {
                    return Err(config());
                }
                messages.push(
                    json!({"role":"tool","tool_call_id":id,"content":content(&item["output"])?}),
                );
            }
            Some("message") | None => {
                if !pending.is_empty() {
                    return Err(config());
                }
                let role = item["role"].as_str().ok_or_else(config)?;
                if !["assistant", "user"].contains(&role) {
                    return Err(config());
                }
                messages.push(json!({"role":role,"content":content(&item["content"])?}));
            }
            _ => return Err(config()),
        }
    }
    if !pending.is_empty() {
        return Err(config());
    }
    let definitions = tools.iter().map(|tool| {
        if tool["type"] != "function" || !tool["parameters"].is_object() { return Err(config()); }
        Ok(json!({"type":"function","function":{"name":tool["name"].as_str().ok_or_else(config)?,"description":tool["description"].as_str().unwrap_or(""),"parameters":tool["parameters"]}}))
    }).collect::<Result<Vec<_>,_>>()?;
    // The compatible/local API (including Ollama) accepts max_tokens. Models
    // requiring OpenAI's newer reasoning contract should use Responses instead.
    let mut result =
        json!({"model":model,"messages":messages,"stream":true,"max_tokens":max_tokens});
    if !definitions.is_empty() {
        result["tools"] = json!(definitions);
        result["parallel_tool_calls"] = json!(false);
    }
    if let Some(format) = format {
        if format["type"] != "json_schema" || !format["schema"].is_object() {
            return Err(config());
        }
        result["response_format"] = json!({"type":"json_schema","json_schema":{"name":format["name"],"schema":format["schema"],"strict":format["strict"]}});
    }
    Ok(result)
}

#[derive(Default)]
struct Call {
    id: String,
    name: String,
    arguments: String,
}
#[derive(Default)]
pub struct Decoder {
    id: Option<String>,
    text: String,
    calls: BTreeMap<u64, Call>,
    reason: Option<String>,
    done: bool,
    accounting_tail: bool,
    usage: Option<Usage>,
    bytes: usize,
}
impl StreamDecoder for Decoder {
    fn usage_report(&self) -> Option<crate::telemetry::TokenUsage> {
        self.usage.as_ref().map(Usage::reported)
    }
    fn output_started(&self) -> bool {
        !self.text.is_empty()
            || self
                .calls
                .values()
                .any(|c| !c.name.is_empty() || !c.arguments.is_empty())
    }
    fn event(&mut self, event: &Value) -> Result<Option<String>, Error> {
        if self.done || self.accounting_tail || event.get("error").is_some() {
            return Err(invalid());
        }
        if let Some(id) = event["id"].as_str() {
            if self.id.as_ref().is_some_and(|old| old != id) {
                return Err(invalid());
            }
            self.id = Some(id.into());
        }
        self.bytes += event.to_string().len();
        if self.bytes > 8 * 1024 * 1024 {
            return Err(invalid());
        }
        let usage = event
            .get("usage")
            .filter(|u| !u.is_null())
            .map(|usage| {
                Ok::<_, Error>(Usage {
                    input_tokens: usage["prompt_tokens"].as_u64().ok_or_else(invalid)?,
                    output_tokens: usage["completion_tokens"].as_u64().ok_or_else(invalid)?,
                    cached_input_tokens: usage["prompt_tokens_details"]["cached_tokens"]
                        .as_u64()
                        .unwrap_or(0),
                    cache_read_input_tokens: usage["prompt_tokens_details"]["cached_tokens"]
                        .as_u64(),
                    cache_write_input_tokens: None,
                    reasoning_output_tokens: usage["completion_tokens_details"]["reasoning_tokens"]
                        .as_u64(),
                })
            })
            .transpose()?;
        let choices = event["choices"].as_array().ok_or_else(invalid)?;
        if choices.is_empty() {
            return if self.reason.is_some() && usage.is_some() {
                self.usage = usage;
                self.accounting_tail = true;
                Ok(None)
            } else {
                Err(invalid())
            };
        }
        if choices.len() != 1 || choices[0]["index"] != 0 {
            return Err(invalid());
        }
        let choice = &choices[0];
        let delta = &choice["delta"];
        if let Some(reason) = &self.reason {
            // OpenRouter's final usage chunk repeats the terminal reason with an
            // empty delta, unlike OpenAI's choices:[] accounting chunk. It is not
            // a second completion and cannot append text or tool arguments.
            // https://openrouter.ai/docs/api_reference/streaming
            if usage.is_none()
                || choice["finish_reason"].as_str() != Some(reason.as_str())
                || choice.get("error").is_some()
                || event["id"]
                    .as_str()
                    .is_none_or(|id| Some(id) != self.id.as_deref())
                || !delta.as_object().is_some_and(|fields| {
                    fields.iter().all(|(key, value)| match key.as_str() {
                        "role" => value.is_null() || value == "assistant",
                        "content" => value.is_null() || value == "",
                        "tool_calls" => {
                            value.is_null() || value.as_array().is_some_and(Vec::is_empty)
                        }
                        "refusal" => value.is_null(),
                        _ => false,
                    })
                })
            {
                return Err(invalid());
            }
            self.usage = usage;
            self.accounting_tail = true;
            return Ok(None);
        }
        if usage.is_some() {
            self.usage = usage;
        }
        if !delta.is_object()
            || delta.get("role").is_some_and(|r| r != "assistant")
            || delta.get("refusal").is_some_and(|r| !r.is_null())
        {
            return Err(Error::new(Failure::SafetyBlocked));
        }
        let text = match delta.get("content").filter(|v| !v.is_null()) {
            Some(value) => {
                let s = value.as_str().ok_or_else(invalid)?;
                self.text.push_str(s);
                Some(s.to_owned())
            }
            None => None,
        };
        if let Some(calls) = delta.get("tool_calls") {
            for c in calls.as_array().ok_or_else(invalid)? {
                let index = c["index"].as_u64().ok_or_else(invalid)?;
                if index >= 64 || c.get("type").is_some_and(|v| v != "function") {
                    return Err(invalid());
                }
                let call = self.calls.entry(index).or_default();
                if let Some(id) = c.get("id") {
                    if !call.id.is_empty() {
                        return Err(invalid());
                    }
                    call.id = id.as_str().ok_or_else(invalid)?.into();
                }
                if let Some(name) = c["function"].get("name") {
                    call.name.push_str(name.as_str().ok_or_else(invalid)?);
                }
                if let Some(args) = c["function"].get("arguments") {
                    call.arguments.push_str(args.as_str().ok_or_else(invalid)?);
                }
            }
        }
        if let Some(reason) = choice.get("finish_reason").filter(|v| !v.is_null()) {
            match reason.as_str() {
                Some("stop") if self.calls.is_empty() => {}
                Some("tool_calls") if !self.calls.is_empty() => {}
                Some("length") => return Err(Error::new(Failure::Truncated)),
                Some("content_filter") => return Err(Error::new(Failure::SafetyBlocked)),
                _ => return Err(invalid()),
            }
            self.reason = Some(reason.as_str().unwrap().into());
        }
        Ok(text)
    }
    fn done(&mut self) -> Result<(), Error> {
        if self.done || self.reason.is_none() {
            return Err(invalid());
        }
        self.done = true;
        Ok(())
    }
    fn finish(self) -> Result<Completion, Error> {
        if !self.done || self.reason.is_none() {
            return Err(Error::new(Failure::IncompleteStream));
        }
        let mut output = vec![];
        if !self.text.is_empty() {
            output.push(json!({"type":"message","role":"assistant","content":[{"type":"output_text","text":self.text}]}));
        }
        for (index, call) in self.calls {
            if call.id.is_empty() || call.name.is_empty() {
                return Err(invalid());
            }
            output.push(json!({"type":"function_call","id":format!("chat_call_{index}"),"call_id":call.id,"name":call.name,"arguments":call.arguments}));
        }
        if output.is_empty() || crate::model::validate_output(&output).is_err() {
            return Err(invalid());
        }
        Ok(Completion {
            output,
            usage: self.usage,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn chunk(delta: Value, reason: Value) -> Value {
        json!({"id":"c1","choices":[{"index":0,"delta":delta,"finish_reason":reason}]})
    }
    fn usage_tail(reason: &str) -> Value {
        let mut event = chunk(json!({"role":"assistant","content":""}), json!(reason));
        event["choices"][0]["native_finish_reason"] = json!(reason);
        event["usage"] = json!({"prompt_tokens":100,"completion_tokens":20,"total_tokens":120,"prompt_tokens_details":{"cached_tokens":80}});
        event
    }
    fn stopped_text() -> Decoder {
        let mut decoder = Decoder::default();
        assert_eq!(
            decoder
                .event(&chunk(json!({"content":"Hello"}), json!("stop")))
                .unwrap(),
            Some("Hello".into())
        );
        decoder
    }
    #[test]
    fn openrouter_usage_tail_preserves_text_and_accounting() {
        let mut decoder = stopped_text();
        assert_eq!(decoder.event(&usage_tail("stop")).unwrap(), None);
        decoder.done().unwrap();
        let result = decoder.finish().unwrap();
        assert_eq!(result.output.len(), 1);
        assert_eq!(result.output[0]["content"][0]["text"], "Hello");
        let usage = result.usage.unwrap();
        assert_eq!(
            (
                usage.input_tokens,
                usage.output_tokens,
                usage.cached_input_tokens
            ),
            (100, 20, 80)
        );
    }
    #[test]
    fn openrouter_usage_tail_preserves_fragmented_tool_call_once() {
        let mut decoder = Decoder::default();
        decoder.event(&chunk(json!({"tool_calls":[{"index":0,"id":"t1","type":"function","function":{"name":"game__show","arguments":"{\"title\":"}}]}), Value::Null)).unwrap();
        decoder
            .event(&chunk(
                json!({"tool_calls":[{"index":0,"function":{"arguments":"\"hello\"}"}}]}),
                json!("tool_calls"),
            ))
            .unwrap();
        assert_eq!(decoder.event(&usage_tail("tool_calls")).unwrap(), None);
        decoder.done().unwrap();
        let result = decoder.finish().unwrap();
        assert_eq!(result.output.len(), 1);
        assert_eq!(result.output[0]["call_id"], "t1");
        assert_eq!(result.output[0]["arguments"], "{\"title\":\"hello\"}");
        assert_eq!(result.usage.unwrap().input_tokens, 100);
    }
    #[test]
    fn openai_empty_choices_usage_tail_remains_supported() {
        let mut decoder = stopped_text();
        let mut tail = usage_tail("stop");
        tail["choices"] = json!([]);
        assert_eq!(decoder.event(&tail).unwrap(), None);
        decoder.done().unwrap();
        assert_eq!(decoder.finish().unwrap().usage.unwrap().output_tokens, 20);
    }
    #[test]
    fn accounting_tail_rejects_choice_level_errors() {
        for reason in ["stop", "tool_calls"] {
            let mut decoder = Decoder::default();
            let delta = if reason == "stop" {
                json!({"content":"hello"})
            } else {
                json!({"tool_calls":[{"index":0,"id":"t1","type":"function","function":{"name":"game__show","arguments":"{}"}}]})
            };
            decoder.event(&chunk(delta, json!(reason))).unwrap();
            let mut tail = usage_tail(reason);
            tail["choices"][0]["error"] = json!({"code":"server_error"});
            assert_eq!(
                decoder.event(&tail).unwrap_err().kind,
                Failure::InvalidResponse
            );
        }
    }
    #[test]
    fn accounting_tail_rejects_conflicts_and_new_content() {
        let mut cases = vec![];
        for delta in [
            json!({"content":"late text"}),
            json!({"tool_calls":[{"index":0,"function":{"arguments":"{}"}}]}),
            json!({"role":"user","content":""}),
            json!({"refusal":"blocked"}),
            json!({"reasoning":"late reasoning"}),
            json!({"function_call":{"name":"unsafe"}}),
            Value::Null,
        ] {
            let mut tail = usage_tail("stop");
            tail["choices"][0]["delta"] = delta;
            cases.push(tail);
        }
        for reason in ["tool_calls", "length", "content_filter"] {
            cases.push(usage_tail(reason));
        }
        let mut tail = usage_tail("stop");
        tail["id"] = json!("another-generation");
        cases.push(tail);
        let mut tail = usage_tail("stop");
        tail.as_object_mut().unwrap().remove("id");
        cases.push(tail);
        let mut tail = usage_tail("stop");
        tail["choices"][0]["index"] = json!(1);
        cases.push(tail);
        let mut tail = usage_tail("stop");
        tail.as_object_mut().unwrap().remove("usage");
        cases.push(tail);
        let mut tail = usage_tail("stop");
        tail["usage"]["prompt_tokens"] = json!("invalid");
        cases.push(tail);
        let mut tail = usage_tail("stop");
        tail["error"] = json!({"message":"failed"});
        cases.push(tail);
        for tail in cases {
            assert!(
                stopped_text().event(&tail).is_err(),
                "Accepted unsafe accounting tail: {tail}"
            );
        }
    }
    #[test]
    fn accounting_tail_does_not_replace_done_or_allow_later_events() {
        let mut decoder = stopped_text();
        decoder.event(&usage_tail("stop")).unwrap();
        assert_eq!(
            decoder.finish().unwrap_err().kind,
            Failure::IncompleteStream
        );
        let mut decoder = stopped_text();
        decoder.event(&usage_tail("stop")).unwrap();
        assert!(decoder.event(&usage_tail("stop")).is_err());
        assert!(decoder
            .event(&chunk(json!({"content":"late"}), Value::Null))
            .is_err());
        let mut decoder = stopped_text();
        decoder.event(&usage_tail("stop")).unwrap();
        decoder.done().unwrap();
        assert!(decoder.event(&usage_tail("stop")).is_err());
        assert!(decoder.done().is_err());
    }
    #[test]
    fn fragmented_call_requires_complete_stream() {
        let mut decoder = Decoder::default();
        decoder.event(&chunk(json!({"tool_calls":[{"index":0,"id":"t1","type":"function","function":{"name":"game__show","arguments":"{\"title\":"}}]}),Value::Null)).unwrap();
        decoder
            .event(&chunk(
                json!({"tool_calls":[{"index":0,"function":{"arguments":"\"hello\"}"}}]}),
                json!("tool_calls"),
            ))
            .unwrap();
        decoder.done().unwrap();
        let result = decoder.finish().unwrap();
        assert_eq!(result.output[0]["arguments"], "{\"title\":\"hello\"}");
        let mut incomplete = Decoder::default();
        incomplete
            .event(&chunk(json!({"content":"x"}), json!("stop")))
            .unwrap();
        assert_eq!(
            incomplete.finish().unwrap_err().kind,
            Failure::IncompleteStream
        );
    }
    #[test]
    fn truncation_duplicate_calls_and_cross_provider_history_fail_closed() {
        let mut decoder = Decoder::default();
        assert_eq!(
            decoder
                .event(&chunk(json!({}), json!("length")))
                .unwrap_err()
                .kind,
            Failure::Truncated
        );
        assert!(body(
            "local:8b",
            "system",
            &[json!({"type":"reasoning","encrypted_content":"secret"})],
            &[],
            100,
            None
        )
        .is_err());
        assert!(body(
            "local:8b",
            "system",
            &[json!({"type":"function_call_output","call_id":"missing","output":"x"})],
            &[],
            100,
            None
        )
        .is_err());
    }
    #[test]
    fn roundtrip_tools_and_images() {
        let input = vec![
            json!({"role":"user","content":[{"type":"input_image","image_url":"data:image/png;base64,abc"}]}),
            json!({"type":"function_call","call_id":"t1","name":"game__show","arguments":"{}"}),
            json!({"type":"function_call_output","call_id":"t1","output":"ok"}),
        ];
        let b = body(
            "org/local:8b",
            "system",
            &input,
            &[json!({"type":"function","name":"game__show","parameters":{"type":"object"}})],
            100,
            None,
        )
        .unwrap();
        assert_eq!(b["messages"][1]["content"][0]["type"], "image_url");
        assert_eq!(b["messages"][3]["tool_call_id"], "t1");
        assert_eq!(b["tools"][0]["function"]["name"], "game__show");
    }
}
