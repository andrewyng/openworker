//! Lead-side independent inspection checkout using the qualified import outbox.
use crate::{
    agent_team::Tx,
    auth, db,
    service_team_imports::{Intent, Job},
    PostgresStore,
};
use anyhow::{ensure, Context, Result};
use openworker_runtime::{
    agent_team::{Document, Transaction},
    coding_handoff::{config::Choice, transfer::Transfer, Import},
    store::Session,
    team_workspace::Receipt,
};
use serde_json::json;

pub(crate) fn authority(
    tx: &mut postgres::Transaction<'_>,
    lead: &Session,
    d: &Document,
    i: &Intent,
) -> Result<()> {
    let id = i.reception.as_ref().context("Not a reception")?;
    ensure!(
        d.id == i.team
            && i.lead == lead.id
            && d.roster
                .instance(&d.lead)
                .is_some_and(|a| a.session_id == lead.id)
            && d.coding_selection
            && !d.stopped,
        "Original receiving lead unavailable"
    );
    let (_, profile) = crate::agent_team::lead_authority(tx, lead, d)?;
    let p = crate::service_catalog::entry(tx, "profile", &profile.id)?;
    ensure!(
        i.environment
            == lead
                .environment
                .clone()
                .context("Missing original receiving environment")?
            && i.root == lead.workspace
            && p.id == i.profile_id
            && p.digest == i.profile_digest,
        "Original reception placement changed"
    );
    let env: openworker_runtime::environment::Record = serde_json::from_value(
        tx.query_one(
            "SELECT data FROM environments WHERE id=$1 FOR SHARE",
            &[&i.environment.environment_id],
        )?
        .get(0),
    )?;
    ensure!(
        env.phase == openworker_runtime::environment::Phase::Ready
            && env.binding.as_ref() == Some(&i.environment),
        "Original receiving environment unavailable"
    );
    let reception = d.receptions.get(id).context("Original reception removed")?;
    ensure!(
        reception.receipt.is_none()
            && reception.error.is_none()
            && reception.workspace == i.workspace
            && i.choice == (Choice::Submission { id: id.clone() })
            && i.agent == format!("receive_{id}"),
        "Original reception changed"
    );
    let (manifest, bundle, artifact) =
        crate::service_team_submissions::ready_snapshot(tx, lead, d, id)?;
    ensure!(
        reception.manifest_digest == manifest.digest()?
            && i.transfer.manifest == manifest
            && i.transfer.bundle == bundle
            && i.artifact == artifact,
        "Original receiving artifact changed"
    );
    i.transfer.validate()?;
    ensure!(
        i.transfer.request.operation_id == i.workspace.operation_id
            && i.transfer.request.scope_digest == i.workspace.scope_digest
            && i.workspace.selection
                == (openworker_runtime::team_workspace::Selection::Directory {
                    path: i.transfer.request.directory.clone()
                }),
        "Reception import binding changed"
    );
    Ok(())
}
pub(crate) fn complete(
    tx: &mut postgres::Transaction<'_>,
    lead: &Session,
    d: &Document,
    i: &Intent,
    receipt: &Receipt,
) -> Result<()> {
    authority(tx, lead, d, i)?;
    receipt.validate_scope(std::path::Path::new(&i.root), &receipt.path)?;
    ensure!(
        receipt.request == i.workspace
            && receipt.independent_git.is_some()
            && receipt.initial_commit.as_ref() == Some(&i.transfer.manifest.submitted_commit),
        "Reception is not the exact independent submission"
    );
    let mut d = d.clone();
    d.receptions
        .get_mut(i.reception.as_ref().unwrap())
        .context("Missing original reception")?
        .receipt = Some(receipt.clone());
    d.receptions
        .get_mut(i.reception.as_ref().unwrap())
        .context("Missing original reception")?
        .uncertain = false;
    Tx(tx).write(&d, false)?;
    Ok(())
}
impl PostgresStore {
    pub fn claim_team_reception(&self, installation: &str) -> Result<Option<Job>> {
        ensure!(self.auth.is_none(), auth::Error::Forbidden);
        let installation = installation.to_owned();
        self.call(move|db| {
            let mut tx=db.client.transaction()?;
            let candidate=tx.query_opt("SELECT l.id,t.id,r.key FROM agent_teams t JOIN sessions l ON l.id=t.data->'roster'->'instances'->(t.data->>'lead')->>'session_id' JOIN service_operations o ON o.session_id=l.id JOIN service_catalog c ON c.kind='release' AND c.id=o.release_id CROSS JOIN LATERAL jsonb_each(coalesce(t.data->'receptions','{}'::jsonb)) r LEFT JOIN service_team_imports q ON q.team=t.id AND q.agent='receive_'||r.key WHERE c.data->>'installation'=$1 AND NOT coalesce((t.data->>'stopped')::boolean,false) AND r.value->'receipt'='null'::jsonb AND r.value->'error'='null'::jsonb AND (q.team IS NULL OR (q.status='pending' AND q.next_check<=clock_timestamp())) ORDER BY t.id,r.key LIMIT 1 FOR UPDATE OF l SKIP LOCKED",&[&installation])?;
            let Some(c)=candidate else {tx.commit()?;return Ok(None)};
            let lead=db::locked(&mut tx,&c.get::<_,String>(0),None)?;let team:String=c.get(1);let id:String=c.get(2);let agent=format!("receive_{id}");
            let mut d=Tx(&mut tx).load(&team)?;
            if d.stopped {tx.commit()?;return Ok(None)}
            let reception=d.receptions.get(&id).context("Original reception missing")?;
            if reception.receipt.is_some() || reception.error.is_some() {tx.commit()?;return Ok(None)}
            if crate::service_admin::current(&mut tx)?.configuration.admissions_paused {tx.commit()?;return Ok(None)}
            let row=tx.query_opt("SELECT data,prepare_dispatched,checkout_dispatched,next_check<=clock_timestamp(),status FROM service_team_imports WHERE team=$1 AND agent=$2 FOR UPDATE",&[&team,&agent])?;
            if row.as_ref().is_some_and(|r|!r.get::<_,bool>(3)||r.get::<_,String>(4)!="pending") {tx.commit()?;return Ok(None)}
            let prepare=row.as_ref().is_none_or(|r|!r.get::<_,bool>(1));let checkout_dispatched=row.as_ref().is_some_and(|r|r.get::<_,bool>(2));
            let intent:Intent=if let Some(row)=row {serde_json::from_value(row.get(0))?} else {
                let prepared=(|| {
                    let (_,p)=crate::agent_team::lead_authority(&mut tx,&lead,&d)?;
                    let p=crate::service_catalog::entry(&mut tx,"profile",&p.id)?;
                    let (manifest,bundle,artifact)=crate::service_team_submissions::ready_snapshot(&mut tx,&lead,&d,&id)?;
                    let workspace=reception.workspace.clone();
                    let openworker_runtime::team_workspace::Selection::Directory{path}= &workspace.selection else {anyhow::bail!("Reception needs an independent directory")};
                    let transfer=Transfer{request:Import{operation_id:workspace.operation_id.clone(),scope_digest:workspace.scope_digest.clone(),manifest_digest:manifest.digest()?,directory:path.clone()},manifest,bundle};transfer.validate()?;
                    let i=Intent{reception:Some(id.clone()),team:team.clone(),agent:agent.clone(),lead:lead.id.clone(),choice:Choice::Submission{id:id.clone()},environment:lead.environment.clone().context("Missing lead environment")?,root:lead.workspace.clone(),profile_id:p.id,profile_digest:p.digest,workspace,transfer,artifact};authority(&mut tx,&lead,&d,&i)?;Ok::<_,anyhow::Error>(i)
                })();
                let i=match prepared {Ok(i)=>i,Err(e) if e.downcast_ref::<postgres::Error>().is_some()=>return Err(e),Err(_)=>{
                    d.receptions.get_mut(&id).context("Missing reception")?.error=Some("Original receiving authority unavailable before dispatch".into());Tx(&mut tx).write(&d,false)?;
                    crate::team_execution::session_event(&mut tx,&lead.id,None,"team_reception",json!({"submission_id":id,"phase":"blocked_before_dispatch"}))?;tx.commit()?;return Ok(None)
                }};
                tx.execute("INSERT INTO service_team_imports(team,agent,data) VALUES($1,$2,$3)",&[&team,&agent,&json!(i)])?;i
            };
            if let Err(e)=authority(&mut tx,&lead,&d,&intent) {
                if e.downcast_ref::<postgres::Error>().is_some(){return Err(e)}
                tx.execute("UPDATE service_team_imports SET status='blocked',claim=NULL WHERE team=$1 AND agent=$2",&[&team,&agent])?;
                d.receptions.get_mut(&id).context("Missing reception")?.error=Some("Original receiving authority unavailable; work retained".into());Tx(&mut tx).write(&d,false)?;tx.commit()?;return Ok(None)
            }
            let claim=uuid::Uuid::new_v4().to_string();
            tx.execute("UPDATE service_team_imports SET prepare_dispatched=TRUE,claim=$3,next_check=clock_timestamp()+interval '180 seconds' WHERE team=$1 AND agent=$2",&[&team,&agent,&claim])?;
            crate::team_execution::session_event(&mut tx,&lead.id,None,"team_reception",json!({"submission_id":id,"phase":"preparing","dispatch":prepare,"checkout_dispatched":checkout_dispatched}))?;
            tx.commit()?;Ok(Some(Job{intent,claim,prepare,checkout_dispatched}))
        })
    }
}
