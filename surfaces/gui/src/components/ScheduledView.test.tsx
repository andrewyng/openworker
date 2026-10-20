import { StrictMode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen } from "@testing-library/react";
import { ScheduledView } from "./ScheduledView";
import { announceAutomationsChanged, getAutomation, markAutomationSeen, type Automation } from "../api";

vi.mock("../api", () => ({
  announceAutomationsChanged: vi.fn(),
  createAutomation: vi.fn(),
  deleteAutomation: vi.fn(),
  getAutomation: vi.fn(),
  getAutomations: vi.fn().mockResolvedValue([]),
  markAutomationSeen: vi.fn(),
  updateAutomation: vi.fn(),
}));

vi.mock("./AutomationQuickstart", () => ({
  AutomationQuickstart: () => <div data-testid="automation-quickstart" />,
}));

vi.mock("./IntegrationsView", () => ({
  PanelHead: ({ title, sub }: { title: string; sub: string }) => (
    <header>
      <h1>{title}</h1>
      <p>{sub}</p>
    </header>
  ),
}));

afterEach(cleanup);
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(getAutomation).mockReset();
  vi.mocked(markAutomationSeen).mockResolvedValue({ ok: true });
});

const automation: Automation = {
  id: "task-1", title: "Daily news", instructions: "Summarize the news",
  schedule: "Daily", workspace: "/workspace", agent: "cowork", enabled: true,
  next_run: null, last_run: 200, last_status: "completed", run_count: 1,
  notify_on_completion: true, always_allowed: [], seen_runs_at: 100,
};

function detail(task = automation) {
  return {
    task: { ...task },
    runs: [{
      run_id: "r1", task_id: task.id, session_id: "__run__r1", started_at: 200,
      finished_at: 201, status: "completed", result_text: "Done", artifacts: [],
      error: null, trigger: "schedule",
    }],
  };
}

function deferredDetail() {
  let resolve!: (value: ReturnType<typeof detail>) => void;
  const promise = new Promise<ReturnType<typeof detail>>((done) => { resolve = done; });
  return { promise, resolve };
}

describe("ScheduledView read receipts", () => {
  it("captures the old seen mark before advancing it, preserving new-run highlights", async () => {
    const pending = deferredDetail();
    let storedMark = 100;
    vi.mocked(getAutomation).mockImplementation(() =>
      pending.promise.then(() => detail({ ...automation, seen_runs_at: storedMark })),
    );
    vi.mocked(markAutomationSeen).mockImplementation(async () => {
      storedMark = 300;
      return { ok: true };
    });
    render(<ScheduledView initialOpenId="task-1" onOpenRun={vi.fn()} onRunNow={vi.fn()} />);
    expect(markAutomationSeen).not.toHaveBeenCalled();
    await act(async () => { pending.resolve(detail()); });
    expect(screen.getByTestId("run-new")).toBeTruthy();
    expect(markAutomationSeen).toHaveBeenCalledTimes(1);
    expect(markAutomationSeen).toHaveBeenCalledWith("task-1");
    expect(announceAutomationsChanged).toHaveBeenCalledOnce();
  });

  it("ignores the discarded StrictMode request before marking the active detail seen", async () => {
    const discarded = deferredDetail();
    const active = deferredDetail();
    vi.mocked(getAutomation)
      .mockReturnValueOnce(discarded.promise)
      .mockReturnValueOnce(active.promise);
    render(
      <StrictMode>
        <ScheduledView initialOpenId="task-1" onOpenRun={vi.fn()} onRunNow={vi.fn()} />
      </StrictMode>,
    );
    await act(async () => { discarded.resolve(detail()); });
    expect(markAutomationSeen).not.toHaveBeenCalled();
    await act(async () => { active.resolve(detail()); });
    expect(screen.getByTestId("run-new")).toBeTruthy();
    expect(markAutomationSeen).toHaveBeenCalledTimes(1);
    expect(markAutomationSeen).toHaveBeenCalledWith("task-1");
  });

  it("does not mark an unread detail seen when loading fails", async () => {
    vi.mocked(getAutomation).mockRejectedValue(new Error("offline"));
    await act(async () => {
      render(<ScheduledView initialOpenId="task-1" onOpenRun={vi.fn()} onRunNow={vi.fn()} />);
    });
    expect(markAutomationSeen).not.toHaveBeenCalled();
    expect(announceAutomationsChanged).not.toHaveBeenCalled();
  });

  it("does not mark a detail seen after the user leaves before it loads", async () => {
    const pending = deferredDetail();
    vi.mocked(getAutomation).mockReturnValue(pending.promise);
    const view = render(<ScheduledView initialOpenId="task-1" onOpenRun={vi.fn()} onRunNow={vi.fn()} />);
    view.unmount();
    await act(async () => { pending.resolve(detail()); });
    expect(markAutomationSeen).not.toHaveBeenCalled();
  });
});

describe("ScheduledView empty state", () => {
  it("renders translated emphasis as a strong element, not literal markup", () => {
    const { container } = render(
      <ScheduledView onOpenRun={vi.fn()} onRunNow={vi.fn()} />,
    );

    expect(
      screen.getByText("+ New automation", { selector: "strong" }),
    ).toBeTruthy();
    expect(container.textContent).not.toContain("<strong>");
  });
});
