// Launch feature flags.
//
// A flag is read at render time (not import time) so tests and a running build can flip
// it via localStorage without a reload race: `localStorage.setItem(key, "1")` shows the
// feature, `"0"` force-hides it, anything else falls back to the shipped default.

function flag(key: string, fallback: boolean): boolean {
  try {
    const v = localStorage.getItem(key);
    if (v === "1") return true;
    if (v === "0") return false;
  } catch {
    // No storage (jsdom teardown, privacy mode) — ship the default.
  }
  return fallback;
}

/** Personas management is visible by default: the Settings tab and "Manage personas…"
 * menu entry are part of the normal role-selection flow. The flag remains as an escape
 * hatch (`ocw.flag.personas=0`) for tests or temporary rollbacks. */
export const showPersonas = () => flag("ocw.flag.personas", true);
