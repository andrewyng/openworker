// Enter-to-send behavior, including the IME composition guard: pressing Enter to confirm
// a CJK candidate (注音/拼音選字) must NOT send — only Enter outside composition sends.
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

afterEach(() => cleanup());

const box = () => screen.getByPlaceholderText(/Ask the coworker/) as HTMLTextAreaElement;
const type = (value: string) => fireEvent.change(box(), { target: { value } });

describe("Composer Enter-to-send + IME composition", () => {
  it("sends on a plain Enter (no composition)", () => {
    const onSend = vi.fn();
    render(<Composer {...props({ onSend })} />);
    type("hello");
    fireEvent.keyDown(box(), { key: "Enter", shiftKey: false });
    expect(onSend).toHaveBeenCalledTimes(1);
    expect(onSend).toHaveBeenCalledWith("hello", []);
  });

  it("does NOT send when Enter confirms an IME candidate (isComposing)", () => {
    // 中文輸入法選字按 Enter：isComposing=true 表示還在組字，應只確認候選字、不發送。
    const onSend = vi.fn();
    render(<Composer {...props({ onSend })} />);
    type("你好");
    fireEvent.keyDown(box(), { key: "Enter", isComposing: true });
    expect(onSend).not.toHaveBeenCalled();
    // draft survives — nothing was cleared
    expect(box().value).toBe("你好");
  });

  it("does NOT send on the IME 'composing' keycode (229)", () => {
    const onSend = vi.fn();
    render(<Composer {...props({ onSend })} />);
    type("你好");
    fireEvent.keyDown(box(), { key: "Enter", keyCode: 229 });
    expect(onSend).not.toHaveBeenCalled();
  });

  it("does NOT send on Shift+Enter (newline)", () => {
    const onSend = vi.fn();
    render(<Composer {...props({ onSend })} />);
    type("multi line");
    fireEvent.keyDown(box(), { key: "Enter", shiftKey: true });
    expect(onSend).not.toHaveBeenCalled();
  });

  it("does NOT send on the WebKit ordering (compositionend then Enter, isComposing:false)", () => {
    // Safari/WebKit fires the committing keydown AFTER compositionend, so isComposing is false
    // on that Enter (WebKit bug 165004). composingRef must still swallow it.
    const onSend = vi.fn();
    render(<Composer {...props({ onSend })} />);
    type("你好");
    fireEvent.compositionStart(box());
    fireEvent.compositionEnd(box());
    fireEvent.keyDown(box(), { key: "Enter", isComposing: false, keyCode: 13 });
    expect(onSend).not.toHaveBeenCalled();
    expect(box().value).toBe("你好");
  });

  it("sends again once the composition-end guard releases", async () => {
    const onSend = vi.fn();
    render(<Composer {...props({ onSend })} />);
    type("你好");
    fireEvent.compositionStart(box());
    fireEvent.compositionEnd(box());
    // composingRef resets on the next tick (setTimeout 0); a later send-Enter must work.
    await new Promise((r) => setTimeout(r, 0));
    fireEvent.keyDown(box(), { key: "Enter" });
    expect(onSend).toHaveBeenCalledTimes(1);
    expect(onSend).toHaveBeenCalledWith("你好", []);
  });
});
