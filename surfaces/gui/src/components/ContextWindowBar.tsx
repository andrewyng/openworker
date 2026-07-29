/** Context-window usage bar — Ollama only.
 *
 * Every other provider either hides its true context cap or we'd have to hardcode a per-model
 * table to guess it (and it'd go stale). Ollama's native API exposes the real configured number,
 * so this is the one provider where the bar shows a real fraction rather than a guess.
 *
 * `usedTokens` is the LATEST turn's `total_tokens` — since the app resends the full message
 * history on every turn, that number already reflects current context occupancy (system +
 * history + last message + reply), not just the newest message.
 */
export function ContextWindowBar({
  usedTokens,
  maxTokens,
}: {
  usedTokens: number;
  maxTokens: number;
}) {
  const pct = Math.min(100, Math.round((usedTokens / maxTokens) * 100));
  const warn = pct >= 75 && pct < 90;
  const danger = pct >= 90;
  return (
    <div
      className="flex items-center gap-2 px-3 py-1 text-[11px] text-muted"
      data-testid="context-window-bar"
      title={`${usedTokens.toLocaleString()} / ${maxTokens.toLocaleString()} tokens (context window)`}
    >
      <span className="shrink-0">Context</span>
      <div className="flex-1 h-1.5 rounded-full bg-line overflow-hidden">
        <div
          className={
            "h-full rounded-full transition-[width] " +
            (danger ? "bg-red-500" : warn ? "bg-amber-500" : "bg-accent")
          }
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="shrink-0 tabular-nums">
        {usedTokens.toLocaleString()} / {maxTokens.toLocaleString()}
      </span>
    </div>
  );
}
