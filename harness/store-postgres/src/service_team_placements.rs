//! Trusted provider outbox. Allocation/quota/ownership commit before I/O; a
//! reclaimed operation can inspect its original identity, never replay create.
use crate::{
    db,
    service_catalog::{Entry, Profile, Release},
    PostgresStore,
};
use anyhow::{ensure, Context, Result};
use openworker_runtime::{
    agent_team::Document,
    environment::{Binding, Management, Phase, Record, Spec},
    team_definition::Sandbox,
};
use serde::{Deserialize, Serialize};
use serde_json::json;
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct Intent {
    pub team: String,
    pub agent: String,
    pub lead: String,
    pub environment_id: String,
    pub profile_id: String,
    pub profile_digest: String,
    pub source_release: String,
    pub principal: String,
    pub project: String,
    pub choice: Sandbox,
    pub spec: Spec,
}
#[derive(Clone, Debug)]
pub struct Job {
    pub intent: Intent,
    pub environment: Record,
    pub dispatch: bool,
    pub claim: String,
}
pub(crate) fn migrate(tx: &mut postgres::Transaction<'_>) -> Result<()> {
    tx.batch_execute("CREATE TABLE IF NOT EXISTS service_team_environments(team TEXT NOT NULL,agent TEXT NOT NULL,environment_id TEXT NOT NULL UNIQUE REFERENCES environments(id),data JSONB NOT NULL,status TEXT NOT NULL DEFAULT 'pending',dispatched BOOLEAN NOT NULL DEFAULT FALSE,next_check TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),claim TEXT,PRIMARY KEY(team,agent));")?;
    Ok(())
}
impl PostgresStore {
    /// No model-facing endpoint. `spec` comes from the configured trusted
    /// provider and is revalidated there before any remote creation.
    pub fn reserve_team_environment(
        &self,
        lead_id: &str,
        agent: &str,
        target: Entry,
        spec: Spec,
    ) -> Result<Intent> {
        ensure!(self.auth.is_none(), crate::auth::Error::Forbidden);
        let (lead_id, agent) = (lead_id.to_owned(), agent.to_owned());
        self.call(move|db|{
            let mut tx=db.client.transaction()?;
            let lead=db::locked(&mut tx,&lead_id,None)?;
            let team=&lead.agent_team.as_ref().context("Not a team lead")?.team_id;
            let doc:Document=serde_json::from_value(tx.query_one("SELECT data FROM agent_teams WHERE id=$1 FOR UPDATE",&[team])?.get(0))?;
            ensure!(doc.roster.instance(&doc.lead).is_some_and(|i|i.session_id==lead.id) && !doc.stopped && doc.placement_selection,"Team lead/scope unavailable");
            ensure!(doc.roster.instance(&agent).is_some_and(|i|i.phase==openworker_runtime::team_coordination::Phase::Reserved),crate::SubmissionError::Conflict);
            let choice=doc.sandbox_choices.get(&agent).context("Missing original sandbox choice")?;
            let Sandbox::New{profile,files,network,credentials,cpu,memory_mb}=choice else{anyhow::bail!("A new environment requires a New choice")};
            let catalog=doc.type_catalog.as_ref().context("Placement is not delegated")?;catalog.check_scope(&lead)?;choice.validate(&catalog.ceiling)?;
            let identity=crate::agent_team::workspace_authority(&mut tx,&lead,&doc,&agent)?;
            let origin=tx.query_one("SELECT principal,project,release_id,profile_id FROM service_operations WHERE session_id=$1 AND status='complete'",&[&lead.id])?;
            let principal:String=origin.get(0);let project:String=origin.get(1);let release_id:String=origin.get(2);
            let source=crate::service_catalog::entry(&mut tx,"release",&release_id)?;
            let source_profile=crate::service_catalog::entry(&mut tx,"profile",&origin.get::<_,String>(3))?;
            let source_p:Profile=serde_json::from_value(source_profile.definition)?;
            let release:Release=serde_json::from_value(source.definition)?;
            let saved=crate::service_catalog::entry(&mut tx,"profile",profile)?;
            let p:Profile=serde_json::from_value(saved.definition.clone())?;p.validate()?;
            ensure!(openworker_runtime::team_definition::Grants{files:files.clone(),network:network.clone(),credentials:credentials.clone(),cpu:*cpu,memory_mb:*memory_mb} == p.delegated_grants(),"Requested grants must match the selected profile exactly");
            ensure!(saved.id==*profile && saved.id==target.id && saved.digest==target.digest && saved.activated && saved.installation==source.installation && release.runtime_profiles.contains(profile),"Target profile unavailable, changed, aliased or not delegated by the release");
            crate::service_catalog::allowed(&identity,&p.allowed_principals)?;
            ensure!(*cpu==p.cpu && *memory_mb==p.memory_mb && p.cpu<=source_p.cpu && p.memory_mb<=source_p.memory_mb,"Requested resources must match a bounded activated RuntimeProfile");
            let kind=openworker_runtime::agent_team::runtime_type(&doc,doc.roster.instance(&agent).context("Missing reservation")?)?;
            let template:serde_json::Value=tx.query_one("SELECT data FROM agent_team_templates WHERE team=$1 AND kind=$2",&[team,&kind])?.get(0);
            let child=crate::service_catalog::entry(&mut tx,"release",template["release"].as_str().context("Missing child release")?)?;
            let child:Release=serde_json::from_value(child.definition)?;
            ensure!(child.runtime_profiles.contains(profile),"Child release does not permit target profile");
            target_authority(&mut tx,&lead,&doc,&agent,profile)?;
            let lead_binding=lead.environment.as_ref().context("Lead has no environment")?;
            spec.validate()?;
            ensure!(spec.provider==lead_binding.spec.provider && spec.provider_identity==lead_binding.spec.provider_identity && spec.image==lead_binding.spec.image && spec.workspace_root==lead_binding.spec.workspace_root && spec.sharing==p.sharing && spec.management==Management::Harness,"Provider profile does not belong to the original deployment");
            if let Some(row)=tx.query_opt("SELECT data FROM service_team_environments WHERE team=$1 AND agent=$2",&[team,&agent])?{
                let old:Intent=serde_json::from_value(row.get(0))?;
                ensure!(old.lead==lead.id && old.choice==*choice && old.spec==spec && old.profile_digest==saved.digest && old.principal==principal && old.project==project,"Changed placement intent");
                tx.commit()?;return Ok(old)
            }
            tx.query_one("SELECT pg_advisory_xact_lock(hashtext(current_schema()),hashtext('service-environment-quota'))",&[])?;
            let configured=crate::service_admin::current(&mut tx)?;
            ensure!(!configured.configuration.admissions_paused,crate::SubmissionError::Busy);
            let pending:i64=tx.query_one("SELECT (SELECT count(*) FROM service_operations WHERE status NOT IN ('complete','failed'))+(SELECT count(*) FROM service_team_environments WHERE status NOT IN ('ready','failed'))",&[])?.get(0);
            let owned:i64=tx.query_one("SELECT count(*) FROM service_environment_owners o JOIN environments e ON e.id=o.environment_id WHERE o.principal=$1 AND e.data->>'phase'<>'deleted'",&[&principal])?.get(0);
            let total:i64=tx.query_one("SELECT count(*) FROM service_environment_owners o JOIN environments e ON e.id=o.environment_id WHERE e.data->>'phase'<>'deleted'",&[])?.get(0);
            ensure!(pending<configured.configuration.max_pending_operations && owned<configured.configuration.max_environments_per_principal && total<configured.configuration.max_environments,crate::SubmissionError::QueueFull);
            let environment=Record{id:openworker_runtime::id("env"),request_id:openworker_runtime::id("teamenv"),actor:principal.clone(),spec:spec.clone(),phase:Phase::Provisioning,binding:None,revision:1};
            let intent=Intent{team:team.clone(),agent,lead:lead.id,environment_id:environment.id.clone(),profile_id:saved.id,profile_digest:saved.digest,source_release:release_id,principal,project,choice:choice.clone(),spec};
            tx.execute("INSERT INTO environments(id,request_id,data) VALUES($1,$2,$3)",&[&environment.id,&environment.request_id,&json!(environment)])?;
            tx.execute("INSERT INTO service_environment_owners VALUES($1,$2,$3,$4)",&[&environment.id,&intent.profile_id,&intent.principal,&intent.project])?;
            tx.execute("INSERT INTO service_team_environments(team,agent,environment_id,data) VALUES($1,$2,$3,$4)",&[&intent.team,&intent.agent,&environment.id,&json!(intent)])?;
            tx.execute("INSERT INTO environment_events(environment_id,revision,kind,data) VALUES($1,1,'environment_requested',$2)",&[&environment.id,&json!({"team":intent.team,"agent_id":intent.agent,"actor":intent.principal})])?;
            crate::team_execution::session_event(&mut tx,&intent.lead,None,"team_environment",json!({"agent_id":intent.agent,"environment_id":environment.id,"profile_id":intent.profile_id,"phase":"pending","retention":"keep"}))?;
            tx.commit()?;Ok(intent)
        })
    }
    pub fn claim_team_environment(&self, installation: &str) -> Result<Option<Job>> {
        ensure!(self.auth.is_none(), crate::auth::Error::Forbidden);
        let installation = installation.to_owned();
        self.call(move|db|{
            let mut tx=db.client.transaction()?;
            // Match admission's lock order: lead, team, then placement. Selecting
            // a candidate is read-only; the guarded row lock below elects one owner.
            let row=tx.query_opt("SELECT o.data FROM service_team_environments o JOIN service_catalog c ON c.kind='release' AND c.id=o.data->>'source_release' WHERE c.data->>'installation'=$1 AND o.status IN ('pending','dispatching','unknown') AND o.next_check<=clock_timestamp() ORDER BY o.next_check,o.team,o.agent LIMIT 1",&[&installation])?;
            let Some(row)=row else{tx.commit()?;return Ok(None)};
            let candidate:Intent=serde_json::from_value(row.get(0))?;
            let lead=db::locked(&mut tx,&candidate.lead,None)?;
            let doc:Document=serde_json::from_value(tx.query_one("SELECT data FROM agent_teams WHERE id=$1 FOR UPDATE",&[&candidate.team])?.get(0))?;
            let row=tx.query_opt("SELECT o.data,e.data,e.revision,o.dispatched FROM service_team_environments o JOIN environments e ON e.id=o.environment_id WHERE o.team=$1 AND o.agent=$2 AND o.status IN ('pending','dispatching','unknown') AND o.next_check<=clock_timestamp() FOR UPDATE OF o SKIP LOCKED",&[&candidate.team,&candidate.agent])?;
            let Some(row)=row else{tx.commit()?;return Ok(None)};
            let intent:Intent=serde_json::from_value(row.get(0))?;let mut environment:Record=serde_json::from_value(row.get(1))?;environment.revision=row.get(2);let dispatched:bool=row.get(3);
            ensure!(intent==candidate && environment.id==intent.environment_id && environment.spec==intent.spec && environment.actor==intent.principal,"Original environment intent changed");
            if !dispatched {
                if crate::service_admin::current(&mut tx)?.configuration.admissions_paused {tx.commit()?;return Ok(None)}
                let available=!doc.stopped && doc.roster.instance(&intent.agent).is_some_and(|i|i.phase==openworker_runtime::team_coordination::Phase::Reserved);
                let authorized=(||->Result<()> {
                    ensure!(available,"Original reservation was retired");
                    let identity=crate::agent_team::workspace_authority(&mut tx,&lead,&doc,&intent.agent)?;
                    let target=crate::service_catalog::entry(&mut tx,"profile",&intent.profile_id)?;
                    ensure!(target.activated && target.digest==intent.profile_digest,"Original target profile unavailable");
                    let p:Profile=serde_json::from_value(target.definition)?;
                    crate::service_catalog::allowed(&identity,&p.allowed_principals)?;
                    target_authority(&mut tx,&lead,&doc,&intent.agent,&intent.profile_id)?;Ok(())
                })();
                if let Err(error)=authorized {
                    if error.downcast_ref::<postgres::Error>().is_some(){return Err(error)}
                    tx.execute("UPDATE service_team_environments SET status='failed' WHERE team=$1 AND agent=$2",&[&intent.team,&intent.agent])?;
                    // No dispatch ever occurred. Only this proven-unstarted
                    // allocation may release quota without provider deletion.
                    if environment.phase==Phase::Provisioning && environment.binding.is_none(){
                        let mut cancelled=environment.clone();cancelled.phase=Phase::Deleted;cancelled.revision+=1;
                        tx.execute("UPDATE environments SET data=$2,revision=$3 WHERE id=$1 AND revision=$4",&[&cancelled.id,&json!(cancelled),&cancelled.revision,&environment.revision])?;
                        tx.execute("INSERT INTO environment_events(environment_id,revision,kind,data) VALUES($1,$2,'creation_cancelled_before_dispatch',$3)",&[&cancelled.id,&cancelled.revision,&json!({"team":intent.team,"agent_id":intent.agent,"dispatched":false})])?;
                    }
                    crate::team_execution::session_event(&mut tx,&lead.id,None,"team_environment",json!({"agent_id":intent.agent,"environment_id":environment.id,"phase":"denied_before_dispatch"}))?;
                    tx.commit()?;return Ok(None)
                }
            }
            let claim=openworker_runtime::id("claim");
            tx.execute("UPDATE service_team_environments SET dispatched=TRUE,status='dispatching',claim=$3,next_check=clock_timestamp()+interval '180 seconds' WHERE team=$1 AND agent=$2",&[&intent.team,&intent.agent,&claim])?;
            tx.commit()?;Ok(Some(Job{intent,environment,dispatch:!dispatched,claim}))
        })
    }
    /// Observation is immutable evidence about the ORIGINAL resource. It grants
    /// no child model/tool admission; that transaction must recheck authority.
    pub fn observe_team_environment(&self, job: &Job, binding: Option<Binding>) -> Result<Record> {
        ensure!(self.auth.is_none(), crate::auth::Error::Forbidden);
        ensure!(
            job.environment.id == job.intent.environment_id
                && job.environment.spec == job.intent.spec
                && job.environment.actor == job.intent.principal,
            "Observation job belongs to another environment"
        );
        let job = job.clone();
        self.call(move|db|{
            let mut tx=db.client.transaction()?;
            let lead=db::locked(&mut tx,&job.intent.lead,None)?;
            let mut doc:Document=serde_json::from_value(tx.query_one("SELECT data FROM agent_teams WHERE id=$1 FOR UPDATE",&[&job.intent.team])?.get(0))?;
            let row=tx.query_one("SELECT data,claim,status,next_check>clock_timestamp() AS live FROM service_team_environments WHERE team=$1 AND agent=$2 FOR UPDATE",&[&job.intent.team,&job.intent.agent])?;
            let saved:Intent=serde_json::from_value(row.get(0))?;
            ensure!(saved==job.intent && row.get::<_,Option<String>>(1).as_deref()==Some(&job.claim) && row.get::<_,String>(2)=="dispatching" && row.get::<_,bool>(3),"Stale placement claim");
            if let Some(b)=&binding {ensure!(b.environment_id==saved.environment_id && b.spec==saved.spec,"Observation belongs to another intent");}
            // Fence the receipt and original environment in ONE transaction.
            // A reclaimed controller may inspect, never replay a lost create.
            let observed=crate::environments::complete(&mut tx,&saved.environment_id,job.environment.revision,binding)?;
            let phase=if observed.phase==Phase::Ready{"ready"}else{"unknown"};
            tx.execute("UPDATE service_team_environments SET status=$3,next_check=clock_timestamp()+interval '2 seconds',claim=NULL WHERE team=$1 AND agent=$2",&[&saved.team,&saved.agent,&phase])?;
            if observed.phase!=job.environment.phase {
                let body=if observed.phase==Phase::Ready {format!("Sandbox for {} is ready; environment {}. This status does not authorize new actions.",saved.agent,saved.environment_id)}else{format!("Sandbox creation outcome for {} is unknown; environment {}. The controller will inspect this original resource only. Do not assume creation failed or request a replacement as a retry.",saved.agent,saved.environment_id)};
                if observed.phase==Phase::Unknown {
                doc.sequence+=1;
                let message=openworker_runtime::agent_team::Message{id:openworker_runtime::id("tm"),sequence:doc.sequence,sender:"harness".into(),body,recipients:vec![doc.lead.clone()],board:false};
                tx.execute("INSERT INTO agent_team_messages VALUES($1,$2,$3,$4)",&[&doc.id,&(message.sequence as i64),&message.id,&json!(message)])?;
                tx.execute("UPDATE agent_teams SET data=$2 WHERE id=$1",&[&doc.id,&json!(doc)])?;
                }
                crate::team_execution::session_event(&mut tx,&lead.id,None,"team_environment",json!({"agent_id":saved.agent,"environment_id":observed.id,"phase":phase,"retention":"keep"}))?;
            }
            tx.commit()?;Ok(observed)
        })
    }
}

/// Recheck effective target policy on allocation, binding and admission. An
/// approved label cannot enlarge the actual lead's CPU/memory or permissions.
pub(crate) fn target_authority(
    tx: &mut postgres::Transaction<'_>,
    lead: &openworker_runtime::store::Session,
    doc: &Document,
    agent: &str,
    profile_id: &str,
) -> Result<Entry> {
    let identity = crate::agent_team::workspace_authority(tx, lead, doc, agent)?;
    let origin=tx.query_one("SELECT release_id,profile_id FROM service_operations WHERE session_id=$1 AND status='complete'",&[&lead.id])?;
    let source = crate::service_catalog::entry(tx, "release", &origin.get::<_, String>(0))?;
    let release: Release = serde_json::from_value(source.definition)?;
    let original = crate::service_catalog::entry(tx, "profile", &origin.get::<_, String>(1))?;
    let original: Profile = serde_json::from_value(original.definition)?;
    let target = crate::service_catalog::entry(tx, "profile", profile_id)?;
    let p: Profile = serde_json::from_value(target.definition.clone())?;
    ensure!(
        target.id == profile_id
            && target.activated
            && target.installation == source.installation
            && release.runtime_profiles.contains(&target.id),
        "Target profile is not available to source release"
    );
    crate::service_catalog::allowed(&identity, &p.allowed_principals)?;
    p.require_delegatable_from(&original)?;
    if p.id != original.id {
        let grants = doc
            .type_catalog
            .as_ref()
            .and_then(|c| c.ceiling.profiles.get(&p.id))
            .context("Target profile is not delegated")?;
        ensure!(
            grants == &p.delegated_grants(),
            "Target profile does not match delegated grants/resources"
        );
    }
    let kind = openworker_runtime::agent_team::runtime_type(
        doc,
        doc.roster.instance(agent).context("Missing reservation")?,
    )?;
    let template: serde_json::Value = tx
        .query_one(
            "SELECT data FROM agent_team_templates WHERE team=$1 AND kind=$2",
            &[&doc.id, &kind],
        )?
        .get(0);
    let child = crate::service_catalog::entry(
        tx,
        "release",
        template["release"]
            .as_str()
            .context("Missing child release")?,
    )?;
    let child: Release = serde_json::from_value(child.definition)?;
    ensure!(
        child.runtime_profiles.contains(&p.id),
        "Child release does not permit target profile"
    );
    Ok(target)
}
impl PostgresStore {
    /// Pin a READY original New/Member provider object before child or workspace
    /// admission. A pending target waits; it never falls back to the lead.
    pub fn bind_team_placement(&self, lead_id: &str, agent: &str) -> Result<bool> {
        ensure!(self.auth.is_none(), crate::auth::Error::Forbidden);
        let (lead_id, agent) = (lead_id.to_owned(), agent.to_owned());
        self.call(move|db|{
            let mut tx=db.client.transaction()?;
            let lead=db::locked(&mut tx,&lead_id,None)?;
            let team=&lead.agent_team.as_ref().context("Not a team lead")?.team_id;
            let mut doc:Document=serde_json::from_value(tx.query_one("SELECT data FROM agent_teams WHERE id=$1 FOR UPDATE",&[team])?.get(0))?;
            ensure!(doc.roster.instance(&doc.lead).is_some_and(|i|i.session_id==lead.id),"Not original lead");
            if doc.stopped || !doc.roster.instance(&agent).is_some_and(|i|i.phase==openworker_runtime::team_coordination::Phase::Reserved) {tx.commit()?;return Ok(false)}
            let choice=doc.sandbox_choices.get(&agent).context("Missing selected placement")?.clone();
            if choice==Sandbox::default() || doc.placement_bindings.contains_key(&agent) {tx.commit()?;return Ok(false)}
            ensure!(doc.placement_selection,"Placement adapter is not enabled");
            let (id,profile)=match &choice {
                Sandbox::New{..}=>{
                    let row=tx.query_opt("SELECT environment_id,data,status FROM service_team_environments WHERE team=$1 AND agent=$2",&[team,&agent])?;
                    let Some(row)=row else{tx.commit()?;return Ok(false)};
                    let status:String=row.get(2);
                    if status=="failed" {
                        doc.roster.close(&agent)?;doc.failures.insert(agent,"Sandbox admission denied before dispatch".into());
                        tx.execute("UPDATE agent_teams SET data=$2 WHERE id=$1",&[team,&json!(doc)])?;tx.commit()?;return Ok(true)
                    }
                    if status!="ready" {tx.commit()?;return Ok(false)}
                    let intent:Intent=serde_json::from_value(row.get(1))?;
                    ensure!(intent.lead==lead.id && intent.choice==choice,"Changed original placement intent");
                    (row.get::<_,String>(0),intent.profile_id)
                },
                Sandbox::Member{agent_id}=>{
                    let member=doc.roster.instance(agent_id).context("Missing sandbox source")?;
                    if member.phase==openworker_runtime::team_coordination::Phase::Reserved {tx.commit()?;return Ok(false)}
                    ensure!(member.phase==openworker_runtime::team_coordination::Phase::Ready,"Sandbox source retired");
                    let session:openworker_runtime::store::Session=serde_json::from_value(tx.query_one("SELECT data FROM sessions WHERE id=$1",&[&member.session_id])?.get(0))?;
                    ensure!(!session.closed,"Sandbox source is closed");
                    let binding=session.environment.context("Source has no environment")?;
                    ensure!(binding.spec.sharing==openworker_runtime::environment::Sharing::Shared,"Source sandbox is exclusive");
                    let profile:String=tx.query_one("SELECT profile_id FROM service_environment_owners WHERE environment_id=$1",&[&binding.environment_id])?.get(0);
                    (binding.environment_id,profile)
                },
                Sandbox::Team{}=>unreachable!(),
            };
            let profile=target_authority(&mut tx,&lead,&doc,&agent,&profile)?;
            let row=tx.query_one("SELECT data FROM environments WHERE id=$1 FOR UPDATE",&[&id])?;
            let env:Record=serde_json::from_value(row.get(0))?;
            ensure!(env.phase==Phase::Ready,"Selected original environment unavailable");
            let binding=env.binding.context("Environment has no binding")?;
            if matches!(choice,Sandbox::Member{..}) {ensure!(binding.spec.sharing==openworker_runtime::environment::Sharing::Shared,"Member sandbox is not shareable")}
            doc.placement_bindings.insert(agent.clone(),openworker_runtime::team_placement::Binding{choice,environment:binding,profile_id:profile.id,profile_digest:profile.digest});
            tx.execute("UPDATE agent_teams SET data=$2 WHERE id=$1",&[team,&json!(doc)])?;
            crate::team_execution::session_event(&mut tx,&lead.id,None,"team_environment",json!({"agent_id":agent,"environment_id":id,"phase":"bound","retention":"keep"}))?;
            tx.commit()?;Ok(true)
        })
    }
}

impl PostgresStore {
    /// Close only an unstarted child; provider resources retain independent
    /// ownership/reconciliation and are never deleted because admission failed.
    pub fn reject_team_placement(&self, lead_id: &str, agent: &str) -> Result<()> {
        ensure!(self.auth.is_none(), crate::auth::Error::Forbidden);
        let (lead_id, agent) = (lead_id.to_owned(), agent.to_owned());
        self.call(move|db|{
            let mut tx=db.client.transaction()?;
            let lead=db::locked(&mut tx,&lead_id,None)?;
            let team=&lead.agent_team.as_ref().context("Not a team lead")?.team_id;
            let mut doc:Document=serde_json::from_value(tx.query_one("SELECT data FROM agent_teams WHERE id=$1 FOR UPDATE",&[team])?.get(0))?;
            ensure!(doc.roster.instance(&doc.lead).is_some_and(|i|i.session_id==lead.id),"Not original lead");
            let member=doc.roster.instance(&agent).context("Missing reservation")?;
            if member.phase!=openworker_runtime::team_coordination::Phase::Reserved {tx.commit()?;return Ok(())}
            ensure!(!tx.query_one("SELECT EXISTS(SELECT 1 FROM sessions WHERE id=$1)",&[&member.session_id])?.get::<_,bool>(0),"A created child needs normal lifecycle reconciliation");
            doc.roster.close(&agent)?;
            doc.failures.insert(agent.clone(),"Sandbox admission denied: original authority, target profile or provider binding is unavailable. Existing resources are retained for inspection.".into());
            tx.execute("UPDATE agent_teams SET data=$2 WHERE id=$1",&[team,&json!(doc)])?;
            crate::team_execution::session_event(&mut tx,&lead.id,None,"team_environment",json!({"agent_id":agent,"phase":"admission_denied","retention":"keep"}))?;
            tx.commit()?;Ok(())
        })
    }
}
