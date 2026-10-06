// A local provider opened in place (UX-055): "Your system" laid out like the Mac's
// About panel, then the models the server holds with size, tools, reasoning, how each
// sits on this machine and the context it runs at. Tick = in the picker, ⚙ = the
// model's settings, ✕ = out of the picker.
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  addModel,
  getLocalModels,
  getSystemFacts,
  removeModel,
  type LocalModelRow,
  type SystemFacts,
} from "../../api";

export function fmtBytes(n: number | null | undefined): string {
  if (!n || n <= 0) return "—";
  const gb = n / 1024 ** 3;
  if (gb >= 100) return `${Math.round(gb)} GB`;
  if (gb >= 10) return `${gb.toFixed(0)} GB`;
  if (gb >= 1) return `${gb.toFixed(1)} GB`;
  return `${Math.round(n / 1024 ** 2)} MB`;
}

export function fmtContext(n: number | null | undefined): string {
  if (!n) return "—";
  if (n >= 1_000_000) return `${Math.round(n / 1_048_576)}M`;
  return `${Math.round(n / 1024)}K`;
}

export function YourSystem() {
  const { t } = useTranslation();
  const [facts, setFacts] = useState<SystemFacts | null>(null);
  useEffect(() => {
    getSystemFacts().then(setFacts).catch(() => setFacts(null));
  }, []);
  if (!facts) return null;
  const row = (label: string, value: string, key: string) => (
    <div className="flex items-center px-4 py-2.5 border-b border-line last:border-b-0 text-ui" key={key} data-testid={`sys-${key}`}>
      <span>{label}</span>
      <span className="ml-auto text-muted text-right">{value}</span>
    </div>
  );
  return (
    <div className="mt-5" data-testid="your-system">
      <div className="text-label text-faint font-semibold tracking-wide uppercase mb-2">{t("manage.your_system")}</div>
      <div className="rounded-xl border border-line bg-panel">
        {row(t("manage.sys_processor"), facts.processor, "processor")}
        {row(t("manage.sys_graphics"), facts.graphics, "graphics")}
        {row(t("manage.sys_memory"), fmtBytes(facts.memory_bytes), "memory")}
        {row(
          t("manage.sys_storage"),
          t("manage.sys_storage_free", { free: fmtBytes(facts.storage_free_bytes), total: fmtBytes(facts.storage_total_bytes) }),
          "storage",
        )}
      </div>
      {facts.runs_well_up_to_bytes ? (
        <p className="text-meta text-muted mt-2">{t("manage.sys_runs_well", { size: fmtBytes(facts.runs_well_up_to_bytes) })}</p>
      ) : null}
    </div>
  );
}

export function LocalModelsTable({
  provider,
  curated,
  defaultModel,
  onChanged,
  onConfigure,
}: {
  provider: string;
  curated: string[]; // the picker's models
  defaultModel: string;
  onChanged: () => void;
  onConfigure: (model: string) => void;
}) {
  const { t } = useTranslation();
  const [rows, setRows] = useState<LocalModelRow[] | null>(null);
  const [alive, setAlive] = useState<boolean | undefined>(undefined);
  const load = () =>
    getLocalModels(provider)
      .then((r) => {
        setRows(r.models || []);
        setAlive(r.alive);
      })
      .catch(() => setRows([]));
  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [provider]);
  if (rows === null) return null;
  const inPicker = (id: string) => curated.includes(id);
  const toggle = async (row: LocalModelRow) => {
    if (inPicker(row.model)) {
      if (row.model === defaultModel) return;
      await removeModel(row.model);
    } else {
      await addModel(row.model);
    }
    onChanged();
  };
  const fitLabel = (row: LocalModelRow) => {
    if (row.tools === false) return <span className="text-faint">{t("manage.fit_not_usable")}</span>;
    const cls = row.fit === "runs_well" ? "text-ok" : row.fit === "tight" || row.fit === "too_large" ? "text-warnInk" : "text-faint";
    return <span className={cls}>{t(`manage.fit_${row.fit}`)}</span>;
  };
  const vllmOff = provider === "vllm" && rows.some((r) => r.tools === false);
  return (
    <div className="mt-6" data-testid="local-models">
      <div className="flex items-center mb-2">
        <div className="text-label text-faint font-semibold tracking-wide uppercase">
          {t(provider === "ollama" ? "manage.local_models_title" : "manage.local_models_title_server")}
        </div>
        {provider === "ollama" && (
          <a className="ml-auto text-meta text-accent" href="https://ollama.com/search?c=tools" target="_blank" rel="noreferrer">
            {t("manage.get_more_models")} ↗
          </a>
        )}
      </div>
      {alive === false && <p className="text-meta text-warnInk mb-2">{t("manage.local_server_not_answering")}</p>}
      {vllmOff && (
        <div className="rounded-lg border border-line bg-warnSoft text-warnInk text-meta px-3 py-2 mb-2" data-testid="vllm-tools-off">
          {t("manage.vllm_tools_off")}
          <code className="block mt-1 text-[11px]">--enable-auto-tool-choice --tool-call-parser &lt;name&gt;</code>
        </div>
      )}
      <div className="rounded-xl border border-line bg-panel">
        <div className="grid grid-cols-[22px_1fr_56px_52px_72px_104px_52px_22px_22px] items-center gap-2.5 px-3.5 py-1.5 text-label text-faint">
          <span />
          <span>{t("manage.col_model")}</span>
          <span>{t("manage.col_size")}</span>
          <span>{t("manage.col_tools")}</span>
          <span>{t("manage.col_reasoning")}</span>
          <span>{t("manage.col_fit")}</span>
          <span>{t("manage.col_context")}</span>
          <span />
          <span />
        </div>
        {rows.map((row) => {
          const usable = row.tools !== false;
          const ticked = inPicker(row.model);
          return (
            <div
              key={row.model}
              className={"grid grid-cols-[22px_1fr_56px_52px_72px_104px_52px_22px_22px] items-center gap-2.5 px-3.5 py-2.5 border-t border-line" + (usable ? "" : " opacity-60")}
              data-testid={`local-model-${row.name}`}
            >
              <input type="checkbox" checked={ticked} disabled={!usable || row.model === defaultModel} onChange={() => toggle(row)} />
              <span className="min-w-0">
                <span className="block font-mono text-[12.5px] truncate" title={row.model}>{row.name}</span>
                {!usable && <span className="block text-meta text-muted">{t("manage.no_tools_note")}</span>}
              </span>
              <span className="text-meta text-muted">{row.remote ? t("manage.fit_cloud") : fmtBytes(row.size_bytes)}</span>
              <span className={"text-meta " + (row.tools ? "text-ok" : "text-faint")}>{row.tools === null ? "—" : row.tools ? "✓" : "✗"}</span>
              <span className={"text-meta " + (row.thinking ? "text-ok" : "text-faint")}>{row.thinking ? `✓ ${t("manage.thinking_tag")}` : "—"}</span>
              <span className="text-meta">{fitLabel(row)}</span>
              <span className="text-meta text-muted">{fmtContext(row.context)}</span>
              {usable ? (
                <button className="text-faint hover:text-ink" title={t("manage.configure_model")} onClick={() => onConfigure(row.model)} data-testid={`local-model-configure-${row.name}`}>
                  ⚙
                </button>
              ) : (
                <span />
              )}
              {usable && ticked && row.model !== defaultModel ? (
                <button className="text-faint hover:text-ink" title={t("manage.remove_from_picker")} onClick={() => toggle(row)}>
                  ✕
                </button>
              ) : (
                <span />
              )}
            </div>
          );
        })}
      </div>
      <p className="text-meta text-muted mt-2 leading-relaxed">{t("manage.local_models_help")}</p>
    </div>
  );
}
