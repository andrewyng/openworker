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
  getRatings,
  getUsageRollup,
  type ApproverStrip,
  type RatingsSummary,
  type HeavyTurn,
  type Machine,
  type UsageRollup,
} from "../api";
import { formatTokens } from "../usage";
import { listPersonaName } from "../personaScope";
import { formatDuration } from "./StepsPanel";
import {
  approverPart,
  changePct,
  comparable,
  hhmm,
  localDays,
  overheadByDay,
  previousDays,
  purposeByDay,
  REASONS,
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
  /** T3-10; null when the engine has no ratings endpoint (older) or it failed */
  ratings?: RatingsSummary | null;
}

/** The coworker names as shown on the page: the server's names, the default coworker's as
 * "Coworker (default)" (T3-15 e). The default coworker has an entry even without a name. */
export function shownNames(names: Record<string, string>, t: T): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [id, name] of Object.entries(names)) out[id] = listPersonaName(name, id, t);
  out.cowork = listPersonaName(names.cowork, "cowork", t);
  return out;
}

/** The coworkers of the period for the picker: each id once, by its name (M3-R1), A to Z. */
export function coworkerChoices(r: UsageRollup | null | undefined, t: T): { id: string; name: string }[] {
  const names = shownNames(r?.names || {}, t);
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
  const [total, prev, dayPurpose, coworkers, heavy, approver, fleet, ratings] = await Promise.all([
    getUsageRollup({ from, to, group: "all", ...who }),
    getUsageRollup({ from: prevDays[0][1], to: prevDays[prevDays.length - 1][2], group: "all", ...who }).catch(() => null),
    getUsageRollup({ from, to, days, group: "day,purpose", ...who }),
    getUsageRollup({ from, to, group: "persona_id,task_id" }),
    getHeaviestTurns(from, to, null, 20, personaId).then((r) => r.turns).catch(() => [] as HeavyTurn[]),
    getApproverStrip(from, to, null, personaId).catch(() => null),
    getMachines().then((r) => r.machines || []).catch(() => [] as Machine[]),
    getRatings(from, to, personaId).catch(() => null),
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
  return { total, prev, dayPurpose, coworkers, heavy, approver, machines: [...machines, ...remote], ratings };
}

export function UsagePage({ onOpenTurn, onOpenPersona }: {
  onOpenTurn?: (sessionId: string, turnId: string) => void;
  /** T3-12: a coworker's page, at its "How it's been doing" */
  onOpenPersona?: (personaId: string) => void;
}) {
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
  const choices = data ? coworkerChoices(data.coworkers, t) : [];

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
        <UsageBody data={data} period={period} t={t} onOpenTurn={onOpenTurn} onOpenPersona={onOpenPersona} picked={!!persona} />
      )}
    </div>
  );
}

export function UsageBody({ data, period, t, onOpenTurn, onOpenPersona, picked = false }: {
  data: UsageData; period: Period; t: T; onOpenTurn?: (sessionId: string, turnId: string) => void;
  onOpenPersona?: (personaId: string) => void;
  /** a coworker is picked: everything narrows to it but By coworker (T3-11) */
  picked?: boolean;
}) {
  const days = useMemo(() => localDays(period), [period]);
  const all = sumAll(data.total);
  const prev = sumAll(data.prev);
  const prevStart = previousDays(days)[0][1];
  const canCompare = comparable(data.total.kept_from, prevStart);
  const pct = canCompare && prev ? changePct(all?.total ?? 0, prev.total) : null;
  const chart = purposeByDay(days, data.dayPurpose.groups);
  const overhead = overheadByDay(chart);
  // T3-15 (e): the default coworker is "Coworker (default)" in every list on the page
  const names = shownNames({ ...(data.coworkers.names || {}), ...(data.total.names || {}) }, t);
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
          <Bars rows={chart.rows} max={chart.max} series={SERIES} t={t} testid="usage-bars" />
          <DayLabels days={days} />
          <div className="p3-legend" data-testid="usage-legend">
            {SERIES.filter((s) => chart.legend[s] > 0).map((s) => (
              <span key={s}><i className={`p3-s-${s}`} />{t(`p3.series.${s}`)} {formatTokens(chart.legend[s])}</span>
            ))}
          </div>
          {overhead && (
            <div className="mt-3" data-testid="usage-overhead">
              <b className="p3-h">{t("p3.usage.overhead")}</b>
              <div className="text-meta text-faint">{t("p3.usage.overhead_sub", { tokens: formatTokens(overhead.max) })}</div>
              <Bars rows={overhead.rows} max={overhead.max} series={overhead.series} t={t} small testid="usage-overhead-bars" />
              <DayLabels days={days} />
            </div>
          )}
          <div className="text-meta text-faint mt-2">{t("p3.usage.point_hint")}</div>
        </div>
        <div className={CARD + " p3-card"}>
          <b className="p3-h">{t("p3.usage.by_coworker")}</b>
          {picked && <div className="text-meta text-faint" data-testid="usage-coworkers-all">{t("p3.usage.by_coworker_all")}</div>}
          <table className="p3-t" data-testid="usage-coworkers">
            <thead><tr><th>{t("p3.usage.coworker")}</th><th className="n">{t("p3.usage.tokens")}</th><th className="n">{t("p3.usage.with_issues")}</th></tr></thead>
            <tbody>
              {coworkerRows.map((r) => (
                <tr key={r.key}><td>
                  {r.personaId && onOpenPersona ? (
                    <button className="p3-link" title={t("p3.usage.open_history", { name: r.name })}
                      data-testid={`usage-coworker-open-${r.personaId}`} onClick={() => onOpenPersona(r.personaId!)}>{r.name}</button>
                  ) : r.name}
                  {r.automation && <span className="text-faint"> {t("p3.usage.automation")}</span>}</td>
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
          // T3-5: fixed column widths, so long titles, model names and badges are cut inside
          // their cell (full text on hover) and the table never grows past its card.
          <table className="p3-t p3-heavy" data-testid="usage-heaviest">
            <colgroup>
              <col style={{ width: "25%" }} /><col style={{ width: "12%" }} /><col style={{ width: "16%" }} />
              <col style={{ width: "8%" }} /><col style={{ width: "8%" }} /><col style={{ width: "25%" }} /><col style={{ width: "6%" }} />
            </colgroup>
            <thead><tr><th>{t("p3.usage.session")}</th><th>{t("p3.usage.coworker")}</th><th>{t("p3.usage.model")}</th>
              <th className="n">{t("p3.usage.tokens")}</th>
              <th className="n" title={t("p3.usage.active_time")}>{t("p3.usage.active_time_short")}</th>
              <th>{t("p3.usage.issues")}</th><th></th></tr></thead>
            <tbody>
              {data.heavy.map((h) => {
                const title = h.session_deleted ? t("p3.usage.deleted_session") : (h.title || h.session_id);
                const turn = h.seq != null ? t("p3.usage.turn_n", { n: h.seq }) : "";
                const who = (h.persona_id && names[h.persona_id]) || h.persona_id || "—";
                const models = h.model_ids.join(", ") || "—";
                const shortModels = h.model_ids.map(shortModel).join(", ") || "—";
                const issues = h.checks.map((c) => t(`steps.issues.title.${c}`, { defaultValue: c }));
                return (
                <tr key={h.turn_id}>
                  <td><span className="p3-titleturn" title={turn ? `${title} · ${turn}` : title}>
                    {/* the title is cut, never the turn number after it */}
                    <span className={"p3-cut" + (h.session_deleted ? " text-faint" : "")}>{title}</span>
                    {turn && <span className="text-faint p3-keep" data-testid="usage-heavy-turn">&nbsp;· {turn}</span>}</span></td>
                  <td><span className="p3-cut" title={who}>{who}</span></td>
                  <td><span className="p3-cut font-mono" title={models} data-testid="usage-heavy-model">{shortModels}</span></td>
                  <td className="n">{(h.at_least ? "≥ " : "") + formatTokens(h.total)}</td>
                  <td className="n">{h.found_open ? <span className="text-faint p3-wrap">{t("p3.usage.found_open")}</span> : formatDuration(h.active_ms)}</td>
                  <td>{issues.length > 0 && (
                    <span className="p3-badges" title={issues.join(", ")} data-testid="usage-heavy-issues">
                      <span className="steps-badge warn">{issues[0]}</span>
                      {issues.length > 1 && <span className="steps-badge warn">+{issues.length - 1}</span>}
                    </span>
                  )}</td>
                  <td>{!h.session_deleted && onOpenTurn && (
                    <button className="p3-link" data-testid="usage-open-steps" onClick={() => onOpenTurn(h.session_id, h.turn_id)}>{t("steps.open")}</button>
                  )}</td>
                </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>

      {ap && ap.shown && <ApproverStripView ap={ap} t={t} />}

      {data.ratings && <RatingsCard r={data.ratings} names={names} t={t} onOpenTurn={onOpenTurn} />}

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

/** The model's own name without its provider and lab ("openrouter:deepseek/deepseek-v4-pro"
 * → "deepseek-v4-pro"); the cell's hover keeps the full id (T3-5). */
export function shortModel(id: string): string {
  if (id.includes("/")) return id.slice(id.lastIndexOf("/") + 1);
  // only the provider prefix goes: "ollama:qwen3:0.6b" keeps its size tag
  const i = id.indexOf(":");
  return i >= 0 ? id.slice(i + 1) : id;
}

type ChartRow = { label: string; values: Record<string, number>; total: number };

function Bars({ rows, max, series, t, small, testid }: { rows: ChartRow[]; max: number; series: readonly string[]; t: T; small?: boolean; testid: string }) {
  return (
    <div className={"p3-bars" + (small ? " small" : "")} data-testid={testid}>
      {rows.map((r) => (
        <div key={r.label} className="p3-bar" title={`${r.label}: ${formatTokens(r.total)}`}>
          {series.map((s) =>
            r.values[s] ? (
              <i key={s} className={`p3-s-${s}`} style={{ height: `${Math.max(1, (r.values[s] / max) * 100)}%` }}
                title={`${t(`p3.series.${s}`)}: ${r.values[s].toLocaleString()}`} />
            ) : null,
          )}
        </div>
      ))}
    </div>
  );
}

function DayLabels({ days }: { days: [string, number, number][] }) {
  if (days.length <= 1) return null;
  return (
    <div className="p3-xl">
      {days.map(([label, start]) => (
        <span key={label}>{days.length <= 7 ? new Date(start).toLocaleDateString(undefined, { weekday: "short", day: "2-digit" }) : new Date(start).getDate()}</span>
      ))}
    </div>
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
  const rows = new Map<string, { key: string; personaId: string | null; name: string; automation: boolean; total: number; atLeast: boolean; checked: number; withWarn: number }>();
  for (const g of groups) {
    const pid = g.key.persona_id as string | null;
    const auto = g.key.task_id != null;
    const key = `${pid ?? ""}|${auto ? "a" : ""}`;
    const r = rows.get(key) || { key, personaId: pid, name: pid ? names[pid] || pid : t("p3.usage.unknown_coworker"), automation: auto, total: 0, atLeast: false, checked: 0, withWarn: 0 };
    r.total += g.total;
    r.atLeast = r.atLeast || g.unreported > 0;
    r.checked += g.checked;
    r.withWarn += g.with_warn;
    rows.set(key, r);
  }
  return [...rows.values()].sort((a, b) => b.total - a.total);
}

/** T3-10 (lean): the period's 👍 and 👎, how often each reason was picked, and the turns rated
 * 👎 with their note, each opening its trace. No charts or trends: those stay with Langfuse. */
export function RatingsCard({ r, names, t, onOpenTurn }: {
  r: RatingsSummary; names: Record<string, string>; t: T; onOpenTurn?: (sessionId: string, turnId: string) => void;
}) {
  const reasonLabel = (k: string) => t(`p3.fb.reason.${k}`, { defaultValue: k });
  const picked = [...REASONS.filter((k) => r.reasons[k]), ...Object.keys(r.reasons).filter((k) => !(REASONS as readonly string[]).includes(k))];
  return (
    <div className={CARD + " p3-card mt-3"} data-testid="usage-ratings">
      <b className="p3-h">{t("p3.ratings.title")}</b>
      {r.up + r.down === 0 ? (
        <div className="text-meta text-faint mt-1">{t("p3.ratings.none")}</div>
      ) : (
        <>
          <div className="flex items-center gap-4 mt-1 text-ui" data-testid="usage-ratings-counts">
            <span>👍 {r.up}</span>
            <span>👎 {r.down}</span>
          </div>
          {picked.length > 0 && (
            <div className="flex gap-1.5 flex-wrap mt-2 items-center text-meta" data-testid="usage-ratings-reasons">
              <span className="text-faint">{t("p3.ratings.reasons")}</span>
              {picked.map((k) => (
                <span key={k} className="p3-chip">{reasonLabel(k)} {r.reasons[k]}</span>
              ))}
            </div>
          )}
          {r.down_turns.length > 0 && (
            <table className="p3-t p3-heavy mt-2" data-testid="usage-ratings-down">
              <colgroup>
                <col style={{ width: "26%" }} /><col style={{ width: "14%" }} /><col style={{ width: "22%" }} />
                <col style={{ width: "24%" }} /><col style={{ width: "7%" }} /><col style={{ width: "7%" }} />
              </colgroup>
              <thead><tr><th>{t("p3.usage.session")}</th><th>{t("p3.usage.coworker")}</th><th>{t("p3.ratings.col_reasons")}</th>
                <th>{t("p3.ratings.col_note")}</th><th className="n" title={t("p3.ratings.auto_title")}>{t("p3.ratings.col_auto")}</th><th></th></tr></thead>
              <tbody>
                {r.down_turns.map((d) => {
                  const title = d.session_deleted ? t("p3.usage.deleted_session") : (d.title || d.session_id);
                  const turn = d.seq != null ? t("p3.usage.turn_n", { n: d.seq }) : "";
                  const who = (d.persona_id && names[d.persona_id]) || d.persona_id || "—";
                  const reasons = d.reasons.map(reasonLabel).join(", ");
                  return (
                    <tr key={d.turn_id}>
                      <td><span className="p3-titleturn" title={turn ? `${title} · ${turn}` : title}>
                        <span className={"p3-cut" + (d.session_deleted ? " text-faint" : "")}>{title}</span>
                        {turn && <span className="text-faint p3-keep">&nbsp;· {turn}</span>}</span></td>
                      <td><span className="p3-cut" title={who}>{who}</span></td>
                      <td><span className="p3-cut" title={reasons}>{reasons || <span className="text-faint">—</span>}</span></td>
                      <td><span className="p3-cut" title={d.note || ""} data-testid="usage-ratings-note">{d.note || <span className="text-faint">—</span>}</span></td>
                      <td className="n">{d.auto_approved || "—"}</td>
                      <td>{!d.session_deleted && onOpenTurn && (
                        <button className="p3-link" data-testid="usage-ratings-open" onClick={() => onOpenTurn(d.session_id, d.turn_id)}>{t("steps.open")}</button>
                      )}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
          {r.more > 0 && <div className="text-meta text-faint mt-1">{t("p3.ratings.more", { count: r.more })}</div>}
        </>
      )}
    </div>
  );
}

/** T3-6: one row per reviewer, named in full, and a Total row; the point is comparing
 * reviewers, so they stay apart. Under 10 checks a cell is a count, from 10 a share (M3-R5). */
export function ApproverStripView({ ap, t }: { ap: ApproverStrip; t: T }) {
  const rows = ap.by_reviewer.length ? ap.by_reviewer : [{ ...ap, kind: "unknown", tokens: undefined }];
  const part = (n: number, checks: number) => {
    const p = approverPart(n, checks);
    return p.share ? t("p3.ap.share", { n: p.value }) : String(p.value);
  };
  const cells = (l: { checks: number; allowed: number; asked: number; blocked: number; could_not_run: number; median_ms: number | null },
                 overridden: number | undefined, tokens: number | undefined) => (
    <>
      <td className="n">{l.checks}</td>
      <td className="n c-ok">{part(l.allowed, l.checks)}</td>
      <td className="n c-warn">{part(l.asked, l.checks)}</td>
      <td className="n c-err">{part(l.blocked, l.checks)}</td>
      <td className="n">{l.could_not_run}</td>
      <td className="n">{overridden ?? "—"}</td>
      <td className="n">{formatDuration(l.median_ms)}</td>
      <td className="n">{tokens == null ? "—" : formatTokens(tokens)}</td>
    </>
  );
  const anyShare = rows.some((r) => r.checks >= 10) || ap.checks >= 10;
  return (
    <div className={CARD + " p3-card mt-3"} data-testid="usage-approver">
      <b className="p3-h">{t("p3.ap.title")}</b>
      <table className="p3-t p3-ap" data-testid="usage-approver-table">
        <thead>
          <tr>
            <th>{t("p3.ap.col.reviewer")}</th>
            {(["checks", "allowed", "asked", "blocked", "could_not_run", "overridden", "median", "tokens"] as const).map((c) => (
              <th key={c} className="n">{t(`p3.ap.col.${c}`)}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((l) => (
            <tr key={l.kind} data-testid={`usage-approver-${l.kind}`}>
              <td>{t(`p3.ap.name.${l.kind}`, { defaultValue: l.kind })}</td>
              {cells(l, l.overridden, l.tokens)}
            </tr>
          ))}
          <tr className="p3-total" data-testid="usage-approver-total">
            <td>
              <b>{t("p3.ap.total")}</b>
              {ap.shadow_checks > 0 && <div className="text-faint">{t("p3.ap.incl_shadow", { count: ap.shadow_checks })}</div>}
            </td>
            {cells(ap, ap.overridden, ap.tokens)}
          </tr>
        </tbody>
      </table>
      {anyShare && <div className="text-meta text-faint mt-1">{t("p3.ap.shares_note")}</div>}
    </div>
  );
}
