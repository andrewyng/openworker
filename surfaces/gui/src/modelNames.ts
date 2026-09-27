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
