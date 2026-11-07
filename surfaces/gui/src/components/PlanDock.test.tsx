// The plan dock: collapsed carries the live step and count; the card is on demand;
// a finished plan folds itself back to the quiet line.
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { TodoItem } from "../types";
import { PlanDock } from "./PlanDock";

const plan = (statuses: TodoItem["status"][]): TodoItem[] =>
  statuses.map((status, i) => ({ content: `step ${i + 1}`, status }));

afterEach(cleanup);

describe("PlanDock", () => {
  it("renders nothing without a plan", () => {
    render(<PlanDock todo={[]} />);
    expect(screen.queryByTestId("plan-dock")).toBeNull();
  });

  it("collapsed: shows the in-progress step and the count", () => {
    render(<PlanDock todo={plan(["done", "done", "in_progress", "pending"])} />);
    const head = screen.getByTestId("plan-dock-toggle");
    expect(head.textContent).toContain("step 3");
    expect(head.textContent).toContain("2 of 4");
    expect(screen.queryByTestId("plan-dock-steps")).toBeNull();
  });

  it("expanded: lists every step with its state", () => {
    render(<PlanDock todo={plan(["done", "in_progress", "pending"])} />);
    fireEvent.click(screen.getByTestId("plan-dock-toggle"));
    const steps = screen.getByTestId("plan-dock-steps");
    expect(steps.querySelectorAll(".plandock-step").length).toBe(3);
    expect(steps.querySelector(".plandock-step.done")?.textContent).toContain("step 1");
    expect(steps.querySelector(".plandock-step.in_progress")?.textContent).toContain("step 2");
    expect(steps.querySelector(".plandock-step.pending")?.textContent).toContain("step 3");
  });

  it("auto-collapses and says so once every step is done", () => {
    const { rerender } = render(<PlanDock todo={plan(["done", "in_progress"])} />);
    fireEvent.click(screen.getByTestId("plan-dock-toggle"));
    expect(screen.getByTestId("plan-dock-steps")).toBeTruthy();
    rerender(<PlanDock todo={plan(["done", "done"])} />);
    expect(screen.queryByTestId("plan-dock-steps")).toBeNull();
    expect(screen.getByTestId("plan-dock-toggle").textContent).toContain("All steps done");
  });
});
