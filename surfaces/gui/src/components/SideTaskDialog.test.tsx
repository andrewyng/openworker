// "+ ▸ Side task…" form: Start stays disabled until both fields have text, submits the
// trimmed title and brief, shows the server's refusal, and cancels on Escape.
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { SideTaskDialog } from "./SideTaskDialog";

afterEach(cleanup);

describe("SideTaskDialog", () => {
  it("enables Start only with a name and a brief, then submits them trimmed", async () => {
    const onSubmit = vi.fn(async () => ({ ok: true }));
    render(<SideTaskDialog onSubmit={onSubmit} onCancel={vi.fn()} />);
    const start = screen.getByTestId("side-task-start") as HTMLButtonElement;
    expect(start.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "  Pi 4 study " } });
    expect(start.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("Brief"), { target: { value: " How would the design change on a Raspberry Pi 4? " } });
    expect(start.disabled).toBe(false);
    fireEvent.click(start);
    await waitFor(() => expect(onSubmit).toHaveBeenCalledWith("Pi 4 study", "How would the design change on a Raspberry Pi 4?"));
  });

  it("shows the server's refusal and keeps the form open", async () => {
    const onSubmit = vi.fn(async () => ({ ok: false, error: "3 side tasks are already running for this chat." }));
    render(<SideTaskDialog onSubmit={onSubmit} onCancel={vi.fn()} />);
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Fourth" } });
    fireEvent.change(screen.getByLabelText("Brief"), { target: { value: "brief" } });
    fireEvent.click(screen.getByTestId("side-task-start"));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toMatch(/already running/));
    expect(screen.getByTestId("side-task-dialog")).toBeTruthy();
  });

  it("cancels on Escape", () => {
    const onCancel = vi.fn();
    render(<SideTaskDialog onSubmit={vi.fn(async () => ({ ok: true }))} onCancel={onCancel} />);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onCancel).toHaveBeenCalled();
  });
});
