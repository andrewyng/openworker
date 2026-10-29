// OPE-238: the "changes made without asking" summary opens Activity on one tool call's
// audit rows; the call filter is shown and can be cleared.
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AuditView } from "./AuditView";

vi.mock("../api", () => ({
  getAudit: vi.fn(async () => []),
}));

import { getAudit } from "../api";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("AuditView call filter", () => {
  it("loads only the given tool call's rows", async () => {
    render(<AuditView callId="call_9" />);
    await waitFor(() => expect(getAudit).toHaveBeenCalled());
    expect((getAudit as any).mock.calls[0][0]).toMatchObject({ call_id: "call_9" });
    expect(screen.getByTestId("audit-call-filter").textContent).toContain("call_9");
  });

  it("clearing the call filter loads everything again", async () => {
    render(<AuditView callId="call_9" />);
    await waitFor(() => expect(getAudit).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByTestId("audit-call-filter-clear"));
    await waitFor(() => expect(getAudit).toHaveBeenCalledTimes(2));
    expect((getAudit as any).mock.calls[1][0].call_id).toBeUndefined();
    expect(screen.queryByTestId("audit-call-filter")).toBeNull();
  });

  it("without a call filter nothing changes", async () => {
    render(<AuditView />);
    await waitFor(() => expect(getAudit).toHaveBeenCalled());
    expect((getAudit as any).mock.calls[0][0].call_id).toBeUndefined();
    expect(screen.queryByTestId("audit-call-filter")).toBeNull();
  });
});
