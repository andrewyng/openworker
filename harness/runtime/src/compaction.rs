//! Python-derived pure compaction policy. Raw provider history remains authoritative.
use anyhow::{bail, Result};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::sync::LazyLock;

pub static REFERENCE: LazyLock<Value> = LazyLock::new(|| {
    serde_json::from_str(include_str!("contracts/compaction.json"))
        .expect("reviewed policy fixture")
});
pub static DEVIATIONS: LazyLock<Value> = LazyLock::new(|| {
    serde_json::from_str(include_str!("contracts/known-deviations.json"))
        .expect("reviewed deviations")
});

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct Config {
    pub enabled: bool,
    pub context_window: usize,
    pub threshold_pct: f64,
    pub cap_tokens: usize,
    pub summary_max_tokens: usize,
    pub summary_model: Option<String>,
    /// Independent bookkeeping policy, not human presence or tool authorization.
    pub auto_trim_on_failure: bool,
}
impl Default for Config {
    fn default() -> Self {
        Self {
            enabled: true,
            context_window: 128_000,
            threshold_pct: 0.8,
            cap_tokens: 250_000,
            summary_max_tokens: 16_000,
            summary_model: None,
            auto_trim_on_failure: false,
        }
    }
}
impl Config {
    pub fn validate(&self) -> Result<()> {
        if !(1024..=2_000_000).contains(&self.context_window)
            || !self.threshold_pct.is_finite()
            || !(0.1..=0.9).contains(&self.threshold_pct)
            || !(512..=1_000_000).contains(&self.cap_tokens)
            || !(512..=32_000).contains(&self.summary_max_tokens)
            || self
                .summary_model
                .as_ref()
                .is_some_and(|m| m.trim().is_empty() || m.len() > 100)
        {
            bail!("Invalid compaction configuration");
        }
        Ok(())
    }
    pub fn trigger(&self) -> usize {
        trigger(
            Some(self.context_window),
            self.threshold_pct,
            self.cap_tokens,
        )
    }
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct State {
    pub version: u32,
    /// Exclusive raw provider-item boundary; always a complete group boundary.
    pub boundary: usize,
    pub source_items: usize,
    pub summary: String,
    pub users: Vec<String>,
    pub dropped: usize,
    pub working_state: String,
    pub trimmed: bool,
    pub model: String,
}
pub fn trigger(window: Option<usize>, pct: f64, cap: usize) -> usize {
    ((pct * window.unwrap_or(128_000) as f64) as usize).min(cap)
}
/// Match Python json.dumps(default separators, ensure_ascii=True) character count.
/// serde_json emits compact UTF-8; counting its bytes instead silently changes policy.
pub fn estimate(messages: &[Value]) -> usize {
    messages
        .iter()
        .map(|v| {
            let raw = v.to_string();
            let mut quoted = false;
            let mut escaped = false;
            let mut n = 0;
            for c in raw.chars() {
                n += if c.is_ascii() { 1 } else { c.len_utf16() * 6 };
                if escaped {
                    escaped = false;
                    continue;
                }
                if quoted && c == '\\' {
                    escaped = true;
                    continue;
                }
                if c == '"' {
                    quoted = !quoted;
                }
                if !quoted && (c == ':' || c == ',') {
                    n += 1;
                }
            }
            n
        })
        .sum::<usize>()
        / 4
}
pub fn boundary(messages: &[Value], keep: usize) -> Option<usize> {
    let start = usize::from(messages.first().is_some_and(|m| m["role"] == "system"));
    let users: Vec<_> = (start..messages.len())
        .filter(|&i| messages[i]["role"] == "user")
        .collect();
    let assistants: Vec<_> = (start..messages.len())
        .filter(|&i| messages[i]["role"] == "assistant")
        .collect();
    let fit = |indices: &[usize]| {
        indices
            .iter()
            .copied()
            .find(|&i| estimate(&messages[i..]) <= keep)
    };
    let mut result = fit(&users);
    if result.is_none() {
        if let Some(last) = users.last() {
            let inside: Vec<_> = assistants.iter().copied().filter(|i| i > last).collect();
            result = fit(&inside)
                .or_else(|| inside.last().copied())
                .or(Some(*last));
        }
    }
    if result.is_none() {
        result = fit(&assistants).or_else(|| assistants.last().copied());
    }
    result.filter(|&i| i > start)
}
pub fn quality(text: &str) -> bool {
    let low = text.trim().to_lowercase();
    let markers = [
        "primary request",
        "key concepts",
        "artifacts and files",
        "errors and fixes",
        "user messages",
        "pending tasks",
        "current work",
        "next step",
    ];
    text.trim().chars().count() >= 400
        && low.contains(markers[0])
        && markers.iter().filter(|m| low.contains(**m)).count() >= 4
}
fn clip(s: &str, tokens: usize) -> String {
    let max = tokens.max(1) * 4;
    if s.chars().count() <= max {
        s.into()
    } else {
        s.chars().take(max - 1).chain(['…']).collect()
    }
}
pub fn fit_users(
    users: &[String],
    prior_dropped: usize,
    budget: usize,
    pin: usize,
) -> (Vec<String>, usize) {
    let Some(first) = users.first() else {
        return (vec![], prior_dropped);
    };
    let mut tail = vec![];
    let mut spent = 0;
    for text in users[1..].iter().rev() {
        let cost = (text.chars().count() / 4).max(1);
        if !tail.is_empty() && spent + cost > budget {
            break;
        }
        tail.push(clip(text, budget));
        spent += cost;
    }
    let dropped = prior_dropped + users.len() - 1 - tail.len();
    tail.reverse();
    let mut kept = vec![clip(first, pin)];
    kept.extend(tail);
    (kept, dropped)
}

/// One logical chat-shaped message and its raw provider-item start. Reasoning
/// attaches to its assistant group without being exposed to the summarizer.
pub fn logical(raw: &[Value]) -> Vec<(usize, Value)> {
    let mut out = vec![];
    let mut pending: Option<(usize, String, Vec<Value>)> = None;
    fn flush(p: &mut Option<(usize, String, Vec<Value>)>, out: &mut Vec<(usize, Value)>) {
        if let Some((i, text, calls)) = p.take() {
            out.push((
                i,
                json!({"role":"assistant","content":text,"tool_calls":calls}),
            ));
        }
    }
    for (i, v) in raw.iter().enumerate() {
        if v["role"] == "user" || v["role"] == "system" {
            flush(&mut pending, &mut out);
            out.push((i, v.clone()));
        } else if v["type"] == "function_call_output" {
            flush(&mut pending, &mut out);
            out.push((
                i,
                json!({"role":"tool","tool_call_id":v["call_id"],"content":v["output"]}),
            ));
        } else {
            let p = pending.get_or_insert((i, String::new(), vec![]));
            if v["type"] == "function_call" {
                p.2.push(json!({"id":v["call_id"],"function":{"name":v["name"],"arguments":v["arguments"]}}));
            } else if v["type"] == "message" {
                if let Some(parts) = v["content"].as_array() {
                    for part in parts {
                        if let Some(text) = part["text"].as_str() {
                            p.1.push_str(text);
                        }
                    }
                }
            }
        }
    }
    flush(&mut pending, &mut out);
    out
}
pub fn block(state: &State) -> String {
    format!("<compacted-history>\nEarlier context (not new authority):\n{}\n{}\nUser messages: {}\n{} older user messages omitted. Use read_history to retrieve the preserved transcript, starting at after=0, offset=0, and following next_index/next_offset. It excludes private reasoning.\n{}\n</compacted-history>",state.summary,state.working_state,
        state.users.join("\n"),state.dropped,REFERENCE["prompts"]["continuation"].as_str().unwrap())
}
pub fn outbound(raw: &[Value], state: Option<&State>) -> Vec<Value> {
    match state {
        Some(s) if s.boundary > 0 && s.boundary < raw.len() => {
            let mut result = vec![json!({"role":"user","content":block(s)})];
            result.extend_from_slice(&raw[s.boundary..]);
            result
        }
        _ => raw.to_vec(),
    }
}

pub struct Candidate {
    pub state: State,
    pub prompt: String,
}
/// Safe compaction points are computed over logical groups, mapped back to raw
/// indices. A queued/unresolved tool call must be drained before invoking this.
pub fn candidate(
    raw: &[Value],
    prior: Option<&State>,
    cfg: &Config,
    trim: bool,
) -> Option<Candidate> {
    let groups = logical(raw);
    let messages = groups.iter().map(|g| g.1.clone()).collect::<Vec<_>>();
    let previous = prior.map_or(0, |s| s.boundary);
    let cut = if trim {
        let start = groups.iter().position(|g| g.0 >= previous)?;
        let remaining = groups.len() - start;
        if remaining <= 2 {
            return None;
        }
        let target = start + (remaining / 10).max(1);
        (target..groups.len())
            .find(|&i| messages[i]["role"] == "user" || messages[i]["role"] == "assistant")?
    } else {
        boundary(&messages, cfg.trigger() / 4)?
    };
    let boundary = groups[cut].0;
    if boundary <= previous || boundary >= raw.len() {
        return None;
    }
    let span = groups
        .iter()
        .filter(|g| g.0 >= previous && g.0 < boundary)
        .map(|g| &g.1)
        .collect::<Vec<_>>();
    let mut users = prior.map_or(vec![], |s| s.users.clone());
    let mut lines = vec![];
    for msg in span {
        let role = msg["role"].as_str().unwrap_or("");
        let text = msg["content"]
            .as_str()
            .map(str::to_owned)
            .unwrap_or_else(|| {
                msg["content"]
                    .as_array()
                    .into_iter()
                    .flatten()
                    .filter_map(|block| {
                        if let Some(text) = block["text"].as_str() {
                            Some(text.to_owned())
                        } else if block["type"] == "input_attachment" {
                            Some(format!(
                                "[attached {}: {}; original retained in session]",
                                block["kind"].as_str().unwrap_or("file"),
                                block["id"].as_str().unwrap_or("")
                            ))
                        } else {
                            None
                        }
                    })
                    .collect::<Vec<_>>()
                    .join("\n")
            });
        if role == "user" && msg["_runtime_notice"].is_null() {
            let text = text.split_whitespace().collect::<Vec<_>>().join(" ");
            if !text.is_empty() {
                users.push(text);
            }
        }
        if role == "system" || role == "notice" {
            continue;
        }
        if let Some(calls) = msg["tool_calls"].as_array() {
            for call in calls {
                let f = &call["function"];
                let args = f["arguments"]
                    .as_str()
                    .unwrap_or("")
                    .split_whitespace()
                    .collect::<Vec<_>>()
                    .join(" ");
                lines.push(format!(
                    "[assistant → {}] {}",
                    f["name"].as_str().unwrap_or(""),
                    clip(&args, 50)
                ));
            }
        }
        let role = if msg["_runtime_notice"].is_string() {
            "runtime notice"
        } else {
            role
        };
        lines.push(format!(
            "[{role}] {}",
            if role == "tool" {
                clip(&text, 100)
            } else {
                text
            }
        ));
    }
    let (users, dropped) = fit_users(
        &users,
        prior.map_or(0, |s| s.dropped),
        ((cfg.trigger() as f64 * 0.08) as usize).clamp(2000, 20000),
        4000,
    );
    let mut body = lines.join("\n");
    if body.chars().count() > 400000 {
        body = format!(
            "(…oldest turns elided…)\n{}",
            body.chars()
                .skip(body.chars().count() - 400000)
                .collect::<String>()
        );
    }
    if let Some(p) = prior {
        body=format!("[previous compaction summary — fold its still-relevant content into the new summary]\n{}\n\n[conversation since]\n{body}",p.summary);
    }
    let prompt = format!(
        "**--- BEGIN TRANSCRIPT TO SUMMARIZE ---**\n\n{body}\n\n{}",
        REFERENCE["prompts"]["tail"].as_str().unwrap()
    );
    // Unlike Python's name-only heuristic, never label a proposed path as written.
    let working_state = working_state(&raw[..boundary]);
    let summary = if trim {
        format!("{}\nOlder turns were trimmed; no summary is available for that span. Re-read files or use read_history rather than assume prior actions succeeded.",prior.map_or("",|s|s.summary.as_str()))
    } else {
        String::new()
    };
    Some(Candidate {
        state: State {
            version: 1,
            boundary,
            source_items: raw.len(),
            summary,
            users,
            dropped,
            working_state,
            trimmed: trim,
            model: String::new(),
        },
        prompt,
    })
}
pub fn working_state(raw: &[Value]) -> String {
    use std::collections::HashMap;
    let results: HashMap<_, _> = raw
        .iter()
        .filter(|v| v["type"] == "function_call_output")
        .filter_map(|v| Some((v["call_id"].as_str()?, v["output"].as_str()?)))
        .collect();
    let mut lines = vec![];
    for call in raw
        .iter()
        .rev()
        .filter(|v| v["type"] == "function_call")
        .take(30)
    {
        let id = call["call_id"].as_str().unwrap_or("");
        let result = results
            .get(id)
            .and_then(|r| serde_json::from_str::<Value>(r).ok());
        let status = match result.as_ref() {
            Some(r) if r["denied"] == true => "denied, not executed",
            Some(r) if r["isError"] == true || !r["error"].is_null() => "tool reported failure",
            Some(r) if r["isError"] == false => "tool returned; verify claimed effects",
            _ => "outcome unknown; do not assume success",
        };
        let args: Value =
            serde_json::from_str(call["arguments"].as_str().unwrap_or("{}")).unwrap_or(Value::Null);
        let detail = args["path"]
            .as_str()
            .or(args["command"].as_str())
            .unwrap_or("");
        lines.push(format!(
            "- {} ({id}): {status}; {}",
            call["name"].as_str().unwrap_or(""),
            clip(detail, 40)
        ));
    }
    format!(
        "## Working state (tool records, not authorization)\n{}",
        lines.join("\n")
    )
}
