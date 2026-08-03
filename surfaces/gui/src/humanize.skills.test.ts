// SKILLS-SPEC §4.6 GUI — the transcript trust line: a load_skill tool call always renders
// as a human-readable "Used skill: X" step, whether model-invoked or forced via /skill.
import { describe, expect, it } from "vitest";
import { humanizeTool } from "./humanize";

describe("humanizeTool(load_skill)", () => {
  it("renders the Used-skill line with the skill name", () => {
    const line = humanizeTool("load_skill", { name: "incident-summary" });
    expect(line.pre).toBe("Used skill: ");
    expect(line.obj).toBe("incident-summary");
  });

  it("stays safe on null/missing args", () => {
    expect(humanizeTool("load_skill", null).obj).toBe("");
    expect(humanizeTool("load_skill", {}).obj).toBe("");
  });
});
