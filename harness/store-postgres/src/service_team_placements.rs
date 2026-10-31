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
            ensure!(doc.roster.instance(&agent).is_some_and(|i|i.phase==openworker_runtime::team_coordination::Phase::Reserved),"Child is not reserved");
            let choice=doc.sandbox_choices.get(&agent).context("Missing original sandbox choice")?;
            let Sandbox::New{profile,files,network,credentials,cpu,memory_mb}=choice else{anyhow::bail!("A new environment requires a New choice")};
            ensure!(files.is_empty() && network.is_empty() && credentials.is_empty(),"Provider cannot enforce requested file/network/credential grants");
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
            ensure!(saved.id==*profile && saved.id==target.id && saved.digest==target.digest && saved.activated && saved.installation==source.installation && release.runtime_profiles.contains(profile),"Target profile unavailable, changed, aliased or not delegated by the release");
            crate::service_catalog::allowed(&identity,&p.allowed_principals)?;
            ensure!(*cpu==p.cpu && *memory_mb==p.memory_mb && p.cpu<=source_p.cpu && p.memory_mb<=source_p.memory_mb,"Requested resources must match a bounded activated RuntimeProfile");
            let kind=openworker_runtime::agent_team::runtime_type(&doc,doc.roster.instance(&agent).context("Missing reservation")?)?;
            let template:serde_json::Value=tx.query_one("SELECT data FROM agent_team_templates WHERE team=$1 AND kind=$2",&[team,&kind])?.get(0);
            let child=crate::service_catalog::entry(&mut tx,"release",template["release"].as_str().context("Missing child release")?)?;
            let child:Release=serde_json::from_value(child.definition)?;
            ensure!(child.runtime_profiles.contains(profile),"Child release does not permit target profile");
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
                    crate::service_catalog::allowed(&identity,&p.allowed_principals)?;Ok(())
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
            let row=tx.query_one("SELECT data,claim,status,next_check>clock_timestamp() AS live FROM service_team_environments WHERE team=$1 AND agent=$2 FOR UPDATE",&[&job.intent.team,&job.intent.agent])?;
            let saved:Intent=serde_json::from_value(row.get(0))?;
            ensure!(saved==job.intent && row.get::<_,Option<String>>(1).as_deref()==Some(&job.claim) && row.get::<_,String>(2)=="dispatching" && row.get::<_,bool>(3),"Stale placement claim");
            if let Some(b)=&binding {ensure!(b.environment_id==saved.environment_id && b.spec==saved.spec,"Observation belongs to another intent");}
            // Fence the receipt and original environment in ONE transaction.
            // A reclaimed controller may inspect, never replay a lost create.
            let observed=crate::environments::complete(&mut tx,&saved.environment_id,job.environment.revision,binding)?;
            let phase=if observed.phase==Phase::Ready{"ready"}else{"unknown"};
            tx.execute("UPDATE service_team_environments SET status=$3,next_check=clock_timestamp()+interval '2 seconds',claim=NULL WHERE team=$1 AND agent=$2",&[&saved.team,&saved.agent,&phase])?;
            tx.commit()?;Ok(observed)
        })
    }
}
