// Observability Phase 1 (UX-059 OB-1, OB-2, OB-4): every step of a turn on one time axis,
// the "where did the time go" tiles, one step's details, and the session's token usage.
// Reads the engine's recording endpoints (technical design §12); `refreshKey` moves when
// live step events or a turn's end arrive, so the panel follows a running turn.

import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  getRecordedTurns,
  getRecordedUsage,
  getStepDetail,
  getTurnSteps,
  type RecordedStep,
  type RecordedTurn,
} from "../api";
import { formatTokens } from "../usage";

const BUCKETS = ["waiting", "tools", "model", "auto_approver", "summary"] as const;

/** 0.1s, 3.4s, 41s, 2m 14s: the panel's one duration format. */
export function formatDuration(ms: number | null | undefined): string {
  if (ms == null || !Number.isFinite(ms)) return "—";
  if (ms > 0 && ms < 100) return "<0.1s";
  if (ms < 10_000) return `${(ms / 1000).toFixed(1)}s`;
  if (ms < 60_000) return `${Math.round(ms / 1000)}s`;
  const m = Math.floor(ms / 60_000);
  const s = Math.round((ms % 60_000) / 1000);
  return s ? `${m}m ${s}s` : `${m}m`;
}

/** The row's label, as the mock writes it: "Model · summary", "Auto-approver · allowed"… */
export function stepLabel(step: RecordedStep, t: (k: string, o?: any) => string): string {
  const a = step.attrs || {};
  switch (step.kind) {
    case "context":
      return t("steps.row.context");
    case "model": {
      const purpose = a.purpose && a.purpose !== "main" ? ` · ${t(`steps.purpose.${a.purpose}`, { defaultValue: a.purpose })}` : "";
      return t("steps.row.model") + purpose;
    }
    case "wait":
      return `${t("steps.row.wait")} · ${t(`steps.wait.${a.kind}`, { defaultValue: a.kind || "" })}`;
    case "auto_approver":
      return `${t("steps.row.auto_approver")} · ${t(`steps.result.${a.result}`, { defaultValue: a.result || "" })}`;
    case "summary": {
      const before = a.tokens_before, after = a.tokens_after;
      const arrow = before != null && after != null ? ` · ${formatTokens(before)}→${formatTokens(after)}` : "";
      return (step.name === "trim" ? t("steps.row.trim") : t("steps.row.summary")) + arrow;
    }
    case "notice":
      return t(`steps.notice.${step.name}`, { defaultValue: step.name });
    default:
      return step.name;
  }
}

/** Steps in canonical order, each nested child right under its parent. */
function nest(steps: RecordedStep[]): { step: RecordedStep; depth: number }[] {
  const ids = new Set(steps.map((s) => s.step_id));
  const children = new Map<string, RecordedStep[]>();
  for (const s of steps) {
    if (s.parent_step_id && ids.has(s.parent_step_id)) {
      children.set(s.parent_step_id, [...(children.get(s.parent_step_id) || []), s]);
    }
  }
  const out: { step: RecordedStep; depth: number }[] = [];
  const walk = (s: RecordedStep, depth: number) => {
    out.push({ step: s, depth });
    for (const c of children.get(s.step_id) || []) walk(c, depth + 1);
  };
  for (const s of steps) if (!s.parent_step_id || !ids.has(s.parent_step_id)) walk(s, 0);
  return out;
}

export function StepsPanel({
  sessionId,
  refreshKey = 0,
  onClose,
}: {
  sessionId: string;
  refreshKey?: number;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const [turns, setTurns] = useState<RecordedTurn[] | null>(null);
  const [turnIdx, setTurnIdx] = useState<number | null>(null);
  const [steps, setSteps] = useState<RecordedStep[]>([]);
  const [tab, setTab] = useState<"timeline" | "usage">("timeline");
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let live = true;
    getRecordedTurns(sessionId)
      .then((ts) => {
        if (!live) return;
        setTurns(ts);
        setError(false);
        // Follow the newest turn unless one was picked.
        setTurnIdx((i) => (i == null || i >= ts.length - 1 ? ts.length - 1 : i));
      })
      .catch(() => live && setError(true));
    return () => {
      live = false;
    };
  }, [sessionId, refreshKey]);

  const turn = turns && turnIdx != null && turnIdx >= 0 ? turns[turnIdx] : null;
  // A step's details belong to its turn: picking another turn closes them.
  useEffect(() => setSelected(null), [turn?.turn_id]);
  useEffect(() => {
    if (!turn) return;
    let live = true;
    getTurnSteps(sessionId, turn.turn_id).then((s) => live && setSteps(s)).catch(() => {});
    return () => {
      live = false;
    };
  }, [sessionId, turn?.turn_id, refreshKey]);

  const rows = useMemo(() => nest(steps), [steps]);
  const checks = steps.filter((s) => s.kind === "auto_approver" && !s.shadow);

  return (
    <div className="team-view steps-panel" data-testid="steps-panel">
      <div className="team-pane-header">
        <strong>{t("steps.title")}</strong>
        {turns && turns.length > 0 && turnIdx != null && (
          <span className="steps-turn-picker">
            <button aria-label={t("steps.prev_turn")} disabled={turnIdx <= 0} onClick={() => setTurnIdx(turnIdx - 1)}>‹</button>
            <span data-testid="steps-turn">{t("steps.turn_of", { n: turnIdx + 1, total: turns.length })}</span>
            <button aria-label={t("steps.next_turn")} disabled={turnIdx >= turns.length - 1} onClick={() => setTurnIdx(turnIdx + 1)}>›</button>
          </span>
        )}
        <button className="team-close" aria-label={t("steps.close")} onClick={onClose}>×</button>
      </div>

      {error && <p className="steps-empty">{t("steps.load_error")}</p>}
      {turns && turns.length === 0 && <p className="steps-empty" data-testid="steps-none">{t("steps.not_recorded")}</p>}

      {turn && (
        <div className="steps-body">
          <div className="steps-tiles" data-testid="steps-tiles">
            <Tile value={formatDuration(turn.buckets.total)} label={t("steps.bucket.total")} />
            {BUCKETS.filter((b) => turn.buckets[b] > 0).map((b) => (
              <Tile key={b} value={formatDuration(turn.buckets[b])} label={t(`steps.bucket.${b}`)} />
            ))}
            {turn.buckets.other_shown && <Tile value={formatDuration(turn.buckets.other)} label={t("steps.bucket.other")} />}
          </div>
          {checks.length > 0 && (
            <p className="steps-line" data-testid="steps-checks">
              {t("steps.checks_line", {
                count: checks.length,
                allowed: checks.filter((c) => c.attrs.result === "allow").length,
                asked: checks.filter((c) => c.attrs.result !== "allow" && c.attrs.result !== "deny").length,
                blocked: checks.filter((c) => c.attrs.result === "deny").length,
              })}
            </p>
          )}
          <div className="team-tabs steps-tabs" role="tablist">
            <button role="tab" aria-selected={tab === "timeline"} onClick={() => setTab("timeline")}>{t("steps.tab.timeline")}</button>
            <button role="tab" aria-selected={tab === "usage"} onClick={() => setTab("usage")}>{t("steps.tab.usage")}</button>
          </div>

          {tab === "timeline" && (
            <div className="steps-timeline" data-testid="steps-timeline">
              {rows.map(({ step, depth }) => (
                <StepRow
                  key={step.step_id}
                  step={step}
                  depth={depth}
                  turn={turn}
                  label={stepLabel(step, t)}
                  selected={selected === step.step_id}
                  onSelect={() => setSelected(selected === step.step_id ? null : step.step_id)}
                />
              ))}
              {selected && <StepDetails sessionId={sessionId} stepId={selected} turn={turn} />}
            </div>
          )}
          {tab === "usage" && <UsageView sessionId={sessionId} refreshKey={refreshKey} />}
        </div>
      )}
    </div>
  );
}

function Tile({ value, label }: { value: string; label: string }) {
  return (
    <div className="steps-tile">
      <strong>{value}</strong>
      <span>{label}</span>
    </div>
  );
}

function StepRow({ step, depth, turn, label, selected, onSelect }: {
  step: RecordedStep; depth: number; turn: RecordedTurn; label: string; selected: boolean; onSelect: () => void;
}) {
  const { t } = useTranslation();
  const total = Math.max(1, turn.buckets.total || turn.duration_ms || 1);
  const left = Math.min(100, Math.max(0, ((step.started_at - turn.started_at) / total) * 100));
  const width = step.duration_ms ? Math.min(100 - left, (step.duration_ms / total) * 100) : 0;
  const bad = ["error", "denied", "interrupted", "skipped"].includes(step.status);
  return (
    <button
      className={`steps-row kind-${step.kind}` + (selected ? " selected" : "") + (step.shadow ? " shadow" : "")}
      style={{ paddingLeft: 8 + depth * 14 }}
      onClick={onSelect}
      data-testid="steps-row"
    >
      <span className="steps-dot" />
      <span className="steps-label">
        {label}
        {bad && <span className={`steps-badge ${step.status}`}>{t(`steps.status.${step.status}`)}</span>}
        {step.after_turn && <span className="steps-badge">{t("steps.after_turn")}</span>}
      </span>
      <span className="steps-track">
        <span className="steps-bar" style={{ left: `${left}%`, width: width ? `${width}%` : undefined }} />
      </span>
      <span className="steps-time">{formatDuration(step.duration_ms)}</span>
    </button>
  );
}

function StepDetails({ sessionId, stepId, turn }: { sessionId: string; stepId: string; turn: RecordedTurn }) {
  const { t } = useTranslation();
  const [d, setD] = useState<any>(null);
  useEffect(() => {
    let live = true;
    getStepDetail(sessionId, stepId).then((s) => live && setD(s)).catch(() => {});
    return () => {
      live = false;
    };
  }, [sessionId, stepId]);
  if (!d) return <div className="steps-details">{t("steps.loading")}</div>;
  const a = d.attrs || {};
  const rows: [string, any][] = [
    [t("steps.detail.status"), t(`steps.status.${d.status}`, { defaultValue: d.status })],
    [t("steps.detail.started"), formatDuration(d.started_at - turn.started_at)],
    [t("steps.detail.duration"), formatDuration(d.duration_ms)],
  ];
  if (d.kind === "model") {
    rows.push([t("steps.detail.model"), [a.model, a.provider_title].filter(Boolean).join(" · ")]);
    if (a.provider_facts?.served_model) rows.push([t("steps.detail.served"), a.provider_facts.served_model]);
    if (a.ttft_ms != null) rows.push([t("steps.detail.ttft"), formatDuration(a.ttft_ms)]);
    if (a.finish_reason) rows.push([t("steps.detail.stopped"), a.finish_reason]);
    if (a.tools_offered) rows.push([t("steps.detail.tools"), `${a.tools_offered} · ${a.tools_hash || ""}`]);
    if (turn.stamp?.persona_fp) rows.push([t("steps.detail.coworker"), `ⓕ${turn.stamp.persona_fp}`]);
    if (a.error) rows.push([t("steps.detail.error"), `${a.error.type}${a.error.text ? ` · ${a.error.text}` : ""}`]);
  }
  if (d.kind === "tool") {
    if (a.risk_class) rows.push([t("steps.detail.risk"), a.risk_class]);
    if (a.exit_code != null) rows.push([t("steps.detail.exit"), a.exit_code]);
    if (a.decision?.origin) rows.push([t("steps.detail.origin"), t(`steps.origin.${a.decision.origin}`, { defaultValue: a.decision.origin })]);
    if (a.sandbox) rows.push([t("steps.detail.blocked"), a.sandbox.blocked_known ? a.sandbox.blocked_count : t("steps.detail.unknown")]);
  }
  if (d.kind === "wait") {
    rows.push([t("steps.detail.where"), a.where], [t("steps.detail.outcome"), a.outcome]);
    if (d.content?.resolved_by) rows.push([t("steps.detail.answered_by"), d.content.resolved_by]);
  }
  if (d.kind === "auto_approver") {
    rows.push([t("steps.detail.mode"), a.mode], [t("steps.detail.reviewer"), a.reviewer?.model || a.reviewer?.kind]);
    if (d.content?.reason) rows.push([t("steps.detail.reason"), d.content.reason]);
  }
  if (d.kind === "context") {
    rows.push([t("steps.detail.session_prompt"), a.session_prompt_hash || "—"], [t("steps.detail.size"), a.size_chars]);
  }
  const u = a.usage;
  return (
    <div className="steps-details" data-testid="steps-details">
      <h4>{stepLabel(d, t)}</h4>
      <dl>
        {rows.map(([k, v]) => (
          <div key={k}><dt>{k}</dt><dd>{String(v ?? "—")}</dd></div>
        ))}
      </dl>
      {u && (
        <div className="steps-tokens" data-testid="steps-tokens">
          {u.reported ? (
            <span>
              {t("steps.tokens_line", {
                cache: formatTokens(u.cache_read || 0),
                written: u.cache_write == null ? "—" : formatTokens(u.cache_write),
                fresh: formatTokens(u.input_fresh || 0),
                out: formatTokens(u.output || 0),
              })}
              {u.reasoning != null && ` · ${t("steps.reasoning", { n: formatTokens(u.reasoning) })}`}
            </span>
          ) : (
            <span>{t("steps.tokens_not_reported")}</span>
          )}
        </div>
      )}
      {a.decision?.path && (
        <ol className="steps-path">
          {a.decision.path.map((p: any, i: number) => (
            <li key={i}>{t(`steps.stage.${p.stage}`, { defaultValue: p.stage })}: {p.outcome}{p.detail ? ` (${p.detail})` : ""}</li>
          ))}
        </ol>
      )}
      {d.message && d.message.content && (
        <pre className="steps-pre">{d.message.content}{d.message.cut ? "\n…" : ""}</pre>
      )}
      {d.exchange_rebuilt?.length > 0 && (
        <details className="steps-exchange">
          <summary>{t("steps.exchange", { count: d.exchange_rebuilt.length })}</summary>
          {d.exchange_rebuilt.map((x: any, i: number) => (
            <pre className="steps-pre" key={i}>{JSON.stringify(x.request, null, 2)}{"\n\n"}{x.response}</pre>
          ))}
        </details>
      )}
    </div>
  );
}

function UsageView({ sessionId, refreshKey }: { sessionId: string; refreshKey: number }) {
  const { t } = useTranslation();
  const [u, setU] = useState<any>(null);
  useEffect(() => {
    let live = true;
    getRecordedUsage(sessionId).then((x) => live && setU(x)).catch(() => {});
    return () => {
      live = false;
    };
  }, [sessionId, refreshKey]);
  if (!u) return <div className="steps-usage">{t("steps.loading")}</div>;
  const purposes = Object.entries(u.by_purpose || {}) as [string, number][];
  const max = Math.max(1, ...purposes.map(([, n]) => n));
  return (
    <div className="steps-usage" data-testid="steps-usage">
      <div className="steps-tiles">
        <Tile value={(u.at_least ? "≥ " : "") + formatTokens(u.totals?.input || 0)} label={t("steps.usage.input")} />
        <Tile value={formatTokens(u.totals?.output || 0)} label={t("steps.usage.output")} />
        <Tile value={u.cache_share == null ? "—" : `${Math.round(u.cache_share * 100)}%`} label={t("steps.usage.from_cache")} />
      </div>
      {u.unreported_calls > 0 && <p className="steps-line">{t("steps.usage.unreported", { count: u.unreported_calls })}</p>}
      {u.failed_calls > 0 && <p className="steps-line">{t("steps.usage.failed", { count: u.failed_calls })}</p>}
      {u.overflow_calls > 0 && <p className="steps-line">{t("steps.usage.overflow", { count: u.overflow_calls })}</p>}
      <h4>{t("steps.usage.by_purpose")}</h4>
      {purposes.map(([p, n]) => (
        <div className="steps-purpose" key={p}>
          <span>{t(`steps.purpose_long.${p}`, { defaultValue: p })}</span>
          <span>{formatTokens(n)}</span>
          <span className="steps-purpose-bar"><span style={{ width: `${(n / max) * 100}%` }} /></span>
        </div>
      ))}
      <h4>{t("steps.usage.by_model")}</h4>
      <table className="steps-models">
        <thead><tr><th>{t("steps.usage.model")}</th><th>{t("steps.usage.in")}</th><th>{t("steps.usage.cache")}</th><th>{t("steps.usage.out")}</th></tr></thead>
        <tbody>
          {Object.entries(u.by_model_parts || {}).map(([m, p]: [string, any]) => (
            <tr key={m}><td>{m}</td><td>{formatTokens(p.input)}</td><td>{formatTokens(p.cache)}</td><td>{formatTokens(p.output)}</td></tr>
          ))}
        </tbody>
      </table>
      {u.context && u.context.window && (
        <>
          <h4>{t("steps.usage.context_now")}</h4>
          <div className="steps-purpose-bar wide"><span style={{ width: `${Math.min(100, (u.context.tokens / u.context.window) * 100)}%` }} /></div>
          <p className="steps-line">{t("steps.usage.context_line", { used: formatTokens(u.context.tokens), window: formatTokens(u.context.window) })}</p>
        </>
      )}
    </div>
  );
}
