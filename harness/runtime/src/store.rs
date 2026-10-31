use anyhow::{bail, Context, Result};
use fs2::FileExt;
use rusqlite::{params, Connection, OpenFlags, OptionalExtension, Transaction};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{fs::OpenOptions, path::Path};

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Interaction {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub requested_at_ms: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub resolved_at_ms: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub grant_id: Option<String>,
    pub id: String,
    pub turn_id: String,
    pub kind: String,
    pub call: Value,
    pub bundle_digest: String,
    pub schema_digest: String,
    pub response: Option<String>,
    #[serde(default)]
    pub origin: Option<String>,
    #[serde(default)]
    pub server: Option<String>,
    #[serde(default)]
    pub remote_tool: Option<String>,
    #[serde(default)]
    pub policy_digest: String,
    pub resource_policy_digest: String,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Turn {
    #[serde(default)]
    pub budget: crate::budget::State,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub observation: Option<TurnObservation>,
    pub id: String,
    pub submission_id: String,
    pub text: String,
    pub status: String,
    pub iterations: u32,
    pub queue: Vec<Value>,
    #[serde(default)]
    pub provider_attempts: u32,
    #[serde(default)]
    pub compactions: u32,
    #[serde(default)]
    pub continuations: u32,
    #[serde(default)]
    pub reviewer_denials: u32,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct TurnObservation {
    pub first_started_at_ms: u64,
    pub last_segment: crate::telemetry::TraceContext,
    pub segments: u32,
}
pub fn now_ms() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
        .min(i64::MAX as u128) as u64
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Session {
    #[serde(default)]
    pub agent_team: Option<crate::agent_team::Membership>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub environment: Option<crate::environment::Binding>,
    /// Adapter concurrency token, not a model-visible permission or identity.
    /// SQLite's exclusive local adapter leaves this at zero.
    #[serde(default, skip_serializing_if = "revision_is_zero")]
    pub storage_revision: i64,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub approval_grants: Vec<crate::permissions::SessionGrant>,
    pub id: String,
    pub name: String,
    #[serde(default)]
    pub model: String,
    #[serde(default)]
    pub model_provider: crate::provider::Kind,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub model_selection: Option<crate::connections::Selection>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub reviewer_selection: Option<crate::connections::Selection>,
    #[serde(default)]
    pub scripted: bool,
    pub workspace: String,
    pub coworker: String,
    pub bundle_digest: String,
    pub schema_digest: Option<String>,
    // Canonical items live in append-only rows, never in session metadata.
    #[serde(default, skip_serializing)]
    pub history: Vec<Value>,
    pub interactions: Vec<Interaction>,
    pub turn: Option<Turn>,
    pub sandbox: Option<String>,
    pub closed: bool,
    #[serde(default)]
    pub compaction_config: crate::compaction::Config,
    #[serde(default)]
    pub compaction: Option<crate::compaction::State>,
    #[serde(default)]
    pub last_usage: Option<crate::provider::Usage>,
    #[serde(default)]
    pub provider_blocked: bool,
    #[serde(default)]
    pub compaction_action: Option<String>,
    #[serde(default)]
    pub permissions: crate::permissions::Config,
    #[serde(default)]
    pub execution_backend: crate::execution::Backend,
    #[serde(default)]
    pub direct_processes: std::collections::BTreeMap<String, crate::direct::Identity>,
    #[serde(default)]
    pub tool_policy: crate::tool_policy::ResolvedPolicy,
    #[serde(default)]
    pub package: crate::package::Manifest,
    #[serde(default)]
    pub skills: Vec<String>,
    #[serde(default)]
    pub loaded_skills: std::collections::BTreeSet<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub plan: Option<crate::planning::Plan>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub schedules: Vec<crate::assistant_tools::Schedule>,
    pub resource_policy: crate::resource_policy::Resolved,
}
fn revision_is_zero(revision: &i64) -> bool {
    *revision == 0
}
impl Session {
    pub fn enforcement(&self) -> &'static str {
        if self.resource_policy.servers.is_empty() {
            return self.execution_backend.enforcement();
        }
        let host = self
            .resource_policy
            .servers
            .values()
            .any(|p| *p == crate::resource_policy::Placement::Host);
        match (host, self.resource_policy.needs_sandbox()) {
            (true, true) => "mixed",
            (true, false) => "none",
            _ => "full",
        }
    }
    pub fn tool_backend(&self, server: Option<&str>) -> crate::execution::Backend {
        server.map_or(self.execution_backend, |id| {
            self.resource_policy.backend(id)
        })
    }
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Event {
    pub event_id: i64,
    pub session_id: String,
    pub turn_id: Option<String>,
    #[serde(rename = "type")]
    pub kind: String,
    pub data: Value,
}
/// Persistence boundary consumed by the existing native runtime.
///
/// This first seam preserves the synchronous local SDK contract. Implementations
/// must atomically commit canonical state/history with each returned event and
/// refuse modified history prefixes. Distributed adapters additionally need owner
/// fencing and bounded database I/O; implementing this trait alone does not make
/// the runtime safe for shared workers. `recover` may only touch sessions owned
/// exclusively by this store instance. No automatic replay of tool effects.
pub trait StateStore: Send {
    fn supports_agent_teams(&self) -> bool {
        false
    }
    fn agent_team(
        &mut self,
        _sid: &str,
        _operation: &str,
        _args: &Value,
        _call: Option<(&str, &Value)>,
    ) -> Result<Value> {
        bail!("Agent team storage unavailable")
    }
    fn assistant_call(
        &mut self,
        _sid: &str,
        _tid: &str,
        _call: &Value,
        _args: &Value,
    ) -> Result<Event> {
        bail!("This storage adapter does not implement memory/scheduling tools")
    }
    /// Latest durable SDK run receipt, optionally for a particular submission.
    /// Receipts use the ordinary fenced journal, not a second persistence path.
    fn agent_run(&self, _sid: &str, _submission: Option<&str>) -> Result<Option<Value>> {
        bail!("SDK run receipts unsupported by this store")
    }
    fn team_create(&mut self, _request: crate::team_service::Create) -> Result<Value> {
        bail!("Team storage unavailable")
    }
    fn team_snapshot(&self, _id: &str) -> Result<Value> {
        bail!("Team storage unavailable")
    }
    fn team_command(&mut self, _id: &str, _command: crate::team_service::Command) -> Result<Value> {
        bail!("Team storage unavailable")
    }
    fn team_events(&self, _id: &str, _after: i64) -> Result<Value> {
        bail!("Team storage unavailable")
    }
    fn team_tick(&mut self) -> Result<()> {
        Ok(())
    }
    fn team_next(&self, _sid: &str) -> Result<Option<(String, String)>> {
        Ok(None)
    }
    fn team_cancellations(&self) -> Result<Vec<(String, String)>> {
        Ok(vec![])
    }
    fn team_cancel_settled(&mut self, _sid: &str, _tid: &str) -> Result<()> {
        Ok(())
    }
    fn supports_teams(&self) -> bool {
        false
    }
    fn team_context(&self, _sid: &str) -> Result<Option<crate::teams::Context>> {
        Ok(None)
    }
    fn team_call(
        &mut self,
        _sid: &str,
        _turn_id: &str,
        _call: &Value,
        _args: &Value,
    ) -> Result<Event> {
        bail!("Teams are not supported by this store")
    }
    fn environment(&self, _id: &str) -> Result<crate::environment::Record> {
        bail!("Remote environments unsupported by this store")
    }
    fn reserve_attachment(
        &mut self,
        _sid: &str,
        _server: &str,
    ) -> Result<(crate::environment::Attachment, bool)> {
        bail!("Remote attachments unsupported by this store")
    }
    fn settle_attachment(
        &mut self,
        _sid: &str,
        _id: &str,
        _released: bool,
        _unknown: bool,
    ) -> Result<()> {
        bail!("Remote attachments unsupported by this store")
    }
    fn abandon_attachments(&mut self, _sid: &str) -> Result<()> {
        bail!("Remote attachments unsupported by this store")
    }
    fn uncertain_calls(&self, _sid: &str) -> Result<Vec<Value>> {
        Ok(vec![])
    }
    fn control(&self, _sid: &str, _key: &str) -> Result<crate::control::Record> {
        bail!("Durable server controls are not supported by this store")
    }
    fn settle_control(
        &mut self,
        _sid: &str,
        _key: &str,
        _rejected: bool,
        _reason: &str,
    ) -> Result<Event> {
        bail!("Durable server controls are not supported by this store")
    }
    fn get(&self, id: &str) -> Result<Session>;
    fn metadata(&self, id: &str) -> Result<Session>;
    fn list(&self) -> Result<Vec<Session>>;
    fn list_metadata(&self) -> Result<Vec<Session>>;
    fn recent_messages(&self, sid: &str) -> Result<(Vec<Value>, bool)>;
    fn save(&mut self, s: &Session, kind: &str, data: Value) -> Result<Event>;
    fn append_event(&mut self, sid: &str, kind: &str, data: Value) -> Result<Event>;
    fn start_attempt(&mut self, sid: &str, id: &str, number: u32) -> Result<Event>;
    fn attempt_delta(&mut self, sid: &str, id: &str, text: &str) -> Result<Event>;
    fn finish_attempt(
        &mut self,
        sid: &str,
        id: &str,
        status: &str,
        kind: Option<crate::provider::Failure>,
    ) -> Result<Event>;
    fn interrupt_attempts(&mut self, sid: &str) -> Result<()>;
    fn attempts(&self, sid: &str) -> Result<Vec<Value>>;
    fn history_page(&self, sid: &str, after: usize) -> Result<Value>;
    fn transcript_excerpt(&self, sid: &str, after: usize, offset: usize) -> Result<Value>;
    fn accept(
        &mut self,
        sid: &str,
        key: &str,
        text: &str,
    ) -> Result<(Session, bool, Option<Event>)>;
    fn accept_with_limits(
        &mut self,
        sid: &str,
        key: &str,
        text: &str,
        limits: Option<crate::budget::Limits>,
    ) -> Result<(Session, bool, Option<Event>)> {
        anyhow::ensure!(
            limits.is_none(),
            "Storage adapter does not support turn limits"
        );
        self.accept(sid, key, text)
    }
    fn cursor(&self, sid: &str) -> Result<i64>;
    fn has_submission(&self, sid: &str, key: &str) -> Result<bool>;
    fn events(&self, sid: &str, after: i64) -> Result<Vec<Event>>;
    fn recover(&mut self) -> Result<()>;
}

pub type SharedStore = std::sync::Arc<std::sync::Mutex<Box<dyn StateStore>>>;
#[path = "scoped_store.rs"]
mod scoped_store;
pub use scoped_store::ScopedStore;

pub struct Store {
    db: Connection,
    _lock: Option<std::fs::File>,
}
#[path = "sqlite_agent_team.rs"]
mod sqlite_agent_team;
#[path = "sqlite_assistant.rs"]
mod sqlite_assistant;
#[path = "sqlite_teams.rs"]
mod sqlite_teams;
pub fn terminal(s: &str) -> bool {
    matches!(
        s,
        "completed" | "failed" | "cancelled" | "interrupted" | "truncated"
    )
}

impl Store {
    pub fn open(root: &Path) -> Result<Self> {
        let path = root.join("runtime.sqlite3");
        // Reject incompatible development stores before any chmod/WAL/schema write.
        if path.exists() {
            let check = Connection::open_with_flags(&path, OpenFlags::SQLITE_OPEN_READ_ONLY)?;
            let version: i64 = check.query_row("PRAGMA user_version", [], |r| r.get(0))?;
            let tables: i64 = check.query_row(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table'",
                [],
                |r| r.get(0),
            )?;
            if version != 7 && !(version == 0 && tables == 0) {
                bail!("Incompatible runtime state schema {version}; use a fresh state directory. M1 migration is intentionally unsupported; existing data was not reset.");
            }
        }
        crate::platform::private_directory(root)?;
        let lock = OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .truncate(false)
            .open(root.join("runtime.lock"))?;
        lock.try_lock_exclusive()
            .context("Another runtime owns this state directory")?;
        let db = Connection::open(path)?;
        Self::initialize(db, Some(lock))
    }
    /// The identical transactional adapter, with no session data written to disk.
    pub fn memory() -> Result<Self> {
        Self::initialize(Connection::open_in_memory()?, None)
    }
    fn initialize(db: Connection, lock: Option<std::fs::File>) -> Result<Self> {
        db.execute_batch("PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL;
            BEGIN IMMEDIATE;
            CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS history(session_id TEXT NOT NULL,seq INTEGER NOT NULL,data TEXT NOT NULL,PRIMARY KEY(session_id,seq));
            CREATE TABLE IF NOT EXISTS attempts(id TEXT PRIMARY KEY,session_id TEXT NOT NULL,turn_id TEXT NOT NULL,status TEXT NOT NULL,text TEXT NOT NULL,truncated INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS submissions(session_id TEXT, submission_id TEXT, text TEXT, turn_id TEXT, PRIMARY KEY(session_id,submission_id));
            CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT,session_id TEXT NOT NULL, turn_id TEXT,kind TEXT NOT NULL,data TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS session_events ON events(session_id,id);
            PRAGMA user_version=7;
            COMMIT;")?;
        sqlite_teams::migrate(&db)?;
        sqlite_assistant::migrate(&db)?;
        sqlite_agent_team::migrate(&db)?;
        Ok(Self { db, _lock: lock })
    }
    pub fn get(&self, id: &str) -> Result<Session> {
        let mut s = self.metadata(id)?;
        let mut q = self
            .db
            .prepare("SELECT data FROM history WHERE session_id=? ORDER BY seq")?;
        s.history = q
            .query_map([id], |r| r.get::<_, String>(0))?
            .map(|r| Ok(serde_json::from_str(&r?)?))
            .collect::<Result<Vec<_>>>()?;
        Ok(s)
    }
    pub fn metadata(&self, id: &str) -> Result<Session> {
        let raw: String = self
            .db
            .query_row("SELECT data FROM sessions WHERE id=?", [id], |r| r.get(0))
            .context("Session not found")?;
        Ok(serde_json::from_str(&raw)?)
    }
    pub fn list(&self) -> Result<Vec<Session>> {
        self.list_metadata()?
            .into_iter()
            .map(|s| self.get(&s.id))
            .collect()
    }
    pub fn list_metadata(&self) -> Result<Vec<Session>> {
        let mut q = self
            .db
            .prepare("SELECT data FROM sessions ORDER BY rowid DESC")?;
        let rows = q.query_map([], |r| r.get::<_, String>(0))?;
        rows.map(|r| Ok(serde_json::from_str(&r?)?)).collect()
    }
    pub fn recent_messages(&self, sid: &str) -> Result<(Vec<Value>, bool)> {
        let predicate = "session_id=? AND (json_extract(data,'$.role')='user' OR json_extract(data,'$.type')='message')";
        let count: usize = self.db.query_row(
            &format!("SELECT COUNT(*) FROM history WHERE {predicate}"),
            [sid],
            |r| r.get(0),
        )?;
        let mut q = self.db.prepare(&format!(
            "SELECT data FROM history WHERE {predicate} ORDER BY seq DESC LIMIT 100"
        ))?;
        let mut messages = vec![];
        let mut bytes = 0;
        for row in q.query_map([sid], |r| r.get::<_, String>(0))? {
            let raw = row?;
            bytes += raw.len();
            if bytes > 262144 {
                break;
            }
            messages.push(serde_json::from_str(&raw)?);
        }
        messages.reverse();
        let truncated = messages.len() < count;
        Ok((messages, truncated))
    }
    fn append_history(tx: &Transaction<'_>, s: &Session) -> Result<()> {
        let mut q = tx.prepare("SELECT seq,data FROM history WHERE session_id=? ORDER BY seq")?;
        let mut count = 0usize;
        let mut bytes = 0usize;
        for row in q.query_map([&s.id], |r| {
            Ok((r.get::<_, usize>(0)?, r.get::<_, String>(1)?))
        })? {
            let (seq, raw) = row?;
            if seq != count || s.history.get(seq) != Some(&serde_json::from_str::<Value>(&raw)?) {
                bail!("Canonical history is append-only; stale or modified prefix refused");
            }
            bytes += raw.len();
            count += 1;
        }
        drop(q);
        if s.history.len() > 200_000 {
            bail!("Session history item quota reached");
        }
        for (seq, item) in s.history.iter().enumerate().skip(count) {
            let raw = serde_json::to_string(item)?;
            bytes += raw.len();
            if raw.len() > 512_000 || bytes > 64 * 1024 * 1024 {
                bail!("Session history storage quota reached; existing history retained");
            }
            tx.execute("INSERT INTO history VALUES(?,?,?)", params![s.id, seq, raw])?;
        }
        Ok(())
    }
    pub fn save(&mut self, s: &Session, kind: &str, data: Value) -> Result<Event> {
        let tx = self.db.transaction()?;
        sqlite_teams::check(&tx, s, kind)?;
        Self::append_history(&tx, s)?;
        tx.execute(
            "INSERT INTO sessions VALUES(?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",
            params![s.id, serde_json::to_string(s)?],
        )?;
        sqlite_teams::finished(&tx, s)?;
        let tid = s.turn.as_ref().map(|t| t.id.clone());
        tx.execute(
            "INSERT INTO events(session_id,turn_id,kind,data) VALUES(?,?,?,?)",
            params![s.id, tid, kind, data.to_string()],
        )?;
        let event = Event {
            event_id: tx.last_insert_rowid(),
            session_id: s.id.clone(),
            turn_id: tid,
            kind: kind.into(),
            data,
        };
        tx.commit()?;
        Ok(event)
    }
    /// Streaming progress must not load or rewrite canonical history/session JSON.
    pub fn append_event(&mut self, sid: &str, kind: &str, data: Value) -> Result<Event> {
        let s = self.metadata(sid)?;
        sqlite_teams::check(&self.db, &s, kind)?;
        let tid = s.turn.map(|t| t.id);
        self.db.execute(
            "INSERT INTO events(session_id,turn_id,kind,data) VALUES(?,?,?,?)",
            params![sid, tid, kind, data.to_string()],
        )?;
        Ok(Event {
            event_id: self.db.last_insert_rowid(),
            session_id: sid.into(),
            turn_id: tid,
            kind: kind.into(),
            data,
        })
    }
    pub fn start_attempt(&mut self, sid: &str, id: &str, number: u32) -> Result<Event> {
        let s = self.metadata(sid)?;
        let t = s.turn.context("No current turn")?;
        let tx = self.db.transaction()?;
        tx.execute(
            "INSERT INTO attempts(id,session_id,turn_id,status,text) VALUES(?,?,?,'running','')",
            params![id, sid, t.id],
        )?;
        let data = json!({"attempt_id":id,"attempt":number});
        tx.execute("INSERT INTO events(session_id,turn_id,kind,data) VALUES(?,?,'model_attempt_started',?)",params![sid,t.id,data.to_string()])?;
        let event = Event {
            event_id: tx.last_insert_rowid(),
            session_id: sid.into(),
            turn_id: Some(t.id),
            kind: "model_attempt_started".into(),
            data,
        };
        tx.commit()?;
        Ok(event)
    }
    pub fn attempt_delta(&mut self, sid: &str, id: &str, text: &str) -> Result<Event> {
        let tx = self.db.transaction()?;
        let tid: String = tx.query_row(
            "SELECT turn_id FROM attempts WHERE id=? AND session_id=? AND status='running'",
            params![id, sid],
            |r| r.get(0),
        )?;
        tx.execute("UPDATE attempts SET truncated=truncated OR length(text || ?)>16000,text=substr(text || ?,1,16000) WHERE id=?",params![text,text,id])?;
        let data = json!({"text":text,"attempt_id":id});
        tx.execute(
            "INSERT INTO events(session_id,turn_id,kind,data) VALUES(?,?,'assistant_delta',?)",
            params![sid, tid, data.to_string()],
        )?;
        let e = Event {
            event_id: tx.last_insert_rowid(),
            session_id: sid.into(),
            turn_id: Some(tid),
            kind: "assistant_delta".into(),
            data,
        };
        tx.commit()?;
        Ok(e)
    }
    pub fn finish_attempt(
        &mut self,
        sid: &str,
        id: &str,
        status: &str,
        kind: Option<crate::provider::Failure>,
    ) -> Result<Event> {
        let tx = self.db.transaction()?;
        let tid: String = tx.query_row(
            "SELECT turn_id FROM attempts WHERE id=? AND session_id=?",
            params![id, sid],
            |r| r.get(0),
        )?;
        tx.execute(
            "UPDATE attempts SET status=? WHERE id=? AND session_id=?",
            params![status, id, sid],
        )?;
        let data = json!({"attempt_id":id,"status":status,"kind":kind});
        tx.execute("INSERT INTO events(session_id,turn_id,kind,data) VALUES(?,?,'model_attempt_finished',?)",params![sid,tid,data.to_string()])?;
        let event = Event {
            event_id: tx.last_insert_rowid(),
            session_id: sid.into(),
            turn_id: Some(tid),
            kind: "model_attempt_finished".into(),
            data,
        };
        tx.commit()?;
        Ok(event)
    }
    pub fn interrupt_attempts(&mut self, sid: &str) -> Result<()> {
        self.db.execute(
            "UPDATE attempts SET status='interrupted' WHERE session_id=? AND status='running'",
            [sid],
        )?;
        Ok(())
    }
    pub fn attempts(&self, sid: &str) -> Result<Vec<Value>> {
        let mut q=self.db.prepare("SELECT id,turn_id,status,text,truncated FROM attempts WHERE session_id=? ORDER BY rowid DESC LIMIT 3")?;
        let rows=q.query_map([sid],|r|Ok(json!({"id":r.get::<_,String>(0)?,"turn_id":r.get::<_,String>(1)?,"status":r.get::<_,String>(2)?,"text":r.get::<_,String>(3)?,"truncated":r.get::<_,bool>(4)?})))?;
        rows.map(|r| Ok(r?)).collect()
    }
    /// Cursor counts canonical items, including hidden provider-private rows.
    /// Private reasoning is never exposed through the display history API.
    pub fn history_page(&self, sid: &str, after: usize) -> Result<Value> {
        self.metadata(sid)?;
        let total: usize = self.db.query_row(
            "SELECT COUNT(*) FROM history WHERE session_id=?",
            [sid],
            |r| r.get(0),
        )?;
        if after > total {
            bail!("Invalid history cursor");
        }
        let mut q = self.db.prepare(
            "SELECT seq,data FROM history WHERE session_id=? AND seq>=? ORDER BY seq LIMIT 100",
        )?;
        let mut items = vec![];
        let mut bytes = 0;
        let mut cursor = after;
        for row in q.query_map(params![sid, after], |r| {
            Ok((r.get::<_, usize>(0)?, r.get::<_, String>(1)?))
        })? {
            let (seq, raw) = row?;
            let v: Value = serde_json::from_str(&raw)?;
            if v["type"] != "reasoning" {
                let display = match v["type"].as_str() {
                    Some("function_call") => {
                        json!({"type":"function_call","call_id":v["call_id"],"name":v["name"],"arguments":v["arguments"]})
                    }
                    Some("function_call_output") => {
                        json!({"type":"function_call_output","call_id":v["call_id"],"output":v["output"]})
                    }
                    _ if v["role"] == "user" || v["type"] == "message" => {
                        json!({"role":v["role"],"type":v["type"],"content":v["content"],"runtime_notice":v["_runtime_notice"]})
                    }
                    _ => {
                        cursor = seq + 1;
                        continue;
                    }
                };
                let size = serde_json::to_vec(&display)?.len();
                if !items.is_empty() && bytes + size > 512_000 {
                    break;
                }
                items.push(json!({"index":seq,"item":display}));
                bytes += size;
            }
            cursor = seq + 1;
        }
        Ok(json!({"items":items,"cursor":cursor,"has_more":cursor<total,"total_items":total}))
    }
    pub fn transcript_excerpt(&self, sid: &str, after: usize, offset: usize) -> Result<Value> {
        let page = self.history_page(sid, after)?;
        let Some(first) = page["items"].as_array().and_then(|a| a.first()) else {
            return Ok(
                json!({"next_index":page["cursor"],"next_offset":0,"has_more":page["has_more"],"text":""}),
            );
        };
        let raw = serde_json::to_string(&first["item"])?;
        let count = raw.chars().count();
        if offset > count {
            bail!("Invalid transcript character offset");
        }
        let text = raw.chars().skip(offset).take(8000).collect::<String>();
        let next = offset + text.chars().count();
        let index = first["index"].as_u64().unwrap();
        Ok(
            json!({"index":index,"offset":offset,"text":text,"item_complete":next==count,
            "next_index":if next==count{index+1}else{index},"next_offset":if next==count{0}else{next},
            "has_more":next<count||index+1<page["total_items"].as_u64().unwrap()}),
        )
    }
    pub fn accept(
        &mut self,
        sid: &str,
        key: &str,
        text: &str,
    ) -> Result<(Session, bool, Option<Event>)> {
        self.accept_with_limits(sid, key, text, None)
    }
    pub fn accept_with_limits(
        &mut self,
        sid: &str,
        key: &str,
        text: &str,
        limits: Option<crate::budget::Limits>,
    ) -> Result<(Session, bool, Option<Event>)> {
        if let Some(l) = &limits {
            l.validate()?;
        }
        let mut s = self.get(sid)?;
        let existing = self.db.query_row(
            "SELECT text,turn_id FROM submissions WHERE session_id=? AND submission_id=?",
            params![sid, key],
            |r| Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?)),
        );
        if let Ok((old, tid)) = existing {
            let requested:Option<crate::budget::Limits>=self.db.query_row("SELECT data FROM events WHERE turn_id=? AND kind='turn_accepted' ORDER BY id LIMIT 1",[&tid],|r|r.get::<_,String>(0)).optional()?.map(|raw|->Result<_>{Ok(serde_json::from_str::<Value>(&raw)?["limits"].clone())}).transpose()?.map(serde_json::from_value).transpose()?.flatten();
            if old != text || requested != limits {
                bail!("Submission ID reused with different text");
            }
            if s.turn.as_ref().is_some_and(|t| t.id == tid) {
                return Ok((s, false, None));
            }
            let raw:String=self.db.query_row("SELECT data FROM events WHERE turn_id=? AND kind IN ('turn_completed','turn_recovered') ORDER BY id DESC LIMIT 1",[&tid],|r|r.get(0))?;
            let event: Value = serde_json::from_str(&raw)?;
            // Return the original durable identity without altering current session state.
            s.turn = Some(Turn {
                budget: crate::budget::State::new(s.package.limits.as_ref(), limits.clone())?,
                observation: None,
                id: tid,
                submission_id: key.into(),
                text: old,
                status: event["status"].as_str().unwrap_or("interrupted").into(),
                iterations: 0,
                queue: vec![],
                provider_attempts: 0,
                compactions: 0,
                continuations: 0,
                reviewer_denials: 0,
            });
            return Ok((s, false, None));
        }
        if s.closed {
            bail!("Session is closed");
        }
        if s.provider_blocked {
            bail!("Provider safety block requires operator review; this session cannot dispatch more actions");
        }
        if s.turn.as_ref().is_some_and(|t| !terminal(&t.status)) {
            bail!("Session busy; respond or cancel first");
        }
        if let Some((next, _)) = self.team_next(sid)? {
            anyhow::ensure!(next == key, "Queued team work must be consumed first");
        }
        // Reconcile unresolved provider calls from an interrupted/failed run, without executing them.
        if let Some(t) = &s.turn {
            for c in &t.queue {
                s.history.push(json!({"type":"function_call_output","call_id":c["call_id"],"output":"Previous turn interrupted; action outcome unknown, do not assume success."}));
            }
        }
        let mut input = json!({"role":"user","content":text});
        if crate::assistant_tools::admit(&mut s, key, text, crate::assistant_tools::now())? {
            input["_runtime_notice"] = json!("scheduled_task");
        }
        if let Some(delivery) = sqlite_teams::notice(&self.db, sid, key)? {
            let expected: String = self.db.query_row(
                "SELECT text FROM team_queue WHERE session_id=? AND id=?",
                params![sid, key],
                |r| r.get(0),
            )?;
            anyhow::ensure!(text == expected, "Team delivery text changed");
            input["_runtime_notice"] = json!("team_delivery");
            input["_team_delivery"] = delivery;
        }
        s.history.push(input);
        // A bookkeeping decision belongs to the old turn, not the next task.
        s.compaction_action = None;
        if let Some(member) = &mut s.agent_team {
            member.waiting = None;
        }
        s.turn = Some(Turn {
            budget: crate::budget::State::new(s.package.limits.as_ref(), limits.clone())?,
            observation: None,
            id: crate::id("t"),
            submission_id: key.into(),
            text: text.into(),
            status: "accepted".into(),
            iterations: 0,
            queue: vec![],
            provider_attempts: 0,
            compactions: 0,
            continuations: 0,
            reviewer_denials: 0,
        });
        let tx = self.db.transaction()?;
        sqlite_agent_team::ensure_admission(&tx, &s)?;
        Self::append_history(&tx, &s)?;
        tx.execute("UPDATE team_queue SET status='accepted' WHERE session_id=? AND id=? AND status='queued'", params![sid,key])?;
        tx.execute(
            "INSERT INTO submissions VALUES(?,?,?,?)",
            params![sid, key, text, s.turn.as_ref().unwrap().id],
        )?;
        tx.execute(
            "UPDATE sessions SET data=? WHERE id=?",
            params![serde_json::to_string(&s)?, sid],
        )?;
        let tid = s.turn.as_ref().unwrap().id.clone();
        tx.execute(
            "INSERT INTO events(session_id,turn_id,kind,data) VALUES(?,?,?,?)",
            params![
                sid,
                tid,
                "turn_accepted",
                json!({"limits":limits}).to_string()
            ],
        )?;
        let e = Event {
            event_id: tx.last_insert_rowid(),
            session_id: sid.into(),
            turn_id: Some(tid),
            kind: "turn_accepted".into(),
            data: json!({"limits":limits}),
        };
        tx.commit()?;
        Ok((s, true, Some(e)))
    }
    pub fn cursor(&self, sid: &str) -> Result<i64> {
        Ok(self.db.query_row(
            "SELECT COALESCE(MAX(id),0) FROM events WHERE session_id=?",
            [sid],
            |r| r.get(0),
        )?)
    }
    pub fn has_submission(&self, sid: &str, key: &str) -> Result<bool> {
        Ok(self.db.query_row(
            "SELECT EXISTS(SELECT 1 FROM submissions WHERE session_id=? AND submission_id=?)",
            params![sid, key],
            |r| r.get(0),
        )?)
    }
    pub fn events(&self, sid: &str, after: i64) -> Result<Vec<Event>> {
        self.metadata(sid)?;
        if after < 0 || after > self.cursor(sid)? {
            bail!("Invalid cursor");
        }
        let mut q=self.db.prepare("SELECT id,turn_id,kind,data FROM events WHERE session_id=? AND id>? ORDER BY id LIMIT 500")?;
        let rows = q.query_map(params![sid, after], |r| {
            Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get::<_, String>(3)?))
        })?;
        let mut result = Vec::new();
        let mut bytes = 0usize;
        for r in rows {
            let (id, tid, kind, data) = r?;
            if !result.is_empty() && bytes + data.len() > 512000 {
                break;
            }
            bytes += data.len();
            result.push(Event {
                event_id: id,
                session_id: sid.into(),
                turn_id: tid,
                kind,
                data: serde_json::from_str(&data)?,
            });
        }
        Ok(result)
    }
    pub fn recover(&mut self) -> Result<()> {
        for mut s in self.list()? {
            self.interrupt_attempts(&s.id)?;
            if let Some(t) = &mut s.turn {
                if !terminal(&t.status)
                    && !matches!(t.status.as_str(), "waiting_agents" | "resuming_agents")
                {
                    let pending = s
                        .interactions
                        .iter()
                        .any(|i| i.turn_id == t.id && i.response.is_none());
                    t.status = if matches!(t.status.as_str(), "waiting" | "recoverable") && pending
                    {
                        "recoverable"
                    } else {
                        "interrupted"
                    }
                    .into();
                    // Match the PostgreSQL recovery contract. Delivery loss is
                    // not proof of failure: a started call without a durable
                    // result becomes model-visible unknown, never replayable.
                    let mut remaining = vec![];
                    let mut unknown = 0;
                    for call in &t.queue {
                        if s.history.iter().any(|v| {
                            v["type"] == "function_call_output" && v["call_id"] == call["call_id"]
                        }) {
                            continue;
                        }
                        let started: bool = self.db.query_row(
                            "SELECT EXISTS(SELECT 1 FROM events WHERE session_id=?1 AND turn_id=?2 AND kind='tool_started' AND json_extract(data,'$.call_id')=?3)",
                            params![s.id, t.id, call["call_id"].as_str().context("Invalid saved call ID")?], |r| r.get(0))?;
                        if started {
                            let output = json!({"type":"tool_outcome_unknown","tool_call_id":call["call_id"],"tool":call["name"],"source":"harness_recovery","may_still_be_running":true,"message":"No durable result was recorded. The operation may have completed, failed, or still be running. Inspect the original environment before deciding how to continue. This is not permission to retry."});
                            s.history.push(json!({"type":"function_call_output","call_id":call["call_id"],"output":output.to_string()}));
                            unknown += 1;
                        } else {
                            remaining.push(call.clone());
                        }
                    }
                    t.queue = remaining;
                    let status = t.status.clone();
                    self.save(&s,"turn_recovered",json!({"status":status,"outcome_unknown_count":unknown,"automatic_replay":false,"shell_state":"not guaranteed across runtime restart"}))?;
                }
            }
        }
        Ok(())
    }
}

impl StateStore for Store {
    fn supports_agent_teams(&self) -> bool {
        true
    }
    fn agent_team(
        &mut self,
        sid: &str,
        operation: &str,
        args: &Value,
        call: Option<(&str, &Value)>,
    ) -> Result<Value> {
        Store::agent_team(self, sid, operation, args, call)
    }

    fn assistant_call(
        &mut self,
        sid: &str,
        tid: &str,
        call: &Value,
        args: &Value,
    ) -> Result<Event> {
        Store::assistant_call(self, sid, tid, call, args)
    }
    fn agent_run(&self, sid: &str, submission: Option<&str>) -> Result<Option<Value>> {
        self.metadata(sid)?;
        let raw: Option<String> = self.db.query_row(
            "SELECT data FROM events WHERE session_id=? AND kind='agent_run_state' AND (? IS NULL OR json_extract(data,'$.submission_id')=?) ORDER BY id DESC LIMIT 1",
            params![sid, submission, submission], |r| r.get(0)
        ).optional()?;
        raw.map(|s| serde_json::from_str(&s).map_err(Into::into))
            .transpose()
    }
    fn supports_teams(&self) -> bool {
        true
    }
    fn team_create(&mut self, r: crate::team_service::Create) -> Result<Value> {
        Store::team_create(self, r)
    }
    fn team_snapshot(&self, id: &str) -> Result<Value> {
        Store::team_snapshot(self, id)
    }
    fn team_command(&mut self, id: &str, c: crate::team_service::Command) -> Result<Value> {
        Store::team_command(self, id, c)
    }
    fn team_events(&self, id: &str, after: i64) -> Result<Value> {
        Store::team_events(self, id, after)
    }
    fn team_context(&self, sid: &str) -> Result<Option<crate::teams::Context>> {
        Store::team_context(self, sid)
    }
    fn team_call(&mut self, sid: &str, tid: &str, call: &Value, args: &Value) -> Result<Event> {
        Store::team_call(self, sid, tid, call, args)
    }
    fn team_tick(&mut self) -> Result<()> {
        Store::team_tick(self)
    }
    fn team_next(&self, sid: &str) -> Result<Option<(String, String)>> {
        Store::team_next(self, sid)
    }
    fn team_cancellations(&self) -> Result<Vec<(String, String)>> {
        Store::team_cancellations(self)
    }
    fn team_cancel_settled(&mut self, sid: &str, tid: &str) -> Result<()> {
        Store::team_cancel_settled(self, sid, tid)
    }
    fn get(&self, id: &str) -> Result<Session> {
        Store::get(self, id)
    }
    fn metadata(&self, id: &str) -> Result<Session> {
        Store::metadata(self, id)
    }
    fn list(&self) -> Result<Vec<Session>> {
        Store::list(self)
    }
    fn list_metadata(&self) -> Result<Vec<Session>> {
        Store::list_metadata(self)
    }
    fn recent_messages(&self, sid: &str) -> Result<(Vec<Value>, bool)> {
        Store::recent_messages(self, sid)
    }
    fn save(&mut self, s: &Session, kind: &str, data: Value) -> Result<Event> {
        Store::save(self, s, kind, data)
    }
    fn append_event(&mut self, sid: &str, kind: &str, data: Value) -> Result<Event> {
        Store::append_event(self, sid, kind, data)
    }
    fn start_attempt(&mut self, sid: &str, id: &str, number: u32) -> Result<Event> {
        Store::start_attempt(self, sid, id, number)
    }
    fn attempt_delta(&mut self, sid: &str, id: &str, text: &str) -> Result<Event> {
        Store::attempt_delta(self, sid, id, text)
    }
    fn finish_attempt(
        &mut self,
        sid: &str,
        id: &str,
        status: &str,
        kind: Option<crate::provider::Failure>,
    ) -> Result<Event> {
        Store::finish_attempt(self, sid, id, status, kind)
    }
    fn interrupt_attempts(&mut self, sid: &str) -> Result<()> {
        Store::interrupt_attempts(self, sid)
    }
    fn attempts(&self, sid: &str) -> Result<Vec<Value>> {
        Store::attempts(self, sid)
    }
    fn history_page(&self, sid: &str, after: usize) -> Result<Value> {
        Store::history_page(self, sid, after)
    }
    fn transcript_excerpt(&self, sid: &str, after: usize, offset: usize) -> Result<Value> {
        Store::transcript_excerpt(self, sid, after, offset)
    }
    fn accept(
        &mut self,
        sid: &str,
        key: &str,
        text: &str,
    ) -> Result<(Session, bool, Option<Event>)> {
        Store::accept(self, sid, key, text)
    }
    fn accept_with_limits(
        &mut self,
        sid: &str,
        key: &str,
        text: &str,
        limits: Option<crate::budget::Limits>,
    ) -> Result<(Session, bool, Option<Event>)> {
        Store::accept_with_limits(self, sid, key, text, limits)
    }
    fn cursor(&self, sid: &str) -> Result<i64> {
        Store::cursor(self, sid)
    }
    fn has_submission(&self, sid: &str, key: &str) -> Result<bool> {
        Store::has_submission(self, sid, key)
    }
    fn events(&self, sid: &str, after: i64) -> Result<Vec<Event>> {
        Store::events(self, sid, after)
    }
    fn recover(&mut self) -> Result<()> {
        Store::recover(self)
    }
}
