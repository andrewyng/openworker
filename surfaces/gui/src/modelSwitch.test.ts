// OPE-260: when a session switches itself off a model this machine can no longer run.
import { describe, expect, it } from "vitest";
import { autoSwitchTarget } from "./modelSwitch";

const base = {
  model: "openrouter:z-ai/glm-5.3", // the session's model; its key was removed
  selectable: ["ollama:llama3.2:3b", "ollama:qwen3:0.6b"],
  defaultId: "ollama:llama3.2:3b",
  defaultReady: true,
  personaBound: false,
};

describe("autoSwitchTarget", () => {
  it("moves a session off a model the machine no longer lists, to a runnable default", () => {
    expect(autoSwitchTarget(base)).toBe("ollama:llama3.2:3b");
  });

  it("leaves a session alone while its model still runs", () => {
    expect(autoSwitchTarget({ ...base, model: "ollama:qwen3:0.6b" })).toBeNull();
    // On the default itself, with the default running.
    expect(autoSwitchTarget({ ...base, model: "ollama:llama3.2:3b" })).toBeNull();
  });

  it("does nothing before the settings arrive (empty list)", () => {
    expect(autoSwitchTarget({ ...base, selectable: [] })).toBeNull();
  });

  it("does nothing without a model", () => {
    expect(autoSwitchTarget({ ...base, model: "" })).toBeNull();
  });

  it("stays put when the default cannot run either (the picker marks it instead)", () => {
    // The list always carries the default, runnable or not.
    expect(
      autoSwitchTarget({
        ...base,
        selectable: ["gpt-5.6-sol", "openrouter:z-ai/glm-5.3"],
        defaultId: "gpt-5.6-sol",
        defaultReady: false,
      }),
    ).toBeNull();
  });

  it("a session ON the dead default has nowhere to go", () => {
    expect(
      autoSwitchTarget({
        ...base,
        model: "gpt-5.6-sol",
        selectable: ["gpt-5.6-sol", "ollama:llama3.2:3b"],
        defaultId: "gpt-5.6-sol",
        defaultReady: false,
      }),
    ).toBeNull();
  });

  it("never switches to a default the list does not carry", () => {
    expect(autoSwitchTarget({ ...base, defaultId: "anthropic:claude-fable-5" })).toBeNull();
  });

  it("does nothing without a default id (older backend, or settings mid-load)", () => {
    expect(autoSwitchTarget({ ...base, defaultId: "" })).toBeNull();
  });

  it("a coworker bound to its own model list is left to that list", () => {
    expect(autoSwitchTarget({ ...base, personaBound: true })).toBeNull();
  });

  it("is a decision, not a side effect: the same input answers the same", () => {
    expect(autoSwitchTarget(base)).toBe(autoSwitchTarget({ ...base }));
  });
});
