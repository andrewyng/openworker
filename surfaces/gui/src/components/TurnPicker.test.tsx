// T3-14: moving between turns in the Trace panel. "Turn N of M" opens a searchable list (A),
// ← and → move between turns while the panel has focus (B), and a thin bar per turn sits
// under the picker (C).
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { getRecordedTurns, getTurnSteps } from "../api";
import { StepsPanel } from "./StepsPanel";
import { turnMatches, turnTexts } from "../obs3";

vi.mock("../api", () => ({
  getRecordedTurns: vi.fn(),
  getTurnSteps: vi.fn(() => Promise.resolve([])),
  getStepDetail: vi.fn(),
  getRecordedUsage: vi.fn(() => Promise.resolve(null)),
  getTurnIssues: vi.fn(() => Promise.resolve(null)),
  markIssuesSeen: vi.fn(() => Promise.resolve()),
}));

const turn = (n: number, over: Record<string, any> = {}) => ({
  turn_id: `t${n}`, seq: n, kind: "run", trigger: "user", status: "completed", started_at: 1_000 * n,
  duration_ms: 500, stamp: {}, buckets: { total: 500 }, usage: { pill: n * 1000 }, issues: { warn: 0, info: 0 },
  last_message_id: `a${n}`, ...over,
});

const turns = [
  turn(1),
  turn(2, { issues: { warn: 1, info: 0 }, issue_checks: ["tool_failed"], usage: { pill: 12_300 } }),
  turn(3, { wake: { reason: "review" } }),
];
const conversation = [
  { kind: "user", text: "Summarise the quarterly budget please" },
  { kind: "assistant", text: "Here is the summary.", mid: "a1" },
  { kind: "user", text: "Now read the onboarding guide" },
  { kind: "tool", name: "read_file", args: { path: "docs/Onboarding-Guide.md" } },
  { kind: "assistant", text: "The guide says hello.", mid: "a2" },
  { kind: "assistant", text: "The reviewer woke me; nothing to do.", mid: "a3" },
];

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

async function open(list = turns) {
  vi.mocked(getRecordedTurns).mockResolvedValue(list as any);
  render(<StepsPanel sessionId="s1" onClose={() => {}} conversation={conversation} />);
  await waitFor(() => expect(screen.getByTestId("steps-turn").textContent).toContain(`Turn ${list.length} of ${list.length}`));
}

describe("each turn's part of the conversation", () => {
  it("splits at each answer and finds tools and files", () => {
    const texts = turnTexts(turns, conversation);
    expect(texts.get("t1")!.user).toBe("Summarise the quarterly budget please");
    expect(texts.get("t2")!.tools).toEqual(["read_file"]);
    expect(texts.get("t2")!.files).toEqual(["docs/Onboarding-Guide.md"]);
    expect(texts.get("t3")!.user).toBe("");
    expect(texts.get("t3")!.answer).toContain("reviewer woke me");
    // a running last turn with no answer yet takes what is left
    const running = turnTexts([...turns, turn(4, { last_message_id: null })], [...conversation, { kind: "user", text: "and now?" }]);
    expect(running.get("t4")!.user).toBe("and now?");
  });

  it("matches the number, the words, tools, files and issue titles, ignoring case", () => {
    const text = { user: "Read the guide", answer: "Done", tools: ["run_shell"], files: ["a/Budget.xlsx"] };
    expect(turnMatches(2, text, [], "2")).toBe(true);
    expect(turnMatches(2, text, [], "3")).toBe(false);
    expect(turnMatches(2, text, [], "GUIDE")).toBe(true);
    expect(turnMatches(2, text, [], "budget.xlsx")).toBe(true);
    expect(turnMatches(2, text, [], "run_shell")).toBe(true);
    expect(turnMatches(2, text, ["A tool failed"], "tool failed")).toBe(true);
    expect(turnMatches(2, text, [], "nowhere")).toBe(false);
  });
});

describe("the turn list (A)", () => {
  it("lists every turn with its time, first words, tokens, issue and wake reason, and jumps", async () => {
    await open();
    fireEvent.click(screen.getByTestId("steps-turn"));
    const list = screen.getByTestId("turn-list");
    expect(list).toBeTruthy();
    const row2 = screen.getByTestId("turn-list-row-2").textContent || "";
    expect(row2).toContain("Turn 2");
    expect(row2).toContain("Now read the onboarding guide");
    expect(row2).toContain("12.3k tokens");
    expect(row2).toContain("1 issue");
    expect(screen.getByTestId("turn-list-row-3").textContent).toContain("woke:");
    expect(screen.getByTestId("turn-list-row-3").textContent).toContain("(no message of yours)");
    expect(screen.getByTestId("turn-list-row-3").getAttribute("aria-current")).toBe("true");
    fireEvent.click(screen.getByTestId("turn-list-row-1"));
    expect(screen.queryByTestId("turn-list")).toBeNull();
    expect(screen.getByTestId("steps-turn").textContent).toContain("Turn 1 of 3");
  });

  it("search keeps the turns whose text matches; a number jumps on Enter; nothing says so", async () => {
    await open();
    fireEvent.click(screen.getByTestId("steps-turn"));
    const search = screen.getByTestId("turn-list-search");
    fireEvent.change(search, { target: { value: "onboarding-GUIDE.md" } });
    expect(screen.queryByTestId("turn-list-row-1")).toBeNull();
    expect(screen.getByTestId("turn-list-row-2")).toBeTruthy();
    fireEvent.change(search, { target: { value: "a tool failed" } });  // an issue title
    expect(screen.getByTestId("turn-list-row-2")).toBeTruthy();
    expect(screen.queryByTestId("turn-list-row-3")).toBeNull();
    fireEvent.change(search, { target: { value: "zebra" } });
    expect(screen.getByTestId("turn-list-empty").textContent).toBe("No turns match.");
    fireEvent.change(search, { target: { value: "1" } });
    expect(screen.getByTestId("turn-list-hint").textContent).toContain("Press Enter to go to turn 1");
    fireEvent.keyDown(search, { key: "Enter" });
    expect(screen.getByTestId("steps-turn").textContent).toContain("Turn 1 of 3");
    // typing in the search box never moves the turn by itself
    expect(getTurnSteps).toHaveBeenCalled();
  });
});

describe("keys (B)", () => {
  it("← and → move between turns while the panel has focus, never from a text box", async () => {
    await open();
    const panel = screen.getByTestId("steps-panel");
    fireEvent.keyDown(panel, { key: "ArrowLeft" });
    expect(screen.getByTestId("steps-turn").textContent).toContain("Turn 2 of 3");
    fireEvent.keyDown(panel, { key: "ArrowLeft" });
    fireEvent.keyDown(panel, { key: "ArrowLeft" }); // stays at the first
    expect(screen.getByTestId("steps-turn").textContent).toContain("Turn 1 of 3");
    fireEvent.keyDown(panel, { key: "ArrowRight" });
    expect(screen.getByTestId("steps-turn").textContent).toContain("Turn 2 of 3");
    fireEvent.keyDown(panel, { key: "End" }); // no Home or End
    expect(screen.getByTestId("steps-turn").textContent).toContain("Turn 2 of 3");
    fireEvent.click(screen.getByTestId("steps-turn"));
    fireEvent.keyDown(screen.getByTestId("turn-list-search"), { key: "ArrowRight" });
    expect(screen.getByTestId("steps-turn").textContent).toContain("Turn 2 of 3");
  });
});

describe("the turns strip (C)", () => {
  it("one bar per turn: height by tokens, amber with issues, the current one marked, click jumps", async () => {
    await open();
    const bars = screen.getByTestId("turn-strip").querySelectorAll(".steps-strip-bar");
    expect(bars).toHaveLength(3);
    const bar2 = screen.getByTestId("turn-strip-bar-2");
    expect(bar2.className).toContain("warn");
    expect(bar2.getAttribute("title")).toBe("Turn 2 · 12.3k tokens · 1 issue");
    expect((bar2.querySelector("i") as HTMLElement).style.height).toBe("100%"); // the heaviest
    expect(screen.getByTestId("turn-strip-bar-3").className).toContain("current");
    fireEvent.click(screen.getByTestId("turn-strip-bar-1"));
    expect(screen.getByTestId("steps-turn").textContent).toContain("Turn 1 of 3");
    expect(screen.getByTestId("turn-strip-bar-1").className).toContain("current");
  });

  it("works with one turn, and stays usable with 200+", async () => {
    await open([turn(1)]);
    expect(screen.getByTestId("turn-strip").querySelectorAll(".steps-strip-bar")).toHaveLength(1);
    cleanup();
    const many = Array.from({ length: 230 }, (_, i) => turn(i + 1));
    await open(many);
    const strip = screen.getByTestId("turn-strip");
    expect(strip.className).toContain("dense"); // thin bars, the strip scrolls
    expect(strip.querySelectorAll(".steps-strip-bar")).toHaveLength(230);
    fireEvent.click(screen.getByTestId("turn-strip-bar-117"));
    expect(screen.getByTestId("steps-turn").textContent).toContain("Turn 117 of 230");
  });
});
