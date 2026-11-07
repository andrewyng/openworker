import { describe, expect, it } from "vitest";
import { canonicalModel, canonicalModels, coworkerModels, sameModel } from "./modelNames";

describe("canonicalModel", () => {
  it("drops a prefix naming the default provider", () => {
    expect(canonicalModel("openai:gpt-test-1")).toBe("gpt-test-1");
    expect(canonicalModel("gpt-test-1")).toBe("gpt-test-1");
    expect(canonicalModel("  openai:gpt-test-1 ")).toBe("gpt-test-1");
  });

  it("keeps every other prefix, and colons inside the name", () => {
    expect(canonicalModel("anthropic:model-a")).toBe("anthropic:model-a");
    expect(canonicalModel("ollama:qwen-test:32b")).toBe("ollama:qwen-test:32b");
    expect(canonicalModel("openai-codex:gpt-test-1")).toBe("openai-codex:gpt-test-1");
    expect(canonicalModel("openai:qwen-test:32b")).toBe("qwen-test:32b");
  });

  it("leaves an empty or prefix-only id alone", () => {
    expect(canonicalModel("")).toBe("");
    expect(canonicalModel("openai:")).toBe("openai:");
  });
});

describe("sameModel", () => {
  it("treats the prefixed and bare spellings as one model", () => {
    expect(sameModel("openai:gpt-test-1", "gpt-test-1")).toBe(true);
    expect(sameModel("gpt-test-1", "openai:gpt-test-1")).toBe(true);
  });

  it("tells different providers apart", () => {
    expect(sameModel("anthropic:gpt-test-1", "gpt-test-1")).toBe(false);
    expect(sameModel("openrouter:gpt-test-1", "openai:gpt-test-1")).toBe(false);
  });
});

describe("canonicalModels", () => {
  it("canonicalizes, drops duplicates and keeps order", () => {
    expect(canonicalModels(["anthropic:model-a", "openai:gpt-test-1", "gpt-test-1", ""])).toEqual([
      "anthropic:model-a",
      "gpt-test-1",
    ]);
  });
});

describe("coworkerModels", () => {
  const listed = ["anthropic:model-a", "openai:gpt-test-1"];

  it("binds a fresh draft to the OpenAI entry on an OpenAI-only machine", () => {
    const out = coworkerModels(listed, ["gpt-test-1", "gpt-test-2"], "gpt-test-2");
    expect(out.picker).toEqual(["anthropic:model-a", "gpt-test-1"]);
    expect(out.unavailable).toEqual(["anthropic:model-a"]);
    expect(out.bind).toBe("gpt-test-1");
  });

  it("keeps a current model that is on the list, in either spelling", () => {
    expect(coworkerModels(listed, ["gpt-test-1"], "gpt-test-1").bind).toBeNull();
    expect(coworkerModels(listed, ["gpt-test-1"], "openai:gpt-test-1").bind).toBeNull();
  });

  it("still reports nothing runnable when only the other provider is listed", () => {
    const out = coworkerModels(["anthropic:model-a", "anthropic:model-b"], ["gpt-test-1"], "gpt-test-1");
    expect(out.unavailable).toEqual(["anthropic:model-a", "anthropic:model-b"]);
    // The draft binds to the list head, which the composer shows as "No model".
    expect(out.bind).toBe("anthropic:model-a");
  });

  it("matches a machine list that spells the model with the prefix", () => {
    const out = coworkerModels(["gpt-test-1"], ["openai:gpt-test-1"], "");
    expect(out.unavailable).toEqual([]);
    expect(out.bind).toBe("gpt-test-1");
  });

  it("offers the machine's list when the coworker has none", () => {
    expect(coworkerModels([], ["gpt-test-1", "ollama:local-test"], "gpt-test-1")).toEqual({
      picker: ["gpt-test-1", "ollama:local-test"],
      unavailable: [],
      bind: null,
    });
  });
});
