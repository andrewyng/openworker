use super::*;
impl Runtime {
    pub(super) fn internal_observation(
        &self,
        sid: &str,
        turn: &str,
        name: &str,
        call: Option<&str>,
    ) -> crate::telemetry::Span {
        let mut span = self.telemetry.span(Op::Tool, Some(sid), call);
        span.turn(turn);
        span.metadata("gen_ai.operation.name", "execute_tool");
        span.metadata("gen_ai.tool.name", name);
        span.metadata("langfuse.observation.type", "tool");
        span.metadata("openworker.sandbox.provider", "not-applicable");
        span.metadata("openworker.sandbox.enforcement", "session-metadata-only");
        span
    }
    pub(super) fn begin_segment(&self, sid: &str) -> Result<(crate::telemetry::Span, String)> {
        let mut db = self.store.lock().unwrap();
        // save() enforces the immutable history prefix; never pass a metadata-only
        // projection to a canonical checkpoint (SQLite and PostgreSQL alike).
        let mut session = if self.telemetry.enabled() {
            db.get(sid)?
        } else {
            db.metadata(sid)?
        };
        let turn = session.turn.as_mut().context("No current turn")?;
        let tid = turn.id.clone();
        let mut span = self.telemetry.span(Op::Turn, Some(sid), Some(&tid));
        span.turn(&tid);
        if self.telemetry.enabled() {
            let old = turn.observation.as_ref();
            if let Some(old) = old {
                span.link(old.last_segment);
            }
            let now = crate::store::now_ms();
            turn.observation = Some(crate::store::TurnObservation {
                first_started_at_ms: old.map_or(now, |o| o.first_started_at_ms),
                last_segment: span.context(),
                segments: old
                    .map_or(0, |o| o.segments)
                    .checked_add(1)
                    .context("Segment count overflow")?,
            });
            let data = json!({"observed_at_ms":now,"observation":turn.observation});
            let event = db.save(&session, "execution_segment_started", data)?;
            drop(db);
            self.publish(event);
        }
        Ok((span, tid))
    }
    pub(super) fn record_wait(&self, sid: &str, interaction: &Interaction, outcome: Outcome) {
        if let (Some(start), Some(end)) = (interaction.requested_at_ms, interaction.resolved_at_ms)
        {
            self.telemetry.interval(
                if interaction.kind == "approval" {
                    Op::ApprovalWait
                } else {
                    Op::QuestionWait
                },
                outcome,
                sid,
                &interaction.turn_id,
                &interaction.id,
                start,
                end,
            );
        }
    }
}
