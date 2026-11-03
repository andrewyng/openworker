import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  clearWebSearchKey,
  getWebSearch,
  setFetchViaProvider,
  setWebSearchKey,
  setWebSearchProvider,
  type Machine,
  type WebSearchInfo,
  type WebSearchProviderInfo,
} from "../api";
import { openExternal } from "../tauri";
import { PanelHead } from "./IntegrationsView";

// Settings ▸ Customization (UX-058): the services OpenWorker uses behind its tools. Today
// one section, Web search — the provider behind `web_search` as radio rows with a key per
// service (kept in the machine's secret store; "Test and save" runs one search first). The
// active provider, when it can also read pages (Exa, Firecrawl, Tavily), carries one
// indented option: "Also fetch pages through …" for `web_fetch`. Memory, speech
// recognition and the browser come here later, as placeholders in the approved mock.
// Same card idiom as the Sandbox page: one bordered card, hairline-divided rows, ghost
// controls on the right, no new card types.

const CARD = "rounded-xl2 border border-line bg-panel";
const FIELD_LABEL = "text-ui font-medium text-ink";
const INPUT =
  "flex-1 min-w-0 px-3 py-1.5 rounded-lg border border-line bg-paper text-ui text-ink outline-none focus:border-accent";
const BTN_BORDERED =
  "text-ui px-3 py-1.5 rounded-lg border border-line bg-paper hover:border-lineStrong shrink-0 disabled:opacity-40";
const GHOST =
  "text-meta text-muted hover:text-ink hover:bg-paper px-2 py-1 rounded-md shrink-0 disabled:opacity-40";
const PILL = "text-label font-medium px-2 py-0.5 rounded-full bg-okSoft text-ok shrink-0";

// The approved mock's fixed status lines for the keyless services. Everyone else says
// where its key is, or nothing.
const KEYLESS_STATUS: Record<string, string> = {
  duckduckgo: "customization.websearch.status_duckduckgo",
  you: "customization.websearch.status_you",
};

export function CustomizationTab({ machine }: { machine?: Machine | null }) {
  const { t } = useTranslation();
  const machineId = machine?.id ?? null;
  const [info, setInfo] = useState<WebSearchInfo | null>(null);
  const [loadError, setLoadError] = useState("");
  // The provider whose inline key row is open (one at a time), its draft and its state.
  const [open, setOpen] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  // One message at a time, under the row it belongs to: `{name, text}`.
  const [error, setError] = useState<{ name: string; text: string } | null>(null);

  useEffect(() => {
    let alive = true;
    getWebSearch(machineId)
      .then((got) => {
        if (alive) setInfo(got);
      })
      .catch(() => {
        if (alive) setLoadError(t("customization.websearch.load_failed"));
      });
    return () => {
      alive = false;
    };
  }, [machineId, t]);

  // Every mutation answers with the whole section, so the page reflects the server's view
  // (a removed key may move the active provider back to the keyless default).
  const apply = (got: { ok: boolean; error?: string } & Partial<WebSearchInfo>, name: string) => {
    if (got.ok && got.providers) {
      setInfo({ provider: got.provider!, fetch_via_provider: !!got.fetch_via_provider, providers: got.providers });
      setError(null);
      return true;
    }
    setError({ name, text: got.error || t("customization.websearch.failed") });
    return false;
  };

  const choose = async (p: WebSearchProviderInfo) => {
    if (!info || p.name === info.provider) return;
    apply(await setWebSearchProvider(p.name, machineId), p.name);
  };

  const openKeyRow = (name: string) => {
    setOpen(name);
    setDraft("");
    setError(null);
  };

  const saveKey = async (p: WebSearchProviderInfo) => {
    const key = draft.trim();
    if (!key || busy) return;
    setBusy(true);
    try {
      if (apply(await setWebSearchKey(p.name, key, machineId), p.name)) {
        setOpen(null);
        setDraft("");
      }
    } finally {
      setBusy(false);
    }
  };

  const removeKey = async (p: WebSearchProviderInfo) => {
    if (busy) return;
    setBusy(true);
    try {
      if (apply(await clearWebSearchKey(p.name, machineId), p.name)) {
        setOpen(null);
        setDraft("");
      }
    } finally {
      setBusy(false);
    }
  };

  // The flag is stored once; the server honours it only while the active provider offers
  // page fetching, so it sits inert (and unseen here) under any other provider.
  const flipFetch = async (p: WebSearchProviderInfo, next: boolean) => {
    if (!info) return;
    setInfo({ ...info, fetch_via_provider: next }); // optimistic; the reply settles it
    apply(await setFetchViaProvider(next, machineId), p.name);
  };

  const statusLine = (p: WebSearchProviderInfo): string => {
    if (p.key_source === "store") return t("customization.websearch.key_saved");
    if (p.key_source === "env") return t("customization.websearch.key_env", { var: p.key_env || "" });
    const fixed = KEYLESS_STATUS[p.name];
    return fixed ? t(fixed) : "";
  };

  return (
    <section data-testid="customization-tab">
      <PanelHead
        title={t("customization.title")}
        sub={machine ? t("customization.lead_machine", { name: machine.name }) : t("customization.lead")}
      />

      <div className={FIELD_LABEL}>{t("customization.websearch.title")}</div>
      <p className="text-meta text-muted mt-0.5 mb-3 leading-relaxed">{t("customization.websearch.desc")}</p>

      {loadError ? (
        <div className="text-ui text-danger" data-testid="websearch-load-error">
          {loadError}
        </div>
      ) : !info ? null : (
        <>
          <div
            className={CARD + " divide-y divide-line"}
            role="radiogroup"
            aria-label={t("customization.websearch.title")}
            data-testid="websearch-providers"
          >
            {info.providers.map((p) => {
              const isActive = p.name === info.provider;
              const takesKey = p.key_mode !== "none";
              // A service that needs a key cannot be chosen until one is at hand (store or
              // environment); the footnote under the card says so.
              const selectable = isActive || p.key_mode !== "required" || p.has_key;
              const status = statusLine(p);
              const isOpen = open === p.name;
              const rowError = error && error.name === p.name ? error.text : "";
              return (
                <div key={p.name} className="px-4 py-2.5" data-testid={`websearch-row-${p.name}`}>
                  <div className="flex items-center gap-3">
                    <label className={"flex items-center gap-3 flex-1 min-w-0 " + (selectable ? "cursor-pointer" : "")}>
                      <input
                        type="radio"
                        name="websearch-provider"
                        className={selectable ? "" : "opacity-50"}
                        checked={isActive}
                        disabled={!selectable}
                        onChange={() => void choose(p)}
                        data-testid={`websearch-radio-${p.name}`}
                      />
                      <span className="flex-1 min-w-0">
                        <span className={"block text-ui font-medium " + (selectable ? "text-ink" : "text-ink/60")}>
                          {p.display_name}
                        </span>
                        {status ? (
                          <span className="block text-meta text-muted" data-testid={`websearch-status-${p.name}`}>
                            {status}
                          </span>
                        ) : null}
                      </span>
                    </label>
                    {isActive ? (
                      <span className={PILL} data-testid={`websearch-in-use-${p.name}`}>
                        {t("customization.websearch.in_use")}
                      </span>
                    ) : null}
                    {takesKey ? (
                      isOpen ? (
                        <button type="button" className={GHOST} onClick={() => setOpen(null)} data-testid="websearch-key-cancel">
                          {t("customization.websearch.cancel")}
                        </button>
                      ) : (
                        <button
                          type="button"
                          className={GHOST}
                          onClick={() => openKeyRow(p.name)}
                          data-testid={`websearch-key-button-${p.name}`}
                        >
                          {p.has_key ? t("customization.websearch.update_key") : t("customization.websearch.add_key")}
                        </button>
                      )
                    ) : null}
                  </div>
                  {isOpen ? (
                    <div className="mt-2 pl-7 flex items-center gap-2 flex-wrap" data-testid={`websearch-key-row-${p.name}`}>
                      <input
                        type="password"
                        className={INPUT}
                        placeholder={t("customization.websearch.key_placeholder", { name: p.display_name })}
                        value={draft}
                        autoFocus
                        spellCheck={false}
                        onChange={(e) => setDraft(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") void saveKey(p);
                        }}
                        data-testid="websearch-key-input"
                      />
                      <button
                        type="button"
                        className={BTN_BORDERED}
                        disabled={busy || !draft.trim()}
                        onClick={() => void saveKey(p)}
                        data-testid="websearch-key-save"
                      >
                        {busy ? t("customization.websearch.testing") : t("customization.websearch.test_and_save")}
                      </button>
                      <span className="text-meta text-faint">
                        {t("customization.websearch.checked_hint")}{" "}
                        {p.key_url ? (
                          <button
                            type="button"
                            className="text-accent hover:underline"
                            onClick={() => openExternal(p.key_url)}
                            data-testid={`websearch-key-url-${p.name}`}
                          >
                            {t("customization.websearch.get_key")}
                          </button>
                        ) : null}
                      </span>
                      {p.key_source === "store" ? (
                        <button
                          type="button"
                          className={GHOST + " ml-auto"}
                          disabled={busy}
                          onClick={() => void removeKey(p)}
                          data-testid="websearch-key-remove"
                        >
                          {t("customization.websearch.remove_key")}
                        </button>
                      ) : null}
                    </div>
                  ) : null}
                  {isActive && p.supports_fetch ? (
                    <label className="mt-2 pl-7 flex items-start gap-2.5 cursor-pointer" data-testid="websearch-fetch-row">
                      <input
                        type="checkbox"
                        className="mt-0.5"
                        checked={info.fetch_via_provider}
                        onChange={(e) => void flipFetch(p, e.target.checked)}
                        data-testid="websearch-fetch-toggle"
                      />
                      <span className="min-w-0">
                        <span className="block text-ui text-ink">{t("customization.fetch.row", { name: p.display_name })}</span>
                        <span className="block text-meta text-muted">{t("customization.fetch.caution", { name: p.display_name })}</span>
                      </span>
                    </label>
                  ) : null}
                  {rowError ? (
                    <div className="mt-1.5 pl-7 text-meta text-danger" data-testid="websearch-error">
                      {rowError}
                    </div>
                  ) : null}
                </div>
              );
            })}
          </div>
          <p className="text-meta text-faint mt-2.5">{t("customization.websearch.footnote")}</p>

        </>
      )}
    </section>
  );
}
