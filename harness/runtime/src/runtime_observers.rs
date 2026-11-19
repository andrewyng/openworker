//! Observers drain a durable metadata-only outbox independently of agent slots.
//! A crash after callback delivery but before acknowledgement may repeat the
//! same event ID. Observer effects must be idempotent; they never authorize work.
use super::*;
impl Runtime {
    pub(super) fn start_observers(&self) {
        if self.observers_started.load(Ordering::SeqCst) {
            return;
        }
        // No observer task, timer or callback overhead unless an observer is
        // configured or durable deliveries remain from an earlier turn.
        let configured = {
            let db = self.store.lock().unwrap();
            db.list_metadata().unwrap_or_default().iter().any(|s| {
                !s.integrations.observers.is_empty()
                    || s.safety
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
        let connections = self.connections.clone();
        let integrations = self.integrations.clone();
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
                if stop.is_cancelled() {break;}
                let sessions=store.lock().unwrap().list_metadata().unwrap_or_default();
                let mut batch=tokio::task::JoinSet::new();
                for s in sessions {
                    let pending=store.lock().unwrap().pending_observers(&s.id).unwrap_or_default();
                    // Independent observers can run concurrently. Keep each integration's
                    // transitions in journal order within each session and batch.
                    let mut deliveries = Vec::new();
                    let mut groups: BTreeMap<String, Vec<Value>> = BTreeMap::new();
                    for receipt in pending {
                        if let Some(id) = receipt["hook"]["integration"].as_str() {
                            groups.entry(id.to_owned()).or_default().push(receipt);
                        } else if receipt["nextMs"].as_u64().unwrap_or(0) <= crate::store::now_ms() {
                            deliveries.push(vec![receipt]);
                        }
                    }
                    // Each group's receipts arrive in cursor order from the outbox.
                    deliveries.extend(groups.into_values());
                    for group in deliveries {
                        if batch.len() >= 8 {
                            tokio::select! {
                                _ = stop.cancelled() => {batch.abort_all();return}
                                _ = batch.join_next() => {}
                            }
                        }
                        let manager=connections.clone();
                        let model=s.model_selection.clone();
                        let integrations=integrations.clone();
                        let admission=s.integrations.clone();
                        let sid=s.id.clone();
                        let registry=handlers.clone();
                        let store=store.clone();
                        let cancel=stop.child_token();
                        let events=events.clone();
                        let tool_names=tool_names.clone();
                        batch.spawn(async move {
                            for receipt in group {
                                if receipt["hook"]["integration"].is_string() {
                                    let result = tokio::time::timeout(
                                        std::time::Duration::from_secs(3),
                                        integrations.deliver_with_model(&admission,&sid,&receipt,model.as_ref().map(|s|(manager.as_ref(),s))),
                                    ).await;
                                    let mut db = store.lock().unwrap();
                                    if !result.is_ok_and(|delivery|delivery.is_ok()) {
                                        let _=db.append_event(&sid,"observer_delivery",json!({
                                            "code":"INTEGRATION_OBSERVER_UNAVAILABLE",
                                            "integration":receipt["hook"]["integration"],
                                            "cursor":receipt["cursor"]
                                        }));
                                    }
                                    // An uncertain advisory delivery should not replay over newer work.
                                    if db.settle_observer(&sid,&receipt,true).is_err(){break;}
                                    continue;
                                }
                            let Ok(hook)=serde_json::from_value::<crate::safety::Hook>(receipt["hook"].clone()) else {break};
                            let id=format!("{}:{}",sid,receipt["cursor"]);
                            let mut context=crate::safety::Context{call_id:None,evaluation_id:id.clone(),session_id:sid.clone(),turn_id:receipt["turnId"].as_str().map(String::from),phase:crate::safety::Phase::Event,text:String::new(),tool:receipt["record"]["facts"]["tool"].as_str().or_else(|| receipt["record"]["facts"]["name"].as_str()).map(String::from),arguments:None,matches:vec![],context:json!({"eventId":id,"event":receipt["record"]})};
                            if let Some(tool) = &mut context.tool { if let Some(qualified)=tool_names.get(tool) { *tool=qualified.clone(); } }
                            let selected=hook.tools.is_empty()||context.tool.as_ref().is_some_and(|t|hook.tools.iter().any(|selector| tool_names.get(selector).unwrap_or(selector)==t));
                            #[cfg(feature = "guardrails")]
                            let ok=!selected||crate::safety::observe(&hook,&registry,context,cancel.clone()).await;
                            #[cfg(not(feature = "guardrails"))]
                            let ok = { let _ = (&registry, context, &cancel); !selected };
                            let result=store.lock().unwrap().settle_observer(&sid,&receipt,ok);
                            if (!ok||result.is_err())&&receipt["attempts"].as_u64()==Some(0) {
                                // Bounded diagnostic only. Do not re-enqueue itself.
                                if let Ok(event)=store.lock().unwrap().append_event(&sid,"observer_delivery",json!({"hook":hook.name,"code":"OBSERVER_UNAVAILABLE","eventId":id})) {let _=events.send(event);}
                            }
                            if !ok || result.is_err() {break;}
                            }
                        });
                    }
                }
                while !batch.is_empty(){tokio::select!{_=stop.cancelled()=>{batch.abort_all();return},_=batch.join_next()=>{}}}
                tokio::select!{_=stop.cancelled()=>break,_=tokio::time::sleep(std::time::Duration::from_secs(1))=>{}}
            }
        });
    }
}
