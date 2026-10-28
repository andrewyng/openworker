//! Optional PostgreSQL StateStore adapter. No automatic claims, heartbeat or replay.
mod artifacts;
mod assistant;
pub mod auth;
mod controls;
mod db;
mod environments;
mod ingress;
mod operations;
pub mod pools;
pub use operations::OperationalSnapshot;
mod sdk_host;
mod service;
pub mod service_admin;
pub mod service_catalog;
pub mod service_pools;
mod submissions;
mod team_execution;
pub mod teams;
pub mod worker;
use anyhow::{bail, Context, Result};
pub use ingress::{CreationReceipt, SessionCreation};
use openworker_runtime::telemetry::{Operation as Op, Outcome, Telemetry};
use openworker_runtime::{
    provider::Failure,
    store::{Event, Session, StateStore},
};
use serde_json::Value;
use std::{
    path::PathBuf,
    sync::{
        atomic::{AtomicBool, Ordering},
        mpsc, Arc,
    },
    time::{Duration, Instant},
};
pub use submissions::{SubmissionError, SubmissionReceipt, SubmissionStatus, TurnSubmission};
use zeroize::Zeroizing;

#[derive(Clone)]
pub enum Tls {
    /// System roots plus optional PEM CA bundle (256 KiB/32 roots maximum).
    /// Hostname validation is always required.
    Verified { ca_file: Option<PathBuf> },
    /// Explicit evaluation only; accepted for a single TCP loopback host.
    LoopbackPlaintext,
}
#[derive(Clone)]
pub struct Config {
    pub telemetry: Telemetry,
    // Intentionally no Debug: connection strings can contain credentials.
    pub connection: Zeroizing<String>,
    pub schema: String,
    pub tls: Tls,
    pub lease_duration: Duration,
    pub timeout: Duration,
    /// Maximum waiting turns per session (1..=100); excludes the active turn.
    pub max_queued_turns: u16,
}
impl Config {
    pub fn new(connection: String, schema: String) -> Self {
        Self {
            telemetry: Telemetry::default(),
            connection: Zeroizing::new(connection),
            schema,
            tls: Tls::Verified { ca_file: None },
            lease_duration: Duration::from_secs(30),
            timeout: Duration::from_secs(5),
            max_queued_turns: 100,
        }
    }
    fn validate(&self) -> Result<()> {
        if !(1..=100).contains(&self.max_queued_turns) {
            bail!("PostgreSQL queued-turn limit must be between 1 and 100");
        }
        if self.schema.is_empty()
            || self.schema.len() > 48
            || !self.schema.starts_with("ow_")
            || !self
                .schema
                .bytes()
                .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'_')
        {
            bail!("PostgreSQL schema must be a lowercase ow_ identifier of at most 48 bytes");
        }
        if !(Duration::from_secs(1)..=Duration::from_secs(300)).contains(&self.lease_duration)
            || !(Duration::from_millis(100)..=Duration::from_secs(30)).contains(&self.timeout)
        {
            bail!("Invalid PostgreSQL lease or operation timeout");
        }
        Ok(())
    }
}
#[derive(Clone, Debug)]
pub struct Lease {
    pub session_id: String,
    pub owner: String,
    pub epoch: i64,
}
struct Request {
    deadline: Instant,
    op: Box<dyn FnOnce(&mut db::Database) + Send>,
}
struct Handle {
    telemetry: Telemetry,
    tx: mpsc::SyncSender<Request>,
    poisoned: Arc<AtomicBool>,
    timeout: Duration,
}
/// Clones share one bounded connection/actor and one worker incarnation.
#[derive(Clone)]
pub struct PostgresStore {
    inner: Arc<Handle>,
    auth: Option<auth::Context>,
}

fn blocking<T>(f: impl FnOnce() -> Result<T>) -> Result<T> {
    match tokio::runtime::Handle::try_current() {
        Ok(h) if h.runtime_flavor()==tokio::runtime::RuntimeFlavor::MultiThread => tokio::task::block_in_place(f),
        Ok(_) => bail!("PostgreSQL synchronous bridge requires a multi-threaded Tokio host or a blocking thread"),
        Err(_) => f(),
    }
}
impl PostgresStore {
    /// Trusted host catalog API. The caller supplies principal/session
    /// authorization; model output is not authority to publish an artifact.
    pub fn register_artifact(
        &self,
        request: openworker_artifacts::Registration,
        binding: openworker_artifacts::Binding,
        actor: &str,
    ) -> Result<openworker_artifacts::Artifact> {
        request.validate()?;
        binding.validate()?;
        submissions::validate_id(actor, 128)?;
        let actor = actor.to_owned();
        self.call(move |db| db.register_artifact(request, binding, &actor))
    }
    pub fn artifact(&self, sid: &str, id: &str) -> Result<openworker_artifacts::Artifact> {
        submissions::validate_id(sid, 80)?;
        submissions::validate_id(id, 40)?;
        let (sid, id) = (sid.to_owned(), id.to_owned());
        self.call(move |db| db.artifact(&sid, &id))
    }
    pub fn artifacts(&self, sid: &str) -> Result<Vec<openworker_artifacts::Artifact>> {
        submissions::validate_id(sid, 80)?;
        let sid = sid.to_owned();
        self.call(move |db| db.artifacts(&sid))
    }
    /// Only call after the configured store verified bytes and manifest. No API
    /// route accepts an unverified client proof or client-controlled ready flag.
    pub fn publish_artifact(
        &self,
        sid: &str,
        id: &str,
        proof: openworker_artifacts::Proof,
        actor: &str,
    ) -> Result<openworker_artifacts::Artifact> {
        submissions::validate_id(sid, 80)?;
        submissions::validate_id(id, 40)?;
        submissions::validate_id(actor, 128)?;
        proof.expected.validate()?;
        openworker_artifacts::checksum(&proof.manifest_sha256)?;
        let (sid, id, actor) = (sid.to_owned(), id.to_owned(), actor.to_owned());
        self.call(move |db| db.publish_artifact(&sid, &id, proof, &actor))
    }
    pub fn telemetry(&self) -> Telemetry {
        self.inner.telemetry.clone()
    }
    pub fn ready(&self) -> Result<()> {
        self.call(|db| {
            db.client.simple_query("SELECT 1")?;
            Ok(())
        })
    }
    /// Trusted host only, never exposed through session-authorized HTTP handles.
    pub fn operational_snapshot(
        &self,
        stalled_after: u64,
        recent_window: u64,
    ) -> Result<OperationalSnapshot> {
        self.call(move |db| db.operational_snapshot(stalled_after, recent_window))
    }
    pub fn creation(&self, request: SessionCreation) -> Result<Option<CreationReceipt>> {
        request.validate()?;
        self.call(move |db| db.creation(request))
    }
    pub fn create_session(
        &self,
        request: SessionCreation,
        candidate: Session,
    ) -> Result<CreationReceipt> {
        request.validate()?;
        let mut span = self
            .telemetry()
            .span(Op::Admission, None, Some(&request.request_id));
        let result = self.call(move |db| db.create_session(request, candidate));
        if let Ok(receipt) = &result {
            span.session(&receipt.session_id);
        }
        span.finish(if result.is_ok() {
            Outcome::Ok
        } else {
            Outcome::Error
        });
        result
    }
    /// Trusted caller authorizes directory access. Bounded operational rows,
    /// keyset-paginated; separate pages are not a frozen database snapshot.
    pub fn session_page(&self, after: Option<String>, limit: u16) -> Result<Value> {
        if !(1..=100).contains(&limit) {
            return Err(SubmissionError::InvalidRequest.into());
        }
        if let Some(id) = &after {
            submissions::validate_id(id, 80)?;
        }
        self.call(move |db| db.session_page(after.as_deref(), limit))
    }
    /// API-scoped handles authorize access; snapshot/cursor share a transaction.
    pub fn read_snapshot(&self, sid: &str) -> Result<Value> {
        let sid = sid.to_owned();
        self.call(move |db| db.read_snapshot(&sid))
    }
    pub fn read_events(&self, sid: &str, after: i64) -> Result<Vec<Event>> {
        let sid = sid.to_owned();
        self.call(move |db| db.events_for_api(&sid, after))
    }
    /// Atomic bounded scheduling admission; only an otherwise unclaimed store.
    pub fn claim_next(&self, selector: worker::Selector) -> Result<Option<Lease>> {
        selector.validate()?;
        self.call(move |db| db.claim_next(&selector))
    }
    pub fn begin_environment(
        &self,
        request_id: &str,
        actor: &str,
        spec: openworker_runtime::environment::Spec,
    ) -> Result<(openworker_runtime::environment::Record, bool)> {
        let (request_id, actor) = (request_id.to_owned(), actor.to_owned());
        self.call(move |db| db.begin_environment(&request_id, &actor, spec))
    }
    pub fn complete_environment(
        &self,
        id: &str,
        revision: i64,
        binding: Option<openworker_runtime::environment::Binding>,
    ) -> Result<openworker_runtime::environment::Record> {
        let id = id.to_owned();
        self.call(move |db| db.complete_environment(&id, revision, binding))
    }
    pub fn begin_environment_delete(
        &self,
        id: &str,
        request_id: &str,
        actor: &str,
    ) -> Result<(openworker_runtime::environment::Record, bool)> {
        let (id, key, actor) = (id.to_owned(), request_id.to_owned(), actor.to_owned());
        self.call(move |db| db.begin_environment_delete(&id, &key, &actor))
    }
    pub fn complete_environment_delete(
        &self,
        id: &str,
        request_id: &str,
        revision: i64,
    ) -> Result<openworker_runtime::environment::Record> {
        let (id, key) = (id.to_owned(), request_id.to_owned());
        self.call(move |db| db.complete_environment_delete(&id, &key, revision))
    }
    pub fn submit_control(
        &self,
        command: openworker_runtime::control::Command,
    ) -> Result<openworker_runtime::control::Record> {
        command
            .validate()
            .map_err(|_| SubmissionError::InvalidRequest)?;
        let span = self.telemetry().span(
            Op::Admission,
            Some(&command.session_id),
            Some(&command.request_id),
        );
        let result = self.call(move |db| db.submit_control(command));
        span.finish(if result.is_ok() {
            Outcome::Ok
        } else {
            Outcome::Error
        });
        result
    }
    pub fn control_receipt(
        &self,
        sid: &str,
        key: &str,
    ) -> Result<openworker_runtime::control::Record> {
        submissions::validate_id(sid, 80)?;
        submissions::validate_id(key, 100)?;
        let (sid, key) = (sid.to_owned(), key.to_owned());
        self.call(move |db| db.control_record(&sid, &key, false))
    }
    pub fn next_control(&self, sid: &str) -> Result<Option<openworker_runtime::control::Record>> {
        let sid = sid.to_owned();
        self.call(move |db| db.next_control(&sid))
    }
    /// Trusted application ingress; caller must authorize access to this session.
    /// Does not acquire a worker lease or dispatch any work.
    pub fn submit_turn(&self, request: TurnSubmission) -> Result<SubmissionReceipt> {
        request.validate()?;
        let span = self.telemetry().span(
            Op::Admission,
            Some(&request.session_id),
            Some(&request.submission_id),
        );
        let result = self.call(move |db| db.submit_turn(request));
        span.finish(if result.is_ok() {
            Outcome::Ok
        } else {
            Outcome::Error
        });
        result
    }
    /// Reconcile response loss using the stable submission ID, without ownership.
    pub fn submission(&self, session_id: &str, submission_id: &str) -> Result<SubmissionReceipt> {
        submissions::validate_id(session_id, 80)?;
        submissions::validate_id(submission_id, 100)?;
        let (sid, key) = (session_id.to_owned(), submission_id.to_owned());
        self.call(move |db| db.submission(&sid, &key))
    }
    /// Owner-only peek, not a dequeue. Feed to the SAME injected runtime's
    /// turn/start; its StateStore.accept atomically checks FIFO and starts it.
    pub fn next_turn(&self, session_id: &str) -> Result<Option<TurnSubmission>> {
        submissions::validate_id(session_id, 80)?;
        let sid = session_id.to_owned();
        self.call(move |db| db.next_turn(&sid))
    }
    /// Explicit, advisory-locked migration. Never resets an existing schema.
    pub fn migrate(config: &Config) -> Result<()> {
        config.validate()?;
        let c = config.clone();
        let timeout = c.timeout;
        let (tx, rx) = mpsc::sync_channel(1);
        blocking(move || {
            std::thread::Builder::new()
                .name("ow-pg-migrate".into())
                .spawn(move || {
                    let result = db::migrate(&c).map_err(sanitize);
                    let _ = tx.send(result);
                })?;
            rx.recv_timeout(timeout)
                .context("PostgreSQL migration outcome unknown; inspect schema before retrying")?
        })
    }
    pub fn open(config: Config) -> Result<Self> {
        config.validate()?;
        let telemetry = config.telemetry.clone();
        let timeout = config.timeout;
        let (tx, rx) = mpsc::sync_channel::<Request>(16);
        let (ready, wait) = mpsc::sync_channel(1);
        let poisoned = Arc::new(AtomicBool::new(false));
        let poison = poisoned.clone();
        blocking(move || {
            std::thread::Builder::new()
                .name("ow-pg-store".into())
                .spawn(move || {
                    let mut database = match db::Database::open(config) {
                        Ok(db) => db,
                        Err(e) => {
                            let _ = ready.send(Err(sanitize(e)));
                            return;
                        }
                    };
                    if ready.send(Ok(())).is_err() {
                        return;
                    }
                    while let Ok(request) = rx.recv() {
                        if poison.load(Ordering::SeqCst) {
                            break;
                        }
                        if Instant::now() >= request.deadline {
                            poison.store(true, Ordering::SeqCst);
                            break;
                        }
                        (request.op)(&mut database);
                    }
                })?;
            wait.recv_timeout(timeout)
                .context("PostgreSQL connection unavailable")??;
            Ok(Self {
                auth: None,
                inner: Arc::new(Handle {
                    telemetry,
                    tx,
                    poisoned,
                    timeout,
                }),
            })
        })
    }
    fn call<T: Send + 'static>(
        &self,
        f: impl FnOnce(&mut db::Database) -> Result<T> + Send + 'static,
    ) -> Result<T> {
        let inner = self.inner.clone();
        let auth = self.auth.clone();
        let span = inner.telemetry.span(Op::Storage, None, None);
        let result = blocking(move || {
            if inner.poisoned.load(Ordering::SeqCst) {
                bail!("PostgreSQL connection unusable; reconnect and reconcile durable state");
            }
            let (tx, rx) = mpsc::sync_channel(1);
            let poison = inner.poisoned.clone();
            inner
                .tx
                .try_send(Request {
                    deadline: Instant::now() + inner.timeout,
                    op: Box::new(move |db| {
                        db.auth = auth;
                        let result = f(db).map_err(|e| {
                            if e.downcast_ref::<postgres::Error>().is_some() {
                                poison.store(true, Ordering::SeqCst);
                            }
                            sanitize(e)
                        });
                        db.auth = None;
                        let _ = tx.send(result);
                    }),
                })
                .map_err(|_| {
                    anyhow::anyhow!("PostgreSQL queue unavailable; request not admitted")
                })?;
            match rx.recv_timeout(inner.timeout) {
                Ok(v) => v,
                Err(_) => {
                    inner.poisoned.store(true, Ordering::SeqCst);
                    bail!("PostgreSQL operation outcome unknown; reconnect and reconcile durable IDs before retrying")
                }
            }
        });
        span.finish(if result.is_ok() {
            Outcome::Ok
        } else {
            Outcome::Error
        });
        result
    }
    pub fn claim(&self, sid: &str) -> Result<Lease> {
        let sid = sid.to_owned();
        self.call(move |db| db.claim(&sid))
    }
    /// Claim a dedicated SDK schema. Rejects concurrent SDK owners and adoption
    /// of existing distributed sessions. Connection loss releases the host lock;
    /// ordinary session epochs continue to fence every state/effect checkpoint.
    pub fn claim_sdk_host(&self, coworkers: Vec<String>) -> Result<()> {
        self.call(move |db| db.claim_sdk_host(&coworkers))
    }
    pub fn renew(&self, sid: &str) -> Result<Lease> {
        let span = self.telemetry().span(Op::Lease, Some(sid), None);
        let sid = sid.to_owned();
        let result = self.call(move |db| db.renew(&sid));
        span.finish(if result.is_ok() {
            Outcome::Ok
        } else {
            Outcome::Error
        });
        result
    }
    pub fn release(&self, sid: &str) -> Result<()> {
        let sid = sid.to_owned();
        self.call(move |db| db.release(&sid))
    }
    /// Trusted app read, without taking execution ownership. Not an authorization API.
    pub fn inspect(&self, sid: &str) -> Result<Session> {
        let sid = sid.to_owned();
        self.call(move |db| db.inspect(&sid))
    }
    pub fn audit(&self, sid: &str, after: i64) -> Result<Vec<Value>> {
        let sid = sid.to_owned();
        self.call(move |db| db.audit(&sid, after))
    }
}
fn sanitize(e: anyhow::Error) -> anyhow::Error {
    if e.downcast_ref::<postgres::Error>().is_some() {
        anyhow::anyhow!("PostgreSQL operation failed; outcome may be unknown. Reconnect and reconcile durable IDs.")
    } else {
        e
    }
}

impl StateStore for PostgresStore {
    fn agent_run(&self, sid: &str, submission: Option<&str>) -> Result<Option<Value>> {
        let (sid, submission) = (sid.to_owned(), submission.map(str::to_owned));
        self.call(move |db| db.agent_run(&sid, submission.as_deref()))
    }
    fn team_create(&mut self, r: openworker_runtime::team_service::Create) -> Result<Value> {
        self.create_team(r)
    }
    fn team_snapshot(&self, id: &str) -> Result<Value> {
        PostgresStore::team_snapshot(self, id)
    }
    fn team_command(
        &mut self,
        id: &str,
        c: openworker_runtime::team_service::Command,
    ) -> Result<Value> {
        PostgresStore::team_command(self, id, c)
    }
    fn team_events(&self, id: &str, after: i64) -> Result<Value> {
        PostgresStore::team_events(self, id, after)
    }
    fn team_tick(&mut self) -> Result<()> {
        self.call(|db| db.dispatch_team_completion())
    }
    fn team_next(&self, sid: &str) -> Result<Option<(String, String)>> {
        Ok(self.next_turn(sid)?.map(|t| (t.submission_id, t.text)))
    }
    fn supports_teams(&self) -> bool {
        true
    }
    fn team_context(&self, sid: &str) -> Result<Option<openworker_runtime::teams::Context>> {
        let sid = sid.to_owned();
        self.call(move |db| db.team_context(&sid))
    }
    fn team_call(&mut self, sid: &str, tid: &str, call: &Value, args: &Value) -> Result<Event> {
        let (sid, tid, call, args) = (sid.to_owned(), tid.to_owned(), call.clone(), args.clone());
        self.call(move |db| db.team_call(&sid, &tid, call, args))
    }
    fn assistant_call(
        &mut self,
        sid: &str,
        tid: &str,
        call: &Value,
        args: &Value,
    ) -> Result<Event> {
        let (sid, tid, call, args) = (sid.to_owned(), tid.to_owned(), call.clone(), args.clone());
        self.call(move |db| db.assistant_call(&sid, &tid, &call, &args))
    }
    fn environment(&self, id: &str) -> Result<openworker_runtime::environment::Record> {
        let id = id.to_owned();
        self.call(move |db| db.environment(&id))
    }
    fn reserve_attachment(
        &mut self,
        sid: &str,
        server: &str,
    ) -> Result<(openworker_runtime::environment::Attachment, bool)> {
        let (sid, server) = (sid.to_owned(), server.to_owned());
        self.call(move |db| db.reserve_attachment(&sid, &server))
    }
    fn settle_attachment(
        &mut self,
        sid: &str,
        id: &str,
        released: bool,
        unknown: bool,
    ) -> Result<()> {
        let (sid, id) = (sid.to_owned(), id.to_owned());
        self.call(move |db| db.settle_attachment(&sid, &id, released, unknown))
    }
    fn abandon_attachments(&mut self, sid: &str) -> Result<()> {
        let sid = sid.to_owned();
        self.call(move |db| db.abandon_attachments(&sid))
    }
    fn control(&self, sid: &str, key: &str) -> Result<openworker_runtime::control::Record> {
        let (sid, key) = (sid.to_owned(), key.to_owned());
        self.call(move |db| db.control_record(&sid, &key, true))
    }
    fn settle_control(
        &mut self,
        sid: &str,
        key: &str,
        rejected: bool,
        reason: &str,
    ) -> Result<Event> {
        let (sid, key, reason) = (sid.to_owned(), key.to_owned(), reason.to_owned());
        self.call(move |db| db.settle_control(&sid, &key, rejected, &reason))
    }
    fn uncertain_calls(&self, sid: &str) -> Result<Vec<Value>> {
        let sid = sid.to_owned();
        self.call(move |db| db.uncertain_calls(&sid))
    }
    fn get(&self, id: &str) -> Result<Session> {
        let id = id.to_owned();
        self.call(move |d| d.get(&id, true))
    }
    fn metadata(&self, id: &str) -> Result<Session> {
        let id = id.to_owned();
        self.call(move |d| d.get(&id, false))
    }
    fn list(&self) -> Result<Vec<Session>> {
        self.call(|d| d.list(true))
    }
    fn list_metadata(&self) -> Result<Vec<Session>> {
        self.call(|d| d.list(false))
    }
    fn recent_messages(&self, sid: &str) -> Result<(Vec<Value>, bool)> {
        let sid = sid.to_owned();
        self.call(move |d| d.recent(&sid))
    }
    fn save(&mut self, s: &Session, kind: &str, data: Value) -> Result<Event> {
        let (s, kind) = (s.clone(), kind.to_owned());
        self.call(move |d| d.save(&s, &kind, data))
    }
    fn append_event(&mut self, sid: &str, kind: &str, data: Value) -> Result<Event> {
        let (sid, kind) = (sid.to_owned(), kind.to_owned());
        self.call(move |d| d.append(&sid, &kind, data))
    }
    fn start_attempt(&mut self, sid: &str, id: &str, number: u32) -> Result<Event> {
        let (sid, id) = (sid.to_owned(), id.to_owned());
        self.call(move |d| d.start_attempt(&sid, &id, number))
    }
    fn attempt_delta(&mut self, sid: &str, id: &str, text: &str) -> Result<Event> {
        let (sid, id, text) = (sid.to_owned(), id.to_owned(), text.to_owned());
        self.call(move |d| d.attempt_delta(&sid, &id, &text))
    }
    fn finish_attempt(
        &mut self,
        sid: &str,
        id: &str,
        status: &str,
        kind: Option<Failure>,
    ) -> Result<Event> {
        let (sid, id, status) = (sid.to_owned(), id.to_owned(), status.to_owned());
        self.call(move |d| d.finish_attempt(&sid, &id, &status, kind))
    }
    fn interrupt_attempts(&mut self, sid: &str) -> Result<()> {
        let sid = sid.to_owned();
        self.call(move |d| d.interrupt_attempts(&sid))
    }
    fn attempts(&self, sid: &str) -> Result<Vec<Value>> {
        let sid = sid.to_owned();
        self.call(move |d| d.attempts(&sid))
    }
    fn history_page(&self, sid: &str, after: usize) -> Result<Value> {
        let sid = sid.to_owned();
        self.call(move |d| d.history_page(&sid, after))
    }
    fn transcript_excerpt(&self, sid: &str, after: usize, offset: usize) -> Result<Value> {
        let page = self.history_page(sid, after)?;
        db::excerpt(page, offset)
    }
    fn accept(
        &mut self,
        sid: &str,
        key: &str,
        text: &str,
    ) -> Result<(Session, bool, Option<Event>)> {
        let (sid, key, text) = (sid.to_owned(), key.to_owned(), text.to_owned());
        self.call(move |d| d.accept(&sid, &key, &text))
    }
    fn cursor(&self, sid: &str) -> Result<i64> {
        let sid = sid.to_owned();
        self.call(move |d| d.cursor(&sid))
    }
    fn has_submission(&self, sid: &str, key: &str) -> Result<bool> {
        let (sid, key) = (sid.to_owned(), key.to_owned());
        self.call(move |d| d.has_submission(&sid, &key))
    }
    fn events(&self, sid: &str, after: i64) -> Result<Vec<Event>> {
        let sid = sid.to_owned();
        self.call(move |d| d.events(&sid, after))
    }
    fn recover(&mut self) -> Result<()> {
        self.call(|d| d.recover())
    }
}
