// IME regression (caught 2026-08-03): while a pinyin/kana IME is composing, Enter
// belongs to the candidate list — it confirms the highlighted candidate, it must
// never send the message. Guards: e.nativeEvent.isComposing (modern engines,
// incl. the Tauri WKWebView) and legacy keyCode === 229.
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

describe("Composer / IME", () => {
  it("Enter while an IME is composing never sends (isComposing)", () => {
    const p = props();
    render(<Composer {...p} />);
    fireEvent.change(box(), { target: { value: "nihao" } });
    box().dispatchEvent(
      new KeyboardEvent("keydown", { key: "Enter", isComposing: true, bubbles: true, cancelable: true }),
    );
    expect(p.onSend).not.toHaveBeenCalled();
  });

  it("Enter while an IME is composing never sends (legacy keyCode 229)", () => {
    const p = props();
    render(<Composer {...p} />);
    fireEvent.change(box(), { target: { value: "nihao" } });
    fireEvent.keyDown(box(), { key: "Enter", keyCode: 229, which: 229 });
    expect(p.onSend).not.toHaveBeenCalled();
  });

  it("a real Enter (not composing) still sends", () => {
    const p = props();
    render(<Composer {...p} />);
    fireEvent.change(box(), { target: { value: "hello" } });
    fireEvent.keyDown(box(), { key: "Enter" });
    expect(p.onSend).toHaveBeenCalledTimes(1);
  });
});
