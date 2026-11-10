// Issue sentences built on screen from their facts (TD2-3, fix list C-2) say what the engine
// says in English, and leave no blank in Chinese. The fixture is every issue and fine line of
// the 39 golden Phase 2 scenarios, as the engine's checks produce them.
import { describe, expect, it } from "vitest";
import i18n from "i18next";
import en from "../locales/en.json";
import zh from "../locales/zh.json";
import golden from "./__fixtures__/issues-golden.json";
import { fineSentence, issueSentence } from "./StepsPanel";

const tFor = async (lng: "en" | "zh") => {
  const inst = i18n.createInstance();
  await inst.init({ lng, resources: { en: { translation: en }, zh: { translation: zh } }, interpolation: { escapeValue: false } });
  return inst.t.bind(inst) as any;
};

// Where the screen's English deliberately reads better than the stored sentence.
const READS_BETTER: Record<string, (stored: string, g: any, t: any) => string> = {
  waited_unwatched: (s) => "It " + s,
  shadow_disagreed: (s) => s.replace(/said (\w+) on/, (_m, r) => `said "${r === "allow" ? "allowed" : r === "deny" ? "blocked" : "asked you"}" on`),
  // The error type in plain words ("key or sign-in refused", not "auth").
  model_failed: (s, g, t) => s.replace(`(${g.facts.error_type})`, `(${t(`steps.error_type.${g.facts.error_type}`, { defaultValue: g.facts.error_type })})`),
};

describe("issue sentences from facts", () => {
  it("say in English what the engine says, for every golden issue and fine line", async () => {
    const t = await tFor("en");
    expect(golden.length).toBeGreaterThan(300);
    for (const g of golden as any[]) {
      const said = g.kind === "issue" ? issueSentence(g, t) : fineSentence(g, t);
      const want = g.kind === "issue" && READS_BETTER[g.check] ? READS_BETTER[g.check](g.text, g, t) : g.text;
      expect(said, `${g.scenario} ${g.kind} ${g.check}`).toBe(want);
    }
  });

  it("leave no blank in Chinese and never fall back to English", async () => {
    const t = await tFor("zh");
    for (const g of golden as any[]) {
      const said = g.kind === "issue" ? issueSentence(g, t) : fineSentence(g, t);
      expect(said, `${g.scenario} ${g.check}`).not.toBe(g.text);
      expect(said).not.toMatch(/\{\{|undefined|null/);
    }
  });

  it("keeps the stored English sentence for turns checked before facts were stored", async () => {
    const t = await tFor("zh");
    expect(issueSentence({ check: "tool_failed", text: "run_shell failed.", facts: { how: "exit" } }, t)).toBe("run_shell failed.");
    expect(fineSentence({ check: "tool_failed", text: "No tool failed" }, t)).toBe("No tool failed");
  });
});
