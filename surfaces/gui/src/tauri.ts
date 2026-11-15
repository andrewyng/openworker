// Thin bridge to the Tauri desktop shell. In the browser these are inert (isTauri() === false),
// so the SPA stays a single codebase. We use the injected `window.__TAURI__` global (the shell
// sets `withGlobalTauri`) instead of the @tauri-apps npm packages, so the browser build needs
// no Tauri dependencies.

export const isTauri = (): boolean =>
  typeof (globalThis as any).__TAURI__ !== "undefined";

import type { TFunction } from "i18next";

export type PlatformOS = "macos" | "windows" | "linux";

let cachedBackendPlatform: PlatformOS | null = null;
export function setBackendPlatform(os: string | null | undefined): void {
  if (!os) return;
  const lower = os.toLowerCase();
  if (lower === "win32" || lower === "windows") cachedBackendPlatform = "windows";
  else if (lower === "darwin" || lower === "macos") cachedBackendPlatform = "macos";
  else if (lower.startsWith("linux")) cachedBackendPlatform = "linux";
}

// "macos" | "windows" | "linux" — injected by the shell (std::env::consts::OS) before the
// SPA loads; userAgent fallback covers browser dev. The macOS overlay-titlebar layout (and
// its traffic-light compensations) must NEVER apply on Windows, which keeps its native
// title bar (alignment bug, caught on Windows 2026-07-21).
export const platformOS = (): PlatformOS => {
  if (cachedBackendPlatform) return cachedBackendPlatform;
  const injected = (globalThis as any).__OCW_PLATFORM__;
  if (typeof injected === "string" && injected) {
    const lower = injected.toLowerCase();
    if (lower === "win32" || lower === "windows") return "windows";
    if (lower === "darwin" || lower === "macos") return "macos";
    if (lower.startsWith("linux")) return "linux";
  }
  if (typeof navigator !== "undefined") {
    const ua = navigator.userAgent || "";
    const platform = (navigator as any).userAgentData?.platform || navigator.platform || "";
    if (/win/i.test(platform) || /win/i.test(ua)) return "windows";
    if (/mac/i.test(platform) || /mac/i.test(ua)) return "macos";
    if (/linux/i.test(platform) || /linux/i.test(ua)) return "linux";
  }
  return "linux";
};

export const isWindows = (): boolean => platformOS() === "windows";
export const isMac = (): boolean => platformOS() === "macos";
export const isLinux = (): boolean => platformOS() === "linux";

/** User-facing label for the local machine (e.g. "This PC" on Windows, "This Mac" on macOS, "This Linux PC" on Linux). */
export function thisMachineName(t?: TFunction | ((key: string) => string)): string {
  const os = platformOS();
  if (t) {
    if (os === "windows") return (t as any)("machines.this_pc");
    if (os === "linux") return (t as any)("machines.this_linux");
    return (t as any)("machines.this_mac");
  }
  if (os === "windows") return "This PC";
  if (os === "linux") return "This Linux PC";
  return "This Mac";
}

export function thisMachineOption(t?: TFunction | ((key: string) => string)): string {
  const os = platformOS();
  if (t) {
    if (os === "windows") return (t as any)("settingsx.this_pc_option");
    if (os === "linux") return (t as any)("settingsx.this_linux_option");
    return (t as any)("settingsx.this_mac_option");
  }
  if (os === "windows") return "⌂ This PC";
  if (os === "linux") return "⌂ This Linux PC";
  return "⌂ This Mac";
}

export function onMachineThisMachine(t?: TFunction | ((key: string) => string)): string {
  const os = platformOS();
  if (t) {
    if (os === "windows") return (t as any)("onmachine.this_pc");
    if (os === "linux") return (t as any)("onmachine.this_linux");
    return (t as any)("onmachine.this_mac");
  }
  if (os === "windows") return "This PC";
  if (os === "linux") return "This Linux PC";
  return "This Mac";
}

// macOS major version (26 for Tahoe), injected by the shell; 0 when unknown (browser dev).
// macOS 26 places the traffic lights itself, higher than the position the shell asks for,
// so the top strip lays out differently there (styles.css, `.macos-26`).
export const macosMajor = (): number => {
  const injected = (globalThis as any).__OCW_OS_VERSION__;
  const major = typeof injected === "string" ? parseInt(injected.split(".")[0], 10) : NaN;
  return Number.isFinite(major) ? major : 0;
};

export type DictationStatus = {
  recording: boolean;
  model_installed: boolean;
  model_verified: boolean;
  test_passed: boolean;
  download_in_progress: boolean;
  model_name: string;
  model_bytes: number;
  supported: boolean;
  device_summary: string;
  compatibility_reason: string | null;
};

export type DictationDownloadProgress = {
  downloaded_bytes: number;
  total_bytes: number;
};

const invoke = async <T>(cmd: string, args?: Record<string, unknown>): Promise<T | null> => {
  const tauri = (globalThis as any).__TAURI__;
  if (!tauri?.core?.invoke) return null;
  try {
    return (await tauri.core.invoke(cmd, args)) as T;
  } catch {
    return null;
  }
};

const invokeStrict = async <T>(cmd: string, args?: Record<string, unknown>): Promise<T> => {
  const tauri = (globalThis as any).__TAURI__;
  if (!tauri?.core?.invoke) throw new Error("This feature is available in the desktop app.");
  return (await tauri.core.invoke(cmd, args)) as T;
};

/** Open the native macOS folder picker (Tauri only). Returns the chosen path, or null. */
export async function pickFolder(): Promise<string | null> {
  const path = await invoke<string>("pick_folder");
  return typeof path === "string" && path ? path : null;
}

/** The folder picker that works EVERYWHERE: Tauri's native dialog in the desktop shell, else the
 * sidecar-opened OS dialog (the sidecar is local, so the browser GUI still gets a real picker —
 * owner report 2026-07-04: "Browse" was desktop-only and the browser had paste-a-path only). */
export async function chooseFolder(): Promise<string | null> {
  if (isTauri()) return pickFolder();
  const { pickFolderViaServer } = await import("./api");
  return pickFolderViaServer();
}

/** Open-at-login (macOS LaunchAgent). */
export const getAutostart = () => invoke<boolean>("get_autostart");
export const setAutostart = (enabled: boolean) => invoke<boolean>("set_autostart", { enabled });

/** Keep this system awake so scheduled tasks fire while idle (caffeinate on macOS,
 * SetThreadExecutionState on Windows). Persists across restarts. */
export const getKeepAwake = () => invoke<boolean>("get_keep_awake");
export const setKeepAwake = (enabled: boolean) => invoke<boolean>("set_keep_awake", { enabled });

/** Begin native window dragging from a custom title/header region. */
export const startWindowDrag = () => invoke<boolean>("start_window_drag");

// Local dictation is native-only. The browser build deliberately keeps this unavailable rather
// than silently sending microphone audio to a server.
export const getDictationStatus = () => invoke<DictationStatus>("get_dictation_status");
/** Instantaneous mic loudness 0..1 while recording (0 otherwise) — drives the composer's
 * live waveform. Cheap; poll at ~10Hz. */
export const getDictationLevel = () => invoke<number>("dictation_level");
export const startDictation = () => invokeStrict<DictationStatus>("start_dictation");
export const stopDictation = () => invokeStrict<string>("stop_dictation");
export const cancelDictation = () => invokeStrict<void>("cancel_dictation");
export const downloadDictationModel = () => invokeStrict<DictationStatus>("download_dictation_model");
export const cancelDictationModelDownload = () => invokeStrict<void>("cancel_dictation_model_download");
export const verifyDictationModel = () => invokeStrict<DictationStatus>("verify_dictation_model");
export const markDictationTestPassed = () => invokeStrict<DictationStatus>("mark_dictation_test_passed");
export const deleteDictationModel = () => invokeStrict<DictationStatus>("delete_dictation_model");

export async function listenDictationDownloadProgress(
  handler: (progress: DictationDownloadProgress) => void,
): Promise<() => void> {
  const listen = (globalThis as any).__TAURI__?.event?.listen;
  if (!listen) return () => {};
  return (await listen("dictation-download-progress", (event: { payload: DictationDownloadProgress }) => {
    handler(event.payload);
  })) as () => void;
}

// --- Auto-update (desktop only; browser builds see null / throw) -----------------

export type UpdateInfo = { version: string; notes: string };

/** Ask the shell whether a newer release exists (verified manifest; see lib.rs).
 * null = up to date, unreachable endpoint, or not the desktop app. */
export const checkForUpdate = () => invoke<UpdateInfo | null>("check_for_update");

/** Pre-fetch + verify the update bytes in the background so the install is instant.
 * The shell caches them keyed by version; calling again for the same version is a no-op.
 * Rejects when there is no update or the download fails — callers fall back to the
 * download-on-install path. */
export const downloadUpdate = () => invokeStrict<void>("download_update");

/** Drop the pre-fetched update bundle (freed on "Later" so a dismissed release
 * doesn't pin tens of MB for a weeks-long app run). */
export const clearPendingUpdate = () => invokeStrict<void>("clear_pending_update");

/** Install the update (pre-fetched bytes when available, else download + verify now),
 * then relaunch. Resolves only on failure paths (success restarts the process on macOS;
 * Windows hands off to the installer). */
export const installUpdate = () => invokeStrict<void>("install_update");

/** Open a URL in the user's browser: the Tauri opener plugin in the desktop app (wired in
 * src-tauri, `opener:default`), `window.open` in a browser. */
export function openExternal(url: string): void {
  const opener = (globalThis as any).__TAURI__?.opener;
  if (opener?.openUrl) {
    opener.openUrl(url).catch(() => window.open(url, "_blank", "noopener,noreferrer"));
    return;
  }
  window.open(url, "_blank", "noopener,noreferrer");
}
