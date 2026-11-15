import { StrictMode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AUTOMATIONS_CHANGED, type Automation } from "../api";
import { ScheduledView } from "./ScheduledView";

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

const morningBrief: Automation = {
  id: "task-brief",
  title: "Morning brief",
  instructions: "Prepare a brief",
  schedule: "Daily",
  workspace: "/w",
  agent: "cowork",
  enabled: true,
  next_run: null,
  last_run: null,
  last_status: null,
  run_count: 0,
  notify_on_completion: false,
  always_allowed: [],
};

const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status });
const routeLookup = vi.fn<(signal: AbortSignal) => Promise<Response>>();
const detail = vi.fn<(path: string, signal?: AbortSignal | null) => Promise<Response>>();
const seen = vi.fn<(path: string) => void>();
const props = { onOpenRun: vi.fn(), onRunNow: vi.fn() };

function deferredResponse() {
  let resolve!: (value: Response) => void;
  const promise = new Promise<Response>((done) => { resolve = done; });
  return { promise, resolve };
}

beforeEach(() => {
  routeLookup.mockReset().mockImplementation(async () => json({ tasks: [] }));
  detail.mockReset();
  seen.mockReset();
  vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
    const path = new URL(url).pathname;
    if (path === "/v1/automations") return init?.signal ? routeLookup(init.signal) : json({ tasks: [] });
    if (path === "/v1/machines") return json({ machines: [] });
    if (path.endsWith("/seen")) {
      seen(path);
      return json({ ok: true });
    }
    return detail(path, init?.signal);
  });
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
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

describe("ScheduledView detail", () => {
  it("allows another retry after two failures and marks the task seen after loading it", async () => {
    detail
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValueOnce(json({ error: "temporary failure" }, 500))
      .mockResolvedValueOnce(json({ task: morningBrief, runs: [] }));
    render(<ScheduledView {...props} initialOpenId="task-brief" />);

    expect((await screen.findByRole("alert")).textContent).toContain("Could not load this automation.");
    expect(seen).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect((await screen.findByRole("alert")).textContent).toContain("Could not load this automation.");
    expect(seen).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));

    await screen.findByRole("heading", { name: "Morning brief" });
    expect(screen.queryByRole("alert")).toBeNull();
    await waitFor(() => expect(seen.mock.calls).toEqual([["/v1/automations/task-brief/seen"]]));
  });

  it("aborts a stalled detail request and lets the user retry", async () => {
    vi.useFakeTimers();
    detail.mockImplementationOnce((_path, signal) => new Promise((_resolve, reject) => {
      signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
    }));
    detail.mockResolvedValueOnce(json({ task: morningBrief, runs: [] }));
    render(<ScheduledView {...props} initialOpenId="task-brief" />);
    await act(async () => {});
    expect(screen.getByText("Loading…")).toBeTruthy();

    await act(async () => { await vi.advanceTimersByTimeAsync(15_000); });
    expect(screen.getByRole("alert")).toBeTruthy();
    expect(detail.mock.calls[0][1]?.aborted).toBe(true);

    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Try again" })); });
    expect(screen.getByRole("heading", { name: "Morning brief" })).toBeTruthy();
  });

  it("aborts an earlier request and ignores its late result after selecting another task", async () => {
    const first = deferredResponse();
    detail.mockReturnValueOnce(first.promise);
    const weeklyDigest = { ...morningBrief, id: "task-digest", title: "Weekly digest" };
    detail.mockResolvedValueOnce(json({ task: weeklyDigest, runs: [] }));
    const { rerender } = render(<ScheduledView {...props} initialOpenId="task-brief" />);
    await waitFor(() => expect(detail).toHaveBeenCalledTimes(1));

    rerender(<ScheduledView {...props} initialOpenId="task-digest" />);
    await screen.findByRole("heading", { name: "Weekly digest" });
    await act(async () => { first.resolve(json({ task: morningBrief, runs: [] })); });

    expect(detail.mock.calls[0][1]?.aborted).toBe(true);
    expect(screen.getByRole("heading", { name: "Weekly digest" })).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "Morning brief" })).toBeNull();
    expect(seen.mock.calls).toEqual([["/v1/automations/task-digest/seen"]]);
  });

  it("returns to the overview when the server reports a deleted task", async () => {
    detail.mockResolvedValueOnce(json({ error: "not found" }));
    render(<ScheduledView {...props} initialOpenId="task-deleted" />);

    await screen.findByRole("heading", { name: "Automations" });
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("keeps new-run highlights when marking the loaded task seen", async () => {
    let storedMark = 100;
    detail.mockImplementation(async () => json({
      task: { ...morningBrief, seen_runs_at: storedMark },
      runs: [{
        run_id: "r1", task_id: morningBrief.id, session_id: "__run__r1", started_at: 200,
        finished_at: 201, status: "ok", result_text: null, artifacts: [], error: null, trigger: "schedule",
      }],
    }));
    seen.mockImplementation(() => { storedMark = 300; });
    const announced = vi.fn();
    window.addEventListener(AUTOMATIONS_CHANGED, announced);
    try {
      render(<ScheduledView {...props} initialOpenId="task-brief" />);
      await waitFor(() => expect(announced).toHaveBeenCalledTimes(1));
      expect(screen.getByTestId("run-new")).toBeTruthy();
      expect(seen.mock.calls).toEqual([["/v1/automations/task-brief/seen"]]);
    } finally {
      window.removeEventListener(AUTOMATIONS_CHANGED, announced);
    }
  });

  it("discards the canceled StrictMode lookup before marking the active detail seen", async () => {
    const discarded = deferredResponse();
    const active = deferredResponse();
    routeLookup.mockReturnValueOnce(discarded.promise).mockReturnValueOnce(active.promise);
    detail.mockResolvedValueOnce(json({ task: morningBrief, runs: [] }));
    render(<StrictMode><ScheduledView {...props} initialOpenId="task-brief" /></StrictMode>);

    await act(async () => { discarded.resolve(json({ tasks: [] })); });
    expect(routeLookup.mock.calls[0][0].aborted).toBe(true);
    expect(detail).not.toHaveBeenCalled();
    expect(seen).not.toHaveBeenCalled();

    await act(async () => { active.resolve(json({ tasks: [] })); });
    expect(screen.getByRole("heading", { name: "Morning brief" })).toBeTruthy();
    expect(seen.mock.calls).toEqual([["/v1/automations/task-brief/seen"]]);
  });

  it("cancels an unread detail when the user goes back to the overview", async () => {
    const pending = deferredResponse();
    detail.mockReturnValueOnce(pending.promise);
    render(<ScheduledView {...props} initialOpenId="task-brief" />);
    await waitFor(() => expect(detail).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole("button", { name: "← Automations" }));
    await act(async () => { pending.resolve(json({ task: morningBrief, runs: [] })); });

    expect(detail.mock.calls[0][1]?.aborted).toBe(true);
    expect(screen.getByRole("heading", { name: "Automations" })).toBeTruthy();
    expect(seen).not.toHaveBeenCalled();
  });
});
