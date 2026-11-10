// A keyed provider's models on its Models & Keys page (OPE-252, layer 1, design of
// 2026-11-10): exactly what the provider's own list says this key can call. One list of the
// chat models, each a tick (= in the composer's picker) with a line of plain facts under its
// name (context, images in, tools…); the listed models that are not chat models sit behind
// one collapsed line with the reason and a "Use anyway" that moves one into the list with a
// "Your pick · untested" badge and Undo. The name is the one way into a model's settings.
// Nothing on the page says where a fact came from. A provider without a list falls back to
// the curated checklist; a first ask that fails shows the error and Retry only.
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

// A window under this many tokens is worth a second look for a coworker's long tasks.
const SHORT_CONTEXT = 32_768;

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

function fmtDate(iso: string, lang: string): string {
  const d = new Date(iso.length === 10 ? iso + "T00:00:00Z" : iso);
  if (Number.isNaN(d.getTime())) return iso;
  // "11 Dec 2026" in English (the design's form), the locale's own order elsewhere.
  return d.toLocaleDateString(lang.startsWith("en") ? "en-GB" : lang, { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
}

type Fact = { key: string; text: string; tone?: "amber" | "ok" | "danger" };

export function ProviderModels({
  provider,
  providerTitle,
  knownProviders,
  suggested,
  settings,
  onChanged,
  onConfigure,
}: {
  provider: string;
  providerTitle?: string; // how the page names the provider ("Cohere"); the id when absent
  knownProviders: string[];
  suggested: string[]; // the curated bare ids, for the fallback checklist
  settings: ModelSettings;
  onChanged: (next: { models: string[]; model: string }) => void;
  // Opens the model's settings (its window, for one). The model's name is the one way there,
  // on every row (owner, 2026-11-10: two paths were confusing).
  onConfigure?: (model: string) => void;
}) {
  const { t, i18n } = useTranslation();
  const title = providerTitle || provider;
  const [catalog, setCatalog] = useState<ProviderCatalog | null>(null);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);
  const [query, setQuery] = useState("");
  const [showAside, setShowAside] = useState(false);
  const [why, setWhy] = useState<string | null>(null); // the set-aside row whose explanation is open
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
    setShowAside(false);
    setWhy(null);
    setAddError(null);
    setSuggestions([]);
    void load(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [provider]);

  const q = query.trim().toLowerCase();
  const match = (r: ProviderCatalogRow) => !q || r.id.toLowerCase().includes(q) || r.label.toLowerCase().includes(q);
  const offered = useMemo(() => (catalog?.rows || []).filter(match), [catalog, q]); // eslint-disable-line react-hooks/exhaustive-deps
  const aside = useMemo(() => (catalog?.set_aside || []).filter(match), [catalog, q]); // eslint-disable-line react-hooks/exhaustive-deps

  // -- the reason a row is set aside, and the words the explanation uses -------------------
  const reasonFor = (row: ProviderCatalogRow): string => {
    const phrase = t(`manage.catalog_kind_${row.kind}`, {
      detail: row.detail || "",
      date: row.retires_at ? fmtDate(row.retires_at, i18n.language) : "",
      defaultValue: t("manage.catalog_kind_other"),
    });
    if (row.kind_source !== "name") return phrase;
    return t("manage.catalog_kind_likely", { phrase: phrase.charAt(0).toLowerCase() + phrase.slice(1) });
  };
  const kindWord = (row: ProviderCatalogRow): string =>
    t(`manage.catalog_kindword_${row.kind}`, { detail: row.detail || "", defaultValue: t("manage.catalog_kindword_other") });

  // -- actions -------------------------------------------------------------------------------
  const apply = (res: ModelSettings & { ok: boolean; error?: string }) => {
    if (res.ok) onChanged({ models: res.models, model: res.model });
    else setRowError(res.error || t("models.add_failed"));
    return res.ok;
  };

  const tick = async (row: ProviderCatalogRow, on: boolean) => {
    setRowError(null);
    const res: ModelSettings & { ok: boolean; error?: string; reason?: string } = on ? await addModel(row.model) : await removeModel(row.model);
    // the row's facts, badge and place come from the backend; a tick the provider refused
    // moves the row to the set-aside group
    if (apply(res) || res.reason === "unavailable") await load(false);
  };

  const useAnyway = async (row: ProviderCatalogRow) => {
    setRowError(null);
    setWhy(null);
    if (apply(await addModel(row.model, true))) await load(false);
  };

  const undo = async (row: ProviderCatalogRow) => {
    setRowError(null);
    if (apply(await removeModel(row.model))) await load(false);
  };

  const makeDefault = async (row: ProviderCatalogRow) => {
    if (!row.in_picker) {
      const added = await addModel(row.model);
      if (!added.ok) {
        setRowError(added.error || t("models.add_failed"));
        return;
      }
    }
    await setDefaultModel(row.model);
    onChanged({ models: settings.models.includes(row.model) ? settings.models : [...settings.models, row.model], model: row.model });
    await load(false);
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
        await load(false);
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

  // -- the states before a list ------------------------------------------------------------
  if (loading && !catalog) {
    return <p className="text-meta text-muted" data-testid="provider-models-loading">{t("manage.catalog_checking")}</p>;
  }

  const retry = (
    <button className="text-muted underline underline-offset-2 hover:text-ink" onClick={() => void load(true)} data-testid="provider-models-retry">
      {t("manage.retry")}
    </button>
  );

  if (failed && !catalog) {
    return (
      <p className="text-meta text-warnInk" data-testid="provider-models-failed">
        {t("manage.catalog_unreachable")} {retry}
      </p>
    );
  }

  const addBox = (
    <div className="mt-4" data-testid="catalog-add-box">
      <p className="text-meta text-muted mb-1">{t("manage.catalog_add_help", { provider: title })}</p>
      <div className="mlist-add !mt-0">
        <input
          placeholder={t("manage.catalog_add_placeholder")}
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

  if (!catalog || catalog.listing === "none") {
    // No list the app can ask yet (Bedrock, Vertex, the Ark agent plan), or no key: the
    // reason, then the curated checklist of what a key unlocks.
    return (
      <div data-testid="provider-models-fallback">
        <p className="text-meta text-muted mb-2" data-testid="provider-models-reason">{catalog?.reason || ""}</p>
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

  if (catalog.listing === "error") {
    // The first ask failed and nothing is cached: the error and Retry. Never the reviewed
    // rows here (nobody checked them against this key); the Add box still works.
    return (
      <div data-testid="provider-models-fallback">
        <p className="text-meta text-warnInk mb-2" data-testid="provider-models-reason">
          {catalog.error || t("manage.catalog_unreachable")} {retry}
        </p>
        {addBox}
      </div>
    );
  }

  // -- rows ----------------------------------------------------------------------------------
  const factsOf = (row: ProviderCatalogRow): Fact[] => {
    const facts: Fact[] = [];
    if (row.new) facts.push({ key: "new", text: t("manage.catalog_fact_new"), tone: "ok" });
    if (row.context) {
      if (row.context < SHORT_CONTEXT) facts.push({ key: "ctx", text: t("manage.catalog_fact_short_context", { size: fmtTokens(row.context) }), tone: "amber" });
      else facts.push({ key: "ctx", text: t("manage.catalog_fact_context", { size: fmtTokens(row.context) }) });
    }
    if (row.max_output) facts.push({ key: "out", text: t("manage.catalog_fact_output", { size: fmtTokens(row.max_output) }) });
    if (row.input_image) facts.push({ key: "img", text: t("manage.catalog_fact_images") });
    if (row.input_pdf) facts.push({ key: "pdf", text: t("manage.catalog_fact_pdf") });
    if (row.tools === false) facts.push({ key: "tools", text: t("manage.catalog_fact_no_tools"), tone: "amber" });
    else if (row.tools) facts.push({ key: "tools", text: t("manage.catalog_fact_tools") });
    if (row.reasoning) facts.push({ key: "reas", text: t("manage.catalog_fact_reasoning") });
    if (row.retires_at) facts.push({ key: "ret", text: t("manage.catalog_fact_retires", { date: fmtDate(row.retires_at, i18n.language) }), tone: "amber" });
    if (row.in_picker && row.on_key === false) facts.push({ key: "nokey", text: t("manage.catalog_fact_not_on_key"), tone: "danger" });
    return facts;
  };

  const factsLine = (facts: Fact[], testid: string) =>
    facts.length > 0 ? (
      <span className="mlist-facts" data-testid={testid}>
        {facts.map((f, i) => (
          <span key={f.key}>
            {i > 0 && <span className="sep" aria-hidden="true">·</span>}
            <span className={f.tone === "amber" ? "text-warnInk" : f.tone === "ok" ? "text-ok" : f.tone === "danger" ? "text-danger" : ""}>{f.text}</span>
          </span>
        ))}
      </span>
    ) : null;

  const nameEl = (row: ProviderCatalogRow) =>
    onConfigure ? (
      <button
        type="button"
        className="mlist-name text-left hover:underline underline-offset-2"
        title={t("manage.configure_model")}
        data-testid={`catalog-configure-${row.id}`}
        onClick={(e) => {
          e.preventDefault(); // the name sits in the checkbox's label: open settings, do not toggle
          onConfigure(row.model);
        }}
      >
        {row.label.split(" · ")[0]}
      </button>
    ) : (
      <span className="mlist-name" title={row.model}>{row.label.split(" · ")[0]}</span>
    );

  const idEl = (row: ProviderCatalogRow) =>
    row.label.split(" · ")[0] !== row.id ? <span className="mlist-id" title={row.model}>{row.id}</span> : null;

  const offeredRow = (row: ProviderCatalogRow) => {
    const isDefault = row.model === settings.model;
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
          <span className="mlist-text">
            <span className="mlist-head">
              {nameEl(row)}
              {idEl(row)}
              {row.picked_anyway && <span className="mlist-badge" data-testid={`catalog-your-pick-${row.id}`}>{t("manage.catalog_your_pick")}</span>}
            </span>
            {row.picked_anyway && (
              <span className="mlist-facts" data-testid={`catalog-added-${row.id}`}>
                <span>{t("manage.catalog_added_by_you", { reason: row.reason ? reasonFor(row) : "" })}</span>
                <span className="sep" aria-hidden="true">·</span>
                <button type="button" className="underline underline-offset-2 hover:text-ink" onClick={(e) => { e.preventDefault(); void undo(row); }} data-testid={`catalog-undo-${row.id}`}>
                  {t("manage.catalog_undo")}
                </button>
              </span>
            )}
            {factsLine(factsOf(row), `catalog-facts-${row.id}`)}
          </span>
        </label>
        {isDefault ? (
          <span className="mlist-default">{t("models.default_badge")}</span>
        ) : (
          <button className="mlist-make" onClick={() => void makeDefault(row)}>{t("models.make_default")}</button>
        )}
      </div>
    );
  };

  const asideRow = (row: ProviderCatalogRow) => (
    <div key={row.model}>
      <div className="mlist-row aside" data-testid={`catalog-aside-${row.id}`}>
        <span className="mlist-main cursor-default">
          <span className="mlist-spacer" aria-hidden="true" />
          <span className="mlist-text">
            <span className="mlist-head">
              {nameEl(row)}
              {idEl(row)}
            </span>
            <span className="mlist-facts" data-testid={`catalog-reason-${row.id}`}>{reasonFor(row)}</span>
          </span>
        </span>
        <button className="mlist-use" onClick={() => setWhy(why === row.model ? null : row.model)} data-testid={`catalog-use-${row.id}`}>
          {t("manage.catalog_use_anyway")}
        </button>
      </div>
      {why === row.model && (
        <div className="mlist-why" data-testid={`catalog-why-${row.id}`}>
          <p>
            {row.kind === "unavailable"
              ? t("manage.catalog_why_unavailable", { provider: title, detail: row.detail || "" })
              : row.kind_source === "name"
                ? t("manage.catalog_why_name", { provider: title, kind: kindWord(row) })
                : t("manage.catalog_why_provider", { provider: title, kind: kindWord(row) })}
          </p>
          <p className="mt-1.5">
            <b>{t("manage.catalog_why_then_lead")}</b> {t("manage.catalog_why_then")}
          </p>
          <div className="flex gap-2 mt-2.5">
            <button className="btn-primary sm" onClick={() => void useAnyway(row)} data-testid={`catalog-use-confirm-${row.id}`}>
              {t("manage.catalog_use_anyway")}
            </button>
            <button className="text-meta text-muted hover:text-ink px-2" onClick={() => setWhy(null)} data-testid={`catalog-keep-${row.id}`}>
              {t("manage.catalog_keep_here")}
            </button>
          </div>
        </div>
      )}
    </div>
  );

  const asideCount = (catalog.set_aside || []).length;

  return (
    <div data-testid="provider-models">
      <div className="flex items-center gap-3 text-meta text-muted" data-testid="catalog-status">
        <span>
          <b className="text-ink font-semibold">{t("manage.catalog_count", { count: catalog.count ?? (catalog.rows || []).length })}</b>
          {" · "}
          {t("manage.catalog_checked", { provider: title, ago: ago(catalog.fetched_at, t) })}
        </span>
        <button className="underline underline-offset-2 hover:text-ink" disabled={loading} onClick={() => void load(true)} data-testid="catalog-refresh">
          {loading ? t("manage.catalog_refreshing") : t("manage.catalog_refresh")}
        </button>
        <input
          className="ml-auto bg-transparent border border-line rounded-md px-2 py-0.5 text-meta outline-none"
          placeholder={t("manage.catalog_search")}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          data-testid="catalog-search"
        />
      </div>
      {catalog.error && <p className="text-meta text-warnInk mt-1" data-testid="catalog-error">{t("manage.catalog_stale", { error: catalog.error })}</p>}
      {catalog.not_on_key.length > 0 && (
        <p className="text-meta text-muted mt-1" data-testid="catalog-not-on-key">{t("manage.catalog_not_on_key_help", { count: catalog.not_on_key.length })}</p>
      )}
      <div className="mlist mt-3" data-testid="catalog-list">{offered.map(offeredRow)}</div>
      {asideCount > 0 && !q && (
        <p className="text-meta text-muted mt-2" data-testid="catalog-aside-line">
          <b className="text-ink font-semibold">{t("manage.catalog_aside_line", { count: asideCount })}</b>{" "}
          <button className="underline underline-offset-2 hover:text-ink" onClick={() => setShowAside((v) => !v)} data-testid="catalog-aside-toggle">
            {showAside ? t("manage.catalog_hide") : t("manage.catalog_show")}
          </button>
        </p>
      )}
      {(showAside || q) && aside.length > 0 && <div className="mlist" data-testid="catalog-aside">{aside.map(asideRow)}</div>}
      {rowError && <p className="text-meta text-danger mt-1" role="alert" data-testid="catalog-row-error">{rowError}</p>}
      {addBox}
    </div>
  );
}
