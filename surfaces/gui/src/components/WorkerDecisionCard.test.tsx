// A lead's decision on a worker's waiting call gets its own card: the generic approval
// card asked the human to click "Allow" to DENY a command it never showed (owner catch
// 2026-09-17). Payloads come from the card gallery's state files.
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { approvalItemFromPayload } from "../cardPayloads";
import { inboxItemBuilders } from "../gallery/inboxItems";
import { STATES, statePayload } from "../gallery/states";
import { ApprovalCard } from "./ApprovalCard";
import { InboxItemCard } from "./InboxItemCard";
import { WorkerDecisionCard } from "./WorkerDecisionCard";

const state = (id: string) => STATES["worker-decision"].find((s) => s.id === id)!;

describe("WorkerDecisionCard", () => {
  afterEach(cleanup);

  it.each(["reviewer_unsure", "human_required"] as const)("keeps the %s explanation on live and parked lead cards", (kind) => {
    const escalation = { kind, reason: "The exact action needs your judgment." };
    const payload = { ...statePayload("worker-decision", "lead-allows-command"), escalation };
    const view = render(<ApprovalCard item={approvalItemFromPayload(payload)} onApprove={vi.fn()} />);
    expect(screen.getByTestId("approval-escalation").textContent).toContain(escalation.reason);
    view.unmount();
    render(<InboxItemCard item={{ id: "sample", kind: "approval", session_id: "lead", title: "Approval", body: "", state: "pending", created_at: "", data: { ...payload, tool: "decide_worker_call" } } as any} onResolve={vi.fn()} />);
    expect(screen.getByTestId("approval-escalation").textContent).toContain(escalation.reason);
  });

  it.each(["", "This requires access to the project directory.\n"])("does not repeat generated command summaries, retaining policy text (%s)", policy => {
    const command = "cd /workspace/acme/billing-service && npm run build && npm run test -- --run";
    render(<WorkerDecisionCard decision={{ worker: "Maya", callId: "call", decision: "allow", note: "Verify the build." }}
      workerCall={{ tool: "run_shell", arguments: { command, description: "Check the build" },
        reason: `${policy}command: ${command} · description: Check the build` }}
      onFollow={vi.fn()} onOverride={vi.fn()} />);
    const card = screen.getByTestId("workerdec-call");
    expect(card.querySelectorAll(".approval-reason")).toHaveLength(policy ? 1 : 0);
    expect(card.textContent?.split(command)).toHaveLength(2);
    if (policy) expect(card.textContent).toContain(policy.trim());
  });

  it("removes a truncated generated summary for a long command", () => {
    const command = "npm run check " + "--sample-long-option ".repeat(20);
    const summary = command.trim().slice(0, 79) + "…";
    render(<WorkerDecisionCard decision={{ worker: "Maya", callId: "call", decision: "deny", note: "Use the scoped test." }}
      workerCall={{ tool: "run_shell", arguments: { command, description: "Check" }, reason: `command: ${summary} · description: Check` }}
      onFollow={vi.fn()} onOverride={vi.fn()} />);
    expect(screen.getByTestId("workerdec-call").querySelector(".approval-reason")).toBeNull();
  });

  it("shows the worker's command and the lead's reason, never the call id", () => {
    render(<ApprovalCard item={approvalItemFromPayload(statePayload("worker-decision", "lead-denies-command"))} onApprove={vi.fn()} />);
    const card = screen.getByTestId("workerdec-card");
    expect(card.textContent).toContain("The lead wants to deny nia’s command");
    expect(screen.getByTestId("workerdec-call").textContent).toContain("issue-1/web && npm run build");
    expect(screen.getByTestId("workerdec-note").textContent).toContain("already done and merged");
    expect(card.textContent).not.toContain("c51dc4bd");
    expect(card.textContent).not.toContain("requires approval");
  });

  it("following the lead approves the lead's decision and touches nothing else", () => {
    const onApprove = vi.fn();
    const onAnswerWorkerCall = vi.fn();
    render(
      <ApprovalCard
        item={approvalItemFromPayload(statePayload("worker-decision", "lead-denies-command"))}
        onApprove={onApprove}
        onAnswerWorkerCall={onAnswerWorkerCall}
      />,
    );
    expect(screen.getByTestId("workerdec-follow").textContent).toBe("Deny it, as the lead suggests");
    fireEvent.click(screen.getByTestId("workerdec-follow"));
    expect(onApprove).toHaveBeenCalledWith("once");
    expect(onAnswerWorkerCall).not.toHaveBeenCalled();
  });

  it("overriding answers the worker's call with the opposite and declines the lead's", () => {
    const onApprove = vi.fn();
    const onAnswerWorkerCall = vi.fn();
    render(
      <ApprovalCard
        item={approvalItemFromPayload(statePayload("worker-decision", "lead-denies-command"))}
        onApprove={onApprove}
        onAnswerWorkerCall={onAnswerWorkerCall}
      />,
    );
    expect(screen.getByTestId("workerdec-override").textContent).toBe("Allow nia’s command instead");
    fireEvent.click(screen.getByTestId("workerdec-override"));
    expect(onAnswerWorkerCall).toHaveBeenCalledWith("c51dc4bd79f74e87b7fc59050dc41c37", "allow");
    expect(onApprove).toHaveBeenCalledWith("deny");
  });

  it("words the buttons the other way round when the lead allows", () => {
    render(<ApprovalCard item={approvalItemFromPayload(statePayload("worker-decision", "lead-allows-command"))} onApprove={vi.fn()} />);
    expect(screen.getByTestId("workerdec-follow").textContent).toBe("Allow it, as the lead suggests");
    expect(screen.getByTestId("workerdec-override").textContent).toBe("Deny it instead");
  });

  it("says so when the call was already answered, and offers nothing to decide", () => {
    render(<ApprovalCard item={approvalItemFromPayload(statePayload("worker-decision", "already-answered"))} onApprove={vi.fn()} />);
    expect(screen.getByTestId("workerdec-answered").textContent).toContain("already denied");
    expect(screen.queryByTestId("workerdec-override")).toBeNull();
  });

  it("explains the gap when an older server sends no worker call", () => {
    render(<ApprovalCard item={approvalItemFromPayload(statePayload("worker-decision", "old-server"))} onApprove={vi.fn()} />);
    expect(screen.getByTestId("workerdec-missing").textContent).toContain("Open nia’s session");
    expect(screen.getByTestId("workerdec-follow")).toBeTruthy();
  });

  it("parked in the Inbox: follow resolves the lead's item; override resolves the worker's first", () => {
    const onResolve = vi.fn();
    render(<InboxItemCard item={inboxItemBuilders["worker-decision"]!(state("lead-denies-command"))} onResolve={onResolve} />);
    expect(screen.getByTestId("workerdec-card").textContent).not.toContain("call_id");
    fireEvent.click(screen.getByTestId("workerdec-override"));
    expect(onResolve.mock.calls).toEqual([
      ["c51dc4bd79f74e87b7fc59050dc41c37", "allow"],
      ["gallery-item", "deny"],
    ]);
    cleanup();
    const again = vi.fn();
    render(<InboxItemCard item={inboxItemBuilders["worker-decision"]!(state("lead-denies-command"))} onResolve={again} />);
    fireEvent.click(screen.getByTestId("workerdec-follow"));
    expect(again.mock.calls).toEqual([["gallery-item", "allow"]]);
  });
});
