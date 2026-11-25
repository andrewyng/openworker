//! Ephemeral context for one assigned turn. Only a committed proposal can
//! extend the cached prefix between worker updates.
use super::*;
use sha2::{Digest, Sha256};

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct Update {
    pub session: String,
    pub turn: String,
    pub seq: u64,
    // A snapshot has no base and carries the full instructions, tools and history.
    pub base: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub instructions: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub tools: Option<Vec<Value>>,
    pub history: Vec<Value>,
}

fn signature<T: Serialize + ?Sized>(v: &T) -> Result<[u8; 32]> {
    let mut hash = Sha256::new();
    serde_json::to_writer(&mut hash, v)?;
    Ok(hash.finalize().into())
}

// Keep only fingerprints on the worker. Recheck the effective input after
// safety processing, attachment hydration and compaction on every attempt.
#[cfg(any(not(windows), test))]
#[derive(Default)]
pub(super) struct Sender {
    seq: u64,
    history: Vec<[u8; 32]>,
    instructions: Option<[u8; 32]>,
    tools: Option<[u8; 32]>,
}

#[cfg(any(not(windows), test))]
impl Sender {
    pub fn update(
        &mut self,
        session: &str,
        turn: &str,
        instructions: &str,
        tools: &[Value],
        history: &[Value],
    ) -> Result<Update> {
        let hashes = history.iter().map(signature).collect::<Result<Vec<_>>>()?;
        let append = self.seq > 0 && hashes.starts_with(&self.history);
        let start = if append { self.history.len() } else { 0 };
        let text_hash = signature(instructions)?;
        let tools_hash = signature(tools)?;
        let next = self
            .seq
            .checked_add(1)
            .context("Context sequence exhausted")?;
        let update = Update {
            session: session.into(),
            turn: turn.into(),
            seq: next,
            base: append.then_some(self.seq),
            instructions: (!append || self.instructions != Some(text_hash))
                .then(|| instructions.to_owned()),
            tools: (!append || self.tools != Some(tools_hash)).then(|| tools.to_vec()),
            history: history[start..].to_vec(),
        };
        self.seq = next;
        self.history = hashes;
        self.instructions = Some(text_hash);
        self.tools = Some(tools_hash);
        Ok(update)
    }

    pub fn committed(&mut self, proposal: Vec<[u8; 32]>) {
        self.history.extend(proposal);
    }

    pub fn proposal(items: &[Value]) -> Result<Vec<[u8; 32]>> {
        items.iter().map(signature).collect()
    }
}

// Count the encoded size of new data without serializing the cached prefix.
#[derive(Default)]
struct ByteCount(usize);
impl std::io::Write for ByteCount {
    fn write(&mut self, b: &[u8]) -> std::io::Result<usize> {
        self.0 = self.0.saturating_add(b.len());
        Ok(b.len())
    }
    fn flush(&mut self) -> std::io::Result<()> {
        Ok(())
    }
}
fn bytes<T: Serialize + ?Sized>(v: &T) -> Result<usize> {
    let mut n = ByteCount::default();
    serde_json::to_writer(&mut n, v)?;
    Ok(n.0)
}
fn history_bytes(items: &[Value]) -> Result<usize> {
    Ok(bytes(items)?.saturating_sub(2) + usize::from(!items.is_empty()))
}

#[derive(Default)]
pub(super) struct Receiver {
    pub seq: u64,
    pub instructions: String,
    pub tools: Vec<Value>,
    pub history: Vec<Value>,
    text_bytes: usize,
    tool_bytes: usize,
    message_bytes: usize,
    last_update: Option<[u8; 32]>,
    started: bool,
    needs_snapshot: bool,
}

impl Receiver {
    // false requests a fresh snapshot. A conflicting retransmission fails closed.
    pub fn apply(&mut self, u: Update, session: &str, turn: &str) -> Result<bool> {
        ensure!(
            u.session == session && u.turn == turn && u.seq > 0,
            "Invalid context assignment"
        );
        let digest = signature(&u)?;
        if u.seq == self.seq {
            ensure!(
                self.last_update == Some(digest),
                "Conflicting context retransmission"
            );
            return Ok(!self.needs_snapshot);
        }
        if u.seq < self.seq
            || (self.needs_snapshot && u.base.is_some())
            || u.base
                .is_some_and(|b| self.seq == 0 || b != self.seq || b.checked_add(1) != Some(u.seq))
        {
            self.needs_snapshot = true;
            return Ok(false);
        }
        let replace = u.base.is_none();
        ensure!(
            !replace || (u.instructions.is_some() && u.tools.is_some()),
            "Incomplete context snapshot"
        );
        let text_bytes = match &u.instructions {
            Some(v) => bytes(v)?,
            None => self.text_bytes,
        };
        let tool_bytes = match &u.tools {
            Some(v) => bytes(v)?,
            None => self.tool_bytes,
        };
        let message_bytes =
            history_bytes(&u.history)? + if replace { 0 } else { self.message_bytes };
        ensure!(
            text_bytes + tool_bytes + message_bytes <= CONTEXT_LIMIT,
            "Cached context too large"
        );
        if let Some(text) = u.instructions {
            self.instructions = text;
        }
        if let Some(tools) = u.tools {
            self.tools = tools;
        }
        if replace {
            self.history = u.history;
        } else {
            self.history.extend(u.history);
        }
        self.text_bytes = text_bytes;
        self.tool_bytes = tool_bytes;
        self.message_bytes = message_bytes;
        self.seq = u.seq;
        self.last_update = Some(digest);
        self.started = false;
        self.needs_snapshot = false;
        Ok(true)
    }
    pub fn start(&mut self, seq: u64) -> Result<()> {
        ensure!(
            seq != 0 && seq == self.seq && !self.started && !self.needs_snapshot,
            "Unexpected or repeated model start"
        );
        self.started = true;
        Ok(())
    }
    pub fn committed(&mut self, items: Vec<Value>) -> Result<()> {
        let addition = history_bytes(&items)?;
        ensure!(
            self.text_bytes + self.tool_bytes + self.message_bytes + addition <= CONTEXT_LIMIT,
            "Cached proposal too large"
        );
        self.message_bytes += addition;
        self.history.extend(items);
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sends_only_new_canonical_content_and_replaces_rewritten_history() {
        let mut sender = Sender::default();
        let mut runner = Receiver::default();
        let tools = vec![json!({"name":"read_file"})];
        let mut history = vec![json!({"role":"user","content":"x".repeat(65536)})];
        let first = sender.update("s", "t", "base", &tools, &history).unwrap();
        let snapshot_bytes = bytes(&first).unwrap();
        assert!(runner.apply(first, "s", "t").unwrap());
        let proposed = vec![json!({"type":"function_call","call_id":"c1"})];
        sender.committed(Sender::proposal(&proposed).unwrap());
        runner.committed(proposed.clone()).unwrap();
        history.extend(proposed);
        let result = json!({"type":"function_call_output","call_id":"c1","output":"ok"});
        history.push(result.clone());
        let delta = sender.update("s", "t", "base", &tools, &history).unwrap();
        assert!(bytes(&delta).unwrap() < snapshot_bytes / 100);
        assert_eq!(delta.base, Some(1));
        assert_eq!(delta.history, [result]);
        assert!(delta.tools.is_none() && delta.instructions.is_none());
        assert!(runner.apply(delta, "s", "t").unwrap());
        assert_eq!(runner.history, history);
        let changed = sender.update("s", "t", "updated", &[], &history).unwrap();
        assert_eq!(changed.tools, Some(vec![]));
        assert!(changed.history.is_empty());
        assert!(runner.apply(changed, "s", "t").unwrap());
        assert!(runner.tools.is_empty());
        assert_eq!(runner.instructions, "updated");
        let compacted = vec![json!({"role":"user","content":"summary"})];
        let reset = sender.update("s", "t", "base", &tools, &compacted).unwrap();
        assert!(reset.base.is_none());
        assert!(runner.apply(reset, "s", "t").unwrap());
        assert_eq!(runner.history, compacted);
    }

    #[test]
    fn cache_replay_gap_and_bounds_are_checked_before_mutation() {
        let mut sender = Sender::default();
        let mut runner = Receiver::default();
        let snapshot = || {
            serde_json::from_value(json!({"session":"s","turn":"t","seq":1,"instructions":"base","tools":[],"history":[]})).unwrap()
        };
        runner.apply(snapshot(), "s", "t").unwrap();
        runner.start(1).unwrap();
        assert!(runner.apply(snapshot(), "s", "t").unwrap());
        assert!(runner.start(1).is_err());
        let mut conflicting: Update = snapshot();
        conflicting.instructions = Some("altered".into());
        assert!(runner.apply(conflicting, "s", "t").is_err());
        assert!(runner.apply(snapshot(), "other", "t").is_err());
        assert!(runner.apply(snapshot(), "s", "other").is_err());
        sender.update("s", "t", "base", &[], &[]).unwrap();
        sender.update("s", "t", "base", &[], &[]).unwrap();
        let missing = sender
            .update("s", "t", "base", &[], &[json!("new")])
            .unwrap();
        assert!(!runner.apply(missing, "s", "t").unwrap());
        assert_eq!(runner.seq, 1);
        assert!(runner.start(1).is_err());
        let mut repaired: Update = snapshot();
        repaired.seq = 3;
        assert!(runner.apply(repaired, "s", "t").unwrap());
        runner.message_bytes = CONTEXT_LIMIT;
        let too_large = serde_json::from_value(
            json!({"session":"s","turn":"t","seq":4,"base":3,"history":["x"]}),
        )
        .unwrap();
        assert!(runner.apply(too_large, "s", "t").is_err());
        assert!(runner.history.is_empty() && runner.seq == 3);
    }
}
