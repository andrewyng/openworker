//! Observers drain a durable metadata-only outbox independently of agent slots.
//! A crash after callback delivery but before acknowledgement may repeat the
//! same event ID. Observer effects must be idempotent; they never authorize work.
use super::*;
impl Runtime {
    #[cfg(feature = "guardrails")]
    pub(super) fn start_observers(&self) {
        if self.observers_started.load(Ordering::SeqCst) {
            return;
        }
        // No observer task, timer or callback overhead unless an observer is
        // configured or durable deliveries remain from an earlier turn.
        let configured = {
            let db = self.store.lock().unwrap();
            db.list_metadata().unwrap_or_default().iter().any(|s| {
                s.safety
                    .hooks
                    .iter()
                    .chain(s.turn.iter().flat_map(|t| t.safety.hooks.iter()))
                    .any(|h| h.when == crate::safety::HookPhase::OnEvent)
                    || db.pending_observers(&s.id).is_ok_and(|v| !v.is_empty())
            })
        };
        if !configured || self.observers_started.swap(true, Ordering::SeqCst) {
            return;
        }

        let Ok(handle) = tokio::runtime::Handle::try_current() else {
            self.observers_started.store(false, Ordering::SeqCst);
            return;
        };
        let store = self.store.clone();
        let handlers = self.safety_handlers.clone();
        let stop = self.observers_stop.clone();
        let events = self.events.clone();
        let tool_names = Arc::new(
            self.bundle
                .tools
                .bindings
                .keys()
                .map(|alias| (alias.clone(), self.safety_tool_name(alias)))
                .collect::<BTreeMap<_, _>>(),
        );
        handle.spawn(async move {
            loop {
                if stop.is_cancelled(){break}
                let sessions=store.lock().unwrap().list_metadata().unwrap_or_default();
                let mut batch=tokio::task::JoinSet::new();
                for s in sessions {
                    let pending=store.lock().unwrap().pending_observers(&s.id).unwrap_or_default();
                    for receipt in pending {
                        if receipt["nextMs"].as_u64().unwrap_or(0)>crate::store::now_ms(){continue;}
                        if batch.len()>=8 {tokio::select!{_=stop.cancelled()=>{batch.abort_all();return},_=batch.join_next()=>{}}}
                        let Ok(hook)=serde_json::from_value::<crate::safety::Hook>(receipt["hook"].clone()) else {continue};
                        let sid=s.id.clone();let registry=handlers.clone();let store=store.clone();let cancel=stop.child_token();let events=events.clone();let tool_names=tool_names.clone();
                        batch.spawn(async move {
                            let id=format!("{}:{}",sid,receipt["cursor"]);
                            let mut context=crate::safety::Context{call_id:None,evaluation_id:id.clone(),session_id:sid.clone(),turn_id:receipt["turnId"].as_str().map(String::from),phase:crate::safety::Phase::Event,text:String::new(),tool:receipt["record"]["facts"]["tool"].as_str().or_else(|| receipt["record"]["facts"]["name"].as_str()).map(String::from),arguments:None,matches:vec![],context:json!({"eventId":id,"event":receipt["record"]})};
                            if let Some(tool) = &mut context.tool { if let Some(qualified)=tool_names.get(tool) { *tool=qualified.clone(); } }
                            let selected=hook.tools.is_empty()||context.tool.as_ref().is_some_and(|t|hook.tools.iter().any(|selector| tool_names.get(selector).unwrap_or(selector)==t));
                            let ok=!selected||crate::safety::observe(&hook,&registry,context,cancel).await;
                            let result=store.lock().unwrap().settle_observer(&sid,&receipt,ok);
                            if (!ok||result.is_err())&&receipt["attempts"].as_u64()==Some(0) {
                                // Bounded diagnostic only. Do not re-enqueue itself.
                                if let Ok(event)=store.lock().unwrap().append_event(&sid,"observer_delivery",json!({"hook":hook.name,"code":"OBSERVER_UNAVAILABLE","eventId":id})) {let _=events.send(event);}
                            }
                        });
                    }
                }
                while !batch.is_empty(){tokio::select!{_=stop.cancelled()=>{batch.abort_all();return},_=batch.join_next()=>{}}}
                tokio::select!{_=stop.cancelled()=>break,_=tokio::time::sleep(std::time::Duration::from_secs(1))=>{}}
            }
        });
    }
    #[cfg(not(feature = "guardrails"))]
    pub(super) fn start_observers(&self) {}
}
