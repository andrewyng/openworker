// T3-4: one name per model when recordings are shown. Turns saved before the model-prefix fix
// (#837) recorded one OpenAI model as both "openai:model-x" and "model-x"; the rule drops the
// default provider's prefix only, as the server's `canonical_model`.
import { describe, expect, it } from "vitest";
import i18n from "i18next";
import { canonicalModel, canonicalModels, sameModel } from "./modelNames";
import { stampLine } from "./components/StepsPanel";

const t = i18n.t.bind(i18n) as any;

describe("canonicalModel", () => {
  it("drops the openai: prefix only, and keeps every other prefix", () => {
    expect(canonicalModel("openai:model-x")).toBe("model-x");
    expect(canonicalModel(" model-x ")).toBe("model-x");
    expect(canonicalModel("anthropic:model-y")).toBe("anthropic:model-y");
    expect(canonicalModel("openai:")).toBe("openai:");
    expect(sameModel("openai:model-x", "model-x")).toBe(true);
    expect(canonicalModels(["openai:model-x", "model-x", "ollama:model-q:0.6b"])).toEqual(["model-x", "ollama:model-q:0.6b"]);
  });

  it("the Trace panel's stamp line names the model once", () => {
    expect(stampLine({ model: { id: "openai:model-x" } }, t)).toContain("model-x");
    expect(stampLine({ model: { id: "openai:model-x" } }, t)).not.toContain("openai:");
  });
});
