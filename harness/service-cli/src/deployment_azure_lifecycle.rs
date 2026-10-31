//! One durable control object outside AKS serializes effects with Blob ETag CAS.
//! A lost connection is an unknown outcome, returned to the caller/LLM. Never
//! replay a write automatically: read/check the original operation first.
use super::api::{Azure, Reply};
use super::*;
use reqwest::Method;

#[derive(Subcommand)]
pub enum Lifecycle {
    Prepare {
        #[arg(long)]
        plan: PathBuf,
        #[arg(long)]
        out: PathBuf,
    },
    /// Observe the original ARM deployment/resources and checkpoint observations.
    Reconcile(Remote),
    /// Explicit experimental submission of one fixed ARM stage; no automatic replay.
    Apply {
        #[command(flatten)]
        remote: Remote,
        #[arg(long, value_enum)]
        stage: Stage,
    },
    /// Begin/advance deletion of one exact owned resource per invocation.
    Destroy(Remote),
    /// Read shared durable control and sanitized status, never raw provider errors.
    Status {
        #[arg(long)]
        state: PathBuf,
    },
}
#[derive(Args)]
pub struct Remote {
    #[arg(long)]
    state: PathBuf,
    #[arg(long)]
    accept: String,
    #[arg(long)]
    allow_experimental_cloud: bool,
}
#[derive(Subcommand)]
pub enum Cleanup {
    /// Export independent Container Apps scheduled-job/control-store ARM graph.
    Package {
        #[arg(long)]
        state: PathBuf,
        #[arg(long)]
        out: PathBuf,
    },
    /// Create the control Blob once, after an administrator installed its resources.
    Initialize(Remote),
    /// Read the shared control heartbeat/configuration; no cloud writes.
    Verify {
        #[arg(long)]
        state: PathBuf,
    },
    /// One scheduled iteration outside AKS. Uses Container Apps managed identity.
    Tick {
        #[arg(long)]
        control: String,
        #[arg(long)]
        tenant: String,
        #[arg(long)]
        subscription: String,
        #[arg(long)]
        client_id: String,
    },
}
#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq, clap::ValueEnum)]
#[serde(rename_all = "snake_case")]
pub enum Stage {
    Foundation,
    Cluster,
}
impl Stage {
    fn name(self) -> &'static str {
        match self {
            Self::Foundation => "foundation",
            Self::Cluster => "cluster",
        }
    }
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "snake_case")]
enum Phase {
    Prepared,
    Intent,
    Unknown,
    Creating,
    Active,
    Failed,
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Operation {
    stage: Stage,
    phase: Phase,
    request_id: String,
    correlation_id: Option<String>,
    operation_url: Option<String>,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Owned {
    id: String,
    incarnation: String,
    deleted: bool,
    delete_request: Option<String>,
    delete_accepted: bool,
    operation_url: Option<String>,
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct GuardReceipt {
    created_at: String,
    principal_id: String,
    client_id: String,
}
impl GuardReceipt {
    fn validate(&self) -> Result<()> {
        ensure!(
            chrono::DateTime::parse_from_rfc3339(&self.created_at).is_ok(),
            "Invalid controller creation receipt"
        );
        for value in [&self.principal_id, &self.client_id] {
            ensure!(
                uuid::Uuid::parse_str(value).is_ok_and(|u| !u.is_nil() && u.to_string() == *value),
                "Invalid controller identity receipt"
            );
        }
        Ok(())
    }
}
#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "snake_case")]
enum StopPhase {
    Intent,
    Unknown,
    Accepted,
    Absent,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Stop {
    request_id: String,
    phase: StopPhase,
    operation_url: Option<String>,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct State {
    schema_version: u16,
    plan: Plan,
    bundle_digest: String,
    nonce: String,
    operations: Vec<Operation>,
    resources: Vec<Owned>,
    heartbeat_unix: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    guard: Option<GuardReceipt>,
    destroy_requested: bool,
    completed_unix: Option<u64>,
    job_stop: Option<Stop>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    installation_digest: Option<String>,
    #[serde(default)]
    installation_closed: bool,
}
fn digest(p: &Plan) -> Result<String> {
    let files = super::super::artifacts(p)?;
    let hashes: BTreeMap<_, _> = files.into_iter().map(|(n, b)| (n, hash(&b))).collect();
    Ok(hash(&serde_json::to_vec(&hashes)?))
}
pub(super) fn control_url(p: &Plan) -> String {
    format!(
        "https://{}ctl.blob.core.windows.net/control/{}.json",
        graph::names(p).storage,
        p.deployment_id
    )
}
fn job_id(p: &Plan) -> String {
    format!(
        "{}/providers/Microsoft.App/jobs/{}-expiry",
        graph::control_scope(p),
        graph::names(p).prefix
    )
}
fn arm_deployment(s: &State, stage: Stage) -> String {
    format!(
        "{}/providers/Microsoft.Resources/deployments/{}-{}",
        scope(&s.plan),
        graph::names(&s.plan).prefix,
        stage.name()
    )
}
impl State {
    fn validate(&self) -> Result<()> {
        self.plan.validate()?;
        ensure!(
            self.schema_version == 1
                && self.plan.azure_infrastructure.is_some()
                && self.bundle_digest == digest(&self.plan)?,
            "Control graph identity mismatch"
        );
        ensure!(
            uuid::Uuid::parse_str(&self.nonce)
                .is_ok_and(|u| !u.is_nil() && u.to_string() == self.nonce),
            "Invalid ownership nonce"
        );
        ensure!(
            self.operations.len() == 2
                && self.operations[0].stage == Stage::Foundation
                && self.operations[1].stage == Stage::Cluster,
            "Invalid stage state"
        );
        for op in &self.operations {
            ensure!(
                uuid::Uuid::parse_str(&op.request_id)
                    .is_ok_and(|u| !u.is_nil() && u.to_string() == op.request_id),
                "Invalid persisted request"
            );
            if matches!(op.phase, Phase::Creating | Phase::Active | Phase::Failed) {
                ensure!(
                    op.correlation_id
                        .as_ref()
                        .is_some_and(|c| uuid::Uuid::parse_str(c).is_ok()),
                    "Missing ARM correlation receipt"
                );
            }
        }
        if let Some(receipt) = &self.guard {
            receipt.validate()?;
        }
        ensure!(
            self.heartbeat_unix.is_none() || self.guard.is_some(),
            "Heartbeat requires an installer-pinned controller receipt"
        );
        if let Some(digest) = &self.installation_digest {
            ensure!(
                digest.len() == 64
                    && digest
                        .bytes()
                        .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c)),
                "Invalid installation receipt digest"
            );
        }
        ensure!(
            !self.installation_closed || self.installation_digest.is_some(),
            "Closed installation lacks original mutation receipt"
        );
        if let Some(stop) = &self.job_stop {
            ensure!(
                self.completed_unix.is_some()
                    && self.guard.is_some()
                    && uuid::Uuid::parse_str(&stop.request_id)
                        .is_ok_and(|u| !u.is_nil() && u.to_string() == stop.request_id),
                "Controller stop requires original completion/identity/request receipts"
            );
        }
        let inventory = graph::inventory(&self.plan);
        let mut ids = std::collections::BTreeSet::new();
        for r in &self.resources {
            ensure!(
                ids.insert(&r.id)
                    && inventory.iter().any(|i| i["id"].as_str() == Some(&r.id))
                    && !r.incarnation.is_empty()
                    && r.incarnation.len() <= 128,
                "Unexpected or duplicate ownership receipt"
            );
            if let Some(request) = &r.delete_request {
                ensure!(
                    uuid::Uuid::parse_str(request)
                        .is_ok_and(|u| !u.is_nil() && u.to_string() == *request),
                    "Invalid deletion request UUID"
                );
            }
            if r.deleted {
                ensure!(
                    r.delete_request.is_some(),
                    "Absence without delete intent is not cleanup proof"
                );
            }
        }
        ensure!(
            self.resources.len() <= inventory.len(),
            "Invalid receipt count"
        );
        Ok(())
    }
    fn status(&self) -> Value {
        let stopped = self
            .job_stop
            .as_ref()
            .is_some_and(|r| r.phase == StopPhase::Absent);
        let attention = self
            .job_stop
            .as_ref()
            .is_some_and(|r| matches!(r.phase, StopPhase::Intent | StopPhase::Unknown));
        json!({"deploymentId":self.plan.deployment_id,"bundleDigest":self.bundle_digest,"control":control_url(&self.plan),"stages":self.operations,"ownedResourceCount":self.resources.len(),"deletedResourceCount":self.resources.iter().filter(|r|r.deleted).count(),"heartbeatUnix":self.heartbeat_unix,"controllerReceipt":self.guard,"controllerStop":self.job_stop,"controllerStopped":stopped,"requiresAttention":attention,"workloadAndControllerComplete":self.completed_unix.is_some()&&stopped,"workloadCompletedUnix":self.completed_unix,"destroyRequested":self.destroy_requested,"completedUnix":self.completed_unix,"retainedControlResources":true,"liveQualified":false})
    }
}
fn local(path: &Path) -> Result<State> {
    let bytes = super::super::read_file(path, "azure-state.json")?;
    let s: State = serde_json::from_slice(&bytes)?;
    s.validate()?;
    Ok(s)
}
fn private_dir(path: &Path) -> Result<()> {
    let mut d = fs::DirBuilder::new();
    #[cfg(unix)]
    {
        use std::os::unix::fs::DirBuilderExt;
        d.mode(0o700);
    }
    d.create(path)
        .context("Use a fresh private output directory")?;
    Ok(())
}
fn put(path: &Path, name: &str, bytes: &[u8]) -> Result<()> {
    let mut o = fs::OpenOptions::new();
    o.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        o.mode(0o600);
    }
    let mut f = o.open(path.join(name))?;
    f.write_all(bytes)?;
    f.sync_all()?;
    Ok(())
}

trait Cloud {
    async fn read_control(&mut self, url: &str) -> Result<Option<(String, State)>>;
    async fn save_control(
        &mut self,
        url: &str,
        etag: Option<&str>,
        state: &State,
    ) -> Result<String>;
    async fn arm(
        &mut self,
        method: Method,
        id: &str,
        api: &str,
        body: Option<&Value>,
        request: &str,
    ) -> Result<Reply>;
}
impl Cloud for Azure {
    async fn read_control(&mut self, url: &str) -> Result<Option<(String, State)>> {
        let r = self.blob(Method::GET, url, None, None).await?;
        if r.status == 404 {
            return Ok(None);
        }
        ensure!(r.status == 200, "Control read rejected");
        let s: State = serde_json::from_value(r.body.context("Control body missing")?)?;
        s.validate()?;
        ensure!(control_url(&s.plan) == url, "Control location mismatch");
        Ok(Some((r.etag.context("Control ETag required")?, s)))
    }
    async fn save_control(&mut self, url: &str, etag: Option<&str>, s: &State) -> Result<String> {
        s.validate()?;
        let r = self
            .blob(Method::PUT, url, etag, Some(&serde_json::to_value(s)?))
            .await?;
        ensure!(
            r.status == 201,
            "Control CAS conflict/unknown; reread before continuing"
        );
        r.etag.context("Control acknowledgement lacks ETag")
    }
    async fn arm(
        &mut self,
        method: Method,
        id: &str,
        api: &str,
        body: Option<&Value>,
        request: &str,
    ) -> Result<Reply> {
        Azure::arm(self, method, id, api, body, request).await
    }
}
async fn save(c: &mut impl Cloud, s: &State, etag: &mut String) -> Result<()> {
    *etag = c.save_control(&control_url(&s.plan), Some(etag), s).await?;
    Ok(())
}
fn owned_body(s: &State, r: &Value, expected: &Value) -> Result<String> {
    ensure!(
        r["id"]
            .as_str()
            .is_some_and(|id| id.eq_ignore_ascii_case(expected["id"].as_str().unwrap()))
            && r["tags"]["ows-deployment"] == s.plan.deployment_id
            && r["tags"]["ows-nonce"] == s.nonce
            && r["tags"]["ows-bundle"] == s.bundle_digest,
        "Resource ownership mismatch; names/tags alone are not receipts"
    );
    let receipt = r
        .pointer(expected["incarnationPointer"].as_str().unwrap())
        .and_then(Value::as_str)
        .filter(|v| !v.is_empty() && v.len() <= 128)
        .context("Provider immutable incarnation receipt unavailable; refuse cleanup")?;
    Ok(receipt.to_owned())
}
async fn observe(c: &mut impl Cloud, s: &mut State, e: &mut String) -> Result<()> {
    for index in 0..s.operations.len() {
        if s.operations[index].phase == Phase::Prepared {
            continue;
        }
        let op = s.operations[index].clone();
        let r = c
            .arm(
                Method::GET,
                &arm_deployment(s, op.stage),
                "2022-09-01",
                None,
                &op.request_id,
            )
            .await?;
        if r.status == 404 {
            continue;
        } // Absence never grants a second PUT.
        ensure!(r.status == 200, "ARM observation rejected");
        let v = r.body.context("Missing deployment observation")?;
        let p = &v["properties"];
        ensure!(
            p["parameters"]["ownershipNonce"]["value"] == s.nonce
                && p["parameters"]["bundleDigest"]["value"] == s.bundle_digest,
            "Original deployment identity mismatch"
        );
        let correlation = p["correlationId"]
            .as_str()
            .filter(|v| uuid::Uuid::parse_str(v).is_ok())
            .context("ARM correlation receipt missing")?
            .to_owned();
        if let Some(old) = &op.correlation_id {
            ensure!(old == &correlation, "Original deployment was replaced");
        }
        s.operations[index].correlation_id = Some(correlation);
        s.operations[index].phase = match p["provisioningState"].as_str() {
            Some("Succeeded") => Phase::Active,
            Some("Failed" | "Canceled") => Phase::Failed,
            _ => Phase::Creating,
        };
        save(c, s, e).await?;
    }
    for expected in graph::inventory(&s.plan) {
        let id = expected["id"].as_str().unwrap();
        let stage = if expected["stage"] == "cluster" {
            Stage::Cluster
        } else {
            Stage::Foundation
        };
        if s.operations
            .iter()
            .find(|o| o.stage == stage)
            .unwrap()
            .correlation_id
            .is_none()
        {
            continue;
        }
        let r = c
            .arm(
                Method::GET,
                id,
                expected["apiVersion"].as_str().unwrap(),
                None,
                &s.nonce,
            )
            .await?;
        let prior = s.resources.iter().position(|r| r.id == id);
        if r.status == 404 {
            if let Some(i) = prior {
                ensure!(
                    s.resources[i].delete_request.is_some(),
                    "Owned resource disappeared without delete intent"
                );
                s.resources[i].deleted = true;
                save(c, s, e).await?;
            }
            continue;
        }
        ensure!(r.status == 200, "Resource observation rejected");
        let incarnation = owned_body(
            s,
            &r.body.context("Missing resource observation")?,
            &expected,
        )?;
        if let Some(i) = prior {
            ensure!(
                s.resources[i].incarnation == incarnation && !s.resources[i].deleted,
                "Owned resource was replaced; no deletion allowed"
            );
        } else {
            s.resources.push(Owned {
                id: id.into(),
                incarnation,
                deleted: false,
                delete_request: None,
                delete_accepted: false,
                operation_url: None,
            });
            save(c, s, e).await?;
        }
    }
    Ok(())
}
async fn apply(
    c: &mut impl Cloud,
    s: &mut State,
    e: &mut String,
    stage: Stage,
    at: u64,
) -> Result<()> {
    ensure!(
        at < s.plan.expires_at_unix && !s.destroy_requested && s.completed_unix.is_none(),
        "Creation expired or cleanup requested"
    );
    ensure!(
        s.guard.is_some() && s.heartbeat_unix.is_some_and(|t| t <= at && at - t <= 600),
        "Independent cleanup heartbeat is missing/stale"
    );
    let i = if stage == Stage::Foundation { 0 } else { 1 };
    ensure!(
        s.operations[i].phase == Phase::Prepared,
        "Original request already started; reconcile, never automatically retry"
    );
    if stage == Stage::Cluster {
        ensure!(
            s.operations[0].phase == Phase::Active,
            "Foundation must complete before AKS"
        );
    }
    let request = s.operations[i].request_id.clone();
    let r = c
        .arm(
            Method::GET,
            &arm_deployment(s, stage),
            "2022-09-01",
            None,
            &request,
        )
        .await?;
    ensure!(
        r.status == 404,
        "Generated deployment already exists; do not adopt/overwrite"
    );
    for item in graph::inventory(&s.plan)
        .into_iter()
        .filter(|r| r["stage"] == stage.name())
    {
        let r = c
            .arm(
                Method::GET,
                item["id"].as_str().unwrap(),
                item["apiVersion"].as_str().unwrap(),
                None,
                &request,
            )
            .await?;
        ensure!(
            r.status == 404,
            "Existing workload resource refused; no name/tag adoption"
        );
    }
    s.operations[i].phase = Phase::Intent;
    save(c, s, e).await?; // durable CAS before network effect
    let files = graph::artifacts(&s.plan)?;
    let template: Value =
        serde_json::from_slice(&files[&format!("azure-{}.arm.json", stage.name())])?;
    let mut parameters =
        json!({"ownershipNonce":{"value":s.nonce},"bundleDigest":{"value":s.bundle_digest}});
    if stage == Stage::Foundation {
        let v: Value = serde_json::from_slice(&files["azure-foundation.parameters.json"])?;
        parameters["postgresPassword"] = v["parameters"]["postgresPassword"].clone();
    }
    let body =
        json!({"properties":{"mode":"Incremental","template":template,"parameters":parameters}});
    match c
        .arm(
            Method::PUT,
            &arm_deployment(s, stage),
            "2022-09-01",
            Some(&body),
            &request,
        )
        .await
    {
        Ok(r) if [200, 201, 202].contains(&r.status) => {
            s.operations[i].operation_url = r.operation;
            s.operations[i].phase = Phase::Unknown;
            save(c, s, e).await?;
            observe(c, s, e).await
        }
        _ => {
            s.operations[i].phase = Phase::Unknown;
            save(c, s, e).await?;
            bail!("ARM outcome unknown/rejected. Observe the original deployment; no automatic resubmission.")
        }
    }
}
async fn destroy_one(c: &mut impl Cloud, s: &mut State, e: &mut String, at: u64) -> Result<()> {
    if !s.destroy_requested {
        s.destroy_requested = true;
        save(c, s, e).await?;
    }
    observe(c, s, e).await?;
    // Never race an unfinished ARM create against cleanup, or let a 404 create
    // observation erase an uncertain intent. A human can inspect Azure activity.
    ensure!(
        s.operations
            .iter()
            .all(|o| matches!(o.phase, Phase::Prepared | Phase::Active | Phase::Failed)),
        "Creation is still running/unknown; observe before cleanup"
    );
    for expected in graph::inventory(&s.plan) {
        let Some(i) = s
            .resources
            .iter()
            .position(|r| r.id == expected["id"].as_str().unwrap() && !r.deleted)
        else {
            continue;
        };
        if s.resources[i].delete_request.is_some() {
            return Ok(());
        } // GET only on subsequent ticks; even a lost DELETE is not replay permission.
        let id = s.resources[i].id.clone();
        let api = expected["apiVersion"].as_str().unwrap();
        let r = c.arm(Method::GET, &id, api, None, &s.nonce).await?;
        ensure!(
            r.status == 200,
            "Expected owned resource absent; reconcile first"
        );
        let incarnation = owned_body(
            s,
            &r.body.context("Missing ownership observation")?,
            &expected,
        )?;
        ensure!(
            incarnation == s.resources[i].incarnation,
            "Resource incarnation changed; refuse deletion"
        );
        let request = uuid::Uuid::new_v4().to_string();
        s.resources[i].delete_request = Some(request.clone());
        save(c, s, e).await?;
        let r = c.arm(Method::DELETE, &id, api, None, &request).await;
        match r {Ok(r) if [200,202,204].contains(&r.status)=>{s.resources[i].delete_accepted=true;s.resources[i].operation_url=r.operation;save(c,s,e).await?;},_=>bail!("Delete outcome unknown/rejected. Observe this exact resource; no automatic replay.")}
        return Ok(());
    }
    s.completed_unix = Some(at);
    save(c, s, e).await?;
    Ok(())
}
/// Read the original controller after a persisted stop attempt. Never retry DELETE.
async fn reconcile_stop(c: &mut impl Cloud, s: &mut State, e: &mut String) -> Result<()> {
    let Some(stop) = s.job_stop.as_ref() else {
        return Ok(());
    };
    if stop.phase == StopPhase::Absent {
        return Ok(());
    }
    let r = c
        .arm(Method::GET, &job_id(&s.plan), "2024-03-01", None, &s.nonce)
        .await?;
    if r.status == 404 {
        s.job_stop.as_mut().unwrap().phase = StopPhase::Absent;
        save(c, s, e).await?;
        return Ok(());
    }
    ensure!(
        r.status == 200,
        "Controller stop observation unavailable; outcome remains pending"
    );
    guard_receipt(s, &r.body.context("Controller stop observation missing")?)?;
    // A surviving controller after an uncertain request remains visibly unknown;
    // an acknowledged DELETE is still pending until a read proves absence.
    if stop.phase == StopPhase::Intent {
        s.job_stop.as_mut().unwrap().phase = StopPhase::Unknown;
        save(c, s, e).await?;
    }
    Ok(())
}
async fn heartbeat(c: &mut impl Cloud, s: &mut State, e: &mut String, at: u64) -> Result<()> {
    if s.job_stop.is_some() {
        return reconcile_stop(c, s, e).await;
    }
    s.heartbeat_unix = Some(at);
    save(c, s, e).await?;
    if s.completed_unix.is_none() {
        if at >= s.plan.expires_at_unix || s.destroy_requested {
            destroy_one(c, s, e, at).await?;
        } else {
            observe(c, s, e).await?;
        }
    }
    if s.completed_unix.is_some() {
        ensure!(
            s.guard.is_some(),
            "Installer controller receipt missing; refuse self-stop"
        );
        verify_job(c, s).await?;
        let request = uuid::Uuid::new_v4().to_string();
        s.job_stop = Some(Stop {
            request_id: request.clone(),
            phase: StopPhase::Intent,
            operation_url: None,
        });
        save(c, s, e).await?;
        let response = c
            .arm(
                Method::DELETE,
                &job_id(&s.plan),
                "2024-03-01",
                None,
                &request,
            )
            .await;
        match response {
            Ok(r) if [200, 202, 204].contains(&r.status) => {
                let stop = s.job_stop.as_mut().unwrap();
                stop.phase = StopPhase::Accepted;
                stop.operation_url = r.operation;
                save(c, s, e).await?;
                reconcile_stop(c, s, e).await?;
            }
            Ok(r) if r.status == 404 => {
                s.job_stop.as_mut().unwrap().phase = StopPhase::Absent;
                save(c, s, e).await?;
            }
            _ => {
                s.job_stop.as_mut().unwrap().phase = StopPhase::Unknown;
                save(c, s, e).await?;
                bail!("Controller stop outcome unknown/rejected; inspect the original request. DELETE will not be replayed.")
            }
        }
    }
    Ok(())
}
async fn tick(c: &mut impl Cloud, s: &mut State, e: &mut String, at: u64) -> Result<()> {
    ensure!(s.guard.is_some(),"Controller receipt must be pinned by explicit initialization; a scheduled job cannot adopt itself");
    if s.job_stop.is_none() {
        verify_job(c, s).await?;
    }
    heartbeat(c, s, e, at).await
}
fn tick_identity(s: &State, client_id: &str) -> Result<()> {
    let receipt = s
        .guard
        .as_ref()
        .context("Installer-pinned controller identity required")?;
    ensure!(
        uuid::Uuid::parse_str(client_id)
            .is_ok_and(|u| !u.is_nil() && u.to_string() == receipt.client_id),
        "Scheduled execution identity differs from pinned controller"
    );
    Ok(())
}
async fn initialize(c: &mut impl Cloud, s: &mut State) -> Result<()> {
    ensure!(
        s.guard.is_none() && s.heartbeat_unix.is_none() && s.job_stop.is_none(),
        "Use the original uninitialized local state"
    );
    ensure!(
        c.read_control(&control_url(&s.plan)).await?.is_none(),
        "Control already exists; reconcile initialization instead of overwriting"
    );
    // The trusted explicit installer, rather than the first scheduled execution,
    // pins provider metadata before the create-only control write.
    s.guard = Some(verify_job(c, s).await?);
    c.save_control(&control_url(&s.plan), None, s).await?;
    Ok(())
}

pub(crate) async fn lifecycle(command: &Lifecycle) -> Result<()> {
    match command {
        Lifecycle::Prepare { plan, out } => {
            outside(out, plan)?;
            let (p, m) = inspect(plan)?;
            ensure!(
                p.azure_infrastructure.is_some() && now()? < p.expires_at_unix,
                "Unexpired Azure graph required"
            );
            let s = State {
                schema_version: 1,
                plan: p,
                bundle_digest: m.bundle_digest,
                nonce: uuid::Uuid::new_v4().to_string(),
                operations: [Stage::Foundation, Stage::Cluster]
                    .into_iter()
                    .map(|stage| Operation {
                        stage,
                        phase: Phase::Prepared,
                        request_id: uuid::Uuid::new_v4().to_string(),
                        correlation_id: None,
                        operation_url: None,
                    })
                    .collect(),
                resources: vec![],
                heartbeat_unix: None,
                guard: None,
                destroy_requested: false,
                completed_unix: None,
                job_stop: None,
                installation_digest: None,
                installation_closed: false,
            };
            s.validate()?;
            private_dir(out)?;
            put(out, "azure-state.json", &json_bytes(&s)?)?;
            crate::output(s.status())
        }
        Lifecycle::Status { state } => {
            let s = local(state)?;
            let mut c = Azure::new(&s.plan, None)?;
            let (_, s) = c
                .read_control(&control_url(&s.plan))
                .await?
                .context("Shared control is not initialized")?;
            crate::output(s.status())
        }
        Lifecycle::Apply { remote, stage } => run(remote, Some(*stage), false).await,
        Lifecycle::Reconcile(remote) => run(remote, None, false).await,
        Lifecycle::Destroy(remote) => run(remote, None, true).await,
    }
}
async fn run(o: &Remote, stage: Option<Stage>, destroy: bool) -> Result<()> {
    let expected = local(&o.state)?;
    ensure!(
        o.allow_experimental_cloud && o.accept == expected.bundle_digest,
        "Explicit experimental cloud flag and exact bundle digest required"
    );
    let mut c = Azure::new(&expected.plan, None)?;
    let (mut e, mut s) = c
        .read_control(&control_url(&expected.plan))
        .await?
        .context("Initialize independent control before workload creation")?;
    ensure!(
        s.nonce == expected.nonce && s.bundle_digest == expected.bundle_digest,
        "Local/shared control identity mismatch"
    );
    if let Some(stage) = stage {
        verify_job(&mut c, &s).await?;
        apply(&mut c, &mut s, &mut e, stage, now()?).await?;
    } else if destroy {
        ensure!(
            s.installation_digest.is_none() || s.installation_closed,
            "Close verified Kubernetes installation before manual infrastructure destruction"
        );
        destroy_one(&mut c, &mut s, &mut e, now()?).await?;
    } else {
        observe(&mut c, &mut s, &mut e).await?;
        reconcile_stop(&mut c, &mut s, &mut e).await?;
    }
    crate::output(s.status())
}
pub(crate) async fn cleanup(command: &Cleanup) -> Result<()> {
    match command {
        Cleanup::Package { state, out } => {
            outside(out, state)?;
            let s = local(state)?;
            let files = control_artifacts(&s)?;
            private_dir(out)?;
            for (n, b) in files {
                put(out, &n, &b)?;
            }
            crate::output(
                json!({"deploymentId":s.plan.deployment_id,"bundleDigest":s.bundle_digest,"path":out,"cloudExecuted":false,"liveQualified":false}),
            )
        }
        Cleanup::Initialize(o) => {
            let s = local(&o.state)?;
            ensure!(
                o.allow_experimental_cloud
                    && o.accept == s.bundle_digest
                    && now()? < s.plan.expires_at_unix,
                "Unexpired explicit control initialization confirmation required"
            );
            let mut c = Azure::new(&s.plan, None)?;
            let mut s = s;
            initialize(&mut c, &mut s).await?;
            crate::output(s.status())
        }
        Cleanup::Verify { state } => {
            let s = local(state)?;
            let mut c = Azure::new(&s.plan, None)?;
            let (_, v) = c
                .read_control(&control_url(&s.plan))
                .await?
                .context("Control unavailable")?;
            ensure!(s.nonce == v.nonce, "Control identity mismatch");
            if v.completed_unix.is_some() || v.job_stop.is_some() {
                return crate::output(
                    json!({"heartbeatReady":false,"status":v.status(),"next":"lifecycle_reconcile","liveQualified":false,"workloadApplyReady":false}),
                );
            }
            let at = now()?;
            ensure!(
                v.heartbeat_unix.is_some_and(|t| t <= at && at - t <= 600),
                "Independent cleanup heartbeat stale/missing"
            );
            verify_job(&mut c, &v).await?;
            crate::output(
                json!({"heartbeatReady":true,"independentOfAks":true,"liveQualified":false,"workloadApplyReady":false}),
            )
        }
        Cleanup::Tick {
            control,
            tenant,
            subscription,
            client_id,
        } => {
            ensure!(
                uuid::Uuid::parse_str(client_id).is_ok_and(|u| !u.is_nil()),
                "Exact cleanup client ID required"
            );
            let mut c = Azure::target(tenant, subscription, Some(client_id.clone()))?;
            let (mut e, mut s) = c
                .read_control(control)
                .await?
                .context("Control not initialized")?;
            let p = &s.plan;
            let Target::Azure {
                tenant: t,
                subscription: sub,
                ..
            } = &p.target
            else {
                unreachable!()
            };
            ensure!(
                t == tenant && sub == subscription && control_url(p) == *control,
                "Cleanup target mismatch"
            );
            tick_identity(&s, client_id)?;
            tick(&mut c, &mut s, &mut e, now()?).await?;
            crate::output(s.status())
        }
    }
}
fn control_artifacts(s: &State) -> Result<BTreeMap<String, Vec<u8>>> {
    let p = &s.plan;
    let n = graph::names(p);
    let c = p.azure_infrastructure.as_ref().unwrap();
    let Target::Azure {
        location,
        tenant,
        subscription,
    } = &p.target
    else {
        unreachable!()
    };
    let control_scope = graph::control_scope(p);
    let control_account = format!("{}ctl", n.storage);
    let identity_name = format!("{}-cleanup", n.prefix);
    let identity_id=format!("{control_scope}/providers/Microsoft.ManagedIdentity/userAssignedIdentities/{identity_name}");
    let account_id =
        format!("{control_scope}/providers/Microsoft.Storage/storageAccounts/{control_account}");
    let environment_id = format!(
        "{control_scope}/providers/Microsoft.App/managedEnvironments/{}-expiry",
        n.prefix
    );
    let recipient = format!("[reference('{identity_id}', '2023-01-31').principalId]");
    let client = format!("[reference('{identity_id}', '2023-01-31').clientId]");
    let mut workload_actions = std::collections::BTreeSet::new();
    for item in graph::inventory(p) {
        let parts: Vec<_> = item["id"]
            .as_str()
            .unwrap()
            .split("/providers/")
            .nth(1)
            .unwrap()
            .split('/')
            .collect();
        let kind = format!("{}/{}", parts[0], parts[1]);
        for verb in ["read", "delete"] {
            workload_actions.insert(format!("{kind}/{verb}"));
        }
    }
    workload_actions.insert("Microsoft.Resources/deployments/read".into());
    let role_id = format!(
        "/subscriptions/{subscription}/providers/Microsoft.Authorization/roleDefinitions/{}",
        guid(p, "cleanup-role")
    );
    let custom_role = json!({"properties":{"roleName":format!("{}-cleanup",n.prefix),"type":"CustomRole","assignableScopes":[scope(p)],"permissions":[{"actions":workload_actions,"notActions":[],"dataActions":[],"notDataActions":[]}]}});
    let cleanup_assignment = json!({"properties":{"principalId":recipient,"principalType":"ServicePrincipal","roleDefinitionId":role_id}});
    let blob_scope = format!("{account_id}/blobServices/default/containers/control");
    let builtin=format!("/subscriptions/{subscription}/providers/Microsoft.Authorization/roleDefinitions/ba92f5b4-2d11-453d-a403-e96b0029c9fe");
    let control_resources = vec![
        json!({"type":"Microsoft.ManagedIdentity/userAssignedIdentities","apiVersion":"2023-01-31","name":identity_name,"location":location}),
        json!({"type":"Microsoft.Storage/storageAccounts","apiVersion":"2023-05-01","name":control_account,"location":location,"sku":{"name":"Standard_LRS"},"kind":"StorageV2","properties":{"allowSharedKeyAccess":false,"allowBlobPublicAccess":false,"minimumTlsVersion":"TLS1_2","supportsHttpsTrafficOnly":true,"publicNetworkAccess":"Enabled"}}),
        json!({"type":"Microsoft.Storage/storageAccounts/blobServices/containers","apiVersion":"2023-05-01","name":format!("{control_account}/default/control"),"dependsOn":[account_id],"properties":{"publicAccess":"None"}}),
        json!({"type":"Microsoft.App/managedEnvironments","apiVersion":"2024-03-01","name":format!("{}-expiry",n.prefix),"location":location,"properties":{"appLogsConfiguration":{"destination":"none"}}}),
        json!({"type":"Microsoft.App/jobs","apiVersion":"2024-03-01","name":format!("{}-expiry",n.prefix),"location":location,"tags":{"ows-deployment":p.deployment_id,"ows-nonce":s.nonce,"ows-bundle":s.bundle_digest},"dependsOn":[identity_id,environment_id],"identity":{"type":"UserAssigned","userAssignedIdentities":{identity_id.clone():{}}},"properties":{"environmentId":environment_id,"configuration":{"triggerType":"Schedule","replicaTimeout":240,"replicaRetryLimit":0,"scheduleTriggerConfig":{"cronExpression":"*/5 * * * *","parallelism":1,"replicaCompletionCount":1}},"template":{"containers":[{"name":"cleanup","image":c.guard_image,"command":["/opt/openworker/ows"],"args":["deploy","azure","cleanup","tick","--control",control_url(p),"--tenant",tenant,"--subscription",subscription,"--client-id",client],"resources":{"cpu":0.25,"memory":"0.5Gi"}}]}}}),
    ];
    let template = json!({"$schema":"https://schema.management.azure.com/schemas/2019-04-01/deploymentTemplate.json#","contentVersion":"1.0.0.0","resources":control_resources});
    let blob_binding = json!({"schemaVersion":1,"scope":blob_scope,"roleDefinitionId":builtin,"assignments":[{"guid":guid(p,"cleanup-control"),"principalId":recipient,"principalType":"ServicePrincipal"},{"guid":guid(p,"setup-control"),"principalId":p.azure_bootstrap.as_ref().unwrap().setup_object_id,"principalType":p.azure_bootstrap.as_ref().unwrap().setup_principal_type.azure()}],"resolveIdentityAfterControlDeployment":true});
    let job_role = json!({"properties":{"roleName":format!("{}-cleanup-stop",n.prefix),"type":"CustomRole","assignableScopes":[control_scope],"permissions":[{"actions":["Microsoft.App/jobs/read","Microsoft.App/jobs/delete"],"notActions":[],"dataActions":[],"notDataActions":[]}]},"assignmentScope":job_id(p),"principalId":recipient,"roleDefinitionGuid":guid(p,"cleanup-stop-role"),"assignmentGuid":guid(p,"cleanup-stop-assignment")});
    let readme=format!("# Independent Azure expiry control\n\nOffline export. Reviewed administrator deploys `azure-control.arm.json` into existing `{control_scope}`, resolves the actual cleanup managed-identity principal, and grants the exact exported Blob bindings, workload cleanup role and own-job stop role. Role/assignment files are REST payloads, not ARM templates; review their fixed IDs/scopes. ows never applies these privileged grants implicitly.\n\nThen initialize shared control exactly once, wait for a scheduled heartbeat and verify it before creating workload infrastructure. Cleanup uses the same versioned Blob with ETag compare-and-swap as the CLI. It survives AKS and invoking-laptop loss. No account key/SAS is allowed; the control endpoint is public HTTPS with private OAuth-only Blob data. Customer policy/network must permit this controller path.\n\nThe pinned external image must contain this matching native `ows` binary. Schedule: five minutes, one parallel invocation, platform retry count zero. CAS protects concurrent ticks/CLI. Unknown mutation outcomes remain parked; a subsequent tick reads original state/resources instead of replaying a write. Azure can reject a request before effect or lose its response after effect; neither is automatic retry authorization.\n\nWorkload deletions compare exact IDs, nonce, bundle digest and immutable creation receipts. No customer RG, guessed `MC_*` group or foreign resource is deleted. Missing ownership receipts, an active/uncertain create, replacement or ambiguous delete pauses cleanup for operator inspection; TTL is not an unconditional spending cap. A concurrent privileged administrator can replace a resource between read and delete because ARM has no universal conditional DELETE contract; do not operate on these dedicated owned IDs during teardown.\n\nAfter workload teardown, the job deletes its own exact job ID once. Control Blob/account, cleanup managed identity, consumption environment and reviewed role grants are retained for audit/recovery; the customer explicitly removes them after reviewing the checkpoint. Retention may incur small storage charges. This is workload expiry qualification, not a claim that every control byte/cost vanishes.\n\nNo real Azure deployment, IAM, scheduling, managed-identity endpoint, quotas, prices or AKS/OpenShell runtime has been tested. `liveQualified:false` and `applyReady:false` are intentional.\n");
    Ok(BTreeMap::from([
        ("azure-control.arm.json".into(), json_bytes(&template)?),
        ("azure-cleanup-role.json".into(), json_bytes(&custom_role)?),
        (
            "azure-cleanup-assignment.json".into(),
            json_bytes(&cleanup_assignment)?,
        ),
        (
            "azure-control-bindings.json".into(),
            json_bytes(&blob_binding)?,
        ),
        (
            "azure-cleanup-stop-role.json".into(),
            json_bytes(&job_role)?,
        ),
        (
            "azure-control-manifest.json".into(),
            json_bytes(
                &json!({"deploymentId":p.deployment_id,"bundleDigest":s.bundle_digest,"control":control_url(p),"controlScope":control_scope,"cleanupRoleDefinitionId":role_id,"cleanupRoleUrl":format!("https://management.azure.com{}/providers/Microsoft.Authorization/roleDefinitions/{}?api-version=2022-04-01",scope(p),guid(p,"cleanup-role")),"cleanupAssignmentUrl":format!("https://management.azure.com{}/providers/Microsoft.Authorization/roleAssignments/{}?api-version=2022-04-01",scope(p),guid(p,"cleanup-workload")),"initializationRequired":true,"retainedControlOwnedByCustomer":true,"liveQualified":false}),
            )?,
        ),
        ("AZURE-CLEANUP.md".into(), readme.into_bytes()),
    ]))
}

#[cfg(test)]
mod tests {
    use super::*;
    pub(super) fn state() -> State {
        let mut p = super::super::super::tests::plan();
        p.schema_version = 2;
        p.topology.architecture = "amd64".into();
        p.target = Target::Azure {
            tenant: "11111111-2222-4333-8444-555555555555".into(),
            subscription: "66666666-7777-4888-8999-aaaaaaaaaaaa".into(),
            location: "westus2".into(),
        };
        p.azure_bootstrap=Some(serde_json::from_value(json!({"resourceGroup":"customer-workload","setupObjectId":"bbbbbbbb-cccc-4ddd-8eee-ffffffffffff","setupPrincipalType":"ServicePrincipal"})).unwrap());
        p.azure_infrastructure=Some(serde_json::from_value(json!({"kubernetesVersion":"1.33.4","nodeVmSize":"Standard_D4s_v5","sshPublicKey":"ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA","adminGroupObjectIds":["11111111-1111-4111-8111-111111111111"],"controlResourceGroup":"customer-control","guardImage":format!("ghcr.io/test/ows@sha256:{}","a".repeat(64)),"postgresPassword":{"keyVaultId":"/subscriptions/66666666-7777-4888-8999-aaaaaaaaaaaa/resourceGroups/customer-secrets/providers/Microsoft.KeyVault/vaults/customer-vault","secretName":"postgres","secretVersion":"a".repeat(32)}})).unwrap());
        let s = State {
            schema_version: 1,
            bundle_digest: digest(&p).unwrap(),
            plan: p,
            nonce: uuid::Uuid::new_v4().to_string(),
            operations: [Stage::Foundation, Stage::Cluster]
                .into_iter()
                .map(|stage| Operation {
                    stage,
                    phase: Phase::Prepared,
                    request_id: uuid::Uuid::new_v4().to_string(),
                    correlation_id: None,
                    operation_url: None,
                })
                .collect(),
            resources: vec![],
            heartbeat_unix: Some(150),
            guard: Some(fixture_guard()),
            destroy_requested: false,
            completed_unix: None,
            job_stop: None,
            installation_digest: None,
            installation_closed: false,
        };
        s.validate().unwrap();
        s
    }
    fn reply(status: u16, body: Option<Value>) -> Reply {
        Reply {
            status,
            body,
            etag: None,
            operation: None,
        }
    }
    struct Fake {
        control: State,
        revision: u64,
        objects: BTreeMap<String, Value>,
        writes: Vec<(Method, String)>,
        lose_put: bool,
        lose_delete: bool,
        conflict: bool,
    }
    impl Fake {
        fn new(s: State) -> Self {
            let mut objects = BTreeMap::new();
            objects.insert(job_id(&s.plan), fixture_job(&s));
            Self {
                control: s,
                revision: 1,
                objects,
                writes: vec![],
                lose_put: false,
                lose_delete: false,
                conflict: false,
            }
        }
        fn etag(&self) -> String {
            format!("\"{}\"", self.revision)
        }
        fn puts(&self) -> usize {
            self.writes
                .iter()
                .filter(|(m, _)| *m == Method::PUT)
                .count()
        }
        fn deletes(&self) -> usize {
            self.writes
                .iter()
                .filter(|(m, _)| *m == Method::DELETE)
                .count()
        }
    }
    impl Cloud for Fake {
        async fn read_control(&mut self, _: &str) -> Result<Option<(String, State)>> {
            Ok(Some((self.etag(), self.control.clone())))
        }
        async fn save_control(&mut self, _: &str, etag: Option<&str>, s: &State) -> Result<String> {
            s.validate()?;
            ensure!(
                !self.conflict && etag == Some(self.etag().as_str()),
                "CAS conflict"
            );
            self.revision += 1;
            self.control = s.clone();
            Ok(self.etag())
        }
        async fn arm(
            &mut self,
            method: Method,
            id: &str,
            _: &str,
            body: Option<&Value>,
            request: &str,
        ) -> Result<Reply> {
            if method == Method::GET {
                return Ok(self
                    .objects
                    .get(id)
                    .cloned()
                    .map(|v| reply(200, Some(v)))
                    .unwrap_or_else(|| reply(404, None)));
            }
            self.writes.push((method.clone(), id.into()));
            if method == Method::PUT {
                let op = self
                    .control
                    .operations
                    .iter()
                    .find(|o| o.request_id == request)
                    .unwrap();
                assert_eq!(
                    op.phase,
                    Phase::Intent,
                    "Intent must be durable before mutation"
                );
                let stage = op.stage;
                let params = body.unwrap()["properties"]["parameters"].clone();
                self.objects.insert(id.into(),json!({"properties":{"parameters":params,"correlationId":"12345678-1234-4234-8234-123456789abc","provisioningState":"Succeeded"}}));
                for item in graph::inventory(&self.control.plan)
                    .into_iter()
                    .filter(|r| r["stage"] == stage.name())
                {
                    let rid = item["id"].as_str().unwrap();
                    let mut v = json!({"id":rid,"tags":{"ows-deployment":self.control.plan.deployment_id,"ows-nonce":self.control.nonce,"ows-bundle":self.control.bundle_digest},"systemData":{"createdAt":"2026-10-31T00:00:00Z"},"properties":{"creationTime":"2026-10-31T00:00:00Z","creationDate":"2026-10-31T00:00:00Z","resourceGuid":"receipt-guid","principalId":"12345678-1234-4234-8234-123456789abc"}});
                    v["etag"] = json!("ignored-mutable-etag");
                    self.objects.insert(rid.into(), v);
                }
                if self.lose_put {
                    bail!("response lost after effect")
                }
                return Ok(reply(201, None));
            }
            if method == Method::DELETE {
                if id == job_id(&self.control.plan) {
                    assert!(
                        self.control.completed_unix.is_some()
                            && self
                                .control
                                .job_stop
                                .as_ref()
                                .is_some_and(|stop| stop.request_id == request)
                    );
                } else {
                    assert!(
                        self.control
                            .resources
                            .iter()
                            .any(|r| r.id == id && r.delete_request.as_deref() == Some(request)),
                        "Delete intent must be durable"
                    );
                }
                self.objects.remove(id);
                if self.lose_delete {
                    self.lose_delete = false;
                    bail!("delete response lost after effect")
                };
                return Ok(reply(202, None));
            }
            bail!("Unexpected method")
        }
    }
    async fn active() -> (Fake, State, String) {
        let mut s = state();
        let mut c = Fake::new(s.clone());
        let mut e = c.etag();
        apply(&mut c, &mut s, &mut e, Stage::Foundation, 150)
            .await
            .unwrap();
        assert_eq!(s.operations[0].phase, Phase::Active);
        (c, s, e)
    }
    #[tokio::test]
    async fn lost_create_response_is_observed_without_replaying_original_put() {
        let mut s = state();
        let mut c = Fake::new(s.clone());
        c.lose_put = true;
        let mut e = c.etag();
        assert!(apply(&mut c, &mut s, &mut e, Stage::Foundation, 150)
            .await
            .is_err());
        assert_eq!(c.puts(), 1);
        assert_eq!(s.operations[0].phase, Phase::Unknown);
        assert!(apply(&mut c, &mut s, &mut e, Stage::Foundation, 150)
            .await
            .is_err());
        assert_eq!(c.puts(), 1);
        let (mut e, mut restarted) = c.read_control("").await.unwrap().unwrap();
        observe(&mut c, &mut restarted, &mut e).await.unwrap();
        assert_eq!(restarted.operations[0].phase, Phase::Active);
        assert_eq!(restarted.resources.len(), 10);
        assert_eq!(c.puts(), 1);
    }
    #[tokio::test]
    async fn cas_failure_prevents_any_infrastructure_write() {
        let mut s = state();
        let mut c = Fake::new(s.clone());
        c.conflict = true;
        let mut e = c.etag();
        assert!(apply(&mut c, &mut s, &mut e, Stage::Foundation, 150)
            .await
            .is_err());
        assert!(c.writes.is_empty());
        assert_eq!(c.control.operations[0].phase, Phase::Prepared);
    }
    #[tokio::test]
    async fn lost_delete_response_is_reconciled_after_restart_and_never_repeated() {
        let (mut c, mut s, mut e) = active().await;
        c.lose_delete = true;
        assert!(destroy_one(&mut c, &mut s, &mut e, 160).await.is_err());
        let deleted = c.writes.last().unwrap().1.clone();
        let (mut e, mut restarted) = c.read_control("").await.unwrap().unwrap();
        destroy_one(&mut c, &mut restarted, &mut e, 161)
            .await
            .unwrap();
        assert!(restarted
            .resources
            .iter()
            .any(|r| r.id == deleted && r.deleted));
        assert_eq!(
            c.writes
                .iter()
                .filter(|(m, id)| *m == Method::DELETE && id == &deleted)
                .count(),
            1
        );
    }
    #[tokio::test]
    async fn replacement_or_missing_incarnation_refuses_deletion() {
        let (mut c, mut s, mut e) = active().await;
        let id = s.resources[0].id.clone();
        let pointer = graph::inventory(&s.plan)
            .into_iter()
            .find(|r| r["id"] == id)
            .unwrap()["incarnationPointer"]
            .as_str()
            .unwrap()
            .to_owned();
        *c.objects
            .get_mut(&id)
            .unwrap()
            .pointer_mut(&pointer)
            .unwrap() = json!("replacement");
        assert!(destroy_one(&mut c, &mut s, &mut e, 160).await.is_err());
        assert_eq!(c.deletes(), 0);
    }
    #[tokio::test]
    async fn expiry_runs_outside_cluster_and_stops_its_job_after_workload_cleanup() {
        let (mut c, mut s, mut e) = active().await;
        for _ in 0..15 {
            heartbeat(&mut c, &mut s, &mut e, 201).await.unwrap();
        }
        assert!(s.completed_unix.is_some());
        assert!(s.resources.iter().all(|r| r.deleted));
        assert_eq!(c.deletes(), 11);
        assert!(s.job_stop.is_some());
        heartbeat(&mut c, &mut s, &mut e, 202).await.unwrap();
        assert_eq!(c.deletes(), 11);
        assert!(
            !c.writes.iter().any(|(_, id)| id == &scope(&s.plan)),
            "Customer RG must never be deleted"
        );
    }
    #[tokio::test]
    async fn expiry_and_missing_guard_prevent_creates_and_unknown_404_stays_parked() {
        let mut s = state();
        let mut c = Fake::new(s.clone());
        let mut e = c.etag();
        assert!(apply(&mut c, &mut s, &mut e, Stage::Foundation, 201)
            .await
            .is_err());
        s.heartbeat_unix = None;
        assert!(apply(&mut c, &mut s, &mut e, Stage::Foundation, 150)
            .await
            .is_err());
        s.operations[0].phase = Phase::Unknown;
        save(&mut c, &s, &mut e).await.unwrap();
        observe(&mut c, &mut s, &mut e).await.unwrap();
        assert_eq!(s.operations[0].phase, Phase::Unknown);
        assert!(destroy_one(&mut c, &mut s, &mut e, 201).await.is_err());
        assert!(c.writes.is_empty());
    }
    #[test]
    fn control_package_has_no_keys_no_cluster_dependency_and_retains_audit_control() {
        let s = state();
        let files = control_artifacts(&s).unwrap();
        let v: Value = serde_json::from_slice(&files["azure-control.arm.json"]).unwrap();
        let job = v["resources"]
            .as_array()
            .unwrap()
            .iter()
            .find(|v| v["type"] == "Microsoft.App/jobs")
            .unwrap();
        assert_eq!(job["properties"]["configuration"]["replicaRetryLimit"], 0);
        assert!(!job.to_string().contains("managedClusters"));
        let grants: String = files
            .iter()
            .filter(|(n, _)| n.contains("role"))
            .map(|(_, b)| String::from_utf8_lossy(b))
            .collect();
        assert!(!grants.contains('*'));
        assert!(!grants.contains("roleAssignments/write"));
        assert!(!grants.contains("resourceGroups/delete"));
        let mut altered = s.clone();
        altered.nonce = "".into();
        assert!(altered.validate().is_err());
    }
}

pub(super) fn local_plan(root: &Path) -> Result<Plan> {
    Ok(local(root)?.plan)
}
pub(super) async fn installation_snapshot(
    root: &Path,
    provisioning: bool,
) -> Result<(Plan, Azure, Value)> {
    let expected = local(root)?;
    let mut c = Azure::new(&expected.plan, None)?;
    let (_, s) = c
        .read_control(&control_url(&expected.plan))
        .await?
        .context("Shared Azure control missing")?;
    ensure!(
        s.nonce == expected.nonce
            && s.bundle_digest == expected.bundle_digest
            && s.operations.iter().all(|o| o.phase == Phase::Active),
        "Original infrastructure stages are not active"
    );
    if provisioning {
        let at = now()?;
        ensure!(
            at < s.plan.expires_at_unix
                && !s.destroy_requested
                && s.completed_unix.is_none()
                && s.heartbeat_unix.is_some_and(|t| t <= at && at - t <= 600),
            "Expired/closing infrastructure or stale independent cleanup"
        );
    }
    let entry = graph::inventory(&s.plan)
        .into_iter()
        .find(|v| v["stage"] == "cluster")
        .unwrap();
    let id = entry["id"].as_str().unwrap();
    let r = c
        .arm(
            Method::GET,
            id,
            entry["apiVersion"].as_str().unwrap(),
            None,
            &s.nonce,
        )
        .await?;
    ensure!(r.status == 200, "Original AKS cluster missing");
    let cluster = r.body.context("AKS response missing")?;
    let incarnation = owned_body(&s, &cluster, &entry)?;
    ensure!(
        s.resources
            .iter()
            .any(|r| r.id == id && r.incarnation == incarnation && !r.deleted),
        "AKS ownership receipt missing/changed"
    );
    Ok((s.plan, c, cluster))
}
pub(super) async fn installation_mark(root: &Path, digest: &str, closed: bool) -> Result<()> {
    ensure!(
        digest.len() == 64
            && digest
                .bytes()
                .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c)),
        "Invalid installation binding digest"
    );
    let expected = local(root)?;
    let mut c = Azure::new(&expected.plan, None)?;
    let (mut e, mut s) = c
        .read_control(&control_url(&expected.plan))
        .await?
        .context("Shared control missing")?;
    ensure!(
        s.nonce == expected.nonce && s.bundle_digest == expected.bundle_digest,
        "Azure installation identity mismatch"
    );
    if closed {
        ensure!(
            s.installation_digest.as_deref() == Some(digest),
            "Original installation mutation receipt missing"
        );
        s.installation_closed = true;
    } else {
        ensure!(
            now()? < s.plan.expires_at_unix && !s.destroy_requested && !s.installation_closed,
            "Infrastructure is closing/expired"
        );
        if let Some(old) = &s.installation_digest {
            ensure!(
                old == digest,
                "Another installation binding already has mutation intent"
            );
        }
        s.installation_digest = Some(digest.to_owned());
    }
    save(&mut c, &s, &mut e).await
}

#[cfg(test)]
pub(super) fn fixture_plan() -> Plan {
    tests::state().plan
}
#[cfg(test)]
pub(super) fn fixture_state_dir(root: &Path) -> Plan {
    let s = tests::state();
    private_dir(root).unwrap();
    put(root, "azure-state.json", &json_bytes(&s).unwrap()).unwrap();
    s.plan
}

async fn verify_job(c: &mut impl Cloud, s: &State) -> Result<GuardReceipt> {
    let r = c
        .arm(Method::GET, &job_id(&s.plan), "2024-03-01", None, &s.nonce)
        .await?;
    ensure!(r.status == 200, "Independent cleanup job unavailable");
    guard_body(s, &r.body.context("Cleanup job body missing")?)
}
fn guard_receipt(s: &State, body: &Value) -> Result<GuardReceipt> {
    let stamp = body["systemData"]["createdAt"]
        .as_str()
        .context("Controller immutable creation receipt missing")?
        .to_owned();
    let mi = format!(
        "{}/providers/Microsoft.ManagedIdentity/userAssignedIdentities/{}-cleanup",
        graph::control_scope(&s.plan),
        graph::names(&s.plan).prefix
    );
    let identities = body["identity"]["userAssignedIdentities"]
        .as_object()
        .context("Controller identity map missing")?;
    ensure!(
        identities.len() == 1,
        "Exactly one controller managed identity required"
    );
    let identity = identities
        .iter()
        .find(|(id, _)| id.eq_ignore_ascii_case(&mi))
        .map(|(_, value)| value)
        .context("Controller identity scope mismatch")?;
    let receipt = GuardReceipt {
        created_at: stamp,
        principal_id: identity["principalId"]
            .as_str()
            .context("Controller principal receipt missing")?
            .to_owned(),
        client_id: identity["clientId"]
            .as_str()
            .context("Controller client receipt missing")?
            .to_owned(),
    };
    receipt.validate()?;
    ensure!(
        body["id"]
            .as_str()
            .is_some_and(|id| id.eq_ignore_ascii_case(&job_id(&s.plan)))
            && body["tags"]["ows-deployment"] == s.plan.deployment_id
            && body["tags"]["ows-nonce"] == s.nonce
            && body["tags"]["ows-bundle"] == s.bundle_digest
            && s.guard.as_ref().is_none_or(|old| old == &receipt),
        "Independent cleanup controller replaced/identity changed"
    );
    Ok(receipt)
}
fn guard_body(s: &State, body: &Value) -> Result<GuardReceipt> {
    let receipt = guard_receipt(s, body)?;
    let resources = &body["properties"]["template"]["containers"];
    let container = resources
        .as_array()
        .filter(|v| v.len() == 1)
        .map(|v| &v[0])
        .context("Exactly one cleanup container required")?;
    let config = &body["properties"]["configuration"];
    let args = container["args"]
        .as_array()
        .context("Cleanup arguments missing")?;
    let Target::Azure {
        tenant,
        subscription,
        ..
    } = &s.plan.target
    else {
        unreachable!()
    };
    let mi = format!(
        "{}/providers/Microsoft.ManagedIdentity/userAssignedIdentities/{}-cleanup",
        graph::control_scope(&s.plan),
        graph::names(&s.plan).prefix
    );
    ensure!(
        body["properties"]["provisioningState"] == "Succeeded"
            && container["image"] == s.plan.azure_infrastructure.as_ref().unwrap().guard_image
            && container["command"] == json!(["/opt/openworker/ows"])
            && args.len() == 12
            && args[..4]
                == json!(["deploy", "azure", "cleanup", "tick"])
                    .as_array()
                    .unwrap()[..]
            && args[4] == "--control"
            && args[5] == control_url(&s.plan)
            && args[6] == "--tenant"
            && args[7] == *tenant
            && args[8] == "--subscription"
            && args[9] == *subscription
            && args[10] == "--client-id"
            && args[11] == receipt.client_id
            && body["identity"]["type"] == "UserAssigned"
            && body["identity"]["userAssignedIdentities"]
                .as_object()
                .is_some_and(|m| m.len() == 1 && m.keys().any(|id| id.eq_ignore_ascii_case(&mi)))
            && config["triggerType"] == "Schedule"
            && config["replicaRetryLimit"] == 0
            && config["scheduleTriggerConfig"]["cronExpression"] == "*/5 * * * *"
            && config["scheduleTriggerConfig"]["parallelism"] == 1
            && config["scheduleTriggerConfig"]["replicaCompletionCount"] == 1,
        "Independent cleanup job configuration drift"
    );
    Ok(receipt)
}

#[cfg(test)]
mod guard_tests {
    use super::*;
    #[tokio::test]
    async fn cleanup_configuration_drift_blocks_workload_submission() {
        struct ReadOnly {
            body: Value,
        }
        impl Cloud for ReadOnly {
            async fn read_control(&mut self, _: &str) -> Result<Option<(String, State)>> {
                bail!("unexpected control read")
            }
            async fn save_control(
                &mut self,
                _: &str,
                _: Option<&str>,
                _: &State,
            ) -> Result<String> {
                bail!("unexpected mutation")
            }
            async fn arm(
                &mut self,
                m: Method,
                _: &str,
                _: &str,
                _: Option<&Value>,
                _: &str,
            ) -> Result<Reply> {
                ensure!(m == Method::GET, "Guard verification must only read");
                Ok(Reply {
                    status: 200,
                    etag: None,
                    operation: None,
                    body: Some(self.body.clone()),
                })
            }
        }
        let s = tests::state();
        let body = fixture_job(&s);
        verify_job(&mut ReadOnly { body: body.clone() }, &s)
            .await
            .unwrap();
        for pointer in [
            "/properties/template/containers/0/image",
            "/properties/template/containers/0/args/5",
            "/properties/configuration/scheduleTriggerConfig/cronExpression",
        ] {
            let mut changed = body.clone();
            *changed.pointer_mut(pointer).unwrap() = json!("drift");
            assert!(verify_job(&mut ReadOnly { body: changed }, &s)
                .await
                .is_err());
        }
        let mut replaced = body;
        replaced["systemData"]["createdAt"] = json!("2026-10-31T00:00:01Z");
        assert!(
            verify_job(&mut ReadOnly { body: replaced }, &s)
                .await
                .is_err(),
            "Same-name controller replacement must never be deleted"
        );
        let mut invalid = s;
        invalid.installation_closed = true;
        assert!(invalid.validate().is_err());
    }
}

#[cfg(test)]
fn fixture_job(s: &State) -> Value {
    let files = control_artifacts(s).unwrap();
    let graph: Value = serde_json::from_slice(&files["azure-control.arm.json"]).unwrap();
    let mut body = graph["resources"]
        .as_array()
        .unwrap()
        .iter()
        .find(|r| r["type"] == "Microsoft.App/jobs")
        .unwrap()
        .clone();
    body["properties"]["template"]["containers"][0]["args"][11] =
        json!("12345678-1234-4234-8234-123456789abc");
    body["id"] = json!(job_id(&s.plan));
    body["properties"]["provisioningState"] = json!("Succeeded");
    body["systemData"] = json!({"createdAt":"2026-10-31T00:00:00Z"});
    let mi = format!(
        "{}/providers/Microsoft.ManagedIdentity/userAssignedIdentities/{}-cleanup",
        graph::control_scope(&s.plan),
        graph::names(&s.plan).prefix
    );
    body["identity"]["userAssignedIdentities"][&mi] =
        json!({"clientId":fixture_guard().client_id,"principalId":fixture_guard().principal_id});
    body
}

#[cfg(test)]
fn fixture_guard() -> GuardReceipt {
    GuardReceipt {
        created_at: "2026-10-31T00:00:00Z".into(),
        principal_id: "22345678-1234-4234-8234-123456789abc".into(),
        client_id: "12345678-1234-4234-8234-123456789abc".into(),
    }
}

#[cfg(test)]
mod review_tests {
    use super::*;
    #[derive(Clone, Copy)]
    enum Delete {
        Success,
        Rejected,
        LostBefore,
        LostAfter,
        AcceptedPending,
    }
    struct Fixture {
        stored: Option<State>,
        revision: u64,
        job: Option<Value>,
        deletes: usize,
        reads: usize,
        saves: usize,
        behavior: Delete,
        lose_initial_control: bool,
    }
    impl Fixture {
        fn new(s: &State, initialized: bool) -> Self {
            Self {
                stored: initialized.then(|| s.clone()),
                revision: 1,
                job: Some(fixture_job(s)),
                deletes: 0,
                reads: 0,
                saves: 0,
                behavior: Delete::Success,
                lose_initial_control: false,
            }
        }
        fn etag(&self) -> String {
            format!("\"{}\"", self.revision)
        }
    }
    impl Cloud for Fixture {
        async fn read_control(&mut self, _: &str) -> Result<Option<(String, State)>> {
            Ok(self.stored.as_ref().map(|s| (self.etag(), s.clone())))
        }
        async fn save_control(&mut self, _: &str, e: Option<&str>, s: &State) -> Result<String> {
            s.validate()?;
            ensure!(
                match &self.stored {
                    None => e.is_none(),
                    Some(_) => e == Some(self.etag().as_str()),
                },
                "CAS conflict"
            );
            let initial = self.stored.is_none();
            self.stored = Some(s.clone());
            self.revision += 1;
            self.saves += 1;
            if initial && self.lose_initial_control {
                bail!("control response lost after effect")
            };
            Ok(self.etag())
        }
        async fn arm(
            &mut self,
            m: Method,
            id: &str,
            _: &str,
            _: Option<&Value>,
            request: &str,
        ) -> Result<Reply> {
            let s = self.stored.as_ref();
            let expected = s.map(|s| job_id(&s.plan));
            if let Some(expected) = expected {
                ensure!(id == expected, "Unexpected resource");
            }
            if m == Method::GET {
                self.reads += 1;
                return Ok(Reply {
                    status: if self.job.is_some() { 200 } else { 404 },
                    body: self.job.clone(),
                    etag: None,
                    operation: None,
                });
            }
            ensure!(m == Method::DELETE, "Unexpected mutation");
            let s = self.stored.as_ref().unwrap();
            assert!(
                s.job_stop.as_ref().is_some_and(
                    |stop| stop.phase == StopPhase::Intent && stop.request_id == request
                ),
                "Stop intent must be durable before DELETE"
            );
            self.deletes += 1;
            match self.behavior {
                Delete::Success=>{self.job=None;Ok(Reply{status:200,body:None,etag:None,operation:None})},
                Delete::Rejected=>Ok(Reply{status:403,body:None,etag:None,operation:None}),
                Delete::LostBefore=>bail!("connection lost before observed effect"),
                Delete::LostAfter=>{self.job=None;bail!("connection lost after committed effect")},
                Delete::AcceptedPending=>Ok(Reply{status:202,body:None,etag:None,operation:Some("https://management.azure.com/subscriptions/66666666-7777-4888-8999-aaaaaaaaaaaa/providers/Microsoft.App/locations/westus2/operationResults/exact?api-version=2024-03-01".into())}),
            }
        }
    }
    fn pending_install() -> State {
        let mut s = tests::state();
        s.guard = None;
        s.heartbeat_unix = None;
        s
    }
    fn completed() -> State {
        let mut s = tests::state();
        s.destroy_requested = true;
        s.completed_unix = Some(160);
        s
    }
    #[tokio::test]
    async fn installer_pins_provider_receipt_before_create_only_control_and_first_tick_rejects_replacement(
    ) {
        let mut s = pending_install();
        let mut f = Fixture::new(&s, false);
        initialize(&mut f, &mut s).await.unwrap();
        assert_eq!(s.guard, Some(fixture_guard()));
        assert_eq!(f.stored.as_ref().unwrap().guard, s.guard);
        assert_eq!(s.heartbeat_unix, None);
        assert_eq!(f.saves, 1);
        assert!(initialize(&mut f, &mut pending_install()).await.is_err());
        let mut e = f.etag();
        f.job.as_mut().unwrap()["systemData"]["createdAt"] = json!("2026-10-31T00:00:01Z");
        assert!(tick(&mut f, &mut s, &mut e, 150).await.is_err());
        assert_eq!(s.heartbeat_unix, None);
        assert_eq!(f.saves, 1);
        assert_eq!(f.deletes, 0);
    }
    #[tokio::test]
    async fn scheduled_job_cannot_bootstrap_its_own_receipt_or_accept_a_wrong_client_argument() {
        let mut s = pending_install();
        let mut f = Fixture::new(&s, true);
        let mut e = f.etag();
        assert!(tick(&mut f, &mut s, &mut e, 150).await.is_err());
        assert_eq!(f.reads, 0);
        assert_eq!(f.saves, 0);
        let mut f = Fixture::new(&s, false);
        f.job.as_mut().unwrap()["properties"]["template"]["containers"][0]["args"][11] =
            json!("33333333-3333-4333-8333-333333333333");
        assert!(initialize(&mut f, &mut s).await.is_err());
        assert!(f.stored.is_none());
        assert_eq!(f.saves, 0);
        let bound = tests::state();
        tick_identity(&bound, &fixture_guard().client_id).unwrap();
        assert!(tick_identity(&bound, "33333333-3333-4333-8333-333333333333").is_err());
        let mut bad = fixture_job(&bound);
        bad["properties"]["provisioningState"] = json!("Deleting");
        assert!(guard_body(&bound, &bad).is_err());
        assert!(
            guard_receipt(&bound, &bad).is_ok(),
            "Deleting owned job remains observable during stop reconciliation"
        );
    }
    #[tokio::test]
    async fn lost_initialization_reply_keeps_the_original_pinned_receipt_and_never_overwrites() {
        let mut s = pending_install();
        let mut f = Fixture::new(&s, false);
        f.lose_initial_control = true;
        assert!(initialize(&mut f, &mut s).await.is_err());
        let receipt = f.stored.as_ref().unwrap().guard.clone();
        assert_eq!(receipt, Some(fixture_guard()));
        assert!(initialize(&mut f, &mut pending_install()).await.is_err());
        assert_eq!(f.saves, 1);
        assert_eq!(f.stored.as_ref().unwrap().guard, receipt);
    }
    #[tokio::test]
    async fn rejected_or_unknown_stop_remains_visible_after_restart_without_repeating_delete() {
        for behavior in [Delete::Rejected, Delete::LostBefore] {
            let mut s = completed();
            let mut f = Fixture::new(&s, true);
            f.behavior = behavior;
            let mut e = f.etag();
            assert!(heartbeat(&mut f, &mut s, &mut e, 170).await.is_err());
            assert_eq!(s.job_stop.as_ref().unwrap().phase, StopPhase::Unknown);
            let saved_heartbeat = s.heartbeat_unix;
            let (_, saved) = f.read_control("").await.unwrap().unwrap();
            let mut s: State =
                serde_json::from_slice(&serde_json::to_vec(&saved).unwrap()).unwrap();
            tick(&mut f, &mut s, &mut e, 180).await.unwrap();
            assert_eq!(f.deletes, 1);
            assert_eq!(s.heartbeat_unix, saved_heartbeat);
            assert_eq!(s.status()["requiresAttention"], true);
            assert_eq!(s.status()["controllerStopped"], false);
            assert_eq!(s.status()["workloadAndControllerComplete"], false);
            assert_eq!(s.status()["controllerStop"]["phase"], "unknown");
        }
    }
    #[tokio::test]
    async fn lost_stop_reply_after_effect_is_confirmed_absent_on_read_without_replay() {
        let mut s = completed();
        let mut f = Fixture::new(&s, true);
        f.behavior = Delete::LostAfter;
        let mut e = f.etag();
        assert!(heartbeat(&mut f, &mut s, &mut e, 170).await.is_err());
        let mut restarted = f.stored.clone().unwrap();
        tick(&mut f, &mut restarted, &mut e, 180).await.unwrap();
        assert_eq!(f.deletes, 1);
        assert_eq!(
            restarted.job_stop.as_ref().unwrap().phase,
            StopPhase::Absent
        );
        assert_eq!(restarted.status()["controllerStopped"], true);
        assert_eq!(restarted.status()["requiresAttention"], false);
    }
    #[tokio::test]
    async fn accepted_stop_is_pending_until_absence_and_persisted_intent_never_replays() {
        let mut s = completed();
        let mut f = Fixture::new(&s, true);
        f.behavior = Delete::AcceptedPending;
        let mut e = f.etag();
        heartbeat(&mut f, &mut s, &mut e, 170).await.unwrap();
        assert_eq!(s.job_stop.as_ref().unwrap().phase, StopPhase::Accepted);
        assert_eq!(s.status()["workloadAndControllerComplete"], false);
        assert!(s.job_stop.as_ref().unwrap().operation_url.is_some());
        tick(&mut f, &mut s, &mut e, 180).await.unwrap();
        assert_eq!(f.deletes, 1);
        f.job = None;
        reconcile_stop(&mut f, &mut s, &mut e).await.unwrap();
        assert_eq!(s.job_stop.as_ref().unwrap().phase, StopPhase::Absent);
        assert_eq!(s.status()["workloadAndControllerComplete"], true);
        let mut s = completed();
        s.job_stop = Some(Stop {
            request_id: uuid::Uuid::new_v4().to_string(),
            phase: StopPhase::Intent,
            operation_url: None,
        });
        let mut f = Fixture::new(&s, true);
        let mut e = f.etag();
        tick(&mut f, &mut s, &mut e, 180).await.unwrap();
        assert_eq!(f.deletes, 0);
        assert_eq!(s.job_stop.as_ref().unwrap().phase, StopPhase::Unknown);
        assert_eq!(s.status()["requiresAttention"], true);
    }
}
