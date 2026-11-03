// Observability Phase 1 (UX-059 OB-1, OB-2, OB-11, OB-4): every step of a turn on one time
// axis, the "where did the time go" tiles, one step's details beside the list, and the
// session's token usage. Laid out and styled as the UX-059 mock (docs/ux-improvements/mocks/
// UX-059-observability-desktop.html). Reads the engine's recording endpoints (technical design
// §12); `refreshKey` moves when live step events or a turn's end arrive, so the panel follows a
// running turn.

import { Fragment, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  getRecordedTurns,
  getRecordedUsage,
  getStepDetail,
  getTurnIssues,
  getTurnSteps,
  markIssuesSeen,
  type RecordedStep,
  type RecordedTurn,
  type TurnIssue,
  type TurnIssues,
} from "../api";
import { formatTokens } from "../usage";

type T = (k: string, o?: any) => string;

const BUCKETS = ["waiting", "tools", "model", "auto_approver", "summary", "sandbox"] as const;

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

/** A moment in the turn as the mock writes it: 00:52.1 */
function clock(ms: number): string {
  const v = Math.max(0, ms);
  const m = Math.floor(v / 60_000);
  const s = (v % 60_000) / 1000;
  return `${String(m).padStart(2, "0")}:${s.toFixed(1).padStart(4, "0")}`;
}

/** Axis labels for a turn of `total` ms: five marks, as the mock's 0 · 1m · 2m · 3m · 4m. */
function ticks(total: number): string[] {
  return [0, 1, 2, 3, 4].map((i) => (i === 0 ? "0" : formatDuration((total * i) / 4)));
}

const MODE_KEYS: Record<string, string> = {
  discuss: "composer.mode.discuss", interactive: "composer.mode.interactive", plan: "steps.mode.plan",
  "auto-approve": "composer.mode.auto_approve", "bypass-approvals": "composer.mode.auto", auto: "composer.mode.auto",
  "dangerously-bypass-approvals": "steps.mode.dangerous",
};

/** Who reviews in Auto-approve or Shadow evaluation: the session model by name, or the service. */
export function reviewerLabel(r: RecordedTurn["reviewer"], t: T): string {
  if (!r || !r.kind) return "";
  if (r.kind === "session_model") return r.model ? t("steps.reviewer.session_model_named", { model: r.model }) : t("steps.reviewer.session_model");
  return t(`steps.reviewer.${r.kind}`, { defaultValue: r.kind });
}

/** The mode pill (OB-1): the approval mode, and in Auto-approve or Shadow evaluation the reviewer. */
export function modeLabel(turn: RecordedTurn, t: T): string {
  if (!turn.approval_mode) return "";
  const mode = t(MODE_KEYS[turn.approval_mode] || "", { defaultValue: turn.approval_mode });
  const r = turn.reviewer;
  const who = reviewerLabel(r, t);
  if (r?.active && who) return `${mode} · ${who}`;
  if (r?.shadow && who) return `${mode} · ${t("steps.reviewer.shadow", { reviewer: who })}`;
  return mode;
}

/** The header's stamp line (OB-12, OB-14, OB-15): machine · sandbox · coworker · model and provider. */
export function stampLine(stamp: Record<string, any> | undefined, t: T): string {
  if (!stamp) return "";
  const parts: string[] = [];
  const m = stamp.machine;
  if (m) {
    const where = m.kind && m.kind !== "this_machine" ? ` (${t(`steps.machine.${m.kind}`, { defaultValue: m.kind })})` : "";
    parts.push(`${m.name || t("steps.machine.this_machine")}${where}${m.app_version ? ` · ${m.app_version}` : ""}`);
  }
  const sb = stamp.sandbox;
  if (sb) {
    parts.push(!sb.enforcement || sb.enforcement === "none" || sb.provider === "direct"
      ? t("steps.sandbox.none")
      : [sb.provider, t(`steps.sandbox.${sb.enforcement}`, { defaultValue: sb.enforcement }),
         sb.profile ? t("steps.sandbox.network", { profile: sb.profile }) : ""].filter(Boolean).join(" · "));
  }
  if (stamp.persona_fp) parts.push(`ⓕ${stamp.persona_fp}`);
  const md = stamp.model;
  if (md?.id) parts.push([md.id, md.provider_title].filter(Boolean).join(" · "));
  return parts.join("  ·  ");
}

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

/** The row's label, as the mock writes it: "Model · summary", "Auto-approver · allowed"… */
export function stepLabel(step: RecordedStep, t: T): string {
  const a = step.attrs || {};
  switch (step.kind) {
    case "context":
      return t("steps.row.context");
    case "model": {
      const purpose = a.purpose && a.purpose !== "main" ? ` · ${t(`steps.purpose.${a.purpose}`, { defaultValue: a.purpose })}` : "";
      return t("steps.row.model") + purpose;
    }
    case "wait":
      return `${a.where === "inbox" ? t("steps.row.wait_inbox") : t("steps.row.wait")} · ${t(`steps.wait.${a.kind}`, { defaultValue: a.kind || "" })}`;
    case "auto_approver": {
      const result = step.status === "error" ? "could_not_run" : a.result;
      if (step.shadow && result !== "could_not_run") {
        return `${t("steps.row.shadow")} · ${t(`steps.would.${result}`, { defaultValue: result || "" })}`;
      }
      return `${t("steps.row.auto_approver")} · ${t(`steps.result.${result}`, { defaultValue: result || "" })}`;
    }
    case "summary": {
      const before = a.tokens_before, after = a.tokens_after;
      const arrow = before != null && after != null ? ` · ${formatTokens(before)}→${formatTokens(after)}` : "";
      return (step.name === "trim" ? t("steps.row.trim") : t("steps.row.summary")) + arrow;
    }
    case "sandbox":
      return t("steps.row.sandbox_setup");
    case "notice":
      if (step.name === "sandbox_restarted" && a.how === "regrant") return t("steps.notice.sandbox_regranted");
      if (step.name === "recording_gap" && (a.cause === "restart" || a.cause === "stopped")) return t(`steps.notice.recording_gap_${a.cause}`);
      return t(`steps.notice.${step.name}`, { defaultValue: step.name });
    default:
      return step.name;
  }
}

type Flag = { cls: "err" | "warn" | "info"; text: string };

/** The row's badges, as the mock draws them: why a call failed, a refused or failed tool,
 * a call with no token counts, an auto-approved action, a step after the turn. */
export function stepFlags(step: RecordedStep, t: T): Flag[] {
  const a = step.attrs || {};
  const flags: Flag[] = [];
  if (step.kind === "model") {
    if (a.error) {
      flags.push(a.error.type === "context_length"
        ? { cls: "warn", text: t("steps.flag.over_limit") }
        : { cls: "err", text: t("steps.flag.failed", { type: t(`steps.error_type.${a.error.type}`, { defaultValue: a.error.type }) }) });
    } else if (step.status === "interrupted") {
      flags.push({ cls: "err", text: t("steps.status.interrupted") });
    } else if (a.usage && !a.usage.reported) {
      flags.push({ cls: "warn", text: t("steps.flag.no_usage") });
    }
  } else if (step.kind === "tool") {
    if (a.exit_code != null && a.exit_code !== 0 && step.status !== "denied") {
      flags.push({ cls: "err", text: t("steps.flag.exit", { code: a.exit_code }) });
    } else if (["error", "denied", "interrupted", "skipped", "unknown"].includes(step.status)) {
      flags.push({ cls: "err", text: t(`steps.status.${step.status}`) });
    }
    if (a.sandbox?.blocked_count > 0) flags.push({ cls: "err", text: t("steps.flag.blocked") });
    if (a.decision?.origin === "reviewer") flags.push({ cls: "info", text: t("steps.flag.auto_approved") });
  } else if (step.kind === "auto_approver") {
    if (step.status === "interrupted") flags.push({ cls: "err", text: t("steps.status.interrupted") });
  } else if (step.kind === "sandbox" && step.status === "error") {
    const stage = a.failed_stage || "preparing";
    flags.push({ cls: "err", text: t("steps.flag.sandbox_failed", { stage: t(`steps.sbx_stage.${stage}`, { defaultValue: stage }).toLowerCase() }) });
  } else if (step.kind === "notice" && step.name === "sandbox_restarted") {
    if (a.reason) flags.push({ cls: "info", text: t(`steps.flag.restart_${a.reason}`, { defaultValue: a.reason }) });
  } else if (step.kind === "notice" && (step.name === "controller_offline" || step.name === "nobody_watching")) {
    if (step.duration_ms) flags.push({ cls: "info", text: `${clockTime(step.started_at)}–${clockTime(step.started_at + step.duration_ms)}` });
  } else if (step.kind === "notice" && step.name === "clock_jump") {
    flags.push({ cls: "info", text: t("steps.flag.gap_skipped", { t: formatDuration(a.skipped_ms ?? step.duration_ms) }) });
  } else if (step.kind === "notice" && step.name === "recording_gap" && typeof a.until === "number") {
    flags.push({ cls: "warn", text: t("steps.flag.not_recorded", { t: formatDuration(Math.max(0, a.until - step.started_at)) }) });
  } else if (["error", "denied", "interrupted", "skipped", "unknown"].includes(step.status)) {
    flags.push({ cls: "err", text: t(`steps.status.${step.status}`) });
  }
  if (step.kind === "model" && step.status === "unknown") flags.push({ cls: "warn", text: t("steps.status.unknown") });
  if (step.after_turn) flags.push({ cls: "info", text: t("steps.after_turn") });
  return flags;
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

const KIND_CLASS: Record<string, string> = {
  context: "ctx", model: "model", tool: "tool", wait: "wait", auto_approver: "review", summary: "sum", notice: "ctx",
  sandbox: "sbx",
};

const GAPS = ["controller_offline", "nobody_watching", "clock_jump"];

/** A gap notice with a duration (OB-14): drawn as a dashed band, not a point. */
function isGap(step: RecordedStep): boolean {
  return step.kind === "notice" && GAPS.includes(step.name) && !!step.duration_ms;
}

/** The class of a row: a sandbox restart notice is drawn in the sandbox colour (mock ob13),
 * a gap as a dashed band (mock ob14). */
function kindClass(step: RecordedStep): string {
  if (step.kind === "notice" && step.name === "sandbox_restarted") return "sbx";
  if (isGap(step)) return "gap";
  return KIND_CLASS[step.kind] || "ctx";
}

/** A moment as the clock on the wall shows it, "14:02". */
function clockTime(ms: number): string {
  return new Date(ms).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

/** How long the turn spent with the machine offline, or with nobody watching (mock ob14). */
function gapTotal(steps: RecordedStep[], name: string): number {
  return steps.filter((s) => s.kind === "notice" && s.name === name).reduce((n, s) => n + (s.duration_ms || 0), 0);
}

type SandboxStage = { name: string; started_at: number; duration_ms: number | null; status: string; uncertain?: boolean };

/** A sandbox setup step's stages as rows under it, in the order run (OB-13). An image pull
 * the provider cannot time is not a row of its own: it is said on the Create row. */
function StageRows({ step, depth, turn, total }: { step: RecordedStep; depth: number; turn: RecordedTurn; total: number }) {
  const { t } = useTranslation();
  const stages: SandboxStage[] = Array.isArray(step.attrs?.stages) ? step.attrs.stages : [];
  const pulled = stages.some((s) => s.name === "download_image" && s.uncertain);
  return (
    <>
      {stages.filter((s) => !(s.name === "download_image" && s.uncertain)).map((s, i) => {
        const left = Math.min(100, Math.max(0, ((s.started_at - turn.started_at) / total) * 100));
        const width = s.duration_ms ? Math.min(100 - left, (s.duration_ms / total) * 100) : 0;
        const name = t(`steps.sbx_stage.${s.name}`, { defaultValue: s.name }) + (s.name === "create" && pulled ? ` · ${t("steps.sbx_stage.image_maybe")}` : "");
        return (
          <div key={`${s.name}-${i}`} className="steps-row steps-stage k-sbx" data-testid="steps-stage">
            <span className="steps-label" style={{ paddingLeft: (depth + 1) * 16 }}>
              <span className="steps-kind" />
              <span className="steps-name">{name}</span>
              {s.status === "error" && <span className="steps-badge err">{t("steps.status.error")}</span>}
            </span>
            <span className="steps-track">
              <span className="steps-bar" style={{ left: `${left}%`, width: width ? `${width}%` : undefined }} />
            </span>
            <span className="steps-time">{formatDuration(s.duration_ms)}</span>
          </div>
        );
      })}
    </>
  );
}

export function StepsPanel({
  sessionId,
  refreshKey = 0,
  openAt = null,
  onClose,
}: {
  sessionId: string;
  refreshKey?: number;
  // Opened from a turn's answer: its time (unix seconds) picks the turn that produced it.
  openAt?: { ts?: number; n: number } | null;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const [turns, setTurns] = useState<RecordedTurn[] | null>(null);
  const [turnIdx, setTurnIdx] = useState<number | null>(null);
  const [steps, setSteps] = useState<RecordedStep[]>([]);
  const [tab, setTab] = useState<"timeline" | "usage" | "issues">("timeline");
  const [issues, setIssues] = useState<TurnIssues | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState(false);

  // Another session: start again at its newest turn.
  useEffect(() => {
    setTurnIdx(null);
    setSteps([]);
  }, [sessionId]);
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

  // An answer's Steps link: the first turn that had not ended before that answer (the turns
  // before it all ended earlier). The answer's time is in whole seconds, hence the second's slack.
  useEffect(() => {
    if (!openAt || !turns || !turns.length || openAt.ts == null) return;
    const at = openAt.ts * 1000 - 1000;
    const idx = turns.findIndex((tn) => tn.duration_ms == null || tn.started_at + tn.duration_ms >= at);
    setTurnIdx(idx < 0 ? turns.length - 1 : idx);
    setTab("timeline");
  }, [openAt?.n, turns]);  // eslint-disable-line react-hooks/exhaustive-deps

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

  // OB-3: the turn's Issues, for the tab and the row marks; a turn still running has none.
  useEffect(() => {
    setIssues(null);
    if (!turn || turn.status === "running") return;
    let live = true;
    getTurnIssues(sessionId, turn.turn_id).then((x) => live && setIssues(x)).catch(() => {});
    return () => {
      live = false;
    };
  }, [sessionId, turn?.turn_id, turn?.status, refreshKey]);
  // Opening Issues is "I have looked": the session's ring in the sidebar clears.
  useEffect(() => {
    if (tab === "issues") markIssuesSeen(sessionId).catch(() => {});
  }, [tab, sessionId, turn?.turn_id]);
  const flagged = useMemo(() => new Set((issues?.issues || []).filter((i) => i.severity === "warn").map((i) => i.step_id)), [issues]);

  const rows = useMemo(() => nest(steps), [steps]);
  const checks = steps.filter((s) => s.kind === "auto_approver" && !s.shadow);
  const inspecting = tab === "timeline" && !!selected && !!turn;
  const total = turn ? Math.max(1, turn.buckets.total || turn.duration_ms || 1) : 1;

  const picker = turns && turns.length > 0 && turnIdx != null && (
    <span className="steps-turn-picker">
      <button aria-label={t("steps.prev_turn")} disabled={turnIdx <= 0} onClick={() => setTurnIdx(turnIdx - 1)}>‹</button>
      <span data-testid="steps-turn">{t("steps.turn_of", { n: turnIdx + 1, total: turns.length })}</span>
      <button aria-label={t("steps.next_turn")} disabled={turnIdx >= turns.length - 1} onClick={() => setTurnIdx(turnIdx + 1)}>›</button>
    </span>
  );

  const timeline = turn && (
    <div className="steps-timeline" data-testid="steps-timeline">
      <div className="steps-axis" aria-hidden>
        <span>{t("steps.axis.step")}</span>
        <span className="steps-ticks">{ticks(total).map((x, i) => <span key={i}>{x}</span>)}</span>
        <span className="steps-axis-time">{t("steps.axis.time")}</span>
      </div>
      {rows.map(({ step, depth }) => (
        <Fragment key={step.step_id}>
          <StepRow
            step={step}
            depth={depth}
            turn={turn}
            total={total}
            label={stepLabel(step, t)}
            selected={selected === step.step_id}
            issue={flagged.has(step.step_id)}
            onSelect={() => setSelected(selected === step.step_id ? null : step.step_id)}
          />
          {step.kind === "sandbox" && <StageRows step={step} depth={depth} turn={turn} total={total} />}
        </Fragment>
      ))}
    </div>
  );

  return (
    <div className={"team-view steps-panel" + (inspecting ? " inspecting" : "")} data-testid="steps-panel">
      <div className="steps-railhead">
        <div className="steps-railhead-title">
          <strong>{t("steps.title")}</strong>
          {picker}
          <span className="steps-sp" />
          <button className="steps-close" aria-label={t("steps.close")} onClick={onClose}>✕</button>
        </div>
        {turn && (modeLabel(turn, t) || stampLine(turn.stamp, t)) && (
          <div className="steps-stamp" data-testid="steps-stamp">
            {modeLabel(turn, t) && (
              <span className={"steps-pill steps-mode" + (turn.reviewer?.active ? " review" : "")} data-testid="steps-mode">{modeLabel(turn, t)}</span>
            )}
            {stampLine(turn.stamp, t) && <span className="steps-stamp-line">{stampLine(turn.stamp, t)}</span>}
          </div>
        )}
        {turn && !inspecting && (
          <>
            <div className="steps-tiles" data-testid="steps-tiles">
              <Tile value={formatDuration(turn.buckets.total)} label={t("steps.bucket.total")} />
              {BUCKETS.filter((b) => turn.buckets[b] > 0).map((b) => (
                <Tile key={b} value={formatDuration(turn.buckets[b])} label={t(`steps.bucket.${b}`)} />
              ))}
              {turn.buckets.other_shown && <Tile value={formatDuration(turn.buckets.other)} label={t("steps.bucket.other")} />}
              {gapTotal(steps, "controller_offline") > 0 && (
                <Tile value={formatDuration(gapTotal(steps, "controller_offline"))} label={t("steps.gap.offline_total")} />
              )}
              {gapTotal(steps, "nobody_watching") > 0 && (
                <Tile value={formatDuration(gapTotal(steps, "nobody_watching"))} label={t("steps.gap.nobody_total")} />
              )}
            </div>
            {steps.some((s) => s.status === "unknown") && (
              <p className="steps-line" data-testid="steps-gap">
                {t("steps.unknown_line", { count: steps.filter((s) => s.status === "unknown").length })}
              </p>
            )}
            {checks.length > 0 && (
              <p className="steps-line" data-testid="steps-checks">
                {t("steps.checks_line", {
                  count: checks.length,
                  allowed: checks.filter((c) => c.attrs.result === "allow").length,
                  asked: checks.filter((c) => c.attrs.result !== "allow" && c.attrs.result !== "deny").length,
                  blocked: checks.filter((c) => c.attrs.result === "deny").length,
                  time: formatDuration(turn.buckets.auto_approver || 0),
                  reviewer: reviewerLabel(turn.reviewer, t) || reviewerLabel(checks[0]?.attrs?.reviewer, t) || "—",
                })}
              </p>
            )}
          </>
        )}
        {turn && (
          <div className="steps-tabs" role="tablist">
            <button role="tab" aria-selected={tab === "timeline"} onClick={() => setTab("timeline")}>{t("steps.tab.timeline")}</button>
            <button role="tab" aria-selected={tab === "usage"} onClick={() => setTab("usage")}>{t("steps.tab.usage")}</button>
            <button role="tab" aria-selected={tab === "issues"} onClick={() => setTab("issues")} data-testid="steps-tab-issues">
              {(turn.issues?.warn ?? issues?.warn) ? t("steps.issues.tab_n", { n: turn.issues?.warn ?? issues?.warn }) : t("steps.issues.tab")}
            </button>
          </div>
        )}
      </div>

      {error && <p className="steps-empty">{t("steps.load_error")}</p>}
      {turns && turns.length === 0 && <p className="steps-empty" data-testid="steps-none">{t("steps.not_recorded")}</p>}

      {turn && tab === "timeline" && !inspecting && <div className="steps-railbody">{timeline}</div>}
      {turn && inspecting && (
        <div className="steps-split">
          <div className="steps-railbody steps-list">{timeline}</div>
          <StepDetails
            sessionId={sessionId}
            stepId={selected!}
            turn={turn}
            steps={steps}
            onClose={() => setSelected(null)}
          />
        </div>
      )}
      {turn && tab === "usage" && <div className="steps-railbody"><UsageView sessionId={sessionId} refreshKey={refreshKey} /></div>}
      {turn && tab === "issues" && (
        <div className="steps-railbody">
          <IssuesView
            sessionId={sessionId}
            turn={turn}
            issues={issues}
            steps={steps}
            onGo={(stepId) => {
              setTab("timeline");
              setSelected(stepId);
            }}
          />
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

function StepRow({ step, depth, turn, total, label, selected, issue, onSelect }: {
  step: RecordedStep; depth: number; turn: RecordedTurn; total: number; label: string; selected: boolean;
  issue?: boolean; onSelect: () => void;
}) {
  const { t } = useTranslation();
  const left = Math.min(100, Math.max(0, ((step.started_at - turn.started_at) / total) * 100));
  const width = step.duration_ms ? Math.min(100 - left, (step.duration_ms / total) * 100) : 0;
  const kind = kindClass(step);
  return (
    <button
      className={`steps-row k-${kind}` + (selected ? " selected" : "") + (step.shadow ? " shadow" : "")}
      onClick={onSelect}
      data-testid="steps-row"
    >
      <span className="steps-label" style={{ paddingLeft: depth * 16 }}>
        <span className="steps-kind" />
        <span className="steps-name">{label}</span>
        {stepFlags(step, t).map((f) => (
          <span key={f.text} className={`steps-badge ${f.cls}`}>{f.text}</span>
        ))}
        {issue && <span className="steps-badge warn" data-testid="steps-row-issue">{t("steps.issues.badge")}</span>}
      </span>
      <span className="steps-track">
        <span className="steps-bar" style={{ left: `${left}%`, width: width ? `${width}%` : undefined }} />
      </span>
      <span className="steps-time">{formatDuration(step.duration_ms)}</span>
    </button>
  );
}

// -- Issues (OB-3) --------------------------------------------------------------------------------

/** One title per check, and per kind for the context check (mock review M2-R4). */
function issueTitle(i: { check: string; facts?: Record<string, any> }, t: T): string {
  const kind = i.check === "context_full" ? i.facts?.kind : null;
  return kind ? t(`steps.issues.title.context_full_${kind}`, { defaultValue: t("steps.issues.title.context_full") })
    : t(`steps.issues.title.${i.check}`, { defaultValue: i.check });
}

/** What the app adds from the step's own content, shown here only (design §6): the command a
 * failed tool ran and the first line of its output; the call a shadow check judged. */
function IssueDetail({ sessionId, issue }: { sessionId: string; issue: TurnIssue }) {
  const [d, setD] = useState<any>(null);
  useEffect(() => {
    if (!issue.step_id || !["tool_failed", "shadow_disagreed", "refused", "reviewer_allowed_send", "blocked_by_sandbox"].includes(issue.check)) return;
    let live = true;
    getStepDetail(sessionId, issue.step_id).then((x) => live && setD(x)).catch(() => {});
    return () => {
      live = false;
    };
  }, [sessionId, issue.step_id, issue.check]);
  if (!d) return null;
  const args = d.kind === "auto_approver" ? d.for_step?.arguments : d.arguments;
  const main = args && typeof args === "object" ? (args.command ?? args.path ?? args.url ?? args.query) : null;
  let first = "";
  if (issue.check === "tool_failed" && typeof d.message?.content === "string") {
    try {
      const r = JSON.parse(d.message.content);
      first = String(r?.output ?? r?.error ?? "").split("\n").map((l: string) => l.trim()).find(Boolean) || "";
    } catch {
      first = "";
    }
  }
  return (
    <>
      {main != null && <span className="steps-mono steps-issue-arg"> {String(main).slice(0, 120)}</span>}
      {first && <span className="steps-issue-out"> {first.slice(0, 140)}</span>}
    </>
  );
}

function IssuesView({ sessionId, turn, issues, steps, onGo }: {
  sessionId: string; turn: RecordedTurn; issues: TurnIssues | null; steps: RecordedStep[]; onGo: (stepId: string) => void;
}) {
  const { t } = useTranslation();
  const [openFine, setOpenFine] = useState(false);
  if (turn.status === "running") return <p className="steps-empty">{t("steps.issues.running")}</p>;
  if (!issues) return <p className="steps-empty">{t("steps.loading")}</p>;
  // Grouped by check (V2-17), warnings first; context_full by kind (M2-R4).
  const groups: { key: string; items: TurnIssue[] }[] = [];
  for (const i of [...issues.issues].sort((a, b) => (a.severity === b.severity ? 0 : a.severity === "warn" ? -1 : 1))) {
    const key = i.check === "context_full" ? `${i.check}:${i.facts?.kind}` : i.check;
    const g = groups.find((x) => x.key === key);
    if (g) g.items.push(i);
    else groups.push({ key, items: [i] });
  }
  const noPlan = issues.not_checked.find((n) => n.check === "off_plan");
  return (
    <div className="steps-issues" data-testid="steps-issues">
      {groups.length === 0 && <p className="steps-issues-none">{t("steps.issues.none")}</p>}
      {groups.map((g) => {
        const first = g.items[0];
        const info = first.severity === "info";
        return (
          <div key={g.key} className={"steps-callout " + (info ? "info" : "warn")} data-testid="steps-issue">
            <b>{issueTitle(first, t)}{g.items.length > 1 ? ` · ${g.items.length}` : ""}</b>
            {info && <span className="steps-pill steps-info-pill">{t("steps.issues.information")}</span>}
            {g.items.map((i, n) => (
              <div key={(i.step_id || "") + n} className="steps-issue-line">
                {i.step_seq != null && <span className="steps-faint">{t("steps.insp.step", { n: i.step_seq })} </span>}
                <span>{i.text}</span>
                <IssueDetail sessionId={sessionId} issue={i} />
                {i.step_id && steps.some((s) => s.step_id === i.step_id) && (
                  <button className="steps-link" onClick={() => onGo(i.step_id!)}>{t("steps.issues.go")}</button>
                )}
              </div>
            ))}
          </div>
        );
      })}
      <button className="steps-fine-toggle" onClick={() => setOpenFine(!openFine)} data-testid="steps-fine-toggle">
        {t("steps.issues.fine", { n: issues.fine.length })} {openFine ? "▾" : "▸"}
      </button>
      {openFine && (
        <div className="steps-fine" data-testid="steps-fine">
          {issues.fine.map((f) => <div key={f.check}>✓ {f.text}</div>)}
          {noPlan && <div className="steps-faint">– {t("steps.issues.no_plan")}</div>}
        </div>
      )}
      {!openFine && noPlan && <div className="steps-faint steps-fine">– {t("steps.issues.no_plan")}</div>}
    </div>
  );
}

// -- details (the inspector of the mock) ---------------------------------------------------------

/** The messages a model call was given, as "role · text" lines (Chat, Responses or Gemini shape). */
function inputLines(request: any): string[] {
  if (!request || typeof request !== "object") return [];
  const items: any[] = request.messages || request.input || request.contents || [];
  if (!Array.isArray(items)) return [];
  const text = (c: any): string => {
    if (typeof c === "string") return c;
    if (Array.isArray(c)) return c.map((p) => (typeof p === "string" ? p : p?.text ?? p?.output ?? "")).filter(Boolean).join(" ");
    if (c && typeof c === "object") return c.text ?? "";
    return "";
  };
  return items.map((m) => {
    const role = m.role || m.type || "message";
    const calls = (m.tool_calls || []).map((c: any) => `(tool call) ${c.function?.name ?? c.name} ${c.function?.arguments ?? ""}`.trim());
    const body = [text(m.content ?? m.parts ?? m.output ?? ""), ...calls].filter(Boolean).join("\n");
    return `${role} · ${body}`.slice(0, 600);
  });
}

function Kv({ rows }: { rows: [string, React.ReactNode][] }) {
  return (
    <dl className="steps-kv">
      {rows.map(([k, v]) => (
        <div key={k}><dt>{k}</dt><dd>{v ?? "—"}</dd></div>
      ))}
    </dl>
  );
}

function Sec({ label, right }: { label: string; right?: React.ReactNode }) {
  return <div className="steps-seclbl">{label}{right != null && <><span className="steps-sp" /><span className="steps-seclbl-right">{right}</span></>}</div>;
}

function ContextAdded({ ctx, t }: { ctx: Record<string, any> | null | undefined; t: T }) {
  if (!ctx) return null;
  const start = ctx.session_start, pt = ctx.per_turn || {};
  const rows: [string, React.ReactNode][] = [];
  if (start) {
    const mems = start.memories || [];
    rows.push(["memory", <>{t("steps.detail.memories")} {mems.length}{start.memory_mode === "index" ? ` · ${t("steps.detail.as_index")}` : ""}
      {mems.length > 0 && <span className="steps-mono steps-faint"> {mems.slice(0, 6).map((m: any) => `mem_${m}`).join(", ")}</span>}</>]);
    if (start.rules) rows.push(["rules", <>{t("steps.detail.rules")} {start.rules}</>]);
    if (start.agents_md) rows.push(["AGENTS.md", <>{t("steps.detail.agents_md")} {start.agents_md}</>]);
  }
  rows.push(["skill", <>{t("steps.detail.skills")} {pt.skills_menu || 0}{pt.skills_hash && <span className="steps-mono steps-faint"> ⓢ{pt.skills_hash}</span>}</>]);
  rows.push(["folder", <>{t("steps.detail.folders")} {pt.folders ?? 0}</>]);
  if (pt.mode_note) rows.push(["mode", <>{t("steps.detail.mode_note")} {pt.mode_note}</>]);
  if (pt.sandbox_note) rows.push(["sandbox", <>{t("steps.detail.sandbox_note")}</>]);
  return (
    <div className="steps-ctxlist">
      {rows.map(([tag, body]) => (
        <div key={tag}><span className="steps-pill">{tag}</span><span className="steps-c">{body}</span></div>
      ))}
      {ctx.size_chars != null && (
        <div><span className="steps-pill">{t("steps.insp.block")}</span><span className="steps-c">{t("steps.detail.size")}</span>
          <span className="steps-sz">{ctx.size_chars} · <span className="steps-mono">{ctx.block_hash || "—"}</span></span></div>
      )}
    </div>
  );
}

function TokenBar({ u, t }: { u: any; t: T }) {
  if (!u) return null;
  if (!u.reported) return <p className="steps-line" data-testid="steps-tokens">{t("steps.tokens_not_reported")}</p>;
  const cache = u.cache_read || 0, written = u.cache_write || 0, fresh = u.input_fresh || 0, out = u.output || 0;
  const all = Math.max(1, cache + written + fresh + out);
  const seg = (n: number, cls: string) => (n > 0 ? <i className={cls} style={{ width: `${(n / all) * 100}%` }} /> : null);
  return (
    <div data-testid="steps-tokens">
      <div className="steps-tokbar">{seg(cache, "c-cache")}{seg(written, "c-write")}{seg(fresh, "c-new")}{seg(out, "c-out")}</div>
      <div className="steps-legend">
        ■ <span className="c-cache">{t("steps.insp.from_cache", { n: formatTokens(cache) })}</span>
        {" · "}<span className="c-write">{t("steps.insp.written", { n: u.cache_write == null ? "—" : formatTokens(written) })}</span>
        {" · "}<span className="c-new">{t("steps.insp.new", { n: formatTokens(fresh) })}</span>
        {" · "}<span className="c-out">{u.reasoning != null
          ? t("steps.insp.out_reasoning", { n: formatTokens(out), r: formatTokens(u.reasoning) })
          : t("steps.insp.out", { n: formatTokens(out) })}</span>
      </div>
    </div>
  );
}

function Joined({ audit, t }: { audit: any[]; t: T }) {
  if (!audit?.length) return null;
  const stages = Array.from(new Set(audit.map((r) => r.stage).filter(Boolean)));
  return (
    <>
      <Sec label={t("steps.insp.joined")} />
      <div className="steps-small">{t("steps.insp.joined_line", { count: audit.length })} · <span className="steps-mono">{stages.join(", ")}</span> · {t("steps.insp.same_call")}</div>
    </>
  );
}

function StepDetails({ sessionId, stepId, turn, steps, onClose }: {
  sessionId: string; stepId: string; turn: RecordedTurn; steps: RecordedStep[]; onClose: () => void;
}) {
  const { t } = useTranslation();
  const [d, setD] = useState<any>(null);
  useEffect(() => {
    let live = true;
    setD(null);
    getStepDetail(sessionId, stepId).then((s) => live && setD(s)).catch(() => {});
    return () => {
      live = false;
    };
  }, [sessionId, stepId]);
  return (
    <div className="steps-insp steps-details" data-testid="steps-details">
      <button className="steps-close steps-insp-close" aria-label={t("steps.insp.close")} onClick={onClose}>✕</button>
      {!d ? <p className="steps-line">{t("steps.loading")}</p> : <Inspector d={d} turn={turn} steps={steps} sessionId={sessionId} t={t} />}
    </div>
  );
}

function statusPill(d: any, t: T): { cls: string; text: string } {
  const a = d.attrs || {};
  if (d.kind === "tool" && a.exit_code != null && a.exit_code !== 0 && d.status !== "denied") return { cls: "danger", text: t("steps.flag.exit", { code: a.exit_code }) };
  if (d.status === "ok") return { cls: "ok", text: t("steps.insp.done") };
  if (d.status === "interrupted" || d.status === "unknown") return { cls: "warn", text: t(`steps.status.${d.status}`) };
  return { cls: "danger", text: t(`steps.status.${d.status}`, { defaultValue: d.status }) };
}

function Inspector({ d, turn, steps, sessionId, t }: { d: any; turn: RecordedTurn; steps: RecordedStep[]; sessionId: string; t: T }) {
  const a = d.attrs || {};
  const kind = kindClass(d);
  const started = clock(d.started_at - turn.started_at);
  const ctxStep = steps.find((s) => s.kind === "context");
  const exchanges = (d.exchange_rebuilt || []) as any[];
  const exact = exchanges.length > 0 && (
    <details className="steps-exchange">
      <summary>{t("steps.exchange", { count: exchanges.length })}</summary>
      {exchanges.map((x: any, i: number) => (
        <pre className="steps-box" key={i}>{JSON.stringify(x.request, null, 2)}{"\n\n"}{x.response}</pre>
      ))}
    </details>
  );

  if (d.kind === "model") {
    const mains = steps.filter((s) => s.kind === "model" && (s.attrs?.purpose || "main") === (a.purpose || "main") && !s.parent_step_id);
    const n = mains.findIndex((s) => s.step_id === d.step_id) + 1;
    const title = (a.purpose || "main") === "main" && n > 0
      ? t("steps.insp.model_call", { n, total: mains.length })
      : stepLabel(d, t);
    const attempts = exchanges.filter((x) => !x.auxiliary).length;
    const why = a.error
      ? t("steps.flag.failed", { type: t(`steps.error_type.${a.error.type}`, { defaultValue: a.error.type }) })
      : a.finish_reason ? t("steps.insp.stopped_because", { why: t(`steps.finish.${a.finish_reason}`, { defaultValue: a.finish_reason }) }) : "";
    const facts = a.provider_facts || {};
    const u = a.usage;
    const lines = inputLines(exchanges.find((x) => !x.auxiliary)?.request);
    return (
      <>
        <h3 className="steps-h3"><span className={`steps-kind k-${kind}`} />{title}<span className="steps-pill">{t("steps.insp.step", { n: d.seq ?? "—" })}</span></h3>
        <div className="steps-meta">
          {[a.provider_title || a.provider ? t("steps.insp.via", { model: a.model, provider: a.provider_title || a.provider }) : a.model, t("steps.insp.started", { at: started }), why].filter(Boolean).join(" · ")}
        </div>
        <Kv rows={[
          [t("steps.detail.duration"), <>{formatDuration(d.duration_ms)}{a.ttft_ms != null && <span className="steps-faint"> ({t("steps.insp.first_reply", { t: formatDuration(a.ttft_ms) })})</span>}</>],
          [t("steps.detail.attempts"), attempts <= 1 ? t("steps.insp.attempt_one") : t("steps.insp.attempt_many", { n: attempts, retries: attempts - 1 })],
          ...(facts.served_model && facts.served_model !== String(a.model || "").split(":").pop()
            ? [[t("steps.detail.served"), facts.served_model] as [string, React.ReactNode]] : []),
          ...(ctxStep?.attrs?.session_prompt_hash
            ? [[t("steps.detail.session_prompt"), <><span className="steps-mono">{ctxStep.attrs.session_prompt_hash}</span> <span className="steps-faint">{t("steps.insp.session_prompt_note")}</span></>] as [string, React.ReactNode]] : []),
          ...(turn.stamp?.persona_fp
            ? [[t("steps.detail.coworker"), <><span className="steps-mono">ⓕ{turn.stamp.persona_fp}</span> <span className="steps-faint">{t("steps.insp.coworker_note")}</span></>] as [string, React.ReactNode]] : []),
          [t("steps.detail.tools"), a.tools_offered ? <>{t("steps.insp.tools_n", { count: a.tools_offered })} <span className="steps-mono steps-faint">set·{a.tools_hash || "—"}</span></> : "0"],
          ...(facts.provider_request_id ? [[t("steps.detail.request_id"), <span className="steps-mono">{facts.provider_request_id}</span>] as [string, React.ReactNode]] : []),
          ...(facts.provider_response_id ? [[t("steps.detail.response_id"), <span className="steps-mono">{facts.provider_response_id}</span>] as [string, React.ReactNode]] : []),
          ...(a.error ? [[t("steps.detail.error"), `${t(`steps.error_type.${a.error.type}`, { defaultValue: a.error.type })}${a.error.text ? ` · ${a.error.text}` : ""}`] as [string, React.ReactNode]] : []),
        ]} />
        {u && (
          <>
            <Sec label={t("steps.insp.tokens")} right={u.reported ? t("steps.insp.in_out", {
              in: formatTokens((u.cache_read || 0) + (u.cache_write || 0) + (u.input_fresh || 0)), out: formatTokens(u.output || 0) }) : undefined} />
            <TokenBar u={u} t={t} />
          </>
        )}
        {(a.purpose || "main") === "main" && ctxStep && (
          <>
            <Sec label={t("steps.insp.context_added")} />
            <ContextAdded ctx={ctxStep.attrs} t={t} />
          </>
        )}
        {lines.length > 0 && <InputBox lines={lines} t={t} />}
        {(d.message?.content || d.message?.tool_calls?.length) && (
          <>
            <Sec label={t("steps.insp.output")} />
            <div className="steps-box">
              {[d.message.content ? `${d.message.content}${d.message.cut ? "\n…" : ""}` : "",
                ...(d.message.tool_calls || []).map((c: any) => `→ ${t("steps.insp.tool_call")}: ${c.name} ${c.arguments}`)].filter(Boolean).join("\n")}
            </div>
          </>
        )}
        {exact}
      </>
    );
  }

  if (d.kind === "tool") {
    const pill = statusPill(d, t);
    const path = a.decision?.path || [];
    const check = steps.find((s) => s.kind === "auto_approver" && !s.shadow && s.call_id === d.call_id);
    const wait = d.wait;
    const right = (stage: string): string => {
      if (stage === "auto_approver" && check) return formatDuration(check.duration_ms);
      if (stage === "card" && wait) return clock(wait.started_at - turn.started_at);
      if (stage === "resolved" && wait && wait.duration_ms != null) return clock(wait.started_at + wait.duration_ms - turn.started_at);
      return "";
    };
    const mode = turn.approval_mode ? t(MODE_KEYS[turn.approval_mode] || "", { defaultValue: turn.approval_mode }) : "";
    const who = turn.reviewer?.active ? reviewerLabel(turn.reviewer, t) : "";
    return (
      <>
        <h3 className="steps-h3"><span className={`steps-kind k-${kind}`} />{d.name}<span className={`steps-pill ${pill.cls}`}>{pill.text}</span></h3>
        <div className="steps-meta">
          {[a.risk_class ? t(`steps.risk.${a.risk_class}`, { defaultValue: a.risk_class }) : "",
            d.call_id ? `${t("steps.detail.call_id")} ${d.call_id}` : "",
            mode ? `${t("steps.detail.mode")} ${mode}${who ? ` (${t("steps.reviewer_is", { who })})` : ""}` : ""].filter(Boolean).join(" · ")}
        </div>
        <Kv rows={[
          [t("steps.insp.ran_for"), formatDuration(d.duration_ms)],
          ...(wait ? [[t("steps.insp.waited"), formatDuration(wait.duration_ms)] as [string, React.ReactNode]] : []),
          ...(a.result_bytes != null ? [[t("steps.detail.result_size"), formatBytes(a.result_bytes)] as [string, React.ReactNode]] : []),
          ...(a.exit_code != null ? [[t("steps.detail.exit"), String(a.exit_code)] as [string, React.ReactNode]] : []),
          ...(a.decision?.origin ? [[t("steps.detail.origin"), t(`steps.origin.${a.decision.origin}`, { defaultValue: a.decision.origin })] as [string, React.ReactNode]] : []),
          ...(a.sandbox ? [[t("steps.detail.blocked"), a.sandbox.blocked_known ? String(a.sandbox.blocked_count) : t("steps.detail.unknown")] as [string, React.ReactNode]] : []),
        ]} />
        {path.length > 0 && (
          <>
            <Sec label={t("steps.insp.how_allowed")} />
            <ol className="steps-ctxlist steps-path">
              {path.map((p: any, i: number) => {
                const last = i === path.length - 1;
                const good = last && /^approved|allow|grant|session_grant|gate_tier|readonly_tier/.test(p.outcome);
                return (
                  <li key={i}>
                    <span className={"steps-pill" + (good ? " ok" : "")}>{i + 1}</span>
                    <span className="steps-c">{t(`steps.stage.${p.stage}`, { defaultValue: p.stage })}: <b>{t(`steps.outcome.${p.outcome}`, { defaultValue: p.outcome })}</b>
                      {p.detail ? <span className="steps-faint"> ({t(`steps.origin.${p.detail}`, { defaultValue: p.detail })})</span> : null}</span>
                    <span className="steps-sz">{right(p.stage)}</span>
                  </li>
                );
              })}
            </ol>
          </>
        )}
        {d.arguments != null && (
          <>
            <Sec label={t("steps.insp.arguments")} />
            <div className="steps-box">{typeof d.arguments === "string" ? d.arguments : JSON.stringify(d.arguments, null, 2)}</div>
          </>
        )}
        {d.message?.content && (
          <>
            <Sec label={t("steps.insp.result")} />
            <div className="steps-box">{d.message.content}{d.message.cut ? "\n…" : ""}</div>
          </>
        )}
        <Joined audit={d.audit} t={t} />
      </>
    );
  }

  if (d.kind === "auto_approver") return <CheckInspector d={d} turn={turn} sessionId={sessionId} t={t} />;

  // Waits, summaries, context steps and notices.
  const pill = statusPill(d, t);
  const rows: [string, React.ReactNode][] = [
    [t("steps.detail.started"), started],
    [t("steps.detail.duration"), formatDuration(d.duration_ms)],
  ];
  if (d.kind === "wait") {
    rows.push([t("steps.detail.where"), t(`steps.where.${a.where}`, { defaultValue: a.where })],
              [t("steps.detail.outcome"), a.outcome == null ? "—" : t(`steps.outcome.${a.outcome}`, { defaultValue: a.outcome })]);
    if (d.content?.resolved_by) rows.push([t("steps.detail.answered_by"), d.content.resolved_by]);
  }
  if (d.kind === "summary") {
    if (a.reason) rows.push([t("steps.detail.summary_reason"), t(`steps.summary_reason.${a.reason}`, { defaultValue: a.reason })]);
    rows.push([t("steps.detail.tokens_before"), a.tokens_before == null ? "—" : formatTokens(a.tokens_before)],
              [t("steps.detail.tokens_after"), a.tokens_after == null ? "—" : formatTokens(a.tokens_after)]);
  }
  if (d.kind === "context") rows.push([t("steps.detail.session_prompt"), <span className="steps-mono">{a.session_prompt_hash || "—"}</span>]);
  if (d.kind === "sandbox") {
    rows.push([t("steps.sbx.provider"), a.provider || "—"],
              [t("steps.sbx.trigger"), t(`steps.sbx.trigger_${a.trigger}`, { defaultValue: a.trigger || "—" })]);
  }
  if (d.kind === "notice" && d.name === "sandbox_restarted") {
    rows.push([t("steps.sbx.provider"), a.provider || "—"],
              [t("steps.sbx.reason"), t(`steps.sbx.reason_${a.reason}`, { defaultValue: a.reason || "—" })],
              [t("steps.sbx.how"), t(`steps.sbx.how_${a.how}`, { defaultValue: a.how || "—" })]);
  }
  if (isGap(d) || (d.kind === "notice" && d.name === "recording_gap")) {
    const until = d.name === "recording_gap" ? a.until : d.started_at + (d.duration_ms || 0);
    rows.splice(0, rows.length,
      [t("steps.gap.from"), clockTime(d.started_at)],
      [t("steps.gap.until"), typeof until === "number" ? clockTime(until) : "—"]);
    if (d.name === "clock_jump") rows.push([t("steps.gap.skipped"), formatDuration(a.skipped_ms ?? d.duration_ms)]);
    else rows.push([t("steps.detail.duration"), formatDuration(typeof until === "number" ? until - d.started_at : d.duration_ms)]);
    if (a.reason) rows.push([t("steps.gap.why"), t(`steps.gap.reason_${a.reason}`, { defaultValue: a.reason })]);
    if (a.cause) rows.push([t("steps.gap.why"), t(`steps.gap.cause_${a.cause}`, { defaultValue: a.cause })]);
  }
  const sbxStages: SandboxStage[] = d.kind === "sandbox" && Array.isArray(a.stages) ? a.stages : [];
  const changed = d.kind === "notice" && d.name === "stamp_changed" ? stampChanges(a, t) : [];
  const showsContext = d.kind === "context" || (d.kind === "notice" && d.name === "context_changed");
  return (
    <>
      <h3 className="steps-h3"><span className={`steps-kind k-${kind}`} />{stepLabel(d, t)}<span className={`steps-pill ${pill.cls}`}>{pill.text}</span></h3>
      {d.kind === "sandbox" && d.status === "error" && (
        <div className="steps-callout err" data-testid="steps-sandbox-failed">
          {t("steps.sbx.failed", {
            stage: t(`steps.sbx_stage.${a.failed_stage || "preparing"}`, { defaultValue: a.failed_stage || "preparing" }).toLowerCase(),
            type: a.error?.type || "error",
          })}
        </div>
      )}
      <Kv rows={rows} />
      {sbxStages.length > 0 && (
        <>
          <Sec label={t("steps.sbx.stages")} />
          <Kv rows={sbxStages.map((s) => [
            t(`steps.sbx_stage.${s.name}`, { defaultValue: s.name }),
            s.uncertain ? t("steps.sbx.image_note") : `${formatDuration(s.duration_ms)}${s.status === "error" ? ` · ${t("steps.status.error")}` : ""}`,
          ] as [string, React.ReactNode])} />
        </>
      )}
      {d.kind === "notice" && d.name === "clock_jump" && <p className="steps-line">{t("steps.gap.asleep_note")}</p>}
      {d.kind === "sandbox" && a.error?.text && (
        <>
          <Sec label={t("steps.sbx.error_text")} />
          <div className="steps-box">{a.error.text}</div>
        </>
      )}
      {changed.length > 0 && (
        <>
          <Sec label={t("steps.insp.changed")} />
          <Kv rows={changed} />
        </>
      )}
      {showsContext && (
        <>
          <Sec label={d.kind === "context" ? t("steps.insp.context_added") : t("steps.insp.context_now")} />
          <ContextAdded ctx={a} t={t} />
        </>
      )}
      {d.children?.length > 0 && (
        <>
          <Sec label={t("steps.insp.inside")} />
          <div className="steps-ctxlist">
            {d.children.map((c: any) => (
              <div key={c.step_id}><span className={`steps-kind k-${KIND_CLASS[c.kind] || "ctx"}`} /><span className="steps-c">{stepLabel(c, t)}</span><span className="steps-sz">{formatDuration(c.duration_ms)}</span></div>
            ))}
          </div>
        </>
      )}
      {exact}
    </>
  );
}

/** A `stamp_changed` notice's new values: only the keys that changed (attended, reviewer,
 * the sandbox group). */
function stampChanges(a: Record<string, any>, t: T): [string, React.ReactNode][] {
  const out: [string, React.ReactNode][] = [];
  if (typeof a.attended === "boolean")
    out.push([t("steps.insp.changed_attended"), t(a.attended ? "steps.insp.attended_yes" : "steps.insp.attended_no")]);
  if (a.reviewer) {
    const who = reviewerLabel(a.reviewer, t);
    out.push([t("steps.insp.changed_reviewer"),
              a.reviewer.active && who ? who
                : a.reviewer.shadow && who ? t("steps.reviewer.shadow", { reviewer: who })
                : t("steps.insp.reviewer_off")]);
  }
  const sb = a.stamp?.sandbox;
  if (sb) {
    const extra = [sb.sites != null ? t("steps.insp.sites", { count: sb.sites }) : "",
                   sb.runner_version ? t("steps.insp.runner", { version: sb.runner_version }) : ""].filter(Boolean);
    out.push([t("steps.insp.changed_sandbox"), [stampLine({ sandbox: sb }, t), ...extra].join(" · ")]);
  }
  return out;
}

function InputBox({ lines, t }: { lines: string[]; t: T }) {
  const [all, setAll] = useState(false);
  const shown = all ? lines : lines.slice(-3);
  return (
    <>
      <Sec
        label={all ? t("steps.insp.input_all", { total: lines.length }) : t("steps.insp.input_last", { n: shown.length, total: lines.length })}
        right={lines.length > 3 ? <button className="steps-btn" onClick={() => setAll(!all)}>{all ? t("steps.insp.show_less") : t("steps.insp.show_all")}</button> : undefined}
      />
      <div className={"steps-box" + (all ? " tall" : "")}>{shown.join("\n\n")}</div>
    </>
  );
}

function CheckInspector({ d, turn, sessionId, t }: { d: any; turn: RecordedTurn; sessionId: string; t: T }) {
  const a = d.attrs || {};
  const result = d.status === "error" ? "could_not_run" : a.result;
  const inner = (d.children || []).find((c: any) => c.kind === "model");
  const [shown, setShown] = useState<string>("");
  useEffect(() => {
    let live = true;
    if (inner) {
      getStepDetail(sessionId, inner.step_id).then((x) => {
        if (!live || !x) return;
        const lines = inputLines((x.exchange_rebuilt || []).find((e: any) => !e.auxiliary)?.request);
        const users = lines.filter((l) => !l.startsWith("system ·") && !l.startsWith("developer ·"));
        setShown((users.length ? users : lines).slice(-1).join(""));
      }).catch(() => {});
    }
    return () => {
      live = false;
    };
  }, [sessionId, inner?.step_id]);  // eslint-disable-line react-hooks/exhaustive-deps
  const pillCls = result === "allow" ? "ok" : result === "deny" || result === "could_not_run" ? "danger" : "warn";
  const reviewer = reviewerLabel(a.reviewer, t) || reviewerLabel(turn.reviewer, t);
  const target = d.for_step;
  const action = target ? `${target.name}${d.arguments?.command ? ` ${d.arguments.command}` : ""}` : d.name;
  const u = inner?.attrs?.usage;
  const tokens = u?.reported
    ? t("steps.insp.check_tokens", { in: formatTokens((u.cache_read || 0) + (u.cache_write || 0) + (u.input_fresh || 0)),
        cache: formatTokens(u.cache_read || 0), out: formatTokens(u.output || 0) })
    : inner ? t("steps.tokens_not_reported") : "—";
  const questions: any[] = Array.isArray(d.content?.questions) ? d.content.questions : [];
  const wait = d.wait;
  return (
    <>
      <h3 className="steps-h3">
        <span className="steps-kind k-review" />
        {d.shadow ? t("steps.insp.shadow_title") : t("steps.insp.check_title")}
        <span className={`steps-pill ${d.shadow ? "" : pillCls}`}>{d.shadow ? t("steps.check_mode.shadow") : t(`steps.result.${result}`, { defaultValue: result || "" })}</span>
      </h3>
      <div className="steps-meta">
        {[target ? t("steps.insp.for_step", { n: target.seq ?? "—", action }) : action, reviewer ? t("steps.reviewer_is", { who: reviewer }) : ""].filter(Boolean).join(" · ")}
      </div>
      {result === "could_not_run" && <div className="steps-callout err">{t("steps.insp.cnr")}</div>}
      <Kv rows={d.shadow ? [
        [t("steps.insp.would"), t(`steps.result.${result}`, { defaultValue: result || "" })],
        [t("steps.insp.you_decided"), wait ? <><b>{t(`steps.outcome.${wait.attrs?.outcome}`, { defaultValue: wait.attrs?.outcome || "—" })}</b> {t("steps.insp.after", { t: formatDuration(wait.duration_ms) })}</> : "—"],
        [t("steps.insp.match"), a.matched_user == null ? "—" : <span className={"steps-pill " + (a.matched_user ? "ok" : "warn")}>{a.matched_user ? t("steps.insp.matched") : t("steps.insp.not_matched")}</span>],
        [t("steps.insp.took"), formatDuration(d.duration_ms)],
        [t("steps.insp.tokens"), tokens],
      ] : [
        [t("steps.insp.result_label"), t(`steps.insp.result_${result}`, { defaultValue: t(`steps.result.${result}`, { defaultValue: result || "—" }) })],
        ...(d.content?.reason ? [[t("steps.detail.reason"), `"${d.content.reason}"`] as [string, React.ReactNode]] : []),
        [t("steps.insp.took"), formatDuration(d.duration_ms)],
        [t("steps.insp.tokens"), tokens],
        ...(a.breaker ? [[t("steps.detail.breaker"), a.breaker.tripped
          ? t("steps.insp.breaker_paused", { n: a.breaker.denials_in_row })
          : t("steps.insp.breaker_armed", { n: a.breaker.denials_in_row ?? 0 })] as [string, React.ReactNode]] : []),
      ]} />
      {questions.length > 0 && (
        <>
          <Sec label={t("steps.insp.questions")} />
          <table className="steps-qtable" data-testid="steps-questions">
            <thead><tr><th>{t("steps.insp.question")}</th><th>{t("steps.insp.answer")}</th></tr></thead>
            <tbody>
              {questions.map((q) => (
                <tr key={q.name}><td className="steps-mono">{q.name}</td><td>{t(`steps.detail.${q.answer === "yes" ? "yes" : "no"}`)}{q.score != null ? ` ${q.score.toFixed(2)}` : ""}</td></tr>
              ))}
            </tbody>
          </table>
          {a.reviewer?.external && <div className="steps-callout info">{t("steps.insp.outside", { name: reviewer })}</div>}
        </>
      )}
      {shown && (
        <>
          <Sec label={t("steps.insp.shown")} />
          <div className="steps-box">{shown}</div>
        </>
      )}
      <Joined audit={d.audit} t={t} />
    </>
  );
}

// -- usage (OB-4) --------------------------------------------------------------------------------

const PURPOSE_CLASS: Record<string, string> = { main: "c-new", helper: "c-new", summary: "c-sum", auto_approver: "c-out", title: "c-faint" };

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
  const purposes = (Object.entries(u.by_purpose || {}) as [string, number][]).sort((x, y) => y[1] - x[1]);
  const max = Math.max(1, ...purposes.map(([, n]) => n));
  return (
    <div className="steps-usage" data-testid="steps-usage">
      <div className="steps-seclbl steps-usage-title">{t("steps.usage.title")}<span className="steps-sp" />
        {u.turns != null && <span className="steps-seclbl-right">{t("steps.usage.turns", { count: u.turns })}</span>}</div>
      <div className="steps-tiles">
        <Tile value={(u.at_least ? "≥ " : "") + formatTokens(u.totals?.input || 0)} label={t("steps.usage.input")} />
        <Tile value={formatTokens(u.totals?.output || 0)} label={t("steps.usage.output")} />
        <Tile value={u.cache_share == null ? "—" : `${Math.round(u.cache_share * 100)}%`} label={t("steps.usage.from_cache")} />
      </div>
      {u.unreported_calls > 0 && <p className="steps-line">{t("steps.usage.unreported", { count: u.unreported_calls })}</p>}
      {u.failed_calls > 0 && <p className="steps-line">{t("steps.usage.failed", { count: u.failed_calls })}</p>}
      {u.overflow_calls > 0 && <p className="steps-line">{t("steps.usage.overflow", { count: u.overflow_calls })}</p>}
      <Sec label={t("steps.usage.by_purpose")} />
      <div className="steps-purposes">
        {purposes.map(([p, n]) => (
          <div className="steps-purpose" key={p}>
            <span>{t(`steps.purpose_long.${p}`, { defaultValue: p })}</span>
            <span className="steps-num">{formatTokens(n)}</span>
            <span className="steps-purpose-bar"><i className={PURPOSE_CLASS[p] || "c-new"} style={{ width: `${Math.max(2, (n / max) * 100)}%` }} /></span>
          </div>
        ))}
      </div>
      <Sec label={t("steps.usage.by_model")} />
      <table className="steps-models">
        <thead><tr><th>{t("steps.usage.model")}</th><th>{t("steps.usage.in")}</th><th>{t("steps.usage.cache")}</th><th>{t("steps.usage.out")}</th></tr></thead>
        <tbody>
          {Object.entries(u.by_model_parts || {}).map(([m, p]: [string, any]) => {
            const [model, purpose] = m.split(" (");
            return (
              <tr key={m}><td>{model}{purpose && <span className="steps-faint"> ({purpose}</span>}</td><td>{formatTokens(p.input)}</td><td>{formatTokens(p.cache)}</td><td>{formatTokens(p.output)}</td></tr>
            );
          })}
        </tbody>
      </table>
      {u.context && u.context.window ? (
        <>
          <Sec label={t("steps.usage.context_now")} />
          <div className="steps-purpose-bar wide"><i className="c-new" style={{ width: `${Math.min(100, (u.context.tokens / u.context.window) * 100)}%` }} /></div>
          <p className="steps-line">{t("steps.usage.context_line", { used: formatTokens(u.context.tokens), window: formatTokens(u.context.window) })}</p>
        </>
      ) : u.context?.tokens ? (
        <>
          <Sec label={t("steps.usage.context_now")} />
          <p className="steps-line">{t("steps.usage.context_no_window", { used: formatTokens(u.context.tokens) })}</p>
        </>
      ) : null}
    </div>
  );
}
