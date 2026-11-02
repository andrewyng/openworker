// Observability Phase 1 (UX-059 OB-1, OB-2, OB-4): the Steps panel reads the recording.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { getRecordedTurns, getRecordedUsage, getStepDetail, getTurnSteps } from "../api";
import { StepsPanel, formatDuration } from "./StepsPanel";

vi.mock("../api", () => ({
  getRecordedTurns: vi.fn(),
  getTurnSteps: vi.fn(),
  getStepDetail: vi.fn(),
  getRecordedUsage: vi.fn(),
}));

const turn = {
  turn_id: "t1", seq: 1, kind: "run", trigger: "user", status: "completed", started_at: 1000,
  duration_ms: 10_000, stamp: {},
  buckets: { total: 10_000, waiting: 4000, tools: 2000, model: 3000, auto_approver: 0, summary: 0, other: 1000, other_shown: true },
  usage: { pill: 2077 },
};

const step = (over: Record<string, any>) => ({
  step_id: "s", turn_id: "t1", parent_step_id: null, seq: 1, kind: "tool", name: "read_file",
  started_at: 1000, duration_ms: 100, status: "ok", call_id: null, message_id: null, shadow: false,
  after_turn: false, attrs: {}, ...over,
});

const steps = [
  step({ step_id: "c", kind: "context", name: "context", duration_ms: 0 }),
  step({ step_id: "m1", kind: "model", name: "model", duration_ms: 3000,
         attrs: { purpose: "main", usage: { reported: true, input_fresh: 100, cache_read: 900, cache_write: null, output: 10, reasoning: null } } }),
  step({ step_id: "a1", kind: "auto_approver", name: "run_shell", started_at: 4000, attrs: { result: "allow", mode: "live" } }),
  step({ step_id: "w1", kind: "wait", name: "approval", started_at: 4100, duration_ms: 4000, attrs: { kind: "approval", where: "session", outcome: "approved" } }),
  step({ step_id: "x1", kind: "tool", name: "run_shell", started_at: 8100, duration_ms: 2000, status: "error" }),
  step({ step_id: "h1", kind: "model", name: "model", parent_step_id: "x1", started_at: 8200, attrs: { purpose: "helper" } }),
];

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("StepsPanel", () => {
  it("shows where the time went, every step on one axis, and nested helper steps", async () => {
    vi.mocked(getRecordedTurns).mockResolvedValue([turn] as any);
    vi.mocked(getTurnSteps).mockResolvedValue(steps as any);
    render(<StepsPanel sessionId="s1" onClose={() => {}} />);

    await screen.findByText("Turn 1 of 1");
    const tiles = screen.getByTestId("steps-tiles").textContent || "";
    // Total, every bucket that is not zero, and Other (it is 10% here).
    expect(tiles).toContain("10s");
    expect(tiles).toContain("Waiting for you");
    expect(tiles).toContain("Other");
    expect(tiles).not.toContain("Auto-approver");
    await waitFor(() => expect(screen.getAllByTestId("steps-row")).toHaveLength(6));
    const labels = screen.getAllByTestId("steps-row").map((r) => r.textContent);
    expect(labels[0]).toContain("Context built");
    expect(labels[2]).toContain("Auto-approver · allowed");
    expect(labels[3]).toContain("Waiting for you · approval");
    expect(labels[4]).toContain("error");
    expect(labels[5]).toContain("Model · helper"); // right under its explore tool step
    expect(screen.getByTestId("steps-checks").textContent).toContain("1 check · 1 allowed");
  });

  it("opens a step's details with its tokens", async () => {
    vi.mocked(getRecordedTurns).mockResolvedValue([turn] as any);
    vi.mocked(getTurnSteps).mockResolvedValue(steps as any);
    vi.mocked(getStepDetail).mockResolvedValue({ ...steps[1], attrs: { ...steps[1].attrs, model: "openai:model-a", finish_reason: "tool_calls" } });
    render(<StepsPanel sessionId="s1" onClose={() => {}} />);
    await waitFor(() => expect(screen.getAllByTestId("steps-row")).toHaveLength(6));
    fireEvent.click(screen.getAllByTestId("steps-row")[1]);
    const tokens = await screen.findByTestId("steps-tokens");
    expect(tokens.textContent).toContain("from cache 900");
    expect(tokens.textContent).toContain("written to cache —");  // not reported is not 0
    expect(screen.getByTestId("steps-details").textContent).toContain("tool_calls");
  });

  it("shows the session's usage by purpose and model", async () => {
    vi.mocked(getRecordedTurns).mockResolvedValue([turn] as any);
    vi.mocked(getTurnSteps).mockResolvedValue(steps as any);
    vi.mocked(getRecordedUsage).mockResolvedValue({
      pill: 2077, at_least: true, unreported_calls: 1, failed_calls: 0, overflow_calls: 0, cache_share: 0.94,
      by_purpose: { main: 2035, title: 42 }, totals: { input: 2060, cache: 1900, output: 17 },
      by_model_parts: { "openai:model-a": { input: 2020, cache: 1900, output: 15 } },
      context: { tokens: 1020, window: 200000 },
    });
    render(<StepsPanel sessionId="s1" onClose={() => {}} />);
    fireEvent.click(await screen.findByRole("tab", { name: "Usage" }));
    const usage = await screen.findByTestId("steps-usage");
    expect(usage.textContent).toContain("≥ 2.1k");
    expect(usage.textContent).toContain("94%");
    expect(usage.textContent).toContain("Main work");
    expect(usage.textContent).toContain("Session title");
    expect(usage.textContent).toContain("1 call did not report usage");
  });

  it("says when a session has no recorded steps", async () => {
    vi.mocked(getRecordedTurns).mockResolvedValue([]);
    render(<StepsPanel sessionId="old" onClose={() => {}} />);
    expect((await screen.findByTestId("steps-none")).textContent).toContain("No recorded steps yet");
  });
});

describe("formatDuration", () => {
  it("reads like the mock", () => {
    expect(formatDuration(100)).toBe("0.1s");
    expect(formatDuration(3400)).toBe("3.4s");
    expect(formatDuration(41_000)).toBe("41s");
    expect(formatDuration(134_000)).toBe("2m 14s");
    expect(formatDuration(null)).toBe("—");
  });
});
