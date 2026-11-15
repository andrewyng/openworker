import { useTranslation } from "react-i18next";
import type { ContextInfo, SessionUsage } from "../types";
import { formatTokens } from "../usage";

// The Context section (FB-015): how full the model's window is and what fills it.
// The composer's ring (UX-048) stays the glanceable signal; this is the expanded truth.
// The category split is a server-side estimate, so segments are drawn as PROPORTION of
// the reported total, never presented as an exact ledger (the body says "estimated").

export function contextPct(info: ContextInfo): number | null {
  if (!info.window || info.window <= 0) return null;
  return Math.min(100, Math.round((info.used / info.window) * 100));
}

// Collapsed-header value: "34%" plus a micro meter (or the token count when the
// model's window is unknown — same contract as the composer ring).
export function ContextSummary({ info }: { info: ContextInfo }) {
  const pct = contextPct(info);
  if (pct === null) return <>{formatTokens(info.used)}</>;
  return (
    <span className="ctx-sum" data-testid="ctx-summary">
      <span>{pct}%</span>
      <span className={"ctx-meter" + (pct >= 80 ? " warn" : "")}>
        <span className="ctx-meter-fill" style={{ width: `${pct}%` }} />
      </span>
    </span>
  );
}

const CATS = ["system", "tools", "messages"] as const;

export function ContextPanel({ info, usage }: { info: ContextInfo; usage?: SessionUsage }) {
  const { t } = useTranslation();
  const pct = contextPct(info);
  // Scale the estimated categories to the reported total so the bar never lies about
  // the whole even when the parts are approximate.
  const estSum = CATS.reduce((n, c) => n + (info.breakdown[c] || 0), 0) || 1;
  const share = (c: (typeof CATS)[number]) =>
    Math.round(((info.breakdown[c] || 0) / estSum) * info.used);
  const models = Object.entries(usage?.byModel || {});
  return (
    <div className="ctx-panel" data-testid="ctx-panel">
      <div className="ctx-head">
        <span className="ctx-used">
          {info.window
            ? t("rail.ctx_used", { used: formatTokens(info.used), window: formatTokens(info.window) })
            : t("rail.ctx_used_no_window", { used: formatTokens(info.used) })}
        </span>
        {pct !== null && <span className="ctx-pct">{t("rail.ctx_free", { pct: 100 - pct })}</span>}
      </div>
      {info.window ? (
        <div className="ctx-bar" role="img" aria-label={t("rail.ctx_bar_label")}>
          {CATS.map((c) => (
            <span
              key={c}
              className={`ctx-seg ctx-seg-${c}`}
              style={{ width: `${Math.min(100, (share(c) / info.window!) * 100)}%` }}
            />
          ))}
        </div>
      ) : null}
      <div className="ctx-rows">
        {CATS.map((c) => (
          <div className="ctx-row" key={c}>
            <span className={`ctx-dot ctx-seg-${c}`} />
            <span className="ctx-label">{t(`rail.ctx_${c}`)}</span>
            <span className="ctx-val">{t("rail.ctx_approx", { tokens: formatTokens(share(c)) })}</span>
          </div>
        ))}
        {info.window ? (
          <div className="ctx-row">
            <span className="ctx-dot ctx-seg-free" />
            <span className="ctx-label">{t("rail.ctx_free_label")}</span>
            <span className="ctx-val">{formatTokens(Math.max(0, info.window - info.used))}</span>
          </div>
        ) : null}
      </div>
      {models.length > 0 && (
        <div className="ctx-models">
          {models.map(([model, u]) => (
            <div className="ctx-row" key={model}>
              <span className="ctx-label ctx-model" title={model}>{model}</span>
              <span className="ctx-val">
                {t("rail.ctx_model_io", {
                  in: formatTokens(u.input + u.cache_read + u.cache_write),
                  out: formatTokens(u.output),
                })}
              </span>
            </div>
          ))}
        </div>
      )}
      {info.compaction && (
        <div className="ctx-note" data-testid="ctx-compacted">
          {t("rail.ctx_compacted", { count: info.compaction.messages || 0 })}
        </div>
      )}
    </div>
  );
}
