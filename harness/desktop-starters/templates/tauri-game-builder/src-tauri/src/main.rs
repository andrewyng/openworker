#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]
#[cfg(not(any(windows, target_os = "macos")))]
compile_error!("This preview template targets Windows/macOS; do not enable untrusted iframes on platforms without frame-origin IPC isolation.");
use futures_util::StreamExt;
use openworker_sdk::{
    agents::{InterruptionKind, RunResult, RunStatus},
    Agent, AgentHandle, ApprovalDecision, Credentials, Harness, Model, ModelProvider, Sandbox,
    Session, SessionInput, Tool,
};
use serde_json::{json, Value};
use std::{
    path::PathBuf,
    sync::{
        atomic::{AtomicBool, AtomicU64, Ordering},
        Arc, Mutex as SyncMutex,
    },
};
use tauri::{Emitter, Manager};
use tokio::sync::Mutex;
mod recovery;

struct AppState {
    harness: Harness,
    agent: AgentHandle,
    session: Mutex<Option<Session>>,
    busy: AtomicBool,
    closing: AtomicBool,
    reference: PathBuf,
    preview_html: Arc<SyncMutex<String>>,
    preview_version: Arc<AtomicU64>,
}
async fn refresh_project(app: &tauri::AppHandle) -> Result<(), String> {
    let state = app.state::<AppState>();
    let session = state.session.lock().await.clone();
    let preferences = recovery::load(&state.reference.with_file_name("model-settings.json"))
        .map_err(|_| "Cannot read model settings")?;
    let view = recovery::recover(&state.harness, session.as_ref(), preferences)
        .await
        .map_err(|_| "Cannot restore project history; preserve it and inspect before retrying")?;
    let html = view["html"].as_str().unwrap_or_default().to_owned();
    {
        let mut current = state.preview_html.lock().unwrap();
        if *current != html {
            *current = html.clone();
            state.preview_version.fetch_add(1, Ordering::SeqCst);
        }
    }
    let origin = if cfg!(windows) {
        "http://arcade-preview.localhost"
    } else {
        "arcade-preview://localhost"
    };
    emit(
        app,
        json!({"type":"project","messages":view["messages"],"settings":view["settings"],"url":if html.is_empty(){String::new()}else{format!("{origin}/index.html?v={}",state.preview_version.load(Ordering::SeqCst))}}),
    );
    Ok(())
}
fn emit(app: &tauri::AppHandle, value: Value) {
    let _ = app.emit_to("main", "agent-event", value);
}
fn result_value(result: RunResult) -> Value {
    let status = match result.status {
        RunStatus::Completed => "completed",
        RunStatus::Interrupted => "interrupted",
        RunStatus::Cancelled => "cancelled",
        RunStatus::Failed(_) => "failed",
    };
    let interruptions: Vec<_> = result
        .interruptions
        .into_iter()
        .map(|i| {
            let mut v = json!({"id":i.id,"session_id":i.session_id});
            match i.kind {
                InterruptionKind::Approval { tool, .. } => {
                    v["kind"] = json!("approval");
                    v["tool"] = json!(tool);
                }
                InterruptionKind::Question { question, .. } => {
                    v["kind"] = json!("question");
                    v["question"] = json!(question);
                }
            }
            v
        })
        .collect();
    json!({"status":status,"finalOutput":result.final_output,"interruptions":interruptions})
}
fn stream(app: tauri::AppHandle, run: openworker_sdk::Run) {
    tauri::async_runtime::spawn(async move {
        let outcome = async {
            let mut events = run.events();
            while let Some(event) = events.next().await {
                event?;
                emit(
                    &app,
                    json!({"type":"activity","message":"Building your idea…"}),
                );
            }
            run.result().await
        }
        .await;
        app.state::<AppState>().busy.store(false, Ordering::SeqCst);
        match outcome {
            Ok(result) => {
                if let Err(message) = refresh_project(&app).await {
                    emit(&app, json!({"type":"error","message":message}));
                } else {
                    emit(&app, json!({"type":"result","result":result_value(result)}));
                }
            }
            Err(_) => emit(
                &app,
                json!({"type":"error","message":"The run could not finish. Check settings and inspect the saved session before retrying."}),
            ),
        }
    });
}
fn text<'a>(v: &'a Value, key: &str) -> Result<&'a str, String> {
    v[key].as_str().ok_or_else(|| format!("Missing {key}"))
}
#[tauri::command]
async fn action(
    app: tauri::AppHandle,
    state: tauri::State<'_, AppState>,
    input: Value,
) -> Result<Value, String> {
    let error = |_: openworker_sdk::Error| {
        "Harness rejected the action. Check settings or the saved session.".to_owned()
    };
    match text(&input, "type")? {
        "restore" => {
            refresh_project(&app).await?;
            let session = state.session.lock().await.clone();
            if let Some(session) = session {
                if !session.state().await.map_err(error)?["run_id"].is_null()
                    && !state.busy.swap(true, Ordering::SeqCst)
                {
                    tauri::async_runtime::spawn(async move {
                        let result = session.resume().await;
                        app.state::<AppState>().busy.store(false, Ordering::SeqCst);
                        match result {
                            Ok(result) => {
                                if let Err(message) = refresh_project(&app).await {
                                    emit(&app, json!({"type":"error","message":message}));
                                    return;
                                }
                                emit(&app, json!({"type":"result","result":result_value(result)}))
                            }
                            Err(_) => emit(
                                &app,
                                json!({"type":"error","message":"Could not resume the saved session. Inspect it before retrying."}),
                            ),
                        }
                    });
                }
            }
            Ok(json!({"attached":true}))
        }
        "provider" => {
            let p = &input["provider"];
            let name = text(p, "name")?;
            let mut provider = match text(p, "api")? {
                "responses" => ModelProvider::responses(name),
                "chat-completions" => ModelProvider::chat_completions(name),
                "anthropic" => ModelProvider::anthropic(name),
                _ => return Err("Unknown API".into()),
            }
            .base_url(text(p, "baseUrl")?);
            if let Some(key) = p["credentials"]["apiKey"].as_str() {
                provider = provider.credentials(Credentials::secure(key));
            }
            state
                .harness
                .set_model_provider(provider)
                .await
                .map_err(error)?;
            recovery::save(&state.reference.with_file_name("model-settings.json"), &json!({"provider":name,"api":p["api"],"baseUrl":p["baseUrl"],"model":input["model"].as_str().unwrap_or_default()})).map_err(|_|"Cannot save model preferences")?;
            Ok(json!({"saved":true}))
        }
        "send" => {
            if state.busy.swap(true, Ordering::SeqCst) {
                return Err("Wait for the current run".into());
            }
            let outcome = async {
                let mut current = state.session.lock().await;
                if current.is_none() {
                    let model = &input["model"];
                    let session = state
                        .agent
                        .new_session_with(
                            openworker_sdk::SessionOptions::default()
                                .model(Model::new(text(model, "provider")?, text(model, "name")?)),
                        )
                        .await
                        .map_err(error)?;
                    recovery::save(
                        &state.reference,
                        &serde_json::to_value(session.reference())
                            .map_err(|_| "Invalid reference")?,
                    )
                    .map_err(|_| "Cannot save project reference")?;
                    *current = Some(session);
                }
                current
                    .as_ref()
                    .unwrap()
                    .send(text(&input, "text")?)
                    .await
                    .map_err(error)
            }
            .await;
            match outcome {
                Ok(run) => {
                    stream(app, run);
                    Ok(json!({"accepted":true}))
                }
                Err(e) => {
                    state.busy.store(false, Ordering::SeqCst);
                    Err(e)
                }
            }
        }
        "respond" => {
            if state.busy.swap(true, Ordering::SeqCst) {
                return Err("Wait for current operation".into());
            }
            let outcome = async {
                let current = state.session.lock().await;
                let session = current.as_ref().ok_or("No session")?;
                let i = &input["interaction"];
                let interaction_id = text(i, "id")?.into();
                let target_session_id = Some(text(i, "session_id")?.into());
                let response = if i["kind"] == "approval" {
                    SessionInput::Approval {
                        interaction_id,
                        target_session_id,
                        decision: match text(&input, "decision")? {
                            "once" => ApprovalDecision::Once,
                            "session" => ApprovalDecision::Session,
                            "deny" => ApprovalDecision::Deny,
                            _ => return Err("Invalid approval".into()),
                        },
                    }
                } else {
                    SessionInput::Answer {
                        interaction_id,
                        target_session_id,
                        text: text(&input, "answer")?.into(),
                    }
                };
                session.send(response).await.map_err(error)
            }
            .await;
            match outcome {
                Ok(run) => {
                    stream(app, run);
                    Ok(json!({"accepted":true}))
                }
                Err(e) => {
                    state.busy.store(false, Ordering::SeqCst);
                    Err(e)
                }
            }
        }
        "cancel" => {
            if let Some(session) = state.session.lock().await.as_ref() {
                session.cancel().await.map_err(error)?;
            }
            Ok(json!({"cancelled":true}))
        }
        "new" => {
            if state.busy.load(Ordering::SeqCst) {
                return Err("Stop the current run first".into());
            }
            let mut session = state.session.lock().await;
            if let Some(current) = session.as_ref() {
                current.cancel().await.map_err(error)?;
            }
            *session = None;
            recovery::save(&state.reference, &Value::Null)
                .map_err(|_| "Cannot save new project")?;
            drop(session);
            refresh_project(&app).await?;
            Ok(json!({"ready":true}))
        }
        _ => Err("Unsupported app action".into()),
    }
}
fn main() {
    let preview_html = Arc::new(SyncMutex::new(String::new()));
    let state_preview = preview_html.clone();
    let route_html = preview_html.clone();
    let app=tauri::Builder::default().register_uri_scheme_protocol("arcade-preview",move|_,request|{
        if request.uri().path()!="/index.html" {return tauri::http::Response::builder().status(404).body(Vec::new()).unwrap();}
        tauri::http::Response::builder().header("Content-Type","text/html; charset=utf-8").header("Cache-Control","no-store").header("X-Content-Type-Options","nosniff")
            .header("Content-Security-Policy","default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; media-src data: blob:; connect-src 'none'; frame-src 'none'; form-action 'none'; base-uri 'none'")
            .body(route_html.lock().unwrap().as_bytes().to_vec()).unwrap()
    }).invoke_handler(tauri::generate_handler![action]).setup(move|app|{
        let handle=app.handle().clone();
        let state=tauri::async_runtime::block_on(async {
            let config:Value=serde_json::from_str(include_str!("../../app.json"))?;
            let home=std::env::var_os(if cfg!(windows){"USERPROFILE"}else{"HOME"}).ok_or("User directory unavailable")?;
            let mut builder=Harness::builder().state_dir(PathBuf::from(home).join(".openworker").join(config["appId"].as_str().ok_or("Invalid app ID")?).join("state")).sandbox(Sandbox::Off);
            if cfg!(debug_assertions){builder=builder.executable(PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../bin").join(if cfg!(windows){"openworker-harness.exe"}else{"openworker-harness"}));}
            let harness=builder.start().await?;
            let preview=handle.clone();let version=Arc::new(AtomicU64::new(0));let state_version=version.clone();
            harness.register_tools("game",[Tool::new("show_preview").description("Display a self-contained playable HTML game in the isolated preview.").input_schema(json!({"type":"object","properties":{"html":{"type":"string","maxLength":100000}},"required":["html"],"additionalProperties":false})).handler(move|args,context|{let preview=preview.clone();let html=preview_html.clone();let version=version.clone();async move{
                if context.cancellation().is_cancelled(){return Err(std::io::Error::other("Cancelled").into());}
                *html.lock().unwrap()=args["html"].as_str().ok_or_else(||std::io::Error::other("Expected HTML"))?.to_owned();
                let origin=if cfg!(windows){"http://arcade-preview.localhost"}else{"arcade-preview://localhost"};
                emit(&preview,json!({"type":"preview","url":format!("{origin}/index.html?v={}",version.fetch_add(1,Ordering::SeqCst)+1)}));Ok(json!({"shown":true}))
            }})]).await?;
            let agent=harness.create_agent(Agent::new("arcade-builder-v1").instructions("Build playful, polished self-contained HTML/CSS/JavaScript games. Call game.show_preview with complete HTML. No external assets or network calls. Iterate on the same game when asked; do not claim it was shown before the tool succeeds.").tool("game.show_preview")).await?;
            let reference=handle.path().app_data_dir()?.join("session.json");
            let saved=recovery::load(&reference)?;
            let session=if saved.is_null(){None}else{Some(agent.resume_session(serde_json::from_value(saved)?).await?)};
            Ok::<_,Box<dyn std::error::Error>>(AppState{harness,agent,session:Mutex::new(session),busy:AtomicBool::new(false),closing:AtomicBool::new(false),reference,preview_html:state_preview,preview_version:state_version})
        })?;app.manage(state);Ok(())
    }).build(tauri::generate_context!()).expect("Could not start Arcade");
    app.run(|handle, event| {
        if let tauri::RunEvent::ExitRequested { api, .. } = event {
            let state = handle.state::<AppState>();
            if !state.closing.swap(true, Ordering::SeqCst) {
                api.prevent_exit();
                let harness = state.harness.clone();
                let handle = handle.clone();
                tauri::async_runtime::spawn(async move {
                    let _ = harness.shutdown().await;
                    handle.exit(0);
                });
            }
        }
    });
}
