// Phase 3 helpers: the viewer's day boundaries (DST included), the chart's series, the
// share-vs-count rule, the footer's parts and how an answer finds its turn.
import { afterEach, describe, expect, it } from "vitest";
import {
  approverPart,
  comparable,
  footerParts,
  healthTone,
  localDays,
  previousDays,
  purposeByDay,
  seriesOf,
  turnForAnswer,
  wakeKey,
  whereKey,
  type TurnFooter,
} from "./obs3";

// Node reads TZ again whenever it changes, so a test can view the days from London.
const env = (globalThis as any).process.env as Record<string, string | undefined>;
const TZ = env.TZ;
afterEach(() => {
  env.TZ = TZ;
});

describe("localDays", () => {
  it("builds the viewer's local days from local midnights, 25 Oct 2026 being 25 hours in London", () => {
    env.TZ = "Europe/London";
    const days = localDays("30d", new Date(2026, 10, 4, 15, 0));
    expect(days).toHaveLength(30);
    expect(days[0][0]).toBe("2026-10-06");
    expect(days[29][0]).toBe("2026-11-04");
    const dst = days.find((d) => d[0] === "2026-10-25")!;
    expect((dst[2] - dst[1]) / 3_600_000).toBe(25);
    // contiguous: each day starts where the one before ended
    for (let i = 1; i < days.length; i++) expect(days[i][1]).toBe(days[i - 1][2]);
    // matches golden/phase3 vectors/days-europe-london.json for its first day
    expect(days[0][1]).toBe(Date.UTC(2026, 9, 5, 23, 0));
  });

  it("today is one day; the previous period is the same length just before", () => {
    env.TZ = "Asia/Kolkata";
    const now = new Date(2026, 10, 4, 9, 30);
    const today = localDays("today", now);
    expect(today).toHaveLength(1);
    expect(today[0][2] - today[0][1]).toBe(86_400_000);
    const week = localDays("7d", now);
    const prev = previousDays(week);
    expect(prev).toHaveLength(7);
    expect(prev[6][2]).toBe(week[0][1]);
  });

  it("compares with the last period only when the ledger reaches back over all of it (TD3-7)", () => {
    expect(comparable(null, 1000)).toBe(false);
    expect(comparable(1500, 1000)).toBe(false);
    expect(comparable(1000, 1000)).toBe(true);
  });
});

describe("tokens per day, by purpose", () => {
  it("folds helpers into main work and adds a day's series up to its total", () => {
    expect([seriesOf("main"), seriesOf("helper"), seriesOf("summary"), seriesOf("auto_approver"), seriesOf("title"), seriesOf("explore"), seriesOf(null)])
      .toEqual(["main", "main", "summary", "auto_approver", "title", "other", "other"]);
    const days: [string, number, number][] = [["d1", 0, 10], ["d2", 10, 20]];
    const g = (day: string, purpose: string, total: number) => ({ key: { day, purpose }, total }) as any;
    const out = purposeByDay(days, [g("d1", "main", 100), g("d1", "helper", 20), g("d1", "title", 3), g("d2", "auto_approver", 7), g("d9", "main", 999)]);
    expect(out.rows[0].values).toMatchObject({ main: 120, title: 3 });
    expect(out.rows[0].total).toBe(123);
    expect(out.rows[1].total).toBe(7);
    expect(out.legend).toEqual({ main: 120, summary: 0, auto_approver: 7, title: 3, other: 0 });
  });
});

describe("the Auto-approve strip", () => {
  it("shows counts under 10 checks and shares from 10 (M3-R5)", () => {
    expect(approverPart(1, 1)).toEqual({ share: false, value: 1 });
    expect(approverPart(9, 9)).toEqual({ share: false, value: 9 });
    expect(approverPart(7, 10)).toEqual({ share: true, value: 70 });
  });
});

const foot = (over: Partial<TurnFooter> = {}): TurnFooter => ({
  turn_id: "t1", seq: 1, status: "completed", duration_ms: 14_000, total: 41_000, at_least: false, warn: 0,
  auto_approved: 0, asked: 0, ...over,
});

describe("the turn footer", () => {
  it("shows a part only when it is not 0 (M3-R7)", () => {
    expect(footerParts(foot()).map((p) => p.kind)).toEqual(["time", "tokens"]);
    expect(footerParts(foot({ warn: 1, auto_approved: 1 })).map((p) => p.kind)).toEqual(["time", "tokens", "issues", "auto_approved"]);
    expect(footerParts(foot({ duration_ms: null, asked: 2 })).map((p) => p.kind)).toEqual(["tokens", "asked"]);
  });

  it("finds an answer's turn by its message, then its time, then its place", () => {
    const fs = [foot({ turn_id: "a", last_message_id: "m1", started_at: 1000_000, duration_ms: 5000 }),
      foot({ turn_id: "b", last_message_id: "m2", started_at: 1010_000, duration_ms: 5000 })];
    expect(turnForAnswer(fs, undefined, 0, "m2")?.turn_id).toBe("b");
    expect(turnForAnswer(fs, 1014, 0)?.turn_id).toBe("b");
    expect(turnForAnswer(fs, 1004, 1)?.turn_id).toBe("a");
    const noTimes = fs.map((f) => ({ ...f, started_at: undefined }));
    expect(turnForAnswer(noTimes, 1004, 1)?.turn_id).toBe("b");
    expect(turnForAnswer([], 1, 0)).toBeNull();
  });

  it("says where a rating went (§8.3)", () => {
    expect(whereKey(null).key).toBe("p3.fb.where_local");
    expect(whereKey({ local: true, export: null, managed: false, with_note: false }).key).toBe("p3.fb.where_local");
    expect(whereKey({ local: true, export: "langfuse", managed: false, with_note: true })).toEqual({ key: "p3.fb.where_langfuse", withNote: true });
    expect(whereKey({ local: true, export: "langfuse", managed: true, with_note: false }).key).toBe("p3.fb.where_company");
  });
});

describe("wakes and health", () => {
  it("names a gathered review wake a review batch", () => {
    expect(wakeKey({ reason: "review", batched: 3 })).toBe("p3.wake.review_batch");
    expect(wakeKey({ reason: "review", batched: null })).toBe("p3.wake.review");
    expect(wakeKey({ reason: "timer" })).toBe("p3.wake.timer");
    expect(wakeKey(null)).toBeNull();
  });

  it("colours a did-not-finish run as failed", () => {
    expect(healthTone("did_not_finish")).toBe("err");
    expect(healthTone("ok")).toBe("ok");
    expect(healthTone("skipped")).toBe("muted");
  });
});
