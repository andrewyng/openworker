//! Authentication belongs to execution storage, never the recording database.
use crate::http::{ApiError, App};
use axum::{
    extract::{Path, Query},
    routing::get,
    Extension, Json, Router,
};
use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine};
use openworker_observability::{
    postgres::Postgres,
    query::{Query as Read, Scope},
    summary::{self, Canonical},
    Store,
};
use openworker_store_postgres::{
    auth::Action,
    run_query::{self, Run},
};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

#[derive(Debug)]
pub(crate) enum Error {
    Invalid,
    Unavailable,
    Busy,
}
impl std::fmt::Display for Error {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "Recording query failed")
    }
}
impl std::error::Error for Error {}
pub(crate) fn authority(method: &str, path: &str) -> bool {
    if method != "GET" {
        return false;
    }
    let path = path
        .strip_prefix("/v1/")
        .map(|p| format!("/{p}"))
        .unwrap_or_else(|| path.into());
    matches!(
        path.split('/').collect::<Vec<_>>().as_slice(),
        ["", "observability", "runs" | "summary" | "health"]
            | [
                "",
                "sessions",
                _,
                "observability",
                _,
                "timeline" | "summary"
            ]
    )
}
pub(crate) fn action(method: &str, path: &str) -> Option<Action> {
    authority(method, path).then(|| {
        if matches!(path, "/observability/health" | "/v1/observability/health") {
            Action::Admin
        } else {
            Action::Read
        }
    })
}
pub(crate) fn router(prefix: &str) -> Router<App> {
    Router::new()
        .route(&format!("{prefix}/observability/health"), get(health))
        .route(&format!("{prefix}/observability/runs"), get(search))
        .route(&format!("{prefix}/observability/summary"), get(grouped))
        .route(
            &format!("{prefix}/sessions/:sid/observability/:turn/timeline"),
            get(timeline),
        )
        .route(
            &format!("{prefix}/sessions/:sid/observability/:turn/summary"),
            get(summary),
        )
}
async fn health(Extension(app): Extension<App>) -> Result<Json<Value>, ApiError> {
    // These counters cover the serving API process, not an authorized run or
    // the cluster. Never expose other principals' traffic through a read token.
    app.store.authorize(Action::Admin, None, None)?;
    Ok(Json(json!({
        "scope":"api_instance", "recording_enabled":app.config.observability.is_some(),
        "recording":app.config.recording_stats.as_ref().map(|s|s.snapshot()),
        "exporter_enabled":app.runtime.telemetry.enabled(),
        "exporter":app.runtime.telemetry.snapshot()
    })))
}
#[derive(Clone, Default, Deserialize)]
#[serde(deny_unknown_fields)]
struct Search {
    q: Option<String>,
    agent: Option<String>,
    from_ms: Option<i64>,
    to_ms: Option<i64>,
    limit: Option<u16>,
    after: Option<String>,
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Cursor {
    version: u8,
    binding: String,
    as_of_us: i64,
    after: run_query::Cursor,
}
fn run_search(app: &App, q: &Search) -> Result<Value, ApiError> {
    let identity = app.store.authorize(Action::Read, None, None)?;
    let binding = openworker_runtime::bundle::digest(
        json!([
            app.config.schema,
            app.service.as_ref().map(|s| (&s.id, &s.namespace)),
            identity.credential_id,
            q.q,
            q.agent,
            q.from_ms,
            q.to_ms
        ])
        .to_string()
        .as_bytes(),
    );
    let cursor = q
        .after
        .as_ref()
        .map(|s| {
            if s.len() > 1024 {
                return Err(Error::Invalid);
            }
            let bytes = URL_SAFE_NO_PAD.decode(s).map_err(|_| Error::Invalid)?;
            let cursor: Cursor = serde_json::from_slice(&bytes).map_err(|_| Error::Invalid)?;
            if cursor.version != 1 || cursor.binding != binding {
                return Err(Error::Invalid);
            }
            Ok(cursor)
        })
        .transpose()
        .map_err(|e| ApiError(e.into()))?;
    let page = app.store.search_runs(run_query::Search {
        text: q.q.clone().unwrap_or_default(),
        agent: q.agent.clone(),
        from_ms: q.from_ms,
        to_ms: q.to_ms,
        after: cursor.as_ref().map(|c| c.after.clone()),
        as_of_us: cursor.as_ref().map(|c| c.as_of_us),
        limit: q.limit.unwrap_or(25),
    })?;
    let next = page.next.map(|after| {
        URL_SAFE_NO_PAD.encode(
            serde_json::to_vec(&Cursor {
                version: 1,
                binding,
                as_of_us: page.as_of_us,
                after,
            })
            .expect("typed cursor"),
        )
    });
    Ok(json!({"runs":page.runs,"next_cursor":next,"as_of_us":page.as_of_us}))
}
async fn search(
    Extension(app): Extension<App>,
    Query(q): Query<Search>,
) -> Result<Json<Value>, ApiError> {
    Ok(Json(run_search(&app, &q)?))
}
fn scope(sid: &str, turn: &str) -> Scope {
    Scope {
        session_hash: openworker_runtime::bundle::digest(sid.as_bytes()),
        turn_hash: openworker_runtime::bundle::digest(turn.as_bytes()),
    }
}
fn canonical(run: &Run) -> Canonical {
    Canonical {
        terminal: openworker_runtime::store::terminal(&run.status),
        expected_segments: run.observation.as_ref().map(|o| o.segments),
        first_started_ms: run.observation.as_ref().map(|o| o.first_started_at_ms),
        last_span_id: run.observation.as_ref().map(|o| {
            o.last_segment
                .span
                .iter()
                .map(|b| format!("{b:02x}"))
                .collect()
        }),
        pending_waits: Some(run.pending_waits),
    }
}
async fn read(app: &App, q: Read) -> Result<openworker_observability::query::Page, ApiError> {
    q.validate().map_err(|_| ApiError(Error::Invalid.into()))?;
    let options = app
        .config
        .observability
        .as_ref()
        .ok_or_else(|| ApiError(Error::Unavailable.into()))?;
    let config = options
        .database(&app.config)
        .map_err(|_| ApiError(Error::Unavailable.into()))?;
    let permit = app
        .recording_readers
        .clone()
        .try_acquire_owned()
        .map_err(|_| ApiError(Error::Busy.into()))?;
    // The permit stays with the blocking job even if the HTTP client disconnects.
    tokio::task::spawn_blocking(move || {
        let _permit = permit;
        Postgres::open(&config)?.timeline(&q)
    })
    .await
    .map_err(|_| ApiError(Error::Unavailable.into()))?
    .map_err(|_| ApiError(Error::Unavailable.into()))
}
#[derive(Default, Deserialize)]
#[serde(deny_unknown_fields)]
struct Page {
    after: Option<u64>,
    through: Option<u64>,
    limit: Option<u16>,
}
async fn timeline(
    Extension(app): Extension<App>,
    Path((sid, turn)): Path<(String, String)>,
    Query(q): Query<Page>,
) -> Result<Json<Value>, ApiError> {
    app.store.observation_run(&sid, &turn)?; // BEFORE configuration/recording access.
    let limit = q.limit.unwrap_or(100);
    if !(1..=200).contains(&limit) {
        return Err(ApiError(Error::Invalid.into()));
    }
    let page = read(
        &app,
        Read {
            deployment: app.config.schema.clone(),
            scopes: vec![scope(&sid, &turn)],
            after: q.after.unwrap_or(0),
            through: q.through,
            limit,
        },
    )
    .await?;
    let run = app.store.observation_run(&sid, &turn)?; // Revocation/scope recheck after read.
    Ok(Json(
        json!({"run":run,"page":page,"coverage":{"guarantee":"best_effort","order":"recording_cursor","snapshot_across_requests":false}}),
    ))
}
#[derive(Default, Deserialize)]
#[serde(deny_unknown_fields)]
struct Summary {
    through: Option<u64>,
}
async fn summary(
    Extension(app): Extension<App>,
    Path((sid, turn)): Path<(String, String)>,
    Query(q): Query<Summary>,
) -> Result<Json<Value>, ApiError> {
    app.store.observation_run(&sid, &turn)?;
    let page = read(
        &app,
        Read {
            deployment: app.config.schema.clone(),
            scopes: vec![scope(&sid, &turn)],
            after: 0,
            through: q.through,
            limit: openworker_observability::query::MAX_SUMMARY_STEPS,
        },
    )
    .await?;
    let run = app.store.observation_run(&sid, &turn)?;
    let summary = summary::build(&page, canonical(&run));
    Ok(Json(json!({"run":run,"summary":summary})))
}
async fn grouped(
    Extension(app): Extension<App>,
    Query(q): Query<Search>,
) -> Result<Json<Value>, ApiError> {
    if q.limit.unwrap_or(25) > 25 {
        return Err(ApiError(Error::Invalid.into()));
    }
    let selected = run_search(&app, &q)?;
    let runs: Vec<Run> =
        serde_json::from_value(selected["runs"].clone()).map_err(|e| ApiError(e.into()))?;
    if runs.is_empty() {
        let summary = summary::build(
            &openworker_observability::query::Page {
                steps: vec![],
                through: 0,
                next: None,
            },
            Canonical::default(),
        );
        return Ok(Json(
            json!({"selection":selected,"summary":summary,"scope":"selected_run_page","selection_truncated":false,"selected_runs":0,"runs_with_records":0}),
        ));
    }
    let scopes = runs
        .iter()
        .map(|r| scope(&r.session_id, &r.turn_id))
        .collect();
    let page = read(
        &app,
        Read {
            deployment: app.config.schema.clone(),
            scopes,
            after: 0,
            through: None,
            limit: openworker_observability::query::MAX_SUMMARY_STEPS,
        },
    )
    .await?;
    let mut pending = Some(0u64);
    for run in &runs {
        let latest = app.store.observation_run(&run.session_id, &run.turn_id)?;
        pending = pending.and_then(|n| n.checked_add(latest.pending_waits));
    }
    // Multiple concurrent runs have no single meaningful turn wall elapsed.
    let recorded: std::collections::BTreeSet<_> = page
        .steps
        .iter()
        .map(|r| (&r.step.session_hash, &r.step.turn_hash))
        .collect();
    let recorded_runs = recorded.len();
    let summary = summary::build(
        &page,
        Canonical {
            pending_waits: pending,
            ..Default::default()
        },
    );
    Ok(Json(
        json!({"selection":selected,"summary":summary,"scope":"selected_run_page","selection_truncated":selected["next_cursor"].is_string(),"selected_runs":runs.len(),"runs_with_records":recorded_runs}),
    ))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn only_exact_read_routes_are_authorized() {
        for prefix in ["", "/v1"] {
            for path in [
                "/observability/runs",
                "/observability/summary",
                "/observability/health",
                "/sessions/s/observability/t/timeline",
                "/sessions/s/observability/t/summary",
            ] {
                let path = format!("{prefix}{path}");
                assert!(authority("GET", &path));
                for method in ["POST", "PUT", "DELETE"] {
                    assert!(!authority(method, &path));
                }
            }
        }
        assert!(matches!(
            action("GET", "/v1/observability/health"),
            Some(Action::Admin)
        ));
        for path in [
            "/v1evil/observability/runs",
            "/v1/observability/runs/extra",
            "/v1/sessions/s/observability/t/anything",
            "/v1/sessions/s/observability/t/summary/extra",
        ] {
            assert!(!authority("GET", path));
        }
    }
    #[test]
    fn callers_cannot_choose_recording_database_or_scope_hashes() {
        assert!(serde_json::from_value::<Search>(json!({"deployment":"other"})).is_err());
        assert!(serde_json::from_value::<Page>(json!({"session_hash":"other"})).is_err());
        assert!(serde_json::from_value::<Summary>(json!({"database":"other"})).is_err());
    }
}
