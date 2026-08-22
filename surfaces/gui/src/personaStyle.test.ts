import { describe, expect, it } from "vitest";
import type { Persona } from "./api";
import {
  ACCENTS,
  accentFor,
  accentMap,
  budgetUse,
  checkpointProgress,
  checkpointsFor,
  introFor,
  placeholderFor,
} from "./personaStyle";

const persona = (p: Partial<Persona>): Persona => ({
  id: "demo",
  name: "Demo",
  icon: "",
  tagline: "",
  needs_workspace: true,
  builtin: false,
  family: "knowledge",
  workspace: "deliverable",
  tools: [],
  enabled: true,
  surfaced: true,
  default: false,
  ...p,
});

describe("accentFor", () => {
  it("takes the manifest's accent when it names a curated one", () => {
    expect(accentFor(persona({ id: "research", accent: "violet" }))).toBe("violet");
  });

  it("ignores an accent outside the curated set", () => {
    // Server-side validation should have caught it; the GUI must still not emit an unstyled
    // data-accent (every value has to resolve to a real light/dark pair in styles.css).
    const a = accentFor(persona({ id: "research", accent: "chartreuse" }));
    expect(ACCENTS).toContain(a);
  });

  it("keeps the product's cobalt on the default coworker", () => {
    expect(accentFor(persona({ id: "cowork" }))).toBe("cobalt");
  });

  it("derives a stable non-cobalt accent for a persona that declares none", () => {
    const first = accentFor(persona({ id: "builder" }));
    expect(accentFor(persona({ id: "builder" }))).toBe(first); // same persona → same colour, always
    expect(first).not.toBe("cobalt"); // cobalt stays the default coworker's tell
    expect(ACCENTS).toContain(first);
  });
});

describe("introFor", () => {
  it("gives the default coworker its three template tasks", () => {
    const intro = introFor(persona({ id: "cowork" }));
    expect(intro.greeting).toBe("What should we produce?");
    expect(intro.starters.map((s) => s.key)).toEqual(["folder", "hubspot", "github-slack"]);
  });

  it("does not hand another persona the coworker's tasks", () => {
    // The bug this module exists for: a code Builder was offered "Create a report from my
    // HubSpot leads" because the start screen was hardcoded to the default persona's rows.
    const intro = introFor(persona({ id: "builder", family: "code" }));
    expect(intro.starters.every((s) => !s.prompt.includes("HubSpot"))).toBe(true);
    expect(intro.greeting).toBe("What are we building?");
  });

  it("prefers what the persona's manifest declares, field by field", () => {
    const intro = introFor(
      persona({
        id: "builder",
        family: "code",
        intro: {
          greeting: "Which phase are we building?",
          lede: "",
          placeholder: "",
          starters: [
            { key: "phase", title: "Plan the phases", sub: "", prompt: "Plan it.", requires: [] },
          ],
        },
      }),
    );
    expect(intro.greeting).toBe("Which phase are we building?");
    // Declared starters REPLACE the fallback's — never merge, or a persona inherits tasks it
    // never asked for.
    expect(intro.starters).toHaveLength(1);
    // An omitted field still falls back rather than rendering blank.
    expect(intro.lede).toBeTruthy();
    expect(intro.placeholder).toBeTruthy();
  });

  it("falls back on family while the persona list is still loading", () => {
    expect(introFor(undefined, "repo-ops").greeting).toBe("What should we work on?");
    expect(introFor(undefined, "code").greeting).toBe("What are we building?");
  });
});

describe("placeholderFor", () => {
  it("names the persona instead of always saying coworker", () => {
    expect(placeholderFor(persona({ id: "cowork" }))).toMatch(/Ask the coworker/);
    expect(
      placeholderFor(
        persona({
          id: "fastchat",
          intro: { greeting: "", lede: "", placeholder: "Ask Fast Chat…", starters: [] },
        }),
      ),
    ).toBe("Ask Fast Chat…");
    // A persona that declares nothing still gets its family's voice, not the coworker's.
    expect(placeholderFor(persona({ id: "repo-ops" }))).not.toMatch(/coworker/i);
  });
});

describe("accentMap", () => {
  const set = (...ids: (string | [string, string])[]) =>
    ids.map((v) => (Array.isArray(v) ? persona({ id: v[0], accent: v[1] }) : persona({ id: v })));

  it("gives every installed persona a different accent", () => {
    // The clash that motivated this: the built-in Ops and the user's Repo Ops both declared teal.
    const map = accentMap(set(["ops", "teal"], ["repo-ops", "teal"], "builder", "fastchat", "research"));
    const used = Object.values(map);
    expect(new Set(used).size).toBe(used.length);
    expect(Object.keys(map)).toHaveLength(5);
  });

  it("honours a declared accent, and bumps only the later claimant", () => {
    const map = accentMap(set(["ops", "teal"], ["repo-ops", "teal"]));
    expect(map.ops).toBe("teal"); // lower id claims it
    expect(map["repo-ops"]).not.toBe("teal");
  });

  it("keeps cobalt on the default coworker even in a crowd", () => {
    const map = accentMap(set("cowork", "code", "chat", "ops", "builder", "fastchat", "research"));
    expect(map.cowork).toBe("cobalt");
  });

  it("does not shuffle colours when the display order changes", () => {
    const personas = set("cowork", "builder", "research");
    const reversed = [...personas].reverse();
    expect(accentMap(reversed)).toEqual(accentMap(personas));
  });

  it("still assigns something once the palette runs out", () => {
    const many = set(...Array.from({ length: ACCENTS.length + 3 }, (_, i) => `p${i}`));
    const map = accentMap(many);
    expect(Object.keys(map)).toHaveLength(many.length);
    expect(Object.values(map).every((a) => ACCENTS.includes(a))).toBe(true);
  });

  it("is empty before the persona list loads, so callers fall back to accentFor", () => {
    expect(accentMap(null)).toEqual({});
    expect(accentMap([])).toEqual({});
  });
});

describe("checkpoints", () => {
  const steps = [
    { id: "plan", label: "Plan", evidence: ["todo_write"] },
    { id: "gather", label: "Gather", evidence: ["web_search", "read_file"] },
    { id: "produce", label: "Produce", evidence: ["write_file"] },
  ];

  it("uses what the persona declared, else its family's shape", () => {
    expect(checkpointsFor(persona({ id: "x", checkpoints: steps }))).toBe(steps);
    expect(checkpointsFor(persona({ id: "builder", family: "code" })).map((c) => c.id)).toContain("implement");
    expect(checkpointsFor(persona({ id: "research", family: "knowledge" })).map((c) => c.id)).toContain("produce");
  });

  it("marks steps the run went past but never did as skipped", () => {
    // A run that produced a deliverable without planning or gathering did skip those. Calling
    // the first gap "current" would claim the run is at step one while step three is finished.
    const out = checkpointProgress(steps, ["write_file"]);
    expect(out.map((s) => s.state)).toEqual(["skipped", "skipped", "done"]);
  });

  it("advances as evidence arrives", () => {
    expect(checkpointProgress(steps, ["todo_write"]).map((s) => s.state)).toEqual([
      "done", "current", "pending",
    ]);
    expect(checkpointProgress(steps, ["todo_write", "read_file"]).map((s) => s.state)).toEqual([
      "done", "done", "current",
    ]);
  });

  it("starts at step one when nothing has run", () => {
    // Not "all pending": a run that has not started is AT the first step.
    expect(checkpointProgress(steps, []).map((s) => s.state)).toEqual([
      "current", "pending", "pending",
    ]);
  });

  it("has no current step once every step is evidenced", () => {
    const out = checkpointProgress(steps, ["todo_write", "web_search", "write_file"]);
    expect(out.every((s) => s.state === "done")).toBe(true);
  });
});

describe("checkpoints — skipped steps", () => {
  const steps = [
    { id: "recall", label: "Recall", evidence: ["brain_recall"] },
    { id: "plan", label: "Plan", evidence: ["todo_write"] },
    { id: "implement", label: "Implement", evidence: ["write_file"] },
    { id: "verify", label: "Verify", evidence: ["run_shell"] },
    { id: "record", label: "Record", evidence: ["brain_note"] },
  ];

  it("puts the run at the first gap AFTER the furthest step reached", () => {
    // The real case this came from: a Builder run with five files edited and three commands
    // run, which the first implementation labelled "current: Recall" — the panel contradicting
    // its own activity line one row below.
    const out = checkpointProgress(steps, ["write_file", "run_shell", "grep"]);
    expect(out.map((s) => s.state)).toEqual([
      "skipped", "skipped", "done", "done", "current",
    ]);
  });

  it("never reports more than one current step", () => {
    for (const tools of [[], ["todo_write"], ["write_file"], ["brain_recall", "brain_note"]]) {
      const currents = checkpointProgress(steps, tools).filter((s) => s.state === "current");
      expect(currents.length).toBeLessThanOrEqual(1);
    }
  });
});

describe("budgetUse", () => {
  const p = (budgets: any[]) => persona({ id: "research", budgets });
  const B = [
    { id: "searches", label: "searches", limit: 4, tools: ["web_search", "mcp__tavily__tavily-search"] },
    { id: "reads", label: "page reads", limit: 10, tools: ["web_fetch"] },
  ];

  it("counts real tool calls, not the model's account of them", () => {
    const out = budgetUse(p(B), ["web_search", "web_search", "mcp__tavily__tavily-search", "web_fetch"]);
    expect(out.map((b) => [b.budget.id, b.used])).toEqual([["searches", 3], ["reads", 1]]);
  });

  it("flags near, at and over distinctly", () => {
    const one = [{ id: "s", label: "searches", limit: 4, tools: ["web_search"] }];
    const at = (n: number) => budgetUse(p(one), Array(n).fill("web_search"))[0].state;
    expect(at(0)).toBe("ok");
    expect(at(2)).toBe("ok");
    expect(at(3)).toBe("near"); // 75% — late enough to mean something, early enough to act on
    expect(at(4)).toBe("at");
    expect(at(9)).toBe("over");
  });

  it("is empty when the persona declares none, rather than inventing a ceiling", () => {
    expect(budgetUse(persona({ id: "x" }), ["web_search"])).toEqual([]);
    expect(budgetUse(undefined, ["web_search"])).toEqual([]);
  });

  it("ignores tools no budget names", () => {
    expect(budgetUse(p(B), ["read_file", "grep", "todo_write"]).every((b) => b.used === 0)).toBe(true);
  });
});

describe("budgetUse — the total-call sentinel", () => {
  const starred = persona({
    id: "repo-ops",
    budgets: [{ id: "calls", label: "tool calls", limit: 5, tools: ["*"] }],
  });

  it("counts EVERY call, whatever the tool", () => {
    // Seven automations declare a total-call ceiling; enumerating a persona's whole toolset to
    // express it would rot as the catalog changes, so "*" is the escape hatch.
    const out = budgetUse(starred, ["run_shell", "grep", "read_file", "write_file"]);
    expect(out[0].used).toBe(4);
    expect(out[0].state).toBe("near"); // 4/5
  });

  it("goes over rather than capping the count", () => {
    const out = budgetUse(starred, Array(9).fill("run_shell"));
    // The number keeps climbing: the value is in SEEING the overrun, and a counter pinned at
    // 5/5 would hide how far past it went.
    expect(out[0].used).toBe(9);
    expect(out[0].state).toBe("over");
  });
});

describe("checkpointsFor — steps the persona cannot take", () => {
  it("drops a fallback step whose only tools the persona lacks", () => {
    // The default Coworker has no `brain` capability, so a "Recall" step could never complete:
    // it showed as permanently skipped and pushed the apparent position one step along.
    const withoutBrain = persona({ id: "cowork", family: "knowledge", tools: ["files", "search"] });
    expect(checkpointsFor(withoutBrain).map((c) => c.id)).not.toContain("recall");
    const withBrain = persona({ id: "repo-ops", family: "knowledge", tools: ["files", "brain"] });
    expect(checkpointsFor(withBrain).map((c) => c.id)).toContain("recall");
  });

  it("keeps a step that has any usable tool left", () => {
    // "Gather" names web_search AND read_file; lacking one capability must not delete the step.
    const p2 = persona({ id: "x", family: "knowledge", tools: ["files"] });
    expect(checkpointsFor(p2).map((c) => c.id)).toContain("gather");
  });

  it("shows the full shape while the persona list is still loading", () => {
    expect(checkpointsFor(undefined, "cowork").length).toBeGreaterThan(0);
  });

  it("never prunes what a persona declared for itself", () => {
    const declared = persona({
      id: "x",
      tools: [],
      checkpoints: [{ id: "recall", label: "Recall", evidence: ["brain_recall"] }],
    });
    expect(checkpointsFor(declared).map((c) => c.id)).toEqual(["recall"]);
  });
});
