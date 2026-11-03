// Observability Phase 1 (UX-059 OB-1, OB-2, OB-4): the Steps panel reads the recording.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { getRecordedTurns, getRecordedUsage, getStepDetail, getTurnIssues, getTurnSteps, markIssuesSeen } from "../api";
import { StepsPanel, formatDuration } from "./StepsPanel";
import i18n from "i18next";
import zh from "../locales/zh.json";

vi.mock("../api", () => ({
  getRecordedTurns: vi.fn(),
  getTurnSteps: vi.fn(),
  getStepDetail: vi.fn(),
  getRecordedUsage: vi.fn(),
  getTurnIssues: vi.fn(() => Promise.resolve(null)),
  markIssuesSeen: vi.fn(() => Promise.resolve()),
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
    expect(screen.getByTestId("steps-details").textContent).toContain("stopped because it asked for a tool");
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

  it("closes a step's details when another turn is picked", async () => {
    const turn2 = { ...turn, turn_id: "t2", seq: 2 };
    vi.mocked(getRecordedTurns).mockResolvedValue([turn, turn2] as any);
    vi.mocked(getTurnSteps).mockResolvedValue(steps as any);
    vi.mocked(getStepDetail).mockResolvedValue(steps[1]);
    render(<StepsPanel sessionId="s1" onClose={() => {}} />);
    await screen.findByText("Turn 2 of 2");
    await waitFor(() => expect(screen.getAllByTestId("steps-row")).toHaveLength(6));
    fireEvent.click(screen.getAllByTestId("steps-row")[1]);
    await screen.findByTestId("steps-details");
    fireEvent.click(screen.getByRole("button", { name: "Previous turn" }));
    await screen.findByText("Turn 1 of 2");
    expect(screen.queryByTestId("steps-details")).toBeNull();
  });

  it("badges rows the way the mock does", async () => {
    // Round-2 in-app check: a failed shell command, a failed or unreported model call, a
    // shadow check, an Inbox wait and an auto-approved action each say so on their row.
    vi.mocked(getRecordedTurns).mockResolvedValue([turn] as any);
    vi.mocked(getTurnSteps).mockResolvedValue([
      step({ step_id: "e1", kind: "tool", name: "run_shell", attrs: { exit_code: 3 } }),
      step({ step_id: "e2", kind: "model", name: "model", status: "error", attrs: { purpose: "title", error: { type: "provider_error" }, usage: { reported: false } } }),
      step({ step_id: "e3", kind: "model", name: "model", status: "error", attrs: { purpose: "main", error: { type: "context_length" }, usage: { reported: false } } }),
      step({ step_id: "e4", kind: "model", name: "model", attrs: { purpose: "main", usage: { reported: false } } }),
      step({ step_id: "e5", kind: "auto_approver", name: "write_file", shadow: true, attrs: { result: "allow", mode: "shadow" } }),
      step({ step_id: "e6", kind: "wait", name: "approval", attrs: { kind: "approval", where: "inbox", outcome: "approved" } }),
      step({ step_id: "e7", kind: "tool", name: "run_shell", attrs: { exit_code: 0, decision: { origin: "reviewer", path: [] } } }),
      step({ step_id: "e8", kind: "tool", name: "write_file", status: "denied", attrs: {} }),
    ] as any);
    render(<StepsPanel sessionId="s1" onClose={() => {}} />);
    await waitFor(() => expect(screen.getAllByTestId("steps-row")).toHaveLength(8));
    const labels = screen.getAllByTestId("steps-row").map((r) => r.textContent);
    expect(labels[0]).toContain("exit 3");
    expect(labels[1]).toContain("failed · provider error");
    expect(labels[2]).toContain("over the context limit");
    expect(labels[3]).toContain("no usage");
    expect(labels[4]).toContain("Shadow check · would have allowed");
    expect(labels[5]).toContain("Waiting · parked in Inbox · approval");
    expect(labels[6]).toContain("auto-approved");
    expect(labels[7]).toContain("refused");
  });

  it("shows what the context held, a tool's result size and a model call's ids", async () => {
    const ctx = step({ step_id: "c", kind: "context", name: "context", duration_ms: 0, attrs: {
      session_prompt_hash: "42070c", size_chars: 1057, block_hash: "69a95b",
      session_start: { memories: ["m1", "m2"], memory_mode: "index", rules: 1, agents_md: 1 },
      per_turn: { folders: 2, mode_note: "plan", sandbox_note: true, skills_menu: 3, skills_hash: "ab12cd" } } });
    const tool = step({ step_id: "t", kind: "tool", name: "read_file", call_id: "call-9", attrs: { result_bytes: 2048 } });
    const model = step({ step_id: "m", kind: "model", name: "model", attrs: { purpose: "main", usage: { reported: true },
      provider_facts: { provider_request_id: "req_1", provider_response_id: "resp_1" } } });
    vi.mocked(getRecordedTurns).mockResolvedValue([turn] as any);
    vi.mocked(getTurnSteps).mockResolvedValue([ctx, model, tool] as any);
    vi.mocked(getStepDetail).mockImplementation(async (_s, id) => ({ c: ctx, m: { ...model, exchange_rebuilt: [{ request: {}, response: "" }, { request: {}, response: "" }, { auxiliary: true, request: {}, response: "" }] }, t: tool } as any)[id]);
    render(<StepsPanel sessionId="s1" onClose={() => {}} />);
    await waitFor(() => expect(screen.getAllByTestId("steps-row")).toHaveLength(3));
    fireEvent.click(screen.getAllByTestId("steps-row")[0]);
    let d = (await screen.findByTestId("steps-details")).textContent || "";
    for (const s of ["Memories 2 · as an index", "Your rules 1", "AGENTS.md files 1", "Folders 2", "Mode note plan", "Skills offered 3 ⓢab12cd", "1057 · 69a95b"]) expect(d).toContain(s);
    fireEvent.click(screen.getAllByTestId("steps-row")[1]);
    await waitFor(() => expect(screen.getByTestId("steps-details").textContent).toContain("req_1"));
    d = screen.getByTestId("steps-details").textContent || "";
    expect(d).toContain("resp_1");
    expect(d).toContain("Attempts2");  // the auxiliary lookup is not an attempt
    fireEvent.click(screen.getAllByTestId("steps-row")[2]);
    await waitFor(() => expect(screen.getByTestId("steps-details").textContent).toContain("2.0 KB"));
    expect(screen.getByTestId("steps-details").textContent).toContain("call-9");
  });

  it("shows the context size when the window is not known", async () => {
    vi.mocked(getRecordedTurns).mockResolvedValue([turn] as any);
    vi.mocked(getTurnSteps).mockResolvedValue(steps as any);
    vi.mocked(getRecordedUsage).mockResolvedValue({
      pill: 10, at_least: false, unreported_calls: 0, failed_calls: 0, overflow_calls: 0, cache_share: 0,
      by_purpose: { main: 10 }, totals: { input: 9, cache: 0, output: 1 }, by_model_parts: {},
      context: { tokens: 6837, window: null },
    });
    render(<StepsPanel sessionId="s1" onClose={() => {}} />);
    fireEvent.click(await screen.findByRole("tab", { name: "Usage" }));
    expect((await screen.findByTestId("steps-usage")).textContent).toContain("6.8k (window not known)");
  });

  it("says which steps were still running when the app stopped", async () => {
    // TD-12: a turn found open at start is closed as unknown; its open steps have no time.
    vi.mocked(getRecordedTurns).mockResolvedValue([{ ...turn, status: "unknown", duration_ms: null }] as any);
    vi.mocked(getTurnSteps).mockResolvedValue([
      step({ step_id: "m", kind: "model", name: "model", attrs: { purpose: "main", usage: { reported: true } } }),
      step({ step_id: "g", kind: "notice", name: "recording_gap", duration_ms: 0 }),
      step({ step_id: "w", kind: "wait", name: "approval", status: "unknown", duration_ms: null, attrs: { kind: "approval", where: "inbox" } }),
    ] as any);
    render(<StepsPanel sessionId="s1" onClose={() => {}} />);
    expect((await screen.findByTestId("steps-gap")).textContent).toContain("1 step was still running when OpenWorker stopped");
    const labels = screen.getAllByTestId("steps-row").map((r) => r.textContent);
    expect(labels[1]).toContain("Recording gap");
    expect(labels[2]).toContain("unknown");
    expect(labels[2]).toContain("—");
  });

  it("opens another session at its newest turn", async () => {
    // Round-2 in-app check: switching sessions with the panel open kept the old turn
    // position ("Turn 1 of 2" instead of the newest).
    vi.mocked(getRecordedTurns).mockImplementation(async (sid) =>
      (sid === "a" ? [turn, { ...turn, turn_id: "t2", seq: 2 }] : [turn, { ...turn, turn_id: "t2", seq: 2 }, { ...turn, turn_id: "t3", seq: 3 }]) as any);
    vi.mocked(getTurnSteps).mockResolvedValue(steps as any);
    const { rerender } = render(<StepsPanel sessionId="a" onClose={() => {}} />);
    await screen.findByText("Turn 2 of 2");
    fireEvent.click(screen.getByRole("button", { name: "Previous turn" }));
    await screen.findByText("Turn 1 of 2");
    rerender(<StepsPanel sessionId="b" onClose={() => {}} />);
    await screen.findByText("Turn 3 of 3");
  });

  it("lists a decision model's answers", async () => {
    const check = step({ step_id: "q", kind: "auto_approver", name: "run_shell", attrs: { result: "unsure", mode: "live", reviewer: { kind: "openai_decisions" } },
      content: { reason: "requested 0.98 but unrelated_destination 0.99", questions: [{ name: "requested", answer: "yes", score: 0.98 }, { name: "unrelated_destination", answer: "yes", score: 0.99 }] } });
    vi.mocked(getRecordedTurns).mockResolvedValue([turn] as any);
    vi.mocked(getTurnSteps).mockResolvedValue([check] as any);
    vi.mocked(getStepDetail).mockResolvedValue(check as any);
    render(<StepsPanel sessionId="s1" onClose={() => {}} />);
    fireEvent.click((await screen.findAllByTestId("steps-row"))[0]);
    expect((await screen.findByTestId("steps-questions")).textContent).toContain("unrelated_destinationyes 0.99");
  });

  it("shows the mode, the reviewer and the turn's stamps in the header", async () => {
    // UX-059 OB-1 mode pill; OB-12, OB-14, OB-15 stamp line.
    vi.mocked(getRecordedTurns).mockResolvedValue([{ ...turn,
      approval_mode: "auto-approve", reviewer: { active: true, shadow: false, kind: "session_model", model: "openrouter:model-b" },
      buckets: { ...turn.buckets, auto_approver: 2900 },
      stamp: { machine: { kind: "this_machine", name: "DESKTOP-1", app_version: "0.2.3" }, sandbox: { provider: "direct", enforcement: "none" },
               persona_fp: "c5dc98", model: { id: "openrouter:model-b", provider_title: "OpenRouter" } } }] as any);
    vi.mocked(getTurnSteps).mockResolvedValue(steps as any);
    render(<StepsPanel sessionId="s1" onClose={() => {}} />);
    expect((await screen.findByTestId("steps-mode")).textContent).toBe("Auto-approve · session model (openrouter:model-b)");
    const stamp = screen.getByTestId("steps-stamp").textContent || "";
    for (const part of ["DESKTOP-1 · 0.2.3", "No sandbox", "ⓕc5dc98", "openrouter:model-b · OpenRouter"]) expect(stamp).toContain(part);
    expect((await screen.findByTestId("steps-checks")).textContent).toContain("1 check · 1 allowed · 0 asked you · 0 blocked · 2.9s · session model (openrouter:model-b)");
  });

  it("names a shadow reviewer and a sandbox", async () => {
    vi.mocked(getRecordedTurns).mockResolvedValue([{ ...turn, approval_mode: "interactive",
      reviewer: { active: false, shadow: true, kind: "openai_decisions", model: null },
      stamp: { machine: { kind: "cloud", name: "box-7" }, sandbox: { provider: "OpenShell", enforcement: "full", profile: "limited" } } }] as any);
    vi.mocked(getTurnSteps).mockResolvedValue([] as any);
    render(<StepsPanel sessionId="s1" onClose={() => {}} />);
    expect((await screen.findByTestId("steps-mode")).textContent).toBe("Ask for approval · shadow: OpenAI Decisions");
    expect(screen.getByTestId("steps-stamp").textContent).toContain("box-7 (cloud)");
    expect(screen.getByTestId("steps-stamp").textContent).toContain("OpenShell · enforced · network limited");
  });

  it("opens the turn an answer's Steps link points at", async () => {
    // UX-059 OB-1: the "Steps" link in a turn's footer. The answer's time picks the turn.
    // The next message was sent within a second of turn 1's answer (round-2 check).
    const turns = [{ ...turn, turn_id: "t1", seq: 1, started_at: 1_000_000, duration_ms: 4_200 },
                   { ...turn, turn_id: "t2", seq: 2, started_at: 1_004_600, duration_ms: 3_000 },
                   { ...turn, turn_id: "t3", seq: 3, started_at: 3_000_000, duration_ms: null }];
    vi.mocked(getRecordedTurns).mockResolvedValue(turns as any);
    vi.mocked(getTurnSteps).mockResolvedValue(steps as any);
    const { rerender } = render(<StepsPanel sessionId="s1" openAt={{ ts: 1004, n: 1 }} onClose={() => {}} />);
    await screen.findByText("Turn 1 of 3");
    rerender(<StepsPanel sessionId="s1" openAt={{ ts: 1007, n: 2 }} onClose={() => {}} />);
    await screen.findByText("Turn 2 of 3");
    rerender(<StepsPanel sessionId="s1" openAt={{ ts: 3010, n: 3 }} onClose={() => {}} />);
    await screen.findByText("Turn 3 of 3");
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
    expect(formatDuration(4)).toBe("<0.1s");
    expect(formatDuration(0)).toBe("0.0s");
    expect(formatDuration(3400)).toBe("3.4s");
    expect(formatDuration(41_000)).toBe("41s");
    expect(formatDuration(134_000)).toBe("2m 14s");
    expect(formatDuration(null)).toBe("—");
  });
});

describe("Issues tab (OB-3)", () => {
  const issues = {
    turn_id: "t1", signals_version: "0.2", warn: 2, info: 1, seen: false,
    issues: [
      { check: "tool_failed", severity: "warn", step_id: "x1", step_seq: 5, text: "run_shell failed with exit code 6.", facts: { how: "exit" } },
      { check: "tool_failed", severity: "warn", step_id: "x1", step_seq: 5, text: "run_shell failed with exit code 6 again.", facts: { how: "exit" } },
      { check: "shadow_disagreed", severity: "info", step_id: "a1", step_seq: 3, text: "The shadow reviewer would have said allow.", facts: {} },
    ],
    fine: [{ check: "repeated_step", text: "No step repeated 3 times in a row" }],
    not_checked: [{ check: "off_plan", text: "No plan to check against" }],
  };

  it("groups issues by check, puts warnings first, folds the fine list, and goes to the step", async () => {
    vi.mocked(getRecordedTurns).mockResolvedValue([{ ...turn, issues: { warn: 2, info: 1 } }] as any);
    vi.mocked(getTurnSteps).mockResolvedValue(steps as any);
    vi.mocked(getTurnIssues).mockResolvedValue(issues as any);
    vi.mocked(getStepDetail).mockResolvedValue(null);
    render(<StepsPanel sessionId="s1" refreshKey={0} onClose={() => {}} />);
    const tab = await screen.findByTestId("steps-tab-issues");
    expect(tab.textContent).toBe("Issues · 2");
    fireEvent.click(tab);
    const groups = await screen.findAllByTestId("steps-issue");
    expect(groups.map((g) => g.querySelector("b")?.textContent)).toEqual(["A tool failed · 2", "Shadow check disagreed with you"]);
    expect(groups[1].className).toContain("info");
    expect(markIssuesSeen).toHaveBeenCalledWith("s1");
    expect(screen.queryByTestId("steps-fine")?.textContent ?? "").not.toContain("No step repeated");
    fireEvent.click(screen.getByTestId("steps-fine-toggle"));
    expect(screen.getByTestId("steps-fine").textContent).toContain("No step repeated 3 times in a row");
    expect(screen.getByTestId("steps-fine").textContent).toContain("No plan to check against");
    fireEvent.click(screen.getAllByText("Go to step")[0]);
    await waitFor(() => expect(screen.getByTestId("steps-timeline")).toBeTruthy());
    expect(screen.getAllByTestId("steps-row-issue").length).toBe(1);
  });
});


describe("Helpers and team workers (OB-7)", () => {
  const usage = (n: number) => ({ purpose: "helper", usage: { reported: true, input_fresh: n - 100, cache_read: 0, cache_write: 0, output: 100 } });
  const helperSteps = [
    step({ step_id: "m0", kind: "model", name: "model", duration_ms: 500, attrs: { purpose: "main" } }),
    step({ step_id: "e1", name: "explore", call_id: "c1", started_at: 1500, duration_ms: 6000,
           attrs: { helper: { index: 1, status: "ok", model_calls: 4, tool_calls: 2, tokens: 12300 } } }),
    step({ step_id: "e1m", kind: "model", name: "model", parent_step_id: "e1", started_at: 1600, attrs: usage(6000) }),
    step({ step_id: "e1t", name: "grep", parent_step_id: "e1", started_at: 1700 }),
    step({ step_id: "e2", name: "explore", call_id: "c2", started_at: 1500, duration_ms: 5000,
           attrs: { helper: { index: 2, status: "step_limit", model_calls: 10, tool_calls: 9, tokens: 40000 } } }),
    step({ step_id: "e2m", kind: "model", name: "model", parent_step_id: "e2", started_at: 1600, attrs: usage(4000) }),
    step({ step_id: "m9", kind: "model", name: "model", started_at: 8000, duration_ms: 2000, attrs: { purpose: "main" } }),
  ];

  it("folds each helper's steps behind its summary line, and opens them on click", async () => {
    vi.mocked(getRecordedTurns).mockResolvedValue([turn] as any);
    vi.mocked(getTurnSteps).mockResolvedValue(helperSteps as any);
    render(<StepsPanel sessionId="s1" onClose={() => {}} />);
    const helpers = await screen.findAllByTestId("steps-helper");
    expect(helpers.map((h) => h.querySelector(".steps-name")?.textContent)).toEqual([
      "Helper 1 · 6 steps · 12.3k tokens · ok",
      "Helper 2 · 19 steps · 40k tokens · hit its step limit",
    ]);
    // Folded by default: the explore rows and the lead's own steps, no helper steps.
    expect(screen.getAllByTestId("steps-row")).toHaveLength(4);
    fireEvent.click(helpers[0]);
    expect(helpers[0].getAttribute("aria-expanded")).toBe("true");
    const labels = screen.getAllByTestId("steps-row").map((r) => r.textContent || "");
    expect(labels).toHaveLength(6);
    expect(labels[2]).toContain("Model · helper");
    expect(labels[3]).toContain("grep");
    fireEvent.click(helpers[0]);
    expect(screen.getAllByTestId("steps-row")).toHaveLength(4);
  });

  it("sums a running helper's own steps until its summary is recorded", async () => {
    const running = helperSteps.slice(0, 4).map((x) => x.step_id === "e1" ? { ...x, status: "running", duration_ms: null, attrs: {} } : x);
    vi.mocked(getRecordedTurns).mockResolvedValue([turn] as any);
    vi.mocked(getTurnSteps).mockResolvedValue(running as any);
    render(<StepsPanel sessionId="s1" onClose={() => {}} />);
    const [helper] = await screen.findAllByTestId("steps-helper");
    expect(helper.querySelector(".steps-name")?.textContent).toBe("Helper 1 · 2 steps · 6k tokens · running");
  });

  const workers = [{ session_id: "sess_qa", turn_id: "W1", seq: 3, name: "qa", started_at: 6000, duration_ms: 41_000,
                     status: "completed", tokens: 12_000, at_least: false, team_id: "team-1", item_ids: ["3"], via: "board" }];

  it("shows a worker's turn as one row that opens the worker session", async () => {
    const onOpen = vi.fn();
    vi.mocked(getRecordedTurns).mockResolvedValue([{ ...turn, workers }] as any);
    vi.mocked(getTurnSteps).mockResolvedValue(helperSteps as any);
    render(<StepsPanel sessionId="s1" onClose={() => {}} onOpenSession={onOpen} />);
    const row = await screen.findByTestId("steps-worker");
    expect(row.textContent).toContain("Worker qa · turn 3");
    expect(row.textContent).toContain("12k tokens");
    expect(row.textContent).toContain("41s");
    // In time order: after the helpers, before the final answer (which started at 8000).
    const timeline = screen.getByTestId("steps-timeline");
    const order = Array.from(timeline.querySelectorAll("[data-testid]")).map((e) => e.getAttribute("data-testid")).filter((x) => x !== "steps-worker-open");
    expect(order.slice(-2)).toEqual(["steps-worker", "steps-row"]);
    fireEvent.click(screen.getByTestId("steps-worker-open"));
    expect(onOpen).toHaveBeenCalledWith("sess_qa");
  });

  it("has no open link where the app cannot open sessions", async () => {
    vi.mocked(getRecordedTurns).mockResolvedValue([{ ...turn, workers }] as any);
    vi.mocked(getTurnSteps).mockResolvedValue([] as any);
    render(<StepsPanel sessionId="s1" onClose={() => {}} />);
    await screen.findByTestId("steps-worker");
    expect(screen.queryByTestId("steps-worker-open")).toBeNull();
  });

  it("reads in Chinese", async () => {
    i18n.addResourceBundle("zh", "translation", zh, true, true);
    await i18n.changeLanguage("zh");
    try {
      vi.mocked(getRecordedTurns).mockResolvedValue([{ ...turn, workers }] as any);
      vi.mocked(getTurnSteps).mockResolvedValue(helperSteps as any);
      render(<StepsPanel sessionId="s1" onClose={() => {}} onOpenSession={() => {}} />);
      const helpers = await screen.findAllByTestId("steps-helper");
      expect(helpers[0].querySelector(".steps-name")?.textContent).toBe("助手 1 · 6 个步骤 · 12.3k 个 token · 完成");
      expect(helpers[1].textContent).toContain("达到步骤上限");
      expect(screen.getByTestId("steps-worker").textContent).toContain("同事 qa · 第 3 轮");
      expect(screen.getByTestId("steps-worker-open").textContent).toBe("打开同事的会话 ↗");
    } finally {
      await i18n.changeLanguage("en");
    }
  });
});
