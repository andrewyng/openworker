// OB-8: the Usage tab's numbers, from fixed rollup answers (technical-design.md §7).
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import i18n from "i18next";
import { ApproverStripView, coworkerChoices, coworkerTable, loadUsage, RatingsCard, shortModel, UsageBody, type UsageData } from "./UsagePage";
import type { UsageRollup } from "../api";

afterEach(cleanup);

const t = i18n.t.bind(i18n) as any;
const group = (key: Record<string, any>, total: number, extra: Record<string, number> = {}) => ({
  key, total, input_fresh: 0, cache_read: 0, cache_write: 0, output: 0, calls: 1, unreported: 0, failed: 0,
  turns: 1, checked: 1, with_warn: 0, ...extra,
});
const rollup = (groups: any[], over: Partial<UsageRollup> = {}): UsageRollup =>
  ({ groups, no_turn: { calls: 0, total: 0 }, not_checked: 0, kept_from: null, ...over }) as UsageRollup;

function data(over: Partial<UsageData> = {}): UsageData {
  return {
    total: rollup([group({ all: "all" }, 848_910, { turns: 20, checked: 18, with_warn: 5, automation_turns: 2, median_active_ms: 14_000 })],
      { not_checked: 2, no_turn: { calls: 2, total: 54 }, kept_from: Date.now(), names: { cowork: "Cowork" } }),
    prev: null,
    dayPurpose: rollup([]),
    coworkers: rollup([group({ persona_id: "cowork", task_id: null }, 800_000, { checked: 10, with_warn: 3 }),
      group({ persona_id: "cowork", task_id: "task-1" }, 48_910)]),
    heavy: [
      { turn_id: "t5", session_id: "s1", title: "Run the watch", seq: 5, session_deleted: false, persona_id: "cowork",
        model_ids: ["model-x"], total: 146_000, at_least: false, active_ms: 22_000, status: "completed", found_open: false,
        warn: 1, checks: ["tool_failed"], started_at: 0 },
      { turn_id: "t2", session_id: "s1", title: "Run the watch", seq: 2, session_deleted: false, persona_id: "cowork",
        model_ids: ["model-x"], total: 75_000, at_least: false, active_ms: null, status: "unknown", found_open: true,
        warn: 0, checks: [], started_at: 0 },
      { turn_id: "t9", session_id: "gone", title: null, seq: null, session_deleted: true, persona_id: "cowork",
        model_ids: [], total: 1_000, at_least: true, active_ms: 1000, status: "completed", found_open: false,
        warn: null, checks: [], started_at: 0 },
    ],
    approver: null,
    machines: [
      { id: null, name: "", total: 848_910, at_least: false, state: "live" },
      { id: "m1", name: "build-box-1", total: 12_000, at_least: false, state: "cached", as_of: new Date(2026, 10, 4, 14, 2).getTime() },
      { id: "m2", name: "laptop-2", total: null, at_least: false, state: "offline" },
    ],
    ...over,
  };
}

describe("UsageBody", () => {
  it("shows the tiles without a comparison when no earlier period is kept (V3-11)", () => {
    render(<UsageBody data={data()} period="7d" t={t} />);
    expect(screen.getByTestId("usage-tile-tokens").textContent).toContain("no earlier week kept to compare");
    expect(screen.getByTestId("usage-tile-turns").textContent).toContain("2 automation turns");
    expect(screen.getByTestId("usage-tile-issues").textContent).toContain("28%");
    expect(screen.getByTestId("usage-tile-issues").textContent).toContain("2 not checked yet");
    expect(screen.getByTestId("usage-tile-median").textContent).toContain("14s");
    expect(screen.getByTestId("usage-no-turn").textContent).toContain("Not in a session: 2 calls · 54 tokens");
  });

  it("names each heaviest turn, says when it was found open, and gives a deleted session no link (M3-R2, M3-R3)", () => {
    const open = vi.fn();
    render(<UsageBody data={data()} period="7d" t={t} onOpenTurn={open} />);
    const table = screen.getByTestId("usage-heaviest").textContent || "";
    expect(table).toContain("turn 5");
    expect(table).toContain("found open after a crash");
    expect(table).toContain("deleted session");
    expect(table).toContain("A tool failed");
    const links = screen.getAllByTestId("usage-open-steps");
    expect(links).toHaveLength(2);
    fireEvent.click(links[0]);
    expect(open).toHaveBeenCalledWith("s1", "t5");
  });

  it("keeps the heaviest turns inside the card: fixed columns, +N for more badges, full names on hover (T3-5)", () => {
    const long = "openrouter:some-lab/a-very-long-model-name-v4-pro";
    const heavy = [{ ...data().heavy[0], model_ids: [long], checks: ["bad_tool_call", "tool_failed", "slow_tool"] }];
    render(<UsageBody data={data({ heavy })} period="7d" t={t} onOpenTurn={() => {}} />);
    const table = screen.getByTestId("usage-heaviest");
    expect(table.className).toContain("p3-heavy");
    expect(table.querySelectorAll("col")).toHaveLength(7);
    expect(screen.getByTestId("usage-heavy-model").getAttribute("title")).toBe(long);
    expect(screen.getByTestId("usage-heavy-model").textContent).toBe("a-very-long-model-name-v4-pro");
    // the turn number is its own piece, so the title is cut before it, never the number
    expect(screen.getByTestId("usage-heavy-turn").textContent).toContain("turn 5");
    // a short header, the long one on hover
    const th = [...table.querySelectorAll("th")].find((x) => x.textContent === "Active time")!;
    expect(th.getAttribute("title")).toBe("Time without waiting");
    const issues = screen.getByTestId("usage-heavy-issues");
    expect(issues.querySelectorAll(".steps-badge")).toHaveLength(2);
    expect(issues.textContent).toContain("+2");
    expect(issues.getAttribute("title")).toContain("A tool failed");
    expect(screen.getByTestId("usage-open-steps").textContent).toBe("Trace");
  });

  it("lists machines, an offline one by its cached answer's time", () => {
    render(<UsageBody data={data()} period="7d" t={t} />);
    const m = screen.getByTestId("usage-machines").textContent || "";
    expect(m).toContain("This machine");
    expect(m).toContain("offline · as of 14:02");
    expect(m).toContain("laptop-2");
  });

  it("names coworkers, with automations on their own row (M3-R1)", () => {
    const rows = coworkerTable(data().coworkers.groups as any, { cowork: "Cowork" }, t);
    expect(rows.map((r) => [r.name, r.automation, r.total])).toEqual([["Cowork", false, 800_000], ["Cowork", true, 48_910]]);
    render(<UsageBody data={data()} period="7d" t={t} />);
    expect(screen.getByTestId("usage-coworkers").textContent).toContain("(automation)");
    expect(screen.queryByTestId("usage-coworkers-all")).toBeNull();
  });

  it("with a coworker picked, says By coworker still lists them all (T3-11)", () => {
    render(<UsageBody data={data()} period="7d" t={t} picked />);
    expect(screen.getByTestId("usage-coworkers-all").textContent).toBe("All coworkers, for comparison");
  });

  it("each coworker's name in By coworker opens its history (T3-12)", () => {
    const open = vi.fn();
    render(<UsageBody data={data()} period="7d" t={t} onOpenPersona={open} />);
    const links = screen.getAllByTestId("usage-coworker-open-cowork");
    expect(links).toHaveLength(2); // its own row and its automation row
    fireEvent.click(links[0]);
    expect(open).toHaveBeenCalledWith("cowork");
  });
});

describe("shortModel (T3-5)", () => {
  it("drops the provider and the lab, keeps a size tag", () => {
    expect(shortModel("openrouter:deepseek/deepseek-v4-pro")).toBe("deepseek-v4-pro");
    expect(shortModel("openrouter:z-ai/glm-5.3")).toBe("glm-5.3");
    expect(shortModel("openai:gpt-5.6-sol")).toBe("gpt-5.6-sol");
    expect(shortModel("ollama:qwen3:0.6b")).toBe("qwen3:0.6b");
    expect(shortModel("model-x")).toBe("model-x");
  });
});

describe("ApproverStripView", () => {
  it("counts under 10 checks and says when shadow checks are in the tokens", () => {
    const ap = { checks: 1, allowed: 1, asked: 0, blocked: 0, could_not_run: 0, median_ms: 1000, overridden: 0,
      tokens: 3796, shadow_checks: 2, by_reviewer: [{ kind: "session_model", checks: 1, allowed: 1, asked: 0, blocked: 0, could_not_run: 0, median_ms: 1000 }], shown: true };
    render(<ApproverStripView ap={ap} t={t} />);
    const text = screen.getByTestId("usage-approver").textContent || "";
    expect(text).not.toContain("%");
    expect(screen.getByTestId("usage-approver-session_model").textContent).toContain("Session model");
    expect(screen.getByTestId("usage-approver-total").textContent).toContain("incl. 2 shadow checks");
    expect(screen.getByTestId("usage-approver-total").textContent).toContain("3.8k");
    expect(text).toContain("Auto-approve");
    expect(text).not.toContain("Safety reviewer");
  });

  it("one row per reviewer, named in full, with its own overrides and tokens, and a Total row (T3-6)", () => {
    const row = (kind: string, checks: number, allowed: number, asked: number, blocked: number, overridden: number, tokens: number) =>
      ({ kind, checks, allowed, asked, blocked, could_not_run: 0, median_ms: 1000, overridden, tokens });
    const ap = { checks: 98, allowed: 80, asked: 10, blocked: 8, could_not_run: 0, median_ms: 1000, overridden: 3,
      tokens: 457_000, shadow_checks: 2, shown: true,
      by_reviewer: [row("openai_decisions", 2, 1, 1, 0, 0, 0), row("session_model", 95, 79, 8, 8, 3, 450_000), row("typesafe_jev", 1, 0, 1, 0, 0, 0)] };
    render(<ApproverStripView ap={ap} t={t} />);
    const cells = (id: string) => [...screen.getByTestId(id).querySelectorAll("td")].map((c) => c.textContent);
    expect(cells("usage-approver-openai_decisions")[0]).toBe("Decision model · OpenAI Decisions");
    expect(cells("usage-approver-typesafe_jev")[0]).toBe("Decision model · Jev");
    // overridden is each reviewer's own, not the period's total on every line
    expect(cells("usage-approver-session_model")[6]).toBe("3");
    expect(cells("usage-approver-typesafe_jev")[6]).toBe("0");
    // 95 checks: shares; 2 checks: counts
    expect(cells("usage-approver-session_model")[2]).toBe("83%");
    expect(cells("usage-approver-openai_decisions")[2]).toBe("1");
    const total = cells("usage-approver-total");
    expect(total[0]).toContain("Total");
    expect(total[0]).toContain("incl. 2 shadow checks");
    expect(total[1]).toBe("98");
    expect(total[6]).toBe("3");
    expect(total[8]).toBe("457k");
  });
});

describe("one coworker", () => {
  it("lists each coworker of the period once, by name", () => {
    const r = rollup([group({ persona_id: "sec", task_id: null }, 5), group({ persona_id: "sec", task_id: "t1" }, 1),
      group({ persona_id: "cowork", task_id: null }, 3), group({ persona_id: null, task_id: null }, 1)],
      { names: { sec: "Security Coworker", cowork: "OpenWorker" } });
    expect(coworkerChoices(r)).toEqual([{ id: "cowork", name: "OpenWorker" }, { id: "sec", name: "Security Coworker" }]);
  });

  it("narrows every call but the coworker list to the picked coworker", async () => {
    const urls: string[] = [];
    const fetchMock = vi.fn(async (u: string) => {
      urls.push(String(u));
      const body = String(u).includes("/machines") ? { machines: [] } : String(u).includes("/turns") ? { turns: [] }
        : String(u).includes("/approver") ? { shown: false } : { groups: [], no_turn: { calls: 0, total: 0 }, not_checked: 0, kept_from: null };
      return { ok: true, json: async () => body } as any;
    });
    vi.stubGlobal("fetch", fetchMock);
    await loadUsage("7d", new Date(2026, 10, 4, 12), "sec");
    vi.unstubAllGlobals();
    const usage = urls.filter((u) => u.includes("/observability/"));
    const narrowed = usage.filter((u) => u.includes("persona_id=sec"));
    const list = usage.filter((u) => u.includes("group=persona_id%2Ctask_id"));
    expect(list).toHaveLength(1);
    expect(list[0]).not.toContain("persona_id=sec");
    expect(narrowed).toHaveLength(usage.length - 1);
  });
});

describe("RatingsCard (T3-10)", () => {
  const ratings = {
    up: 4, down: 2, reasons: { too_slow: 1, wrong_answer: 2, did_unasked: 1 }, more: 0,
    down_turns: [
      { turn_id: "t7", session_id: "s1", title: "Fix the build", seq: 7, session_deleted: false, persona_id: "cowork",
        started_at: 2, reasons: ["wrong_answer", "did_unasked"], note: "ran a command I didn't want", auto_approved: 2 },
      { turn_id: "t3", session_id: "gone", title: null, seq: null, session_deleted: true, persona_id: "cowork",
        started_at: 1, reasons: [], note: null, auto_approved: 0 },
    ],
  };

  it("counts the thumbs, the reasons in their fixed order, and lists the 👎 turns with their note", () => {
    const open = vi.fn();
    render(<RatingsCard r={ratings} names={{ cowork: "OpenWorker" }} t={t} onOpenTurn={open} />);
    expect(screen.getByTestId("usage-ratings-counts").textContent).toBe("👍 4👎 2");
    const reasons = screen.getByTestId("usage-ratings-reasons").textContent || "";
    expect(reasons.indexOf("Wrong answer 2")).toBeLessThan(reasons.indexOf("Took too long 1"));
    expect(reasons).toContain("Did something I didn't ask 1");
    const table = screen.getByTestId("usage-ratings-down").textContent || "";
    expect(table).toContain("Fix the build");
    expect(table).toContain("OpenWorker");
    expect(screen.getAllByTestId("usage-ratings-note")[0].textContent).toBe("ran a command I didn't want");
    // a deleted session's turn has no Trace link
    const links = screen.getAllByTestId("usage-ratings-open");
    expect(links).toHaveLength(1);
    expect(links[0].textContent).toBe("Trace");
    fireEvent.click(links[0]);
    expect(open).toHaveBeenCalledWith("s1", "t7");
  });

  it("says so when nothing was rated", () => {
    render(<RatingsCard r={{ up: 0, down: 0, reasons: {}, down_turns: [], more: 0 }} names={{}} t={t} />);
    expect(screen.getByTestId("usage-ratings").textContent).toContain("No turns of this period were rated.");
  });
});
