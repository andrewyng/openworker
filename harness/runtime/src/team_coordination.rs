//! First-class team coordination rules. Storage adapters must execute these decisions
//! atomically; this module does not provision sessions or advertise runtime tools.
//! Message authors come from the authenticated caller, never a model-supplied field.
use anyhow::{ensure, Context, Result};
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Communication {
    pub board: bool,
    pub direct_messages: bool,
}
impl Default for Communication {
    fn default() -> Self {
        Self {
            board: true,
            direct_messages: false,
        }
    }
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Policy {
    pub communication: Communication,
    pub max_instances: usize,
}
impl Default for Policy {
    fn default() -> Self {
        Self {
            communication: Communication::default(),
            max_instances: 32,
        }
    }
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SpawnRequest {
    pub agent_type: String,
    pub agent_id: String,
    pub message: String,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Phase {
    Reserved,
    Ready,
    Closed,
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Instance {
    pub agent_type: String,
    pub agent_id: String,
    pub session_id: String,
    pub phase: Phase,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Roster {
    policy: Policy,
    spawn_types: BTreeSet<String>,
    instances: BTreeMap<String, Instance>,
    requests: BTreeMap<String, SpawnRequest>,
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Reservation {
    pub instance: Instance,
    pub created: bool,
}
fn identifier(id: &str) -> Result<()> {
    ensure!(
        !id.is_empty()
            && id.len() <= 64
            && id != "all"
            && id.as_bytes()[0].is_ascii_alphabetic()
            && id
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_'),
        "Invalid instance/type identifier or reserved @all identifier"
    );
    Ok(())
}
fn text(body: &str, max: usize) -> Result<()> {
    ensure!(
        !body.trim().is_empty() && body.len() <= max && !body.contains('\0'),
        "Empty or oversized message"
    );
    Ok(())
}
fn session_id(id: &str) -> Result<()> {
    ensure!(
        !id.is_empty() && id.len() <= 200 && !id.chars().any(char::is_control),
        "Invalid session reference"
    );
    Ok(())
}
impl Roster {
    pub fn policy(&self) -> &Policy {
        &self.policy
    }
    pub fn spawn_types(&self) -> &BTreeSet<String> {
        &self.spawn_types
    }
    /// Trusted catalog resolution calls this only after persisting an exact
    /// approved definition in the same transaction. It grants no host authority.
    pub fn register_type(&mut self, name: &str) -> Result<()> {
        identifier(name)?;
        ensure!(
            self.spawn_types.contains(name) || self.spawn_types.len() < 96,
            "Team type capacity reached"
        );
        self.spawn_types.insert(name.into());
        Ok(())
    }
    pub fn new(lead: Instance, spawn_types: BTreeSet<String>, policy: Policy) -> Result<Self> {
        ensure!(
            (1..=1024).contains(&policy.max_instances),
            "Invalid team capacity"
        );
        identifier(&lead.agent_id)?;
        identifier(&lead.agent_type)?;
        session_id(&lead.session_id)?;
        ensure!(lead.phase == Phase::Ready, "Lead must be a ready instance");
        ensure!(
            !spawn_types.is_empty() && spawn_types.len() <= 128,
            "Invalid registered agent types"
        );
        for kind in &spawn_types {
            identifier(kind)?;
        }
        Ok(Self {
            policy,
            spawn_types,
            instances: BTreeMap::from([(lead.agent_id.clone(), lead)]),
            requests: BTreeMap::new(),
        })
    }
    pub fn instance(&self, id: &str) -> Option<&Instance> {
        self.instances.get(id)
    }
    pub fn instances(&self) -> impl Iterator<Item = &Instance> {
        self.instances.values()
    }
    /// Must run within the adapter's serialized team transaction. Allocate the
    /// canonical child session reference only for the first reservation; duplicates
    /// cannot launch more work, even if the original instance is now closed.
    pub fn reserve(
        &mut self,
        request: SpawnRequest,
        allocate_session: impl FnOnce() -> String,
    ) -> Result<Reservation> {
        identifier(&request.agent_id)?;
        identifier(&request.agent_type)?;
        text(&request.message, 16000)?;
        ensure!(
            self.spawn_types.contains(&request.agent_type),
            "Agent type is not registered for this team"
        );
        if let Some(instance) = self.instances.get(&request.agent_id) {
            ensure!(self.requests.get(&request.agent_id) == Some(&request), "Instance ID already belongs to a different spawn request; use send_message for follow-up work");
            return Ok(Reservation {
                instance: instance.clone(),
                created: false,
            });
        }
        ensure!(
            self.instances.len() < self.policy.max_instances,
            "Team instance capacity reached"
        );
        let sid = allocate_session();
        session_id(&sid)?;
        ensure!(
            !self.instances.values().any(|i| i.session_id == sid),
            "Session already belongs to an instance"
        );
        let instance = Instance {
            agent_type: request.agent_type.clone(),
            agent_id: request.agent_id.clone(),
            session_id: sid,
            phase: Phase::Reserved,
        };
        self.requests.insert(request.agent_id.clone(), request);
        self.instances
            .insert(instance.agent_id.clone(), instance.clone());
        Ok(Reservation {
            instance,
            created: true,
        })
    }
    pub fn ready(&mut self, id: &str, sid: &str) -> Result<()> {
        let i = self.instances.get_mut(id).context("Unknown instance")?;
        ensure!(
            i.session_id == sid && i.phase != Phase::Closed,
            "Instance reference changed or was closed"
        );
        i.phase = Phase::Ready;
        Ok(())
    }
    pub fn close(&mut self, id: &str) -> Result<()> {
        self.instances
            .get_mut(id)
            .context("Unknown instance")?
            .phase = Phase::Closed;
        Ok(())
    }
    fn sender(&self, id: &str) -> Result<()> {
        ensure!(
            self.instances
                .get(id)
                .is_some_and(|i| i.phase == Phase::Ready),
            "Sender is not an active team instance"
        );
        Ok(())
    }
    fn recipient(&self, id: &str) -> Result<Instance> {
        self.instances
            .get(id)
            .filter(|i| i.phase != Phase::Closed)
            .cloned()
            .context("Mention/recipient is not a current team instance")
    }
    /// Resolve once when posting and persist this exact recipient snapshot with
    /// the board message. Do not expand @all again when reading or delivering it.
    pub fn board_recipients(&self, sender: &str, body: &str) -> Result<Vec<Instance>> {
        ensure!(
            self.policy.communication.board,
            "Board communication is disabled"
        );
        self.sender(sender)?;
        text(body, 4000)?;
        let mut recipients = BTreeSet::new();
        for mention in mentions(body) {
            if mention == "all" {
                recipients.extend(
                    self.instances
                        .values()
                        .filter(|i| i.agent_id != sender && i.phase != Phase::Closed)
                        .map(|i| i.agent_id.clone()),
                );
            } else {
                self.recipient(&mention)?;
                recipients.insert(mention);
            }
        }
        recipients
            .into_iter()
            .map(|id| self.recipient(&id))
            .collect()
    }
    pub fn direct_recipient(&self, sender: &str, target: &str, body: &str) -> Result<Instance> {
        ensure!(
            self.policy.communication.direct_messages,
            "Direct communication is disabled; no board message was posted"
        );
        self.sender(sender)?;
        text(body, 4000)?;
        self.recipient(target)
    }
}
/// Mentions are exact instance tokens, not email addresses. Escaped @ and code
/// literals are ordinary board text. No fuzzy matching or implicit role broadcast.
fn mentions(body: &str) -> Vec<String> {
    let bytes = body.as_bytes();
    let mut out = vec![];
    let mut i = 0;
    let mut code = 0;
    while i < bytes.len() {
        if bytes[i] == b'\\' {
            i += 2;
            continue;
        }
        if bytes[i] == b'`' {
            let start = i;
            while i < bytes.len() && bytes[i] == b'`' {
                i += 1;
            }
            let run = i - start;
            if code == 0 {
                code = run;
            } else if code == run {
                code = 0;
            }
            continue;
        }
        if code == 0
            && bytes[i] == b'@'
            && (i == 0 || bytes[i - 1].is_ascii_whitespace() || b"([{,:;".contains(&bytes[i - 1]))
        {
            let start = i + 1;
            let mut end = start;
            while end < bytes.len()
                && (bytes[end].is_ascii_alphanumeric() || b"_-".contains(&bytes[end]))
            {
                end += 1;
            }
            if end > start {
                out.push(body[start..end].to_owned());
            }
            i = end;
        } else {
            i += 1;
        }
    }
    out
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Activity {
    Busy,
    Idle,
    Waiting,
    Starting,
    Paused,
    Stopped,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Delivery {
    NextModelCall,
    Wake,
    Retain,
}
pub fn delivery(activity: Activity) -> Delivery {
    match activity {
        Activity::Busy => Delivery::NextModelCall,
        Activity::Idle | Activity::Waiting => Delivery::Wake,
        Activity::Starting | Activity::Paused | Activity::Stopped => Delivery::Retain,
    }
}
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Direction {
    #[default]
    Before,
    After,
}
fn default_limit() -> usize {
    20
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct HistoryQuery {
    pub message_id: Option<String>,
    #[serde(default)]
    pub direction: Direction,
    #[serde(default = "default_limit")]
    pub limit: usize,
}
impl HistoryQuery {
    pub fn direction(&self) -> Direction {
        if self.message_id.is_none() {
            Direction::Before
        } else {
            self.direction
        }
    }
    pub fn validate(&self) -> Result<()> {
        ensure!(
            (1..=50).contains(&self.limit),
            "History limit must be 1..50"
        );
        if let Some(id) = &self.message_id {
            session_id(id)?;
        }
        Ok(())
    }
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct BoardMessage {
    pub id: String,
    pub sequence: u64,
    pub sender: String,
    pub body: String,
    pub recipients: Vec<String>,
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct HistoryPage {
    pub messages: Vec<BoardMessage>,
    pub has_more: bool,
    pub next_message_id: Option<String>,
}
/// Adapters fetch at most limit+1 rows in query order, within this team's range.
/// Normalize to chronological order without loading a team's entire history.
/// An unresolved anchor is an error, never a fallback to another team's history.
pub fn history_page(
    query: &HistoryQuery,
    anchor_sequence: Option<u64>,
    mut rows: Vec<BoardMessage>,
) -> Result<HistoryPage> {
    query.validate()?;
    ensure!(
        query.message_id.is_some() == anchor_sequence.is_some(),
        "History anchor was not resolved in this team"
    );
    ensure!(
        anchor_sequence != Some(0),
        "Invalid history anchor sequence"
    );
    ensure!(
        rows.len() <= query.limit + 1,
        "History adapter exceeded bounded fetch"
    );
    let dir = query.direction();
    let mut ids = BTreeSet::new();
    for (n, row) in rows.iter().enumerate() {
        ensure!(
            row.sequence > 0 && ids.insert(&row.id),
            "Invalid or duplicate board message"
        );
        if let Some(anchor) = anchor_sequence {
            ensure!(
                match dir {
                    Direction::Before => row.sequence < anchor,
                    Direction::After => row.sequence > anchor,
                },
                "History anchor must be exclusive"
            );
        }
        if n > 0 {
            ensure!(
                match dir {
                    Direction::Before => rows[n - 1].sequence > row.sequence,
                    Direction::After => rows[n - 1].sequence < row.sequence,
                },
                "History adapter returned unordered messages"
            );
        }
    }
    let has_more = rows.len() > query.limit;
    rows.truncate(query.limit);
    if dir == Direction::Before {
        rows.reverse();
    }
    let next_message_id = if has_more {
        match dir {
            Direction::Before => rows.first(),
            Direction::After => rows.last(),
        }
        .map(|m| m.id.clone())
    } else {
        None
    };
    Ok(HistoryPage {
        messages: rows,
        has_more,
        next_message_id,
    })
}
