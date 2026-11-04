// OB-15: "How it's been doing" on the coworker page (technical-design.md §9).
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import i18n from "i18next";
import type { DoingRow } from "../api";

vi.mock("../api", () => ({
  getPersonaDoing: vi.fn(),
  getPersonaVersionDiff: vi.fn(),
}));

import { getPersonaDoing } from "../api";
import { changedLine, PersonaDoing } from "./PersonaDoing";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});
const t = i18n.t.bind(i18n) as any;

const row = (over: Partial<DoingRow> = {}): DoingRow => ({
  fp: "9f3a21", current: true, unknown_version: false, first_seen: Date.UTC(2026, 9, 21), last_seen: Date.UTC(2026, 10, 4),
  before_kept: false, runs: 15, automation_runs: 0, turns: 30, checked: 27, with_warn: 7, not_checked: 3,
  median_tokens: 192_000, at_least: false, approvals_per_run: 0, up: 0, down: 0, too_few: false, models: ["model-x"],
  changed: null, model_line: { kind: "unchanged", models: ["model-x"] }, ...over,
});

describe("changedLine", () => {
  it("names a skill's version change and the instructions' line change, and the model", () => {
    const r = row({
      changed: { from: "7c10e4", files: [
        { path: "manifest.md", kind: "edited", lines: 4 },
        { path: "skills/semgrep-scan/SKILL.md", kind: "edited", lines: 0, skill: "semgrep-scan", from_version: "1.1", to_version: "1.2" },
      ] },
      model_line: { kind: "changed", from: "model-x", to: "model-y" },
    });
    expect(changedLine(r, t)).toBe("instructions edited (+4 lines) · skill semgrep-scan 1.1 → 1.2 · model changed: model-x → model-y");
    expect(changedLine(row(), t)).toBe("the first version seen on this machine · model unchanged");
  });
});

describe("PersonaDoing", () => {
  it("one row per version: 'too few runs', 'before' the oldest kept day, and no ratings as — / —", async () => {
    (getPersonaDoing as any).mockResolvedValue({
      persona_id: "security", kept_from: Date.UTC(2026, 9, 6), min_runs: 10, total: 1, runs: 18,
      rows: [row(), row({ fp: "7c10e4", current: false, runs: 3, too_few: true, median_tokens: null, before_kept: true, up: 2, down: 1 })],
    });
    render(<PersonaDoing personaId="security" />);
    await waitFor(() => expect(screen.getAllByTestId("persona-doing-row")).toHaveLength(2));
    const [a, b] = screen.getAllByTestId("persona-doing-row").map((r) => r.textContent || "");
    expect(a).toContain("ⓕ9f3a21");
    expect(a).toContain("current");
    expect(a).toContain("192k");
    expect(a).toContain("26%");
    expect(a).toContain("— / —");
    expect(b).toContain("too few runs to compare");
    expect(b).toContain("before");
    expect(b).toContain("2 / 1");
    expect((screen.getByTestId("persona-doing-diff") as HTMLButtonElement).disabled).toBe(true);
  });

  it("renders nothing when the engine has no such endpoint", async () => {
    (getPersonaDoing as any).mockResolvedValue({ id: "ops" });
    const { container } = render(<PersonaDoing personaId="ops" />);
    await waitFor(() => expect(getPersonaDoing).toHaveBeenCalled());
    expect(container.textContent).toBe("");
  });
});
