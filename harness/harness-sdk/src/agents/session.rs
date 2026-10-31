//! Sessions and runs: a typed wrapper over the host's `agent/*` and `run/*`
//! methods. Run completion, team handling, `interrupt_on` auto-approval,
//! busy retries and journal streaming all happen in the host, so this file is
//! what every language SDK needs to reproduce, and no more.
use super::team::Team;
use crate::{ApprovalDecision, Error, Event, HarnessClient, Result};
use serde::{Deserialize, Serialize};
use serde_json::{json, Map, Value};
use std::{collections::HashSet, path::Path};
use tokio::sync::mpsc;

/// Per-call long-poll bound. Well under the client request timeout.
const WAIT_MS: u64 = 2000;

/// Everything needed to reattach after an application restart. Persist it
/// (it is `Serialize`); only `session_id` is strictly required.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct SessionRef {
    pub session_id: String,
    pub agent: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub team: Option<TeamRef>,
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct TeamRef {
    pub team_id: String,
    pub workers: Vec<WorkerRef>,
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct WorkerRef {
    pub session_id: String,
    pub agent: String,
}

/// A run stopped because a human must decide something.
#[derive(Clone, Debug, PartialEq)]
pub struct Interruption {
    pub id: String,
    pub session_id: String,
    /// Which agent asked (the lead or one of its subagents).
    pub agent: String,
    pub kind: InterruptionKind,
}
#[derive(Clone, Debug, PartialEq)]
pub enum InterruptionKind {
    /// A tool call awaiting approval. `tool` is the model-facing alias
    /// (`provider__tool`); `category` is the engine's effect classification.
    Approval {
        tool: String,
        arguments: Value,
        category: Option<String>,
    },
    /// `ask_user`, a compaction choice, or any other non-tool interaction.
    Question {
        question: String,
        options: Vec<String>,
    },
}
impl Interruption {
    fn from_wire(v: &Value) -> Option<Self> {
        let s = |k: &str| v[k].as_str().map(str::to_owned);
        let kind = if v["kind"] == "approval" {
            InterruptionKind::Approval {
                tool: s("tool").unwrap_or_default(),
                arguments: v["arguments"].clone(),
                category: s("category"),
            }
        } else {
            InterruptionKind::Question {
                question: s("question").unwrap_or_default(),
                options: v["options"]
                    .as_array()
                    .into_iter()
                    .flatten()
                    .filter_map(|o| o.as_str().map(str::to_owned))
                    .collect(),
            }
        };
        Some(Self {
            id: s("id")?,
            session_id: s("session_id")?,
            agent: s("agent").unwrap_or_default(),
            kind,
        })
    }
}

/// Your reply to an [`Interruption`].
#[derive(Clone, Debug)]
pub enum Response {
    Approve(ApprovalDecision),
    Deny,
    Answer(String),
    /// Leave it pending; the run returns `Interrupted`.
    Defer,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum RunStatus {
    Completed,
    /// Waiting on `interruptions`; respond, then call [`Session::resume`].
    Interrupted,
    Cancelled,
    /// Engine turn status: `failed`, `interrupted` or `truncated`.
    Failed(String),
}

#[derive(Clone, Debug)]
pub struct RunResult {
    pub status: RunStatus,
    /// Lead agent's last assistant message for this run, if any.
    pub final_output: Option<String>,
    pub interruptions: Vec<Interruption>,
    /// Stable ID of the submitted input; reuse it to retry after `OutcomeUnknown`.
    pub submission_id: Option<String>,
    /// Lead session's journal cursor when the run returned.
    pub cursor: i64,
    /// Team board at the end of the run, for sessions with subagents.
    pub board: Option<Value>,
}
impl RunResult {
    fn from_wire(v: &Value) -> Self {
        let status = match v["status"].as_str().unwrap_or("") {
            "completed" => RunStatus::Completed,
            "interrupted" => RunStatus::Interrupted,
            "cancelled" => RunStatus::Cancelled,
            _ => RunStatus::Failed(v["failure"].as_str().unwrap_or("failed").to_owned()),
        };
        Self {
            status,
            final_output: v["final_output"].as_str().map(str::to_owned),
            interruptions: v["interruptions"]
                .as_array()
                .into_iter()
                .flatten()
                .filter_map(Interruption::from_wire)
                .collect(),
            submission_id: v["submission_id"].as_str().map(str::to_owned),
            cursor: v["cursor"].as_i64().unwrap_or(0),
            board: v.get("board").filter(|b| !b.is_null()).cloned(),
        }
    }
}

/// Progress while a run is streaming.
#[derive(Clone, Debug)]
pub enum RunEvent {
    /// Named-team events retain both instance and definition identities.
    TeamActivity {
        agent_id: String,
        agent_type: String,
        team_id: String,
        event: Event,
        summary: Value,
    },
    Message {
        agent: String,
        text: String,
    },
    ToolStarted {
        agent: String,
        tool: String,
    },
    ToolFinished {
        agent: String,
        tool: String,
    },
    /// The host approved a call because the agent's `interrupt_on` excludes it.
    AutoApproved {
        agent: String,
        tool: String,
    },
    PlanUpdated {
        agent: String,
        plan: Value,
    },
    Interrupted(Interruption),
    /// Any other engine event (team updates, attempts, sandbox lifecycle...).
    Other(Event),
}
impl RunEvent {
    fn from_wire(item: &Value) -> Option<Self> {
        if let (Some(agent_id), Some(team_id)) =
            (item["agent_id"].as_str(), item["team_id"].as_str())
        {
            return Some(Self::TeamActivity {
                agent_id: agent_id.into(),
                agent_type: item["agent"].as_str()?.into(),
                team_id: team_id.into(),
                event: serde_json::from_value(item["event"].clone()).ok()?,
                summary: item["summary"].clone(),
            });
        }
        let agent = item["agent"].as_str().unwrap_or_default().to_owned();
        let summary = &item["summary"];
        let tool = || summary["tool"].as_str().unwrap_or_default().to_owned();
        Some(match summary["type"].as_str() {
            Some("message") => RunEvent::Message {
                agent,
                text: summary["text"].as_str().unwrap_or_default().to_owned(),
            },
            Some("tool_started") => RunEvent::ToolStarted {
                agent,
                tool: tool(),
            },
            Some("tool_finished") => RunEvent::ToolFinished {
                agent,
                tool: tool(),
            },
            Some("auto_approved") => RunEvent::AutoApproved {
                agent,
                tool: tool(),
            },
            Some("plan_updated") => RunEvent::PlanUpdated {
                agent,
                plan: summary["plan"].clone(),
            },
            _ => RunEvent::Other(serde_json::from_value(item["event"].clone()).ok()?),
        })
    }
}

/// A run in progress. `next()` yields events; `finish()` returns the result.
pub struct RunStream {
    rx: mpsc::UnboundedReceiver<RunEvent>,
    task: tokio::task::JoinHandle<Result<RunResult>>,
}
impl RunStream {
    pub async fn next(&mut self) -> Option<RunEvent> {
        self.rx.recv().await
    }
    /// Drain remaining events and return the outcome.
    pub async fn finish(mut self) -> Result<RunResult> {
        while self.rx.recv().await.is_some() {}
        self.task.await.unwrap_or(Err(Error::Closed))
    }
}

#[derive(Clone)]
pub struct Session {
    client: HarnessClient,
    reference: SessionRef,
}
#[derive(Clone, Debug, Serialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum SessionInput {
    Message {
        text: String,
    },
    Approval {
        interaction_id: String,
        #[serde(skip_serializing_if = "Option::is_none")]
        target_session_id: Option<String>,
        decision: ApprovalDecision,
    },
    Answer {
        interaction_id: String,
        #[serde(skip_serializing_if = "Option::is_none")]
        target_session_id: Option<String>,
        text: String,
    },
}
impl From<&str> for SessionInput {
    fn from(text: &str) -> Self {
        Self::Message { text: text.into() }
    }
}
impl From<&String> for SessionInput {
    fn from(text: &String) -> Self {
        Self::Message { text: text.clone() }
    }
}
impl From<String> for SessionInput {
    fn from(text: String) -> Self {
        Self::Message { text }
    }
}

/// Lightweight durable run reference. Readers page the journal independently;
/// not reading events never queues unbounded data or pauses execution.
#[derive(Clone)]
pub struct Run {
    session: Session,
    submission: String,
    cursors: Map<String, Value>,
}
impl Run {
    pub fn id(&self) -> &str {
        &self.submission
    }
    pub async fn result(&self) -> Result<RunResult> {
        self.session.wait(Some(&self.submission)).await
    }
    pub fn events(
        &self,
    ) -> std::pin::Pin<Box<dyn futures_util::Stream<Item = Result<RunEvent>> + Send>> {
        let me = self.clone();
        Box::pin(async_stream::try_stream! {
            let mut cursors=me.cursors.clone();
            loop {
                let value=me.session.client.request("run/wait",json!({"session_id":me.session.id(),"submission_id":me.submission,"wait_ms":WAIT_MS,"max_events":128,"cursors":cursors})).await?;
                for item in value["events"].as_array().into_iter().flatten() {
                    if let Some(event)=RunEvent::from_wire(item) { yield event; }
                }
                cursors=value["cursors"].as_object().cloned().ok_or_else(||Error::Remote("Missing event cursors".into()))?;
                if value["status"]!="running" && value["more_events"]!=true { break; }
            }
        })
    }
}

impl Session {
    pub async fn send(&self, input: impl Into<SessionInput>) -> Result<Run> {
        let input = input.into();
        if let SessionInput::Message { text } = &input {
            return self
                .send_with_id(&openworker_runtime::id("sub"), text)
                .await;
        }
        let mut cursors = Map::new();
        for sid in self.member_ids() {
            cursors.insert(
                sid.clone(),
                self.client.snapshot(&sid).await?["cursor"].clone(),
            );
        }
        let receipt = self
            .client
            .request(
                "session/send",
                json!({"session_id":self.id(),"input":input}),
            )
            .await?;
        let submission = receipt["run_id"]
            .as_str()
            .ok_or_else(|| Error::Remote("Control accepted without an SDK run reference".into()))?
            .to_owned();
        Ok(Run {
            session: self.clone(),
            submission,
            cursors,
        })
    }
    pub async fn send_with_limits(&self, text: &str, limits: super::TurnLimits) -> Result<Run> {
        self.send_with_id_and_limits(&openworker_runtime::id("sub"), text, Some(limits))
            .await
    }
    pub async fn send_with_id(&self, id: &str, text: &str) -> Result<Run> {
        self.send_with_id_and_limits(id, text, None).await
    }
    pub async fn send_with_id_and_limits(
        &self,
        id: &str,
        text: &str,
        limits: Option<super::TurnLimits>,
    ) -> Result<Run> {
        if limits.is_some() && self.client.info()["capabilities"]["turn_limits"] != true {
            return Err(crate::Error::Setup(
                "Host does not support turn limits".into(),
            ));
        }
        let mut parameters =
            json!({"session_id":self.id(),"submission_id":id,"input":{"text":text}});
        if let Some(limits) = limits {
            parameters["limits"] = json!(limits);
        }
        let started = self.client.request("session/send", parameters).await?;
        Ok(Run {
            session: self.clone(),
            submission: id.into(),
            cursors: started["cursors"].as_object().cloned().unwrap_or_default(),
        })
    }
    /// Authoritative read only; no scheduling or automatic approval side effects.
    pub async fn state(&self) -> Result<Value> {
        self.client
            .request("session/state", json!({"session_id":self.id()}))
            .await
    }
    pub(crate) async fn create(
        client: HarnessClient,
        agent: &str,
        workspace: &Path,
    ) -> Result<Self> {
        Self::create_with_model(client, agent, workspace, None).await
    }
    pub(crate) async fn create_with_model(
        client: HarnessClient,
        agent: &str,
        workspace: &Path,
        model: Option<crate::ModelChoice>,
    ) -> Result<Self> {
        let mut options = json!({"agent":agent,"workspace":workspace});
        if let Some(model) = model {
            options["model"] = json!(model);
        }
        Self::create_options(client, options).await
    }
    pub(crate) async fn create_options(client: HarnessClient, options: Value) -> Result<Self> {
        if options.get("auto_skip_questions").is_some()
            && client.info()["capabilities"]["sdk_question_policy"] != true
        {
            return Err(Error::Setup(
                "Update the host to configure SDK question policy".into(),
            ));
        }
        let created = client.request("agent/session", options).await?;
        let reference: SessionRef = serde_json::from_value(created["session"].clone())
            .map_err(|_| Error::Remote("Malformed agent/session result".into()))?;
        Self::open(client, reference).await
    }

    pub(crate) async fn open(client: HarnessClient, reference: SessionRef) -> Result<Self> {
        let session = Self { client, reference };
        // Attach every member at its current cursor so child-restart recovery
        // covers them without replaying history.
        for sid in session.member_ids() {
            let cursor = session.client.snapshot(&sid).await?["cursor"]
                .as_i64()
                .unwrap_or(0);
            session.client.attach(&sid, cursor).await?;
        }
        Ok(session)
    }

    pub fn id(&self) -> &str {
        &self.reference.session_id
    }
    pub fn agent(&self) -> &str {
        &self.reference.agent
    }
    /// Persist this to resume after an application restart.
    pub fn reference(&self) -> &SessionRef {
        &self.reference
    }
    /// Typed team board access, for sessions whose agent has subagents.
    pub fn team(&self) -> Option<Team> {
        self.reference
            .team
            .as_ref()
            .map(|t| Team::new(self.client.clone(), t.team_id.clone()))
    }
    /// Current durable state of the lead session.
    pub async fn snapshot(&self) -> Result<Value> {
        self.client.snapshot(self.id()).await
    }
    fn member_ids(&self) -> Vec<String> {
        let mut ids = vec![self.reference.session_id.clone()];
        if let Some(t) = &self.reference.team {
            ids.extend(t.workers.iter().map(|w| w.session_id.clone()));
        }
        ids
    }

    /// Send input and wait until the run completes or needs you.
    pub async fn run(&self, input: &str) -> Result<RunResult> {
        self.run_with_submission(&openworker_runtime::id("sub"), input)
            .await
    }
    /// Like [`Self::run`] with your own stable submission ID. Resending the same
    /// ID after `Error::OutcomeUnknown` never starts a second turn.
    pub async fn run_with_submission(&self, submission_id: &str, input: &str) -> Result<RunResult> {
        self.start(submission_id, input).await?;
        self.wait(Some(submission_id)).await
    }
    /// Run and answer interruptions inline with `handler`. Returning
    /// [`Response::Defer`] stops and returns the run as `Interrupted`.
    pub async fn run_with<F>(&self, input: &str, mut handler: F) -> Result<RunResult>
    where
        F: FnMut(&Interruption) -> Response,
    {
        let mut result = self.run(input).await?;
        loop {
            if result.status != RunStatus::Interrupted {
                return Ok(result);
            }
            let mut deferred = false;
            for i in &result.interruptions {
                let response = handler(i);
                deferred |= matches!(response, Response::Defer);
                self.respond(i, response).await?;
            }
            if deferred {
                return Ok(result);
            }
            let submission = result.submission_id.clone();
            result = self.resume().await?;
            result.submission_id = submission;
        }
    }
    /// Send input and stream progress events (read from the durable journal
    /// by the host, so nothing is lost to timing or a child restart).
    pub async fn run_streamed(&self, input: &str) -> Result<RunStream> {
        let submission = openworker_runtime::id("sub");
        let started = self.start(&submission, input).await?;
        let cursors = started["cursors"].as_object().cloned().unwrap_or_default();
        Ok(self.spawn(Some(submission), cursors))
    }
    /// Keep waiting after responding to interruptions (or after reattaching).
    pub async fn resume(&self) -> Result<RunResult> {
        self.wait(None).await
    }
    /// Streaming form of [`Self::resume`], from the current journal position.
    pub async fn resume_streamed(&self) -> Result<RunStream> {
        let mut cursors = Map::new();
        for sid in self.member_ids() {
            let c = self.client.snapshot(&sid).await?["cursor"].clone();
            cursors.insert(sid, c);
        }
        Ok(self.spawn(None, cursors))
    }

    async fn start(&self, submission_id: &str, input: &str) -> Result<Value> {
        self.client
            .request(
                "run/start",
                json!({"session_id": self.id(), "submission_id": submission_id, "text": input}),
            )
            .await
    }
    async fn wait(&self, submission: Option<&str>) -> Result<RunResult> {
        loop {
            let v = self
                .client
                .request(
                    "run/wait",
                    json!({"session_id": self.id(), "submission_id": submission,
                           "wait_ms": WAIT_MS}),
                )
                .await?;
            if v["status"] != "running" {
                return Ok(RunResult::from_wire(&v));
            }
        }
    }
    fn spawn(&self, submission: Option<String>, mut cursors: Map<String, Value>) -> RunStream {
        // Unbounded so an unread stream never stalls the run.
        let (tx, rx) = mpsc::unbounded_channel();
        let me = self.clone();
        let task = tokio::spawn(async move {
            let mut announced = HashSet::new();
            loop {
                let v = me
                    .client
                    .request(
                        "run/wait",
                        json!({"session_id": me.id(), "submission_id": submission,
                               "wait_ms": WAIT_MS, "cursors": cursors}),
                    )
                    .await?;
                for item in v["events"].as_array().into_iter().flatten() {
                    if let Some(e) = RunEvent::from_wire(item) {
                        let _ = tx.send(e);
                    }
                }
                if let Some(c) = v["cursors"].as_object() {
                    cursors = c.clone();
                }
                let result = RunResult::from_wire(&v);
                for i in &result.interruptions {
                    if announced.insert(i.id.clone()) {
                        let _ = tx.send(RunEvent::Interrupted(i.clone()));
                    }
                }
                if v["status"] != "running" && v["more_events"] != true {
                    return Ok(result);
                }
            }
        });
        RunStream { rx, task }
    }

    /// Reply to an interruption. The host checks the reply matches the
    /// interruption kind and retries while the engine is briefly busy.
    pub async fn respond(&self, interruption: &Interruption, response: Response) -> Result<()> {
        let mut p = json!({"session_id": interruption.session_id,
                           "interaction_id": interruption.id});
        match (&interruption.kind, response) {
            (_, Response::Defer) => return Ok(()),
            (InterruptionKind::Approval { .. }, Response::Approve(d)) => p["decision"] = json!(d),
            (InterruptionKind::Approval { .. }, Response::Deny) => {
                p["decision"] = json!(ApprovalDecision::Deny)
            }
            (InterruptionKind::Question { .. }, Response::Answer(a)) => p["answer"] = json!(a),
            _ => {
                return Err(Error::InvalidInput(
                    "Approve/Deny approvals; Answer questions".into(),
                ))
            }
        }
        let mut input = json!({"type":if p.get("decision").is_some(){"approval"}else{"answer"},"interaction_id":interruption.id,"target_session_id":interruption.session_id});
        if let Some(d) = p.get("decision") {
            input["decision"] = d.clone();
        } else {
            input["text"] = p["answer"].clone();
        }
        self.client.request("session/send",json!({"session_id":self.id(),"submission_id":openworker_runtime::id("response"),"input":input})).await.map(drop)
    }

    /// Cancel every active turn in this session and its workers. The team
    /// board stays usable; use [`Team::cancel`] to end the team for good.
    pub async fn cancel(&self) -> Result<()> {
        self.client
            .request("run/cancel", json!({"session_id": self.id()}))
            .await
            .and_then(check_member_errors)
    }
    /// Close the session and its workers. Cancel active turns first.
    pub async fn close(&self) -> Result<()> {
        self.client
            .request("agent/close", json!({"session_id": self.id()}))
            .await
            .and_then(check_member_errors)
    }
}

fn check_member_errors(result: Value) -> Result<()> {
    if let Some(errors) = result
        .get("errors")
        .and_then(Value::as_object)
        .filter(|e| !e.is_empty())
    {
        return Err(Error::Remote(format!(
            "Some session operations failed: {}",
            json!(errors)
        )));
    }
    Ok(())
}

impl super::Approvals {
    /// Convenience for building an [`super::Approvals::Only`] set.
    pub fn only<I, S>(tools: I) -> Self
    where
        I: IntoIterator<Item = S>,
        S: Into<String>,
    {
        Self::Only(tools.into_iter().map(Into::into).collect())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn partial_member_failure_is_not_success() {
        assert!(check_member_errors(
            json!({"closed":1,"errors":{"worker":"Cancel active turn before closing"}})
        )
        .is_err());
        assert!(
            check_member_errors(json!({"cancelled":1,"errors":{"worker":"Unavailable"}})).is_err()
        );
        assert!(check_member_errors(json!({"closed":2,"errors":{}})).is_ok());
    }

    #[test]
    fn wire_results_map_to_types() {
        let r = RunResult::from_wire(&json!({
            "status": "interrupted", "final_output": null, "cursor": 7, "submission_id": "s",
            "interruptions": [
                {"id":"i","session_id":"w","agent":"worker","kind":"approval",
                 "tool":"files__write","arguments":{"path":"x"},"category":"write_local"},
                {"id":"q","session_id":"l","agent":"lead","kind":"question",
                 "question":"Go?","options":["Yes"]}
            ],
            "board": null
        }));
        assert_eq!(r.status, RunStatus::Interrupted);
        assert_eq!(r.cursor, 7);
        assert!(r.board.is_none());
        assert!(matches!(&r.interruptions[0].kind,
            InterruptionKind::Approval { tool, category: Some(c), .. } if tool == "files__write" && c == "write_local"));
        assert!(matches!(&r.interruptions[1].kind,
            InterruptionKind::Question { options, .. } if options == &["Yes"]));
        let f = RunResult::from_wire(&json!({"status":"failed","failure":"truncated"}));
        assert_eq!(f.status, RunStatus::Failed("truncated".into()));
    }

    #[test]
    fn stream_items_map_to_events() {
        let m = RunEvent::from_wire(&json!({"agent":"a","summary":{"type":"message","text":"hi"}}));
        assert!(matches!(m, Some(RunEvent::Message { text, .. }) if text == "hi"));
        let a = RunEvent::from_wire(
            &json!({"agent":"a","summary":{"type":"auto_approved","tool":"x__y"}}),
        );
        assert!(matches!(a, Some(RunEvent::AutoApproved { tool, .. }) if tool == "x__y"));
        let o = RunEvent::from_wire(&json!({"agent":"a","summary":null,
            "event":{"event_id":3,"session_id":"s","turn_id":null,"type":"compacted","data":{}}}));
        assert!(matches!(o, Some(RunEvent::Other(e)) if e.event_id == 3));
    }
}
