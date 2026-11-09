// A keyed provider's models on its Models & Keys page (OPE-252, layer 1): what the
// provider's own list says the user's key can call, merged with the curated rows. Three
// sections — New since last check, Recommended, All models — each row a tick (= in the
// composer's picker), the facts we have (context, images), and where they came from
// ("unverified" when nobody has a figure yet). A curated row the key cannot call reads
// "not on this key". A provider without a list falls back to the curated checklist.
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  addModel,
  getProviderCatalog,
  removeModel,
  setDefaultModel,
  type ModelSettings,
  type ProviderCatalog,
  type ProviderCatalogRow,
} from "../../api";
import { ModelChecklist } from "../ModelChecklist";

const FOLD = 12;

// Cloud windows are decimal figures (200,000; 922,000; 1,048,576): show them as the vendor
// writes them, not in the power-of-two steps local servers use.
export function fmtTokens(n: number): string {
  if (n >= 1_000_000) {
    const m = n / 1_000_000;
    const text = m >= 10 || Math.abs(m - Math.round(m)) < 0.06 ? String(Math.round(m)) : m.toFixed(1);
    return `${text}M`;
  }
  return `${Math.round(n / 1000)}K`;
}

function ago(epoch: number | null | undefined, t: (k: string, o?: Record<string, unknown>) => string): string {
  if (!epoch) return "";
  const secs = Math.max(0, Math.floor(Date.now() / 1000 - epoch));
  if (secs < 90) return t("manage.catalog_just_now");
  if (secs < 3600) return t("manage.catalog_min_ago", { count: Math.round(secs / 60) });
  if (secs < 86400) return t("manage.catalog_hours_ago", { count: Math.round(secs / 3600) });
  return t("manage.catalog_days_ago", { count: Math.round(secs / 86400) });
}

export function ProviderModels({
  provider,
  knownProviders,
  suggested,
  settings,
  onChanged,
}: {
  provider: string;
  knownProviders: string[];
  suggested: string[]; // the curated bare ids, for the fallback checklist
  settings: ModelSettings;
  onChanged: (next: { models: string[]; model: string }) => void;
}) {
  const { t } = useTranslation();
  const [catalog, setCatalog] = useState<ProviderCatalog | null>(null);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);
  const [query, setQuery] = useState("");
  const [showAll, setShowAll] = useState(false);
  const [draft, setDraft] = useState("");
  const [adding, setAdding] = useState(false);
  const [addError, setAddError] = useState<string | null>(null);
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [rowError, setRowError] = useState<string | null>(null);

  const load = async (refresh: boolean) => {
    setLoading(true);
    setFailed(false);
    try {
      setCatalog(await getProviderCatalog(provider, refresh));
    } catch {
      setFailed(true);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    setCatalog(null);
    setQuery("");
    setShowAll(false);
    setAddError(null);
    setSuggestions([]);
    void load(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [provider]);

  const rows = catalog?.rows || [];
  const q = query.trim().toLowerCase();
  const sections = useMemo(() => {
    const match = (r: ProviderCatalogRow) => !q || r.id.toLowerCase().includes(q) || r.label.toLowerCase().includes(q);
    const fresh = rows.filter((r) => r.new && match(r));
    const recommended = rows.filter((r) => !r.new && (r.recommended || r.verified === "catalog") && match(r));
    const rest = rows.filter((r) => !r.new && !(r.recommended || r.verified === "catalog") && match(r));
    return { fresh, recommended, rest };
  }, [rows, q]);

  const setRow = (model: string, patch: Partial<ProviderCatalogRow>) =>
    setCatalog((c) => (c ? { ...c, rows: c.rows.map((r) => (r.model === model ? { ...r, ...patch } : r)) } : c));

  const tick = async (row: ProviderCatalogRow, on: boolean) => {
    setRowError(null);
    const res: ModelSettings & { ok: boolean; error?: string } = on ? await addModel(row.model) : await removeModel(row.model);
    if (res.ok) {
      setRow(row.model, { in_picker: on });
      onChanged({ models: res.models, model: res.model });
    } else {
      setRowError(res.error || t("models.add_failed"));
    }
  };

  const makeDefault = async (row: ProviderCatalogRow) => {
    if (!row.in_picker) {
      const added = await addModel(row.model);
      if (!added.ok) {
        setRowError(added.error || t("models.add_failed"));
        return;
      }
      setRow(row.model, { in_picker: true });
    }
    await setDefaultModel(row.model);
    onChanged({ models: settings.models.includes(row.model) ? settings.models : [...settings.models, row.model], model: row.model });
  };

  const prefixed = (bare: string) => (provider === "openai" || bare.includes(":") ? bare : `${provider}:${bare}`);

  const add = async (typed?: string) => {
    const id = (typed ?? draft).trim();
    if (!id || adding) return;
    setAdding(true);
    setAddError(null);
    setSuggestions([]);
    try {
      const res = await addModel(prefixed(id));
      if (res.ok) {
        setDraft("");
        onChanged({ models: res.models, model: res.model });
        const full = prefixed(id);
        if (rows.some((r) => r.model === full)) setRow(full, { in_picker: true });
        else await load(false);
      } else {
        setAddError(res.error || t("models.add_failed"));
        setSuggestions(res.suggestions || []);
      }
    } catch {
      setAddError(t("models.add_failed"));
    } finally {
      setAdding(false);
    }
  };

  if (loading && !catalog) {
    return <p className="text-meta text-muted" data-testid="provider-models-loading">{t("manage.catalog_checking")}</p>;
  }

  if (failed && !catalog) {
    return (
      <p className="text-meta text-warnInk" data-testid="provider-models-failed">
        {t("manage.catalog_unreachable")}{" "}
        <button className="text-muted underline underline-offset-2 hover:text-ink" onClick={() => void load(true)} data-testid="provider-models-retry">
          {t("manage.retry")}
        </button>
      </p>
    );
  }

  if (!catalog || catalog.listing !== "live") {
    // No list to ask (Bedrock, Vertex, Ark, the ChatGPT plan) or the first ask failed:
    // the curated checklist, with the reason above it.
    return (
      <div data-testid="provider-models-fallback">
        <p className="text-meta text-muted mb-2" data-testid="provider-models-reason">
          {catalog?.reason || catalog?.error || ""}
          {catalog?.listing === "error" && (
            <>
              {" "}
              <button className="text-muted underline underline-offset-2 hover:text-ink" onClick={() => void load(true)} data-testid="provider-models-retry">
                {t("manage.retry")}
              </button>
            </>
          )}
        </p>
        <ModelChecklist
          provider={provider}
          knownProviders={knownProviders}
          suggested={suggested}
          curated={settings.models}
          defaultModel={settings.model}
          labels={settings.model_labels}
          onChanged={onChanged}
        />
      </div>
    );
  }

  const rowEl = (row: ProviderCatalogRow) => {
    const isDefault = row.model === settings.model;
    const tags: { key: string; text: string; tone?: string }[] = [];
    if (row.context) tags.push({ key: "ctx", text: fmtTokens(row.context) });
    if (row.vision) tags.push({ key: "img", text: t("manage.catalog_tag_images") });
    if (row.new) tags.push({ key: "new", text: t("manage.catalog_tag_new"), tone: "text-ok" });
    if (row.verified === "unverified") tags.push({ key: "unv", text: t("manage.catalog_tag_unverified"), tone: "text-warnInk" });
    if (row.in_picker && row.on_key === false) tags.push({ key: "nokey", text: t("manage.catalog_tag_not_on_key"), tone: "text-danger" });
    return (
      <div className={"mlist-row" + (row.in_picker ? "" : " off")} key={row.model} data-testid={`catalog-row-${row.id}`}>
        <label className="mlist-main">
          <input
            type="checkbox"
            checked={row.in_picker}
            disabled={isDefault}
            title={isDefault ? t("models.default_locked") : undefined}
            onChange={(e) => void tick(row, e.target.checked)}
          />
          <span className="mlist-name" title={row.model}>{row.label}</span>
          {tags.length > 0 && (
            <span className="text-meta text-muted shrink-0 flex gap-2" data-testid={`catalog-tags-${row.id}`}>
              {tags.map((g) => <span key={g.key} className={g.tone || ""}>{g.text}</span>)}
            </span>
          )}
        </label>
        {isDefault ? (
          <span className="mlist-default">{t("models.default_badge")}</span>
        ) : (
          <button className="mlist-make" onClick={() => void makeDefault(row)}>{t("models.make_default")}</button>
        )}
      </div>
    );
  };

  const section = (title: string, items: ProviderCatalogRow[], testid: string, foldable = false) => {
    if (!items.length) return null;
    const shown = foldable && !showAll && !q ? items.slice(0, FOLD) : items;
    return (
      <div className="mt-3" data-testid={testid}>
        <div className="text-meta font-semibold text-muted uppercase tracking-wide mb-1">{title}</div>
        <div className="mlist">{shown.map(rowEl)}</div>
        {foldable && !q && items.length > FOLD && (
          <button className="text-meta text-muted underline underline-offset-2 hover:text-ink mt-1" onClick={() => setShowAll((v) => !v)} data-testid="catalog-show-all">
            {showAll ? t("manage.show_fewer") : t("manage.catalog_show_all", { count: items.length })}
          </button>
        )}
      </div>
    );
  };

  return (
    <div data-testid="provider-models">
      <div className="flex items-center gap-3 text-meta text-muted" data-testid="catalog-status">
        <span>{t("manage.catalog_checked", { ago: ago(catalog.fetched_at, t), count: catalog.count ?? rows.length })}</span>
        <button className="underline underline-offset-2 hover:text-ink" disabled={loading} onClick={() => void load(true)} data-testid="catalog-refresh">
          {loading ? t("manage.catalog_refreshing") : t("manage.catalog_refresh")}
        </button>
        {rows.length > FOLD && (
          <input
            className="ml-auto bg-transparent border border-line rounded-md px-2 py-0.5 text-meta outline-none"
            placeholder={t("manage.catalog_search")}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            data-testid="catalog-search"
          />
        )}
      </div>
      {catalog.error && <p className="text-meta text-warnInk mt-1" data-testid="catalog-error">{t("manage.catalog_stale", { error: catalog.error })}</p>}
      {catalog.not_on_key.length > 0 && (
        <p className="text-meta text-muted mt-1" data-testid="catalog-not-on-key">{t("manage.catalog_not_on_key_help", { count: catalog.not_on_key.length })}</p>
      )}
      {section(t("manage.catalog_new_since"), sections.fresh, "catalog-new")}
      {section(t("manage.catalog_recommended"), sections.recommended, "catalog-recommended")}
      {section(t("manage.catalog_all"), sections.rest, "catalog-all", true)}
      {rows.some((r) => r.verified === "unverified" && r.in_picker) && (
        <p className="text-meta text-muted mt-2" data-testid="catalog-unverified-help">{t("manage.catalog_unverified_help")}</p>
      )}
      {rowError && <p className="text-meta text-danger mt-1" role="alert" data-testid="catalog-row-error">{rowError}</p>}
      <div className="mlist-add">
        <input
          placeholder={t("models.add_placeholder")}
          value={draft}
          spellCheck={false}
          autoComplete="off"
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && void add()}
          data-testid="catalog-add-input"
        />
        <button className="btn-primary sm" onClick={() => void add()} disabled={!draft.trim() || adding} aria-busy={adding} data-testid="catalog-add">
          {adding ? t("models.adding") : t("models.add_btn")}
        </button>
      </div>
      {addError && (
        <p className="text-meta text-danger" role="alert" data-testid="catalog-add-error">
          {addError}
          {suggestions.length > 0 && (
            <>
              {" "}
              {t("manage.catalog_did_you_mean")}{" "}
              {suggestions.map((s) => (
                <button key={s} className="underline underline-offset-2 mr-2" onClick={() => void add(s)} data-testid={`catalog-suggest-${s}`}>
                  {s}
                </button>
              ))}
            </>
          )}
        </p>
      )}
    </div>
  );
}
