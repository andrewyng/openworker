// OPE-238: a finished event pairs with its own proposed item. Two same-named calls in one
// batch finish in call order; matching the LAST pending item by name handed each one the
// other's result, exit code, origin and audit link (seen in review, 28 Oct 2026).
import { describe, expect, it } from "vitest";
import { updateLastTool } from "./App";
import type { Item } from "./types";

const pending = (): Item[] => [
  { kind: "tool", id: "a", callId: "c_pytest", name: "run_shell", args: { command: "pytest -q" }, status: "…" },
  { kind: "tool", id: "b", callId: "c_rm", name: "run_shell", args: { command: "rm -rf build" }, status: "…" },
];

describe("updateLastTool", () => {
  it("matches by call id, so same-named calls keep their own results", () => {
    let items = updateLastTool(pending(), "run_shell", "ok", "pytest result", { callId: "c_pytest", approvalOrigin: "user", exitCode: 0 });
    items = updateLastTool(items, "run_shell", "ok", "rm result", { callId: "c_rm", approvalOrigin: "reviewer", exitCode: 1 });
    const [a, b] = items as any[];
    expect(a).toMatchObject({ args: { command: "pytest -q" }, preview: "pytest result", approvalOrigin: "user", exitCode: 0 });
    expect(b).toMatchObject({ args: { command: "rm -rf build" }, preview: "rm result", approvalOrigin: "reviewer", exitCode: 1 });
  });

  it("falls back to the last pending item with that name when there is no id", () => {
    const items = updateLastTool(pending(), "run_shell", "ok", "x", {});
    expect((items[1] as any).status).toBe("ok");
    expect((items[0] as any).status).toBe("…");
  });

  it("keeps a null exit code as unknown and drops absent flags", () => {
    const [a] = updateLastTool(pending(), "run_shell", "ok", "x", { callId: "c_pytest", exitCode: null, timedOut: true }) as any[];
    expect(a.exitCode).toBeNull();
    expect(a.timedOut).toBe(true);
    expect("background" in a).toBe(false);
  });
});
