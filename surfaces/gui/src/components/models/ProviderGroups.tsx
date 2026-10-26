// Models & Keys, the provider cards (UX-055): three groups by kind — on your own
// hardware, subscriptions, API keys — connected cards first in each group and filled,
// the rest quieter behind them, a search box over all three, and more air in each card.
// The card grid and the open-in-place detail are the page's existing shape; only the
// order, the grouping and the spacing change here.
import { useState } from "react";
import { useTranslation } from "react-i18next";

import type { ProviderInfo } from "../../api";
import { ProviderMark, type ProviderSetupState } from "../../providers/ProviderSetup";
import { Icon } from "../Icon";

type Kind = "local" | "subscription" | "api_key";
const KINDS: Kind[] = ["local", "subscription", "api_key"];
const FOLD = 6; // cards shown before "Show N more" (two rows of three)

export function providerKind(p: ProviderInfo): Kind {
  return p.kind || "api_key";
}

/** Connected = usable now: a key or sign-in stored, or a keyless local server that
 * answers (the setup state's `keylessOk`). */
export function providerConnected(p: ProviderInfo, keylessOk: Set<string>): boolean {
  if (p.auth === "oauth") return !!p.signed_in;
  if (!p.needs_key) return !!p.alive || keylessOk.has(p.name);
  return p.configured;
}

/** How many of the picker's models belong to a provider. */
export function modelCountFor(name: string, models: string[], known: string[]): number {
  return models.filter((id) => {
    const i = id.indexOf(":");
    const prov = i > 0 && known.includes(id.slice(0, i)) ? id.slice(0, i) : "openai";
    return prov === name;
  }).length;
}

export function ProviderGroups({
  ps,
  tp,
  models,
}: {
  ps: ProviderSetupState;
  tp: string;
  models: string[]; // the picker's models, for the count on each card
}) {
  const { t } = useTranslation();
  const [query, setQuery] = useState("");
  const [expanded, setExpanded] = useState<Set<Kind>>(new Set());
  const known = ps.providers.map((p) => p.name);
  const q = query.trim().toLowerCase();
  const shown = ps.ordered.filter((p) => !q || p.title.toLowerCase().includes(q) || p.name.includes(q));
  // Connected cards are filled; the rest sit on the page background with a quieter
  // title, so the eye lands on what is set up without a divider saying so.
  const card =
    "flex items-center gap-3 rounded-xl border border-line px-4 py-4 min-h-[74px] text-left hover:border-lineStrong transition-colors";

  const cardFor = (p: ProviderInfo) => {
    const connected = providerConnected(p, ps.keylessOk);
    const count = connected ? modelCountFor(p.name, models, known) : 0;
    return (
      <button
        key={p.name}
        className={card + (connected ? " bg-panel" : " bg-transparent")}
        data-testid={`${tp}-provider-${p.name}`}
        onClick={() => ps.openProvider(p.name)}
      >
        <ProviderMark name={p.name} title={p.title} />
        <span className="min-w-0 flex-1">
          <span className={"block text-ui font-semibold leading-tight truncate" + (connected ? "" : " text-muted")}>{p.title}</span>
          {connected && count > 0 ? (
            <span className="block text-meta text-ok font-medium truncate">
              {t(p.needs_key || p.auth === "oauth" ? "provider.connected_count" : "provider.running_count", { count })}
            </span>
          ) : connected && !p.needs_key && p.auth !== "oauth" ? (
            <span className="block text-meta text-ok font-medium truncate">{t("provider.running")}</span>
          ) : !connected && !p.needs_key && p.auth !== "oauth" && p.kind === "local" && p.name !== "ollama" ? (
            <span className="block text-meta text-faint truncate">{t("provider.not_connected")}</span>
          ) : (
            ps.statusFor(p, { lastUsed: true })
          )}
        </span>
        <span className="text-faint text-body">›</span>
      </button>
    );
  };

  return (
    <div data-testid="provider-groups">
      <label className="flex items-center gap-2 rounded-lg border border-lineStrong bg-panel px-3 py-2 mb-5">
        <Icon name="search" size={14} className="text-faint shrink-0" />
        <input
          className="flex-1 bg-transparent outline-none text-ui"
          placeholder={t("manage.search_providers")}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          data-testid="provider-search"
        />
      </label>
      {shown.length === 0 && <p className="text-meta text-faint mb-6" data-testid="provider-search-empty">{t("manage.no_providers_match")}</p>}
      {KINDS.map((kind) => {
        const inKind = shown.filter((p) => providerKind(p) === kind);
        if (inKind.length === 0) return null;
        const connected = inKind.filter((p) => providerConnected(p, ps.keylessOk));
        const rest = inKind.filter((p) => !providerConnected(p, ps.keylessOk));
        const cards = [...connected, ...rest];
        return (
          <section key={kind} className="mb-6" data-testid={`provider-group-${kind}`}>
            <div className="text-label text-faint font-semibold tracking-wide uppercase mb-2.5">{t(`manage.group_${kind}`)}</div>
            {/* A long group (API keys) shows two rows, then a "Show N more" line expands it in place. */}
            <div className="grid grid-cols-2 xl:grid-cols-3 gap-3" data-testid={`provider-grid-${kind}`}>
              {(expanded.has(kind) || q ? cards : cards.slice(0, FOLD)).map(cardFor)}
            </div>
            {!q && cards.length > FOLD && (
              <button
                className="mt-3 text-meta text-muted hover:text-ink"
                onClick={() => setExpanded((prev) => { const next = new Set(prev); next.has(kind) ? next.delete(kind) : next.add(kind); return next; })}
                data-testid={`provider-more-${kind}`}
              >
                {expanded.has(kind) ? t("manage.show_fewer") : t("manage.show_more", { count: cards.length - FOLD })}
              </button>
            )}
          </section>
        );
      })}
    </div>
  );
}
