//! Private deployment adapter for session-scoped approved types. No provider I/O,
//! keys, global catalog entries, or worker-lease ownership lives here.
use crate::{
    service_catalog::{self, Entry, Release},
    PostgresStore,
};
use anyhow::{ensure, Context, Result};
use openworker_runtime::{
    agent_team::Document,
    connections::{Choice, Selection},
    store::Session,
    team_type_catalog::{Record, Status},
};
use serde_json::{json, Value};
use std::collections::BTreeMap;

#[derive(Clone)]
pub struct Team {
    pub document: Document,
    pub lead: Session,
    pub release: Entry,
    pub profile: Entry,
    pub models: BTreeMap<String, Selection>,
}
pub fn model_key(choice: &Choice) -> Result<String> {
    choice.validate()?;
    Ok(openworker_runtime::bundle::digest(&serde_json::to_vec(
        choice,
    )?))
}
pub(crate) fn validate_models(
    tx: &mut postgres::Transaction<'_>,
    lead: &Session,
    args: &Value,
    models: &BTreeMap<String, Selection>,
) -> Result<()> {
    let Some(value) = args.get("delegation").filter(|v| !v.is_null()) else {
        ensure!(models.is_empty(), "Unexpected delegated models");
        return Ok(());
    };
    let d: openworker_runtime::team_definition::Delegation = serde_json::from_value(value.clone())?;
    d.bind(lead)?;
    ensure!(
        !d.placements
            .contains(&openworker_runtime::team_definition::Placement::New)
            || !d.profiles.is_empty(),
        "New placement requires a delegated profile"
    );
    let row = tx.query_one(
        "SELECT release_id FROM service_operations WHERE session_id=$1 AND status='complete'",
        &[&lead.id],
    )?;
    let source = service_catalog::entry(tx, "release", &row.get::<_, String>(0))?;
    let release: Release = serde_json::from_value(source.definition)?;
    ensure!(source.activated, "Lead release deactivated");
    let origin: String = tx
        .query_one(
            "SELECT profile_id FROM service_operations WHERE session_id=$1",
            &[&lead.id],
        )?
        .get(0);
    let origin = service_catalog::entry(tx, "profile", &origin)?;
    let original: service_catalog::Profile = serde_json::from_value(origin.definition)?;
    for (name, grants) in &d.profiles {
        let entry = service_catalog::entry(tx, "profile", name)?;
        let p: service_catalog::Profile = serde_json::from_value(entry.definition)?;
        ensure!(
            entry.id == *name
                && entry.activated
                && entry.installation == source.installation
                && release.runtime_profiles.contains(name),
            "Profile is not an immutable activated delegated profile"
        );
        ensure!(
            grants == &p.delegated_grants(),
            "Delegated profile grants must match the activated profile"
        );
        p.require_delegatable_from(&original)?;
    }
    ensure!(
        models.len() == d.models.len(),
        "Delegated model reference count changed"
    );
    for choice in &d.models {
        ensure!(
            release.default_model.as_ref() == Some(choice)
                || release.model_overrides.contains(choice),
            "Model not delegated by activated lead release"
        );
        let selected = models
            .get(&model_key(choice)?)
            .context("Missing model reference")?;
        ensure!(
            selected.connection == choice.connection
                && selected.model == choice.model
                && !selected.identity.is_empty(),
            "Invalid model reference"
        );
    }
    Ok(())
}
fn approved<'a>(doc: &'a Document, record: &Record, lead: &Session) -> Result<&'a Record> {
    let catalog = doc
        .type_catalog
        .as_ref()
        .context("No scoped type catalog")?;
    catalog.check_scope(lead)?;
    let stored = catalog
        .records
        .values()
        .flatten()
        .find(|r| r.binding.digest() == record.binding.digest())
        .context("Missing scoped revision")?;
    ensure!(stored.status == Status::Approved, "Type is not approved");
    stored
        .binding
        .revalidate(&catalog.ceiling, &catalog.prebuilt)?;
    ensure!(
        doc.placement_selection || stored.binding.definition().sandbox == Default::default(),
        "Unsupported service placement"
    );
    Ok(stored)
}
impl PostgresStore {
    /// Private, bounded cursor page. Restarted controllers/workers reconstruct the
    /// same approved revisions; no globally loadable coworker is registered.
    pub fn service_team_type_page(
        &self,
        installed: &str,
        after: &str,
        limit: u16,
    ) -> Result<Vec<Team>> {
        ensure!(self.auth.is_none(), crate::auth::Error::Forbidden);
        ensure!((1..=32).contains(&limit), "Invalid type page size");
        let (installed, after) = (installed.to_owned(), after.to_owned());
        self.call(move|db|{
            let mut tx=db.client.transaction()?;
            let rows=tx.query("SELECT t.data,s.data,o.release_id,o.profile_id FROM agent_teams t JOIN sessions s ON s.id=t.data->'roster'->'instances'->(t.data->>'lead')->>'session_id' JOIN service_operations o ON o.session_id=s.id AND o.status='complete' JOIN service_catalog c ON c.kind='release' AND c.id=o.release_id WHERE t.id>$1 AND t.data->'type_catalog' IS NOT NULL AND c.data->>'installation'=$2 ORDER BY t.id LIMIT $3",&[&after,&installed,&(limit as i64)])?;
            let mut result=vec![];
            for row in rows {
                let doc:Document=serde_json::from_value(row.get(0))?;
                let lead:Session=serde_json::from_value(row.get(1))?;
                let release=service_catalog::entry(&mut tx,"release",&row.get::<_,String>(2))?;
                let profile=service_catalog::entry(&mut tx,"profile",&row.get::<_,String>(3))?;
                let models=tx.query_opt("SELECT data->'models' FROM agent_team_templates WHERE team=$1 AND data ? 'models' ORDER BY kind LIMIT 1",&[&doc.id])?.and_then(|r|r.get::<_,Option<Value>>(0)).map(serde_json::from_value).transpose()?.unwrap_or_default();
                result.push(Team{document:doc,lead,release,profile,models});
            }
            tx.commit()?;Ok(result)
        })
    }
    /// Compilation is deterministic I/O outside this transaction. Commit only
    /// against the original approved revision, source release and lead scope.
    pub fn install_service_team_type(
        &self,
        team: &Team,
        record: &Record,
        session: Session,
    ) -> Result<()> {
        ensure!(self.auth.is_none(), crate::auth::Error::Forbidden);
        let (team, record) = (team.clone(), record.clone());
        self.call(move|db|{
            let mut tx=db.client.transaction()?;
            // Match worker/API lock order: lead session, then team. Compilation
            // runs outside this short transaction and never claims a worker lease.
            let lead=crate::db::locked(&mut tx,&team.lead.id,None)?;
            let doc:Document=serde_json::from_value(tx.query_one("SELECT data FROM agent_teams WHERE id=$1 FOR UPDATE",&[&team.document.id])?.get(0))?;
            let stored=approved(&doc,&record,&lead)?;
            let source=service_catalog::entry(&mut tx,"release",&team.release.id)?;
            let profile=service_catalog::entry(&mut tx,"profile",&team.profile.id)?;
            ensure!(source.activated && profile.activated && source.digest==team.release.digest && profile.digest==team.profile.digest,"Source release/profile unavailable or changed");
            ensure!(session.coworker==stored.runtime_id() && session.environment==lead.environment && session.workspace==lead.workspace && serde_json::to_value(&session.permissions)?==serde_json::to_value(&lead.permissions)? && serde_json::to_value(&session.compaction_config)?==serde_json::to_value(&lead.compaction_config)? && session.turn.is_none() && session.history.is_empty() && session.agent_team.is_none() && !session.closed,"Scoped template differs from approved lead scope");
            let expected_model=match &stored.binding.definition().model {Some(m)=>Some(team.models.get(&model_key(m)?).context("Missing pinned type model")?),None=>lead.model_selection.as_ref()};
            ensure!(serde_json::to_value(&session.model_selection)?==serde_json::to_value(expected_model)?
                && (expected_model.is_some() || (session.model==lead.model && session.model_provider==lead.model_provider))
                && serde_json::to_value(&session.reviewer_selection)?==serde_json::to_value(&lead.reviewer_selection)?, "Scoped model/reviewer changed");
            let payload=json!({"release":source.id,"session":session,"models":team.models,"scope":{"binding":stored.binding.digest(),"source":source.digest,"profile":profile.digest,"lead":lead.id}});
            let kind=stored.runtime_id();
            let inserted=tx.execute("INSERT INTO agent_team_templates VALUES($1,$2,$3) ON CONFLICT DO NOTHING",&[&doc.id,&kind,&payload])?;
            if inserted==1 {crate::team_execution::session_event(&mut tx,&lead.id,None,"team_activity",json!({"operation":"type_compiled","agent_type":stored.binding.definition().name,"revision":stored.binding.revision(),"outcome":"ready"}))?;}
            let saved:Value=tx.query_one("SELECT data FROM agent_team_templates WHERE team=$1 AND kind=$2",&[&doc.id,&kind])?.get(0);
            let mut expected=payload;expected["session"]["id"]=saved["session"]["id"].clone();
            ensure!(saved==expected,"Scoped template receipt changed");
            tx.commit()?;Ok(())
        })
    }
}
pub(crate) fn check_template(
    tx: &mut postgres::Transaction<'_>,
    doc: &Document,
    instance: &openworker_runtime::team_coordination::Instance,
    template: &Value,
) -> Result<()> {
    if !doc.type_bindings.contains_key(&instance.agent_id) {
        return Ok(());
    }
    let lead_id = &doc
        .roster
        .instance(&doc.lead)
        .context("Missing lead")?
        .session_id;
    let lead: Session = serde_json::from_value(
        tx.query_one("SELECT data FROM sessions WHERE id=$1", &[&lead_id])?
            .get(0),
    )?;
    let catalog = doc.type_catalog.as_ref().context("Missing catalog")?;
    let record = catalog
        .records
        .values()
        .flatten()
        .find(|r| r.binding.digest() == doc.type_bindings[&instance.agent_id])
        .context("Missing pinned type")?;
    let record = approved(doc, record, &lead)?;
    let origin=tx.query_one("SELECT release_id,profile_id FROM service_operations WHERE session_id=$1 AND status='complete'",&[&lead.id])?;
    let source = service_catalog::entry(tx, "release", &origin.get::<_, String>(0))?;
    let profile = service_catalog::entry(tx, "profile", &origin.get::<_, String>(1))?;
    ensure!(
        source.activated
            && profile.activated
            && template["release"] == source.id
            && template["scope"]["binding"] == record.binding.digest()
            && template["scope"]["source"] == source.digest
            && template["scope"]["profile"] == profile.digest
            && template["scope"]["lead"] == lead.id,
        "Scoped template source/approval changed"
    );
    Ok(())
}
impl PostgresStore {
    pub fn service_team_type_installed(&self, team: &str, kind: &str) -> Result<bool> {
        ensure!(self.auth.is_none(), crate::auth::Error::Forbidden);
        let (team, kind) = (team.to_owned(), kind.to_owned());
        self.call(move |db| {
            Ok(db
                .client
                .query_opt(
                    "SELECT kind FROM agent_team_templates WHERE team=$1 AND kind=$2",
                    &[&team, &kind],
                )?
                .is_some())
        })
    }
    pub fn service_team_type_ready(&self, team: &str, kind: &str) -> Result<bool> {
        ensure!(self.auth.is_none(), crate::auth::Error::Forbidden);
        let (team, kind) = (team.to_owned(), kind.to_owned());
        self.call(move|db|Ok(db.client.query_opt("SELECT kind FROM agent_team_templates WHERE team=$1 AND kind=$2 AND data ? 'session'",&[&team,&kind])?.is_some()))
    }
    pub fn verify_service_team_type_runtime(
        &self,
        team: &str,
        kind: &str,
        digest: &str,
    ) -> Result<()> {
        ensure!(self.auth.is_none(), crate::auth::Error::Forbidden);
        let (team, kind, digest) = (team.to_owned(), kind.to_owned(), digest.to_owned());
        self.call(move|db|{
            let row=db.client.query_one("SELECT data->'session'->>'bundle_digest' FROM agent_team_templates WHERE team=$1 AND kind=$2",&[&team,&kind])?;
            ensure!(row.get::<_,Option<String>>(0).as_deref()==Some(&digest),"Scoped execution identity changed");Ok(())
        })
    }
    /// Known compilation/admission failure closes only this unstarted revision.
    /// No tool was dispatched, no alternate model/profile is substituted.
    pub fn reject_service_team_type(&self, team: &Team, record: &Record) -> Result<()> {
        ensure!(self.auth.is_none(), crate::auth::Error::Forbidden);
        let (team, record) = (team.clone(), record.clone());
        self.call(move|db|{
            let mut tx=db.client.transaction()?;
            // Match worker/API lock order: lead session, then team. Compilation
            // runs outside this short transaction and never claims a worker lease.
            let lead=crate::db::locked(&mut tx,&team.lead.id,None)?;
            let doc:Document=serde_json::from_value(tx.query_one("SELECT data FROM agent_teams WHERE id=$1 FOR UPDATE",&[&team.document.id])?.get(0))?;
            ensure!(doc.roster.instance(&doc.lead).is_some_and(|i|i.session_id==lead.id),"Changed team lead");
            let record=doc.type_catalog.as_ref().context("Missing catalog")?.records.values().flatten().find(|r|r.binding.digest()==record.binding.digest() && r.status==Status::Approved).context("Missing approved revision")?;
            let payload=json!({"release":team.release.id,"error":"Scoped type could not be prepared with its approved configuration"});
            let inserted=tx.execute("INSERT INTO agent_team_templates VALUES($1,$2,$3) ON CONFLICT DO NOTHING",&[&doc.id,&record.runtime_id(),&payload])?;
            if inserted==1 {crate::team_execution::session_event(&mut tx,&lead.id,None,"team_activity",json!({"operation":"type_compiled","agent_type":record.binding.definition().name,"revision":record.binding.revision(),"outcome":"failed_before_dispatch"}))?;}
            tx.commit()?;Ok(())
        })
    }
}
