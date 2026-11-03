// OPE-237: in auto-approve the plan card lists the plan's steps, marks the risky ones,
// and offers approve-all or send-back. Plan mode's card is unchanged.
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { PlanCard } from "./PlanCard";
import { planItemFromPayload } from "../cardPayloads";

afterEach(cleanup);

const AUTO = planItemFromPayload({
  plan: "Do the three tasks in TODO.md.",
  auto_approve: true,
  steps: [
    { kind: "write", target: "summary.txt", note: "a two-sentence summary of notes.txt", risky: false },
    { kind: "delete", target: "old-notes.txt", risky: true },
    { kind: "send", target: "dummy.csv", destination: "httpbin.org", risky: true },
  ],
});

describe("PlanCard in auto-approve (OPE-237)", () => {
  it("lists every step, with the risky ones marked", () => {
    render(<PlanCard item={AUTO} onRespond={vi.fn()} />);
    const rows = screen.getAllByTestId("plan-step");
    expect(rows.length).toBe(3);
    expect(rows[0].textContent).toContain("summary.txt");
    expect(within(rows[0]).getByTestId("plan-step-note").textContent).toBe("a two-sentence summary of notes.txt");
    expect(within(rows[0]).queryByTestId("plan-step-risky")).toBeNull();
    expect(within(rows[1]).queryByTestId("plan-step-note")).toBeNull();
    expect(within(rows[1]).getByTestId("plan-step-risky")).toBeTruthy();
    expect(rows[2].textContent).toContain("dummy.csv");
    expect(rows[2].textContent).toContain("httpbin.org");
    expect(within(rows[2]).getByTestId("plan-step-risky")).toBeTruthy();
    // The summary still shows, as text.
    expect(screen.getByText("Do the three tasks in TODO.md.")).toBeTruthy();
  });

  it("says what approving does", () => {
    render(<PlanCard item={AUTO} onRespond={vi.fn()} />);
    expect(screen.getByTestId("plan-steps-note").textContent).toContain("for this request");
  });

  it("approves all, with no mode choice", () => {
    const onRespond = vi.fn();
    render(<PlanCard item={AUTO} onRespond={onRespond} />);
    expect(screen.queryByText("Approve — ask per step")).toBeNull();
    expect(screen.queryByText("Approve & run")).toBeNull();
    fireEvent.click(screen.getByTestId("plan-approve"));
    expect(onRespond).toHaveBeenCalledWith(true, undefined);
  });

  it("sends back with feedback", () => {
    const onRespond = vi.fn();
    render(<PlanCard item={AUTO} onRespond={onRespond} />);
    fireEvent.click(screen.getByText("Request changes"));
    fireEvent.change(screen.getByPlaceholderText("What should change about the plan?"), {
      target: { value: "skip the upload" },
    });
    fireEvent.click(screen.getByText("Send feedback"));
    expect(onRespond).toHaveBeenCalledWith(false, undefined, "skip the upload");
  });
});

describe("PlanCard in plan mode (unchanged)", () => {
  it("keeps its two approve choices and shows no step list", () => {
    const onRespond = vi.fn();
    render(<PlanCard item={planItemFromPayload({ plan: "Refactor the parser." })} onRespond={onRespond} />);
    expect(screen.queryAllByTestId("plan-step").length).toBe(0);
    fireEvent.click(screen.getByText("Approve & run"));
    expect(onRespond).toHaveBeenCalledWith(true, "auto");
  });
});

describe("planItemFromPayload", () => {
  it("keeps well-formed steps and drops the rest", () => {
    const item = planItemFromPayload({
      plan: "x",
      auto_approve: true,
      steps: [{ kind: "run", target: "pytest -q", risky: false }, { kind: 3 }, "nope", null],
    });
    expect(item.steps).toEqual([{ kind: "run", target: "pytest -q", destination: undefined, note: undefined, risky: false }]);
    expect(item.autoApprove).toBe(true);
    expect(planItemFromPayload({ plan: "x" }).steps).toBeUndefined();
  });
});
