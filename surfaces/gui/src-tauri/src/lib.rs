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
//!      (caffeinate on macOS, SetThreadExecutionState on Windows, systemd-inhibit on Linux,
//!      so scheduled tasks fire while the machine is idle).
//!
//! Platform notes. macOS and Windows always have a status area, so closing the window hides it
//! to the tray. Linux does not: plenty of sessions (ChromeOS Crostini among them) run no
//! StatusNotifier host at all, and there `TrayIconBuilder::build` fails. Tray creation is
//! therefore non-fatal, and when it fails, closing the window really closes the app — hiding
//! to a tray that isn't there would strand the user with a running, unreachable sidecar.
//!
//! The sidecar inherits this process's environment, so a shell-launched `npm run tauri dev`
//! passes `OPENAI_API_KEY` through. A Finder-launched app has no shell env — there the key
//! comes from the SecretStore (Settings tab), see `coworker.providers.resolve_api_key`.

use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};

// Voice Input ships on macOS and Windows only. On Linux the engine is swapped for the stub
// in `dictation_stub` below: linking ocw-stt would drag whisper.cpp (cmake + a C++ toolchain)
// and ALSA headers into every Linux build — a heavy build-time tax for a feature the released
// .deb/AppImage don't offer. The Tauri commands are identical either way; the stub simply
// reports "not installed" and refuses to record.
#[cfg(target_os = "linux")]
use dictation_stub::{Dictation, DownloadProgress};
#[cfg(not(target_os = "linux"))]
use ocw_stt::{Dictation, DownloadProgress};
use serde::Serialize;
use tauri::{
    menu::{Menu, MenuItem},
    tray::TrayIconBuilder,
    Emitter, Manager, RunEvent, WebviewUrl, WebviewWindowBuilder, WindowEvent,
};
use tauri_plugin_autostart::ManagerExt;
use uuid::Uuid;

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

/// Directories where user-installed CLIs live but launchd's PATH never looks. Used to
/// repair PATH when the login-shell probe can't run (broken profile, exotic shell).
#[cfg(not(target_os = "windows"))]
const KNOWN_TOOL_DIRS: &[&str] = &[
    "/opt/homebrew/bin", // Apple Silicon Homebrew
    "/opt/homebrew/sbin",
    "/usr/local/bin", // Intel Homebrew, most installers
    "/usr/local/sbin",
    "/opt/local/bin", // MacPorts
    "/home/linuxbrew/.linuxbrew/bin", // Linuxbrew
    "/snap/bin",                      // snap
    // Home-relative (expanded against $HOME below). Debian/Ubuntu only put ~/.local/bin on
    // PATH from ~/.profile *if it already existed at login*, so a pipx/pip --user install
    // made after that login is invisible to a desktop-launched app without this.
    "~/.local/bin",
    "~/.cargo/bin",
    "~/go/bin",
    "~/.deno/bin",
];

/// The environment the sidecar should run with (OPE-83).
///
/// A Finder/Dock-launched app inherits launchd's minimal PATH — `/usr/bin:/bin:/usr/sbin:/sbin`
/// — so every tool the user installed via Homebrew/nvm/pyenv/asdf is invisible to the agent:
/// semgrep, gitleaks, gh, node, aws, kubectl, terraform. That silently guts the security
/// coworkers (they drive those scanners) and every ops workflow. Fix, same as VS Code and
/// friends: ask the user's login shell for its environment once at spawn and merge it in, so
/// the coworker gets the user's REAL toolchain. Credentials follow for free — aws/kubectl read
/// ~/.aws and ~/.kube via HOME, which a Finder launch already has.
///
/// Guards: `-i` (not just `-l`) because brew/nvm/pyenv init usually lives in .zshrc; markers so
/// a chatty profile's own output can't be parsed as variables; a 5s timeout with the child
/// killed, so a hanging profile can never block app launch; and a well-known-dirs PATH repair as
/// the fallback. Skipped entirely when we were launched FROM a shell (SHLVL set) — we already
/// inherit the real thing, and `npm run tauri dev` should behave exactly as before.
#[cfg(not(target_os = "windows"))]
fn sidecar_env() -> std::collections::HashMap<String, String> {
    use std::collections::HashMap;
    use std::io::Read;
    use std::sync::mpsc;
    use std::time::Duration;

    const START: &str = "__OCW_ENV_START__";
    const END: &str = "__OCW_ENV_END__";

    let mut out: HashMap<String, String> = HashMap::new();

    // Launched from a shell (dev run, `open` from a terminal): the env is already real.
    if std::env::var_os("SHLVL").is_some() {
        return out;
    }

    // Fall back to the platform's own default login shell — zsh on macOS, bash on Linux
    // (where /bin/zsh usually does not exist, and spawning it would skip the probe entirely).
    let default_shell = if cfg!(target_os = "macos") {
        "/bin/zsh"
    } else {
        "/bin/bash"
    };
    let shell = std::env::var("SHELL").unwrap_or_else(|_| default_shell.to_string());
    let script = format!("echo {START}; env; echo {END}");
    let spawned = Command::new(&shell)
        .args(["-ilc", &script])
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn();

    if let Ok(mut child) = spawned {
        if let Some(mut stdout) = child.stdout.take() {
            let (tx, rx) = mpsc::channel();
            std::thread::spawn(move || {
                let mut buf = String::new();
                let _ = stdout.read_to_string(&mut buf);
                let _ = tx.send(buf);
            });
            match rx.recv_timeout(Duration::from_secs(5)) {
                Ok(text) => {
                    let _ = child.wait();
                    let mut inside = false;
                    for line in text.lines() {
                        if line.trim_end() == START {
                            inside = true;
                            continue;
                        }
                        if line.trim_end() == END {
                            break;
                        }
                        if !inside {
                            continue;
                        }
                        // `env` prints KEY=value; continuation lines of a multi-line value
                        // have no '=' before whitespace and are skipped rather than guessed at.
                        if let Some((k, v)) = line.split_once('=') {
                            if !k.is_empty() && !k.contains(char::is_whitespace) {
                                out.insert(k.to_string(), v.to_string());
                            }
                        }
                    }
                }
                Err(_) => {
                    // Hung profile — never let it hold up launch.
                    let _ = child.kill();
                    let _ = child.wait();
                }
            }
        }
    }

    // These describe the probe shell, not the user's environment.
    for k in ["SHLVL", "PWD", "OLDPWD", "_"] {
        out.remove(k);
    }

    // Whether the probe worked or not, make sure the usual install dirs are reachable.
    let base = out
        .get("PATH")
        .cloned()
        .or_else(|| std::env::var("PATH").ok())
        .unwrap_or_default();
    let mut parts: Vec<String> = base.split(':').filter(|s| !s.is_empty()).map(String::from).collect();
    let home = out
        .get("HOME")
        .cloned()
        .or_else(|| std::env::var("HOME").ok())
        .unwrap_or_default();
    for dir in KNOWN_TOOL_DIRS {
        // `~/...` entries resolve against the user's home; with no HOME to resolve them
        // against they are skipped rather than added as a literal "~" path.
        let resolved = match dir.strip_prefix("~/") {
            Some(rest) if !home.is_empty() => format!("{home}/{rest}"),
            Some(_) => continue,
            None => (*dir).to_string(),
        };
        if !parts.iter().any(|p| *p == resolved) && std::path::Path::new(&resolved).is_dir() {
            parts.push(resolved);
        }
    }
    out.insert("PATH".to_string(), parts.join(":"));
    out
}

/// Windows GUI apps inherit the user's full environment already.
#[cfg(target_os = "windows")]
fn sidecar_env() -> std::collections::HashMap<String, String> {
    std::collections::HashMap::new()
}

/// Path to the server entrypoint. Resolution order:
///   1. `COWORKER_SERVER_BIN` env override.
///   2. Tauri's own resource directory — `resource_dir` is what the bundler actually
///      populated on this platform, so it is right by construction. This is the only
///      candidate that finds the sidecar in a Linux .deb/.AppImage, where the binary lands
///      in `/usr/bin/` and its resources in `/usr/lib/OpenWorker/` — nothing the exe-relative
///      guesses below would ever reach.
///   3. Exe-relative guesses, kept as-is for the layouts they already serve: the `sidecar/`
///      folder lands in Contents/Resources on macOS and in the install dir (next to the app
///      exe) on Windows.
///   4. Legacy onefile slot: `openworker-server[.exe]` next to the app binary (pre-onedir
///      builds used Tauri externalBin).
///   5. Dev fallback: the repo venv, relative to this crate (`src-tauri` → repo-root `.venv`;
///      `bin/` on POSIX, `Scripts\` on Windows).
fn server_bin(resource_dir: Option<PathBuf>) -> PathBuf {
    if let Ok(p) = std::env::var("COWORKER_SERVER_BIN") {
        return PathBuf::from(p);
    }
    let exe_name = if cfg!(windows) {
        "openworker-server.exe"
    } else {
        "openworker-server"
    };
    let mut candidates: Vec<PathBuf> = Vec::new();
    if let Some(res) = resource_dir {
        candidates.push(res.join("sidecar").join(exe_name));
    }
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            candidates.push(dir.join("sidecar").join(exe_name));
            if let Some(contents) = dir.parent() {
                candidates.push(contents.join("Resources").join("sidecar").join(exe_name));
            }
            candidates.push(dir.join(exe_name)); // legacy onefile externalBin slot
        }
    }
    for c in candidates {
        if c.exists() {
            return c;
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
// regardless of which Tauri worker thread toggled it); Linux and the other unixes use
// `systemd-inhibit`. None → no inhibitor on this system, and the caller reports the toggle
// as off rather than claiming a hold it never took.

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
struct KeepAwakeGuard(Child);

#[cfg(not(any(target_os = "macos", target_os = "windows")))]
impl Drop for KeepAwakeGuard {
    fn drop(&mut self) {
        // Closing the pipe — not killing the process — is what releases the lock: `cat` reads
        // EOF and exits, and systemd-inhibit drops the inhibitor as it reaps it. Killing
        // systemd-inhibit instead would leave its `cat` child orphaned to init forever, and a
        // hard-killed app releases the lock for free this way (our pipe end closes with us).
        drop(self.0.stdin.take());
        for _ in 0..50 {
            match self.0.try_wait() {
                Ok(Some(_)) => return,
                Ok(None) => std::thread::sleep(std::time::Duration::from_millis(10)),
                Err(_) => break,
            }
        }
        // Backstop: a wedged inhibitor must never hold the UI thread longer than this.
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}

#[cfg(not(any(target_os = "macos", target_os = "windows")))]
fn start_keep_awake() -> Option<KeepAwakeGuard> {
    // logind's inhibitor lock, held for exactly as long as the command it runs. `cat` with a
    // piped stdin is the hold (see Drop). No lock → None, and the Settings toggle stays off
    // instead of lying.
    //
    // ChromeOS Crostini caveat: the lock is real inside the VM, but ChromeOS itself decides
    // when the device suspends, and a suspended Chromebook stops the VM regardless.
    let mut child = Command::new("systemd-inhibit")
        .args([
            "--what=idle:sleep",
            "--who=OpenWorker",
            "--why=Scheduled coworker runs",
            "--mode=block",
            "cat",
        ])
        .stdin(Stdio::piped())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .ok()?;

    // A successful spawn only proves the binary ran. systemd-inhibit ITSELF fails whenever it
    // cannot reach logind — containers, non-systemd sessions, no session bus — printing
    // "Failed to connect to bus" to the stderr we discarded and exiting 1 straight away. Taking
    // the spawn as success left us holding a corpse and reporting a hold nobody held, which is
    // the exact lie this function exists to avoid.
    //
    // So wait for it to fail. A working inhibitor runs `cat` until we close its stdin, i.e.
    // forever; a broken one is gone in milliseconds. Polling caps the cost at GRACE and returns
    // the instant it dies, which on the happy path a settings toggle will never notice.
    const GRACE: std::time::Duration = std::time::Duration::from_millis(400);
    const STEP: std::time::Duration = std::time::Duration::from_millis(10);
    let deadline = std::time::Instant::now() + GRACE;
    while std::time::Instant::now() < deadline {
        match child.try_wait() {
            Ok(None) => std::thread::sleep(STEP), // still alive — the lock is real so far
            Ok(Some(_)) => return None,           // exited: no inhibitor was ever taken
            Err(_) => {
                let _ = child.kill();
                let _ = child.wait();
                return None;
            }
        }
    }
    Some(KeepAwakeGuard(child))
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

/// Linux stand-in for the `ocw-stt` engine (see the import at the top of this file).
///
/// It mirrors exactly the API surface the commands below touch, so there is ONE set of Tauri
/// commands on every platform — no per-target `#[cfg]` on twelve `#[tauri::command]`s, and no
/// way for the two paths to drift. Every call answers the way an engine with no model installed
/// would, and `voice_input_compatibility()` already reports `supported: false` on Linux, so the
/// GUI dims the mic button before any of this is reachable.
#[cfg(target_os = "linux")]
mod dictation_stub {
    use serde::Serialize;
    use std::path::PathBuf;

    const UNSUPPORTED: &str = "Voice Input is not available in the Linux build of OpenWorker.";

    #[derive(Debug, Clone, Serialize)]
    pub struct DictationStatus {
        pub recording: bool,
        pub model_installed: bool,
        pub model_verified: bool,
        pub test_passed: bool,
        pub download_in_progress: bool,
        pub model_name: &'static str,
        pub model_bytes: u64,
    }

    #[derive(Debug, Clone, Copy, Serialize)]
    pub struct DownloadProgress {
        pub downloaded_bytes: u64,
        pub total_bytes: u64,
    }

    pub struct Dictation;

    impl Dictation {
        pub fn new(_model_dir: impl Into<PathBuf>) -> Self {
            Self
        }

        pub fn status(&self) -> DictationStatus {
            DictationStatus {
                recording: false,
                model_installed: false,
                model_verified: false,
                test_passed: false,
                download_in_progress: false,
                model_name: "none",
                model_bytes: 0,
            }
        }

        pub fn install_default_model_with_progress(
            &self,
            _on_progress: impl FnMut(DownloadProgress),
        ) -> Result<(), String> {
            Err(UNSUPPORTED.to_owned())
        }

        pub fn verify_default_model(&self) -> Result<(), String> {
            Err(UNSUPPORTED.to_owned())
        }

        pub fn mark_test_passed(&self) -> Result<(), String> {
            Err(UNSUPPORTED.to_owned())
        }

        pub fn delete_default_model(&self) -> Result<(), String> {
            Err(UNSUPPORTED.to_owned())
        }

        pub fn start(&self) -> Result<(), String> {
            Err(UNSUPPORTED.to_owned())
        }

        pub fn stop_and_transcribe(&self) -> Result<String, String> {
            Err(UNSUPPORTED.to_owned())
        }

        pub fn cancel_model_download(&self) {}

        pub fn cancel(&self) {}

        pub fn input_level(&self) -> f32 {
            0.0
        }
    }
}

// -- local dictation ---------------------------------------------------------------------------
// The actual microphone/model code lives in the Tauri-free `ocw-stt` crate. This shell owns the
// macOS permission prompt and translates the reusable API into React-friendly Tauri commands.

#[derive(Clone, Serialize)]
struct VoiceInputStatus {
    recording: bool,
    model_installed: bool,
    model_verified: bool,
    test_passed: bool,
    download_in_progress: bool,
    model_name: &'static str,
    model_bytes: u64,
    supported: bool,
    device_summary: String,
    compatibility_reason: Option<String>,
}

fn voice_input_status(dictation: &Dictation) -> VoiceInputStatus {
    let status = dictation.status();
    let (supported, device_summary, compatibility_reason) = voice_input_compatibility();
    VoiceInputStatus {
        recording: status.recording,
        model_installed: status.model_installed,
        model_verified: status.model_verified,
        test_passed: status.test_passed,
        download_in_progress: status.download_in_progress,
        model_name: status.model_name,
        model_bytes: status.model_bytes,
        supported,
        device_summary,
        compatibility_reason,
    }
}

#[cfg(target_os = "macos")]
fn voice_input_compatibility() -> (bool, String, Option<String>) {
    let version = Command::new("/usr/bin/sw_vers")
        .arg("-productVersion")
        .output()
        .ok()
        .filter(|output| output.status.success())
        .map(|output| String::from_utf8_lossy(&output.stdout).trim().to_owned())
        .unwrap_or_else(|| "unknown version".to_owned());
    let major = version
        .split('.')
        .next()
        .and_then(|part| part.parse::<u32>().ok())
        .unwrap_or(0);
    let apple_silicon = std::env::consts::ARCH == "aarch64";
    let supported = apple_silicon && major >= 12;
    let architecture = if apple_silicon {
        "Apple Silicon"
    } else {
        "Intel"
    };
    let summary = format!("macOS {version} · {architecture}");
    let reason = if !apple_silicon {
        Some("Voice Input currently requires an Apple Silicon Mac (M1 or newer).".to_owned())
    } else if major < 12 {
        Some("Voice Input requires macOS 12 or newer.".to_owned())
    } else {
        None
    };
    (supported, summary, reason)
}

#[cfg(target_os = "windows")]
fn voice_input_compatibility() -> (bool, String, Option<String>) {
    let version = Command::new("cmd")
        .args(["/C", "ver"])
        .output()
        .ok()
        .map(|output| String::from_utf8_lossy(&output.stdout).trim().to_owned())
        .unwrap_or_else(|| "Windows (unknown version)".to_owned());
    let build = version
        .split(|character: char| !character.is_ascii_digit() && character != '.')
        .find(|part| part.matches('.').count() >= 2)
        .and_then(|part| part.split('.').nth(2))
        .and_then(|part| part.parse::<u32>().ok())
        .unwrap_or(0);
    let x64 = std::env::consts::ARCH == "x86_64";
    let supported = x64 && build >= 19_045;
    let reason = if !x64 {
        Some("Voice Input currently requires a 64-bit x64 Windows PC.".to_owned())
    } else if build < 19_045 {
        Some("Voice Input requires Windows 10 22H2 or Windows 11.".to_owned())
    } else {
        None
    };
    (supported, format!("{version} · x64"), reason)
}

#[cfg(not(any(target_os = "macos", target_os = "windows")))]
fn voice_input_compatibility() -> (bool, String, Option<String>) {
    // Not a runtime capability check: the Linux build links the `dictation_stub` engine, so
    // there is nothing to be compatible WITH. Everything else in the app works the same.
    (
        false,
        format!("{} · {}", std::env::consts::OS, std::env::consts::ARCH),
        Some("Voice Input is currently supported on macOS and Windows.".to_owned()),
    )
}

#[tauri::command]
fn get_dictation_status(state: tauri::State<Arc<Dictation>>) -> VoiceInputStatus {
    voice_input_status(&state)
}

#[tauri::command]
async fn start_dictation(
    state: tauri::State<'_, Arc<Dictation>>,
) -> Result<VoiceInputStatus, String> {
    // Off the main thread: opening the input device blocks on macOS's one-time microphone
    // permission dialog (and CoreAudio device setup) — a sync command would freeze the UI
    // behind the system prompt.
    let (supported, _, reason) = voice_input_compatibility();
    if !supported {
        return Err(
            reason.unwrap_or_else(|| "Voice Input is not supported on this device.".to_owned())
        );
    }
    let dictation = state.inner().clone();
    tauri::async_runtime::spawn_blocking(move || {
        dictation.start()?;
        Ok::<VoiceInputStatus, String>(voice_input_status(&dictation))
    })
    .await
    .map_err(|e| format!("Dictation failed to start: {e}"))?
}

#[tauri::command]
async fn stop_dictation(state: tauri::State<'_, Arc<Dictation>>) -> Result<String, String> {
    let dictation = state.inner().clone();
    tauri::async_runtime::spawn_blocking(move || dictation.stop_and_transcribe())
        .await
        .map_err(|e| format!("Dictation stopped unexpectedly: {e}"))?
}

#[tauri::command]
fn cancel_dictation(state: tauri::State<Arc<Dictation>>) {
    state.cancel();
}

#[tauri::command]
async fn download_dictation_model(
    app: tauri::AppHandle,
    state: tauri::State<'_, Arc<Dictation>>,
) -> Result<VoiceInputStatus, String> {
    let dictation = state.inner().clone();
    tauri::async_runtime::spawn_blocking(move || {
        dictation.install_default_model_with_progress(|progress: DownloadProgress| {
            let _ = app.emit("dictation-download-progress", progress);
        })?;
        Ok::<VoiceInputStatus, String>(voice_input_status(&dictation))
    })
    .await
    .map_err(|e| format!("Voice model download stopped unexpectedly: {e}"))?
}

#[tauri::command]
fn cancel_dictation_model_download(state: tauri::State<Arc<Dictation>>) {
    state.cancel_model_download();
}

#[tauri::command]
async fn verify_dictation_model(
    state: tauri::State<'_, Arc<Dictation>>,
) -> Result<VoiceInputStatus, String> {
    let dictation = state.inner().clone();
    tauri::async_runtime::spawn_blocking(move || {
        dictation.verify_default_model()?;
        Ok::<VoiceInputStatus, String>(voice_input_status(&dictation))
    })
    .await
    .map_err(|e| format!("Voice model verification stopped unexpectedly: {e}"))?
}

#[tauri::command]
fn mark_dictation_test_passed(
    state: tauri::State<Arc<Dictation>>,
) -> Result<VoiceInputStatus, String> {
    state.mark_test_passed()?;
    Ok(voice_input_status(&state))
}

#[tauri::command]
fn delete_dictation_model(state: tauri::State<Arc<Dictation>>) -> Result<VoiceInputStatus, String> {
    state.delete_default_model()?;
    Ok(voice_input_status(&state))
}

/// Instantaneous mic loudness (0..1) while a dictation is recording — the composer polls
/// this to draw a real input-driven waveform instead of decorative bars (owner catch,
/// DMG #28 walkthrough).
#[tauri::command]
fn dictation_level(state: tauri::State<Arc<Dictation>>) -> f32 {
    state.input_level()
}

fn show_main(app: &tauri::AppHandle) {
    if let Some(w) = app.get_webview_window("main") {
        let _ = w.unminimize();
        let _ = w.show();
        let _ = w.set_focus();
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

/// Whether this install can replace itself in place.
///
/// macOS and Windows always can (the .app is swapped; the NSIS installer relaunches). On Linux
/// only an AppImage can: Tauri's Linux updater rewrites the file `$APPIMAGE` points at, and a
/// .deb has no such file to rewrite — the system package manager owns those paths, and writing
/// into them behind its back is how you get a half-upgraded install. So a .deb never sees an
/// update prompt; `apt`/the download page is its upgrade path (docs/linux.md).
fn self_update_supported() -> bool {
    if cfg!(target_os = "linux") {
        std::env::var_os("APPIMAGE").is_some()
    } else {
        true
    }
}

const NO_SELF_UPDATE: &str =
    "This build updates through your package manager, not from inside the app.";

/// Asked once by Settings so it can drop its "Check for updates" button where checking is
/// meaningless. Without this the button would answer "You're on the latest version" to a .deb
/// install — a claim it has no way to make, since it never looked.
#[tauri::command]
fn can_self_update() -> bool {
    self_update_supported()
}

#[tauri::command]
async fn check_for_update(app: tauri::AppHandle) -> Result<Option<UpdateInfo>, String> {
    use tauri_plugin_updater::UpdaterExt;
    // No prompt where accepting it could not work — see self_update_supported().
    if !self_update_supported() {
        return Ok(None);
    }
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
    if !self_update_supported() {
        return Err(NO_SELF_UPDATE.to_owned());
    }
    let updater = app.updater().map_err(|e| e.to_string())?;
    let Some(update) = updater.check().await.map_err(|e| e.to_string())? else {
        return Err("no update available".into());
    };
    // Periodic re-checks re-invoke this for the same release — the cached bytes stand.
    // (Guard scope stays sync: a std MutexGuard must not live across an await.)
    {
        let slot = pending.0.lock().unwrap();
        if slot.as_ref().map(|(v, _)| v == &update.version).unwrap_or(false) {
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
    if !self_update_supported() {
        return Err(NO_SELF_UPDATE.to_owned());
    }
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
    // macOS: the .app was swapped in place — restart into the new version. Linux: same, for
    // the AppImage. The tray Exit path's sidecar kill runs via RunEvent, so no orphaned
    // openworker-server.
    app.restart();
}

/// ChromeOS Crostini: the container reaches the GPU through virtio-gpu and Sommelier, and
/// WebKitGTK's DMABuf renderer (its default since 2.42) renders a BLANK WHITE WINDOW there —
/// the app looks hung on first launch with nothing in the log to explain it. The older
/// renderer costs nothing in a VM with no direct GPU access anyway.
///
/// Deliberately narrow: only on Crostini (both markers are ChromeOS-only), and only when the
/// user has not set the variable themselves. A normal Linux desktop keeps the fast path.
/// Must run before the webview exists, hence the top of `run()`.
#[cfg(target_os = "linux")]
fn apply_crostini_workarounds() {
    if std::env::var_os("WEBKIT_DISABLE_DMABUF_RENDERER").is_some() {
        return;
    }
    let crostini = std::path::Path::new("/dev/.cros_milestone").exists()
        || std::path::Path::new("/opt/google/cros-containers").is_dir();
    if crostini {
        std::env::set_var("WEBKIT_DISABLE_DMABUF_RENDERER", "1");
    }
}

pub fn run() {
    #[cfg(target_os = "linux")]
    apply_crostini_workarounds();

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
            can_self_update,
            check_for_update,
            download_update,
            clear_pending_update,
            install_update
        ])
        .setup(move |app| {
            // 1. Start the Python server sidecar on the chosen port (inherits our env).
            let mut server_cmd = Command::new(server_bin(app.path().resource_dir().ok()));
            server_cmd
                .args(["--host", "127.0.0.1", "--port", &port.to_string()])
                // The user's real shell environment (PATH to their tools, AWS_PROFILE,
                // KUBECONFIG, …) — see sidecar_env(). Applied FIRST so the explicit COWORKER_*
                // vars below always win over anything a profile happens to export.
                .envs(sidecar_env())
                // The sidecar self-exits if we die abruptly (dev-watcher restart, crash) —
                // belt-and-suspenders alongside the RunEvent::ExitRequested kill below.
                // The explicit PID matters: under PyInstaller onefile the python process is a
                // *grandchild* (bootloader in between), so getppid() never points at us and a
                // reparenting check alone leaks both processes on quit.
                .env("COWORKER_EXIT_WITH_PARENT", "1")
                .env("COWORKER_PARENT_PID", std::process::id().to_string())
                .env("COWORKER_API_TOKEN", &api_token)
                // This GUI app has no console, so a console-subsystem child would inherit
                // invalid std handles and crash a few seconds in when uvicorn writes its logs
                // (the "Starting coworker…" freeze on Windows). Hand it real handles: the
                // server's output goes to a log file so field issues are debuggable at all
                // ("relay off, no messages" was undiagnosable with everything on /dev/null).
                // One file per launch, previous run kept as .old.
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
            // CREATE_NO_WINDOW: the sidecar is a console binary; without this a console window
            // would flash when the GUI app spawns it on Windows.
            #[cfg(windows)]
            {
                use std::os::windows::process::CommandExt;
                server_cmd.creation_flags(0x0800_0000);
            }
            let child = match server_cmd.spawn() {
                Ok(child) => Some(child),
                Err(e) => {
                    eprintln!("[coworker] failed to start server sidecar: {e}");
                    None
                }
            };
            app.manage(ServerProcess(Mutex::new(child)));

            // Restore keep-awake from the last session.
            let ka = if read_keep_awake_pref() {
                start_keep_awake()
            } else {
                None
            };
            app.manage(KeepAwake(Mutex::new(ka)));
            app.manage(PendingUpdate(Mutex::new(None)));
            // Voice recordings are transient; only the explicitly installed local Whisper model
            // lives in the existing application state directory.
            app.manage(Arc::new(Dictation::new(state_dir().join("models"))));

            // 2. Build the window, injecting the sidecar endpoints before the SPA loads.
            //    Overlay title bar (macOS): traffic lights float over the edge-to-edge UI.
            // Only the macOS block below reassigns `builder`, so everywhere else the `mut`
            // is dead — allow it there rather than duplicating the whole builder chain.
            #[cfg_attr(not(target_os = "macos"), allow(unused_mut))]
            let mut builder =
                WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
                    .title("OpenWorker")
                    .inner_size(1360.0, 900.0)
                    .min_inner_size(980.0, 640.0)
                    // Let the WEBVIEW receive OS file drags: Tauri's own drag-drop handler
                    // otherwise intercepts them, so the composer's HTML5 onDrop (attach by
                    // dragging a file in) never fired in the desktop shell — browser dev
                    // worked, DMGs didn't. main.tsx guards against drops outside the
                    // composer navigating the page.
                    .disable_drag_drop_handler()
                    .initialization_script(&inject);
            #[cfg(target_os = "macos")]
            {
                builder = builder
                    .title_bar_style(tauri::TitleBarStyle::Overlay)
                    .hidden_title(true)
                    // Nudge the traffic lights down + in so they sit vertically centered in a
                    // roomier top strip, aligned with the sidebar toggle and title rather than
                    // jammed against the top edge.
                    .traffic_light_position(tauri::LogicalPosition::new(19.0, 24.0));
            }
            let win = builder.build()?;

            // Close-to-tray: hide instead of quitting so the sidecar keeps running — but ONLY
            // once we know there is a tray to close TO. Set by step 3 below; the flag is read
            // at close time, long after setup has finished.
            let has_tray = Arc::new(AtomicBool::new(false));
            let w = win.clone();
            let close_has_tray = has_tray.clone();
            win.on_window_event(move |event| {
                if let WindowEvent::CloseRequested { api, .. } = event {
                    if close_has_tray.load(Ordering::SeqCst) {
                        let _ = w.hide();
                        api.prevent_close();
                    }
                    // No tray: let the close through and let the app exit. Hiding would strand
                    // the user with an invisible window and a running sidecar, reachable only
                    // by killing the process.
                }
            });

            // 3. System tray: Open / Settings / Quit.
            let open_i = MenuItem::with_id(app, "open", "Open OpenWorker", true, None::<&str>)?;
            let settings_i = MenuItem::with_id(app, "settings", "Settings", true, None::<&str>)?;
            let quit_i = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&open_i, &settings_i, &quit_i])?;

            // A monochrome template icon (black + alpha, raw RGBA 44×44) so the menu bar tints
            // it for light/dark automatically — not the full-color app icon.
            let tray_icon = tauri::image::Image::new(include_bytes!("../icons/tray.rgba"), 44, 44);
            let tray = TrayIconBuilder::new()
                .tooltip("OpenWorker")
                .icon(tray_icon)
                .icon_as_template(true)
                .menu(&menu)
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "open" => show_main(app),
                    "settings" => {
                        show_main(app);
                        if let Some(w) = app.get_webview_window("main") {
                            let _ = w.eval(
                                "window.dispatchEvent(new CustomEvent('coworker:open-settings'))",
                            );
                        }
                    }
                    "quit" => app.exit(0),
                    _ => {}
                })
                .build(app);
            // NOT fatal, and this used to be `?`. macOS and Windows always have a status area,
            // but many Linux sessions run no StatusNotifier/AppIndicator host — ChromeOS
            // Crostini has no status area at all — and there this call fails. Propagating the
            // error aborts `setup`, so the whole app would refuse to start over a tray icon.
            // Instead: log it, leave has_tray false, and the window close handler above turns
            // close-to-tray back into a plain quit.
            match tray {
                Ok(_) => has_tray.store(true, Ordering::SeqCst),
                Err(e) => eprintln!(
                    "[coworker] no system tray on this desktop ({e}) — closing the window will quit"
                ),
            }

            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building the OpenWorker desktop app")
        .run(|app, event| {
            // Also on Exit: belt-and-suspenders in case a quit path reaches teardown without
            // a preceding ExitRequested (observed with macOS Cmd+Q under the tray setup).
            if matches!(event, RunEvent::ExitRequested { .. } | RunEvent::Exit) {
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
        });
}


#[cfg(test)]
mod tests {
    use super::*;

    /// `COWORKER_SERVER_BIN` short-circuits resolution, so these tests are only meaningful
    /// when it is unset — which it is everywhere except a developer's overridden shell.
    fn env_override_set() -> bool {
        std::env::var_os("COWORKER_SERVER_BIN").is_some()
    }

    /// The Linux .deb/.AppImage layout — binary in `/usr/bin/OpenWorker`, its resources in
    /// `/usr/lib/OpenWorker/` — is reachable ONLY through Tauri's resource dir. Every
    /// exe-relative guess (`<exe dir>/sidecar`, `<exe dir>/../Resources/sidecar`) misses it,
    /// so before the resource-dir candidate existed the packaged Linux app fell all the way
    /// through to the dev-venv path and never started its server.
    #[test]
    fn server_bin_prefers_the_resource_dir() {
        if env_override_set() {
            return;
        }
        let root = std::env::temp_dir().join(format!("ocw-sidecar-{}", Uuid::new_v4().simple()));
        let dir = root.join("sidecar");
        std::fs::create_dir_all(&dir).expect("temp sidecar dir");
        let exe = dir.join(if cfg!(windows) {
            "openworker-server.exe"
        } else {
            "openworker-server"
        });
        std::fs::write(&exe, b"").expect("temp sidecar binary");

        assert_eq!(server_bin(Some(root.clone())), exe);

        let _ = std::fs::remove_dir_all(&root);
    }

    /// A resource dir that holds no sidecar must not win by existing: resolution carries on to
    /// the exe-relative slots and finally the dev venv, which is what `npm run tauri dev` uses.
    #[test]
    fn server_bin_falls_through_an_empty_resource_dir() {
        if env_override_set() {
            return;
        }
        let empty = std::env::temp_dir().join(format!("ocw-empty-{}", Uuid::new_v4().simple()));
        std::fs::create_dir_all(&empty).expect("temp dir");

        let resolved = server_bin(Some(empty.clone()));
        assert!(
            resolved.to_string_lossy().contains(".venv"),
            "expected the dev venv fallback, got {}",
            resolved.display()
        );

        let _ = std::fs::remove_dir_all(&empty);
    }

    /// A keep-awake guard must represent a LIVE hold. `Command::spawn` only proves the binary
    /// could be executed: `systemd-inhibit` itself exits non-zero when it cannot reach logind
    /// (containers, non-systemd sessions, a session bus that isn't there), and a guard built
    /// from that dead child would make the Settings toggle report a hold nobody is holding.
    ///
    /// Holds on either kind of machine: where an inhibitor can be taken this asserts the child
    /// is alive, and where one can't, `start_keep_awake()` must return None rather than a
    /// corpse. (Reported by a review bot; reproduced in a container where systemd-inhibit is
    /// installed but exits 1 with "Failed to connect to bus".)
    #[cfg(not(any(target_os = "macos", target_os = "windows")))]
    #[test]
    fn keep_awake_never_reports_a_dead_inhibitor() {
        if let Some(mut guard) = start_keep_awake() {
            // A real inhibitor lives until we release it; a broken one dies within
            // milliseconds. Checking immediately after spawn cannot tell them apart — the
            // child has not been scheduled yet — so give it time to fail first.
            std::thread::sleep(std::time::Duration::from_millis(500));
            assert!(
                guard.0.try_wait().expect("query the inhibitor").is_none(),
                "start_keep_awake() returned a guard whose inhibitor had already exited"
            );
        }
    }

    /// Voice Input is compiled out on Linux, and the GUI gates the mic button on this flag —
    /// so it must stay false there however the stub engine answers.
    #[test]
    fn voice_input_is_unsupported_on_linux() {
        let (supported, _summary, reason) = voice_input_compatibility();
        if cfg!(target_os = "linux") {
            assert!(!supported);
            assert!(reason.is_some(), "an unsupported platform must say why");
        }
    }
}
