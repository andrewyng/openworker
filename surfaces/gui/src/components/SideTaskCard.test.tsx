// The side task report card: fresh cards open with the summary and the user's three
// choices; a decided card folds to its line and offers only "Open side chat".
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { SideTaskCard, type SideTaskItem } from "./SideTaskCard";

const item: SideTaskItem = {
  kind: "sidetask",
  id: "r1",
  title: "Hoop sensing",
  summary: "Three methods: break-beam, net-motion, vision.",
  reportPath: "side-tasks/hoops.md",
  childSession: "c1",
  status: "new",
};

afterEach(cleanup);

describe("SideTaskCard", () => {
  it("opens a fresh card with the summary, the report path and all three actions", () => {
    const onAction = vi.fn();
    render(<SideTaskCard item={item} onAction={onAction} />);
    expect(screen.getByText("Hoop sensing")).toBeTruthy();
    expect(screen.getByText(/break-beam/)).toBeTruthy();
    expect(screen.getByText("side-tasks/hoops.md")).toBeTruthy();
    fireEvent.click(screen.getByTestId("sidetask-add"));
    expect(onAction).toHaveBeenLastCalledWith(item, "add");
    fireEvent.click(screen.getByTestId("sidetask-dismiss"));
    expect(onAction).toHaveBeenLastCalledWith(item, "dismiss");
    fireEvent.click(screen.getByTestId("sidetask-open"));
    expect(onAction).toHaveBeenLastCalledWith(item, "open");
  });

  it("folds a decided card to its line with the verdict; expanding offers only Open", () => {
    render(<SideTaskCard item={{ ...item, status: "added" }} />);
    expect(screen.getByTestId("sidetask-verdict").textContent).toBe("Added to chat");
    expect(screen.queryByText(/break-beam/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { expanded: false }));
    expect(screen.getByText(/break-beam/)).toBeTruthy();
    expect(screen.queryByTestId("sidetask-add")).toBeNull();
    expect(screen.queryByTestId("sidetask-dismiss")).toBeNull();
    expect(screen.getByTestId("sidetask-open")).toBeTruthy();
  });

  it("shows the dismissed verdict", () => {
    render(<SideTaskCard item={{ ...item, status: "dismissed" }} />);
    expect(screen.getByTestId("sidetask-verdict").textContent).toBe("Dismissed");
  });
});
