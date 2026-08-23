import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { RightRail } from "./RightRail";

// The rail's own network calls (artifacts, Access) are irrelevant here; answer everything with
// an empty object so nothing throws and no test depends on a fixture it never asserts on.
vi.stubGlobal(
  "fetch",
  vi.fn(async () => ({ ok: true, json: async () => ({}) }) as Response),
);

afterEach(cleanup);

// A code-family run that has done both kinds of work: three reads, two greps and a recall
// (intake — Memory's story), against one write and two shell calls (change — Progress's).
const TOOLS = [
  "read_file",
  "read_file",
  "read_file",
  "grep",
  "grep",
  "brain_recall",
  "run_shell",
  "run_shell",
  "write_file",
];

function renderRail(over: Partial<React.ComponentProps<typeof RightRail>> = {}) {
  return render(
    <RightRail
      active
      sessionId="s1"
      refreshKey={0}
      toolNames={TOOLS}
      todo={[]}
      running
      showArtifacts={false}
      personaFamily="code"
      personaName="Builder"
      contextUsed={47_000}
      contextWindow={100_000}
      compactions={1}
      threadsTouched={[{ id: "openevolve-phase-2", read: true, written: true }]}
      {...over}
    />,
  );
}

/** Open a collapsed RailSection by clicking its header button. */
function expand(title: string) {
  fireEvent.click(screen.getByRole("button", { name: new RegExp(title, "i") }));
}

describe("RightRail — Progress shows what the run changed", () => {
  it("counts edits and commands, and leaves reads/searches/recalls to Memory", () => {
    renderRail();
    const activity = screen.getByTestId("rail-activity");
    expect(activity.textContent).toContain("1 file edited");
    expect(activity.textContent).toContain("2 commands");
    expect(activity.textContent).not.toContain("files read");
    expect(activity.textContent).not.toContain("searches");
    expect(activity.textContent).not.toContain("recall");
  });

  it("no longer carries the context meter or the compaction line", () => {
    renderRail();
    // Progress is the only section open by default, so a hit here would be Progress's.
    expect(screen.queryByTestId("rail-context-meters")).toBeNull();
    expect(screen.queryByTestId("rail-compactions")).toBeNull();
  });
});

describe("RightRail — Memory shows what the session took in", () => {
  it("puts the context percentage in the collapsed header, where it is always readable", () => {
    renderRail();
    expect(screen.getByRole("button", { name: /Memory/i }).textContent).toContain("47% context");
  });

  it("holds the context meter, the compaction line and the intake counts once opened", () => {
    renderRail();
    expand("Memory");
    expect(screen.getByTestId("rail-context-meters").textContent).toContain("47%");
    expect(screen.getByTestId("rail-compactions").textContent).toContain("Compacted 1");
    const intake = screen.getByTestId("rail-memory-activity");
    expect(intake.textContent).toContain("3 files read");
    expect(intake.textContent).toContain("2 searches");
    expect(intake.textContent).toContain("1 recall");
    // And the durable threads it already showed.
    expect(within(screen.getByTestId("rail-threads")).getByText("openevolve-phase-2")).toBeTruthy();
  });

  // The regression this split could easily have introduced: the section used to render only when
  // a brain thread was touched, which is a minority of runs. Gating the context meter on that
  // would hide it exactly where it matters most — a long run that never calls brain_*.
  it("renders with no brain threads at all", () => {
    renderRail({ threadsTouched: [] });
    const header = screen.getByRole("button", { name: /Memory/i });
    expect(header.textContent).toContain("47% context");
    expand("Memory");
    expect(screen.getByTestId("rail-context-meters").textContent).toContain("47%");
    expect(screen.queryByTestId("rail-threads")).toBeNull();
  });

  it("stays hidden when there is genuinely nothing to report", () => {
    renderRail({
      threadsTouched: [],
      toolNames: ["run_shell"],
      contextUsed: undefined,
      contextWindow: undefined,
      compactions: 0,
    });
    expect(screen.queryByRole("button", { name: /Memory/i })).toBeNull();
  });
});

// The Memory body used to be a meter, a row of bare spans and a thread list with nothing
// between them — one undifferentiated stretch of text to anything reading the page linearly.
describe("RightRail — Memory is organized and announces itself", () => {
  it("splits into three named regions that can be reached by heading", () => {
    renderRail();
    expand("Memory");
    for (const name of ["Window", "Taken in", "Threads"]) {
      expect(screen.getByRole("region", { name })).toBeTruthy();
      expect(screen.getByRole("heading", { name, level: 3 })).toBeTruthy();
    }
  });

  it("counts the intake as a real list, not one run-on line", () => {
    renderRail();
    expand("Memory");
    const intake = screen.getByRole("region", { name: "Taken in" });
    // 3 files read / 2 searches / 1 recall — three items, with boundaries between them.
    expect(within(intake).getAllByRole("listitem")).toHaveLength(3);
  });

  it("says what happened to a thread instead of stacking two nouns after its name", () => {
    renderRail();
    expand("Memory");
    const row = within(screen.getByTestId("rail-threads")).getAllByRole("listitem")[0];
    expect(row.textContent).toContain("read from and updated");
    // The pills are the sighted reading of the same fact, so they must not be read twice.
    for (const pill of row.querySelectorAll(".rail-thread-tag")) {
      expect(pill.getAttribute("aria-hidden")).toBe("true");
    }
  });

  it("reads the meter out as a percentage, not as a raw token count", () => {
    renderRail();
    expand("Memory");
    const meter = screen.getByRole("meter", { name: /context/ });
    expect(meter.getAttribute("aria-valuetext")).toBe("47%");
    expect(meter.getAttribute("aria-valuemax")).toBe("100000");
  });

  it("spells out the compaction glyph for anything reading it aloud", () => {
    renderRail();
    expand("Memory");
    const line = screen.getByTestId("rail-compactions");
    expect(line.querySelector("[aria-hidden]")?.textContent).toContain("Compacted 1×");
    expect(line.textContent).toContain("History compacted 1 time");
  });

  it("names only the groups it actually shows", () => {
    renderRail({ threadsTouched: [], compactions: 0 });
    expand("Memory");
    expect(screen.getByRole("region", { name: "Window" })).toBeTruthy();
    expect(screen.getByRole("region", { name: "Taken in" })).toBeTruthy();
    expect(screen.queryByRole("region", { name: "Threads" })).toBeNull();
  });
});
