// Observability Phase 3 ("learn across runs"): the pure helpers the Usage tab, the turn
// footer, the coworker page, TeamView and the Scheduled view share. Kept out of the
// components so the rules can be tested on their own (technical-design.md §7 to §12).
// Tokens only: nothing here ever turns a count into money.

/** One day as the viewer sees it: `[label, start, end)` in epoch ms (TD3-2). */
export type DayBoundary = [string, number, number];
export type Period = "today" | "7d" | "30d";

const pad = (n: number) => String(n).padStart(2, "0");
const isoLocal = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;

/** The viewer's local days of a period, oldest first, built from local midnights, so a DST
 * day is simply 23 or 25 hours long. "Today" is one day; 7 and 30 days end today. */
export function localDays(period: Period, now: Date = new Date()): DayBoundary[] {
  const n = period === "today" ? 1 : period === "7d" ? 7 : 30;
  const out: DayBoundary[] = [];
  for (let i = n - 1; i >= 0; i--) {
    const a = new Date(now.getFullYear(), now.getMonth(), now.getDate() - i);
    const b = new Date(now.getFullYear(), now.getMonth(), now.getDate() - i + 1);
    out.push([isoLocal(a), a.getTime(), b.getTime()]);
  }
  return out;
}

/** The same number of local days just before `days` (for "vs last period"). */
export function previousDays(days: DayBoundary[]): DayBoundary[] {
  if (!days.length) return [];
  const first = new Date(days[0][1]);
  const n = days.length;
  const out: DayBoundary[] = [];
  for (let i = n; i >= 1; i--) {
    const a = new Date(first.getFullYear(), first.getMonth(), first.getDate() - i);
    const b = new Date(first.getFullYear(), first.getMonth(), first.getDate() - i + 1);
    out.push([isoLocal(a), a.getTime(), b.getTime()]);
  }
  return out;
}

/** "vs last period" only when the ledger reaches back over the whole earlier period (TD3-7,
 * V3-11): never a "▲ 100%" from a period that was not kept. */
export function comparable(keptFrom: number | null | undefined, prevStart: number): boolean {
  return keptFrom != null && keptFrom <= prevStart;
}

export function changePct(now: number, before: number): number | null {
  if (!before) return null;
  return Math.round(((now - before) / before) * 100);
}

// -- tokens per day, by purpose (§7.2, M3-R4) --------------------------------------------------
export const SERIES = ["main", "summary", "auto_approver", "title", "other"] as const;
export type Series = (typeof SERIES)[number];

/** The chart's five series: main work and helpers together, summaries, the auto-approver,
 * session titles, and everything else, so a day's series add up to that day's total. */
export function seriesOf(purpose: string | null | undefined): Series {
  switch (purpose) {
    case "main":
    case "helper":
      return "main";
    case "summary":
      return "summary";
    case "auto_approver":
      return "auto_approver";
    case "title":
      return "title";
    default:
      return "other";
  }
}

export interface RollupGroup {
  key: Record<string, any>;
  total: number;
  input_fresh: number;
  cache_read: number;
  cache_write: number;
  output: number;
  calls: number;
  unreported: number;
  failed: number;
  over_limit?: number;
  turns: number;
  checked: number;
  with_warn: number;
  automation_turns?: number;
  approvals?: number;
  median_active_ms?: number | null;
}

/** `day,purpose` groups -> per day, per series, plus each series' total for the legend. */
export function purposeByDay(days: DayBoundary[], groups: RollupGroup[]) {
  const byDay = new Map<string, Record<Series, number>>();
  for (const [label] of days) byDay.set(label, { main: 0, summary: 0, auto_approver: 0, title: 0, other: 0 });
  const legend: Record<Series, number> = { main: 0, summary: 0, auto_approver: 0, title: 0, other: 0 };
  for (const g of groups) {
    const row = byDay.get(String(g.key.day));
    if (!row) continue;
    const s = seriesOf(g.key.purpose);
    row[s] += g.total;
    legend[s] += g.total;
  }
  const rows = days.map(([label]) => ({ label, values: byDay.get(label)!, total: SERIES.reduce((n, s) => n + byDay.get(label)![s], 0) }));
  return { rows, legend, max: Math.max(1, ...rows.map((r) => r.total)) };
}

/** T3-7: the same days without main work, on their own scale, so summaries, the
 * auto-approver and titles get a readable size (main work is often 95% or more). Null when
 * the period has none of them. */
export function overheadByDay(chart: ReturnType<typeof purposeByDay>) {
  const series = SERIES.filter((s) => s !== "main");
  const rows = chart.rows.map((r) => ({ ...r, values: { ...r.values, main: 0 }, total: r.total - r.values.main }));
  const max = Math.max(0, ...rows.map((r) => r.total));
  return max > 0 ? { rows, max, series } : null;
}

// -- the Auto-approve strip (§7.3, M3-R5) -----------------------------------------------------
/** Under 10 checks a part is a count ("allowed 1"), from 10 a share ("allowed 72%"): "allowed
 * 100%" of one check says nothing. */
export function approverPart(n: number, checks: number): { share: boolean; value: number } {
  if (checks >= 10) return { share: true, value: Math.round((n / checks) * 100) };
  return { share: false, value: n };
}

// -- the turn footer (§8.2, M3-R6, M3-R7) -------------------------------------------------------
export interface TurnFooter {
  turn_id: string;
  seq: number | null;
  status: string;
  first_message_id?: string | null;
  last_message_id?: string | null;
  started_at?: number;
  duration_ms: number | null;
  active_ms?: number | null;
  total: number;
  at_least: boolean;
  warn: number | null;
  auto_approved: number;
  asked: number;
  live_checks?: number;
  wake?: { reason?: string; batched?: number | null } | null;
  feedback?: TurnFeedback | null;
}

export interface TurnFeedback {
  turn_id: string;
  session_id: string;
  rating: "up" | "down";
  reasons: string[];
  rated_at: number;
  changed_at: number | null;
  content?: { note: string | null };
}

export interface FeedbackWhere {
  local: boolean;
  export: string | null;
  managed: boolean;
  by?: string | null;
  with_note: boolean;
}

export type FooterPart =
  | { kind: "time"; ms: number }
  | { kind: "tokens"; total: number; at_least: boolean }
  | { kind: "issues"; n: number }
  | { kind: "auto_approved"; n: number }
  | { kind: "asked"; n: number };

/** The footer's parts, each only when it says something: no "0 issues", no "0 asked you". */
export function footerParts(f: TurnFooter): FooterPart[] {
  const out: FooterPart[] = [];
  if (f.duration_ms != null) out.push({ kind: "time", ms: f.duration_ms });
  out.push({ kind: "tokens", total: f.total, at_least: f.at_least });
  if (f.warn) out.push({ kind: "issues", n: f.warn });
  if (f.auto_approved) out.push({ kind: "auto_approved", n: f.auto_approved });
  if (f.asked) out.push({ kind: "asked", n: f.asked });
  return out;
}

/** The turn an answer belongs to: the turn whose last message is the answer (`mid`); else by
 * the answer's time (unix seconds), the first turn not yet over a second before it, as the
 * Steps link picks it; else the answer's position among the answers. */
export function turnForAnswer(footers: TurnFooter[], ts: number | undefined, answerIndex: number, mid?: string): TurnFooter | null {
  if (!footers.length) return null;
  if (mid) {
    const byMid = footers.find((f) => f.last_message_id === mid);
    if (byMid) return byMid;
  }
  if (ts != null && footers.every((f) => f.started_at != null)) {
    const at = ts * 1000 - 1000;
    const f = footers.find((x) => x.duration_ms == null || (x.started_at as number) + x.duration_ms >= at);
    return f ?? footers[footers.length - 1];
  }
  return footers[Math.min(answerIndex, footers.length - 1)] ?? null;
}

export const REASONS = ["wrong_answer", "too_slow", "did_unasked", "did_not_finish", "other"] as const;

/** The rating card's last line: where the rating went (§8.3). */
export function whereKey(w: FeedbackWhere | null | undefined, future = false): { key: string; withNote: boolean } {
  // T3-8: before Send or Skip nothing is saved yet, so the open card says where it *will* go.
  const pre = future ? "p3.fb.will_" : "p3.fb.where_";
  if (!w || !w.export) return { key: pre + "local", withNote: false };
  if (w.managed) return { key: pre + "company", withNote: !future && !!w.with_note };
  return { key: pre + (w.export === "langfuse" ? "langfuse" : "otlp"), withNote: !future && !!w.with_note };
}

// -- wake reasons (OB-17) ----------------------------------------------------------------------
/** The i18n key of a wake reason; a review wake that the 2 s window gathered is a "review batch". */
export function wakeKey(wake: { reason?: string; batched?: number | null } | null | undefined): string | null {
  const r = wake?.reason;
  if (!r) return null;
  if (r === "review" && wake?.batched) return "p3.wake.review_batch";
  return `p3.wake.${r}`;
}

export function cacheShare(cacheRead: number, input: number): number | null {
  return input > 0 ? Math.round((cacheRead / input) * 100) : null;
}

// -- coverage (OB-16) -----------------------------------------------------------------------------
export const AREA_WORD: Record<string, string> = { code: "code", secrets: "secret", deps: "dependency", iac: "infrastructure" };

// -- run health (OB-18) -----------------------------------------------------------------------------
export type Health = "ok" | "failed" | "skipped" | "did_not_finish" | "unknown";
export function healthTone(h: string | undefined): "ok" | "err" | "muted" {
  if (h === "ok") return "ok";
  if (h === "failed" || h === "did_not_finish") return "err";
  return "muted";
}

/** Compact time-of-day for "as of 14:02". */
export function hhmm(ms: number): string {
  const d = new Date(ms);
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** "21 Oct" */
export function shortDate(ms: number): string {
  return new Date(ms).toLocaleDateString(undefined, { day: "2-digit", month: "short" });
}
