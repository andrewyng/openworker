// OPE-309: opening an automation marks it seen, but the "new" pills must compare against
// the mark as it was BEFORE this visit. The mock server below behaves like the real one:
// mark-seen moves `seen_runs_at` to now, and the detail load returns whatever it is then.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { ScheduledView } from "./ScheduledView";
import * as api from "../api";

const NOW = 1_793_700_000;
const server = vi.hoisted(() => ({ seenRunsAt: 0, calls: [] as string[] }));

vi.mock("../api", () => ({
  announceAutomationsChanged: vi.fn(),
  createAutomation: vi.fn(),
  deleteAutomation: vi.fn(),
  getAutomation: vi.fn(async () => {
    server.calls.push("load");
    // A slow load: it answers after anything sent at the same time has finished.
    await new Promise((r) => setTimeout(r, 20));
    return {
      task: { id: "task-news", title: "Daily AI News", instructions: "news", cron: "0 9 * * *", workspace: "/w",
        agent: "cowork", enabled: true, seen_runs_at: server.seenRunsAt, permissions: [] },
      runs: [
        { run_id: "r2", task_id: "task-news", session_id: "__run__r2", started_at: NOW - 60, finished_at: NOW - 30,
          status: "ok", result_text: null, artifacts: [], error: null, trigger: "schedule" },
      ],
      failed_in_a_row: null,
    };
  }),
  getAutomations: vi.fn().mockResolvedValue([]),
  markAutomationSeen: vi.fn(async () => {
    server.calls.push("mark-seen");
    server.seenRunsAt = NOW; // the server moves the mark to "now" at once
    return { ok: true };
  }),
  updateAutomation: vi.fn(),
}));
vi.mock("./AutomationQuickstart", () => ({ AutomationQuickstart: () => null }));
vi.mock("./IntegrationsView", () => ({ PanelHead: ({ title }: { title: string }) => <h1>{title}</h1> }));

beforeEach(() => {
  server.seenRunsAt = 0;
  server.calls = [];
  vi.mocked(api.announceAutomationsChanged).mockClear();
});
afterEach(cleanup);

describe("ScheduledView seen mark", () => {
  it("keeps the New pill on runs from before this visit, then marks the automation seen", async () => {
    render(<ScheduledView onOpenRun={vi.fn()} onRunNow={vi.fn()} initialOpenId="task-news" />);
    await waitFor(() => expect(screen.getByTestId("run-new")).toBeTruthy());
    // The load read the old mark first; mark-seen went out only after it.
    await waitFor(() => expect(server.calls).toEqual(["load", "mark-seen"]));
    // …and the sidebar badge is still told to clear.
    await waitFor(() => expect(api.announceAutomationsChanged).toHaveBeenCalledTimes(1));
    expect(server.seenRunsAt).toBe(NOW);
  });
});
