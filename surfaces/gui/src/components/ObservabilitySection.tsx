// Settings ▸ Machine ▸ Observability (OB-6, mocks UX-059 ob6 and ob6b; phase-2 design §10).
// Four parts: On this machine (record steps, keep for, save one session's recording), Send
// recordings to (Off, Langfuse, an OpenTelemetry endpoint), What to include, and Status.
// Everything is the chosen machine's, read and written through /v1/observability/* (the
// machine proxy for a remote one). Keys and header values are write-only: the page shows
// the masked hints the machine sends back, and an empty field never clears a stored key.
// When a company policy sets it, the page is read-only and says who set it (ob6b).
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  downloadSessionRecording,
  getMachineSessions,
  getObservabilitySettings,
  getObservabilityStatus,
  sendObservabilityTest,
  setObservabilitySettings,
  type Machine,
  type ObservabilityInclude,
  type ObservabilitySettings,
  type ObservabilityStatus,
} from "../api";
import { Toggle } from "./Toggle";
import { PanelHead } from "./IntegrationsView";

const CARD = "rounded-xl2 border border-line bg-panel";
const SEC = "text-meta font-medium text-muted mt-6 mb-2";
const ROW = "flex items-center gap-4 px-4 py-3.5";
const ROW_TITLE = "text-ui font-medium text-ink";
const ROW_SUB = "text-meta text-muted mt-0.5 leading-relaxed";
const INPUT =
  "flex-1 min-w-0 px-3 py-2 rounded-lg border border-line bg-paper text-ui text-ink font-mono outline-none focus:border-accent placeholder:text-faint";
const BTN_SMALL = "text-meta px-2.5 py-1.5 rounded-lg border border-line bg-paper hover:border-lineStrong shrink-0 disabled:opacity-40";
const PILL = "inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-meta border shrink-0";
const PILL_TONE = {
  ok: "bg-okSoft text-ok border-okLine",
  warn: "bg-warnSoft text-warnInk border-transparent",
  bad: "bg-dangerSoft text-danger border-transparent",
  plain: "bg-chrome text-muted border-line",
};
const MAX_HEADERS = 8;
type Kind = "off" | "langfuse" | "otlp";
const INCLUDE_ROWS: (keyof ObservabilityInclude)[] = ["messages", "tool_io", "context", "names"];

function Pill({ tone, children, testId }: { tone: keyof typeof PILL_TONE; children: React.ReactNode; testId?: string }) {
  return (
    <span className={`${PILL} ${PILL_TONE[tone]}`} data-testid={testId}>
      {children}
    </span>
  );
}

function hostOf(url: string): string {
  try {
    return new URL(url).host || url;
  } catch {
    return url;
  }
}

export function ObservabilitySection({ machine }: { machine?: Machine | null }) {
  const { t, i18n } = useTranslation();
  const mid = machine?.id ?? null;
  const [cfg, setCfg] = useState<ObservabilitySettings | null>(null);
  const [status, setStatus] = useState<ObservabilityStatus | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  const [error, setError] = useState("");
  // The card shown under "Send recordings to": the saved choice, or one being filled in.
  const [kind, setKind] = useState<Kind>("off");
  const [host, setHost] = useState("");
  const [publicKey, setPublicKey] = useState("");
  const [secretKey, setSecretKey] = useState("");
  const [url, setUrl] = useState("");
  const [headers, setHeaders] = useState<{ name: string; value: string }[]>([]);
  const [testing, setTesting] = useState(false);
  const [test, setTest] = useState<{ ok: boolean; status: number; text: string; ms?: number } | null>(null);
  const [saving, setSaving] = useState<{ sessions: { session_id: string; title?: string | null }[]; pick: string } | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const loaded = useRef(false);

  const adopt = (s: ObservabilitySettings) => {
    setCfg(s);
    const d = s.destination;
    if (!loaded.current || d.kind !== "off") setKind(d.kind);
    if (d.kind === "langfuse") setHost(d.host);
    if (d.kind === "otlp") {
      setUrl(d.url);
      setHeaders(d.header_names.map((name) => ({ name, value: "" })));
    }
    loaded.current = true;
  };

  useEffect(() => {
    loaded.current = false;
    getObservabilitySettings(mid)
      .then(adopt)
      .catch(() => setLoadFailed(true));
  }, [mid]); // eslint-disable-line react-hooks/exhaustive-deps

  // The status card follows the machine while the page is open.
  useEffect(() => {
    let live = true;
    const load = () =>
      getObservabilityStatus(mid)
        .then((s) => live && setStatus(s))
        .catch(() => {});
    load();
    const timer = window.setInterval(() => {
      load();
      setNow(Date.now());
    }, 5000);
    return () => {
      live = false;
      window.clearInterval(timer);
    };
  }, [mid]);

  const save = async (patch: Parameters<typeof setObservabilitySettings>[0]) => {
    const res = await setObservabilitySettings(patch, mid);
    if (!res.ok) {
      setError(res.error || t("obs.error_save"));
      return false;
    }
    setError("");
    adopt(res as ObservabilitySettings);
    getObservabilityStatus(mid).then(setStatus).catch(() => {});
    return true;
  };

  if (loadFailed) {
    return (
      <section>
        <PanelHead title={t("obs.title")} sub={t("obs.sub")} />
        <p className="text-ui text-muted">{t("obs.load_failed")}</p>
      </section>
    );
  }
  if (!cfg) return null;

  const managed = cfg.managed;
  const saved = cfg.destination;
  const validUrl = (v: string) => /^https?:\/\/[^/\s]+/.test(v.trim());

  // -- Send recordings to ----------------------------------------------------------------
  const choose = (k: Kind) => {
    setKind(k);
    setTest(null);
    if (k === "off") void save({ destination: { kind: "off" } });
    else if (k === "langfuse" && validUrl(host)) void save({ destination: { kind: "langfuse", host: host.trim() } });
    else if (k === "otlp" && validUrl(url))
      void save({ destination: { kind: "otlp", url: url.trim(), header_names: headers.map((h) => h.name).filter(Boolean) } });
  };

  const saveLangfuse = async () => {
    if (!validUrl(host)) {
      if (host.trim()) setError(t("obs.error_address"));
      return false;
    }
    const secrets: { public_key?: string; secret_key?: string } = {};
    if (publicKey.trim()) secrets.public_key = publicKey.trim();
    if (secretKey.trim()) secrets.secret_key = secretKey.trim();
    const changed = saved.kind !== "langfuse" || saved.host !== host.trim() || Object.keys(secrets).length > 0;
    if (!changed) return true;
    const ok = await save({ destination: { kind: "langfuse", host: host.trim() }, ...(Object.keys(secrets).length ? { secrets } : {}) });
    if (ok) {
      setPublicKey("");
      setSecretKey("");
    }
    return ok;
  };

  const saveOtlp = async (rows = headers) => {
    if (!validUrl(url)) {
      if (url.trim()) setError(t("obs.error_address"));
      return false;
    }
    const names = rows.map((h) => h.name.trim()).filter(Boolean);
    const values = Object.fromEntries(rows.filter((h) => h.name.trim() && h.value).map((h) => [h.name.trim(), h.value]));
    const ok = await save({
      destination: { kind: "otlp", url: url.trim(), header_names: names },
      ...(Object.keys(values).length ? { secrets: { headers: values } } : {}),
    });
    if (ok) setHeaders(names.map((name) => ({ name, value: "" })));
    return ok;
  };

  const runTest = async () => {
    setTesting(true);
    setTest(null);
    const ready = kind === "langfuse" ? await saveLangfuse() : kind === "otlp" ? await saveOtlp() : false;
    if (ready) {
      try {
        setTest(await sendObservabilityTest(mid));
      } catch {
        setTest({ ok: false, status: 0, text: t("obs.test.unreachable") });
      }
    }
    setTesting(false);
  };

  const testPill = test ? (
    test.ok ? (
      <Pill tone="ok" testId="obs-test-result">
        ✓ {t(kind === "langfuse" ? "obs.test.ok_langfuse" : "obs.test.ok_otlp", { ms: test.ms ?? 0 })}
      </Pill>
    ) : (
      <Pill tone="bad" testId="obs-test-result">
        {test.status === 401 || test.status === 403 ? t("obs.refused_key") : test.text}
      </Pill>
    )
  ) : null;

  // -- Status --------------------------------------------------------------------------------
  const relative = (sec: number) => {
    const diff = Math.round(sec - now / 1000);
    const rtf = new Intl.RelativeTimeFormat(i18n.language === "zh" ? "zh" : "en", { numeric: "auto" });
    const abs = Math.abs(diff);
    if (abs < 60) return rtf.format(diff, "second");
    if (abs < 3600) return rtf.format(Math.round(diff / 60), "minute");
    if (abs < 86400) return rtf.format(Math.round(diff / 3600), "hour");
    return rtf.format(Math.round(diff / 86400), "day");
  };
  const fmt = (n: number) => n.toLocaleString(i18n.language === "zh" ? "zh-CN" : "en-US");
  const statusCard = (
    <div className={CARD} data-testid="obs-status">
      <div className={ROW}>
        <div className="flex-1 min-w-0">
          <div className={ROW_TITLE}>
            {!status || status.state === "off"
              ? t("obs.status.off_title")
              : status.last_sent_at
                ? t("obs.status.last_sent", { when: relative(status.last_sent_at) })
                : t("obs.status.nothing_yet")}
          </div>
          <div className={ROW_SUB}>
            {!status || status.state === "off"
              ? t("obs.status.off_sub")
              : t("obs.status.counts", { sent: fmt(status.sent_today), failed: fmt(status.failed), dropped: fmt(status.dropped) }) +
                " " +
                (status.state === "failed"
                  ? t("obs.status.failed_sub")
                  : t("obs.status.resumes"))}
          </div>
          {status?.outside_today?.calls ? (
            <div className="text-meta text-muted mt-1" data-testid="obs-outside">
              {t("obs.status.outside", { count: status.outside_today.calls, tokens: fmt(status.outside_today.tokens) })}
            </div>
          ) : null}
          {status && (status.state === "retrying" || status.state === "failed") && status.last_error ? (
            <div className="text-meta text-danger mt-1" data-testid="obs-status-error">
              {status.last_error === "The destination refused the key" ? t("obs.refused_key") : status.last_error === "Address not found" ? t("obs.not_found") : status.last_error}
            </div>
          ) : null}
        </div>
        {status ? (
          <Pill
            tone={status.state === "sending" || status.state === "managed" ? "ok" : status.state === "retrying" ? "warn" : status.state === "failed" ? "bad" : "plain"}
            testId="obs-status-pill"
          >
            {t(`obs.state.${status.state}`)}
          </Pill>
        ) : null}
      </div>
    </div>
  );

  // -- The company manages it (ob6b) -------------------------------------------------------------
  if (managed) {
    const destName = saved.kind === "langfuse" ? "Langfuse" : saved.kind === "otlp" ? t("obs.dest.otlp") : t("obs.dest.off");
    const place = saved.kind === "langfuse" ? hostOf(saved.host) : saved.kind === "otlp" ? hostOf(saved.url) : "";
    const items = INCLUDE_ROWS.filter((k) => cfg.include[k]).map((k) => t(`obs.callout_item.${k}`));
    const listed = items.length > 1 ? items.slice(0, -1).join(t("obs.list_sep")) + t("obs.list_and") + items[items.length - 1] : items[0] ?? "";
    const sentWhat = listed.charAt(0).toUpperCase() + listed.slice(1);
    return (
      <section data-testid="obs-section">
        <h2 className="text-title font-semibold tracking-tight mb-3">{t("obs.title")}</h2>
        <div className="flex gap-3 items-start rounded-xl2 border border-line bg-chrome px-4 py-3.5" data-testid="obs-managed">
          <svg viewBox="0 0 24 24" className="w-[18px] h-[18px] mt-0.5 shrink-0 text-muted" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true">
            <path d="M12 3l8 4v5c0 5-3.5 8-8 9-4.5-1-8-4-8-9V7z" />
          </svg>
          <div>
            <div className="text-ui font-medium text-ink">{t("obs.managed.by", { by: managed.by })}</div>
            <div className="text-ui text-muted mt-0.5">{t("obs.managed.sub")}</div>
          </div>
        </div>
        <div className={SEC}>{t("obs.sec.send_to")}</div>
        <div className={CARD}>
          <div className={ROW}>
            <div className="flex-1 min-w-0">
              <div className={ROW_TITLE}>{place ? `${destName} · ${place}` : destName}</div>
              <div className={ROW_SUB}>
                {[managed.name ? t("obs.managed.set_by", { name: managed.name }) : t("obs.managed.set_by_policy"), managed.at].filter(Boolean).join(" · ")}
              </div>
            </div>
            <Pill tone="plain">🔒 {t("obs.managed.locked")}</Pill>
          </div>
        </div>
        <div className={SEC}>{t("obs.sec.included")}</div>
        <div className={`${CARD} divide-y divide-line`}>
          <div className={ROW}>
            <div className={`flex-1 ${ROW_TITLE}`}>{t("obs.include.metadata_short")}</div>
            <Toggle checked={saved.kind !== "off"} onChange={() => {}} disabled />
          </div>
          {INCLUDE_ROWS.map((k) => (
            <div key={k} className={ROW}>
              <div className="flex-1 min-w-0">
                <div className={ROW_TITLE}>{t(`obs.include.${k}`)}</div>
                {k === "names" && cfg.include.names ? <div className={ROW_SUB}>{t("obs.include.names_on")}</div> : null}
              </div>
              <Toggle checked={cfg.include[k]} onChange={() => {}} disabled />
            </div>
          ))}
        </div>
        {items.length && saved.kind !== "off" ? (
          <div className="mt-3 rounded-xl2 bg-accentSoft text-accent text-ui px-4 py-3" data-testid="obs-managed-callout">
            {t("obs.managed.callout", { what: sentWhat, by: managed.by, where: destName })}
          </div>
        ) : null}
        <div className={SEC}>{t("obs.sec.status")}</div>
        {statusCard}
      </section>
    );
  }

  // -- The person's own settings (ob6) -------------------------------------------------------------
  const destCard = (k: Kind, title: string, sub: string) => (
    <button
      type="button"
      key={k}
      data-testid={`obs-dest-${k}`}
      aria-pressed={kind === k}
      onClick={() => choose(k)}
      className={
        "text-left rounded-xl2 border px-4 py-3.5 transition-colors " +
        (kind === k ? "border-accent bg-accentSoft ring-1 ring-accent" : "border-line bg-panel hover:border-lineStrong")
      }
    >
      <div className="text-ui font-medium text-ink">{title}</div>
      <div className="text-meta text-muted mt-0.5 leading-relaxed">{sub}</div>
    </button>
  );
  const field = (label: string, input: React.ReactNode) => (
    <div className="flex items-center gap-4 px-4 py-2.5">
      <div className="w-44 shrink-0 text-ui text-ink">{label}</div>
      {input}
    </div>
  );
  const sendingOn = saved.kind !== "off";

  return (
    <section data-testid="obs-section">
      <PanelHead title={t("obs.title")} sub={t("obs.sub")} />

      <div className={SEC}>{t("obs.sec.machine")}</div>
      <div className={`${CARD} divide-y divide-line`}>
        <div className={ROW}>
          <div className="flex-1 min-w-0">
            <div className={ROW_TITLE}>{t("obs.record.title")}</div>
            <div className={ROW_SUB}>{cfg.record ? t("obs.record.sub") : t("obs.record.off")}</div>
          </div>
          <span data-testid="obs-record">
            <Toggle checked={cfg.record} onChange={(v) => void save({ record: v })} />
          </span>
        </div>
        <div className={ROW}>
          <div className="flex-1 min-w-0">
            <div className={ROW_TITLE}>{t("obs.keep.title")}</div>
            <div className={ROW_SUB}>{t("obs.keep.sub")}</div>
          </div>
          <div className="inline-flex rounded-lg border border-line overflow-hidden shrink-0" role="radiogroup" aria-label={t("obs.keep.title")}>
            {([7, 30, 90] as const).map((d) => (
              <button
                key={d}
                type="button"
                role="radio"
                aria-checked={cfg.retention_days === d}
                data-testid={`obs-keep-${d}`}
                onClick={() => void save({ retention_days: d })}
                className={
                  "text-meta px-3 py-1.5 border-l border-line first:border-l-0 " +
                  (cfg.retention_days === d ? "bg-accentSoft text-accent font-medium" : "bg-paper text-muted hover:text-ink")
                }
              >
                {t("obs.keep.days", { count: d })}
              </button>
            ))}
          </div>
        </div>
        <div className={ROW}>
          <div className="flex-1 min-w-0">
            <div className={ROW_TITLE}>{t("obs.file.title")}</div>
            <div className={ROW_SUB}>{t("obs.file.sub")}</div>
            {saving ? (
              <div className="flex items-center gap-2 mt-2.5">
                <select
                  className="flex-1 min-w-0 px-2.5 py-1.5 rounded-lg border border-line bg-paper text-ui text-ink"
                  value={saving.pick}
                  aria-label={t("obs.file.pick")}
                  onChange={(e) => setSaving({ ...saving, pick: e.target.value })}
                >
                  {saving.sessions.length === 0 ? <option value="">{t("obs.file.none")}</option> : null}
                  {saving.sessions.map((s) => (
                    <option key={s.session_id} value={s.session_id}>
                      {s.title || s.session_id}
                    </option>
                  ))}
                </select>
                <button
                  type="button"
                  className={BTN_SMALL}
                  disabled={!saving.pick}
                  onClick={() => {
                    void downloadSessionRecording(saving.pick, mid).then((ok) => (ok ? setSaving(null) : setError(t("obs.file.failed"))));
                  }}
                >
                  {t("obs.file.save")}
                </button>
                <button type="button" className={BTN_SMALL} onClick={() => setSaving(null)}>
                  {t("obs.file.cancel")}
                </button>
              </div>
            ) : null}
          </div>
          {saving ? null : (
            <button
              type="button"
              className={BTN_SMALL}
              data-testid="obs-file"
              onClick={() =>
                void getMachineSessions(mid)
                  .then((sessions) => setSaving({ sessions, pick: sessions[0]?.session_id ?? "" }))
                  .catch(() => setError(t("obs.file.failed")))
              }
            >
              {t("obs.file.button")}
            </button>
          )}
        </div>
      </div>

      <div className={SEC}>{t("obs.sec.send_to")}</div>
      <div className="grid grid-cols-3 gap-2.5">
        {destCard("off", t("obs.dest.off"), t("obs.dest.off_sub"))}
        {destCard("langfuse", "Langfuse", t("obs.dest.langfuse_sub"))}
        {destCard("otlp", t("obs.dest.otlp"), t("obs.dest.otlp_sub"))}
      </div>
      {!cfg.record && kind !== "off" ? <p className="text-meta text-warnInk mt-2">{t("obs.record_off_note")}</p> : null}

      {kind === "langfuse" ? (
        <div className={`${CARD} mt-2.5 divide-y divide-line`} data-testid="obs-langfuse">
          {field(
            t("obs.langfuse.address"),
            <input
              className={INPUT}
              value={host}
              placeholder="https://cloud.langfuse.com"
              aria-label={t("obs.langfuse.address")}
              onChange={(e) => setHost(e.target.value)}
              onBlur={() => void saveLangfuse()}
            />,
          )}
          {field(
            t("obs.langfuse.public_key"),
            <input
              className={INPUT}
              value={publicKey}
              placeholder={cfg.secrets.public_key || "pk-lf-…"}
              aria-label={t("obs.langfuse.public_key")}
              autoComplete="off"
              onChange={(e) => setPublicKey(e.target.value)}
              onBlur={() => void (publicKey.trim() && saveLangfuse())}
            />,
          )}
          {field(
            t("obs.langfuse.secret_key"),
            <input
              className={INPUT}
              type="password"
              value={secretKey}
              placeholder={(cfg.secrets.secret_key ? cfg.secrets.secret_key + "  " : "sk-lf-…  ") + t("obs.kept_note")}
              aria-label={t("obs.langfuse.secret_key")}
              autoComplete="off"
              onChange={(e) => setSecretKey(e.target.value)}
              onBlur={() => void (secretKey.trim() && saveLangfuse())}
            />,
          )}
          {field(
            "",
            <div className="flex items-center gap-2 flex-wrap">
              <button type="button" className={BTN_SMALL} data-testid="obs-test" disabled={testing || !host.trim()} onClick={() => void runTest()}>
                {testing ? t("obs.test.sending") : t("obs.test.button")}
              </button>
              {testPill}
            </div>,
          )}
        </div>
      ) : kind === "otlp" ? (
        <div className={`${CARD} mt-2.5 divide-y divide-line`} data-testid="obs-otlp">
          {field(
            t("obs.otlp.url"),
            <input
              className={INPUT}
              value={url}
              placeholder="https://collector.example.com/v1/traces"
              aria-label={t("obs.otlp.url")}
              onChange={(e) => setUrl(e.target.value)}
              onBlur={() => void saveOtlp()}
            />,
          )}
          {headers.map((h, i) => (
            <div key={i} className="flex items-center gap-2 px-4 py-2.5">
              <input
                className={`${INPUT} max-w-[11rem]`}
                value={h.name}
                placeholder={t("obs.otlp.header_name")}
                aria-label={t("obs.otlp.header_name")}
                onChange={(e) => setHeaders(headers.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)))}
                onBlur={() => void (h.name.trim() && h.value && saveOtlp())}
              />
              <input
                className={INPUT}
                type="password"
                value={h.value}
                placeholder={(cfg.secrets.headers?.[h.name] || "") + (cfg.secrets.headers?.[h.name] ? "  " + t("obs.kept_note") : t("obs.otlp.header_value"))}
                aria-label={t("obs.otlp.header_value")}
                autoComplete="off"
                onChange={(e) => setHeaders(headers.map((x, j) => (j === i ? { ...x, value: e.target.value } : x)))}
                onBlur={() => void (h.name.trim() && h.value && saveOtlp())}
              />
              <button
                type="button"
                className={BTN_SMALL}
                aria-label={t("common.remove")}
                onClick={() => {
                  const rest = headers.filter((_, j) => j !== i);
                  setHeaders(rest);
                  void saveOtlp(rest);
                }}
              >
                ×
              </button>
            </div>
          ))}
          <div className="flex items-center gap-2 flex-wrap px-4 py-2.5">
            <button
              type="button"
              className={BTN_SMALL}
              disabled={headers.length >= MAX_HEADERS}
              onClick={() => setHeaders([...headers, { name: "", value: "" }])}
            >
              {t("obs.otlp.add_header")}
            </button>
            <button type="button" className={BTN_SMALL} data-testid="obs-test" disabled={testing || !url.trim()} onClick={() => void runTest()}>
              {testing ? t("obs.test.sending") : t("obs.test.button")}
            </button>
            {testPill}
          </div>
        </div>
      ) : null}
      {error ? (
        <p className="text-meta text-danger mt-2" data-testid="obs-error">
          {error}
        </p>
      ) : null}

      <div className={SEC}>{t("obs.sec.include")}</div>
      <div className={`${CARD} divide-y divide-line`}>
        <div className={ROW}>
          <div className="flex-1 min-w-0">
            <div className={ROW_TITLE}>{t("obs.include.metadata")}</div>
            <div className={ROW_SUB}>{t("obs.include.metadata_sub")}</div>
          </div>
          <span data-testid="obs-include-metadata">
            <Toggle checked={sendingOn} onChange={() => {}} disabled title={t("obs.include.metadata_locked")} />
          </span>
        </div>
        {INCLUDE_ROWS.map((k) => (
          <div key={k} className={ROW}>
            <div className="flex-1 min-w-0">
              <div className={ROW_TITLE}>{t(`obs.include.${k}`)}</div>
              <div className={ROW_SUB}>{t(`obs.include.${k}_sub`)}</div>
            </div>
            <span data-testid={`obs-include-${k}`}>
              <Toggle checked={cfg.include[k]} onChange={(v) => void save({ include: { [k]: v } })} />
            </span>
          </div>
        ))}
      </div>
      <p className="text-meta text-faint mt-2 leading-relaxed">{t("obs.never_sent")}</p>

      <div className={SEC}>{t("obs.sec.status")}</div>
      {statusCard}
    </section>
  );
}
