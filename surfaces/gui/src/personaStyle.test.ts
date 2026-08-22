import { describe, expect, it } from "vitest";
import type { Persona } from "./api";
import { ACCENTS, accentFor, accentMap, introFor, placeholderFor } from "./personaStyle";

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
