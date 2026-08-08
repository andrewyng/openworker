import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { Composer } from "./Composer";

const props = () => ({
  mode: "interactive",
  model: "gpt-5.6-sol",
  running: false,
  connected: true,
  onSend: vi.fn(),
  onInterrupt: vi.fn(),
  onModeChange: vi.fn(),
  onModelChange: vi.fn(),
});

const box = () => screen.getByPlaceholderText(/Ask the coworker/);

afterEach(cleanup);

describe("Composer keyboard input", () => {
  it("does not send when Enter confirms an active IME composition", () => {
    const p = props();
    render(<Composer {...p} />);
    fireEvent.change(box(), { target: { value: "openworker" } });
    fireEvent.keyDown(box(), { key: "Enter", isComposing: true });

    expect(p.onSend).not.toHaveBeenCalled();
    expect((box() as HTMLTextAreaElement).value).toBe("openworker");
  });

  it("does not send for WebKit's process-key fallback after composition ends", () => {
    const p = props();
    render(<Composer {...p} />);
    fireEvent.change(box(), { target: { value: "openworker" } });
    fireEvent.keyDown(box(), { key: "Enter", keyCode: 229 });

    expect(p.onSend).not.toHaveBeenCalled();
    expect((box() as HTMLTextAreaElement).value).toBe("openworker");
  });

  it("still sends with a normal Enter keydown", () => {
    const p = props();
    render(<Composer {...p} />);
    fireEvent.change(box(), { target: { value: "hello" } });
    fireEvent.keyDown(box(), { key: "Enter", keyCode: 13 });

    expect(p.onSend).toHaveBeenCalledWith("hello", [], undefined);
  });
});
