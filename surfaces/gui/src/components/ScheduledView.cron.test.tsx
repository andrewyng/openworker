// A schedule at midnight must survive an edit: the editor parses the cron hour with
// `parseInt(h, 10) || 9`, and 0 is falsy, so "30 0 * * *" opened as 09:30 — and
// saving the editor rewrote the stored cron, silently moving the automation from
// 00:30 to 09:30.
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ScheduledView } from "./ScheduledView";
import { getAutomation, updateAutomation } from "../api";

vi.mock("../api", () => ({
  announceAutomationsChanged: vi.fn(),
  createAutomation: vi.fn(),
  deleteAutomation: vi.fn(),
  getAutomation: vi.fn(),
  getAutomations: vi.fn().mockResolvedValue([]),
  markAutomationSeen: vi.fn(async () => ({ ok: true })),
  updateAutomation: vi.fn(async () => ({ ok: true })),
}));

vi.mock("./AutomationQuickstart", () => ({
  AutomationQuickstart: () => <div data-testid="automation-quickstart" />,
}));

vi.mock("./IntegrationsView", () => ({
  PanelHead: ({ title }: { title: string }) => <h1>{title}</h1>,
}));

afterEach(cleanup);

const midnightTask = {
  id: "task-midnight",
  title: "Nightly digest",
  instructions: "digest the day",
  schedule: "Daily",
  schedule_raw: { kind: "cron", cron: "30 0 * * *" },
  workspace: "/w",
  agent: "cowork",
  enabled: true,
  next_run: null,
  last_run: null,
  last_status: null,
  run_count: 0,
  notify_on_completion: false,
  always_allowed: [],
  seen_runs_at: 0,
};

describe("ScheduledView cron round-trip", () => {
  it("keeps a midnight schedule when the automation is opened, edited and saved", async () => {
    vi.mocked(getAutomation).mockResolvedValue({ task: midnightTask, runs: [] } as never);
    const { container } = render(
      <ScheduledView onOpenRun={vi.fn()} onRunNow={vi.fn()} initialOpenId="task-midnight" />,
    );
    await waitFor(() => expect(screen.getByText("Nightly digest")).toBeTruthy());

    fireEvent.click(screen.getByText("Edit"));
    const time = container.querySelector('input[type="time"]') as HTMLInputElement;
    // Midnight is 00:30, not the 09:30 fallback.
    expect(time.value).toBe("00:30");

    fireEvent.click(screen.getByText("Save"));
    await waitFor(() => expect(updateAutomation).toHaveBeenCalled());
    // Saving must not move the automation to the morning.
    expect(vi.mocked(updateAutomation).mock.calls[0][1]).toMatchObject({ cron: "30 0 * * *" });
  });
});
