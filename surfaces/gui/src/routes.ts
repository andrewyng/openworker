// Hash deep links, v1 (owner-ruled 2026-08-30: hash-based; the full routing
// scheme lands with the Settings rehaul). The hash is captured ONCE at load —
// the app rewrites it as the session changes, so inbound intent must be read
// before the first rewrite.
//
//   #/approve/XXXX-XXXX      → open Settings ▸ Machines with the code prefilled
//                              (what `openworker join` prints on hosted)
//   #/s/{sessionId}          → land on that session (bookmark/refresh)
//   #/settings/{page}?m={id} → open that Settings page; ?m= is the machine
//                              scope for machine-scoped pages (UX-046)

const initialHash = typeof window !== "undefined" ? window.location.hash : "";

export const initialSessionId =
  initialHash.match(/^#\/s\/([\w-]+)/)?.[1] ?? "";

// URL slugs are the pages' NAMES (general, coworkers…), decoupled from the
// internal tab keys ("appearance" predates the General rename; "personas"
// predates Coworkers) — renaming a label must never break a bookmark.
const TAB_BY_SLUG: Record<string, string> = {
  general: "appearance",
  voice: "voice",
  account: "account",
  models: "models",
  context: "context",
  skills: "skills",
  memory: "memory",
  coworkers: "personas",
  machines: "machines",
  connectors: "connectors",
  // UX-049: per-connector glance pages.
  slack: "slack",
  github: "github",
};
const SLUG_BY_TAB: Record<string, string> = Object.fromEntries(
  Object.entries(TAB_BY_SLUG).map(([slug, tab]) => [tab, slug]),
);

/** The Settings page a #/settings deep link asks for ("" = none). Unknown
 * slugs open Settings on its first page rather than dead-ending the link. */
export const initialSettingsTab = (() => {
  const m = initialHash.match(/^#\/settings(?:\/([\w-]+))?/);
  if (!m) return "";
  return TAB_BY_SLUG[m[1] ?? "general"] ?? "appearance";
})();

// A settings deep link owns the URL until the Settings page reflects itself —
// the app-level session reflection fires first (surface still defaults to
// "session" at mount) and would overwrite the link before it is read.
let settingsLinkPending = initialSettingsTab !== "";

/** The ?m= machine scope, read LIVE from the current hash (not the boot one):
 * reflectSettings keeps it current, so a Settings remount re-reads the scope
 * it was already showing instead of resetting to the default. */
export function settingsMachineParam(): string {
  // `[\w:-]` covers the union view's `cloud:` id prefix.
  try {
    return window.location.hash.match(/^#\/settings\/[\w-]+\?m=([\w:-]+)/)?.[1] ?? "";
  } catch {
    return "";
  }
}

/** Reflect the open Settings page (and its machine scope, when the page has
 * one) in the URL. replaceState, same as sessions — tab browsing must not
 * pile up history entries. */
export function reflectSettings(tab: string, machineId?: string | null): void {
  if (hasPendingApproval()) return;
  settingsLinkPending = false;
  const slug = SLUG_BY_TAB[tab] ?? "general";
  try {
    window.history.replaceState(
      null,
      "",
      `#/settings/${slug}${machineId ? `?m=${machineId}` : ""}`,
    );
  } catch {
    /* file:// or restricted contexts — the URL is a convenience */
  }
}

let approvalCode = initialHash.match(/^#\/approve\/([A-Za-z0-9-]+)/)?.[1] ?? "";

/** The deep-linked approval code, if any. Reading does NOT consume — React
 * StrictMode runs state initializers twice in dev, so a consuming read here
 * would hand the second run an empty string. Consume from an effect. */
export function peekApprovalCode(): string {
  return approvalCode;
}

/** Mark the deep link handled — reopening the Machines page later must not
 * replay it. Safe to call twice (effects also double-run in strict dev).
 * The hash moves off the spent code so a refresh lands on the Machines page
 * instead of replaying an approval that was already decided. */
export function consumeApprovalCode(): void {
  if (!approvalCode) return;
  approvalCode = "";
  try {
    window.history.replaceState(null, "", "#/settings/machines");
  } catch {
    /* convenience only */
  }
}

export function hasPendingApproval(): boolean {
  return approvalCode !== "";
}

/** Reflect the active session in the URL so refresh and bookmarks return
 * here. replaceState: session switching must not pile up history entries. */
export function reflectSession(sessionId: string): void {
  if (!sessionId || hasPendingApproval() || settingsLinkPending) return;
  try {
    window.history.replaceState(null, "", `#/s/${sessionId}`);
  } catch {
    /* file:// or restricted contexts — the URL is a convenience */
  }
}
