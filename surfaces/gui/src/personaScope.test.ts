import { describe, expect, it } from "vitest";
import { fullPersonaName, isProjectScoped, shortPersonaName } from "./personaScope";

// Which personas' sessions belong to a project folder — the predicate behind both the folder
// gate and the sidebar's PROJECTS grouping.
describe("isProjectScoped", () => {
  it("honours what the persona declares, whatever its family", () => {
    // The point of the §16 reversal: a knowledge persona can own projects too.
    expect(isProjectScoped({ family: "knowledge", projects: true })).toBe(true);
    expect(isProjectScoped({ family: "code", projects: true })).toBe(true);
  });

  it("lets a chat-shaped persona opt out", () => {
    // Fast Chat: quick questions land in whatever directory is current, so grouping them by
    // folder is noise and a folder prompt is friction.
    expect(isProjectScoped({ family: "knowledge", projects: false })).toBe(false);
    // …even a code-family one, if it ever declared it.
    expect(isProjectScoped({ family: "code", projects: false })).toBe(false);
  });

  it("falls back to the old family rule when the server sends no flag", () => {
    // An older sidecar predates `projects`. Gating everything on a field it does not know about
    // would put a folder prompt in front of every persona it serves.
    expect(isProjectScoped({ family: "code" })).toBe(true);
    expect(isProjectScoped({ family: "knowledge" })).toBe(false);
    expect(isProjectScoped(undefined)).toBe(false);
  });
});

describe("persona naming", () => {
  it("shortens for chrome and expands for the detail page", () => {
    expect(shortPersonaName("OpenWorker", "cowork")).toBe("Coworker");
    expect(shortPersonaName("Ops Coworker", "ops")).toBe("Ops");
    expect(fullPersonaName("Ops", "ops")).toBe("Ops Coworker");
    expect(fullPersonaName("Chat", "chat")).toBe("Chat");
  });
});
