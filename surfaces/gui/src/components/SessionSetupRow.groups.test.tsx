// The coworker picker shows groups (UX ruling 2026-09-29, variant C): the general
// coworkers first with no label, then Engineering and Security, each under a small label.
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { SessionSetupRow, personaGroups } from "./SessionSetupRow";
import type { Persona } from "../api";

const persona = (id: string, group?: string, extra: Partial<Persona> = {}): Persona => ({
  id, name: id, icon: "bot", tagline: `${id} tagline`, requires_folder: false, builtin: true,
  tools: [], enabled: true, surfaced: true, default: id === "cowork", group, ...extra,
});

const PERSONAS = [
  persona("security", "security"),
  persona("cowork", "general"),
  persona("reviewer", "engineering"),
  persona("dep-audit", "security"),
  persona("imported"),
];

afterEach(cleanup);

describe("coworker picker groups", () => {
  it("orders general first, then Engineering, then Security; empty groups vanish", () => {
    expect(personaGroups(PERSONAS).map((g) => [g.group, g.items.map((p) => p.id)])).toEqual([
      ["", ["cowork", "imported"]],
      ["engineering", ["reviewer"]],
      ["security", ["security", "dep-audit"]],
    ]);
    expect(personaGroups([persona("cowork", "general")]).map((g) => g.group)).toEqual([""]);
  });

  it("draws a label for each group but none for the general coworkers", () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => ({ workspaces: [] }) }) as unknown as Response));
    render(
      <SessionSetupRow
        personas={PERSONAS} agent="cowork" showFolder={false} folderName={null}
        onPickCoworker={() => {}} onPickFolder={() => {}} onManage={() => {}} onImport={() => {}}
      />,
    );
    fireEvent.click(screen.getByTestId("coworker-chip"));
    const menu = screen.getByTestId("coworker-group-engineering").parentElement!;
    const text = menu.textContent || "";
    expect(text.indexOf("cowork")).toBeLessThan(text.indexOf("Engineering"));
    expect(text.indexOf("Engineering")).toBeLessThan(text.indexOf("reviewer"));
    expect(text.indexOf("reviewer")).toBeLessThan(text.indexOf("Security"));
    expect(screen.queryByText("General")).toBeNull();
    expect(screen.getByTestId("coworker-group-security").textContent).toContain("dep-audit");
  });
});
