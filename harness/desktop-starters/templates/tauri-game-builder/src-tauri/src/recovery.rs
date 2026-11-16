use anyhow::{ensure, Result};
use openworker_sdk::{Harness, Session};
use serde_json::{json, Value};
use std::{collections::HashMap, io::Write, path::Path};

pub fn load(path: &Path) -> Result<Value> {
    match std::fs::read(path) {
        Ok(bytes) => Ok(serde_json::from_slice(&bytes)?),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(Value::Null),
        Err(e) => Err(e.into()),
    }
}
pub fn save(path: &Path, value: &Value) -> Result<()> {
    let parent = path.parent().unwrap();
    std::fs::create_dir_all(parent)?;
    let mut file = tempfile::NamedTempFile::new_in(parent)?;
    serde_json::to_writer(&mut file, value)?;
    file.flush()?;
    file.as_file().sync_all()?;
    file.persist(path)?;
    Ok(())
}
#[derive(Default)]
pub struct Projection {
    pub messages: Vec<String>,
    pub html: String,
    calls: HashMap<String, String>,
    text_bytes: usize,
}
impl Projection {
    pub fn accept(&mut self, item: &Value) -> Result<()> {
        if matches!(item["role"].as_str(), Some("user" | "assistant"))
            && item["runtime_notice"] != true
        {
            let text = if let Some(s) = item["content"].as_str() {
                s.into()
            } else {
                item["content"]
                    .as_array()
                    .into_iter()
                    .flatten()
                    .filter(|p| {
                        matches!(
                            p["type"].as_str(),
                            Some("text" | "input_text" | "output_text")
                        )
                    })
                    .filter_map(|p| p["text"].as_str())
                    .collect::<String>()
            };
            self.text_bytes += text.len();
            ensure!(
                self.text_bytes <= 10_000_000,
                "Conversation exceeds starter display limit; paginate/export history"
            );
            if !text.is_empty() {
                self.messages.push(text);
            }
        }
        if item["type"] == "function_call" && item["name"] == "game__show_preview" {
            let args = decode(&item["arguments"]);
            if let (Some(id), Some(html)) = (item["call_id"].as_str(), args["html"].as_str()) {
                if html.chars().count() <= 100000 {
                    self.calls.insert(id.into(), html.into());
                }
            }
            ensure!(self.calls.len() <= 64, "Invalid preview call history");
        }
        if item["type"] == "function_call_output" {
            if let Some(html) = item["call_id"]
                .as_str()
                .and_then(|id| self.calls.remove(id))
            {
                let output = decode(&item["output"]);
                if output["isError"] == false && output["structuredContent"]["shown"] == true {
                    self.html = html;
                }
            }
        }
        Ok(())
    }
}
fn decode(value: &Value) -> Value {
    value.as_str().map_or_else(
        || value.clone(),
        |s| serde_json::from_str(s).unwrap_or(Value::Null),
    )
}

pub async fn recover(
    harness: &Harness,
    session: Option<&Session>,
    preferences: Value,
) -> Result<Value> {
    let Some(session) = session else {
        return Ok(json!({"messages":[],"html":"","settings":preferences}));
    };
    let snapshot = harness.client().snapshot(session.id()).await?;
    let mut projection = Projection::default();
    let mut after = 0;
    let mut target = None;
    loop {
        let page = harness
            .client()
            .request(
                "history/read",
                json!({"session_id":session.id(),"after":after}),
            )
            .await?;
        let target = *target.get_or_insert(
            page["total_items"]
                .as_u64()
                .ok_or_else(|| anyhow::anyhow!("Invalid history total"))?,
        );
        for entry in page["items"]
            .as_array()
            .ok_or_else(|| anyhow::anyhow!("Invalid history page"))?
        {
            if entry["index"].as_u64().unwrap_or(u64::MAX) < target {
                projection.accept(&entry["item"])?;
            }
        }
        let cursor = page["cursor"]
            .as_u64()
            .ok_or_else(|| anyhow::anyhow!("Invalid history cursor"))?;
        if cursor >= target {
            break;
        }
        ensure!(cursor > after, "History did not advance");
        after = cursor;
    }
    let selected = &snapshot["session"]["model_selection"];
    let providers = harness.client().request("provider/list", json!({})).await?;
    let provider = providers["connections"]
        .as_array()
        .into_iter()
        .flatten()
        .find(|p| p["id"] == selected["connection"]);
    let settings = provider.map_or(preferences, |p|json!({"provider":p["id"],"api":p["api"],"baseUrl":p["base_url"],"model":selected["model"]}));
    Ok(json!({"messages":projection.messages,"html":projection.html,"settings":settings}))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn only_confirmed_effects_restore_preview() {
        let mut view = Projection::default();
        view.accept(&json!({"type":"function_call","name":"game__show_preview","call_id":"a","arguments":{"html":"approved"}})).unwrap();
        assert!(view.html.is_empty());
        view.accept(&json!({"type":"function_call_output","call_id":"a","output":{"isError":false,"structuredContent":{"shown":true}}})).unwrap();
        view.accept(&json!({"type":"function_call","name":"game__show_preview","call_id":"b","arguments":{"html":"denied"}})).unwrap();
        view.accept(
            &json!({"type":"function_call_output","call_id":"b","output":{"isError":true}}),
        )
        .unwrap();
        assert_eq!(view.html, "approved");
        view.accept(&json!({"role":"user","content":"hello"}))
            .unwrap();
        view.accept(&json!({"role":"assistant","content":[{"type":"output_text","text":"world"}]}))
            .unwrap();
        assert_eq!(view.messages, vec!["hello", "world"]);
    }
}
