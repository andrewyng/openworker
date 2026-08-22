// A persona is "project-scoped" when its sessions belong to a folder the user picks: gated on
// choosing one, and grouped under it in the sidebar.
//
// This used to mean "code-family only" (§16, 2026-07-03) — knowledge personas ran on a
// transparent per-conversation scratch dir and never gated. Reversed by owner ask: with several
// personas installed, every one of them except the chat-shaped Fast Chat does work that belongs
// to a project, and only Builder had the structure to show it. The persona now DECLARES it
// (`projects:` in the manifest), so the answer travels with the persona instead of being
// inferred from a family that also governs unrelated engine behaviour.
export function isProjectScoped(p?: {
  workspace?: string;
  family?: string;
  projects?: boolean;
}): boolean {
  // An older sidecar sends no `projects`; fall back to the pre-§16-reversal rule rather than
  // gating everything on a field it does not know about.
  return p?.projects ?? p?.family === "code";
}

// Persona naming: the product is "OpenWorker"; the personas are a "Coworker" family — Coworker
// (general), Code Coworker, Ops Coworker. In lists/chrome we use the SHORT label (Coworker / Code /
// Ops); the persona detail page uses the FULL family name. Backend names are left untouched (the
// API + tests keep "OpenWorker" / "Ops Coworker"); this is purely the display layer.

// Short label for the sidebar + top bar: "Coworker" / "Code" / "Ops" / "Chat".
export function shortPersonaName(name?: string, id?: string): string {
  if (id === "cowork") return "Coworker";
  const n = (name || id || "").trim();
  return n.replace(/\s*coworker$/i, "").trim() || n;
}

// Full family name for the persona detail page: "Coworker" / "Code Coworker" / "Ops Coworker".
// Chat isn't a coworker — left as-is.
export function fullPersonaName(name?: string, id?: string): string {
  if (id === "cowork") return "Coworker";
  const n = (name || id || "").trim();
  if (id === "chat" || !n) return n;
  return /coworker$/i.test(n) ? n : `${n} Coworker`;
}
