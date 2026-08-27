import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const api = vi.hoisted(() => ({
  getAutomation: vi.fn(async () => ({
    task: {
      id: "task-1",
      title: "Daily brief",
      instructions: "Send a summary.",
      workspace: "/tmp/openworker",
      agent: "cowork",
      enabled: true,
      schedule_raw: { cron: "0 9 * * *" },
      delivery: { kind: "channel", connector: "feishu", target: "feishu:ou_1" },
      sources: [],
      always_allowed: [],
    },
    runs: [{
      run_id: "run-1",
      task_id: "task-1",
      session_id: "__run__run-1",
      started_at: 1,
      finished_at: 2,
      status: "ok",
      result_text: "Summary complete.",
      artifacts: [],
      error: null,
      delivery_status: "failed",
      delivery_error: "invalid receive_id",
      trigger: "manual",
    }],
  } as any)),
  updateAutomation: vi.fn(async () => ({ ok: true })),
  clearAutomationRuns: vi.fn(async () => ({ ok: true, cleared: 1 })),
  getPersonas: vi.fn(async () => [
    {
      id: "cowork",
      name: "OpenWorker",
      icon: "cowork",
      tagline: "Produce a deliverable",
      needs_workspace: true,
      builtin: true,
      family: "knowledge",
      workspace: "deliverable",
      tools: [],
      enabled: true,
      surfaced: true,
      default: true,
    },
    {
      id: "ops",
      name: "Ops Coworker",
      icon: "wrench",
      tagline: "Operate and investigate",
      needs_workspace: true,
      builtin: true,
      family: "knowledge",
      workspace: "deliverable",
      tools: [],
      enabled: true,
      surfaced: true,
      default: false,
    },
  ]),
}));

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return {
    ...actual,
    getAutomation: api.getAutomation,
    getAutomations: vi.fn(async () => []),
    getConnectors: vi.fn(async () => [
      {
        name: "github",
        title: "GitHub",
        connected: true,
        source_capable: true,
        delivery_capable: false,
      },
      {
        name: "feishu",
        title: "Feishu / Lark",
        connected: true,
        source_capable: false,
        delivery_capable: true,
      },
    ]),
    getRecentChannels: vi.fn(async () => []),
    getPersonas: api.getPersonas,
    markAutomationSeen: vi.fn(async () => ({})),
    updateAutomation: api.updateAutomation,
    clearAutomationRuns: api.clearAutomationRuns,
  };
});

import { ScheduledView } from "./ScheduledView";

afterEach(cleanup);

describe("ScheduledView delivery failures", () => {
  it("shows the delivery error recorded for a failed run", async () => {
    render(<ScheduledView initialOpenId="task-1" onOpenRun={vi.fn()} onRunNow={vi.fn()} />);

    expect(await screen.findByText("Delivery error: invalid receive_id")).toBeTruthy();
  });

  it("saves edited sources and delivery", async () => {
    render(<ScheduledView initialOpenId="task-1" onOpenRun={vi.fn()} onRunNow={vi.fn()} />);

    fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
    fireEvent.click(screen.getByLabelText(/GitHub/));
    fireEvent.change(
      screen.getByPlaceholderText("feishu:<open_id (ou_...) or chat_id (oc_...)>"),
      { target: { value: "oc_destination" } },
    );
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(api.updateAutomation).toHaveBeenCalledWith("task-1", {
        title: "Daily brief",
        instructions: "Send a summary.",
        agent: "cowork",
        run_retention_days: null,
        cron: "0 9 * * *",
        sources: ["github"],
        delivery: {
          kind: "channel",
          connector: "feishu",
          target: "feishu:oc_destination",
        },
      }),
    );
  });

  it("saves the selected enabled agent", async () => {
    api.updateAutomation.mockClear();
    render(<ScheduledView initialOpenId="task-1" onOpenRun={vi.fn()} onRunNow={vi.fn()} />);

    fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
    fireEvent.change(screen.getByLabelText("Run as"), { target: { value: "ops" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(api.updateAutomation).toHaveBeenLastCalledWith("task-1", {
        title: "Daily brief",
        instructions: "Send a summary.",
        agent: "ops",
        run_retention_days: null,
        cron: "0 9 * * *",
        sources: [],
        delivery: {
          kind: "channel",
          connector: "feishu",
          target: "feishu:ou_1",
        },
      }),
    );
  });

  it("loads the next run history page", async () => {
    api.getAutomation.mockClear();
    api.getAutomation
      .mockResolvedValueOnce({
        task: {
          id: "task-1", title: "Daily brief", instructions: "Send a summary.",
          workspace: "/tmp/openworker", agent: "cowork", enabled: true,
          schedule_raw: { cron: "0 9 * * *" },
          delivery: { kind: "channel", connector: "feishu", target: "feishu:ou_1" },
          sources: [], always_allowed: [],
        },
        runs: [{
          run_id: "run-new", task_id: "task-1", session_id: "__run__run-new",
          started_at: 2, finished_at: 2, status: "ok", result_text: "Newest run.",
          artifacts: [], error: null, delivery_status: "sent", delivery_error: "",
          trigger: "manual",
        }],
        total_runs: 2, has_more: true, next_offset: 1,
      })
      .mockResolvedValueOnce({
        task: {
          id: "task-1", title: "Daily brief", instructions: "Send a summary.",
          workspace: "/tmp/openworker", agent: "cowork", enabled: true,
          schedule_raw: { cron: "0 9 * * *" },
          delivery: { kind: "channel", connector: "feishu", target: "feishu:ou_1" },
          sources: [], always_allowed: [],
        },
        runs: [{
          run_id: "run-old", task_id: "task-1", session_id: "__run__run-old",
          started_at: 1, finished_at: 1, status: "ok", result_text: "Older run.",
          artifacts: [], error: null, delivery_status: "sent", delivery_error: "",
          trigger: "schedule",
        }],
        total_runs: 2, has_more: false, next_offset: null,
      });
    render(<ScheduledView initialOpenId="task-1" onOpenRun={vi.fn()} onRunNow={vi.fn()} />);

    fireEvent.click(await screen.findByRole("button", { name: "Load 1 more" }));

    expect(await screen.findByText("Older run.")).toBeTruthy();
    expect(api.getAutomation).toHaveBeenLastCalledWith("task-1", { limit: 20, offset: 1 });
  });

  it("clears completed run history after confirmation", async () => {
    api.clearAutomationRuns.mockClear();
    vi.stubGlobal("confirm", vi.fn(() => true));
    render(<ScheduledView initialOpenId="task-1" onOpenRun={vi.fn()} onRunNow={vi.fn()} />);

    fireEvent.click(await screen.findByRole("button", { name: "Manage history" }));
    fireEvent.click(screen.getByRole("button", { name: "Clear all" }));

    await waitFor(() => expect(api.clearAutomationRuns).toHaveBeenCalledWith("task-1"));
  });

  it("clears only checked completed runs", async () => {
    api.clearAutomationRuns.mockClear();
    vi.stubGlobal("confirm", vi.fn(() => true));
    render(<ScheduledView initialOpenId="task-1" onOpenRun={vi.fn()} onRunNow={vi.fn()} />);

    fireEvent.click(await screen.findByRole("button", { name: "Manage history" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Select run run-1" }));
    fireEvent.click(screen.getByRole("button", { name: "Clear selected (1)" }));

    await waitFor(() =>
      expect(api.clearAutomationRuns).toHaveBeenCalledWith("task-1", ["run-1"]),
    );
  });
});
