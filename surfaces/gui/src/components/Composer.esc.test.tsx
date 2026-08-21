// Esc → Stop: HIG key equivalent while a turn is running. Composition matches
// feat/cancel-question (honor defaultPrevented) and keeps dictation Esc ahead of interrupt.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Composer } from "./Composer";

const READY = {
  recording: false,
  model_installed: true,
  model_verified: true,
  test_passed: true,
  download_in_progress: false,
  model_name: "Whisper Base English (local)",
  model_bytes: 147964211,
  supported: true,
  device_summary: "macOS 15 · Apple Silicon",
  compatibility_reason: null,
};
const RECORDING = { ...READY, recording: true };

let invoke: ReturnType<typeof vi.fn>;

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

beforeEach(() => {
  invoke = vi.fn(async (cmd: string) => {
    if (cmd === "get_dictation_status") return READY;
    if (cmd === "start_dictation") return RECORDING;
    if (cmd === "cancel_dictation") return null;
    if (cmd === "stop_dictation") return "hello from the mic";
    return null;
  });
  (globalThis as any).__TAURI__ = { core: { invoke }, event: { listen: async () => () => {} } };
});

afterEach(() => {
  cleanup();
  delete (globalThis as any).__TAURI__;
});

describe("Composer — Esc Stop", () => {
  it("Esc interrupts when a turn is running", () => {
    const onInterrupt = vi.fn();
    render(<Composer {...props({ running: true, onInterrupt })} />);
    expect(screen.getByTitle("Stop (Esc)")).toBeTruthy();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onInterrupt).toHaveBeenCalledTimes(1);
  });

  it("Esc is a no-op when idle (does not interrupt)", () => {
    const onInterrupt = vi.fn();
    render(<Composer {...props({ running: false, onInterrupt })} />);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onInterrupt).not.toHaveBeenCalled();
  });

  it("leaves Esc to a visible question instead of stopping the turn", () => {
    const onInterrupt = vi.fn();
    render(
      <Composer
        {...props({ running: true, questionPending: true, onInterrupt })}
      />,
    );
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onInterrupt).not.toHaveBeenCalled();
  });

  it("honors defaultPrevented so an earlier Esc handler wins", () => {
    const onInterrupt = vi.fn();
    // Capture-phase claim mimics a modal / question Cancel that already handled Esc.
    const claim = (e: KeyboardEvent) => {
      if (e.key === "Escape") e.preventDefault();
    };
    window.addEventListener("keydown", claim, true);
    try {
      render(<Composer {...props({ running: true, onInterrupt })} />);
      fireEvent.keyDown(window, { key: "Escape" });
      expect(onInterrupt).not.toHaveBeenCalled();
    } finally {
      window.removeEventListener("keydown", claim, true);
    }
  });

  it("does not stop the turn while a dialog or menu owns Escape", () => {
    const onInterrupt = vi.fn();
    const { rerender } = render(
      <>
        <div role="dialog">Settings</div>
        <Composer {...props({ running: true, onInterrupt })} />
      </>,
    );
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onInterrupt).not.toHaveBeenCalled();

    rerender(
      <>
        <div role="menu">Session actions</div>
        <Composer {...props({ running: true, onInterrupt })} />
      </>,
    );
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onInterrupt).not.toHaveBeenCalled();
  });

  it("ignores repeated keydown events", () => {
    const onInterrupt = vi.fn();
    render(<Composer {...props({ running: true, onInterrupt })} />);
    fireEvent.keyDown(window, { key: "Escape", repeat: true });
    expect(onInterrupt).not.toHaveBeenCalled();
  });

  it("Esc while dictating cancels the mic, not the turn", async () => {
    const onInterrupt = vi.fn();
    render(<Composer {...props({ running: true, onInterrupt })} />);
    fireEvent.click(await screen.findByLabelText("Start dictation"));
    await screen.findByLabelText("Stop dictation");
    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() => expect(invoke).toHaveBeenCalledWith("cancel_dictation", undefined));
    expect(onInterrupt).not.toHaveBeenCalled();
  });
});
