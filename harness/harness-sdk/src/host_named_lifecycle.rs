use super::*;
use openworker_runtime::{
    agent_team::Document, store::Session, team_coordination::Phase, team_lifecycle::Mode,
};
impl Host {
    fn record_retirement_pending(
        &self,
        lead: &Session,
        doc: &Document,
        id: &str,
        reason: &str,
    ) -> Result<()> {
        if doc.retirements[id].pending_reason.as_deref() != Some(reason) {
            self.store.lock().unwrap().agent_team(
                &lead.id,
                "retirement_error",
                &json!({"agent_id":id,"reason":reason}),
                None,
            )?;
        }
        Ok(())
    }
    pub(super) async fn reconcile_named_retirements(&self, sessions: &[Session]) -> Result<()> {
        for lead in sessions.iter().filter(|s| s.agent_team.is_some()) {
            let doc: Document = serde_json::from_value(self.store.lock().unwrap().agent_team(
                &lead.id,
                "snapshot",
                &json!({}),
                None,
            )?)?;
            if doc
                .roster
                .instance(&doc.lead)
                .is_none_or(|i| i.session_id != lead.id)
            {
                continue;
            }
            for i in doc
                .roster
                .instances()
                .filter(|i| i.phase == Phase::Retiring)
            {
                if let Some(child) = sessions.iter().find(|s| s.id == i.session_id) {
                    // Saved API controls retain priority over lifecycle work, even
                    // though retirement has closed normal turn admission.
                    if let Some(db) = &self.postgres {
                        if db.next_control(&child.id)?.is_some() {
                            continue;
                        }
                    }
                    let runtime = self.runtime(&child.coworker)?;
                    let active = child.turn.as_ref().is_some_and(|t| !terminal(&t.status));
                    if active {
                        if doc.retirements[&i.agent_id].mode == Mode::Cancel {
                            let turn = child.turn.as_ref().unwrap();
                            if (turn.status != "cancelling" || !runtime.is_busy(&child.id))
                                && runtime
                                    .dispatch(
                                        "turn/cancel",
                                        json!({"session_id":child.id,"turn_id":turn.id}),
                                    )
                                    .await
                                    .is_err()
                            {
                                self.record_retirement_pending(
                                    lead,
                                    &doc,
                                    &i.agent_id,
                                    "cancel_cleanup_not_confirmed",
                                )?;
                            }
                        } else if !runtime.is_busy(&child.id)
                            && child
                                .turn
                                .as_ref()
                                .is_some_and(|t| t.status == "resuming_agents")
                        {
                            runtime
                                .dispatch("team/resume", json!({"session_id":child.id}))
                                .await?;
                        } else if !runtime.is_busy(&child.id)
                            && child
                                .agent_team
                                .as_ref()
                                .is_some_and(|m| m.waiting.is_some())
                        {
                            let wake = self.store.lock().unwrap().agent_team(
                                &child.id,
                                "wake",
                                &json!({}),
                                None,
                            )?;
                            if wake["resume"] == true {
                                runtime
                                    .dispatch("team/resume", json!({"session_id":child.id}))
                                    .await?;
                            }
                        }
                        continue;
                    }
                    if runtime.is_busy(&child.id) {
                        continue;
                    }
                    // Retain retirement/capacity on an unknown cleanup outcome.
                    if !child.closed
                        && runtime
                            .dispatch("session/close", json!({"session_id":child.id}))
                            .await
                            .is_err()
                    {
                        self.record_retirement_pending(
                            lead,
                            &doc,
                            &i.agent_id,
                            "cleanup_not_confirmed",
                        )?;
                        continue;
                    }
                }
                self.store.lock().unwrap().agent_team(
                    &lead.id,
                    "retirement_settle",
                    &json!({"agent_id":i.agent_id}),
                    None,
                )?;
            }
        }
        Ok(())
    }
}
