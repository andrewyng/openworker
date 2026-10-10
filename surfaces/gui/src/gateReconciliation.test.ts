import { describe, expect, it } from "vitest";
import { approvalItemFromPayload, teamItemFromPayload, workItemsItemFromPayload } from "./cardPayloads";
import { reconcileResolvedGates, retireFinishedGate } from "./gateReconciliation";
import type { InboxItem } from "./api";

describe("cross-surface gate reconciliation", () => {
  const old = teamItemFromPayload({ tool_call_id: "old", members: [] });
  const newer = teamItemFromPayload({ tool_call_id: "new", members: [] });
  const split = workItemsItemFromPayload({ tool_call_id: "split", items: [] });
  const legacy = teamItemFromPayload({ members: [] });

  it("retains server identity and retires only the exact completed proposal", () => {
    expect(old.toolCallId).toBe("old");
    expect(retireFinishedGate([old, newer, split, legacy], "propose_team", "old"))
      .toEqual([newer, split, legacy]);
    expect(retireFinishedGate([old, split], "propose_work_items", "split")).toEqual([old]);
  });
  it("does not guess for old servers, unrelated results, or unknown IDs", () => {
    const items = [old, newer, legacy];
    expect(retireFinishedGate(items, "propose_team")).toBe(items);
    expect(retireFinishedGate(items, "run_shell", "old")).toBe(items);
    expect(retireFinishedGate(items, "propose_team", "missing")).toEqual(items);
  });
  it.each([JSON.stringify({ approved: true }), JSON.stringify({ approved: false }), "interrupted"])(
    "reconciles a persisted resolution (%s) without dismissing a pending replacement", resolution => {
      const inbox = [
        { state: "resolved", tool_call_id: "old", resolution },
        { state: "pending", tool_call_id: "new" },
      ] as InboxItem[];
      expect(reconcileResolvedGates([old, newer, split, legacy], inbox)).toEqual([newer, split, legacy]);
    },
  );
  it("does not infer resolution from a missing Inbox row", () => {
    const items = [old, split, legacy];
    expect(reconcileResolvedGates(items, [])).toBe(items);
    expect(reconcileResolvedGates(items, [{ state: "resolved" }] as InboxItem[])).toBe(items);
  });
  it("matches ordinary approvals by exact call ID, never tool name alone", () => {
    const a = approvalItemFromPayload({ name: "github_clone", tool_call_id: "clone-old" });
    const b = approvalItemFromPayload({ name: "github_clone", tool_call_id: "clone-new" });
    const unknown = approvalItemFromPayload({ name: "github_clone" });
    expect(retireFinishedGate([a, b, unknown], "github_clone", "clone-old")).toEqual([b, unknown]);
    expect(retireFinishedGate([a, b], "github_clone")).toEqual([a, b]);
    expect(retireFinishedGate([a], "run_shell", "clone-old")).toEqual([a]);
  });
  it.each(["allow", "deny", "interrupted"])("poll retires a resolved approval (%s) only", resolution => {
    const a = approvalItemFromPayload({ name: "run_shell", tool_call_id: "a" });
    const b = approvalItemFromPayload({ name: "run_shell", tool_call_id: "b" });
    expect(reconcileResolvedGates([a, b], [
      { state: "resolved", tool_call_id: "a", resolution },
      { state: "pending", tool_call_id: "b" },
    ] as InboxItem[])).toEqual([b]);
    expect(reconcileResolvedGates([b], [])).toEqual([b]);
  });
});
