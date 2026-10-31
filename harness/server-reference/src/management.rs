//! Service management never runs tools in the API. Definitions select from the
//! operator-installed bundle; controller and existing workers own all execution.
use crate::{
    http::{ApiError, App},
    Config,
};
use anyhow::{ensure, Context, Result};
use axum::{
    extract::Path,
    http::{HeaderMap, StatusCode},
    Extension, Json,
};
use openworker_runtime::store::StateStore;
use openworker_runtime::{
    bundle::{digest, Bundle},
    environment::{Management, Provider},
    runtime::Runtime,
};
use openworker_store_postgres::{
    service_catalog::{Admission, Entry, Profile, Release},
    PostgresStore, SubmissionError,
};
use serde::Deserialize;
use serde_json::{json, Value};
use std::sync::Arc;

pub fn installation(config: &Config) -> Result<String> {
    let base = Bundle::load(&config.bundle)?;
    Ok(digest(&serde_json::to_vec(
        &json!({"bundle":base.digest,"runner":digest(&std::fs::read(&config.runner)?),"gateway":config.openshell.endpoint,"gatewayIdentity":config.openshell.identity,"workspace":config.openshell.workspace,"image":config.openshell.image}),
    )?))
}
pub fn provider(
    config: &Config,
    profile: &Entry,
) -> Result<Arc<openworker_runtime::environment_openshell::OpenShell>> {
    ensure!(
        profile.installation == installation(config)?,
        "Installed service bundle/provider changed; new release required"
    );
    let p: Profile =
        serde_json::from_value(profile.definition.clone()).map_err(anyhow::Error::from)?;
    p.validate()?;
    let mut c = config.clone();
    c.openshell.cpu = Some(p.cpu);
    c.openshell.memory_mb = Some(p.memory_mb);
    c.provider()
}
fn copy_tree(from: &std::path::Path, to: &std::path::Path) -> Result<()> {
    std::fs::create_dir_all(to)?;
    for e in std::fs::read_dir(from)? {
        let e = e?;
        let ty = e.file_type()?;
        ensure!(!ty.is_symlink(), "Installed skill symlink rejected");
        if ty.is_dir() {
            copy_tree(&e.path(), &to.join(e.file_name()))?;
        } else {
            std::fs::copy(e.path(), to.join(e.file_name()))?;
        }
    }
    Ok(())
}
/// Content-addressed installed bundle cache. Generated files contain no secrets.
pub fn release_config(config: &Config, release: &Entry, profile: &Entry) -> Result<Config> {
    ensure!(
        release.installation == installation(config)?
            && release.installation == profile.installation,
        "Release installation changed"
    );
    let r: Release =
        serde_json::from_value(release.definition.clone()).map_err(anyhow::Error::from)?;
    r.validate()?;
    let p: Profile =
        serde_json::from_value(profile.definition.clone()).map_err(anyhow::Error::from)?;
    p.validate()?;
    ensure!(
        r.runtime_profiles.contains(&p.id),
        "Release/profile incompatible"
    );
    let base = Bundle::load(&config.bundle)?;
    let mut manifest = base.manifest.clone();
    manifest.id = format!("svc-{}", &release.digest[..24]);
    manifest.name = r.id.clone();
    manifest.version = release.digest.clone();
    manifest.default_model = r.default_model;
    manifest.limits = r.limits;
    manifest.memory_scope = None;
    let mut tools = base.tools.clone();
    ensure!(
        r.tools.iter().all(|id| tools.bindings.contains_key(id)),
        "Tool is not in the installed catalog"
    );
    tools.bindings.retain(|id, _| r.tools.contains(id));
    tools
        .servers
        .retain(|id, _| tools.bindings.values().any(|b| b.server == *id));
    let manifest_text = format!(
        "---\n{}\n---\n{}\n",
        serde_json::to_string(&manifest)?,
        r.instructions
    );
    let tools_text = serde_json::to_string(&tools)?;
    let root = config.state_root.join("service-bundles");
    std::fs::create_dir_all(&root)?;
    let dir = root.join(&release.digest);
    if !dir.exists() {
        let tmp = root.join(openworker_runtime::id("prepare"));
        std::fs::create_dir_all(tmp.join("coworker"))?;
        std::fs::write(tmp.join("coworker/manifest.md"), &manifest_text)?;
        std::fs::write(tmp.join("tools.json"), &tools_text)?;
        if base.root.join("coworker/skills").exists() {
            copy_tree(
                &base.root.join("coworker/skills"),
                &tmp.join("coworker/skills"),
            )?;
        }
        if let Err(e) = std::fs::rename(&tmp, &dir) {
            std::fs::remove_dir_all(&tmp)?;
            ensure!(dir.exists(), "Cannot publish generated bundle: {e}");
        }
    }
    ensure!(
        !std::fs::symlink_metadata(&dir)?.file_type().is_symlink(),
        "Bundle cache symlink rejected"
    );
    ensure!(
        std::fs::read_to_string(dir.join("coworker/manifest.md"))? == manifest_text
            && std::fs::read_to_string(dir.join("tools.json"))? == tools_text,
        "Installed release cache changed"
    );
    let mut c = config.clone();
    c.bundle = dir;
    c.openshell.cpu = Some(p.cpu);
    c.openshell.memory_mb = Some(p.memory_mb);
    Ok(c)
}
pub(crate) async fn put_profile(
    Extension(app): Extension<App>,
    headers: HeaderMap,
    Json(p): Json<Profile>,
) -> Result<Json<Value>, ApiError> {
    p.validate()?;
    Ok(Json(json!(app.store.catalog_put(
        &crate::service::key(&app, &headers, "profile")?,
        "profile",
        json!(p),
        &installation(&app.config)?
    )?)))
}
pub(crate) async fn put_release(
    Extension(app): Extension<App>,
    headers: HeaderMap,
    Json(r): Json<Release>,
) -> Result<Json<Value>, ApiError> {
    r.validate()?;
    if app.service.as_ref().is_some_and(|s| s.agent == r.id) {
        return Err(ApiError(SubmissionError::Conflict.into()));
    }
    let base = Bundle::load(&app.config.bundle)?;
    if !r.tools.iter().all(|t| base.tools.bindings.contains_key(t)) {
        return Err(ApiError(SubmissionError::InvalidRequest.into()));
    }
    for m in r
        .default_model
        .iter()
        .chain(r.reviewer_model.iter())
        .chain(&r.model_overrides)
    {
        m.validate()?;
        app.runtime.connections.resolve(m)?;
    }
    for p in &r.runtime_profiles {
        app.store.catalog_get("profile", p)?;
    }
    Ok(Json(json!(app.store.catalog_put(
        &crate::service::key(&app, &headers, "release")?,
        "release",
        json!(r),
        &installation(&app.config)?
    )?)))
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Activate {
    pub alias: Option<String>,
}
pub(crate) async fn activate_profile(
    Extension(app): Extension<App>,
    Path(id): Path<String>,
    headers: HeaderMap,
    Json(a): Json<Activate>,
) -> Result<Json<Value>, ApiError> {
    Ok(Json(json!(app.store.catalog_activate(
        &crate::service::key(&app, &headers, "activate-profile")?,
        "profile",
        &id,
        a.alias
    )?)))
}
pub(crate) async fn activate_release(
    Extension(app): Extension<App>,
    Path(id): Path<String>,
    headers: HeaderMap,
    Json(a): Json<Activate>,
) -> Result<Json<Value>, ApiError> {
    if a.alias
        .as_ref()
        .is_some_and(|alias| app.service.as_ref().is_some_and(|s| s.agent == *alias))
    {
        return Err(ApiError(SubmissionError::Conflict.into()));
    }
    Ok(Json(json!(app.store.catalog_activate(
        &crate::service::key(&app, &headers, "activate-release")?,
        "release",
        &id,
        a.alias
    )?)))
}
pub(crate) async fn get_profile(
    Extension(app): Extension<App>,
    Path(id): Path<String>,
) -> Result<Json<Value>, ApiError> {
    Ok(Json(json!(app.store.catalog_get("profile", &id)?)))
}
pub(crate) async fn get_environment(
    Extension(app): Extension<App>,
    Path(id): Path<String>,
) -> Result<Json<Value>, ApiError> {
    Ok(Json(app.store.service_environment(&id)?))
}
pub(crate) async fn get_operation(
    Extension(app): Extension<App>,
    Path(id): Path<String>,
) -> Result<Json<Value>, ApiError> {
    Ok(Json(json!(app.store.service_operation(&id)?)))
}
pub(crate) async fn admit(
    app: App,
    headers: HeaderMap,
    input: Admission,
) -> Result<(StatusCode, Json<Value>), ApiError> {
    admit_kind(app, headers, input, "session").await
}
pub(crate) async fn admit_kind(
    app: App,
    headers: HeaderMap,
    input: Admission,
    kind: &str,
) -> Result<(StatusCode, Json<Value>), ApiError> {
    let release = app.store.catalog_get("release", &input.agent)?;
    let r: Release =
        serde_json::from_value(release.definition.clone()).map_err(anyhow::Error::from)?;
    let implicit_default = input.runtime_profile.is_none()
        && input.environment_id.is_none()
        && r.default_runtime_profile.is_none();
    let profile_id = if let Some(env) = &input.environment_id {
        app.store.service_environment(env)?["runtimeProfile"]
            .as_str()
            .context("Environment profile missing")?
            .to_owned()
    } else {
        input
            .runtime_profile
            .clone()
            .or(r.default_runtime_profile)
            .unwrap_or_else(|| "default".into())
    };
    let profile = match app.store.catalog_get("profile", &profile_id) {
        Ok(p) => p,
        Err(e)
            if implicit_default
                && profile_id == "default"
                && e.downcast_ref::<SubmissionError>() == Some(&SubmissionError::NotFound) =>
        {
            let default = app
                .service
                .as_ref()
                .and_then(|s| s.default_runtime_profile.as_ref())
                .ok_or_else(|| ApiError(SubmissionError::InvalidRequest.into()))?;
            app.store.catalog_get("profile", default)?
        }
        Err(e) => return Err(e.into()),
    };
    let p: Profile =
        serde_json::from_value(profile.definition.clone()).map_err(anyhow::Error::from)?;
    let spec = provider(&app.config, &profile)?.spec(p.sharing, Management::Harness);
    let op = app.store.service_admit(
        &crate::service::key(&app, &headers, kind)?,
        input,
        release,
        profile,
        spec,
    )?;
    Ok((
        if op.status == "complete" {
            StatusCode::OK
        } else {
            StatusCode::ACCEPTED
        },
        Json(json!({"operation":op})),
    ))
}
/// Private controller. A claim permits one create only; all subsequent visits
/// inspect the original remote name/UID and never issue another create.
pub async fn control_once(config: &Config, store: &PostgresStore) -> Result<bool> {
    control_matching(config, store, &installation(config)?).await
}
async fn control_matching(config: &Config, store: &PostgresStore, installed: &str) -> Result<bool> {
    let Some((op, create)) = store.service_claim_for(Some(installed))? else {
        return Ok(false);
    };
    let work:Result<()>=async {
        let rel=store.catalog_get("release",&op.release_id)?;let profile=store.catalog_get("profile",&op.profile_id)?;
        let provider=provider(config,&profile)?;let mut env=store.clone().environment(&op.environment_id)?;
        if op.status!="ready"{
            let result=if create {provider.create(env.clone()).await}else{
                let raw=env.id.strip_prefix("env_").context("Bad environment ID")?;
                provider.bind_existing(&env,&format!("owr-{}",&raw[..15])).await
            };
            match result {Ok(binding)=>{env=store.complete_environment(&env.id,env.revision,Some(binding))?;store.service_update_operation(&op.id,"ready",None)?;},Err(_)=>{store.complete_environment(&env.id,env.revision,None)?;store.service_update_operation(&op.id,"unknown",Some("Provisioning outcome unknown; inspecting original environment only".into()))?;return Ok(());}}
        }
        provider.inspect(env.binding.clone().context("Environment not ready")?).await?;
        let c=release_config(config,&rel,&profile)?;let rt=c.api_runtime(&format!("admit-{}",&op.id[3..19]),store.clone())?;
        let input=store.service_input(&op.id)?;let r:Release=serde_json::from_value(rel.definition.clone())?;let p:Profile=serde_json::from_value(profile.definition.clone())?;
        let mut parameters=json!({"coworker":rt.coworker(),"workspace":"/sandbox","environment_id":env.id,"name":input.name.unwrap_or(op.release_id),"permissions":{"mode":p.approval_mode,"auto_skip_questions":p.auto_skip_questions}});
        if let Some(model)=input.model{parameters["model"]=json!(model);}
        if let Some(model)=r.reviewer_model{parameters["reviewer_model"]=json!(model);}
        let configured=store.service_operation_configuration(&op.id)?;parameters["compaction"]=json!(configured.configuration.compaction);
        let session=rt.prepare_session(parameters)?;
        let service=config.service.as_ref().context("Service config required")?;
        let result=store.service_finish(&op.id,session,&service.id,&service.namespace);rt.shutdown().await?;result?;Ok(())
    }.await;
    if let Err(error) = work {
        if error
            .downcast_ref::<openworker_store_postgres::auth::Error>()
            .is_some()
        {
            store.service_update_operation(
                &op.id,
                "failed",
                Some("Admission authorization no longer valid".into()),
            )?;
        } else {
            return Err(error);
        }
    }
    Ok(true)
}
pub async fn controller(
    config: Config,
    store: PostgresStore,
    stop: tokio_util::sync::CancellationToken,
) -> Result<()> {
    config.require_execution()?;
    let service = config
        .service
        .as_ref()
        .context("Service configuration required")?;
    service.validate()?;
    store.bind_service(&service.id, &service.namespace)?;
    let installed = installation(&config)?;
    while !stop.is_cancelled() {
        delete_once(&config, &store, &installed).await?;
        store.provision_agent_team_child()?;
        store.reconcile_stopped_agent_team()?;
        store.reconcile_unstarted_retirement()?;
        if !control_matching(&config, &store, &installed).await? {
            tokio::select! {_=stop.cancelled()=>break,_=tokio::time::sleep(std::time::Duration::from_millis(500))=>{}}
        }
    }
    Ok(())
}

pub fn selector(
    config: &Config,
    rt: &Arc<Runtime>,
) -> Result<openworker_store_postgres::worker::Selector> {
    let env = config.provider()?.spec(
        openworker_runtime::environment::Sharing::Exclusive,
        Management::Harness,
    );
    Ok(openworker_store_postgres::worker::Selector {
        coworker: rt.coworker().into(),
        bundle_digest: rt.execution_digest().into(),
        backend: openworker_runtime::execution::Backend::Openshell,
        scripted: config.scripted,
        fallback_model: config.model.clone(),
        model_provider: config.model_provider,
        model_connections: config
            .model_connections
            .iter()
            .map(|c| {
                Ok((
                    c.id.clone(),
                    rt.connections
                        .resolve(&openworker_runtime::connections::Choice::new(
                            &c.id,
                            "validation",
                        ))?
                        .identity,
                ))
            })
            .collect::<Result<_>>()?,
        environment_provider: Some(env.provider),
        environment_identity: Some(env.provider_identity),
        environment_profile: Some(env.profile_digest),
    })
}
pub async fn worker(
    config: Config,
    store: PostgresStore,
    slots: usize,
    stop: tokio_util::sync::CancellationToken,
) -> Result<()> {
    worker_with_options(
        config,
        store,
        slots,
        stop,
        tokio_util::sync::CancellationToken::new(),
        "service-worker".into(),
        None,
        300,
    )
    .await
}
#[allow(clippy::too_many_arguments)] // Worker resource and shutdown ownership are explicit.
pub async fn worker_with_options(
    config: Config,
    store: PostgresStore,
    slots: usize,
    stop: tokio_util::sync::CancellationToken,
    abort: tokio_util::sync::CancellationToken,
    node: String,
    health_listen: Option<std::net::SocketAddr>,
    drain_seconds: u64,
) -> Result<()> {
    use openworker_store_postgres::worker::{Catalog, Factory, Options, Worker};
    let _abort_guard = abort.clone().drop_guard();
    config.require_execution()?;
    anyhow::ensure!(
        health_listen.is_none_or(|a| a.ip().is_loopback()),
        "Worker health must bind loopback"
    );
    anyhow::ensure!(
        openworker_runtime::bundle::safe_name(&node),
        "Invalid worker node name"
    );
    let provider = config.provider()?;
    provider.check_gateway().await?;
    let drain = openworker_store_postgres::worker::Drain::default();
    let health = crate::readiness::Readiness::new(drain.clone());
    let health_stop = tokio_util::sync::CancellationToken::new();
    let _health_guard = health_stop.clone().drop_guard();
    let health_server = if let Some(address) = health_listen {
        let listener = tokio::net::TcpListener::bind(address).await?;
        let router = health.router();
        let done = health_stop.clone();
        Some(tokio::spawn(async move {
            axum::serve(listener, router)
                .with_graceful_shutdown(done.cancelled_owned())
                .await
        }))
    } else {
        None
    };
    let signal_stop = stop.clone();
    let signal_abort = abort.clone();
    let signal_drain = drain.clone();
    let signal = tokio::spawn(async move {
        tokio::select! {_=signal_stop.cancelled()=>signal_drain.request(),_=signal_abort.cancelled()=>{}}
    });

    let service = config
        .service
        .as_ref()
        .context("Service configuration required")?;
    service.validate()?;
    store.bind_service(&service.id, &service.namespace)?;
    let base = config.runtime("service-worker-base", store.clone())?;
    let base_selector = selector(&config, &base)?;
    base.shutdown().await?;
    let cache = Arc::new(std::sync::Mutex::new(std::collections::BTreeMap::<
        String,
        (openworker_store_postgres::worker::Selector, Factory),
    >::new()));
    let cached = cache.clone();
    let catalog: Catalog = Arc::new(move || Ok(cached.lock().unwrap().values().cloned().collect()));
    // Slot connections must retain the process-owned instrumentation. Rebuilding
    // only database credentials here silently disabled model/tool/turn recording
    // for catalog-deployed agents, while the monitor still emitted storage spans.
    let mut db = config.database()?;
    db.telemetry = store.telemetry();
    let default_config = config.clone();
    let default_factory: Factory = Arc::new(move |sid, s| {
        default_config.runtime(&format!("svc-{}", &digest(sid.as_bytes())[..16]), s)
    });
    cache.lock().unwrap().insert(
        "legacy".into(),
        (base_selector.clone(), default_factory.clone()),
    );
    let work = Worker::new(
        db,
        base_selector.clone(),
        Options {
            slots,
            ..Default::default()
        },
        default_factory,
    )?
    .with_catalog(catalog)
    .run_with_drain(
        drain.clone(),
        abort.clone(),
        std::time::Duration::from_secs(drain_seconds),
    );
    let installed = installation(&config)?;
    let monitor = async {
        while !abort.is_cancelled() {
            provider.check_gateway().await?;
            store.advertise_worker(
                &node,
                base_selector.clone(),
                slots as u16,
                drain.is_requested(),
            )?;
            health.checked();
            let entries = store.catalog_list()?;
            for release in entries
                .iter()
                .filter(|e| e.kind == "release" && e.activated && e.installation == installed)
            {
                let r: Release = serde_json::from_value(release.definition.clone())
                    .map_err(anyhow::Error::from)?;
                for pid in r.runtime_profiles {
                    let key = format!("{}:{pid}", release.digest);
                    if cache.lock().unwrap().contains_key(&key) {
                        continue;
                    }
                    let profile = entries
                        .iter()
                        .find(|p| p.kind == "profile" && p.id == pid && p.activated)
                        .context("Active profile missing")?;
                    let c = release_config(&config, release, profile)?;
                    let rt = c.runtime(
                        &format!("template-{}", &digest(key.as_bytes())[..16]),
                        store.clone(),
                    )?;
                    let select = selector(&c, &rt)?;
                    rt.shutdown().await?;
                    let factory: Factory = Arc::new(move |sid, s| {
                        c.runtime(&format!("svc-{}", &digest(sid.as_bytes())[..16]), s)
                    });
                    cache.lock().unwrap().insert(key, (select, factory));
                }
            }
            tokio::select! {_=abort.cancelled()=>break,_=tokio::time::sleep(std::time::Duration::from_secs(1))=>{}}
        }
        Ok::<_, anyhow::Error>(())
    };
    tokio::pin!(work);
    tokio::pin!(monitor);
    let result = tokio::select! {result=&mut work=>result,result=&mut monitor=>{health.clear();abort.cancel();let joined=work.await;result.and(joined)}};
    abort.cancel();
    signal.abort();
    health.clear();
    health_stop.cancel();
    if let Some(server) = health_server {
        let _ = server.await;
    }
    result
}

/// The API records intent only; gateway credentials stay on private controllers.
pub(crate) async fn delete_environment(
    Extension(app): Extension<App>,
    Path(id): Path<String>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    app.store.service_environment(&id)?;
    let key = crate::service::key(&app, &headers, &format!("delete-environment:{id}"))?;
    let operation = &key[..32];
    let (record, _) = app
        .store
        .begin_environment_delete(&id, operation, &app.actor)?;
    if record.phase != openworker_runtime::environment::Phase::Deleted {
        app.store.service_queue_delete(&id, operation)?;
    }
    Ok(Json(json!({"id":id,"phase":record.phase})))
}
async fn delete_once(config: &Config, store: &PostgresStore, installed: &str) -> Result<()> {
    let Some((record, key, profile, fresh)) = store.service_claim_delete(installed)? else {
        return Ok(());
    };
    let provider = provider(config, &store.catalog_get("profile", &profile)?)?;
    let binding = record.binding.context("Environment binding missing")?;
    // A lost connection is not retry permission. Only the first claim can delete;
    // after that, read the original object's state and never delete a replacement.
    let deleted = if fresh {
        provider.delete(binding, key.clone()).await.is_ok()
    } else {
        provider.confirm_deleted(binding).await.unwrap_or(false)
    };
    if deleted {
        store.complete_environment_delete(&record.id, &key, record.revision)?;
    } else {
        store.service_recheck_delete(&record.id)?;
    }
    Ok(())
}

pub(crate) async fn get_release(
    Extension(app): Extension<App>,
    Path(id): Path<String>,
) -> Result<Json<Value>, ApiError> {
    Ok(Json(json!(app.store.catalog_get("release", &id)?)))
}
