// llama.cpp set up and run by OpenWorker on a Mac (UX-057): get llama.cpp, pick a model
// sized to this Mac, start it. Then a card for the running server. Polls the state while
// something runs, like the sandbox setup. "Connect to it instead" keeps the plain form.

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import {
  cancelLlamaCpp,
  getLlamaCppSetup,
  installLlamaCpp,
  pickGgufFile,
  setLlamaCppAutostart,
  startLlamaCpp,
  stopLlamaCpp,
  type LlamaCppChoice,
  type LlamaCppSetup,
  type ModelFit,
} from "../../api";
import { ProviderForm, type ProviderSetupState } from "../../providers/ProviderSetup";
import { openExternal } from "../../tauri";
import { fmtBytes, fmtContext } from "./LocalModels";

const BUSY = new Set(["installing", "downloading", "loading", "checking"]);

function choiceKey(c: LlamaCppChoice | null): string {
  if (!c) return "";
  return c.kind === "suggested" ? `s:${c.id}` : c.kind === "ollama" ? `o:${c.name}` : `f:${c.path}`;
}

function fromKey(key: string): LlamaCppChoice | null {
  const v = key.slice(2);
  if (key.startsWith("s:")) return { kind: "suggested", id: v };
  if (key.startsWith("o:")) return { kind: "ollama", name: v };
  if (key.startsWith("f:")) return { kind: "file", path: v };
  return null;
}

function minutes(s: number | null | undefined): number | null {
  return s == null ? null : Math.max(1, Math.round(s / 60));
}

/** The llama.cpp page when OpenWorker can run it here: the provider head, the guided
 * card, then `after` (Your system, and the models table once a server runs), re-mounted
 * when what runs changes so it reads the new server. */
export function LlamaCppPage({
  ps,
  tp,
  footer,
  onChanged,
  after,
}: {
  ps: ProviderSetupState;
  tp: string;
  footer?: ReactNode;
  onChanged: () => void;
  after: (key: string, withTable: boolean) => ReactNode;
}) {
  const { t } = useTranslation();
  const [setup, setSetup] = useState<LlamaCppSetup | null>(null);
  const [loadError, setLoadError] = useState(false);
  // A server the user runs themselves answers and OpenWorker runs nothing: their form.
  const [connect, setConnect] = useState<boolean | null>(null);

  const load = useCallback(async () => {
    try {
      const s = await getLlamaCppSetup();
      setSetup(s);
      setLoadError(false);
      return s;
    } catch {
      setLoadError(true);
      return null;
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (connect !== null || !setup) return;
    setConnect(!!ps.info?.alive && setup.phase !== "running" && !BUSY.has(setup.phase));
  }, [setup, connect, ps.info?.alive]);

  // Poll while something runs; when it settles, refresh the providers and the picker.
  const phase = setup?.phase;
  const wasBusy = useRef(false);
  useEffect(() => {
    const busy = !!phase && BUSY.has(phase);
    if (!busy) {
      if (wasBusy.current) {
        wasBusy.current = false;
        void ps.refreshProviders();
        onChanged();
      }
      return;
    }
    wasBusy.current = true;
    const id = window.setInterval(() => void load(), 1000);
    return () => window.clearInterval(id);
  }, [phase, load, ps, onChanged]);

  const status = (() => {
    if (!setup || connect) return undefined;
    const cls = "block text-meta text-muted";
    if (setup.phase === "running") return <span className="block text-meta text-ok font-medium">{t("llamacpp.status_running")}</span>;
    if (setup.phase === "installing") return <span className={cls}>{t("llamacpp.status_installing")}</span>;
    if (BUSY.has(setup.phase)) return <span className={cls}>{t("llamacpp.status_starting")}</span>;
    if (setup.installed)
      return <span className={cls}>{t("llamacpp.status_installed", { version: setup.installed.version || "" })}</span>;
    return <span className={cls}>{t("provider.not_set_up")}</span>;
  })();

  const guided =
    connect === false && setup ? (
      <SetupCard setup={setup} onState={setSetup} reload={load} onConnectInstead={() => setConnect(true)} onChanged={() => { void ps.refreshProviders(); onChanged(); }} />
    ) : connect === null && loadError ? (
      <p className="text-meta text-muted mt-4" data-testid="llamacpp-load-failed">
        {t("llamacpp.load_failed")}{" "}
        <button className="underline underline-offset-2 hover:text-ink" onClick={() => void load()}>
          {t("manage.retry")}
        </button>
      </p>
    ) : connect === null ? (
      <p className="text-meta text-faint mt-4">{t("llamacpp.loading")}</p>
    ) : null;

  return (
    <>
      <ProviderForm
        ps={ps}
        tp={tp}
        footer={footer}
        status={status}
        blurb={connect ? undefined : t("llamacpp.blurb")}
        guided={guided ?? undefined}
      />
      {connect && (
        <p className="text-meta text-faint mt-3">
          <button className="text-muted underline decoration-line underline-offset-2 hover:text-ink" onClick={() => setConnect(false)} data-testid="llamacpp-setup-here">
            {t("llamacpp.setup_here")}
          </button>
        </p>
      )}
      {/* What the server reports, once there is a server: one it runs, or the user's own. */}
      {after(`${setup?.phase === "running" ? setup.model?.alias : ""}:${setup?.address ?? ""}`, !!connect || setup?.phase === "running")}
    </>
  );
}

function SetupCard({
  setup,
  onState,
  reload,
  onConnectInstead,
  onChanged,
}: {
  setup: LlamaCppSetup;
  onState: (s: LlamaCppSetup) => void;
  reload: () => Promise<LlamaCppSetup | null>;
  onConnectInstead: () => void;
  onChanged: () => void;
}) {
  const { t } = useTranslation();
  const [picked, setPicked] = useState<string>(() => choiceKey(setup.last_choice) || (setup.suggested[0] ? `s:${setup.suggested[0].id}` : ""));
  const [file, setFile] = useState<string | null>(setup.last_choice?.kind === "file" ? setup.last_choice.path : null);
  const [switching, setSwitching] = useState(false);
  const [lastStart, setLastStart] = useState<LlamaCppChoice | null>(null);
  const [copied, setCopied] = useState("");
  const [error, setError] = useState<string | null>(null);

  const { phase } = setup;
  const installed = !!setup.installed;
  const busy = BUSY.has(phase);

  const act = async (fn: () => Promise<LlamaCppSetup & { ok?: boolean; error?: string }>) => {
    setError(null);
    try {
      const r = await fn();
      if (r.ok === false && r.error) setError(r.error);
      if (r.phase) onState(r);
      else await reload();
      // Stop and the switch settle at once; the poll covers the rest.
      if (r.phase && !BUSY.has(r.phase)) onChanged();
    } catch (e) {
      setError(String((e as Error).message || e));
    }
  };

  const copy = (text: string) => {
    void navigator.clipboard?.writeText(text);
    setCopied(text);
    window.setTimeout(() => setCopied(""), 1500);
  };

  const start = (choice: LlamaCppChoice | null) => {
    if (!choice) return;
    setLastStart(choice);
    setSwitching(false);
    void act(() => startLlamaCpp(choice));
  };

  const chooseFile = async () => {
    const r = await pickGgufFile();
    if (r.ok && r.path) {
      setFile(r.path);
      setPicked(`f:${r.path}`);
    }
  };

  // -- the running server (screen 5) ---------------------------------------------------
  if (phase === "running" && !switching && setup.model) {
    const m = setup.model;
    return (
      <div className="mt-4 rounded-xl border border-line bg-panel px-5 py-4" data-testid="llamacpp-running">
        <div className="flex items-center gap-2.5">
          <span className="w-[7px] h-[7px] rounded-full bg-ok shrink-0" />
          <span className="text-ui font-semibold truncate">{m.name}</span>
          {m.detail && <span className="text-meta text-muted truncate">{m.detail}</span>}
          <span className="ml-auto flex gap-2 shrink-0">
            <button className={BTN} onClick={() => setSwitching(true)} data-testid="llamacpp-switch">
              {t("llamacpp.switch_model")}
            </button>
            <button className={BTN} onClick={() => void act(stopLlamaCpp)} data-testid="llamacpp-stop">
              {t("llamacpp.stop")}
            </button>
          </span>
        </div>
        {setup.check && !setup.check.tools && (
          <p className="text-meta text-warnInk mt-2.5" data-testid="llamacpp-no-tools">
            {t("llamacpp.no_tools")}
          </p>
        )}
        {setup.note === "unverified" && <p className="text-meta text-faint mt-2.5" data-testid="llamacpp-note">{t("llamacpp.note_unverified")}</p>}
        <div className="grid grid-cols-[120px_1fr] gap-x-3 gap-y-1 text-ui mt-3">
          <span className="text-muted">{t("llamacpp.context")}</span>
          <span>{fmtContext(m.context)}</span>
          <span className="text-muted">{t("llamacpp.address")}</span>
          <span className="font-mono text-meta">{setup.address}</span>
          <span className="text-muted">{t("llamacpp.model_file")}</span>
          <span className="font-mono text-meta truncate" title={m.file}>{m.file}</span>
        </div>
        <div className="border-t border-line my-3.5" />
        <label className="flex items-center gap-2.5 text-meta cursor-pointer">
          {t("llamacpp.autostart")}
          <Switch on={setup.autostart} onChange={(on) => void act(() => setLlamaCppAutostart(on))} testid="llamacpp-autostart" />
        </label>
        <p className="text-meta text-faint mt-1">{t(setup.autostart ? "llamacpp.autostart_on" : "llamacpp.autostart_off")}</p>
      </div>
    );
  }

  // -- the three steps (screens 1–4) ---------------------------------------------------
  const pickNow = installed && !busy && phase !== "installing";
  const startNow = busy && phase !== "installing";
  const failedStart = phase === "failed" && installed && (lastStart || setup.model);
  const choice = fromKey(picked);
  const pickedSuggested = choice?.kind === "suggested" ? setup.suggested.find((s) => s.id === choice.id) : undefined;
  const pickedSummary = setup.model ? [setup.model.name, setup.model.detail].filter(Boolean).join(" · ") : "";
  const p = setup.progress;

  return (
    <div className="mt-4">
      <div className="rounded-xl border border-line bg-panel px-5 py-4" data-testid="llamacpp-setup">
        <ol className="relative pl-[26px] before:content-[''] before:absolute before:left-[7px] before:top-2 before:bottom-2 before:w-px before:bg-lineStrong">
          {/* 1. Get llama.cpp */}
          <Step state={installed ? "done" : "now"} title={t("llamacpp.step_get")} sum={installed ? t("llamacpp.installed_sum", { version: setup.installed?.version || "" }) : phase === "installing" ? setup.install_command : undefined} testid="llamacpp-step-get">
            {!installed && phase !== "installing" && (
              <div className="mt-2 text-ui">
                {setup.brew ? (
                  <>
                    <p className="text-muted">{t("llamacpp.not_installed")}</p>
                    <Cmd text={setup.install_command} copied={copied === setup.install_command} onCopy={copy} t={t} />
                    <div className="flex items-center gap-2.5 mt-2.5">
                      <button className={BTN_PRIMARY} onClick={() => void act(installLlamaCpp)} data-testid="llamacpp-install">
                        {phase === "failed" ? t("manage.retry") : t("llamacpp.install")}
                      </button>
                      <span className="text-meta text-faint">{t("llamacpp.install_hint")}</span>
                    </div>
                  </>
                ) : (
                  <p className="text-muted" data-testid="llamacpp-no-brew">
                    {t("llamacpp.no_brew")}{" "}
                    <button className="underline underline-offset-2 hover:text-ink" onClick={() => openExternal("https://brew.sh")}>brew.sh ↗</button>
                  </p>
                )}
                {phase === "failed" && setup.error && <p className="text-meta text-danger mt-2" data-testid="llamacpp-error">{setup.error}</p>}
              </div>
            )}
            {phase === "installing" && (
              <div className="mt-2">
                <pre className="rounded-lg bg-[#1d1f23] text-[#c9ced6] px-3 py-2 text-[11.5px] leading-relaxed font-mono whitespace-pre-wrap break-all" data-testid="llamacpp-install-log">
                  {setup.lines.length ? setup.lines.join("\n") : "…"}
                </pre>
                <button className={BTN + " mt-2"} onClick={() => void act(cancelLlamaCpp)} data-testid="llamacpp-cancel">
                  {t("llamacpp.cancel")}
                </button>
              </div>
            )}
          </Step>

          {/* 2. Pick a model */}
          <Step
            state={pickNow ? "now" : startNow ? "done" : "todo"}
            title={t("llamacpp.step_pick")}
            sum={startNow ? pickedSummary : undefined}
            testid="llamacpp-step-pick"
          >
            {pickNow && (
              <div className="mt-2">
                <Group label={t("llamacpp.suggested")} />
                <div className="rounded-[10px] border border-line">
                  {setup.suggested.map((s) => (
                    <ModelRow
                      key={s.id}
                      id={`s:${s.id}`}
                      picked={picked}
                      onPick={setPicked}
                      name={s.name}
                      sub={[s.vendor, s.quant, ...s.features.map((f) => t(`llamacpp.feature_${f}`))].filter(Boolean).join(" · ")}
                      size={s.size}
                      fit={s.fit}
                      extra={s.downloaded ? t("llamacpp.downloaded") : undefined}
                      line={
                        s.license ? (
                          <button
                            className="text-label text-faint underline decoration-line underline-offset-2 hover:text-ink"
                            onClick={(e) => { e.stopPropagation(); if (s.license_url) openExternal(s.license_url); }}
                            data-testid={`llamacpp-license-${s.id}`}
                          >
                            {t("llamacpp.license", { name: s.license })} ↗
                          </button>
                        ) : undefined
                      }
                    />
                  ))}
                  {setup.suggested.length === 0 && <p className="px-3 py-2.5 text-meta text-faint">{t("llamacpp.none_fit")}</p>}
                </div>
                <Group label={t("llamacpp.on_this_mac")} />
                <div className="rounded-[10px] border border-line">
                  {setup.ollama.map((o) => (
                    <ModelRow key={o.name} id={`o:${o.name}`} picked={picked} onPick={setPicked} name={o.name} sub={t("llamacpp.from_ollama")} size={o.size} fit={o.fit} />
                  ))}
                  <ModelRow
                    id={file ? `f:${file}` : "f:"}
                    picked={picked}
                    onPick={() => (file ? setPicked(`f:${file}`) : void chooseFile())}
                    name={file ? file.split("/").pop() || file : t("llamacpp.choose_file")}
                    sub={file ? file : t("llamacpp.choose_file_sub")}
                    action={file ? <button className="text-meta text-muted hover:text-ink" onClick={(e) => { e.stopPropagation(); void chooseFile(); }}>{t("llamacpp.change")}</button> : undefined}
                  />
                </div>
                {setup.ollama_pull.length > 0 && (
                  <>
                    <Group label={t("llamacpp.not_downloaded")} />
                    <div className="rounded-[10px] border border-line" data-testid="llamacpp-pull">
                      {setup.ollama_pull.map((o) => (
                        <div key={o.name} className="flex items-center gap-3 px-3 py-2.5 border-t border-line first:border-t-0">
                          <span className="min-w-0 flex-1">
                            <span className="block text-ui font-medium">{o.name}</span>
                            <span className="block text-label text-faint">{t("llamacpp.pull_sub", { size: fmtBytes(o.size), fit: t(`manage.fit_${o.fit}`) })}</span>
                          </span>
                          <Cmd text={o.command} copied={copied === o.command} onCopy={copy} t={t} compact />
                        </div>
                      ))}
                    </div>
                  </>
                )}
                <p className="text-meta text-faint mt-2" data-testid="llamacpp-cache">
                  {t("llamacpp.cache_where")} <code className="font-mono">{setup.cache_dir}</code>
                  {setup.cache_free_bytes != null && <> · {t("llamacpp.free", { size: fmtBytes(setup.cache_free_bytes) })}</>}
                </p>
                {failedStart && setup.error && (
                  <p className="text-meta text-danger mt-2" data-testid="llamacpp-error">{setup.error}</p>
                )}
                <div className="flex items-center gap-2.5 mt-3">
                  <button className={BTN_PRIMARY} disabled={!choice || picked === "f:"} onClick={() => start(choice)} data-testid="llamacpp-start">
                    {pickedSuggested && !pickedSuggested.downloaded ? t("llamacpp.download_and_start") : t("llamacpp.start")}
                  </button>
                  {switching && (
                    <button className={BTN} onClick={() => setSwitching(false)}>
                      {t("llamacpp.cancel")}
                    </button>
                  )}
                </div>
              </div>
            )}
          </Step>

          {/* 3. Start it */}
          <Step state={startNow ? "now" : "todo"} title={t("llamacpp.step_start")} testid="llamacpp-step-start">
            {startNow && (
              <div className="mt-2">
                {phase === "downloading" && (
                  <>
                    <p className="text-meta text-muted" data-testid="llamacpp-progress">
                      {setup.retry_in != null
                        ? t("llamacpp.rate_limited", { seconds: setup.retry_in })
                        : p?.verifying
                          ? t("llamacpp.verifying")
                          : p
                            ? t("llamacpp.downloading", { done: fmtBytes(p.done), total: fmtBytes(p.total) }) +
                              (minutes(p.seconds_left) != null ? " · " + t("llamacpp.minutes_left", { count: minutes(p.seconds_left) ?? 0 }) : "")
                            : t("llamacpp.download_starting")}
                    </p>
                    <div className="h-1.5 rounded bg-paper overflow-hidden my-2">
                      <i className="block h-full bg-muted rounded" style={{ width: `${p && p.total ? Math.round((100 * p.done) / p.total) : 0}%` }} />
                    </div>
                  </>
                )}
                <ul className="text-meta space-y-0.5">
                  {setup.model?.download && <Check done={phase !== "downloading"} now={phase === "downloading"} text={t("llamacpp.check_download")} />}
                  <Check done={phase === "checking"} now={phase === "loading"} text={t("llamacpp.check_load")} />
                  <Check done={false} now={phase === "checking"} text={t("llamacpp.check_answers")} />
                  <Check done={false} now={false} text={t("llamacpp.check_tools")} />
                </ul>
                {setup.note === "unverified" && <p className="text-meta text-faint mt-2" data-testid="llamacpp-note">{t("llamacpp.note_unverified")}</p>}
                <div className="flex items-center gap-2.5 mt-2.5">
                  <button className={BTN} onClick={() => void act(cancelLlamaCpp)} data-testid="llamacpp-cancel">
                    {t("llamacpp.cancel")}
                  </button>
                  <span className="text-meta text-faint">{t("llamacpp.leave_ok")}</span>
                </div>
              </div>
            )}
          </Step>
        </ol>
      </div>
      <p className="text-meta text-faint mt-3">
        {t("llamacpp.own_server")}{" "}
        <button className="text-muted underline decoration-line underline-offset-2 hover:text-ink" onClick={onConnectInstead} data-testid="llamacpp-connect-instead">
          {t("llamacpp.connect_instead")}
        </button>
      </p>
      {error && <p className="text-meta text-danger mt-2">{error}</p>}
    </div>
  );
}

const BTN = "rounded-lg border border-lineStrong bg-panel px-3 py-1.5 text-ui text-ink hover:border-ink/40 disabled:opacity-40";
const BTN_PRIMARY = "rounded-lg border border-ink bg-ink px-3 py-1.5 text-ui font-medium text-panel hover:brightness-110 disabled:opacity-40";

function Step({ state, title, sum, testid, children }: { state: "done" | "now" | "todo"; title: string; sum?: string; testid: string; children?: ReactNode }) {
  return (
    <li className="relative pb-4 last:pb-0" data-testid={testid} data-state={state}>
      <span
        className={
          "absolute -left-[26px] top-[3px] w-[15px] h-[15px] rounded-full border-[1.5px] grid place-items-center text-[9px] " +
          (state === "now" ? "border-ink bg-ink" : state === "done" ? "border-lineStrong bg-paper text-muted" : "border-lineStrong bg-panel")
        }
      >
        {state === "done" ? "✓" : ""}
      </span>
      <div className="flex items-baseline gap-2">
        <span className={"text-ui " + (state === "todo" ? "text-faint" : "font-semibold")}>{title}</span>
        {sum && <span className="text-meta text-muted truncate">{sum}</span>}
      </div>
      {children}
    </li>
  );
}

function Group({ label }: { label: string }) {
  return <div className="text-[11px] tracking-wide uppercase text-faint font-semibold mt-3 mb-1">{label}</div>;
}

function ModelRow({
  id,
  picked,
  onPick,
  name,
  sub,
  size,
  fit,
  extra,
  action,
  line,
}: {
  id: string;
  picked: string;
  onPick: (id: string) => void;
  name: string;
  sub?: string;
  size?: number | null;
  fit?: ModelFit;
  extra?: string;
  action?: ReactNode;
  // A third line under the name: the licence, for a model that downloads.
  line?: ReactNode;
}) {
  const { t } = useTranslation();
  const on = picked === id && id !== "f:";
  return (
    <div
      role="radio"
      aria-checked={on}
      tabIndex={0}
      className="grid grid-cols-[18px_1fr_auto_auto] gap-2.5 items-center px-3 py-2.5 border-t border-line first:border-t-0 cursor-pointer hover:bg-paper/50"
      onClick={() => onPick(id)}
      onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && onPick(id)}
      data-testid={`llamacpp-model-${id}`}
    >
      <span className={"w-3.5 h-3.5 rounded-full " + (on ? "border-[4.5px] border-ink" : "border-[1.5px] border-lineStrong")} />
      <span className="min-w-0">
        <span className="block text-ui font-medium truncate">{name}</span>
        {sub && <span className="block text-label text-faint truncate">{sub}</span>}
        {line && <span className="block truncate">{line}</span>}
      </span>
      <span className="text-meta text-muted text-right">{action ?? (size ? fmtBytes(size) : "")}</span>
      <span className={"text-meta text-right min-w-[72px] " + (fit === "runs_well" ? "text-ok" : "text-warnInk")}>
        {extra ? <span className="text-muted">{extra}</span> : fit && fit !== "unknown" ? t(`manage.fit_${fit}`) : ""}
      </span>
    </div>
  );
}

function Cmd({ text, copied, onCopy, t, compact }: { text: string; copied: boolean; onCopy: (s: string) => void; t: (k: string) => string; compact?: boolean }) {
  return (
    <button
      className={"flex items-center gap-2 rounded-lg bg-paper font-mono text-meta text-ink text-left " + (compact ? "px-2 py-1 shrink-0" : "w-full px-2.5 py-2 mt-2")}
      onClick={() => onCopy(text)}
      title={t("provider.copy_command")}
      data-testid={`llamacpp-copy-${text.split(" ").pop()}`}
    >
      <span className="truncate">{text}</span>
      <span className="ml-auto font-sans text-label text-faint pl-2">{copied ? t("llamacpp.copied") : t("llamacpp.copy")}</span>
    </button>
  );
}

function Check({ done, now, text }: { done: boolean; now: boolean; text: string }) {
  return (
    <li className={"flex gap-2 " + (done || now ? "text-ink" : "text-muted")}>
      <span className="w-3.5 text-center text-muted">
        {done ? "✓" : now ? <span className="inline-block w-2.5 h-2.5 rounded-full border-2 border-lineStrong border-t-ink animate-spin" /> : "·"}
      </span>
      {text}
    </li>
  );
}

function Switch({ on, onChange, testid }: { on: boolean; onChange: (on: boolean) => void; testid: string }) {
  return (
    <button
      role="switch"
      aria-checked={on}
      className={"ml-auto relative w-[34px] h-5 rounded-full transition-colors " + (on ? "bg-ink" : "bg-lineStrong")}
      onClick={() => onChange(!on)}
      data-testid={testid}
    >
      <span className={"absolute top-0.5 w-4 h-4 rounded-full bg-panel transition-all " + (on ? "right-0.5" : "left-0.5")} />
    </button>
  );
}
