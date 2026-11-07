use crate::{transport::Backend, *};
use std::collections::BTreeMap;

pub(crate) enum Source {
    Embedded(RuntimeConfig, ToolRegistry),
    Process(ProcessConfig),
    Host(HostConfig, ToolRegistry),
    HostProcess(HostProcessConfig),
}
impl Source {
    fn open(&self) -> Result<Backend> {
        match self {
            Self::Embedded(c, r) => Backend::embedded(c, r.clone()),
            Self::Process(c) => Backend::process(c),
            Self::Host(c, r) => Backend::host(c, r.clone()),
            Self::HostProcess(c) => Backend::host_process(c),
        }
    }
}
pub(crate) async fn start(source: Source, options: ClientOptions) -> Result<HarnessClient> {
    if options.request_timeout.is_zero()
        || options.request_timeout > Duration::from_secs(600)
        || options.shutdown_timeout.is_zero()
        || options.shutdown_timeout > Duration::from_secs(600)
    {
        return Err(Error::InvalidInput(
            "Timeouts must be positive and at most 600 seconds".into(),
        ));
    }
    let (tx, rx) = mpsc::channel(32);
    let (state_tx, state) = watch::channel(ConnectionState::Starting);
    let (done_tx, done) = watch::channel(None);
    let (events, _) = broadcast::channel(256);
    let stop = CancellationToken::new();
    let registrations = Arc::new(std::sync::Mutex::new(Vec::new()));
    let callback_transport = Arc::new(std::sync::Mutex::new(None));
    let client = HarnessClient {
        inner: Arc::new(Handle {
            callback_transport: callback_transport.clone(),
            registrations: registrations.clone(),
            tx,
            stop: stop.clone(),
            state,
            events: events.clone(),
            done,
            info: Default::default(),
        }),
    };
    let (ready_tx, ready_rx) = oneshot::channel();
    tokio::spawn(async move {
        let result = run(
            source,
            options,
            rx,
            &state_tx,
            &events,
            stop,
            ready_tx,
            registrations,
            callback_transport,
        )
        .await;
        state_tx.send_replace(ConnectionState::Closed);
        done_tx.send_replace(Some(result));
    });
    let info = ready_rx
        .await
        .map_err(|_| Error::Setup("Harness startup interrupted".into()))??;
    let _ = client.inner.info.set(info);
    Ok(client)
}
async fn initialize(
    backend: &mut Backend,
    events: &broadcast::Sender<Notification>,
    options: &ClientOptions,
    stop: &CancellationToken,
) -> Result<Value> {
    let info = call(
        backend,
        "initialize",
        json!({"protocol_version":openworker_runtime::PROTOCOL_VERSION}),
        events,
        options,
        stop,
    )
    .await?;
    if info["sdk_version"]
        .as_str()
        .is_some_and(|v| v != env!("CARGO_PKG_VERSION"))
    {
        return Err(Error::Setup(
            "SDK and harness release versions differ".into(),
        ));
    }
    if info["protocol_version"] != openworker_runtime::PROTOCOL_VERSION {
        return Err(Error::Setup("Harness protocol version mismatch".into()));
    }
    Ok(info)
}
async fn call(
    backend: &mut Backend,
    method: &str,
    p: Value,
    events: &broadcast::Sender<Notification>,
    options: &ClientOptions,
    stop: &CancellationToken,
) -> Result<Value> {
    tokio::select! { biased;
        _=stop.cancelled()=>Err(Error::Closed),
        r=tokio::time::timeout(options.request_timeout, backend.request(method,p,events))=>r.unwrap_or(Err(Error::OutcomeUnknown)),
    }
}
async fn attach(
    backend: &mut Backend,
    sid: &str,
    after: i64,
    events: &broadcast::Sender<Notification>,
    options: &ClientOptions,
    stop: &CancellationToken,
) -> Result<Value> {
    let snapshot = call(
        backend,
        "session/attach",
        json!({"session_id":sid}),
        events,
        options,
        stop,
    )
    .await?;
    let target = snapshot["cursor"].as_i64().ok_or(Error::OutcomeUnknown)?;
    if after > target {
        return Err(Error::InvalidInput("Cursor exceeds session journal".into()));
    }
    let mut cursor = after;
    // Bound automatic recovery work. Larger histories remain available via event/read.
    for _ in 0..100 {
        if cursor >= target {
            return Ok(snapshot);
        }
        let page = call(
            backend,
            "event/read",
            json!({"session_id":sid,"after":cursor}),
            events,
            options,
            stop,
        )
        .await?;
        let batch: Vec<Event> =
            serde_json::from_value(page["events"].clone()).map_err(|_| Error::OutcomeUnknown)?;
        let previous = cursor;
        for e in batch {
            if e.event_id > target {
                break;
            }
            cursor = e.event_id;
            let _ = events.send(Notification::Event(e));
        }
        if cursor <= previous {
            return Err(Error::Remote("Journal replay did not advance".into()));
        }
    }
    let _ = events.send(Notification::Gap);
    Err(Error::Remote(
        "Automatic replay limit reached; read journal explicitly".into(),
    ))
}
fn reject(command: Command) {
    match command {
        Command::Request(_, _, tx) | Command::Attach(_, _, tx) => {
            let _ = tx.send(Err(Error::Unavailable));
        }
        Command::Ack(_, _, tx) => {
            let _ = tx.send(Err(Error::Unavailable));
        }
    }
}
#[allow(clippy::too_many_arguments)]
async fn run(
    source: Source,
    options: ClientOptions,
    mut rx: mpsc::Receiver<(u64, Command)>,
    state: &watch::Sender<ConnectionState>,
    events: &broadcast::Sender<Notification>,
    stop: CancellationToken,
    ready: oneshot::Sender<Result<Value>>,
    registrations: Arc<std::sync::Mutex<Vec<Value>>>,
    callback_transport: Arc<std::sync::Mutex<Option<crate::transport::CallbackTransport>>>,
) -> Result<()> {
    let mut backend = match source.open() {
        Ok(b) => b,
        Err(e) => {
            let _ = ready.send(Err(e));
            return Ok(());
        }
    };
    let info = match initialize(&mut backend, events, &options, &stop).await {
        Ok(info) => info,
        Err(e) => {
            let _ = ready.send(Err(e));
            return backend.close(options.shutdown_timeout).await;
        }
    };
    let mut generation = 1;
    *callback_transport.lock().unwrap() = backend.callback_transport();
    state.send_replace(ConnectionState::Ready {
        generation,
        process_id: backend.pid(),
    });
    let _ = ready.send(Ok(info));
    let mut attached = BTreeMap::<String, i64>::new();
    let mut attempts = 0;
    loop {
        let failure = tokio::select! {
            _=stop.cancelled()=>break,
            command=rx.recv()=> {
                let Some((queued_generation,command))=command else {break};
                if queued_generation != generation { reject(command); continue; }
                match command {
                    Command::Request(method,p,tx)=> {
                        let result=call(&mut backend,&method,p,events,&options,&stop).await;
                        let lost=matches!(result,Err(Error::OutcomeUnknown));
                        let _=tx.send(result); lost
                    }
                    Command::Attach(sid,cursor,tx)=> {
                        if attached.len() >= 128 && !attached.contains_key(&sid) {
                            let _=tx.send(Err(Error::InvalidInput("SDK attachment limit reached (128)".into())));continue;
                        }
                        let result=attach(&mut backend,&sid,cursor,events,&options,&stop).await;
                        if result.is_ok() { attached.insert(sid,cursor); }
                        let lost=matches!(result,Err(Error::OutcomeUnknown));
                        let _=tx.send(result); lost
                    }
                    Command::Ack(sid,cursor,tx)=> {
                        let result=match attached.get_mut(&sid) {
                            Some(old) if cursor>=*old=>{
                                match call(&mut backend,"event/read",json!({"session_id":sid,"after":cursor}),events,&options,&stop).await {
                                    Ok(_)=>{*old=cursor;Ok(())},Err(e)=>Err(e),
                                }
                            },
                            _=>Err(Error::InvalidInput("Acknowledge an attached session with a nondecreasing cursor".into())),
                        };
                        let lost=matches!(result,Err(Error::OutcomeUnknown));let _=tx.send(result); lost
                    }
                }
            },
            event=backend.receive()=>match event {
                Ok(Notification::Event(e))=>{if attached.contains_key(&e.session_id) {let _=events.send(Notification::Event(e));} false},
                Ok(e)=>{let _=events.send(e);false},
                Err(_)=>true,
            },
        };
        if stop.is_cancelled() {
            break;
        }
        if !failure {
            continue;
        }
        state.send_replace(ConnectionState::Recovering {
            attempt: attempts + 1,
        });
        while let Ok((_, c)) = rx.try_recv() {
            reject(c);
        }
        // Await owned-process exit before another process can open the same store.
        *callback_transport.lock().unwrap() = None;
        backend.close(options.shutdown_timeout).await?;
        let restart = match &source {
            Source::Process(c) => Some(&c.restart),
            Source::HostProcess(c) => Some(&c.restart),
            _ => None,
        };
        let Some(restart) = restart else {
            state.send_replace(ConnectionState::Failed);
            stop.cancelled().await;
            break;
        };
        let mut restored = false;
        while attempts < restart.max_restarts && !stop.is_cancelled() {
            attempts += 1;
            state.send_replace(ConnectionState::Recovering { attempt: attempts });
            let delay = restart
                .initial_delay
                .saturating_mul(1u32 << (attempts - 1).min(16))
                .min(restart.max_delay);
            tokio::select! { _=stop.cancelled()=>break, _=tokio::time::sleep(delay)=>{} }
            while let Ok((_, c)) = rx.try_recv() {
                reject(c);
            }
            let mut fresh = match source.open() {
                Ok(b) => b,
                Err(_) => continue,
            };
            let init = initialize(&mut fresh, events, &options, &stop).await;
            if init.is_err() {
                fresh.close(options.shutdown_timeout).await?;
                continue;
            }
            let mut transport_failed = false;
            *callback_transport.lock().unwrap() = fresh.callback_transport();
            let bindings = registrations.lock().unwrap().clone();
            for mut binding in bindings {
                let method = binding
                    .as_object_mut()
                    .and_then(|v| v.remove("_method"))
                    .and_then(|v| v.as_str().map(str::to_owned))
                    .unwrap_or_else(|| "application/register".into());
                if call(&mut fresh, &method, binding, events, &options, &stop)
                    .await
                    .is_err()
                {
                    transport_failed = true;
                    break;
                }
            }
            if transport_failed {
                fresh.close(options.shutdown_timeout).await?;
                continue;
            }
            for (sid, cursor) in &attached {
                match attach(&mut fresh, sid, *cursor, events, &options, &stop).await {
                    Ok(snapshot) => {
                        let _ = events.send(Notification::Recovered {
                            session_id: sid.clone(),
                            snapshot,
                        });
                    }
                    Err(Error::OutcomeUnknown | Error::Closed) => {
                        transport_failed = true;
                        break;
                    }
                    Err(e) => {
                        let _ = events.send(Notification::RecoveryFailed {
                            session_id: sid.clone(),
                            message: e.to_string(),
                        });
                    }
                }
            }
            if transport_failed {
                fresh.close(options.shutdown_timeout).await?;
                continue;
            }
            backend = fresh;
            generation += 1;
            state.send_replace(ConnectionState::Ready {
                generation,
                process_id: backend.pid(),
            });
            restored = true;
            break;
        }
        if !restored {
            if !stop.is_cancelled() {
                state.send_replace(ConnectionState::Failed);
                // Keep failure observable until explicit shutdown/last-handle drop.
                tokio::select! { _=stop.cancelled()=>{}, _=async { while let Some((_,c))=rx.recv().await {reject(c);} }=>{} }
            }
            break;
        }
    }
    while let Ok((_, c)) = rx.try_recv() {
        reject(c);
    }
    *callback_transport.lock().unwrap() = None;
    backend.close(options.shutdown_timeout).await
}
