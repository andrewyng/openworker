//! Host-side agent runs: team session creation, run completion, interruption
//! shaping, `interrupt_on` auto-approval, busy retries and journal streaming.
//!
//! These live behind the protocol boundary so every client (Rust embedded or
//! child process, other-language SDKs over stdio) gets identical semantics.
//! The wire contract is documented in docs/harness/AGENT-RUN-PROTOCOL.md.
use super::Host;
use crate::agents::{compile::AgentProfile, Approvals};
use anyhow::{bail, ensure, Context, Result};
use openworker_runtime::{
    store::{terminal, Event},
    team_service::Create,
    teams::{Member, Role},
};
use serde_json::{json, Map, Value};
use std::{
    sync::Arc,
    time::{Duration, Instant},
};
use tokio::sync::broadcast;

pub(super) const METHODS: [&str; 9] = [
    "session/send",
    "session/state",
    "agent/list",
    "agent/session",
    "agent/close",
    "run/start",
    "run/wait",
    "run/respond",
    "run/cancel",
];
type Members = (Vec<(String, String)>, Option<String>);
const MAX_WAIT: Duration = Duration::from_secs(5);
const BUSY_RETRY_FOR: Duration = Duration::from_secs(20);

/// Approve calls excluded from an agent's `interrupt_on` as soon as they
/// appear, whether or not any client is currently waiting on the run.
pub(super) fn spawn_auto_approver(host: &Arc<Host>) {
    // Agents can be registered later; keep one host-owned observer alive.
    let weak = Arc::downgrade(host);
    let mut rx = host.events.subscribe();
    let stop = host.stop.clone();
    tokio::spawn(async move {
        let mut retry = tokio::time::interval(Duration::from_millis(500));
        retry.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
        // Approvals left pending across a restart.
        if let Some(h) = weak.upgrade() {
            let _ = h.approve_sweep(None).await;
        }
        loop {
            let event = tokio::select! {
                _ = stop.cancelled() => break,
                _ = retry.tick() => {
                    let Some(h) = weak.upgrade() else { break };
                    let _ = h.approve_sweep(None).await;
                    continue;
                }
                e = rx.recv() => e,
            };
            let Some(h) = weak.upgrade() else { break };
            match event {
                Ok(e) if e.kind == "interaction_required" => {
                    let _ = h.approve_sweep(Some(&e.session_id)).await;
                }
                Err(broadcast::error::RecvError::Lagged(_)) => {
                    let _ = h.approve_sweep(None).await;
                }
                Err(broadcast::error::RecvError::Closed) => break,
                Ok(_) => {}
            }
        }
    });
}

fn busy(e: &anyhow::Error) -> bool {
    let m = e.to_string();
    m.contains("checkpointing") || m.contains("concurrency limit")
}

impl Host {
    fn save_run(&self, sid: &str, record: &Value) -> Result<()> {
        let event =
            self.store
                .lock()
                .unwrap()
                .append_event(sid, "agent_run_state", record.clone())?;
        let _ = self.events.send(event);
        Ok(())
    }

    async fn run_start(&self, p: &Value) -> Result<Value> {
        let _run_admission = self.run_admission.lock().await;
        let sid = text(p, "session_id")?;
        let submission = text(p, "submission_id")?;
        let input = openworker_runtime::runtime::text(p, "text", 32000)?;
        if let Some(value) = p.get("limits").filter(|v| !v.is_null()) {
            let limits: openworker_runtime::budget::Limits = serde_json::from_value(value.clone())?;
            limits.validate()?;
        }
        let existing = self
            .store
            .lock()
            .unwrap()
            .agent_run(sid, Some(submission))?;
        let mut record = if let Some(record) = existing {
            ensure!(
                record["text"] == input && record["limits"] == p["limits"],
                "Submission ID reused with different text"
            );
            if !record["turn"].is_null() || record.get("result").is_some() {
                return Ok(json!({"submission_id":submission,"turn":record["turn"],
                    "duplicate":true,"cursors":record["cursors"]}));
            }
            record // Crash before/after turn admission: retry the same durable ID.
        } else {
            let previous = self.store.lock().unwrap().agent_run(sid, None)?;
            if let Some(previous) = previous.filter(|r| r.get("result").is_none()) {
                let state = self
                    .run_wait(&json!({"session_id":sid,
                    "submission_id":previous["submission_id"],"wait_ms":0}))
                    .await?;
                ensure!(
                    state["status"] != "running" && state["status"] != "interrupted",
                    "Previous agent run is still active; resume or cancel it first"
                );
            }
            let _admission = self.admission.lock().await;
            ensure!(
                !self.stop.is_cancelled() && !self.failed.is_cancelled(),
                "Host unavailable"
            );
            let (members, _) = self.members(sid)?;
            let mut cursors = Map::new();
            for (s, _) in &members {
                cursors.insert(s.clone(), json!(self.store.lock().unwrap().cursor(s)?));
            }
            let record = json!({"submission_id":submission,"text":input,"limits":p["limits"],"cursors":cursors,"turn":null});
            // The original cursors and input survive even a lost start response.
            self.save_run(sid, &record)?;
            record
        };
        let started = self
            .retry_busy(
                "turn/start",
                json!({"session_id":sid,"submission_id":submission,"text":input,"limits":p["limits"]}),
            )
            .await;
        match started {
            Ok(started) => {
                let _admission = self.admission.lock().await;
                // A concurrent waiter may already have finalized this run.
                if let Some(saved) = self
                    .store
                    .lock()
                    .unwrap()
                    .agent_run(sid, Some(submission))?
                {
                    record = saved;
                }
                record["turn"] = started["turn"].clone();
                self.save_run(sid, &record)?;
                Ok(json!({"submission_id":submission,"turn":record["turn"],
                    "duplicate":started["duplicate"],"cursors":record["cursors"]}))
            }
            Err(error) => {
                let _admission = self.admission.lock().await;
                if !self.store.lock().unwrap().has_submission(sid, submission)? {
                    // Known admission rejection; do not strand future runs.
                    record["result"] = json!({"status":"failed","failure":"not_accepted",
                        "final_output":null,"interruptions":[],"submission_id":submission,
                        "cursor":self.store.lock().unwrap().cursor(sid)?,"board":null});
                    record["end_cursors"] = record["cursors"].clone();
                    self.save_run(sid, &record)?;
                }
                Err(error)
            }
        }
    }
    pub(super) async fn agent_dispatch(&self, method: &str, p: Value) -> Result<Value> {
        ensure!(p.is_object(), "Parameters must be an object");
        ensure!(
            !self.stop.is_cancelled() && !self.failed.is_cancelled(),
            "Host stopped or unavailable; reconcile durable state"
        );
        match method {
            "agent/list" => Ok(
                json!({"agents": self.registry.read().unwrap().agents.values().collect::<Vec<_>>()}),
            ),
            "agent/session" => self.agent_session(&p).await,
            "agent/close" => {
                let sid = text(&p, "session_id")?;
                let (members, _) = self.members(sid)?;
                // Attempt every member; report failures instead of stopping halfway.
                let mut closed = 0;
                let mut errors = Map::new();
                for (s, _) in members.iter().rev() {
                    match self
                        .dispatch_locked("session/close", json!({"session_id": s}))
                        .await
                    {
                        Ok(_) => closed += 1,
                        Err(e) => {
                            errors.insert(s.clone(), json!(e.to_string()));
                        }
                    }
                }
                Ok(json!({"closed": closed, "errors": errors}))
            }
            "session/send" => self.session_send(&p).await,
            "session/state" => self.session_state(&p).await,
            "run/start" => self.run_start(&p).await,
            "run/wait" => self.run_wait(&p).await,
            "run/respond" => self.run_respond(&p).await,
            "run/cancel" => {
                let sid = text(&p, "session_id")?;
                let (members, team) = self.members(sid)?;
                // `team: true` also ends the team (cancelling queued deliveries);
                // otherwise queued team work may start again.
                let mut team_cancelled = false;
                if let (Some(id), true) = (&team, p["team"] == true) {
                    self.dispatch_locked(
                        "team/command",
                        json!({"team_id": id, "command": {"request_id": format!("sdk-cancel-{id}"),
                               "operation": "team_cancel", "arguments": {}}}),
                    )
                    .await?;
                    team_cancelled = true;
                }
                let mut cancelled = 0;
                let mut errors = Map::new();
                for (s, _) in &members {
                    let turn = self.store.lock().unwrap().metadata(s)?.turn;
                    if let Some(t) = turn.filter(|t| !terminal(&t.status)) {
                        match self
                            .dispatch_locked(
                                "turn/cancel",
                                json!({"session_id": s, "turn_id": t.id}),
                            )
                            .await
                        {
                            Ok(_) => cancelled += 1,
                            Err(e) => {
                                errors.insert(s.clone(), json!(e.to_string()));
                            }
                        }
                    }
                }
                Ok(
                    json!({"cancelled": cancelled, "team_cancelled": team_cancelled,
                          "errors": errors}),
                )
            }
            _ => bail!("Unknown agent method"),
        }
    }

    async fn session_send(&self, p: &Value) -> Result<Value> {
        ensure!(
            p.as_object().is_some_and(|p| p.keys().all(|k| [
                "session_id",
                "submission_id",
                "input",
                "limits"
            ]
            .contains(&k.as_str()))),
            "Unknown send options"
        );
        let sid = text(p, "session_id")?;
        let input = p.get("input").context("Missing input")?;
        let fields = input.as_object().context("Input must be a typed object")?;
        let kind = input
            .get("type")
            .map(|v| v.as_str().context("Input type must be a string"))
            .transpose()?
            .unwrap_or("message");
        let allowed: &[&str] = match kind {
            "message" => &["type", "text"],
            "answer" => &["type", "text", "interaction_id", "target_session_id"],
            "approval" => &["type", "decision", "interaction_id", "target_session_id"],
            "steer" => bail!("Steering is not supported by this host"),
            _ => bail!("Unsupported session input type"),
        };
        ensure!(
            fields.keys().all(|k| allowed.contains(&k.as_str())),
            "Mixed or unknown input fields"
        );
        ensure!(
            kind == "message" || p.get("limits").is_none_or(Value::is_null),
            "Limits apply only to new message turns"
        );
        if kind == "message" {
            return self.run_start(&json!({"session_id":sid,"text":input["text"],"submission_id":text(p,"submission_id")?,"limits":p["limits"]})).await;
        }
        let target = input
            .get("target_session_id")
            .map(|v| v.as_str().context("Invalid target session"))
            .transpose()?
            .unwrap_or(sid);
        ensure!(
            self.members(sid)?.0.iter().any(|(s, _)| s == target),
            "Control target is not a member of this session"
        );
        // A control receipt must identify the run it affected, not a new turn
        // admitted concurrently after the old interaction was resolved.
        let _run_admission = self.run_admission.lock().await;
        let interaction = text(input, "interaction_id")?;
        let run = {
            let db = self.store.lock().unwrap();
            let session = db.metadata(target)?;
            let pending = session
                .interactions
                .iter()
                .find(|i| i.id == interaction)
                .context("Unknown interaction")?;
            ensure!(
                session
                    .turn
                    .as_ref()
                    .is_some_and(|turn| turn.id == pending.turn_id),
                "Stale interaction from a previous turn"
            );
            db.agent_run(sid, None)?
        };
        let mut response =
            json!({"session_id":target,"interaction_id":text(input,"interaction_id")?});
        if kind == "approval" {
            response["decision"] = input["decision"].clone();
        } else {
            response["answer"] = input["text"].clone();
        }
        let result = self.run_respond(&response).await?;
        Ok(
            json!({"accepted":true,"run_id":run.as_ref().map(|r|&r["submission_id"]),"result":result}),
        )
    }

    async fn session_state(&self, p: &Value) -> Result<Value> {
        let sid = text(p, "session_id")?;
        let _admission = self.admission.lock().await;
        ensure!(
            !self.stop.is_cancelled() && !self.failed.is_cancelled(),
            "Host unavailable"
        );
        let (members, team) = self.members(sid)?;
        let mut pending = vec![];
        let mut active = vec![];
        let mut cursors = Map::new();
        let mut lead = Value::Null;
        for (id, agent) in &members {
            let snapshot = self.runtime(agent)?.snapshot(id)?;
            if id == sid {
                lead = snapshot.clone();
            }
            cursors.insert(id.clone(), snapshot["cursor"].clone());
            for i in snapshot["interactions"].as_array().into_iter().flatten() {
                pending.push(interruption(id, agent, i, &snapshot));
            }
            if let Some(status) = snapshot["turn"]["status"]
                .as_str()
                .filter(|s| !terminal(s) && *s != "waiting")
            {
                active.push(json!({"session_id":id,"agent":agent,"turn_id":snapshot["turn"]["id"],"phase":match status {"executing"=>"tool","running"=>"model","cancelling"=>"cancelling","recoverable"=>"recovering","waiting_agents"=>"waiting_agents",_=>"queued"},"call_id":if status=="executing"{snapshot["turn"]["queue"][0]["call_id"].clone()}else{Value::Null}}));
            }
        }
        let board = team
            .map(|id| self.store.lock().unwrap().team_snapshot(&id))
            .transpose()?;
        let queued = board.as_ref().is_some_and(|b| b["pending_work"] == true);
        let status = if lead["session"]["closed"] == true {
            "closed"
        } else if !active.is_empty() || queued {
            "running"
        } else if !pending.is_empty() {
            "waiting"
        } else {
            "idle"
        };
        let run = self.store.lock().unwrap().agent_run(sid, None)?;
        let last_outcome = lead["turn"]["status"].as_str().filter(|s| terminal(s));
        let timers_enabled = lead["session"]["package"]["scheduling"] == true;
        let timers = if timers_enabled {
            json!(lead["schedules"].as_array().into_iter().flatten()
            .filter(|v|v["status"]=="active").map(|v|json!({"id":v["id"],"next_run_at":v["next_run_at"],"remaining_runs":v["max_runs"].as_u64().unwrap_or(0).saturating_sub(v["fired"].as_u64().unwrap_or(0))})).collect::<Vec<_>>())
        } else {
            Value::Null
        };
        Ok(
            json!({"status":status,"run_id":run.as_ref().map(|r|&r["submission_id"]),"active":active,"pending":pending,"timers":timers,"capabilities":{"steering":false,"timers":timers_enabled},"can_accept_message":status=="idle","last_outcome":last_outcome,"cursor":lead["cursor"],"cursors":cursors,"board":board}),
        )
    }

    fn profile(&self, agent: &str) -> AgentProfile {
        self.registry
            .read()
            .unwrap()
            .agents
            .get(agent)
            .cloned()
            .unwrap_or_else(|| AgentProfile::plain(agent, agent))
    }

    /// Lead first, then workers, as (session_id, agent_id); plus the team ID
    /// when `sid` leads a team. Workers are not runs of their own.
    fn members(&self, sid: &str) -> Result<Members> {
        let mut db = self.store.lock().unwrap();
        let lead = db.metadata(sid)?;
        let mut members = vec![(sid.to_owned(), lead.coworker)];
        if lead.agent_team.is_some() {
            let doc: openworker_runtime::agent_team::Document =
                serde_json::from_value(db.agent_team(sid, "snapshot", &json!({}), None)?)?;
            if lead
                .agent_team
                .as_ref()
                .is_some_and(|m| m.agent_id == doc.lead)
            {
                for i in doc.roster.instances().filter(|i| {
                    i.session_id != sid
                        && i.phase == openworker_runtime::team_coordination::Phase::Ready
                }) {
                    members.push((i.session_id.clone(), i.agent_type.clone()));
                }
            }
            return Ok((members, None));
        }
        let Some(ctx) = db.team_context(sid)? else {
            return Ok((members, None));
        };
        if ctx.role != Role::Lead {
            return Ok((members, None));
        }
        let snap = db.team_snapshot(&ctx.team_id)?;
        let mut workers: Vec<String> = snap["board"]["members"]
            .as_object()
            .into_iter()
            .flat_map(|m| m.iter())
            .filter(|(id, m)| *id != sid && m["role"] == "worker")
            .map(|(id, _)| id.clone())
            .collect();
        workers.sort();
        for w in workers {
            let agent = db.metadata(&w)?.coworker;
            members.push((w, agent));
        }
        Ok((members, Some(ctx.team_id)))
    }

    async fn retry_busy(&self, method: &str, p: Value) -> Result<Value> {
        let started = Instant::now();
        let mut delay = Duration::from_millis(20);
        loop {
            match self.dispatch_locked(method, p.clone()).await {
                Err(e) if busy(&e) && started.elapsed() < BUSY_RETRY_FOR => {
                    tokio::time::sleep(delay).await;
                    delay = (delay * 2).min(Duration::from_millis(500));
                }
                other => return other,
            }
        }
    }

    async fn agent_session(&self, p: &Value) -> Result<Value> {
        ensure!(
            p.get("auto_skip_questions").is_none_or(Value::is_boolean),
            "auto_skip_questions must be a boolean"
        );
        let agent = text(p, "agent")?;
        ensure!(
            self.registry.read().unwrap().agents.contains_key(agent),
            "Unknown agent {agent}"
        );
        let profile = self.profile(agent);
        if !self.config.scripted {
            let registry = self.registry.read().unwrap();
            for id in std::iter::once(&profile.id).chain(profile.subagents.iter()) {
                let definition = registry.definitions.values().find_map(|root| {
                    if &root.id == id {
                        Some(root)
                    } else {
                        root.subagents.iter().find(|a| &a.id == id)
                    }
                });
                if let Some(definition) = definition {
                    ensure!(
                        definition.model.is_some() || (id == agent && p.get("model").is_some()),
                        "Agent {id} needs a default model or an explicit session model"
                    );
                }
            }
        }
        ensure!(
            !profile.worker_only,
            "{agent} is a subagent; start a session of its lead agent"
        );
        ensure!(
            p.get("scratch").is_none_or(Value::is_boolean),
            "scratch must be a boolean"
        );
        ensure!(
            !(p.get("workspace").is_some() && p["scratch"] == true),
            "Choose workspace or scratch, not both"
        );
        let workspace = match p.get("workspace") {
            Some(v) => v.clone(),
            None => {
                ensure!(
                    p["scratch"] != false,
                    "Provide a workspace when scratch is disabled"
                );
                let root = self
                    .config
                    .desktop
                    .scratch_dir
                    .clone()
                    .unwrap_or_else(|| self.config.state_dir.with_extension("scratch"));
                openworker_runtime::platform::private_directory(&root)?;
                let path = root.join(openworker_runtime::id("session"));
                openworker_runtime::platform::private_directory(&path)?;
                json!(path)
            }
        };
        let name = p["name"].as_str().unwrap_or(&profile.name).to_owned();
        let options = |id: &str, name: String| {
            let mode = self.profile(id).approvals.mode();
            let auto_skip_questions = if id == agent {
                p.get("auto_skip_questions")
                    .and_then(Value::as_bool)
                    .unwrap_or(self.profile(id).auto_skip_questions)
            } else {
                self.profile(id).auto_skip_questions
            };
            let mut options = json!({"name": name, "coworker": id, "workspace": workspace,
                   "permissions": {"mode": mode, "auto_skip_questions": auto_skip_questions},"compaction":self.config.desktop.compaction});
            if let Approvals::Auto(model) = self.profile(id).approvals {
                options["reviewer_model"] = json!(model);
            }
            // A lead's override does not silently replace each worker's default.
            if id == agent {
                if let Some(model) = p.get("model") {
                    options["model"] = model.clone();
                }
            }
            options
        };
        let created = self
            .dispatch_locked("session/create", options(agent, name.clone()))
            .await?;
        let lead = created["session"]["id"]
            .as_str()
            .context("Missing session ID")?
            .to_owned();
        if profile.subagents.is_empty() {
            return Ok(json!({"session": {"session_id": lead, "agent": agent}}));
        }
        let mut workers = vec![];
        let result: Result<Value> = async {
            for sub in &profile.subagents {
                let sub_name = format!("{} / {}", name, self.profile(sub).name);
                let created = self
                    .dispatch_locked("session/create", options(sub, sub_name))
                    .await?;
                let id = created["session"]["id"]
                    .as_str()
                    .context("Missing session ID")?
                    .to_owned();
                workers.push((id, sub.clone()));
            }
            let mut members = vec![Member {
                session_id: lead.clone(),
                name: profile.name.clone(),
                role: Role::Lead,
            }];
            members.extend(workers.iter().map(|(id, sub)| Member {
                session_id: id.clone(),
                name: self.profile(sub).name,
                role: Role::Worker,
            }));
            let team = self
                .dispatch_locked(
                    "team/create",
                    serde_json::to_value(Create {
                        request_id: format!("sdk-team-{lead}"),
                        name: profile.name.clone(),
                        members,
                    })?,
                )
                .await?;
            Ok(team["team_id"].clone())
        }
        .await;
        match result {
            Ok(team_id) => Ok(json!({"session": {
                "session_id": lead, "agent": agent,
                "team": {"team_id": team_id, "workers": workers.iter()
                    .map(|(id, a)| json!({"session_id": id, "agent": a})).collect::<Vec<_>>()}
            }})),
            Err(e) => {
                // Best effort: do not leave half a team behind.
                for id in std::iter::once(&lead).chain(workers.iter().map(|(id, _)| id)) {
                    let _ = self
                        .dispatch_locked("session/close", json!({"session_id": id}))
                        .await;
                }
                Err(e)
            }
        }
    }

    async fn run_respond(&self, p: &Value) -> Result<Value> {
        let sid = text(p, "session_id")?;
        let id = text(p, "interaction_id")?;
        let session = self.store.lock().unwrap().metadata(sid)?;
        let Some(i) = session.interactions.iter().find(|i| i.id == id) else {
            bail!("Interaction {id} does not belong to session {sid}");
        };
        let requested = match (i.kind == "approval", p.get("decision"), p.get("answer")) {
            (true, Some(d), None) | (false, None, Some(d)) => {
                d.as_str().context("Response must be a string")?
            }
            _ => bail!("Approvals take `decision`; questions take `answer`"),
        };
        if let Some(response) = &i.response {
            ensure!(
                response == requested,
                "Interaction already answered differently"
            );
            return Ok(json!({"already_resolved": true, "response": response}));
        }
        ensure!(
            session
                .turn
                .as_ref()
                .is_some_and(|t| t.id == i.turn_id && !terminal(&t.status)),
            "Interaction is stale: its turn has ended"
        );
        let body = match (i.kind == "approval", p.get("decision"), p.get("answer")) {
            (true, Some(d), None) => {
                json!({"session_id": sid, "interaction_id": id, "decision": d})
            }
            (false, None, Some(a)) => {
                json!({"session_id": sid, "interaction_id": id, "answer": a})
            }
            _ => bail!("Approvals take `decision`; questions take `answer`"),
        };
        self.retry_busy("interaction/respond", body).await
    }

    /// Approve pending calls that the owning agent's `interrupt_on` excludes.
    /// One attempt per interaction. Busy runtimes retry on the background tick;
    /// progress reads never wait through approval retries or execute tools.
    async fn approve_sweep(&self, only: Option<&str>) -> Result<()> {
        if !self
            .registry
            .read()
            .unwrap()
            .agents
            .values()
            .any(|a| matches!(a.approvals, Approvals::Only(_)))
        {
            return Ok(());
        }
        let sessions: Vec<(String, String)> = match only {
            Some(sid) => vec![(
                sid.to_owned(),
                self.store.lock().unwrap().metadata(sid)?.coworker,
            )],
            None => self
                .store
                .lock()
                .unwrap()
                .list_metadata()?
                .into_iter()
                .map(|s| (s.id, s.coworker))
                .collect(),
        };
        let mut pending = std::collections::HashSet::new();
        for (sid, agent) in sessions {
            let approvals = self.profile(&agent).approvals;
            if !matches!(approvals, Approvals::Only(_)) {
                continue;
            }
            let Ok(rt) = self.runtime(&agent) else {
                continue;
            };
            let Ok(snap) = rt.snapshot(&sid) else {
                continue;
            };
            for i in snap["interactions"].as_array().into_iter().flatten() {
                let tool = i["call"]["name"].as_str().unwrap_or_default();
                let Some(id) = i["id"].as_str() else { continue };
                pending.insert(id.to_owned());
                if i["kind"] == "approval" && approvals.auto_approves(tool) {
                    let outcome = {
                        let _admission = self.admission.lock().await;
                        ensure!(
                            !self.stop.is_cancelled() && !self.failed.is_cancelled(),
                            "Host unavailable"
                        );
                        rt.approve_from_app_policy(&sid, id).await
                    };
                    let mut failures = self.auto_approval_failures.lock().unwrap();
                    if outcome.as_ref().is_err_and(|e| !busy(e)) {
                        failures.insert(id.to_owned());
                    } else {
                        failures.remove(id);
                    }
                }
            }
        }
        if only.is_none() {
            self.auto_approval_failures
                .lock()
                .unwrap()
                .retain(|id| pending.contains(id));
        }
        Ok(())
    }

    /// Long-poll (bounded by `wait_ms`, at most 5 s): returns as soon as the
    /// run finishes, needs a human, or has new events for a streaming caller.
    async fn run_wait(&self, p: &Value) -> Result<Value> {
        let sid = text(p, "session_id")?.to_owned();
        let submission = p["submission_id"].as_str().map(str::to_owned);
        let wait = Duration::from_millis(p["wait_ms"].as_u64().unwrap_or(1000)).min(MAX_WAIT);
        let max_events = p["max_events"].as_u64().unwrap_or(200).clamp(1, 1000) as usize;
        let mut cursors = p.get("cursors").and_then(Value::as_object).cloned();
        let deadline = Instant::now() + wait;
        let mut rx = self.events.subscribe();
        loop {
            let (members, team) = self.members(&sid)?;
            // Prevent a scheduler/app admission between the idle snapshot and
            // queue/outbox check. Model completion may still advance the journal.
            let admission = self.admission.lock().await;
            ensure!(
                !self.stop.is_cancelled() && !self.failed.is_cancelled(),
                "Host unavailable"
            );
            let mut record = self
                .store
                .lock()
                .unwrap()
                .agent_run(&sid, submission.as_deref())?;
            ensure!(
                submission.is_none() || record.is_some(),
                "Unknown agent run submission"
            );
            let cached = record.as_ref().and_then(|r| r.get("result")).cloned();
            let mut snaps = Vec::with_capacity(members.len());
            for (s, agent) in &members {
                let rt = self.runtime(agent)?;
                snaps.push(rt.snapshot(s)?);
            }
            let board = match &team {
                Some(id) => Some(self.store.lock().unwrap().team_snapshot(id)?),
                None => None,
            };
            if cached.is_none() {
                if let Some(r) = record.as_mut().filter(|r| r["turn"].is_null()) {
                    ensure!(
                        snaps[0]["turn"]["submission_id"] == r["submission_id"],
                        "Run not admitted; retry run/start with the same submission ID and text"
                    );
                    r["turn"] = snaps[0]["turn"].clone();
                }
            }

            // Streaming: journal events up to what this observation covers.
            let mut events = vec![];
            let mut more_events = false;
            if let Some(cursors) = &mut cursors {
                for ((s, agent), snap) in members.iter().zip(&snaps) {
                    let until = if cached.is_some() {
                        record
                            .as_ref()
                            .and_then(|r| r["end_cursors"][s].as_i64())
                            .unwrap_or(0)
                    } else {
                        snap["cursor"].as_i64().unwrap_or(0)
                    };
                    let from = cursors.get(s).and_then(Value::as_i64).unwrap_or(0);
                    ensure!(
                        from >= 0 && from <= snap["cursor"].as_i64().unwrap_or(0),
                        "Invalid run cursor"
                    );
                    if from >= until {
                        cursors.insert(s.clone(), json!(from));
                        continue;
                    }
                    let mut last = from;
                    let journal = self.store.lock().unwrap().events(s, from)?;
                    let membership = self.store.lock().unwrap().metadata(s)?.agent_team;
                    for e in journal {
                        if e.event_id > until {
                            break;
                        }
                        if events.len() >= max_events {
                            more_events = true;
                            break;
                        }
                        last = e.event_id;
                        let mut item = stream_item(agent, e);
                        if let Some(member) = &membership {
                            item["agent_id"] = json!(member.agent_id);
                            item["team_id"] = json!(member.team_id);
                        }
                        events.push(item);
                    }
                    // The adapter also pages by count AND bytes. Exhausting
                    // its page is not evidence that we reached the snapshot.
                    more_events |= last < until;
                    cursors.insert(s.clone(), json!(last));
                }
            }

            let mut interruptions = vec![];
            for ((s, agent), snap) in members.iter().zip(&snaps) {
                let approvals = self.profile(agent).approvals;
                for raw in snap["interactions"].as_array().into_iter().flatten() {
                    let tool = raw["call"]["name"].as_str().unwrap_or_default();
                    // The auto-approver handles these unless it failed.
                    let failed = raw["id"]
                        .as_str()
                        .is_some_and(|id| self.auto_approval_failures.lock().unwrap().contains(id));
                    if raw["kind"] == "approval" && approvals.auto_approves(tool) && !failed {
                        continue;
                    }
                    interruptions.push(interruption(s, agent, raw, snap));
                }
            }

            let lead = &snaps[0];
            let lead_turn = &lead["turn"];
            let pending_instances = if self
                .store
                .lock()
                .unwrap()
                .metadata(&sid)?
                .agent_team
                .is_some()
            {
                let mut db = self.store.lock().unwrap();
                let doc: openworker_runtime::agent_team::Document =
                    serde_json::from_value(db.agent_team(&sid, "snapshot", &json!({}), None)?)?;
                let mut pending = false;
                if !doc.stopped {
                    for i in doc.roster.instances() {
                        if i.phase == openworker_runtime::team_coordination::Phase::Reserved {
                            pending = true;
                            continue;
                        }
                        if i.phase != openworker_runtime::team_coordination::Phase::Ready {
                            continue;
                        }
                        let session = db.metadata(&i.session_id)?;
                        if i.agent_id != doc.lead && session.turn.is_none() {
                            pending = true;
                        }
                        if !session.closed
                            && session
                                .turn
                                .as_ref()
                                .is_some_and(|t| t.status == "completed")
                            && db.agent_team(&i.session_id, "pending_inbox", &json!({}), None)?
                                == true
                        {
                            pending = true;
                        }
                    }
                }
                pending
            } else {
                false
            };
            let all_idle = snaps
                .iter()
                .all(|s| s["turn"]["status"].as_str().is_none_or(terminal));
            let status = if let Some(result) = &cached {
                result["status"]
                    .as_str()
                    .context("Invalid saved run status")?
            } else if !interruptions.is_empty() {
                "interrupted"
            } else if pending_instances
                || !all_idle
                || board.as_ref().is_some_and(|b| b["pending_work"] != false)
            {
                "running"
            } else {
                terminal_status(lead_turn)
            };

            if status != "running" || !events.is_empty() || Instant::now() >= deadline {
                let mut out = cached.clone().unwrap_or_else(|| json!({
                    "status": status,
                    "final_output": final_output(lead),
                    "interruptions": interruptions,
                    "submission_id": record.as_ref().map(|r| &r["submission_id"]),
                    "cursor": lead["cursor"],
                    "board": board.as_ref().map(|b| b.get("board").cloned().unwrap_or(b.clone())),
                }));
                if status == "failed" && cached.is_none() {
                    out["failure"] = lead_turn["status"].clone();
                }
                if cached.is_none() && status != "running" && status != "interrupted" {
                    if let Some(r) = record.as_mut() {
                        r["result"] = out.clone();
                        r["end_cursors"] = json!(members
                            .iter()
                            .zip(&snaps)
                            .map(|((s, _), snap)| (s.clone(), snap["cursor"].clone()))
                            .collect::<Map<_, _>>());
                        self.save_run(&sid, r)?;
                    }
                }
                if let Some(c) = cursors {
                    out["events"] = json!(events);
                    out["cursors"] = Value::Object(c);
                    out["more_events"] = json!(more_events);
                }
                return Ok(out);
            }
            drop(admission);

            // Wake early on activity from a member, else re-observe shortly.
            let nap = deadline
                .saturating_duration_since(Instant::now())
                .min(Duration::from_millis(250));
            let wake = async {
                loop {
                    match rx.recv().await {
                        Ok(e) if members.iter().any(|(s, _)| *s == e.session_id) => break,
                        Ok(_) => {}
                        Err(_) => break,
                    }
                }
            };
            let _ = tokio::time::timeout(nap, wake).await;
        }
    }
}

fn text<'a>(p: &'a Value, key: &str) -> Result<&'a str> {
    let s = p[key]
        .as_str()
        .with_context(|| format!("{key} must be a string"))?;
    ensure!(!s.trim().is_empty() && s.len() <= 100, "Invalid {key}");
    Ok(s)
}

fn terminal_status(turn: &Value) -> &'static str {
    match turn["status"].as_str().unwrap_or("") {
        "completed" | "" => "completed",
        "cancelled" => "cancelled",
        _ => "failed",
    }
}

/// Uniform interruption shape. Only tool approvals take a `decision`;
/// questions, compaction choices and future kinds take an `answer`.
fn interruption(sid: &str, agent: &str, raw: &Value, snapshot: &Value) -> Value {
    let call = &raw["call"];
    let name = call["name"].as_str().unwrap_or_default();
    let arguments: Value = call["arguments"]
        .as_str()
        .and_then(|s| serde_json::from_str(s).ok())
        .unwrap_or_else(|| call["arguments"].clone());
    if raw["kind"] == "approval" {
        json!({"id": raw["id"], "session_id": sid, "agent": agent, "kind": "approval",
               "tool": name, "arguments": arguments,
               "category": snapshot["session"]["tool_policy"]["tools"][name]["category"]})
    } else {
        json!({"id": raw["id"], "session_id": sid, "agent": agent, "kind": "question",
               "question": arguments["question"].as_str().unwrap_or_default(),
               "options": arguments["options"].as_array().cloned().unwrap_or_default()})
    }
}

/// Last assistant message after the most recent user message.
fn final_output(snapshot: &Value) -> Value {
    for m in snapshot["messages"].as_array().into_iter().flatten().rev() {
        if m["role"] == "user" {
            return Value::Null;
        }
        if m["type"] == "message" {
            let text: Vec<&str> = m["content"]
                .as_array()
                .into_iter()
                .flatten()
                .filter_map(|c| c["text"].as_str())
                .collect();
            if !text.is_empty() {
                return json!(text.join("\n"));
            }
        }
    }
    Value::Null
}

/// A journal event plus a small typed summary SDKs can map directly.
fn stream_item(agent: &str, e: Event) -> Value {
    let summary = match e.kind.as_str() {
        "interaction_resolved" => (e.data["origin"] == "app_policy")
            .then(|| json!({"type": "auto_approved", "tool": e.data["tool"]})),
        "assistant_message" => {
            let text: Vec<&str> = e.data["messages"]
                .as_array()
                .into_iter()
                .flatten()
                .flat_map(|m| m["content"].as_array().into_iter().flatten())
                .filter_map(|c| c["text"].as_str())
                .collect();
            (!text.is_empty()).then(|| json!({"type": "message", "text": text.join("\n")}))
        }
        "tool_started" | "tool_finished" => Some(json!({
            "type": e.kind,
            "tool": e.data["name"].as_str().or_else(|| e.data["call"]["name"].as_str()),
        })),
        "plan_updated" => Some(json!({"type": "plan_updated", "plan": e.data["plan"]})),
        _ => None,
    };
    json!({"agent": agent, "session_id": e.session_id, "event": e, "summary": summary})
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn interruption_shapes() {
        let snap = json!({"session":{"tool_policy":{"tools":{"files__write":{"category":"write_local"}}}}});
        let a = interruption(
            "s",
            "a",
            &json!({"id":"i","kind":"approval","call":{"name":"files__write","arguments":"{\"path\":\"x\"}"}}),
            &snap,
        );
        assert_eq!(a["kind"], "approval");
        assert_eq!(a["category"], "write_local");
        assert_eq!(a["arguments"]["path"], "x");
        let c = interruption(
            "s",
            "a",
            &json!({"id":"c","kind":"compaction","call":{"name":"compaction","arguments":"{\"question\":\"How?\",\"options\":[\"Retry\"]}"}}),
            &snap,
        );
        assert_eq!(c["kind"], "question");
        assert_eq!(c["options"], json!(["Retry"]));
    }

    #[test]
    fn final_output_only_after_latest_user_message() {
        let done = json!({"messages":[{"role":"user","content":"hi"},
            {"type":"message","content":[{"type":"output_text","text":"a"}]}]});
        assert_eq!(final_output(&done), json!("a"));
        let pending = json!({"messages":[{"type":"message","content":[{"type":"output_text","text":"a"}]},
            {"role":"user","content":"again"}]});
        assert_eq!(final_output(&pending), Value::Null);
    }

    #[test]
    fn busy_errors_are_recognised() {
        assert!(busy(&anyhow::anyhow!(
            "Interaction is checkpointing; retry this same response"
        )));
        assert!(busy(&anyhow::anyhow!("Runtime concurrency limit reached")));
        assert!(!busy(&anyhow::anyhow!("Approval binding mismatch")));
    }
}
