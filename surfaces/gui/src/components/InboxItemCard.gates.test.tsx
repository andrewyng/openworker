// Team and work-item gates parked in the Inbox (a lead on a box, or an unattended lead)
// render the SAME cards as the inline gates, and resolve with the full decision — the
// human's connector ticks and the chat flag — not a bare {approved, mode}
// (owner-hit 2026-09-16: the first SpaceSol team got no connectors and no chat).
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { InboxItem } from "../api";
import { modeLabel } from "./Composer";
import { InboxItemCard } from "./InboxItemCard";

function planItem(data: Record<string, unknown>, title: string): InboxItem {
  return {
    id: "gate-1",
    session_id: "lead-sid",
    kind: "plan",
    title,
    body: "- swe-worker\n- test-worker",
    state: "pending",
    resolution: null,
    inbox: "default",
    created_at: "2026-09-16T00:00:00Z",
    resolved_at: null,
    visibility: "inbox",
    data,
  } as InboxItem;
}

describe("InboxItemCard — gates parked in the Inbox", () => {
  afterEach(cleanup);

  it("renders the staffing card for a team gate and resolves with ticks and the chat flag", () => {
    const onResolve = vi.fn();
    render(
      <InboxItemCard
        item={planItem(
          {
            gate: "team",
            members: [
              {
                persona: "swe-worker",
                name: "nia",
                reason: "builds it",
                connectors: ["github"],
                connector_reasons: { github: "pushes the branch" },
              },
              { persona: "test-worker", name: "checks", reason: "verifies it" },
            ],
            enable_chat: false,
            offer: { "swe-worker": ["github"], "test-worker": [] },
          },
          "Create this team?",
        )}
        onResolve={onResolve}
      />,
    );
    expect(screen.getByTestId("teamreq-card")).toBeTruthy();
    // The lead's suggestion arrives ticked, with its reason — no click needed.
    const github = screen.getByTestId("teamreq-connector-0-github") as HTMLInputElement;
    expect(github.checked).toBe(true);
    expect(screen.getByText("pushes the branch")).toBeTruthy();
    // Nothing else is connected: no expander, and the empty default set says so.
    expect(screen.queryByTestId("teamreq-add-0")).toBeNull();
    expect(screen.getByTestId("teamreq-row-1").textContent).toContain("none available");
    // The body repeats the card (it exists for Slack) — not printed above it.
    expect(screen.queryByText(/- swe-worker/)).toBeNull();
    fireEvent.click(screen.getByTestId("teamreq-chat-toggle"));
    fireEvent.click(screen.getByTestId("teamreq-approve"));
    expect(onResolve).toHaveBeenCalledTimes(1);
    const [id, resolution] = onResolve.mock.calls[0];
    expect(id).toBe("gate-1");
    const parsed = JSON.parse(resolution);
    expect(parsed.approved).toBe(true);
    expect(parsed.enable_chat).toBe(true);
    expect(parsed.members).toEqual([
      { persona: "swe-worker", name: "nia", connectors: ["github"] },
      { persona: "test-worker", name: "checks", connectors: [] },
    ]);
  });

  it("extends a worker beyond its default set: the lead's quoted ask and a human addition", () => {
    const onResolve = vi.fn();
    render(
      <InboxItemCard
        item={planItem(
          {
            gate: "team",
            members: [
              {
                persona: "swe-worker",
                name: "nia",
                reason: "builds it",
                // slack is connected nowhere on this machine — ignored outright
                connectors: ["jira", "slack"],
                connector_reasons: { jira: 'you asked: "Track the work in Jira"' },
              },
            ],
            offer: { "swe-worker": ["github"] },
            other_connected: ["jira", "linear"],
          },
          "Create this team?",
        )}
        onResolve={onResolve}
      />,
    );
    const beyond = screen.getByTestId("teamreq-beyond-0");
    expect(beyond.textContent).toContain("Beyond this worker's usual set");
    expect(beyond.textContent).toContain('you asked: "Track the work in Jira"');
    expect((screen.getByTestId("teamreq-connector-0-jira") as HTMLInputElement).checked).toBe(true);
    // No suggestion in the default set → unticked; an unconnected suggestion is not shown.
    expect((screen.getByTestId("teamreq-connector-0-github") as HTMLInputElement).checked).toBe(false);
    expect(screen.queryByTestId("teamreq-connector-0-slack")).toBeNull();
    // The expander lists what is left, unticked; ticking moves it into the group, ticked.
    expect(screen.queryByTestId("teamreq-connector-0-linear")).toBeNull();
    fireEvent.click(screen.getByTestId("teamreq-add-0"));
    const linear = screen.getByTestId("teamreq-connector-0-linear") as HTMLInputElement;
    expect(linear.checked).toBe(false);
    fireEvent.click(linear);
    expect(beyond.contains(screen.getByTestId("teamreq-connector-0-linear"))).toBe(true);
    expect((screen.getByTestId("teamreq-connector-0-linear") as HTMLInputElement).checked).toBe(true);
    // Nothing left to add → the expander goes away.
    expect(screen.queryByTestId("teamreq-add-0")).toBeNull();
    fireEvent.click(screen.getByTestId("teamreq-approve"));
    const parsed = JSON.parse(onResolve.mock.calls[0][1]);
    expect(parsed.members).toHaveLength(1);
    expect([...parsed.members[0].connectors].sort()).toEqual(["jira", "linear"]);
  });

  it("forwards each worker's chosen model in the resolution", () => {
    const onResolve = vi.fn();
    render(
      <InboxItemCard
        item={planItem(
          {
            gate: "team",
            members: [
              { persona: "swe-worker", name: "nia", resolved_model: "anthropic:claude-opus-4-8" },
              {
                persona: "test-worker",
                name: "checks",
                resolved_model: "anthropic:claude-sonnet-4-8",
                model_warning: "None of test-worker's recommended models can run here.",
              },
            ],
            offer: {},
            model_options: { "swe-worker": ["anthropic:claude-opus-4-8"], "test-worker": [] },
            runnable_models: [
              { id: "anthropic:claude-opus-4-8", label: "Claude Opus 4.8" },
              { id: "anthropic:claude-sonnet-4-8", label: "Claude Sonnet 4.8" },
              { id: "ollama:qwen3", label: "Qwen 3 (local)" },
            ],
            lead_model: "anthropic:claude-sonnet-4-8",
          },
          "Create this team?",
        )}
        onResolve={onResolve}
      />,
    );
    expect(screen.getByTestId("teamreq-model-warn-1")).toBeTruthy();
    fireEvent.change(screen.getByTestId("teamreq-model-1"), { target: { value: "ollama:qwen3" } });
    fireEvent.click(screen.getByTestId("teamreq-approve"));
    const parsed = JSON.parse(onResolve.mock.calls[0][1]);
    expect(parsed.members).toEqual([
      { persona: "swe-worker", name: "nia", connectors: [], model: "anthropic:claude-opus-4-8" },
      { persona: "test-worker", name: "checks", connectors: [], model: "ollama:qwen3" },
    ]);
  });

  it("does not print the plain body above a team or items gate card", () => {
    render(
      <InboxItemCard
        item={planItem(
          { gate: "team", members: [{ persona: "swe-worker", name: "nia" }], offer: {} },
          "Create this team?",
        )}
        onResolve={vi.fn()}
      />,
    );
    expect(screen.getByTestId("teamreq-card")).toBeTruthy();
    expect(screen.queryByText(/- swe-worker/)).toBeNull();
    expect(screen.queryByText(/- test-worker/)).toBeNull();
    cleanup();
    render(
      <InboxItemCard
        item={planItem({ gate: "items", items: [{ title: "Cap it", criteria: "capped" }] }, "Approve?")}
        onResolve={vi.fn()}
      />,
    );
    expect(screen.queryByText(/- swe-worker/)).toBeNull();
  });

  it("states the lead's real mode only when the gate carries it", () => {
    const data = { gate: "team", members: [{ persona: "swe-worker", name: "nia" }], offer: {} };
    render(<InboxItemCard item={planItem(data, "Create this team?")} onResolve={vi.fn()} />);
    expect(screen.queryByText(/Approvals follow the lead/)).toBeNull();
    expect(screen.queryByTestId("teamreq-approvals")).toBeNull();
    cleanup();
    render(
      <InboxItemCard
        item={planItem({ ...data, lead_mode: "auto-approve" }, "Create this team?")}
        onResolve={vi.fn()}
      />,
    );
    const line = screen.getByTestId("teamreq-approvals");
    expect(line.textContent).toContain("Approvals follow the lead");
    expect(line.textContent).toContain(modeLabel("auto-approve"));
  });

  it("renders the work-items card for an items gate and resolves with approved", () => {
    const onResolve = vi.fn();
    render(
      <InboxItemCard
        item={planItem(
          { gate: "items", items: [{ title: "Cap the backoff", criteria: "no wait exceeds maxDelayMs" }] },
          "Approve the proposed work items?",
        )}
        onResolve={onResolve}
      />,
    );
    expect(screen.getByTestId("itemsreq-card")).toBeTruthy();
    fireEvent.click(screen.getByTestId("itemsreq-approve"));
    expect(JSON.parse(onResolve.mock.calls[0][1])).toEqual({ approved: true, feedback: "" });
  });

  it("keeps the plain Approve/Reject for a plan gate without a payload", () => {
    const onResolve = vi.fn();
    render(<InboxItemCard item={planItem({}, "Approve the plan?")} onResolve={onResolve} />);
    expect(screen.queryByTestId("teamreq-card")).toBeNull();
    // A plain plan gate has no card of its own — its body still shows.
    expect(screen.getByText(/- swe-worker/)).toBeTruthy();
    fireEvent.click(screen.getByText("Approve"));
    expect(JSON.parse(onResolve.mock.calls[0][1])).toEqual({ approved: true, mode: "interactive" });
  });

  it("offers Install on a parked tool request and resolves with approved", () => {
    const onResolve = vi.fn();
    const item = {
      ...planItem({ tool: "gitleaks", installable: true, version: "8.30.1", summary: "scans for secrets", source: "github.com/gitleaks" }, "Install gitleaks?"),
      kind: "tool",
      body: "scan the git history for committed secrets",
    } as InboxItem;
    render(<InboxItemCard item={item} onResolve={onResolve} />);
    // It used to fall through to a lone "Dismiss", which the server reads as a decline.
    expect(screen.queryByText("Dismiss")).toBeNull();
    expect(screen.getByText(/scan the git history/)).toBeTruthy();
    fireEvent.click(screen.getByTestId("toolreq-install"));
    expect(JSON.parse(onResolve.mock.calls[0][1])).toEqual({ approved: true });
    cleanup();
    // No pinned build → Install stays disabled; declining is still a plain answer.
    render(<InboxItemCard item={{ ...item, data: { tool: "somescanner" } } as InboxItem} onResolve={onResolve} />);
    expect((screen.getByTestId("toolreq-install") as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByTestId("toolreq-skip"));
    expect(JSON.parse(onResolve.mock.calls[1][1])).toEqual({ approved: false });
  });
});
