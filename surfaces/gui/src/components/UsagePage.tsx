// Observability Phase 3, OB-8: Activity ▸ Usage (technical-design.md §7, UX-059 ob8). How much
// the coworkers, models and machines used over Today, 7 or 30 days. Every number is a sum of
// whole turns from the usage ledger, grouped by the turn's start on the viewer's own local days
// (TD3-1, TD3-2), so the page agrees with the pills. Tokens only, never prices.

import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  getApproverStrip,
  getHeaviestTurns,
  getMachines,
  getUsageRollup,
  type ApproverStrip,
  type HeavyTurn,
  type Machine,
  type UsageRollup,
} from "../api";
import { formatTokens } from "../usage";
import { formatDuration } from "./StepsPanel";
import {
  approverPart,
  changePct,
  comparable,
  hhmm,
  localDays,
  previousDays,
  purposeByDay,
  SERIES,
  type Period,
  type RollupGroup,
} from "../obs3";

const CARD = "rounded-xl2 border border-line bg-panel";
type T = (k: string, o?: any) => string;

interface MachineRow {
  id: string | null;
  name: string;
  total: number | null;
  at_least: boolean;
  state: "live" | "offline" | "cached";
  as_of?: number;
}

export interface UsageData {
  total: UsageRollup;
  prev: UsageRollup | null;
  dayPurpose: UsageRollup;
  coworkers: UsageRollup;
  heavy: HeavyTurn[];
  approver: ApproverStrip | null;
  machines: MachineRow[];
}

/** The coworkers of the period for the picker: each id once, by its name (M3-R1), A to Z. */
export function coworkerChoices(r: UsageRollup | null | undefined): { id: string; name: string }[] {
  const names = r?.names || {};
  const ids = new Set<string>();
  for (const g of r?.groups || []) {
    const id = g.key.persona_id as string | null;
    if (id) ids.add(id);
  }
  return [...ids].map((id) => ({ id, name: names[id] || id })).sort((a, b) => a.name.localeCompare(b.name));
}

const sumAll = (r: UsageRollup | null | undefined): RollupGroup | null =>
  r && r.groups.length ? r.groups[0] : null;

/** Everything the page shows, for one period: one call per grouping, each to the machine
 * that ran the work. Remote machines answer through the controller's proxy. */
export async function loadUsage(period: Period, now = new Date(), personaId: string | null = null): Promise<UsageData> {
  const days = localDays(period, now);
  const from = days[0][1];
  const to = days[days.length - 1][2];
  const prevDays = previousDays(days);
  // One coworker, when picked: every number on the page narrows to its turns. The By
  // coworker table keeps listing all of them, so the picker always has its choices.
  const who = personaId ? { persona_id: personaId } : {};
  const [total, prev, dayPurpose, coworkers, heavy, approver, fleet] = await Promise.all([
    getUsageRollup({ from, to, group: "all", ...who }),
    getUsageRollup({ from: prevDays[0][1], to: prevDays[prevDays.length - 1][2], group: "all", ...who }).catch(() => null),
    getUsageRollup({ from, to, days, group: "day,purpose", ...who }),
    getUsageRollup({ from, to, group: "persona_id,task_id" }),
    getHeaviestTurns(from, to, null, 20, personaId).then((r) => r.turns).catch(() => [] as HeavyTurn[]),
    getApproverStrip(from, to, null, personaId).catch(() => null),
    getMachines().then((r) => r.machines || []).catch(() => [] as Machine[]),
  ]);
  const here = sumAll(total);
  const machines: MachineRow[] = [
    { id: null, name: "", total: here?.total ?? 0, at_least: (here?.unreported ?? 0) > 0, state: "live" },
  ];
  const remote = await Promise.all(
    fleet.map(async (m): Promise<MachineRow> => {
      try {
        const r = await getUsageRollup({ from, to, group: "all", ...who }, m.id);
        const g = sumAll(r);
        return {
          id: m.id, name: m.name, total: g?.total ?? 0, at_least: (g?.unreported ?? 0) > 0,
          state: r.cached ? "cached" : "live", as_of: r.cached?.as_of,
        };
      } catch {
        return { id: m.id, name: m.name, total: null, at_least: false, state: "offline" };
      }
    }),
  );
  return { total, prev, dayPurpose, coworkers, heavy, approver, machines: [...machines, ...remote] };
}

export function UsagePage({ onOpenTurn }: { onOpenTurn?: (sessionId: string, turnId: string) => void }) {
  const { t } = useTranslation();
  const [period, setPeriod] = useState<Period>("7d");
  const [persona, setPersona] = useState<string | null>(null);
  const [data, setData] = useState<UsageData | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let live = true;
    setError(false);
    loadUsage(period, new Date(), persona)
      .then((d) => live && setData(d))
      .catch(() => live && setError(true));
    return () => {
      live = false;
    };
  }, [period, persona]);
  const choices = data ? coworkerChoices(data.coworkers) : [];

  return (
    <div data-testid="usage-page">
      <div className="flex items-center gap-2 mb-4">
        <div className="p3-seg" role="tablist">
          {(["today", "7d", "30d"] as Period[]).map((p) => (
            <button key={p} role="tab" aria-pressed={period === p} data-testid={`usage-period-${p}`} onClick={() => setPeriod(p)}>
              {t(`p3.usage.period_${p}`)}
            </button>
          ))}
        </div>
        <select
          className="p3-select"
          data-testid="usage-coworker"
          aria-label={t("p3.usage.coworker_filter")}
          value={persona || ""}
          onChange={(e) => setPersona(e.target.value || null)}
        >
          <option value="">{t("p3.usage.all_coworkers")}</option>
          {choices.map((c) => (
            <option key={c.id} value={c.id}>{c.name}</option>
          ))}
          {persona && !choices.some((c) => c.id === persona) && <option value={persona}>{persona}</option>}
        </select>
      </div>
      {error ? (
        <div className={CARD + " p-4 text-ui text-muted"}>{t("p3.usage.load_error")}</div>
      ) : !data ? (
        <div className={CARD + " p-4 text-ui text-muted"}>{t("p3.usage.loading")}</div>
      ) : (
        <UsageBody data={data} period={period} t={t} onOpenTurn={onOpenTurn} />
      )}
    </div>
  );
}

export function UsageBody({ data, period, t, onOpenTurn }: { data: UsageData; period: Period; t: T; onOpenTurn?: (sessionId: string, turnId: string) => void }) {
  const days = useMemo(() => localDays(period), [period]);
  const all = sumAll(data.total);
  const prev = sumAll(data.prev);
  const prevStart = previousDays(days)[0][1];
  const canCompare = comparable(data.total.kept_from, prevStart);
  const pct = canCompare && prev ? changePct(all?.total ?? 0, prev.total) : null;
  const chart = purposeByDay(days, data.dayPurpose.groups);
  const names = data.total.names || data.coworkers.names || {};
  const turns = all?.turns ?? 0;
  const checked = all?.checked ?? 0;
  const withWarn = all?.with_warn ?? 0;
  const notChecked = data.total.not_checked || 0;
  const coworkerRows = coworkerTable(data.coworkers.groups, names, t);
  const ap = data.approver;

  return (
    <>
      <div className="p3-tiles">
        <Tile label={t("p3.usage.tokens")} value={(all?.unreported ? "≥ " : "") + formatTokens(all?.total ?? 0)}
          sub={!canCompare ? t(`p3.usage.no_compare_${period}`) : pct == null ? t("p3.usage.no_compare_none") :
            t("p3.usage.vs_last", { sign: pct >= 0 ? "▲" : "▼", pct: Math.abs(pct) })} testid="usage-tile-tokens" />
        <Tile label={t("p3.usage.turns")} value={String(turns)}
          sub={t("p3.usage.automation_runs", { count: all?.automation_turns ?? 0 })} testid="usage-tile-turns" />
        <Tile label={t("p3.usage.with_issues")} value={checked ? `${Math.round((withWarn / checked) * 100)}%` : "—"}
          sub={t("p3.usage.with_issues_sub", { n: withWarn, of: checked }) + (notChecked ? " · " + t("p3.usage.not_checked", { count: notChecked }) : "")}
          testid="usage-tile-issues" />
        <Tile label={t("p3.usage.median_time")} value={formatDuration(all?.median_active_ms ?? null)}
          sub={t("p3.usage.median_sub")} testid="usage-tile-median" />
      </div>

      <div className="p3-two">
        <div className={CARD + " p3-card"}>
          <b className="p3-h">{t("p3.usage.per_day")}</b>
          <div className="p3-bars" data-testid="usage-bars">
            {chart.rows.map((r) => (
              <div key={r.label} className="p3-bar" title={`${r.label}: ${formatTokens(r.total)}`}>
                {SERIES.map((s) =>
                  r.values[s] ? (
                    <i key={s} className={`p3-s-${s}`} style={{ height: `${Math.max(1, (r.values[s] / chart.max) * 100)}%` }}
                      title={`${t(`p3.series.${s}`)}: ${r.values[s].toLocaleString()}`} />
                  ) : null,
                )}
              </div>
            ))}
          </div>
          {days.length > 1 && (
            <div className="p3-xl">
              {days.map(([label, start]) => (
                <span key={label}>{days.length <= 7 ? new Date(start).toLocaleDateString(undefined, { weekday: "short", day: "2-digit" }) : new Date(start).getDate()}</span>
              ))}
            </div>
          )}
          <div className="p3-legend" data-testid="usage-legend">
            {SERIES.filter((s) => chart.legend[s] > 0).map((s) => (
              <span key={s}><i className={`p3-s-${s}`} />{t(`p3.series.${s}`)} {formatTokens(chart.legend[s])}</span>
            ))}
          </div>
        </div>
        <div className={CARD + " p3-card"}>
          <b className="p3-h">{t("p3.usage.by_coworker")}</b>
          <table className="p3-t" data-testid="usage-coworkers">
            <thead><tr><th>{t("p3.usage.coworker")}</th><th className="n">{t("p3.usage.tokens")}</th><th className="n">{t("p3.usage.with_issues")}</th></tr></thead>
            <tbody>
              {coworkerRows.map((r) => (
                <tr key={r.key}><td>{r.name}{r.automation && <span className="text-faint"> {t("p3.usage.automation")}</span>}</td>
                  <td className="n">{(r.atLeast ? "≥ " : "") + formatTokens(r.total)}</td>
                  <td className="n">{r.checked ? `${Math.round((r.withWarn / r.checked) * 100)}%` : "—"}</td></tr>
              ))}
            </tbody>
          </table>
          <b className="p3-h mt-3 block">{t("p3.usage.by_machine")}</b>
          <table className="p3-t" data-testid="usage-machines">
            <thead><tr><th>{t("p3.usage.machine")}</th><th className="n">{t("p3.usage.tokens")}</th><th></th></tr></thead>
            <tbody>
              {data.machines.map((m) => (
                <tr key={m.id || "here"}>
                  <td>{m.id ? m.name : t("p3.usage.this_machine")}</td>
                  <td className="n">{m.total == null ? "—" : (m.at_least ? "≥ " : "") + formatTokens(m.total)}</td>
                  <td className="text-faint">
                    {m.state === "live" ? t("p3.usage.live") : m.state === "cached" && m.as_of ? t("p3.usage.offline_as_of", { time: hhmm(m.as_of) }) : t("p3.usage.offline")}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className={CARD + " p3-card mt-3"}>
        <b className="p3-h">{t("p3.usage.heaviest")}</b>
        {data.heavy.length === 0 ? (
          <div className="text-meta text-faint mt-1">{t("p3.usage.no_turns")}</div>
        ) : (
          <table className="p3-t" data-testid="usage-heaviest">
            <thead><tr><th>{t("p3.usage.session")}</th><th>{t("p3.usage.coworker")}</th><th>{t("p3.usage.model")}</th>
              <th className="n">{t("p3.usage.tokens")}</th><th className="n">{t("p3.usage.active_time")}</th><th>{t("p3.usage.issues")}</th><th></th></tr></thead>
            <tbody>
              {data.heavy.map((h) => (
                <tr key={h.turn_id}>
                  <td className="p3-trunc">{h.session_deleted ? <span className="text-faint">{t("p3.usage.deleted_session")}</span> : (h.title || h.session_id)}
                    {h.seq != null && <span className="text-faint"> · {t("p3.usage.turn_n", { n: h.seq })}</span>}</td>
                  <td>{(h.persona_id && names[h.persona_id]) || h.persona_id || "—"}</td>
                  <td className="font-mono">{h.model_ids.join(", ") || "—"}</td>
                  <td className="n">{(h.at_least ? "≥ " : "") + formatTokens(h.total)}</td>
                  <td className="n">{h.found_open ? <span className="text-faint">{t("p3.usage.found_open")}</span> : formatDuration(h.active_ms)}</td>
                  <td>{h.checks.map((c) => <span key={c} className="steps-badge warn mr-1">{t(`steps.issues.title.${c}`, { defaultValue: c })}</span>)}</td>
                  <td>{!h.session_deleted && onOpenTurn && (
                    <button className="p3-link" data-testid="usage-open-steps" onClick={() => onOpenTurn(h.session_id, h.turn_id)}>{t("steps.open")}</button>
                  )}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {ap && ap.shown && <ApproverStripView ap={ap} t={t} />}

      {data.total.no_turn.calls > 0 && (
        <div className="text-meta text-faint mt-2" data-testid="usage-no-turn">
          {t("p3.usage.no_turn", { calls: data.total.no_turn.calls, tokens: data.total.no_turn.total.toLocaleString() })}
        </div>
      )}
      {(data.total.deleted_session_turns ?? 0) > 0 && (
        <div className="text-meta text-faint mt-1">{t("p3.usage.deleted_note", { count: data.total.deleted_session_turns })}</div>
      )}
      {data.total.kept_from != null && (
        <div className="text-meta text-faint mt-1">{t("p3.usage.kept_note")}</div>
      )}
    </>
  );
}

function Tile({ label, value, sub, testid }: { label: string; value: string; sub: string; testid?: string }) {
  return (
    <div className="p3-kpi" data-testid={testid}>
      <span>{label}</span>
      <b>{value}</b>
      <em>{sub}</em>
    </div>
  );
}

/** By coworker, named from the installed coworkers or the kept versions (M3-R1); an
 * automation's turns get their own row under their coworker, "(automation)". */
export function coworkerTable(groups: RollupGroup[], names: Record<string, string>, t: T) {
  const rows = new Map<string, { key: string; name: string; automation: boolean; total: number; atLeast: boolean; checked: number; withWarn: number }>();
  for (const g of groups) {
    const pid = g.key.persona_id as string | null;
    const auto = g.key.task_id != null;
    const key = `${pid ?? ""}|${auto ? "a" : ""}`;
    const r = rows.get(key) || { key, name: pid ? names[pid] || pid : t("p3.usage.unknown_coworker"), automation: auto, total: 0, atLeast: false, checked: 0, withWarn: 0 };
    r.total += g.total;
    r.atLeast = r.atLeast || g.unreported > 0;
    r.checked += g.checked;
    r.withWarn += g.with_warn;
    rows.set(key, r);
  }
  return [...rows.values()].sort((a, b) => b.total - a.total);
}

export function ApproverStripView({ ap, t }: { ap: ApproverStrip; t: T }) {
  const lines = ap.by_reviewer.length ? ap.by_reviewer : [{ ...ap, kind: "" }];
  return (
    <div className={CARD + " p3-card mt-3 flex flex-col gap-1.5"} data-testid="usage-approver">
      {lines.map((l) => {
        const part = (n: number, key: string) => {
          const p = approverPart(n, l.checks);
          return t(`p3.ap.${key}_${p.share ? "share" : "count"}`, { n: p.value });
        };
        return (
          <div key={l.kind} className="flex items-center gap-3 flex-wrap text-meta">
            <b className="p3-h">{t("p3.ap.title")}</b>
            <span className="text-muted">
              {t("p3.ap.checks", { count: l.checks })}
              {l.kind ? " · " + t(`p3.ap.kind.${l.kind}`, { defaultValue: l.kind }) : ""}
            </span>
            <span className="p3-chip ok">{part(l.allowed, "allowed")}</span>
            <span className="p3-chip warn">{part(l.asked, "asked")}</span>
            <span className="p3-chip err">{part(l.blocked, "blocked")}</span>
            <span className="text-muted">
              {t("p3.ap.could_not_run", { n: l.could_not_run })} · {t("p3.ap.overridden", { n: ap.overridden })} ·{" "}
              {t("p3.ap.median", { time: formatDuration(l.median_ms) })}
            </span>
          </div>
        );
      })}
      <div className="text-meta text-muted">
        {t("p3.ap.tokens", { tokens: formatTokens(ap.tokens) })}
        {ap.shadow_checks > 0 ? " · " + t("p3.ap.incl_shadow", { count: ap.shadow_checks }) : ""}
      </div>
    </div>
  );
}
