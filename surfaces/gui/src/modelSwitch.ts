// OPE-260: a session left on a model this machine can no longer run (its provider's key
// was removed after the session started) moves to the machine default by itself, with a
// notice saying why. This is the decision alone, kept pure so it can be tested without
// the app: it answers with the model to switch to, or null to leave the session alone.

export type AutoSwitchInput = {
  model: string; // the session's current model
  selectable: string[]; // the machine's selectable list (always carries the default)
  defaultId: string; // the machine default's id
  defaultReady: boolean; // can the machine default run right now?
  personaBound: boolean; // a coworker with its own `models:` list governs its own model
};

export function autoSwitchTarget(a: AutoSwitchInput): string | null {
  // A coworker bound to a model list is handled by that list, never by the default.
  if (a.personaBound) return null;
  // No list yet means the settings have not arrived: judge nothing on an empty list.
  if (!a.model || a.selectable.length === 0) return null;
  // The current model still runs: listed, and not the default while the default is down.
  const current = a.selectable.includes(a.model) && !(a.model === a.defaultId && !a.defaultReady);
  if (current) return null;
  // Somewhere to go: a default that is a different model, listed, and able to run.
  if (!a.defaultId || a.defaultId === a.model || !a.defaultReady) return null;
  if (!a.selectable.includes(a.defaultId)) return null;
  return a.defaultId;
}
