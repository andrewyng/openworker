// How a model is named on a card (owner-ruled 2026-09-17, same rule as the composer pill,
// UX-048): show the model's NAME; the provider and the raw id live in the hover text.
// Curated labels read "Claude Opus 4.8 · Anthropic"; without one, the id loses its
// "provider:" prefix.

const withoutProvider = (id: string) => (id.includes(":") ? id.split(":").slice(1).join(":") : id);

export function modelName(id: string, labels?: Record<string, string>): string {
  if (!id) return "";
  return (labels?.[id] || "").split(" · ")[0] || withoutProvider(id);
}

/** Hover text: the curated label when there is one, then the exact id the server uses. */
export function modelHover(id: string, labels?: Record<string, string>): string {
  if (!id) return "";
  const label = labels?.[id];
  return label && label !== id ? `${label} (${id})` : id;
}

// One spelling per model (same rule as the server, `coworker/providers/router.py`
// `canonical_model`). A model id with no "provider:" prefix runs on the default
// provider, so "openai:gpt-5.5" and "gpt-5.5" are the same model. The machine's model
// list spells OpenAI models bare; coworker manifests often write "openai:…". Compare
// and store the bare form. Every other prefix ("anthropic:", "ollama:" …) is kept.
export const DEFAULT_PROVIDER = "openai";

export function canonicalModel(id: string): string {
  const m = (id || "").trim();
  const prefix = `${DEFAULT_PROVIDER}:`;
  return m.startsWith(prefix) && m.length > prefix.length ? m.slice(prefix.length) : m;
}

export function sameModel(a: string, b: string): boolean {
  return canonicalModel(a) === canonicalModel(b);
}

/** The list in canonical form, first spelling of each model kept, order kept. */
export function canonicalModels(ids: string[]): string[] {
  return Array.from(new Set(ids.map(canonicalModel).filter(Boolean)));
}

/**
 * The composer's model choice for a coworker with a `models:` list (connectors-across-
 * machines §4). `picker` is what the picker offers (the coworker's list, or the machine's
 * when it has none); `unavailable` the list entries this machine can't run; `bind` the
 * model a fresh draft should switch to, or null to keep `current`.
 */
export function coworkerModels(
  personaModels: string[],
  selectable: string[],
  current: string,
): { picker: string[]; unavailable: string[]; bind: string | null } {
  const listed = canonicalModels(personaModels);
  if (!listed.length) return { picker: selectable, unavailable: [], bind: null };
  const runnable = new Set(selectable.map(canonicalModel));
  const unavailable = listed.filter((m) => !runnable.has(m));
  const bind = listed.includes(canonicalModel(current))
    ? null
    : (listed.find((m) => runnable.has(m)) ?? listed[0]);
  return { picker: listed, unavailable, bind };
}
