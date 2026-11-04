// OB-8: the Usage tab's numbers, from fixed rollup answers (technical-design.md §7).
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import i18n from "i18next";
import { ApproverStripView, coworkerChoices, coworkerTable, loadUsage, UsageBody, type UsageData } from "./UsagePage";
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
  });
});

describe("ApproverStripView", () => {
  it("counts under 10 checks and says when shadow checks are in the tokens", () => {
    const ap = { checks: 1, allowed: 1, asked: 0, blocked: 0, could_not_run: 0, median_ms: 1000, overridden: 0,
      tokens: 3796, shadow_checks: 2, by_reviewer: [{ kind: "session_model", checks: 1, allowed: 1, asked: 0, blocked: 0, could_not_run: 0, median_ms: 1000 }], shown: true };
    render(<ApproverStripView ap={ap} t={t} />);
    const text = screen.getByTestId("usage-approver").textContent || "";
    expect(text).toContain("allowed 1");
    expect(text).not.toContain("%");
    expect(text).toContain("session model");
    expect(text).toContain("incl. 2 shadow checks");
    expect(text).toContain("Auto-approve");
    expect(text).not.toContain("Safety reviewer");
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
