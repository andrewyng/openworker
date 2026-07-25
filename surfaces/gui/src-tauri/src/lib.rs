//! OpenWorker desktop shell.
//!
//! Tauri is a thin native window over the existing React SPA. It:
//!   1. picks a free localhost port and starts the Python `openworker-server` as a managed
//!      sidecar on that port (so it never clashes with a hand-run server on 8765);
//!   2. injects the sidecar HTTP/WS addresses and per-launch authentication token before the
//!      SPA loads (single codebase — the browser build still hits 8765);
//!   3. lives in the system tray: closing the window hides it (keeps MyHelper + the scheduler
//!      running); only tray → Quit stops the sidecar;
//!   4. exposes native commands: folder picker, autostart (open-at-login), and keep-awake
//!      (caffeinate, so scheduled tasks fire while the Mac is idle).
//!
//! The sidecar inherits this process's environment, so a shell-launched `npm run tauri dev`
//! passes `OPENAI_API_KEY` through. A Finder-launched app has no shell env — there the key
//! comes from the SecretStore (Settings tab), see `coworker.providers.resolve_api_key`.

use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
#[cfg(target_os = "windows")]
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};

use ocw_stt::Dictation;
use tauri::{
    menu::{Menu, MenuItem},
    tray::TrayIconBuilder,
    Emitter, Manager, RunEvent, WebviewUrl, WebviewWindowBuilder, WindowEvent,
};
use tauri_plugin_autostart::ManagerExt;
use uuid::Uuid;

mod voice;
use voice::*;

/// The sidecar server child — killed on exit (orphaned servers have bitten us before).
struct ServerProcess(Mutex<Option<Child>>);
/// The active keep-awake guard while keep-awake is on (None when off). Dropping the guard
/// releases the hold (kills `caffeinate` on macOS, clears the execution state on Windows).
struct KeepAwake(Mutex<Option<KeepAwakeGuard>>);

fn free_port() -> u16 {
    std::net::TcpListener::bind("127.0.0.1:0")
        .and_then(|l| l.local_addr())
        .map(|a| a.port())
        .unwrap_or(8765)
}

fn launch_token() -> String {
    format!("{}{}", Uuid::new_v4().simple(), Uuid::new_v4().simple())
}

/// Path to the server entrypoint. Resolution order:
///   1. `COWORKER_SERVER_BIN` env override.
///   2. The bundled onedir sidecar shipped via Tauri `resources` (production): the
///      `sidecar/` folder lands in Contents/Resources on macOS and in the install dir
///      (next to the app exe) on Windows.
///   3. Legacy onefile slot: `openworker-server[.exe]` next to the app binary (pre-onedir
///      builds used Tauri externalBin).
///   4. Dev fallback: the repo venv, relative to this crate (`src-tauri` → `platform/.venv`;
///      `bin/` on POSIX, `Scripts\` on Windows).
fn server_bin() -> PathBuf {
    if let Ok(p) = std::env::var("COWORKER_SERVER_BIN") {
        return PathBuf::from(p);
    }
    let exe_name = if cfg!(windows) {
        "openworker-server.exe"
    } else {
        "openworker-server"
    };
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            // macOS: Contents/MacOS/<app> → Contents/Resources/sidecar/; Windows: resources
            // unpack next to the exe, so <install>/sidecar/.
            let mut candidates = vec![dir.join("sidecar").join(exe_name)];
            if let Some(contents) = dir.parent() {
                candidates.push(contents.join("Resources").join("sidecar").join(exe_name));
            }
            candidates.push(dir.join(exe_name)); // legacy onefile externalBin slot
            for c in candidates {
                if c.exists() {
                    return c;
                }
            }
        }
    }
    let mut p = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    if cfg!(windows) {
        p.push("../../../.venv/Scripts/openworker-server.exe");
    } else {
        p.push("../../../.venv/bin/openworker-server");
    }
    p
}

/// Mirror of `coworker.secrets.state_dir()` so the shell and server agree on `desktop.json`.
/// Windows: `%APPDATA%\coworker`; POSIX: `~/.config/coworker`. `COWORKER_STATE_DIR` overrides.
fn state_dir() -> PathBuf {
    if let Ok(d) = std::env::var("COWORKER_STATE_DIR") {
        return PathBuf::from(d);
    }
    #[cfg(windows)]
    {
        if let Ok(appdata) = std::env::var("APPDATA") {
            return PathBuf::from(appdata).join("coworker");
        }
    }
    let home = std::env::var("HOME").unwrap_or_else(|_| ".".into());
    PathBuf::from(home).join(".config").join("coworker")
}

fn desktop_prefs_path() -> PathBuf {
    state_dir().join("desktop.json")
}

/// The sidecar's log file: `<state_dir>/logs/openworker-server.log`, fresh per
/// launch with the previous run kept as `.old`. None (→ /dev/null) only if the
/// directory can't be created — logging must never block startup.
fn server_log_file() -> Option<std::fs::File> {
    let dir = state_dir().join("logs");
    std::fs::create_dir_all(&dir).ok()?;
    let path = dir.join("openworker-server.log");
    if path.exists() {
        let _ = std::fs::rename(&path, dir.join("openworker-server.log.old"));
    }
    std::fs::File::create(&path).ok()
}

fn spawn_server(port: u16, api_token: &str) -> Option<Child> {
    let mut server_cmd = Command::new(server_bin());
    server_cmd
        .args(["--host", "127.0.0.1", "--port", &port.to_string()])
        // The sidecar self-exits if we die abruptly (dev-watcher restart, crash).
        .env("COWORKER_EXIT_WITH_PARENT", "1")
        .env("COWORKER_PARENT_PID", std::process::id().to_string())
        .env("COWORKER_API_TOKEN", api_token)
        // The GUI has no console, so route the server to a real per-launch log.
        .stdin(Stdio::null());

    match server_log_file() {
        Some(log) => {
            if let Ok(err_clone) = log.try_clone() {
                server_cmd
                    .stdout(Stdio::from(log))
                    .stderr(Stdio::from(err_clone));
            } else {
                server_cmd.stdout(Stdio::from(log)).stderr(Stdio::null());
            }
        }
        None => {
            server_cmd.stdout(Stdio::null()).stderr(Stdio::null());
        }
    }

    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        server_cmd.creation_flags(0x0800_0000);
    }

    match server_cmd.spawn() {
        Ok(child) => Some(child),
        Err(error) => {
            eprintln!("[coworker] failed to start server sidecar: {error}");
            None
        }
    }
}

fn read_keep_awake_pref() -> bool {
    std::fs::read_to_string(desktop_prefs_path())
        .ok()
        .and_then(|s| serde_json::from_str::<serde_json::Value>(&s).ok())
        .and_then(|v| v.get("keep_awake").and_then(|b| b.as_bool()))
        .unwrap_or(false)
}

fn write_keep_awake_pref(enabled: bool) {
    let path = desktop_prefs_path();
    if let Some(parent) = path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    let _ = std::fs::write(
        &path,
        serde_json::json!({ "keep_awake": enabled }).to_string(),
    );
}

// -- keep-awake: hold off idle + system sleep so the scheduler keeps firing -------------------
// Cross-platform behind a uniform `start_keep_awake() -> Option<KeepAwakeGuard>`; dropping the
// guard releases the hold. macOS uses the built-in `caffeinate`; Windows uses the
// SetThreadExecutionState API (a dedicated thread holds ES_CONTINUOUS so the state survives
// regardless of which Tauri worker thread toggled it); other platforms are a no-op.

#[cfg(target_os = "macos")]
struct KeepAwakeGuard(Child);

#[cfg(target_os = "macos")]
impl Drop for KeepAwakeGuard {
    fn drop(&mut self) {
        let _ = self.0.kill();
    }
}

#[cfg(target_os = "macos")]
fn start_keep_awake() -> Option<KeepAwakeGuard> {
    Command::new("caffeinate")
        .args(["-i", "-s"])
        .spawn()
        .ok()
        .map(KeepAwakeGuard)
}

#[cfg(target_os = "windows")]
extern "system" {
    fn SetThreadExecutionState(es_flags: u32) -> u32;
}

#[cfg(target_os = "windows")]
const ES_CONTINUOUS: u32 = 0x8000_0000;
#[cfg(target_os = "windows")]
const ES_SYSTEM_REQUIRED: u32 = 0x0000_0001;

#[cfg(target_os = "windows")]
struct KeepAwakeGuard {
    stop: Arc<AtomicBool>,
    handle: Option<std::thread::JoinHandle<()>>,
}

#[cfg(target_os = "windows")]
impl Drop for KeepAwakeGuard {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::SeqCst);
        if let Some(h) = self.handle.take() {
            let _ = h.join();
        }
    }
}

#[cfg(target_os = "windows")]
fn start_keep_awake() -> Option<KeepAwakeGuard> {
    let stop = Arc::new(AtomicBool::new(false));
    let stop_thread = stop.clone();
    let handle = std::thread::spawn(move || {
        // SetThreadExecutionState is thread-affine and the ES_CONTINUOUS hold is dropped when
        // the setting thread exits — so keep this thread alive, re-asserting periodically,
        // until asked to stop, then clear the hold from this same thread.
        unsafe { SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED) };
        while !stop_thread.load(Ordering::SeqCst) {
            unsafe { SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED) };
            std::thread::sleep(std::time::Duration::from_secs(30));
        }
        unsafe { SetThreadExecutionState(ES_CONTINUOUS) };
    });
    Some(KeepAwakeGuard {
        stop,
        handle: Some(handle),
    })
}

#[cfg(not(any(target_os = "macos", target_os = "windows")))]
struct KeepAwakeGuard;

#[cfg(not(any(target_os = "macos", target_os = "windows")))]
fn start_keep_awake() -> Option<KeepAwakeGuard> {
    // No portable built-in inhibitor on Linux; keep-awake is a no-op (the toggle still reflects
    // state so the UI behaves, but the OS sleep policy is left to the user).
    Some(KeepAwakeGuard)
}

// -- native commands (invoked from the SPA via window.__TAURI__.core.invoke) -----------------

/// Native macOS folder picker for the workspace gate.
#[tauri::command]
async fn pick_folder(app: tauri::AppHandle) -> Option<String> {
    use tauri_plugin_dialog::DialogExt;
    let (tx, rx) = std::sync::mpsc::channel();
    app.dialog().file().pick_folder(move |p| {
        let _ = tx.send(p);
    });
    rx.recv().ok().flatten().map(|fp| fp.to_string())
}

#[tauri::command]
fn get_autostart(app: tauri::AppHandle) -> bool {
    app.autolaunch().is_enabled().unwrap_or(false)
}

#[tauri::command]
fn set_autostart(app: tauri::AppHandle, enabled: bool) -> bool {
    let m = app.autolaunch();
    let _ = if enabled { m.enable() } else { m.disable() };
    m.is_enabled().unwrap_or(false)
}

#[tauri::command]
fn get_keep_awake(state: tauri::State<KeepAwake>) -> bool {
    state.0.lock().unwrap().is_some()
}

#[tauri::command]
fn set_keep_awake(state: tauri::State<KeepAwake>, enabled: bool) -> bool {
    let mut guard = state.0.lock().unwrap();
    if enabled {
        if guard.is_none() {
            *guard = start_keep_awake();
        }
    } else {
        // Dropping the taken guard releases the hold (kills caffeinate / clears the
        // Windows execution state).
        drop(guard.take());
    }
    let on = guard.is_some();
    write_keep_awake_pref(on);
    on
}

#[tauri::command]
fn start_window_drag(window: tauri::WebviewWindow) -> bool {
    window.start_dragging().is_ok()
}

fn show_main(app: &tauri::AppHandle) {
    if let Some(w) = app.get_webview_window("main") {
        let _ = w.unminimize();
        let _ = w.show();
        let _ = w.set_focus();
    }
}

fn stop_managed_services(app: &tauri::AppHandle) {
    if let Some(state) = app.try_state::<ServerProcess>() {
        if let Some(mut child) = state.0.lock().unwrap().take() {
            let _ = child.kill();
        }
    }
    if let Some(state) = app.try_state::<KeepAwake>() {
        // Dropping the guard releases the hold (caffeinate kill / execution-state clear).
        drop(state.0.lock().unwrap().take());
    }
}

// --- Auto-update (tauri-plugin-updater) -------------------------------------------
// The GUI drives updates through these commands (same invoke bridge as everything
// else — no global plugin JS): check, background pre-download, install. Update
// artifacts are minisign-verified against the pubkey in tauri.conf.json before
// anything is installed; the manifest lives at the endpoints configured there
// (download.openworker.com → GitHub Releases).

#[derive(serde::Serialize)]
struct UpdateInfo {
    version: String,
    notes: String,
}

#[tauri::command]
async fn check_for_update(app: tauri::AppHandle) -> Result<Option<UpdateInfo>, String> {
    use tauri_plugin_updater::UpdaterExt;
    let updater = app.updater().map_err(|e| e.to_string())?;
    let update = updater.check().await.map_err(|e| e.to_string())?;
    Ok(update.map(|u| UpdateInfo {
        version: u.version.clone(),
        notes: u.body.clone().unwrap_or_default(),
    }))
}

/// Update bytes pre-fetched by `download_update`, keyed by version. The GUI kicks the
/// download off as soon as a release is offered, so clicking "Restart to update" installs
/// from memory instead of sitting on a multi-minute download behind a spinner.
struct PendingUpdate(Mutex<Option<(String, Vec<u8>)>>);

#[tauri::command]
async fn download_update(
    app: tauri::AppHandle,
    pending: tauri::State<'_, PendingUpdate>,
) -> Result<(), String> {
    use tauri_plugin_updater::UpdaterExt;
    let updater = app.updater().map_err(|e| e.to_string())?;
    let Some(update) = updater.check().await.map_err(|e| e.to_string())? else {
        return Err("no update available".into());
    };
    // Periodic re-checks re-invoke this for the same release — the cached bytes stand.
    // (Guard scope stays sync: a std MutexGuard must not live across an await.)
    {
        let slot = pending.0.lock().unwrap();
        if slot
            .as_ref()
            .map(|(v, _)| v == &update.version)
            .unwrap_or(false)
        {
            return Ok(());
        }
    }
    let bytes = update
        .download(|_, _| {}, || {})
        .await
        .map_err(|e| e.to_string())?;
    *pending.0.lock().unwrap() = Some((update.version.clone(), bytes));
    Ok(())
}

/// Drop the pre-fetched bundle. Invoked on "Later": a dismissed release would
/// otherwise pin tens of MB in memory for the rest of an app run that can last
/// weeks. Changing one's mind just re-downloads.
#[tauri::command]
fn clear_pending_update(pending: tauri::State<'_, PendingUpdate>) {
    *pending.0.lock().unwrap() = None;
}

#[tauri::command]
async fn install_update(
    app: tauri::AppHandle,
    pending: tauri::State<'_, PendingUpdate>,
) -> Result<(), String> {
    use tauri_plugin_updater::UpdaterExt;
    let updater = app.updater().map_err(|e| e.to_string())?;
    let Some(update) = updater.check().await.map_err(|e| e.to_string())? else {
        return Err("no update available".into());
    };
    // Pre-fetched bytes for this exact version install instantly; a stale or missing
    // cache falls back to the original blocking download-and-install.
    let cached = {
        let mut slot = pending.0.lock().unwrap();
        match slot.take() {
            Some((v, bytes)) if v == update.version => Some(bytes),
            _ => None,
        }
    };
    match cached {
        Some(bytes) => update.install(bytes).map_err(|e| e.to_string())?,
        None => update
            .download_and_install(|_, _| {}, || {})
            .await
            .map_err(|e| e.to_string())?,
    }
    // Windows never reaches here (the NSIS installer takes over and relaunches).
    // macOS: the .app was swapped in place — restart into the new version. The tray
    // Exit path's sidecar kill runs via RunEvent, so no orphaned openworker-server.
    app.restart();
}

fn setup_desktop(
    app: &mut tauri::App,
    port: u16,
    api_token: &str,
    inject: &str,
) -> Result<(), Box<dyn std::error::Error>> {
    let child = spawn_server(port, api_token);
    app.manage(ServerProcess(Mutex::new(child)));

    let keep_awake = if read_keep_awake_pref() {
        start_keep_awake()
    } else {
        None
    };
    app.manage(KeepAwake(Mutex::new(keep_awake)));
    app.manage(PendingUpdate(Mutex::new(None)));
    app.manage(Arc::new(Dictation::new(state_dir().join("models"))));

    let builder = WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
        .title("OpenWorker")
        .inner_size(1360.0, 900.0)
        .min_inner_size(980.0, 640.0)
        .disable_drag_drop_handler()
        .initialization_script(inject);
    #[cfg(target_os = "macos")]
    let builder = builder
        .title_bar_style(tauri::TitleBarStyle::Overlay)
        .hidden_title(true)
        .traffic_light_position(tauri::LogicalPosition::new(19.0, 24.0));
    let window = builder.build()?;

    let close_window = window.clone();
    window.on_window_event(move |event| {
        if let WindowEvent::CloseRequested { api, .. } = event {
            let _ = close_window.hide();
            api.prevent_close();
        }
    });

    let open_item = MenuItem::with_id(app, "open", "Open OpenWorker", true, None::<&str>)?;
    let settings_item = MenuItem::with_id(app, "settings", "Settings", true, None::<&str>)?;
    let quit_item = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&open_item, &settings_item, &quit_item])?;
    let tray_icon = tauri::image::Image::new(include_bytes!("../icons/tray.rgba"), 44, 44);
    TrayIconBuilder::new()
        .tooltip("OpenWorker")
        .icon(tray_icon)
        .icon_as_template(true)
        .menu(&menu)
        .on_menu_event(|app, event| match event.id.as_ref() {
            "open" => show_main(app),
            "settings" => {
                show_main(app);
                if let Some(window) = app.get_webview_window("main") {
                    let _ = window.emit("coworker:open-settings", ());
                }
            }
            "quit" => app.exit(0),
            _ => {}
        })
        .build(app)?;

    Ok(())
}

pub fn run() {
    let port = free_port();
    let api_token = launch_token();
    let http = format!("http://127.0.0.1:{port}");
    let ws = format!("ws://127.0.0.1:{port}");
    // Debug-format yields a quoted JS string literal.
    let inject = format!(
        "window.__COWORKER_HTTP__={http:?};window.__COWORKER_WS__={ws:?};window.__COWORKER_API_TOKEN__={api_token:?};window.__OCW_PLATFORM__={:?};",
        std::env::consts::OS
    );

    tauri::Builder::default()
        // MUST be the first plugin: when a second launch happens (e.g. the user relaunches
        // while the window is closed-to-tray), this fires in the ALREADY-running instance to
        // surface its healthy window, and the second process exits before it can spawn a
        // duplicate sidecar — which previously left a window stuck on "Starting coworker…".
        .plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            show_main(app);
        }))
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_autostart::init(
            tauri_plugin_autostart::MacosLauncher::LaunchAgent,
            None,
        ))
        .invoke_handler(tauri::generate_handler![
            pick_folder,
            get_autostart,
            set_autostart,
            get_keep_awake,
            set_keep_awake,
            start_window_drag,
            get_dictation_status,
            start_dictation,
            stop_dictation,
            cancel_dictation,
            download_dictation_model,
            cancel_dictation_model_download,
            verify_dictation_model,
            mark_dictation_test_passed,
            delete_dictation_model,
            dictation_level,
            check_for_update,
            download_update,
            clear_pending_update,
            install_update
        ])
        .setup(move |app| setup_desktop(app, port, &api_token, &inject))
        .build(tauri::generate_context!())
        .expect("error while building the OpenWorker desktop app")
        .run(|app, event| {
            // Also on Exit: belt-and-suspenders in case a quit path reaches teardown without
            // a preceding ExitRequested (observed with macOS Cmd+Q under the tray setup).
            if matches!(event, RunEvent::ExitRequested { .. } | RunEvent::Exit) {
                stop_managed_services(app);
            }
        });
}
