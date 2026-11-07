// The rail's glance layer: the Environment block, the Context section, and the
// right-aligned header summaries that make collapsed sections still answer
// "what's in here?" (seventeenth-pass rule: nothing shouts louder than this).
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ContextInfo } from "../types";
import { RightRail } from "./RightRail";

vi.mock("../api", () => ({
  getAgentTasks: vi.fn().mockResolvedValue([]),
  getArtifacts: vi.fn().mockResolvedValue([]),
  getJournalCases: vi.fn().mockResolvedValue([]),
  getRoots: vi.fn().mockResolvedValue([]),
  readArtifact: vi.fn(),
  revealArtifact: vi.fn(),
}));

vi.mock("./AccessSection", () => ({ AccessSection: () => null }));

const baseProps = {
  active: true,
  sessionId: "session-a",
  refreshKey: 0,
  toolNames: [],
  todo: [],
  running: false,
};

const ctx = (over: Partial<ContextInfo> = {}): ContextInfo => ({
  window: 200_000,
  used: 68_000,
  estimated: false,
  breakdown: { system: 10_000, tools: 8_000, messages: 50_000 },
  ...over,
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("environment block", () => {
  it("names the machine, the folder with its branch, and the sandbox state", () => {
    render(
      <RightRail
        {...baseProps}
        machineName="ro4d"
        workspace="/Users/x/code/openworker"
        branch="rp/right-panel"
        sandbox={{ state: "sandboxed", provider: "openshell", network: "", sites: [], session_sites: [], folders: [], logins: [], started: true } as any}
      />,
    );
    const env = screen.getByTestId("rail-env");
    expect(env.textContent).toContain("ro4d");
    expect(env.textContent).toContain("openworker");
    expect(env.textContent).toContain("rp/right-panel");
    expect(screen.getByTestId("rail-env-sandbox").textContent).toContain("openshell");
  });

  it("falls back to this machine + temporary folder, and hides a sandbox that is off", () => {
    render(<RightRail {...baseProps} scratchPrimary sandbox={{ state: "off" } as any} />);
    const env = screen.getByTestId("rail-env");
    expect(env.textContent).toContain("This machine");
    expect(env.textContent).toContain("Temporary folder");
    expect(screen.queryByTestId("rail-env-sandbox")).toBeNull();
  });
});

describe("context section", () => {
  it("shows the percentage on the collapsed header and the breakdown when expanded", () => {
    render(<RightRail {...baseProps} context={ctx()} />);
    expect(screen.getByTestId("ctx-summary").textContent).toContain("34%");
    fireEvent.click(screen.getByTestId("rail-toggle-context"));
    const panel = screen.getByTestId("ctx-panel");
    expect(panel.textContent).toContain("System prompt");
    expect(panel.textContent).toContain("Tools");
    expect(panel.textContent).toContain("Conversation");
    expect(panel.textContent).toContain("68k of 200k");
  });

  it("says the window is unknown instead of inventing a percentage", () => {
    render(<RightRail {...baseProps} context={ctx({ window: null })} />);
    expect(screen.queryByTestId("ctx-summary")).toBeNull(); // token count, no meter
    fireEvent.click(screen.getByTestId("rail-toggle-context"));
    expect(screen.getByTestId("ctx-panel").textContent).toContain("window unknown");
  });

  it("notes a compaction once one happened", () => {
    render(<RightRail {...baseProps} context={ctx({ compaction: { messages: 12 } })} />);
    fireEvent.click(screen.getByTestId("rail-toggle-context"));
    expect(screen.getByTestId("ctx-compacted").textContent).toContain("12");
  });

  it("renders no section at all before the server has reported", () => {
    render(<RightRail {...baseProps} />);
    expect(screen.queryByTestId("rail-toggle-context")).toBeNull();
  });
});

describe("subagents section", () => {
  it("lists runs, amber while one waits on an approval, ages for the done", async () => {
    const { getAgentTasks } = await import("../api");
    (getAgentTasks as any).mockResolvedValue([
      { id: "agent-1", label: "repo map", task: "t", mode: "read_only", status: "done", created: 1, finished: Date.now() / 1000 - 7200, icon_seed: 1 },
      { id: "agent-2", label: "marker run", task: "t", mode: "inherit", status: "waiting", created: 2, icon_seed: 2 },
    ]);
    render(<RightRail {...baseProps} />);
    const head = await screen.findByTestId("rail-toggle-subagents");
    expect(head.textContent).toContain("1 waiting");
    expect(head.textContent).toContain("1 done");
    expect(head.querySelector(".rail-summary.attention")).not.toBeNull();
    // Header chips: one animal glyph per run, derived from the stable seed.
    expect(head.querySelectorAll(".agent-chips svg[data-glyph]").length).toBe(2);
    fireEvent.click(head);
    const list = screen.getByTestId("agent-tasks");
    expect(list.querySelector('svg[data-glyph="puppy"]')).not.toBeNull(); // seed 1
    expect(list.querySelector('svg[data-glyph="bunny"]')).not.toBeNull(); // seed 2
    expect(list.textContent).toContain("repo map");
    expect(list.textContent).toContain("marker run");
    expect(list.textContent).toContain("waiting on approval");
    (getAgentTasks as any).mockResolvedValue([]);
  });
});

describe("header summaries", () => {
  it("progress says how much of the plan is done while collapsed", () => {
    render(
      <RightRail
        {...baseProps}
        todo={[
          { content: "a", status: "done" },
          { content: "b", status: "done" },
          { content: "c", status: "pending" },
        ]}
      />,
    );
    const head = screen.getByTestId("rail-toggle-progress");
    expect(head.textContent).toContain("2 of 3 done");
  });

  it("progress says it is working during a live turn", () => {
    render(<RightRail {...baseProps} running />);
    expect(screen.getByTestId("rail-toggle-progress").textContent).toContain("Working…");
  });

  it("the board summary turns to attention counts when items are blocked", () => {
    render(
      <RightRail
        {...baseProps}
        board={{
          space: "s",
          name: "Board",
          items: [
            { id: 1, state: "blocked" } as any,
            { id: 2, state: "open" } as any,
          ],
        }}
      />,
    );
    const head = screen.getByTestId("rail-toggle-board");
    expect(head.textContent).toContain("1 needs attention");
    expect(head.querySelector(".rail-summary.attention")).not.toBeNull();
  });
});
