// OPE-224: a decision-model reviewer (OpenAI Decisions, TypeSafe Jev) gives no reason, so the
// card says Auto-approve wants a decision, with nothing invented after it.
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import en from "../locales/en.json";
import { ApprovalEscalation } from "./ApprovalEscalation";

describe("ApprovalEscalation", () => {
  afterEach(cleanup);

  it("shows the reviewer_asks wording with no reason", () => {
    render(<ApprovalEscalation escalation={{ kind: "reviewer_asks", reason: "" }} />);
    expect(screen.getByTestId("approval-escalation").textContent).toBe(en.approval.escalation_reviewer_asks);
  });

  it("still appends the session-model reviewer's reason", () => {
    render(<ApprovalEscalation escalation={{ kind: "reviewer_unsure", reason: "Which PR?" }} />);
    expect(screen.getByTestId("approval-escalation").textContent).toContain("Which PR?");
  });
});
