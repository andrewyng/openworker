// Observability Phase 3 in TeamView (technical-design.md §10, §11; UX-059 ob16, ob17):
// - "By wake reason": why each team turn started, its tokens (lead and workers together) and
//   cache-read share, split by role on hover. The rows add up to the team total (V3-09).
// - "Team events that used to be silent": cap hits, long deferrals, watchdog nudges, lead
//   polling, an approval asked twice, a review before its prerequisite; each opens Steps.
// - A security task's "Coverage for this task" and a finding's "Evidence".

import { useEffect, useState } from "react";
import { findStepByCall, type RecordedStep, type RecordedTurn } from "../api";
import type { CoverageScan, TaskCoverage, TaskFinding, TeamEvent, TeamWake } from "../teamView";
import { formatTokens } from "../usage";
import { formatDuration } from "./StepsPanel";
import { AREA_WORD, cacheShare, wakeKey } from "../obs3";

type T = (k: string, o?: any) => string;

export function WakeSplit({ wakes, explained, t }: { wakes: TeamWake[]; explained?: { turns: number; explained: number }; t: T }) {
  if (!wakes.length) return null;
  const total = wakes.reduce((n, w) => n + w.total, 0);
  const count = wakes.reduce((n, w) => n + w.wakes, 0);
  const cr = wakes.reduce((n, w) => n + w.cache_read, 0);
  const inp = wakes.reduce((n, w) => n + w.input, 0);
  return (
    <section className="team-stats-section" data-testid="team-wake-split">
      <h3>{t("p3.team.by_wake")}</h3>
      <table className="p3-t">
        <thead>
          <tr><th>{t("p3.team.why")}</th><th className="n">{t("p3.team.wakes")}</th><th className="n">{t("teamview.tokens")}</th>
            <th className="n">{t("p3.team.cache_reads")}</th><th>{t("p3.team.who")}</th></tr>
        </thead>
        <tbody>
          {wakes.map((w) => {
            const share = cacheShare(w.cache_read, w.input);
            const roles = Object.entries(w.by_role || {});
            const split = roles.map(([role, v]) => `${t(role === "lead" ? "teamview.lead" : "p3.team.worker")}: ${v.wakes} · ${formatTokens(v.total)}`).join("\n");
            return (
              <tr key={w.reason} title={split} data-testid={`team-wake-${w.reason}`}>
                <td>{t(wakeKey({ reason: w.reason }) || "p3.wake.unknown")}</td>
                <td className="n">{w.wakes}</td>
                <td className="n">{(w.at_least ? "≥ " : "") + formatTokens(w.total)}</td>
                <td className="n">{share == null ? "—" : `${share}%`}</td>
                <td className="text-faint">{roles.map(([r]) => t(r === "lead" ? "teamview.lead" : "p3.team.worker")).join(", ")}</td>
              </tr>
            );
          })}
          <tr>
            <td><b>{t("p3.team.total")}</b></td>
            <td className="n"><b>{count}</b></td>
            <td className="n"><b>{formatTokens(total)}</b></td>
            <td className="n">{cacheShare(cr, inp) == null ? "—" : `${cacheShare(cr, inp)}%`}</td>
            <td></td>
          </tr>
        </tbody>
      </table>
      {explained && explained.turns > 0 && (
        <p className="team-totals" data-testid="team-wake-explained">
          {t("p3.team.explained", { n: explained.explained, of: explained.turns, pct: Math.round((explained.explained / explained.turns) * 100) })}
        </p>
      )}
    </section>
  );
}

/** One event as a sentence (the i18n keys carry the words). */
export function eventText(e: TeamEvent, t: T): string {
  const mins = (ms?: number | null) => Math.max(1, Math.round((ms || 0) / 60000));
  switch (e.kind) {
    case "wake_capped":
      return t("p3.ev.wake_capped", { min: mins(e.deferred_ms) });
    case "wake_cap_reached":
      return t("p3.ev.wake_cap_reached", { count: e.count ?? 60, held: (e.held || []).length });
    case "deferred":
      return t("p3.ev.deferred", { who: e.who, min: mins(e.deferred_ms), by: t(`p3.ev.by_${e.by}`, { defaultValue: e.by }) });
    case "watchdog_nudged":
      return t("p3.ev.watchdog_nudged", { items: (e.item_ids || []).join(", ") || "—" });
    case "lead_polling":
      return t("p3.ev.lead_polling", { count: e.count });
    case "same_approval_twice":
      return t("p3.ev.same_approval_twice", { tool: e.tool, first: e.first_by, again: e.again_by });
    case "reviewed_before_prereq":
      return t("p3.ev.reviewed_before_prereq", { item: e.item, pre: e.prerequisite, to: e.to || "review" });
    default:
      return e.kind;
  }
}

export function TeamEvents({ events, t, onOpenSteps, onOpenBoard }: {
  events: TeamEvent[];
  t: T;
  onOpenSteps?: (sessionId: string, turnId: string) => void;
  onOpenBoard?: () => void;
}) {
  if (!events.length) return null;
  return (
    <section className="team-stats-section" data-testid="team-events">
      <h3>{t("p3.team.events")}</h3>
      <div className="flex flex-col gap-1.5">
        {events.map((e, i) => {
          const open = e.session_id && e.turn_id && onOpenSteps ? () => onOpenSteps(e.session_id!, e.turn_id!) : onOpenBoard;
          return (
            <button key={i} className="p3-event" disabled={!open} onClick={open} data-testid={`team-event-${e.kind}`}>
              <span className={"steps-badge " + (e.severity === "warn" ? "warn" : "")}>{t(e.severity === "warn" ? "p3.team.issue" : "p3.team.info")}</span>
              <span className="min-w-0 flex-1 text-left">{eventText(e, t)}</span>
              {e.at != null && <span className="text-faint">{new Date(e.at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span>}
            </button>
          );
        })}
      </div>
      <p className="team-totals">{t("p3.team.events_note")}</p>
    </section>
  );
}

// -- OB-16 ---------------------------------------------------------------------------------------

/** "exit 1 (found something)" when the scanner's code means it found something (M3-R9). */
export function exitText(s: { exit_code: number | null; found_something?: boolean }, t: T): string {
  if (s.exit_code == null) return t("p3.cov.no_exit");
  return s.found_something && s.exit_code !== 0 ? t("p3.cov.exit_found", { code: s.exit_code }) : t("p3.cov.exit", { code: s.exit_code });
}

export function CoverageBlock({ coverage, t, onOpenStep }: { coverage: TaskCoverage; t: T; onOpenStep?: (s: CoverageScan) => void }) {
  if (coverage.not_checked) return (
    <div className="team-stats-section" data-testid="task-coverage">
      <h3>{t("p3.cov.title")}</h3>
      <p className="team-totals">{t("p3.cov.not_checked")}</p>
    </div>
  );
  const ran = new Set(coverage.scans.filter((s) => s.ok).map((s) => s.area));
  return (
    <div className="team-stats-section" data-testid="task-coverage">
      <h3>{t("p3.cov.title")}</h3>
      <div className="flex flex-col gap-1 text-meta">
        {coverage.scans.map((s, i) => (
          <div key={i} className={s.ok ? "" : "text-danger"}>
            {s.ok ? "✓" : "✗"} {t(s.ok ? "p3.cov.ran" : "p3.cov.failed", { scanner: s.scanner })} · {exitText(s, t)}
            {s.turn_seq != null && <span className="text-faint"> · {t("p3.cov.where", { actor: s.actor || "", turn: s.turn_seq, step: s.step_seq ?? "?" })}</span>}
            {onOpenStep && s.session_id && s.turn_id && (
              <button className="p3-link ml-2" onClick={() => onOpenStep(s)}>{t("p3.cov.open_step")}</button>
            )}
          </div>
        ))}
        {coverage.promised.filter((a) => !ran.has(a)).map((a) => (
          <div key={a} className="text-danger">✗ {t("p3.cov.missing", { area: t(`p3.area.${a}`, { defaultValue: AREA_WORD[a] || a }) })}</div>
        ))}
        {(coverage.fine || []).filter((f) => !["scan_missing", "scanner_failed"].includes(f.check)).map((f) => (
          <div key={f.check}>✓ {f.text}</div>
        ))}
      </div>
      {coverage.issues.map((i, k) => (
        // The engine's English sentence, as the Issues tab shows it (Phase 2 TD2-3 build note).
        <div key={k} className="p3-callout err mt-2">{i.text}</div>
      ))}
      <p className="team-totals">{t("p3.cov.promised", { areas: coverage.promised.map((a) => t(`p3.area.${a}`, { defaultValue: a })).join(", ") })}</p>
    </div>
  );
}

export function EvidenceBlock({ finding, t, onOpenStep }: { finding: TaskFinding; t: T; onOpenStep?: (sessionId: string, turnId: string) => void }) {
  const [found, setFound] = useState<{ step: RecordedStep; turn: RecordedTurn; session_id: string }[] | null>(null);
  useEffect(() => {
    let live = true;
    const ev = finding.evidence || [];
    Promise.all(ev.map((e) => findStepByCall(e.session_id, e.call_id).then((r) => (r ? { ...r, session_id: e.session_id } : null)).catch(() => null)))
      .then((rows) => live && setFound(rows.filter(Boolean) as any))
      .catch(() => live && setFound([]));
    return () => {
      live = false;
    };
  }, [finding]);
  return (
    <div className="team-stats-section" data-testid="finding-evidence">
      <h3>{t("p3.ev2.title", { id: finding.id })}{finding.severity ? ` · ${finding.severity}` : ""}</h3>
      {!(finding.evidence || []).length ? (
        <p className="team-totals text-danger">{t("p3.ev2.none")}</p>
      ) : found == null ? (
        <p className="team-totals">{t("p3.usage.loading")}</p>
      ) : found.length === 0 ? (
        <p className="team-totals">{t("p3.ev2.no_recorded_step")}</p>
      ) : (
        found.map(({ step, turn, session_id }) => {
          const a = step.attrs || {};
          const sc = a.scanner?.name;
          return (
            <div key={step.step_id} className="flex items-start gap-3 text-meta py-1">
              <div className="flex-1 min-w-0">
                <b className="font-medium">{t("p3.ev2.found_by", { turn: turn.seq, step: step.seq ?? "?" })}</b>
                <div className="font-mono text-muted">
                  {[sc || step.name, a.exit_code != null ? exitText({ exit_code: a.exit_code, found_something: a.exit_code === 1 && !!sc }, t) : null,
                    a.result_sha256 ? `output sha256:${String(a.result_sha256).slice(0, 4)}…${String(a.result_sha256).slice(-2)}` : null,
                    step.duration_ms != null ? formatDuration(step.duration_ms) : null].filter(Boolean).join(" · ")}
                </div>
              </div>
              {onOpenStep && <button className="p3-link" onClick={() => onOpenStep(session_id, turn.turn_id)}>{t("p3.cov.open_step")}</button>}
            </div>
          );
        })
      )}
      {finding.fixed_by && <p className="team-totals">{t("p3.ev2.fixed_by", { who: finding.fixed_by })}</p>}
      {finding.verified_by && (
        <p className={"team-totals" + (finding.verified_by === finding.fixed_by ? " text-danger" : "")}>
          {finding.verified_by === finding.fixed_by ? t("p3.ev2.self_verified", { who: finding.verified_by }) : t("p3.ev2.verified_by", { who: finding.verified_by })}
        </p>
      )}
    </div>
  );
}
