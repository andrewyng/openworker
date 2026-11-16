// OB-16, OB-17: TeamView's wake split and events, a task's coverage and a finding's evidence.
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import i18n from "i18next";
import type { TeamWake } from "../teamView";
import { CoverageBlock, eventText, exitText, foundSomething, TeamEvents, WakeSplit } from "./TeamPhase3";

afterEach(cleanup);
const t = i18n.t.bind(i18n) as any;

describe("WakeSplit", () => {
  it("gives every reason its tokens, workers included, and adds up to the team total (V3-09)", () => {
    const wakes: TeamWake[] = [
      { reason: "timer", wakes: 11, total: 380_000, cache_read: 334_394, input: 368_593, by_role: { lead: { wakes: 11, total: 380_000 } } },
      { reason: "assigned", wakes: 20, total: 300_000, cache_read: 180_000, input: 285_000, by_role: { worker: { wakes: 20, total: 300_000 } } },
      { reason: "review", wakes: 9, total: 320_000, cache_read: 227_199, input: 310_397, by_role: { lead: { wakes: 9, total: 320_000 } } },
    ];
    render(<WakeSplit wakes={wakes} explained={{ turns: 58, explained: 57 }} t={t} />);
    const text = screen.getByTestId("team-wake-split").textContent || "";
    expect(text).toContain("Timer (sleep_for)");
    expect(text).toContain("Assigned work");
    expect(screen.getByTestId("team-wake-timer").textContent).toContain("91%");
    expect(text).toContain("Team total");
    expect(text).toContain("40");  // 11 + 20 + 9 wakes
    expect(screen.getByTestId("team-wake-explained").textContent).toContain("57 of 58 turns have a known reason (98%)");
  });
});

describe("TeamEvents", () => {
  it("words each event and opens its turn's Steps; a cap hit with no turn opens the board", () => {
    const steps = vi.fn();
    const board = vi.fn();
    const events = [
      { kind: "wake_capped", severity: "warn" as const, at: 2, session_id: "lead", turn_id: "t12", deferred_ms: 360_000 },
      { kind: "lead_polling", severity: "warn" as const, at: 1, session_id: "lead", turn_id: "t9", count: 9 },
      { kind: "wake_cap_reached", severity: "warn" as const, at: 0, count: 60, held: ["w1"] },
    ];
    render(<TeamEvents events={events} t={t} onOpenSteps={steps} onOpenBoard={board} />);
    expect(screen.getByTestId("team-event-wake_capped").textContent).toContain("this wake waited 6 min");
    expect(screen.getByTestId("team-event-lead_polling").textContent).toContain("9 times with no board change");
    fireEvent.click(screen.getByTestId("team-event-lead_polling"));
    expect(steps).toHaveBeenCalledWith("lead", "t9");
    fireEvent.click(screen.getByTestId("team-event-wake_cap_reached"));
    expect(board).toHaveBeenCalled();
    expect(eventText({ kind: "same_approval_twice", severity: "warn", at: null, tool: "run_shell", first_by: "lead", again_by: "maya" }, t))
      .toBe("Same approval asked twice: run_shell (lead, then maya)");
  });
});

describe("CoverageBlock", () => {
  it("says a scanner's 'found something' exit, and the area that was never scanned (M3-R9, M3-R11)", () => {
    expect(exitText({ exit_code: 1, found_something: true }, t)).toBe("exit 1 (found something)");
    expect(exitText({ exit_code: 126, found_something: false }, t)).toBe("exit 126");
    render(<CoverageBlock t={t} coverage={{
      promised: ["code", "secrets"],
      scans: [{ scanner: "semgrep", area: "code", exit_code: 1, ok: true, found_something: true, turn_seq: 3, step_seq: 9, actor: "Maya" }],
      issues: [{ check: "no_findings_without_scan", text: "The report says no secret problems were found, but no secret scan ran." }],
    }} />);
    const text = screen.getByTestId("task-coverage").textContent || "";
    expect(text).toContain("semgrep ran · exit 1 (found something)");
    expect(text).toContain("Maya turn 3, step 9");
    expect(text).toContain("no secret scan ran");
    expect(text).toContain("no secret problems were found");
  });
});

describe("foundSomething", () => {
  it("reads each scanner's own codes: gitleaks exit 1 found something, trivy exit 1 failed (M3-R9)", () => {
    const codes = { gitleaks: [1], "npm-audit": [1], trivy: [] };
    expect(foundSomething("gitleaks", 1, codes)).toBe(true);
    expect(foundSomething("trivy", 1, codes)).toBe(false);
    expect(foundSomething("gitleaks", 126, codes)).toBe(false);
    expect(foundSomething("gitleaks", 0, codes)).toBe(false);
    expect(foundSomething(undefined, 1, codes)).toBe(false);
    expect(foundSomething("gitleaks", 1, undefined)).toBe(false);
  });
});
