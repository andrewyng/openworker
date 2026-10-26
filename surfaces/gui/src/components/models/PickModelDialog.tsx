// "Pick a model" (UX-055): one short dialog, three steps as a vertical flow. Provider
// (connected ones only, filtered by kind), model (what the server holds, or a typed
// name), settings (compact selects with the recommended value as a quiet note). Opened
// with a model id it is the settings step alone, with Remove at the bottom.
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  addModel,
  getLocalModels,
  getModelConfig,
  removeModel,
  removeModelConfig,
  setModelConfig,
  type LocalModelRow,
  type ModelConfigView,
  type ModelSettings,
  type ProviderInfo,
} from "../../api";
import { fmtBytes, fmtContext } from "./LocalModels";
import { providerConnected, providerKind } from "./ProviderGroups";

type Kind = "local" | "subscription" | "api_key";
const KINDS: Kind[] = ["local", "subscription", "api_key"];
const CONTEXT_STEPS = [16384, 32768, 65536, 131072, 262144, 1048576];
const REPLY_STEPS = [4096, 8192, 16384, 32768, 65536];
const COMPACT_STEPS = [0.6, 0.7, 0.8, 0.9];

export function fullModelId(provider: ProviderInfo, bare: string): string {
  return provider.name === "openai" ? bare : `${provider.name}:${bare}`;
}

type Draft = {
  context_size?: number;
  max_output_tokens?: number;
  thinking?: boolean;
  reasoning_effort?: string;
  temperature?: number;
  top_p?: number;
  compaction_threshold_pct?: number | null; // null = the machine setting
  default?: boolean;
  custom_sampling?: boolean;
};

export function PickModelDialog({
  providers,
  settings,
  keylessOk,
  configure,
  onClose,
  onChanged,
}: {
  providers: ProviderInfo[];
  settings: ModelSettings;
  keylessOk: Set<string>;
  configure?: string | null; // a model id → settings only
  onClose: () => void;
  onChanged: () => void;
}) {
  const { t } = useTranslation();
  const configuring = !!configure;
  const [kinds, setKinds] = useState<Set<Kind>>(new Set(KINDS));
  const [step, setStep] = useState<1 | 2 | 3>(configuring ? 3 : 1);
  const [provider, setProvider] = useState<ProviderInfo | null>(() => {
    if (!configure) return null;
    const i = configure.indexOf(":");
    const name = i > 0 && providers.some((p) => p.name === configure.slice(0, i)) ? configure.slice(0, i) : "openai";
    return providers.find((p) => p.name === name) || null;
  });
  const [model, setModel] = useState<string>(configure || "");
  const [typed, setTyped] = useState("");
  const [query, setQuery] = useState("");
  const [rows, setRows] = useState<LocalModelRow[] | null>(null);
  const [view, setView] = useState<ModelConfigView | null>(null);
  const [draft, setDraft] = useState<Draft>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const connected = providers.filter((p) => providerConnected(p, keylessOk) && kinds.has(providerKind(p)));
  const isLocal = provider ? providerKind(provider) === "local" : false;
  const [loadFailed, setLoadFailed] = useState(false);
  const [reload, setReload] = useState(0);
  const fixedContext = provider?.name === "llamacpp" || provider?.name === "vllm";

  // Step 2: the models the chosen provider offers. Also read when the dialog opens on
  // step 3 for a local model, since the settings need the model's own facts.
  useEffect(() => {
    if (!provider || (step !== 2 && !(step === 3 && providerKind(provider) === "local" && rows === null))) return;
    setRows(null);
    if (providerKind(provider) === "local") {
      setLoadFailed(false);
      getLocalModels(provider.name)
        .then((r) => setRows((r.models || []).filter((m) => m.tools !== false)))
        .catch(() => {
          setLoadFailed(true);
          setRows([]);
        });
    } else {
      setRows(provider.suggested_models.map((bare) => ({
        model: fullModelId(provider, bare), name: bare, size_bytes: null, tools: true, thinking: null, vision: null, remote: true,
        parameter_size: null, quantization: null, context_max: null, context: null, context_from: "server", fit: "cloud", recommendation: null,
      })));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [provider, step, reload]);

  // Step 3: the settings in force for the chosen model.
  useEffect(() => {
    if (!model || step !== 3) return;
    setView(null);
    getModelConfig(model)
      .then((v) => {
        setView(v);
        setDraft({
          context_size: num(v.context_size),
          max_output_tokens: num(v.max_output_tokens),
          thinking: bool(v.thinking),
          reasoning_effort: str(v.reasoning_effort),
          temperature: num(v.temperature),
          top_p: num(v.top_p),
          compaction_threshold_pct: v.compaction_threshold_pct ? num(v.compaction_threshold_pct) : null,
          default: !!v.default?.value || settings.model === model,
          custom_sampling: v.temperature?.from === "user" || v.top_p?.from === "user",
        });
      })
      .catch(() => setView({ model, default: { value: false, from: "user" } }));
  }, [model, step, settings.model]);

  const row = useMemo(() => rows?.find((r) => r.model === model) || null, [rows, model]);
  const rec = view?.recommendation;
  const contextMax = row?.context_max || rec?.context_max || null;
  const machineContext = row?.context_from === "machine" ? row.context : null;
  const thinkingAvailable = row?.thinking || rec?.thinking.available || false;

  const save = async () => {
    if (!model) return;
    setBusy(true);
    setError("");
    const values: Record<string, number | boolean | string | null> = {
      max_output_tokens: draft.max_output_tokens ?? null,
      thinking: thinkingAvailable ? (draft.thinking ?? null) : null,
      reasoning_effort: draft.reasoning_effort || null,
      temperature: draft.custom_sampling ? (draft.temperature ?? null) : null,
      top_p: draft.custom_sampling ? (draft.top_p ?? null) : null,
      compaction_threshold_pct: draft.compaction_threshold_pct ?? null,
      default: !!draft.default,
    };
    if (isLocal && !fixedContext) values.context_size = draft.context_size ?? null;
    const saved = await setModelConfig(model, values);
    if (!saved.ok) {
      setError(saved.error || "");
      setBusy(false);
      return;
    }
    if (!settings.models.includes(model)) await addModel(model);
    setBusy(false);
    onChanged();
    onClose();
  };

  const remove = async () => {
    if (!model) return;
    await removeModel(model);
    await removeModelConfig(model);
    onChanged();
    onClose();
  };

  const reset = () => {
    if (!view) return;
    setDraft((d) => ({
      ...d,
      context_size: machineContext || d.context_size,
      max_output_tokens: rec?.max_output_tokens || undefined,
      thinking: rec?.thinking.available ? rec.thinking.default : d.thinking,
      reasoning_effort: undefined,
      custom_sampling: false,
      compaction_threshold_pct: null,
    }));
  };

  const marker = (n: number, state: "done" | "now" | "todo") => (
    <span
      className={
        "w-[18px] h-[18px] rounded-full border flex items-center justify-center text-[10px] font-medium shrink-0 " +
        (state === "now" ? "bg-ink border-ink text-panel" : state === "done" ? "bg-chrome border-lineStrong text-ink" : "border-lineStrong text-faint")
      }
    >
      {state === "done" ? "✓" : n}
    </span>
  );
  const line = (n: number, state: "done" | "now" | "todo", label: string, value?: string, onChange?: () => void, hint?: string, mono?: boolean) => (
    <div className={"flex items-center gap-3 min-h-[20px] text-ui " + (state === "todo" ? "text-faint" : "font-medium")}>
      {marker(n, state)}
      <span>{label}</span>
      {value && <span className={"font-normal text-muted truncate " + (mono ? "font-mono text-[12.5px]" : "")}>{value}</span>}
      {onChange && !configuring && (
        <button className="ml-auto text-meta text-muted hover:text-ink font-normal" onClick={onChange}>{t("pick.change")}</button>
      )}
      {hint && <span className="ml-auto text-meta text-faint font-normal">{hint}</span>}
    </div>
  );
  const select = (key: string, value: string, options: { v: string; label: string }[], onPick: (v: string) => void) => (
    <select
      className="rounded-lg border border-line bg-panel px-2 py-1.5 text-ui outline-none"
      value={value}
      onChange={(e) => onPick(e.target.value)}
      data-testid={`pick-${key}`}
    >
      {options.map((o) => (
        <option key={o.v} value={o.v}>{o.label}</option>
      ))}
    </select>
  );
  const setting = (key: string, label: string, control: JSX.Element, note: string) => (
    <div className="grid grid-cols-[110px_150px_1fr] items-center gap-3 py-1.5 text-ui" key={key}>
      <span>{label}</span>
      {control}
      <span className="text-meta text-faint">{note}</span>
    </div>
  );
  const recNote = (from: "user" | "recommended" | undefined, extra?: string) =>
    [from === "recommended" ? t("pick.recommended") : "", extra || ""].filter(Boolean).join(" ");

  return (
    <div className="fixed inset-0 z-50 bg-ink/30 grid place-items-center" data-testid="pick-model-dialog" onClick={onClose}>
      <div className="w-[520px] max-w-[94vw] max-h-[90vh] overflow-auto rounded-xl bg-panel shadow-2xl p-5" onClick={(e) => e.stopPropagation()}>
        <h3 className="text-heading font-semibold mb-3.5">{configuring ? settings.model_labels?.[model] || model : t("pick.title")}</h3>

        <div className="flex flex-col gap-4">
          {/* 1 · provider */}
          <div className="relative">
            {step === 1 ? (
              <>
                {line(1, "now", t("pick.provider"))}
                <div className="ml-[30px] mt-2.5">
                  <div className="flex gap-3.5 mb-2.5 text-meta text-muted">
                    {KINDS.map((k) => (
                      <label key={k} className="flex items-center gap-1.5">
                        <input type="checkbox" checked={kinds.has(k)} onChange={() => setKinds((s) => { const n = new Set(s); n.has(k) ? n.delete(k) : n.add(k); return n; })} />
                        {t(`pick.kind_${k}`)}
                      </label>
                    ))}
                  </div>
                  <div className="rounded-lg border border-line max-h-[236px] overflow-auto">
                    {connected.map((p) => (
                      <button
                        key={p.name}
                        className={"w-full flex items-center gap-2 px-3 py-2 text-ui text-left border-t border-line first:border-t-0 " + (provider?.name === p.name ? "bg-chrome" : "hover:bg-chrome")}
                        onClick={() => { setProvider(p); setModel(""); setStep(2); }}
                        data-testid={`pick-provider-${p.name}`}
                      >
                        {p.title}
                        <span className="text-[11px] text-faint ml-1.5">{t(`pick.kind_${providerKind(p)}`)}</span>
                        <span className="ml-auto text-meta text-ok">{p.auth === "oauth" ? t("provider.signed_in") : p.needs_key ? t("provider.connected_ok") : t("provider.running")}</span>
                      </button>
                    ))}
                    {connected.length === 0 && <div className="px-3 py-3 text-meta text-faint">{t("pick.none_connected")}</div>}
                  </div>
                  <p className="text-meta text-faint mt-2">{t("pick.only_connected")}</p>
                </div>
              </>
            ) : (
              line(1, "done", t("pick.provider"), provider?.title, () => setStep(1))
            )}
          </div>

          {/* 2 · model */}
          <div>
            {step < 2 ? (
              line(2, "todo", t("pick.model"), undefined, undefined, t("pick.after_provider"))
            ) : step === 2 ? (
              <>
                {line(2, "now", t("pick.model"))}
                <div className="ml-[30px] mt-2.5">
                  <div className="rounded-lg border border-line max-h-[260px] overflow-auto">
                    <input
                      className="w-full px-3 py-2 text-ui outline-none border-b border-line bg-transparent"
                      placeholder={isLocal ? t("pick.search_or_pull") : t("pick.search_or_type")}
                      value={query}
                      onChange={(e) => { setQuery(e.target.value); setTyped(e.target.value); }}
                      data-testid="pick-model-search"
                    />
                    {(rows || []).filter((r) => !query || r.name.toLowerCase().includes(query.toLowerCase())).map((r) => (
                      <button
                        key={r.model}
                        className="w-full flex items-center gap-2 px-3 py-2 text-ui text-left border-t border-line hover:bg-chrome"
                        onClick={() => { setModel(r.model); setStep(3); }}
                        data-testid={`pick-model-${r.name}`}
                      >
                        <span className="truncate">{settings.model_labels?.[r.model] || r.name}</span>
                        <span className="ml-auto text-meta text-faint shrink-0">
                          {[r.size_bytes ? fmtBytes(r.size_bytes) : "", r.tools ? t("pick.tools") : "", r.thinking ? t("manage.thinking_tag") : ""].filter(Boolean).join(" · ")}
                        </span>
                      </button>
                    ))}
                    {rows === null && <div className="px-3 py-3 text-meta text-faint">{t("manage.loading")}</div>}
                    {loadFailed && (
                      <div className="px-3 py-3 text-meta text-warnInk" data-testid="pick-models-failed">
                        {t("manage.local_models_failed")}{" "}
                        <button className="text-muted underline underline-offset-2 hover:text-ink" onClick={() => setReload((n) => n + 1)}>
                          {t("manage.retry")}
                        </button>
                      </div>
                    )}
                  </div>
                  {typed.trim() && provider && !isLocal && (
                    <button className="mt-2 text-meta text-accent" onClick={() => { setModel(fullModelId(provider, typed.trim())); setStep(3); }} data-testid="pick-model-typed">
                      {t("pick.use_typed", { name: typed.trim() })}
                    </button>
                  )}
                </div>
              </>
            ) : (
              line(2, "done", t("pick.model"), settings.model_labels?.[model] || model.replace(/^[^:]+:/, ""), () => setStep(2), undefined, true)
            )}
          </div>

          {/* 3 · settings */}
          <div>
            {step < 3 ? (
              line(3, "todo", t("pick.settings"), undefined, undefined, t("pick.after_model"))
            ) : (
              <>
                {line(3, "now", t("pick.settings"), undefined, undefined, [rec?.name, row?.size_bytes ? fmtBytes(row.size_bytes) : "", row?.tools || rec ? t("pick.tools") : "", thinkingAvailable ? t("manage.thinking_tag") : ""].filter(Boolean).join(" · "))}
                {view && (
                  <div className="ml-[30px] mt-2" data-testid="pick-settings">
                    {isLocal && !fixedContext &&
                      setting("context", t("pick.context_size"),
                        select("context", String(draft.context_size || machineContext || ""), CONTEXT_STEPS.filter((c) => !contextMax || c <= contextMax).map((c) => ({ v: String(c), label: fmtContext(c) })), (v) => setDraft((d) => ({ ...d, context_size: Number(v) }))),
                        recNote(draft.context_size === machineContext || !draft.context_size ? "recommended" : "user", rec?.context_for_agents ? t("pick.maker_suggests", { ctx: fmtContext(rec.context_for_agents) }) : ""))}
                    {fixedContext && row?.context &&
                      setting("context", t("pick.context_size"), <span className="text-ui text-muted">{fmtContext(row.context)}</span>, t("pick.set_at_start"))}
                    {setting("reply", t("pick.longest_reply"),
                      select("reply", String(draft.max_output_tokens || ""), [{ v: "", label: t("pick.provider_default") }, ...REPLY_STEPS.map((c) => ({ v: String(c), label: `${fmtContext(c)} ${t("pick.tokens")}` }))], (v) => setDraft((d) => ({ ...d, max_output_tokens: v ? Number(v) : undefined }))),
                      recNote(rec?.max_output_tokens && draft.max_output_tokens === rec.max_output_tokens ? "recommended" : undefined))}
                    {thinkingAvailable &&
                      setting("thinking", t("pick.thinking"),
                        select("thinking", draft.thinking === false ? "off" : "on", [{ v: "on", label: t("pick.on") }, { v: "off", label: t("pick.off") }], (v) => setDraft((d) => ({ ...d, thinking: v === "on" }))),
                        rec ? t("pick.makers_default", { value: rec.thinking.default ? t("pick.on") : t("pick.off") }) : "")}
                    {!isLocal &&
                      setting("effort", t("pick.effort"),
                        select("effort", draft.reasoning_effort || "", [{ v: "", label: t("pick.provider_default") }, { v: "low", label: t("pick.low") }, { v: "medium", label: t("pick.medium") }, { v: "high", label: t("pick.high") }], (v) => setDraft((d) => ({ ...d, reasoning_effort: v || undefined }))),
                        t("pick.effort_note"))}
                    {(rec?.sampling.temperature !== undefined || draft.custom_sampling) &&
                      setting("sampling", t("pick.sampling"),
                        select("sampling", draft.custom_sampling ? "custom" : "rec", [{ v: "rec", label: rec ? t("pick.makers_settings") : t("pick.provider_default") }, { v: "custom", label: t("pick.custom") }], (v) => setDraft((d) => ({ ...d, custom_sampling: v === "custom", temperature: d.temperature ?? rec?.sampling.temperature, top_p: d.top_p ?? rec?.sampling.top_p }))),
                        rec ? `${t("pick.temperature")} ${rec.sampling.temperature}, top-p ${rec.sampling.top_p}.` : "")}
                    {draft.custom_sampling && (
                      <div className="grid grid-cols-[110px_150px_1fr] items-center gap-3 py-1 text-ui">
                        <span />
                        <span className="flex gap-2">
                          <input className="w-[70px] rounded-lg border border-line px-2 py-1 text-ui" type="number" step="0.1" min="0" max="2" value={draft.temperature ?? ""} onChange={(e) => setDraft((d) => ({ ...d, temperature: Number(e.target.value) }))} data-testid="pick-temperature" />
                          <input className="w-[70px] rounded-lg border border-line px-2 py-1 text-ui" type="number" step="0.05" min="0" max="1" value={draft.top_p ?? ""} onChange={(e) => setDraft((d) => ({ ...d, top_p: Number(e.target.value) }))} data-testid="pick-top-p" />
                        </span>
                        <span className="text-meta text-faint">{t("pick.temperature")} · top-p</span>
                      </div>
                    )}
                    {setting("compact", t("pick.auto_compact"),
                      select("compact", draft.compaction_threshold_pct == null ? "" : String(draft.compaction_threshold_pct), [{ v: "", label: t("pick.machine_setting") }, ...COMPACT_STEPS.map((c) => ({ v: String(c), label: `${Math.round(c * 100)}%` }))], (v) => setDraft((d) => ({ ...d, compaction_threshold_pct: v ? Number(v) : null }))),
                      t("pick.compact_note", { pct: Math.round((settings.compaction_threshold_pct || 0.8) * 100) }))}
                    <label className="flex items-center gap-2 text-ui mt-2.5">
                      <input type="checkbox" checked={!!draft.default} disabled={settings.model === model} onChange={(e) => setDraft((d) => ({ ...d, default: e.target.checked }))} data-testid="pick-default" />
                      {t("pick.make_default")}
                    </label>
                  </div>
                )}
              </>
            )}
          </div>
        </div>

        {error && <p className="text-meta text-danger mt-3">{error}</p>}
        <div className="flex items-center gap-2 mt-4 pt-3.5 border-t border-line">
          {step === 3 && !configuring && <button className="text-meta text-accent" onClick={reset}>{t("pick.reset")}</button>}
          {configuring && (
            <button className="text-ui text-danger/80 hover:text-danger" onClick={remove} disabled={settings.model === model} data-testid="pick-remove">
              {t("manage.remove_from_picker_btn")}
            </button>
          )}
          <span className="flex-1" />
          <button className="rounded-lg border border-lineStrong px-3 py-1.5 text-ui" onClick={onClose}>{t("pick.cancel")}</button>
          {step === 3 ? (
            <button className="rounded-lg bg-accent text-panel px-3 py-1.5 text-ui font-medium" onClick={save} disabled={busy || !view} data-testid="pick-save">
              {configuring ? t("pick.save") : t("pick.add")}
            </button>
          ) : (
            <button className="rounded-lg bg-accent text-panel px-3 py-1.5 text-ui font-medium" disabled={step === 1 ? !provider : !model} onClick={() => setStep((s) => (s === 1 ? 2 : 3))}>
              {t("pick.next")}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

const num = (v?: { value: unknown }) => (typeof v?.value === "number" ? v.value : undefined);
const bool = (v?: { value: unknown }) => (typeof v?.value === "boolean" ? v.value : undefined);
const str = (v?: { value: unknown }) => (typeof v?.value === "string" ? v.value : undefined);
