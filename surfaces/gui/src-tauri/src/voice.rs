use std::process::Command;
use std::sync::Arc;

use ocw_stt::{Dictation, DownloadProgress};
use serde::Serialize;
use tauri::Emitter;

// -- local dictation ---------------------------------------------------------------------------
// The actual microphone/model code lives in the Tauri-free `ocw-stt` crate. This shell owns the
// macOS permission prompt and translates the reusable API into React-friendly Tauri commands.

#[derive(Clone, Serialize)]
pub(super) struct VoiceInputStatus {
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
    (
        false,
        format!("{} · {}", std::env::consts::OS, std::env::consts::ARCH),
        Some("Voice Input is currently supported on macOS and Windows.".to_owned()),
    )
}

#[tauri::command]
pub(super) fn get_dictation_status(state: tauri::State<Arc<Dictation>>) -> VoiceInputStatus {
    voice_input_status(&state)
}

#[tauri::command]
pub(super) async fn start_dictation(
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
pub(super) async fn stop_dictation(
    state: tauri::State<'_, Arc<Dictation>>,
) -> Result<String, String> {
    let dictation = state.inner().clone();
    tauri::async_runtime::spawn_blocking(move || dictation.stop_and_transcribe())
        .await
        .map_err(|e| format!("Dictation stopped unexpectedly: {e}"))?
}

#[tauri::command]
pub(super) fn cancel_dictation(state: tauri::State<Arc<Dictation>>) {
    state.cancel();
}

#[tauri::command]
pub(super) async fn download_dictation_model(
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
pub(super) fn cancel_dictation_model_download(state: tauri::State<Arc<Dictation>>) {
    state.cancel_model_download();
}

#[tauri::command]
pub(super) async fn verify_dictation_model(
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
pub(super) fn mark_dictation_test_passed(
    state: tauri::State<Arc<Dictation>>,
) -> Result<VoiceInputStatus, String> {
    state.mark_test_passed()?;
    Ok(voice_input_status(&state))
}

#[tauri::command]
pub(super) fn delete_dictation_model(
    state: tauri::State<Arc<Dictation>>,
) -> Result<VoiceInputStatus, String> {
    state.delete_default_model()?;
    Ok(voice_input_status(&state))
}

/// Instantaneous mic loudness (0..1) while a dictation is recording — the composer polls
/// this to draw a real input-driven waveform instead of decorative bars (owner catch,
/// DMG #28 walkthrough).
#[tauri::command]
pub(super) fn dictation_level(state: tauri::State<Arc<Dictation>>) -> f32 {
    state.input_level()
}
