//! SDK-managed execution ownership. One host per schema; PostgreSQL durability
//! does not imply that an embedded SDK has enabled distributed scheduling.
use crate::db::Database;
use anyhow::{ensure, Result};
impl Database {
    pub(crate) fn claim_sdk_host(
        &mut self,
        coworkers: &[String],
    ) -> Result<
        Vec<(
            openworker_runtime::store::Session,
            openworker_runtime::team_type_catalog::Record,
        )>,
    > {
        ensure!(
            self.leases.is_empty(),
            "SDK host must claim before starting sessions"
        );
        let locked: bool = self
            .client
            .query_one(
                "SELECT pg_try_advisory_lock(hashtext(current_schema()), 18475001)",
                &[],
            )?
            .get(0);
        ensure!(locked, "Another SDK host owns this PostgreSQL schema");
        let mut tx = self.client.transaction()?;
        let marked: bool = tx
            .query_one("SELECT EXISTS(SELECT 1 FROM sdk_host_mode)", &[])?
            .get(0);
        if !marked {
            let empty: bool = tx
                .query_one("SELECT NOT EXISTS(SELECT 1 FROM sessions)", &[])?
                .get(0);
            ensure!(empty,"Use a fresh PostgreSQL schema for SDK-managed execution; distributed sessions cannot be silently adopted");
            tx.execute("INSERT INTO sdk_host_mode VALUES(TRUE,'sdk')", &[])?;
        }
        let parents = coworkers.iter().cloned().collect();
        let mut restored = vec![];
        for row in tx.query("SELECT data FROM agent_teams WHERE data ? 'type_catalog' AND data->'type_catalog' <> 'null'::jsonb", &[])? {
            let doc: openworker_runtime::agent_team::Document = serde_json::from_value(row.get(0))?;
            let lead = doc.roster.instance(&doc.lead).ok_or_else(|| anyhow::anyhow!("Missing catalog lead"))?;
            let lead: openworker_runtime::store::Session = serde_json::from_value(tx.query_one("SELECT data FROM sessions WHERE id=$1", &[&lead.session_id])?.get(0))?;
            restored.extend(openworker_runtime::team_type_catalog::restore(&doc, &lead, &parents)?);
            for instance in doc.roster.instances().filter(|i| doc.type_bindings.contains_key(&i.agent_id)) {
                if let Some(row) = tx.query_opt("SELECT data FROM sessions WHERE id=$1", &[&instance.session_id])? {
                    let child: openworker_runtime::store::Session = serde_json::from_value(row.get(0))?;
                    ensure!(child.coworker == openworker_runtime::agent_team::runtime_type(&doc, instance)? && child.agent_team.as_ref().is_some_and(|m| m.team_id == doc.id && m.agent_id == instance.agent_id), "Private child lost its approved type pin");
                }
            }
        }
        let mut allowed = coworkers.to_vec();
        allowed.extend(restored.iter().map(|(_, r)| r.runtime_id()));
        let valid:bool=tx.query_one("SELECT NOT EXISTS(SELECT 1 FROM sessions WHERE NOT (data->>'coworker'=ANY($1)) OR data ? 'environment')",&[&allowed])?.get(0);
        ensure!(
            valid,
            "SDK coworker configuration does not cover persisted sessions"
        );
        // Holding this connection-level lock proves any previous SDK database
        // connection has closed. Fence its epochs before recovery, retaining
        // uncertain effects rather than replaying them.
        tx.execute("UPDATE sessions SET expires=clock_timestamp()", &[])?;
        let ids: Vec<String> = tx
            .query("SELECT id FROM sessions ORDER BY id", &[])?
            .into_iter()
            .map(|r| r.get(0))
            .collect();
        tx.commit()?;
        for id in ids {
            self.claim(&id)?;
        }
        Ok(restored)
    }
}
