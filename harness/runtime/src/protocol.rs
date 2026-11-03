use crate::runtime::Runtime;
use anyhow::{bail, Result};
use serde_json::{json, Value};
use std::{collections::HashSet, sync::Arc};

pub trait Endpoint: Send + Sync {
    fn application_events(&self) -> Option<tokio::sync::broadcast::Receiver<Value>> {
        None
    }
    fn gaps(&self) -> Option<tokio::sync::broadcast::Receiver<()>> {
        None
    }
    fn closed(&self) -> impl std::future::Future<Output = ()> + Send {
        std::future::pending()
    }
    fn subscribe(&self) -> tokio::sync::broadcast::Receiver<crate::store::Event>;
    fn dispatch(
        &self,
        method: &str,
        params: Value,
    ) -> impl std::future::Future<Output = Result<Value>> + Send;
    fn shutdown(&self) -> impl std::future::Future<Output = Result<()>> + Send;
}
impl Endpoint for Arc<Runtime> {
    fn subscribe(&self) -> tokio::sync::broadcast::Receiver<crate::store::Event> {
        self.events.subscribe()
    }
    async fn dispatch(&self, m: &str, p: Value) -> Result<Value> {
        Runtime::dispatch(self, m, p).await
    }
    async fn shutdown(&self) -> Result<()> {
        Runtime::shutdown(self).await
    }
}
pub async fn serve(rt: Arc<Runtime>) -> Result<()> {
    serve_endpoint(rt).await
}
pub async fn serve_endpoint(rt: impl Endpoint + 'static) -> Result<()> {
    let rt = Arc::new(rt);
    let control = rt.clone();
    let ready = Arc::new(std::sync::atomic::AtomicBool::new(false));
    let control_ready = ready.clone();
    let (tx, mut rx) = tokio::sync::mpsc::channel::<Vec<u8>>(16);
    let input = crate::io::Fd::new(0)?;
    let reader = tokio::spawn(async move {
        let mut buffer = Vec::new();
        loop {
            let bytes = match input.read(65536).await {
                Ok(b) => b,
                Err(_) => break,
            };
            if bytes.is_empty() {
                if !buffer.is_empty() {
                    let _ = tx.send(buffer).await;
                }
                break;
            }
            buffer.extend(bytes);
            while let Some(n) = buffer.iter().position(|&b| b == b'\n') {
                let line = buffer.drain(..=n).collect::<Vec<_>>();
                let too_big = line.len() > crate::MAX_FRAME;
                // Reserved callback notifications have their own control lane.
                // A normal run wait/registration cannot block an app's result.
                if !too_big && control_ready.load(std::sync::atomic::Ordering::Acquire) {
                    if let Ok(v) = serde_json::from_slice::<Value>(&line) {
                        if v["jsonrpc"] == "2.0" && v.get("id").is_none() && v["params"].is_object()
                        {
                            if let Some(
                                method @ ("application/result"
                                | "application/progress"
                                | "provider/auth/result"
                                | "safety/result"),
                            ) = v["method"].as_str()
                            {
                                let _ =
                                    Box::pin(control.dispatch(method, v["params"].clone())).await;
                                continue;
                            }
                        }
                    }
                }
                if tx.send(line).await.is_err() || too_big {
                    return;
                }
            }
            if buffer.len() > crate::MAX_FRAME {
                let _ = tx.send(buffer).await;
                break;
            }
        }
    });
    let mut events = rt.subscribe();
    let mut gaps = rt.gaps();
    let mut application = rt.application_events();
    let mut subscribed = HashSet::<String>::new();
    let mut initialized = false;
    let mut stdout = crate::io::Fd::new(1)?;
    // A desktop child may have no console (CREATE_NO_WINDOW on Windows).
    // Failure to install Ctrl-C is not a shutdown request; pipe ownership and
    // explicit shutdown still govern its lifecycle.
    let interrupt = async {
        if tokio::signal::ctrl_c().await.is_err() {
            std::future::pending::<()>().await;
        }
    };
    tokio::pin!(interrupt);
    loop {
        let frame = tokio::select! {
            event=async {match application.as_mut(){Some(rx)=>rx.recv().await,None=>std::future::pending().await}}=>match event {
                Ok(value)=>json!({"jsonrpc":"2.0","method":"application/event","params":value}),
                Err(_)=>break, // Callback delivery is not replayable; fail the connection.
            },
            _=rt.closed()=>break,
            _=async {match gaps.as_mut(){Some(rx)=>{let _=rx.recv().await;},None=>std::future::pending().await}}=>json!({"jsonrpc":"2.0","method":"stream/gap","params":{"reason":"Replay by cursor"}}),
            _=&mut interrupt=>break,
            input=rx.recv()=>{
                let Some(raw)=input else{break;};
                if raw.len()>crate::MAX_FRAME{write(&mut stdout,json!({"jsonrpc":"2.0","id":null,"error":{"code":-32600,"message":"Frame exceeds 1 MiB"}})).await?;break;}
                let request=serde_json::from_slice::<Value>(&raw);
                match request{
                    Err(_)=>json!({"jsonrpc":"2.0","id":null,"error":{"code":-32700,"message":"Invalid JSON"}}),
                    Ok(v)=>{
                        let id=v.get("id").cloned().unwrap_or(Value::Null);let method=v["method"].as_str().unwrap_or("");
                        let p=v.get("params").cloned().unwrap_or(json!({}));
                        let result=async {
                            if v["jsonrpc"]!="2.0"||!(id.is_string()||id.is_i64()||id.is_u64())||method.is_empty()||!p.is_object(){bail!("Invalid JSON-RPC request");}
                            if method=="initialize"{if initialized{bail!("Already initialized");}}else if !initialized{bail!("Initialize first");}
                            // Keep the routing future off the small native Windows
                            // main-thread stack (especially in debug desktop builds).
                            let result=dispatch_with_callbacks(rt.as_ref(),method,p.clone(),&mut application,&mut stdout).await?;
                            if method=="initialize"{initialized=true;ready.store(true,std::sync::atomic::Ordering::Release);}
                            if method=="session/attach"{subscribed.insert(p["session_id"].as_str().unwrap().to_owned());}
                            Ok::<_,anyhow::Error>(result)
                        }.await;
                        match result{Ok(result)=>json!({"jsonrpc":"2.0","id":id,"result":result}),Err(e)=>json!({"jsonrpc":"2.0","id":id,"error":{"code":-32000,"message":e.to_string()}})}
                    }
                }
            },
            e=events.recv()=>match e{
                Ok(e) if subscribed.contains(&e.session_id)=>json!({"jsonrpc":"2.0","method":"session/event","params":e}),
                Ok(_)=>continue,
                Err(tokio::sync::broadcast::error::RecvError::Lagged(_))=>json!({"jsonrpc":"2.0","method":"stream/gap","params":{"reason":"Reattach for snapshot, then replay by cursor"}}),
                Err(_)=>break,
            }
        };
        // The journal, not this pipe, is authoritative. A blocked reader cannot
        // block execution forever. On timeout the process exits; caller reattaches.
        if !matches!(
            tokio::time::timeout(
                std::time::Duration::from_secs(10),
                write(&mut stdout, frame),
            )
            .await,
            Ok(Ok(()))
        ) {
            break;
        }
    }
    reader.abort();
    rt.shutdown().await?;
    Ok(())
}
async fn write(out: &mut crate::io::Fd, v: Value) -> Result<()> {
    out.write_all(format!("{v}\n").as_bytes()).await?;
    Ok(())
}

async fn dispatch_with_callbacks(
    rt: &impl Endpoint,
    method: &str,
    params: Value,
    application: &mut Option<tokio::sync::broadcast::Receiver<Value>>,
    output: &mut crate::io::Fd,
) -> Result<Value> {
    let mut request = Box::pin(rt.dispatch(method, params));
    loop {
        tokio::select! {
            result=&mut request=>return result,
            event=async{match application.as_mut(){Some(rx)=>rx.recv().await,None=>std::future::pending().await}}=>{
                let event=event.map_err(|_|anyhow::anyhow!("Application callback delivery was lost"))?;
                tokio::time::timeout(std::time::Duration::from_secs(10),write(output,json!({"jsonrpc":"2.0","method":"application/event","params":event}))).await.map_err(|_|anyhow::anyhow!("Application callback pipe blocked"))??;
            }
        }
    }
}
