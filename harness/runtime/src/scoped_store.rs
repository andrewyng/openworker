//! A runtime's view of a host-owned store. Recovery belongs to the host once,
//! before any profile starts work. Runtime cleanup only sees its own coworker.
use super::*;
pub struct ScopedStore {
    pub inner: SharedStore,
    pub coworker: String,
}
impl StateStore for ScopedStore {
    fn assistant_call(
        &mut self,
        sid: &str,
        tid: &str,
        call: &Value,
        args: &Value,
    ) -> Result<Event> {
        self.metadata(sid)?;
        self.inner
            .lock()
            .unwrap()
            .assistant_call(sid, tid, call, args)
    }
    fn agent_run(&self, sid: &str, submission: Option<&str>) -> Result<Option<Value>> {
        self.metadata(sid)?;
        self.inner.lock().unwrap().agent_run(sid, submission)
    }
    fn team_create(&mut self, _request: crate::team_service::Create) -> Result<Value> {
        self.inner.lock().unwrap().team_create(_request)
    }
    fn team_snapshot(&self, _id: &str) -> Result<Value> {
        self.inner.lock().unwrap().team_snapshot(_id)
    }
    fn team_command(&mut self, _id: &str, _command: crate::team_service::Command) -> Result<Value> {
        self.inner.lock().unwrap().team_command(_id, _command)
    }
    fn team_events(&self, _id: &str, _after: i64) -> Result<Value> {
        self.inner.lock().unwrap().team_events(_id, _after)
    }
    fn team_tick(&mut self) -> Result<()> {
        self.inner.lock().unwrap().team_tick()
    }
    fn team_next(&self, _sid: &str) -> Result<Option<(String, String)>> {
        self.inner.lock().unwrap().team_next(_sid)
    }
    fn team_cancellations(&self) -> Result<Vec<(String, String)>> {
        self.inner.lock().unwrap().team_cancellations()
    }
    fn team_cancel_settled(&mut self, _sid: &str, _tid: &str) -> Result<()> {
        self.inner.lock().unwrap().team_cancel_settled(_sid, _tid)
    }
    fn supports_teams(&self) -> bool {
        self.inner.lock().unwrap().supports_teams()
    }
    fn team_context(&self, _sid: &str) -> Result<Option<crate::teams::Context>> {
        self.inner.lock().unwrap().team_context(_sid)
    }
    fn team_call(
        &mut self,
        _sid: &str,
        _turn_id: &str,
        _call: &Value,
        _args: &Value,
    ) -> Result<Event> {
        self.inner
            .lock()
            .unwrap()
            .team_call(_sid, _turn_id, _call, _args)
    }
    fn environment(&self, _id: &str) -> Result<crate::environment::Record> {
        self.inner.lock().unwrap().environment(_id)
    }
    fn reserve_attachment(
        &mut self,
        _sid: &str,
        _server: &str,
    ) -> Result<(crate::environment::Attachment, bool)> {
        self.inner.lock().unwrap().reserve_attachment(_sid, _server)
    }
    fn settle_attachment(
        &mut self,
        _sid: &str,
        _id: &str,
        _released: bool,
        _unknown: bool,
    ) -> Result<()> {
        self.inner
            .lock()
            .unwrap()
            .settle_attachment(_sid, _id, _released, _unknown)
    }
    fn abandon_attachments(&mut self, _sid: &str) -> Result<()> {
        self.inner.lock().unwrap().abandon_attachments(_sid)
    }
    fn uncertain_calls(&self, _sid: &str) -> Result<Vec<Value>> {
        self.inner.lock().unwrap().uncertain_calls(_sid)
    }
    fn control(&self, _sid: &str, _key: &str) -> Result<crate::control::Record> {
        self.inner.lock().unwrap().control(_sid, _key)
    }
    fn settle_control(
        &mut self,
        _sid: &str,
        _key: &str,
        _rejected: bool,
        _reason: &str,
    ) -> Result<Event> {
        self.inner
            .lock()
            .unwrap()
            .settle_control(_sid, _key, _rejected, _reason)
    }
    fn get(&self, id: &str) -> Result<Session> {
        let s = self.inner.lock().unwrap().get(id)?;
        anyhow::ensure!(
            s.coworker == self.coworker,
            "Session belongs to another coworker"
        );
        Ok(s)
    }
    fn metadata(&self, id: &str) -> Result<Session> {
        let s = self.inner.lock().unwrap().metadata(id)?;
        anyhow::ensure!(
            s.coworker == self.coworker,
            "Session belongs to another coworker"
        );
        Ok(s)
    }
    fn list(&self) -> Result<Vec<Session>> {
        Ok(self
            .inner
            .lock()
            .unwrap()
            .list()?
            .into_iter()
            .filter(|s| s.coworker == self.coworker)
            .collect())
    }
    fn list_metadata(&self) -> Result<Vec<Session>> {
        Ok(self
            .inner
            .lock()
            .unwrap()
            .list_metadata()?
            .into_iter()
            .filter(|s| s.coworker == self.coworker)
            .collect())
    }
    fn recent_messages(&self, sid: &str) -> Result<(Vec<Value>, bool)> {
        self.inner.lock().unwrap().recent_messages(sid)
    }
    fn save(&mut self, s: &Session, kind: &str, data: Value) -> Result<Event> {
        anyhow::ensure!(
            s.coworker == self.coworker,
            "Session belongs to another coworker"
        );
        self.inner.lock().unwrap().save(s, kind, data)
    }
    fn append_event(&mut self, sid: &str, kind: &str, data: Value) -> Result<Event> {
        self.inner.lock().unwrap().append_event(sid, kind, data)
    }
    fn start_attempt(&mut self, sid: &str, id: &str, number: u32) -> Result<Event> {
        self.inner.lock().unwrap().start_attempt(sid, id, number)
    }
    fn attempt_delta(&mut self, sid: &str, id: &str, text: &str) -> Result<Event> {
        self.inner.lock().unwrap().attempt_delta(sid, id, text)
    }
    fn finish_attempt(
        &mut self,
        sid: &str,
        id: &str,
        status: &str,
        kind: Option<crate::provider::Failure>,
    ) -> Result<Event> {
        self.inner
            .lock()
            .unwrap()
            .finish_attempt(sid, id, status, kind)
    }
    fn interrupt_attempts(&mut self, sid: &str) -> Result<()> {
        self.inner.lock().unwrap().interrupt_attempts(sid)
    }
    fn attempts(&self, sid: &str) -> Result<Vec<Value>> {
        self.inner.lock().unwrap().attempts(sid)
    }
    fn history_page(&self, sid: &str, after: usize) -> Result<Value> {
        self.inner.lock().unwrap().history_page(sid, after)
    }
    fn transcript_excerpt(&self, sid: &str, after: usize, offset: usize) -> Result<Value> {
        self.inner
            .lock()
            .unwrap()
            .transcript_excerpt(sid, after, offset)
    }
    fn accept(
        &mut self,
        sid: &str,
        key: &str,
        text: &str,
    ) -> Result<(Session, bool, Option<Event>)> {
        self.inner.lock().unwrap().accept(sid, key, text)
    }
    fn accept_with_limits(
        &mut self,
        sid: &str,
        key: &str,
        text: &str,
        limits: Option<crate::budget::Limits>,
    ) -> Result<(Session, bool, Option<Event>)> {
        self.inner
            .lock()
            .unwrap()
            .accept_with_limits(sid, key, text, limits)
    }
    fn cursor(&self, sid: &str) -> Result<i64> {
        self.inner.lock().unwrap().cursor(sid)
    }
    fn has_submission(&self, sid: &str, key: &str) -> Result<bool> {
        self.inner.lock().unwrap().has_submission(sid, key)
    }
    fn events(&self, sid: &str, after: i64) -> Result<Vec<Event>> {
        self.inner.lock().unwrap().events(sid, after)
    }
    fn recover(&mut self) -> Result<()> {
        Ok(())
    }
}
