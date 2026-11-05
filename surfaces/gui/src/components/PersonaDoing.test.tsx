// OB-15: "How it's been doing" on the coworker page (technical-design.md §9), in the words of
// T3-15: versions numbered, sessions, typical numbers, one note across both, and the models used
// on their own line.
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import i18n from "i18next";
import type { DoingRow } from "../api";
import { inUseSpan } from "../obs3";

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
const day = (ms: number) => new Date(ms).toLocaleDateString(undefined, { month: "short", day: "numeric" });

const row = (over: Partial<DoingRow> = {}): DoingRow => ({
  fp: "9f3a21", current: true, unknown_version: false, first_seen: Date.UTC(2026, 9, 21, 12), last_seen: Date.UTC(2026, 10, 4, 12),
  before_kept: false, runs: 15, automation_runs: 0, turns: 30, checked: 27, with_warn: 7, not_checked: 3,
  median_tokens: 192_000, at_least: false, approvals_per_run: 0, up: 0, down: 0, too_few: false, models: ["model-x"],
  changed: null, model_line: { kind: "first", models: ["model-x"] }, version_n: 2, ...over,
});

describe("changedLine", () => {
  it("names a skill's version change, the instructions' line change and a model change", () => {
    const r = row({
      changed: { from: "7c10e4", files: [
        { path: "manifest.md", kind: "edited", lines: 4 },
        { path: "skills/semgrep-scan/SKILL.md", kind: "edited", lines: 0, skill: "semgrep-scan", from_version: "1.1", to_version: "1.2" },
      ] },
      model_line: { kind: "changed", from: "model-x", to: "model-y" },
    });
    expect(changedLine(r, t)).toBe("instructions edited (+4 lines) · skill semgrep-scan 1.1 → 1.2 · model changed: model-x → model-y");
  });

  it("a first version says only that, never 'model unchanged' (T3-15 b, f)", () => {
    expect(changedLine(row(), t)).toBe("First version seen on this machine.");
  });
});

describe("in use (T3-15 c, g)", () => {
  const now = new Date(2026, 10, 5, 15);
  it("one day is one date; today says today; the oldest kept day says 'or earlier'", () => {
    const nov2 = new Date(2026, 10, 2, 9).getTime();
    expect(inUseSpan(nov2, new Date(2026, 10, 2, 18).getTime(), false, t, now)).toBe(day(nov2));
    const nov3 = new Date(2026, 10, 3, 9).getTime();
    expect(inUseSpan(nov3, now.getTime(), false, t, now)).toBe(`${day(nov3)} – today`);
    expect(inUseSpan(nov3, now.getTime(), true, t, now)).toBe(`${day(nov3)} or earlier – today`);
    expect(inUseSpan(null, null, false, t, now)).toBe("—");
  });
});

describe("PersonaDoing", () => {
  it("names versions v1 · current, says sessions, and one 'too few' note across both typical columns", async () => {
    (getPersonaDoing as any).mockResolvedValue({
      persona_id: "security", kept_from: Date.UTC(2026, 9, 6), min_runs: 10, total: 1, runs: 18,
      rows: [
        row({ changed: { from: "7c10e4", files: [] }, model_line: { kind: "unchanged", models: ["model-x"] } }),
        row({ fp: "7c10e4", current: false, runs: 3, too_few: true, median_tokens: null, before_kept: true, up: 2, down: 1, version_n: 1 }),
      ],
    });
    render(<PersonaDoing personaId="security" />);
    await waitFor(() => expect(screen.getAllByTestId("persona-doing-row")).toHaveLength(2));
    const head = screen.getByRole("table").querySelector("thead")!.textContent || "";
    for (const h of ["Sessions", "Typical tokens per session", "Approvals asked per session", "Turns with issues", "Ratings 👍 / 👎"]) expect(head).toContain(h);
    const [a, b] = screen.getAllByTestId("persona-doing-row");
    const [va, vb] = screen.getAllByTestId("persona-doing-version");
    expect(va.textContent).toBe("v2 · current");
    expect(va.getAttribute("title")).toBe("Fingerprint ⓕ9f3a21");
    expect(vb.textContent).toBe("v1");
    expect(a.textContent).toContain("192k");
    expect(a.textContent).toContain("26%");
    expect(a.textContent).toContain("— / —");
    const tooFew = screen.getByTestId("persona-doing-too-few");
    expect(tooFew.textContent).toBe("too few sessions to compare");
    expect(tooFew.getAttribute("colspan")).toBe("2");
    expect(b.textContent).toContain("or earlier");
    expect(b.textContent).toContain("2 / 1");
    // a later version can be compared
    expect(screen.getByTestId("persona-doing-diff").textContent).toBe("Show diff");
  });

  it("a first version: no Show diff, 'Nothing to compare yet', and the models used on their own line", async () => {
    const models = ["gpt-5.6-sol", "model-a", "model-b"];
    (getPersonaDoing as any).mockResolvedValue({
      persona_id: "cowork", kept_from: null, min_runs: 10, total: 1, runs: 1,
      rows: [row({ version_n: 1, model_line: { kind: "first", models }, models })],
    });
    render(<PersonaDoing personaId="cowork" />);
    await waitFor(() => expect(screen.getByTestId("persona-doing-changed")).toBeTruthy());
    expect(screen.queryByTestId("persona-doing-diff")).toBeNull();
    expect(screen.getByTestId("persona-doing-nothing-to-compare").textContent).toBe("Nothing to compare yet");
    expect(screen.getByTestId("persona-doing-changed").textContent).toContain("First version seen on this machine.");
    expect(screen.getByTestId("persona-doing-changed").textContent).not.toContain("unchanged");
    const line = screen.getByTestId("persona-doing-models");
    expect(line.textContent).toBe("Models used: gpt-5.6-sol and 2 more");
    expect(line.getAttribute("title")).toBe("gpt-5.6-sol, model-a, model-b");
  });

  it("renders nothing when the engine has no such endpoint", async () => {
    (getPersonaDoing as any).mockResolvedValue({ id: "ops" });
    const { container } = render(<PersonaDoing personaId="ops" />);
    await waitFor(() => expect(getPersonaDoing).toHaveBeenCalled());
    expect(container.textContent).toBe("");
  });
});
