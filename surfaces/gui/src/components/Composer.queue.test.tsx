// #608 message queueing — while a turn runs, submitting holds the follow-up via `onQueue`
// (App auto-sends it when the turn ends) instead of dropping it; the composer rings the
// "N queued" pill so the user can see what's waiting. `onQueue` is additive: without it the
// composer keeps the exact old Stop-only behavior, so existing tests stay green.
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { Composer } from "./Composer";

const props = (extra: Partial<Parameters<typeof Composer>[0]> = {}) => ({
  mode: "interactive",
  model: "gpt-5.6-sol",
  running: false,
  connected: true,
  sessionId: "s1",
  onSend: vi.fn(),
  onInterrupt: vi.fn(),
  onModeChange: vi.fn(),
  onModelChange: vi.fn(),
  ...extra,
});

const box = () => screen.getByPlaceholderText(/Ask the coworker/);

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("Composer / message queue (#608)", () => {
  it("while running, submit holds the follow-up via onQueue and clears the draft (no onSend)", () => {
    const p = props({ running: true, gateOpen: false, onQueue: vi.fn() });
    render(<Composer {...p} />);
    fireEvent.change(box(), { target: { value: "follow up after the task" } });
    const queueBtn = screen.getByRole("button", { name: /queue/i });
    expect(queueBtn).toBeTruthy();
    fireEvent.click(queueBtn);
    expect(p.onQueue).toHaveBeenCalledTimes(1);
    expect(p.onQueue).toHaveBeenCalledWith("follow up after the task", [], undefined);
    expect(p.onSend).not.toHaveBeenCalled();
    expect((box() as HTMLTextAreaElement).value).toBe("");
  });

  it("without an onQueue handler, the running state renders Stop-only (old behavior)", () => {
    const p = props({ running: true, gateOpen: false });
    render(<Composer {...p} />);
    expect(screen.getByRole("button", { name: /stop/i })).toBeTruthy();
    // No queue-send affordance when queueing isn't wired.
    expect(screen.queryByRole("button", { name: /queue/i })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /stop/i }));
    expect(p.onInterrupt).toHaveBeenCalledTimes(1);
    expect(p.onSend).not.toHaveBeenCalled();
  });

  it("the queued-count pill renders when queuedCount > 0 and only then", () => {
    const p = props({ running: true, gateOpen: false, onQueue: vi.fn(), queuedCount: 2 });
    render(<Composer {...p} />);
    expect(screen.getByTestId("queued-count")).toBeTruthy();
    expect(screen.getByTestId("queued-count").textContent).toMatch(/2/);
  });

  it("not running: submit still sends via onSend and never calls onQueue", () => {
    const p = props({ running: false, gateOpen: false, onQueue: vi.fn() });
    render(<Composer {...p} />);
    fireEvent.change(box(), { target: { value: "plain message" } });
    fireEvent.click(screen.getByRole("button", { name: /send/i }));
    expect(p.onSend).toHaveBeenCalledWith("plain message", [], undefined);
    expect(p.onQueue).not.toHaveBeenCalled();
  });
});
