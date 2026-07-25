// §37 voice input — one composer contract backed by either native Tauri dictation
// or the browser's speech-recognition API.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Composer } from "./Composer";
import { resetBrowserVoiceCaptureForTests } from "../voice";

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
const NOT_READY = { ...READY, model_verified: false, test_passed: false };
const RECORDING = { ...READY, recording: true };

let invoke: ReturnType<typeof vi.fn>;
let browserRecognition: MockSpeechRecognition | null;
let getUserMedia: ReturnType<typeof vi.fn>;

class MockSpeechRecognition {
  continuous = false;
  interimResults = false;
  lang = "";
  onstart: (() => void) | null = null;
  onresult: ((event: any) => void) | null = null;
  onerror: ((event: any) => void) | null = null;
  onend: (() => void) | null = null;
  abortCalls = 0;

  constructor() {
    browserRecognition = this;
  }

  start() {
    this.onstart?.();
  }

  stop() {
    const result = { 0: { transcript: "hello from the browser" }, length: 1, isFinal: true };
    this.onresult?.({ resultIndex: 0, results: { 0: result, length: 1 } });
    this.onend?.();
  }

  abort() {
    this.abortCalls += 1;
    this.onend?.();
  }

  fail(error: string) {
    this.onerror?.({ error });
    this.onend?.();
  }
}

const installBrowserVoice = () => {
  delete (globalThis as any).__TAURI__;
  browserRecognition = null;
  (globalThis as any).webkitSpeechRecognition = MockSpeechRecognition;
  getUserMedia = vi.fn(async () => ({
    getTracks: () => [{ stop: vi.fn() }],
  }));
  Object.defineProperty(navigator, "mediaDevices", {
    configurable: true,
    value: { getUserMedia },
  });
};

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
  window.localStorage.removeItem("openwork-voice-capture-mode");
  invoke = vi.fn(async (cmd: string) => {
    if (cmd === "get_dictation_status") return READY;
    if (cmd === "start_dictation") return RECORDING;
    if (cmd === "stop_dictation") return "hello from the mic";
    return null;
  });
  (globalThis as any).__TAURI__ = { core: { invoke }, event: { listen: async () => () => {} } };
  browserRecognition = null;
  getUserMedia = vi.fn();
});

afterEach(() => {
  resetBrowserVoiceCaptureForTests();
  cleanup();
  window.localStorage.removeItem("openwork-voice-capture-mode");
  delete (globalThis as any).__TAURI__;
  delete (globalThis as any).SpeechRecognition;
  delete (globalThis as any).webkitSpeechRecognition;
  Object.defineProperty(navigator, "mediaDevices", {
    configurable: true,
    value: undefined,
  });
});

describe("Composer voice input (§37)", () => {
  it("keeps a visible disabled mic when browser speech recognition is unavailable", async () => {
    delete (globalThis as any).__TAURI__;
    render(<Composer {...props()} />);
    const mic = await screen.findByLabelText("Voice input unavailable in this browser");
    expect(mic.hasAttribute("disabled")).toBe(true);
    expect(mic.getAttribute("title")).toContain("does not provide speech recognition");
  });

  it("uses browser speech recognition to create an editable dictation draft", async () => {
    installBrowserVoice();
    render(<Composer {...props()} />);

    fireEvent.click(await screen.findByLabelText("Start dictation"));
    expect(getUserMedia).toHaveBeenCalledWith({ audio: true });
    await waitFor(() => expect(browserRecognition).not.toBeNull());
    expect(browserRecognition?.continuous).toBe(true);
    expect(browserRecognition?.interimResults).toBe(true);

    fireEvent.click(await screen.findByLabelText("Stop dictation"));
    const box = screen.getByPlaceholderText(/Ask the coworker/) as HTMLTextAreaElement;
    await waitFor(() => expect(box.value).toBe("hello from the browser"));
  });

  it("auto-submits a browser voice discussion with trusted metadata", async () => {
    installBrowserVoice();
    const onSend = vi.fn();
    render(<Composer {...props({ onSend })} />);

    fireEvent.click(await screen.findByLabelText("Voice input mode"));
    expect(screen.getByText(/Transcription is managed by your browser/)).toBeTruthy();
    const selectedIndicator = screen.getByTestId("voice-mode-selected-indicator");
    expect(selectedIndicator.querySelector("svg")).toBeTruthy();
    expect(selectedIndicator.textContent).toBe("");
    fireEvent.click(screen.getByRole("menuitemradio", { name: /Voice discussion/ }));
    fireEvent.click(screen.getByLabelText("Start voice discussion"));
    fireEvent.click(await screen.findByLabelText("Stop voice discussion"));

    await waitFor(() =>
      expect(onSend).toHaveBeenCalledWith(
        "hello from the browser",
        undefined,
        { inputMode: "voice_discussion" },
      ),
    );
  });

  it("surfaces browser microphone permission failures without wedging the mic", async () => {
    installBrowserVoice();
    getUserMedia.mockRejectedValue(new DOMException("Denied", "NotAllowedError"));
    render(<Composer {...props()} />);

    fireEvent.click(await screen.findByLabelText("Start dictation"));
    expect((await screen.findByRole("alert")).textContent).toContain("Microphone access was blocked");
    expect(screen.getByLabelText("Start dictation").hasAttribute("disabled")).toBe(false);
  });

  it("surfaces browser recognition failures that happen after recording starts", async () => {
    installBrowserVoice();
    render(<Composer {...props()} />);

    fireEvent.click(await screen.findByLabelText("Start dictation"));
    await screen.findByLabelText("Stop dictation");
    act(() => browserRecognition?.fail("network"));

    expect((await screen.findByRole("alert")).textContent).toContain(
      "speech recognition service could not be reached",
    );
    expect(screen.getByLabelText("Start dictation").hasAttribute("disabled")).toBe(false);
  });

  it("not ready → muted mic deep-links to Settings instead of recording", async () => {
    invoke.mockImplementation(async (cmd: string) =>
      cmd === "get_dictation_status" ? NOT_READY : null,
    );
    const onConfigureVoiceInput = vi.fn();
    render(<Composer {...props({ onConfigureVoiceInput })} />);

    const mic = await screen.findByLabelText("Configure Voice Input in Settings");
    expect(mic.getAttribute("aria-disabled")).toBe("true");
    fireEvent.click(mic);
    await waitFor(() => expect(onConfigureVoiceInput).toHaveBeenCalled());
    expect(invoke).not.toHaveBeenCalledWith("start_dictation", undefined);
  });

  it("ready → record shows the waveform and protects Send; stop inserts an editable draft", async () => {
    render(<Composer {...props()} />);

    fireEvent.click(await screen.findByLabelText("Start dictation"));
    const stop = await screen.findByLabelText("Stop dictation");
    expect(document.querySelector(".voice-wave-bars")).toBeTruthy();
    expect(screen.getByLabelText("Send").hasAttribute("disabled")).toBe(true);

    invoke.mockImplementation(async (cmd: string) => {
      if (cmd === "stop_dictation") return "hello from the mic";
      if (cmd === "get_dictation_status") return READY;
      return null;
    });
    fireEvent.click(stop);
    await screen.findByLabelText("Start dictation"); // recording UI wound down
    const box = screen.getByPlaceholderText(/Ask the coworker/) as HTMLTextAreaElement;
    expect(box.value).toBe("hello from the mic"); // a DRAFT — nothing auto-sent
    expect(document.querySelector(".voice-wave-bars")).toBeNull();
  });

  it("a start failure surfaces the error and never wedges the mic", async () => {
    invoke.mockImplementation(async (cmd: string) => {
      if (cmd === "get_dictation_status") return READY;
      if (cmd === "start_dictation") throw new Error("No microphone is available.");
      return null;
    });
    render(<Composer {...props()} />);

    fireEvent.click(await screen.findByLabelText("Start dictation"));
    expect((await screen.findByRole("alert")).textContent).toContain("No microphone is available.");
    expect(screen.getByLabelText("Start dictation").hasAttribute("disabled")).toBe(false);
  });

  it("voice discussion auto-submits the completed transcript with trusted metadata", async () => {
    const onSend = vi.fn();
    render(<Composer {...props({ onSend })} />);

    fireEvent.click(await screen.findByLabelText("Voice input mode"));
    fireEvent.click(screen.getByRole("menuitemradio", { name: /Voice discussion/ }));
    fireEvent.click(screen.getByLabelText("Start voice discussion"));
    fireEvent.click(await screen.findByLabelText("Stop voice discussion"));

    await waitFor(() =>
      expect(onSend).toHaveBeenCalledWith(
        "hello from the mic",
        undefined,
        { inputMode: "voice_discussion" },
      ),
    );
    expect(window.localStorage.getItem("openwork-voice-capture-mode")).toBe("discussion");
  });

  it("editing during voice discussion cancels auto-send and keeps an editable draft", async () => {
    const onSend = vi.fn();
    render(<Composer {...props({ onSend })} />);

    fireEvent.click(await screen.findByLabelText("Voice input mode"));
    fireEvent.click(screen.getByRole("menuitemradio", { name: /Voice discussion/ }));
    fireEvent.click(screen.getByLabelText("Start voice discussion"));

    const box = screen.getByPlaceholderText(/Ask the coworker/) as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: "Please check" } });
    fireEvent.click(await screen.findByLabelText("Stop voice discussion"));

    await waitFor(() => expect(box.value).toBe("Please check hello from the mic"));
    expect(onSend).not.toHaveBeenCalled();
    expect(screen.getByText("Voice transcript kept as an editable draft.")).toBeTruthy();
  });

  it("keeps a completed discussion as a draft if the connection drops while recording", async () => {
    const onSend = vi.fn();
    const view = render(<Composer {...props({ onSend, resetKey: "session-a" })} />);

    fireEvent.click(await screen.findByLabelText("Voice input mode"));
    fireEvent.click(screen.getByRole("menuitemradio", { name: /Voice discussion/ }));
    fireEvent.click(screen.getByLabelText("Start voice discussion"));
    await screen.findByLabelText("Stop voice discussion");

    view.rerender(
      <Composer {...props({ onSend, connected: false, resetKey: "session-a" })} />,
    );
    fireEvent.click(screen.getByLabelText("Stop voice discussion"));

    const box = screen.getByPlaceholderText(/Ask the coworker/) as HTMLTextAreaElement;
    await waitFor(() => expect(box.value).toBe("hello from the mic"));
    expect(onSend).not.toHaveBeenCalled();
    expect(screen.getByText("Voice transcript kept as an editable draft.")).toBeTruthy();
  });

  it("cancels an active browser capture when the conversation changes", async () => {
    installBrowserVoice();
    const firstSend = vi.fn();
    const secondSend = vi.fn();
    const view = render(
      <Composer {...props({ onSend: firstSend, resetKey: "session-a" })} />,
    );

    fireEvent.click(await screen.findByLabelText("Voice input mode"));
    fireEvent.click(screen.getByRole("menuitemradio", { name: /Voice discussion/ }));
    fireEvent.click(screen.getByLabelText("Start voice discussion"));
    await screen.findByLabelText("Stop voice discussion");
    const activeRecognition = browserRecognition;

    view.rerender(
      <Composer {...props({ onSend: secondSend, resetKey: "session-b" })} />,
    );

    await waitFor(() => expect(activeRecognition?.abortCalls).toBe(1));
    expect(firstSend).not.toHaveBeenCalled();
    expect(secondSend).not.toHaveBeenCalled();
    expect(screen.queryByLabelText("Stop voice discussion")).toBeNull();
  });

  it("releases an active browser capture when the composer unmounts", async () => {
    installBrowserVoice();
    const view = render(<Composer {...props({ resetKey: "session-a" })} />);

    fireEvent.click(await screen.findByLabelText("Start dictation"));
    await screen.findByLabelText("Stop dictation");
    const activeRecognition = browserRecognition;
    view.unmount();

    expect(activeRecognition?.abortCalls).toBe(1);
  });
});
