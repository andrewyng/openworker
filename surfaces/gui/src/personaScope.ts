// A persona is "project-scoped" when it declares requires_folder: an explicit directory the
// user picks, sessions grouped by project in the sidebar. Everything else runs on a transparent
// per-conversation scratch dir, with real folders added as roots when needed — no folder gate.
// (The old family/workspace-enum pair collapsed into this trait; workspace-scratch-design.md.)
export function isProjectScoped(p?: { requires_folder?: boolean }): boolean {
  return p?.requires_folder === true;
}

// Persona naming: the product is "OpenWorker"; the personas are a "Coworker" family — Coworker
// (general), Code Coworker, Ops Coworker. In lists/chrome we use the SHORT label (Coworker / Code /
// Ops); the persona detail page uses the FULL family name. Backend names are left untouched (the
// API + tests keep "OpenWorker" / "Ops Coworker"); this is purely the display layer.

/** The default coworker's id. Its backend name is "OpenWorker"; on screen it is "Coworker". */
export const DEFAULT_PERSONA_ID = "cowork";

/** A coworker's name where coworkers are listed side by side (Settings ▸ Coworkers, Usage's
 * tables, its picker, Ratings): the default one is "Coworker (default)", as "Coworker" alone
 * reads like the column's heading there; every other coworker keeps its own name (T3-15 e). */
export function listPersonaName(name: string | null | undefined, id: string | null | undefined,
                                t: (k: string) => string): string {
  if (id === DEFAULT_PERSONA_ID) return t("personas.default_in_list");
  return (name || id || "").trim();
}

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
