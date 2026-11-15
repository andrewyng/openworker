// OB-18: the run list's health, tokens and issues, and the "failed in a row" banner.
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ScheduledView } from "./ScheduledView";

const run = (id: string, over: Record<string, any> = {}) => ({
  run_id: id, task_id: "task-brief", session_id: `__run__${id}`, started_at: 1_793_600_000, finished_at: 1_793_600_030,
  status: "ok", result_text: null, artifacts: [], error: null, trigger: "schedule",
  turns: 1, total: 6000, at_least: false, duration_ms: 30_000, warn: 0, checks: [], check_titles: [], health: "ok",
  first_turn_id: `turn-${id}`, ...over,
});

vi.mock("../api", () => ({
  announceAutomationsChanged: vi.fn(),
  createAutomation: vi.fn(),
  deleteAutomation: vi.fn(),
  getAutomation: vi.fn(async () => ({
    task: { id: "task-brief", title: "Morning brief", instructions: "brief", cron: "0 6 * * *", workspace: "/w", agent: "cowork",
      enabled: true, seen_runs_at: 0, permissions: [] },
    runs: [
      run("run-4", { health: "failed", checks: ["tool_failed"], check_titles: ["A tool failed"] }),
      run("run-3", { status: "error", error: "e2", health: "failed" }),
      run("run-1", { total: null, turns: 0, first_turn_id: null, health: "unknown" }),
    ],
    failed_in_a_row: { count: 3, last_error: "A tool failed", run_id: "run-4", session_id: "__run__run-4", turn_id: "turn-run-4" },
  })),
  getAutomations: vi.fn().mockResolvedValue([]),
  markAutomationSeen: vi.fn(async () => ({ ok: true })),
  updateAutomation: vi.fn(),
}));
vi.mock("./AutomationQuickstart", () => ({ AutomationQuickstart: () => null }));
vi.mock("./IntegrationsView", () => ({ PanelHead: ({ title }: { title: string }) => <h1>{title}</h1> }));

afterEach(cleanup);

describe("ScheduledView run health", () => {
  it("shows each run's health, tokens and issues, the banner, and opens a run's Steps", async () => {
    const steps = vi.fn();
    render(<ScheduledView onOpenRun={vi.fn()} onRunNow={vi.fn()} initialOpenId="task-brief" onOpenRunSteps={steps} />);
    await waitFor(() => expect(screen.getByTestId("runs-failed-banner")).toBeTruthy());
    expect(screen.getByTestId("runs-failed-banner").textContent).toContain("Failed 3 runs in a row. Last error: A tool failed");
    const rows = screen.getAllByTestId("run-health-row").map((r) => r.textContent || "");
    expect(rows[0]).toContain("failed");
    expect(rows[0]).toContain("(run said ok)");
    expect(rows[0]).toContain("6k tokens");
    expect(rows[0]).toContain("A tool failed");
    expect(rows[1]).toContain("error");
    expect(rows[2]).toContain("— tokens");
    expect(rows[2]).toContain("not recorded");
    fireEvent.click(screen.getAllByTestId("run-steps")[0]);
    expect(steps).toHaveBeenCalledWith("__run__run-4", "turn-run-4", "/w", "cowork", { id: "task-brief", title: "Morning brief" });
  });
});
