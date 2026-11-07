// OPE-260: the composer's model chip vs picker when the current model cannot run.
// Before: `modelReady === false` always swapped the picker for a "No model" chip, even
// with five runnable Ollama models in the list, and a session left on a removed
// provider's model showed it as an ordinary ✓ row. Now the chip appears only when
// nothing can run, reads as the action it is, and otherwise the picker stays with the
// current row marked and Send asking for a pick.
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { Composer } from "./Composer";

const LABELS = {
  "anthropic:claude-fable-5": "Claude Fable 5 · Anthropic",
  "openrouter:z-ai/glm-5.3": "GLM 5.3 · via OpenRouter",
  "ollama:qwen3:0.6b": "qwen3:0.6b",
  "ollama:llama3.2:3b": "llama3.2:3b",
};

const props = (extra: Partial<Parameters<typeof Composer>[0]> = {}) => ({
  mode: "interactive",
  model: "anthropic:claude-fable-5",
  models: ["anthropic:claude-fable-5", "ollama:qwen3:0.6b", "ollama:llama3.2:3b"],
  modelLabels: LABELS,
  running: false,
  connected: true,
  sessionId: "s1",
  onSend: vi.fn(),
  onInterrupt: vi.fn(),
  onModeChange: vi.fn(),
  onModelChange: vi.fn(),
  onConnectModel: vi.fn(),
  ...extra,
});

const box = () => screen.getByPlaceholderText(/Ask the coworker/) as HTMLTextAreaElement;
const sendButton = () => screen.getByRole("button", { name: "Send" }) as HTMLButtonElement;
const typeAndSend = () => {
  fireEvent.change(box(), { target: { value: "hello" } });
  fireEvent.click(sendButton());
};
const openPicker = (triggerText: string) => {
  fireEvent.click(screen.getByText(triggerText));
  return screen.getByTestId("model-picker-menu");
};

afterEach(() => {
  cleanup();
  localStorage.clear();
});

describe("Composer: the default cannot run but other models can (OPE-260)", () => {
  it("keeps the picker instead of the chip, with the current model marked unavailable", () => {
    render(<Composer {...props({ modelReady: false })} />);
    expect(screen.queryByTestId("connect-model-chip")).toBeNull();
    const menu = openPicker("Claude Fable 5");
    expect(menu).toBeTruthy();
    // Every runnable Ollama model is offered.
    expect(screen.getByTestId("model-picker-option-ollama:qwen3:0.6b")).toBeTruthy();
    expect(screen.getByTestId("model-picker-option-ollama:llama3.2:3b")).toBeTruthy();
    // The current (unrunnable) default says so on its row; the others do not.
    expect(screen.getByTestId("model-picker-option-anthropic:claude-fable-5").textContent).toContain(
      "Not available on this machine",
    );
    expect(screen.getByTestId("model-picker-option-ollama:qwen3:0.6b").textContent).not.toContain(
      "Not available",
    );
  });

  it("picking a runnable model from that picker calls onModelChange", () => {
    const p = props({ modelReady: false });
    render(<Composer {...p} />);
    openPicker("Claude Fable 5");
    fireEvent.click(screen.getByTestId("model-picker-option-ollama:qwen3:0.6b"));
    expect(p.onModelChange).toHaveBeenCalledWith("ollama:qwen3:0.6b");
  });

  it("Send asks for a pick: nothing is sent, nothing navigates, the draft stays", () => {
    const p = props({ modelReady: false });
    render(<Composer {...p} />);
    expect(sendButton().title).toBe("Pick a model that can run here to send");
    typeAndSend();
    expect(p.onSend).not.toHaveBeenCalled();
    expect(p.onConnectModel).not.toHaveBeenCalled();
    expect(box().value).toBe("hello");
  });
});

describe("Composer: nothing can run (OPE-260)", () => {
  it("shows the chip, worded as the action, when the list holds only the default", () => {
    const p = props({ modelReady: false, models: ["anthropic:claude-fable-5"] });
    render(<Composer {...p} />);
    const chip = screen.getByTestId("connect-model-chip");
    expect(chip.textContent).toContain("Connect a model");
    expect(chip.textContent).not.toContain("No model");
    expect(screen.queryByTestId("model-picker-menu")).toBeNull();
    fireEvent.click(chip);
    expect(p.onConnectModel).toHaveBeenCalledTimes(1);
  });

  it("Send leads to setup and keeps the draft", () => {
    const p = props({ modelReady: false, models: ["anthropic:claude-fable-5"] });
    render(<Composer {...p} />);
    expect(sendButton().title).toBe("Connect a model to send");
    typeAndSend();
    expect(p.onConnectModel).toHaveBeenCalledTimes(1);
    expect(p.onSend).not.toHaveBeenCalled();
    expect(box().value).toBe("hello");
  });

  it("the backend's any_model_ready=false wins over a list that still names models", () => {
    // The list always carries the default, and may carry stale rows; the backend knows.
    const p = props({ modelReady: false, anyModelReady: false });
    render(<Composer {...p} />);
    expect(screen.getByTestId("connect-model-chip")).toBeTruthy();
  });

  it("shows the chip when every other listed model is itself unavailable", () => {
    const p = props({
      modelReady: false,
      models: ["anthropic:claude-fable-5", "openrouter:z-ai/glm-5.3"],
      unavailableModels: ["openrouter:z-ai/glm-5.3"],
    });
    render(<Composer {...p} />);
    expect(screen.getByTestId("connect-model-chip")).toBeTruthy();
  });
});

describe("Composer: a session left on a removed provider's model (OPE-260)", () => {
  it("keeps the picker, marks that model unavailable, and blocks Send until a pick", () => {
    const p = props({
      model: "openrouter:z-ai/glm-5.3",
      modelReady: true, // the machine DEFAULT runs; this session's model does not
      models: ["anthropic:claude-fable-5", "ollama:qwen3:0.6b"],
      unavailableModels: ["openrouter:z-ai/glm-5.3"],
    });
    render(<Composer {...p} />);
    expect(screen.queryByTestId("connect-model-chip")).toBeNull();
    openPicker("GLM 5.3");
    expect(screen.getByTestId("model-picker-option-openrouter:z-ai/glm-5.3").textContent).toContain(
      "Not available on this machine",
    );
    expect(sendButton().title).toBe("Pick a model that can run here to send");
    typeAndSend();
    expect(p.onSend).not.toHaveBeenCalled();
  });
});

describe("Composer: the ordinary case is unchanged", () => {
  it("a runnable model sends", () => {
    const p = props({ modelReady: true });
    render(<Composer {...p} />);
    expect(screen.queryByTestId("connect-model-chip")).toBeNull();
    expect(sendButton().title).toBe("");
    typeAndSend();
    expect(p.onSend).toHaveBeenCalledTimes(1);
    expect(p.onConnectModel).not.toHaveBeenCalled();
  });

  it("no row is marked unavailable", () => {
    render(<Composer {...props({ modelReady: true })} />);
    openPicker("Claude Fable 5");
    expect(screen.getByTestId("model-picker-menu").textContent).not.toContain("Not available");
  });
});
