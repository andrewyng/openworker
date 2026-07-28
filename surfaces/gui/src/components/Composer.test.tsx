import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { Composer } from "./Composer";

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

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("Composer Enter behavior", () => {
  it("defaults to send on Enter and newline on Shift+Enter", () => {
    const onSend = vi.fn();
    render(<Composer {...props({ onSend })} />);

    const box = screen.getByPlaceholderText(/Ask the coworker/) as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: "hello" } });
    fireEvent.keyDown(box, { key: "Enter" });
    expect(onSend).toHaveBeenCalledWith("hello", []);

    fireEvent.change(box, { target: { value: "line" } });
    fireEvent.keyDown(box, { key: "Enter", shiftKey: true });
    expect(onSend).toHaveBeenCalledTimes(1);
  });

  it("supports newline on Enter and send on Shift+Enter", () => {
    const onSend = vi.fn();
    render(<Composer {...props({ onSend, enterBehavior: "newline" })} />);

    const box = screen.getByPlaceholderText(/Ask the coworker/) as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: "hello" } });
    fireEvent.keyDown(box, { key: "Enter" });
    expect(onSend).not.toHaveBeenCalled();

    fireEvent.keyDown(box, { key: "Enter", shiftKey: true });
    expect(onSend).toHaveBeenCalledWith("hello", []);
  });

  it("does not send while IME composition is active", () => {
    const onSend = vi.fn();
    render(<Composer {...props({ onSend })} />);

    const box = screen.getByPlaceholderText(/Ask the coworker/) as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: "hello" } });
    fireEvent.keyDown(box, { key: "Enter", keyCode: 229, nativeEvent: { isComposing: true } });
    expect(onSend).not.toHaveBeenCalled();
  });
});
