import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { Persona } from "../api";
import { SessionIntro } from "./SessionIntro";

// Hermetic fetch: roots (none shared), the session's live connections, and the connector list.
function stubFetch(liveConnectors: string[]) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      const json = url.includes("/connections")
        ? {
            connected: liveConnectors.map((c) => ({ connector: c, enabled: true, detail: "" })),
            recommended: [],
            attention: 0,
          }
        : url.includes("/roots")
          ? { roots: [{ path: "/scratch", primary: true, writable: true }] }
          : url.includes("/connectors")
            ? { connectors: [] }
            : {};
      return { ok: true, json: async () => json } as Response;
    }),
  );
}

const BUILDER: Persona = {
  id: "builder",
  name: "Builder",
  icon: "hammer",
  tagline: "Phase-by-phase implementation",
  requires_folder: true,
  builtin: false,
  family: "code",
  workspace: "git",
  tools: ["code_files"],
  accent: "green",
  intro: {
    greeting: "Which phase are we building?",
    lede: "One phase at a time, in your repo.",
    placeholder: "Describe the phase to build…",
    starters: [
      { key: "plan", title: "Plan the change as phases", sub: "Written to a PHASE file", prompt: "Plan this change as phases.", requires: [] },
      { key: "digest", title: "Post the digest", sub: "To the team channel", prompt: "Post it.", requires: ["slack"] },
    ],
  },
  enabled: true,
  surfaced: true,
  default: false,
};

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("SessionIntro", () => {
  it("renders the persona's own greeting and tasks, not the default coworker's", async () => {
    stubFetch([]);
    render(
      <SessionIntro
        sessionId="s1"
        persona={BUILDER}
        personaId="builder"
        onOpenSessionSettings={() => {}}
        onPrefill={() => {}}
      />,
    );
    expect(await screen.findByText("Which phase are we building?")).toBeTruthy();
    expect(screen.getByTestId("intro-task-plan")).toBeTruthy();
    // The bug that motivated this: every persona used to be offered the coworker's rows.
    expect(screen.queryByTestId("intro-task-hubspot")).toBeNull();
  });

  it("prefills the composer from a ready row and gates one whose connector is dark", async () => {
    stubFetch([]);
    const prefills: string[] = [];
    const opened: number[] = [];
    render(
      <SessionIntro
        sessionId="s1"
        persona={BUILDER}
        personaId="builder"
        onOpenSessionSettings={() => opened.push(1)}
        onPrefill={(t) => prefills.push(t)}
      />,
    );
    fireEvent.click(await screen.findByTestId("intro-task-plan"));
    expect(prefills).toEqual(["Plan this change as phases."]);

    // Slack is not live for this session → the row offers setup instead of the prompt.
    const gated = screen.getByTestId("intro-task-digest");
    expect(gated.className).toContain("gated");
    expect(gated.textContent).toContain("Configure ›");
    fireEvent.click(gated);
    expect(prefills).toHaveLength(1);
    expect(opened).toHaveLength(1);
  });

  it("starts a row whose connector IS live", async () => {
    stubFetch(["slack"]);
    const prefills: string[] = [];
    render(
      <SessionIntro
        sessionId="s1"
        persona={BUILDER}
        personaId="builder"
        onOpenSessionSettings={() => {}}
        onPrefill={(t) => prefills.push(t)}
      />,
    );
    const row = await screen.findByTestId("intro-task-digest");
    await waitFor(() => expect(row.className).not.toContain("gated"));
    fireEvent.click(row);
    expect(prefills).toEqual(["Post it."]);
  });

  it("falls back to the family's start screen for a persona that declares no intro", async () => {
    stubFetch([]);
    render(
      <SessionIntro
        sessionId="s1"
        persona={{ ...BUILDER, intro: null, family: "knowledge" }}
        personaId="repo-ops"
        onOpenSessionSettings={() => {}}
        onPrefill={() => {}}
      />,
    );
    expect(await screen.findByText("What should we work on?")).toBeTruthy();
    expect(screen.queryByTestId("intro-task-hubspot")).toBeNull();
  });
});
