//! Portable model-handoff requests. These identify intended work; they never
//! authorize provider I/O, declare publication, or attest verification success.
use super::{component, oid, Manifest, ObjectFormat, Producer};
use anyhow::{ensure, Result};
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Call {
    pub turn_id: String,
    pub call_id: String,
}
impl Call {
    pub fn validate(&self) -> Result<()> {
        component(&self.turn_id, 80)?;
        ensure!(
            !self.call_id.is_empty()
                && self.call_id.len() <= 200
                && !self.call_id.chars().any(char::is_control),
            "Invalid originating model call"
        );
        Ok(())
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Submission {
    pub id: String,
    pub producer: Producer,
    /// The first accepted model call, retained on duplicate delivery.
    pub origin: Call,
    pub source_manifest: String,
    pub repository: String,
    pub object_format: ObjectFormat,
    pub base_commit: String,
    pub submitted_commit: String,
    /// Untrusted descriptive data, never included in a harness authority notice.
    pub summary: String,
}
impl Submission {
    pub fn new(
        producer: Producer,
        origin: Call,
        source: &Manifest,
        commit: String,
        summary: String,
    ) -> Result<Self> {
        producer.validate()?;
        origin.validate()?;
        source.validate()?;
        ensure!(
            producer.team_id == source.producer.team_id,
            "Submission source belongs to another team"
        );
        let mut request = Self {
            id: String::new(),
            producer,
            origin,
            source_manifest: source.digest()?,
            repository: source.repository.clone(),
            object_format: source.object_format,
            base_commit: source.base_commit.clone(),
            submitted_commit: commit,
            summary,
        };
        request.id = request.key()?;
        request.validate()?;
        Ok(request)
    }
    fn key(&self) -> Result<String> {
        // Commit/source identity deduplicates an uncertain submission even when
        // a model asks again using a NEW call ID. Summary text cannot create a
        // second export of this same intended revision.
        Ok(format!(
            "hs_{}",
            crate::bundle::digest(&serde_json::to_vec(&(
                "coding-submission-v1",
                &self.producer,
                &self.source_manifest,
                &self.repository,
                self.object_format,
                &self.base_commit,
                &self.submitted_commit,
            ))?)
        ))
    }
    pub fn validate(&self) -> Result<()> {
        component(&self.id, 80)?;
        self.producer.validate()?;
        self.origin.validate()?;
        super::hex(&self.source_manifest, 64)?;
        super::repository(&self.repository)?;
        oid(&self.base_commit, self.object_format)?;
        oid(&self.submitted_commit, self.object_format)?;
        ensure!(
            !self.summary.trim().is_empty()
                && self.summary.len() <= 2000
                && !self.summary.contains('\0'),
            "Bounded submission summary required"
        );
        ensure!(self.id == self.key()?, "Submission identity changed");
        Ok(())
    }
    /// Descriptive text/new call IDs do not authorize a second external action.
    pub fn same_revision(&self, other: &Self) -> Result<bool> {
        self.validate()?;
        other.validate()?;
        Ok(self.id == other.id)
    }
    pub fn require_manifest(&self, manifest: &Manifest) -> Result<()> {
        self.validate()?;
        manifest.require_source(&self.repository, &self.base_commit)?;
        ensure!(
            manifest.kind == super::Kind::Submission
                && manifest.producer == self.producer
                && manifest.object_format == self.object_format
                && manifest.submitted_commit == self.submitted_commit,
            "Published handoff differs from originating submission"
        );
        Ok(())
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Verification {
    pub id: String,
    pub submission_id: String,
    pub manifest_digest: String,
    pub submitted_commit: String,
    pub reporter: Producer,
    pub origin: Call,
    /// Attributed agent report, not proof tests passed or an approval to merge.
    pub text: String,
}
impl Verification {
    pub fn require_submission(&self, request: &Submission, manifest: &Manifest) -> Result<()> {
        request.require_manifest(manifest)?;
        component(&self.id, 80)?;
        self.reporter.validate()?;
        self.origin.validate()?;
        ensure!(
            self.submission_id == request.id
                && self.manifest_digest == manifest.digest()?
                && self.submitted_commit == manifest.submitted_commit
                && self.reporter.team_id == request.producer.team_id
                && !self.text.trim().is_empty()
                && self.text.len() <= 8000
                && !self.text.contains('\0'),
            "Verification report differs from exact same-team submitted revision"
        );
        Ok(())
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Integration {
    pub submission_id: String,
    pub manifest_digest: String,
    pub expected_head: String,
}
impl Integration {
    pub fn require_submission(&self, request: &Submission, manifest: &Manifest) -> Result<()> {
        request.require_manifest(manifest)?;
        oid(&self.expected_head, manifest.object_format)?;
        ensure!(
            self.submission_id == request.id && self.manifest_digest == manifest.digest()?,
            "Integration is not bound to the immutable submission"
        );
        // Actual branch, working tree, descendant check and original operation
        // marker belong to the trusted Git adapter, not this metadata contract.
        Ok(())
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct State {
    pub request: Submission,
    /// Set only by the qualified artifact adapter after byte verification.
    pub manifest: Option<Manifest>,
    #[serde(default)]
    pub reports: Vec<Verification>,
    #[serde(default)]
    pub error: Option<String>,
    #[serde(default, skip_serializing_if = "std::ops::Not::not")]
    pub uncertain: bool,
}

/// Metadata-only domain action. The adapter must persist it atomically with the
/// tool result, and qualify the separate export/publication before acknowledging.
pub fn submit(
    doc: &mut crate::agent_team::Document,
    session: &crate::store::Session,
    commit: String,
    summary: String,
) -> Result<serde_json::Value> {
    use anyhow::Context;
    let member = session.agent_team.as_ref().context("Not a team member")?;
    ensure!(
        doc.coding_selection && doc.coding.is_some(),
        "Coding handoff is unavailable on this adapter"
    );
    let source = doc
        .source_imports
        .get(&member.agent_id)
        .context("Submit from an acknowledged independent source checkout")?;
    let request = Submission::new(
        Producer {
            team_id: doc.id.clone(),
            agent_id: member.agent_id.clone(),
            session_id: session.id.clone(),
        },
        current_call(session, "team_submit_code")?,
        &source.manifest,
        commit,
        summary,
    )?;
    if let Some(existing) = doc.submissions.get(&request.id) {
        ensure!(
            existing.request.same_revision(&request)?,
            "Submission retry changed revision"
        );
        return Ok(view(existing));
    }
    ensure!(
        doc.submissions.len() < 256,
        "Team submission capacity reached"
    );
    let state = State {
        request,
        manifest: None,
        reports: vec![],
        error: None,
        uncertain: false,
    };
    let result = view(&state);
    doc.submissions.insert(state.request.id.clone(), state);
    Ok(result)
}
fn current_call(session: &crate::store::Session, name: &str) -> Result<Call> {
    use anyhow::Context;
    let turn = session
        .turn
        .as_ref()
        .context("Handoff requires an originating tool turn")?;
    let call = turn.queue.first().context("No originating tool call")?;
    ensure!(
        turn.status == "running" && call["name"] == name,
        "Stale handoff tool call"
    );
    let origin = Call {
        turn_id: turn.id.clone(),
        call_id: call["call_id"]
            .as_str()
            .context("Missing originating call ID")?
            .into(),
    };
    origin.validate()?;
    Ok(origin)
}
/// The report is attributed to a verifier that imported this EXACT manifest.
/// Its text remains a claim; neither a green status nor merge authority follows.
pub fn report(
    doc: &mut crate::agent_team::Document,
    session: &crate::store::Session,
    id: &str,
    text: String,
) -> Result<serde_json::Value> {
    use anyhow::Context;
    let member = session.agent_team.as_ref().context("Not a team member")?;
    ensure!(doc.coding_selection, "Coding handoff unavailable");
    let imported = doc
        .source_imports
        .get(&member.agent_id)
        .context("No acknowledged submission checkout")?;
    ensure!(
        imported.choice == (super::config::Choice::Submission { id: id.into() }),
        "Verifier did not import this submission"
    );
    let team_report_bytes: usize = doc
        .submissions
        .values()
        .flat_map(|s| &s.reports)
        .map(|r| r.text.len())
        .sum();
    let state = doc
        .submissions
        .get_mut(id)
        .context("Unknown same-team submission")?;
    let manifest = state
        .manifest
        .as_ref()
        .context("Submission is not published")?;
    ensure!(
        imported.manifest == *manifest,
        "Verifier checkout differs from immutable submission"
    );
    let origin = current_call(session, "team_report_verification")?;
    let reporter = Producer {
        team_id: doc.id.clone(),
        agent_id: member.agent_id.clone(),
        session_id: session.id.clone(),
    };
    let key = format!(
        "vr_{}",
        crate::bundle::digest(&serde_json::to_vec(&(id, &reporter, &text))?)
    );
    let report = Verification {
        id: key,
        submission_id: id.into(),
        manifest_digest: manifest.digest()?,
        submitted_commit: manifest.submitted_commit.clone(),
        reporter,
        origin,
        text,
    };
    report.require_submission(&state.request, manifest)?;
    if let Some(old) = state.reports.iter().find(|r| r.id == report.id) {
        return Ok(serde_json::json!(old));
    }
    ensure!(
        state.reports.len() < 16
            && state.reports.iter().map(|r| r.text.len()).sum::<usize>() + report.text.len()
                <= 32768
            && team_report_bytes + report.text.len() <= 262144,
        "Submission report capacity reached"
    );
    state.reports.push(report.clone());
    Ok(serde_json::json!(report))
}
pub fn view(state: &State) -> serde_json::Value {
    serde_json::json!({"id":state.request.id,"producer":state.request.producer.agent_id,"commit":state.request.submitted_commit,"summary":state.request.summary,"status":if state.manifest.is_some(){"ready"}else if state.error.is_some(){"blocked"}else if state.uncertain{"outcome_unknown"}else{"pending"},"error":state.error,"reports":state.reports})
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Reception {
    pub submission_id: String,
    pub manifest_digest: String,
    pub origin: Call,
    pub workspace: crate::team_workspace::Request,
    pub receipt: Option<crate::team_workspace::Receipt>,
    pub error: Option<String>,
    #[serde(default, skip_serializing_if = "std::ops::Not::not")]
    pub uncertain: bool,
}
pub fn receive(
    doc: &mut crate::agent_team::Document,
    session: &crate::store::Session,
    id: &str,
) -> Result<serde_json::Value> {
    use anyhow::Context;
    ensure!(
        doc.coding_selection
            && session
                .agent_team
                .as_ref()
                .is_some_and(|m| m.agent_id == doc.lead),
        "Only the original coding lead may receive a submission"
    );
    let state = doc
        .submissions
        .get(id)
        .context("Unknown same-team submission")?;
    let manifest = state
        .manifest
        .as_ref()
        .context("Submission not published")?;
    state.request.require_manifest(manifest)?;
    if let Some(original) = doc.receptions.get(id) {
        return Ok(reception_view(original));
    }
    let operation_id = format!("rcv_{}", crate::bundle::digest(id.as_bytes()));
    let scope_digest = crate::bundle::digest(&serde_json::to_vec(&(
        "coding-reception-v1",
        &doc.id,
        &session.id,
        &session.workspace,
        &session.environment,
        id,
        manifest.digest()?,
    ))?);
    let request = crate::team_workspace::Request {
        operation_id,
        scope_digest,
        selection: crate::team_workspace::Selection::Directory {
            path: format!(".openworker-received/{}/{id}", doc.id),
        },
    };
    request.validate()?;
    let reception = Reception {
        submission_id: id.into(),
        manifest_digest: manifest.digest()?,
        origin: current_call(session, "team_receive_submission")?,
        workspace: request,
        receipt: None,
        error: None,
        uncertain: false,
    };
    let result = reception_view(&reception);
    doc.receptions.insert(id.into(), reception);
    Ok(result)
}
pub fn reception_view(r: &Reception) -> serde_json::Value {
    serde_json::json!({"submission_id":r.submission_id,"status":if r.receipt.is_some(){"ready"}else if r.error.is_some(){"blocked"}else if r.uncertain{"outcome_unknown"}else{"pending"},"path":r.receipt.as_ref().map(|p|&p.path),"operation_id":r.workspace.operation_id,"error":r.error})
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct IntegrationState {
    pub id: String,
    pub request: Integration,
    pub origin: Call,
    pub outcome: Option<super::integrate::Observation>,
    pub error: Option<String>,
    #[serde(default, skip_serializing_if = "std::ops::Not::not")]
    pub uncertain: bool,
}
pub fn request_integration(
    doc: &mut crate::agent_team::Document,
    session: &crate::store::Session,
    id: &str,
    expected_head: String,
) -> Result<serde_json::Value> {
    use anyhow::Context;
    ensure!(
        doc.coding_selection
            && session
                .agent_team
                .as_ref()
                .is_some_and(|m| m.agent_id == doc.lead),
        "Only the coding lead may integrate"
    );
    let state = doc
        .submissions
        .get(id)
        .context("Unknown same-team submission")?;
    let manifest = state
        .manifest
        .as_ref()
        .context("Submission is not published")?;
    let request = Integration {
        submission_id: id.into(),
        manifest_digest: manifest.digest()?,
        expected_head,
    };
    request.require_submission(&state.request, manifest)?;
    let reception = doc
        .receptions
        .get(id)
        .context("Receive the submission before integration")?;
    ensure!(
        reception.manifest_digest == request.manifest_digest && reception.receipt.is_some(),
        "Original reception not acknowledged"
    );
    let key = format!(
        "int_{}",
        crate::bundle::digest(&serde_json::to_vec(&(
            "coding-integration-v1",
            &doc.id,
            &session.id,
            &session.environment,
            &request
        ))?)
    );
    if let Some(old) = doc.integrations.get(&key) {
        return Ok(integration_view(old));
    }
    ensure!(
        doc.integrations.len() < 256,
        "Team integration capacity reached"
    );
    let state = IntegrationState {
        id: key.clone(),
        request,
        origin: current_call(session, "team_integrate_submission")?,
        outcome: None,
        error: None,
        uncertain: false,
    };
    let result = integration_view(&state);
    doc.integrations.insert(key, state);
    Ok(result)
}
pub fn integration_view(i: &IntegrationState) -> serde_json::Value {
    serde_json::json!({"id":i.id,"submission_id":i.request.submission_id,"expected_head":i.request.expected_head,"status":match i.outcome.as_ref(){Some(super::integrate::Observation::Applied{..})=>"applied",Some(super::integrate::Observation::Rejected{..})=>"rejected",None if i.error.is_some()=>"blocked",None if i.uncertain=>"outcome_unknown",None=>"pending"},"outcome":i.outcome,"error":i.error})
}

pub fn approval_required(alias: &str) -> bool {
    matches!(
        alias,
        "team_submit_code" | "team_receive_submission" | "team_integrate_submission"
    )
}
#[derive(Debug)]
pub struct ApprovalInvalidated;
impl std::fmt::Display for ApprovalInvalidated {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(
            f,
            "Handoff approval is missing, revoked or bound to different policy"
        )
    }
}
impl std::error::Error for ApprovalInvalidated {}
/// Called INSIDE the adapter's session transaction, immediately before the
/// intent/tool-result commit. The earlier LLM-loop check alone cannot fence a
/// concurrent session-grant revocation. No tool label can override these pins.
pub fn require_approval(s: &crate::store::Session, call: &serde_json::Value) -> Result<()> {
    let alias = call["name"].as_str().unwrap_or_default();
    if !approval_required(alias) {
        return Ok(());
    }
    let valid = (|| {
        let turn = s.turn.as_ref()?;
        let i = s
            .interactions
            .iter()
            .rev()
            .find(|i| i.turn_id == turn.id && i.call == *call)?;
        let approved = matches!(i.response.as_deref(), Some("once" | "session"));
        let binding = i.kind == "approval"
            && i.bundle_digest == s.bundle_digest
            && Some(&i.schema_digest) == s.schema_digest.as_ref()
            && i.policy_digest == s.tool_policy.digest
            && i.resource_policy_digest == s.resource_policy.digest
            && i.server.is_none()
            && i.remote_tool.is_none();
        let grant = i.grant_id.as_ref().is_none_or(|id| {
            s.approval_grants.iter().any(|g| {
                g.id == *id
                    && g.tool == alias
                    && g.server.is_none()
                    && g.remote_tool.is_none()
                    && g.bundle_digest == s.bundle_digest
                    && Some(&g.schema_digest) == s.schema_digest.as_ref()
                    && g.policy_digest == s.tool_policy.digest
                    && g.resource_policy_digest == s.resource_policy.digest
            })
        });
        Some(approved && binding && grant)
    })()
    .unwrap_or(false);
    ensure!(valid, ApprovalInvalidated);
    Ok(())
}
