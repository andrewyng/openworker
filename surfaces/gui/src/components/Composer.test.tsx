// Enter-to-send vs. CJK IME composition (owner catch 2026-07-30): pressing Enter to confirm
// a Korean/Japanese/Chinese syllable must not also submit the message, or a duplicate send
// slips out — both a duplicate local bubble and a duplicate turn on the wire (Composer.tsx's
// onKey has no lock of its own; `submit()`'s only guard is `props.running`, which doesn't flip
// until the backend round-trips, so nothing else stops a same-tick double Enter).
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { Composer } from "./Composer";

afterEach(cleanup);

const props = (extra: Partial<Parameters<typeof Composer>[0]> = {}) => ({
  mode: "interactive",
  model: "gpt-5.6-sol",
  running: false,
  connected: true,
  onSend: vi.fn(),
  onInterrupt: vi.fn(),
  onModeChange: vi.fn(),
  onModelChange: vi.fn(),
  ...extra,
});

describe("Composer Enter-to-send / IME composition", () => {
  it("an Enter that only confirms IME composition does not submit", () => {
    const onSend = vi.fn();
    render(<Composer {...props({ onSend })} />);
    const box = screen.getByPlaceholderText(/Ask the coworker/);
    fireEvent.change(box, { target: { value: "안녕하세요" } });

    fireEvent.keyDown(box, { key: "Enter", isComposing: true });
    expect(onSend).not.toHaveBeenCalled();
    expect((box as HTMLTextAreaElement).value).toBe("안녕하세요"); // draft untouched

    // Composition has ended — the next real Enter still sends.
    fireEvent.keyDown(box, { key: "Enter" });
    expect(onSend).toHaveBeenCalledTimes(1);
    expect(onSend).toHaveBeenCalledWith("안녕하세요", []);
  });

  it("a plain Enter (no composition) sends as before", () => {
    const onSend = vi.fn();
    render(<Composer {...props({ onSend })} />);
    const box = screen.getByPlaceholderText(/Ask the coworker/);
    fireEvent.change(box, { target: { value: "hello" } });
    fireEvent.keyDown(box, { key: "Enter" });
    expect(onSend).toHaveBeenCalledTimes(1);
  });

  it("shift+Enter still inserts a newline instead of sending, composing or not", () => {
    const onSend = vi.fn();
    render(<Composer {...props({ onSend })} />);
    const box = screen.getByPlaceholderText(/Ask the coworker/);
    fireEvent.keyDown(box, { key: "Enter", shiftKey: true });
    fireEvent.keyDown(box, { key: "Enter", shiftKey: true, isComposing: true });
    expect(onSend).not.toHaveBeenCalled();
  });
});
