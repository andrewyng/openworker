import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { getI18n } from "react-i18next";
import { Markdown } from "./Markdown";
import { TaskBoardContext, OPEN_TASK_EVENT } from "./TaskChip";
import {
  TeamUpdateLine,
  foldTeamUpdates,
  summarizeUpdates,
  TeamCreatedLine,
} from "./TeamUpdateLine";
import { TeamQuickLook, TeamView } from "./TeamView";
import { sampleTeam } from "../gallery/states/team-view";
import type { Item } from "../types";
import type { MessageSource } from "../api";
import { itemsFromMessages } from "../itemsFromMessages";

const source: MessageSource = {
  connector: "board",
  kind: "channel",
  channel_id: "acme",
  channel_name: "Acme",
  sender_id: "board",
  sender_name: "Board",
  text: "",
  ts: 1,
  board: {
    rows: [
      {
        kind: "moved",
        item: 1,
        title: "Refund rounding",
        from: "open",
        to: "in_progress",
        actor: "sam",
      },
    ],
  },
};
const wake: Item = { kind: "connector", source };
const tool: Item = {
  kind: "tool",
  id: "tool",
  name: "list_items",
  args: {},
  status: "ok",
};
afterEach(cleanup);

describe("team update lines", () => {
  it("folds adjacent wakes across tools without dropping the steps", () => {
    const result = foldTeamUpdates([wake, tool, wake]);
    expect(result).toHaveLength(1);
    expect(result[0].sources).toHaveLength(2);
    expect(result[0].steps).toEqual([tool]);
  });
  it.each<Item>([
    { kind: "user", text: "Keep the scope narrow" },
    { kind: "assistant", text: "Ready for review" },
    { kind: "assistant", text: "", reasoning: "Reviewing the evidence" },
    { kind: "notice", text: "Interrupted", tone: "warn" },
    {
      kind: "approval",
      name: "run_shell",
      args: {},
      reason: "Approval",
      resolved: "once",
    },
    { kind: "connector", source: { ...source, connector: "slack" } },
  ])(
    "does not fold across a visible or decision boundary: $kind",
    (boundary) => {
      expect(
        foldTeamUpdates([wake, boundary, wake]).filter((r) => r.sources),
      ).toHaveLength(2);
    },
  );
  it("does not join different boards", () => {
    expect(
      foldTeamUpdates([
        wake,
        { kind: "connector", source: { ...source, channel_id: "other" } },
      ]),
    ).toHaveLength(2);
  });
  it("folds routine narration and trailing steps but preserves the final answer", () => {
    const narration: Item = { kind: "assistant", text: "I will check the board." };
    const answer: Item = { kind: "assistant", text: "The release is ready." };
    const result = foldTeamUpdates([wake, narration, tool, wake, tool, answer]);
    expect(result).toHaveLength(2);
    expect(result[0].sources).toHaveLength(2);
    expect(result[0].steps).toEqual([narration, tool, tool]);
    expect(result[1].item).toBe(answer);
  });
  it("does not hide a verdict before a final board transition or a pending approval", () => {
    const answer: Item = { kind: "assistant", text: "The tests pass. Accepted." };
    const transition: Item = { ...tool, name: "transition", args: { to: "done" } };
    expect(foldTeamUpdates([wake, answer, transition])[1].item).toBe(answer);
    const approval: Item = { kind: "approval", name: "run_shell", args: {}, reason: "Review" };
    expect(foldTeamUpdates([wake, tool, approval, wake])).toHaveLength(3);
  });
  it("retains a typed timer reminder when expanded, including on replay", () => {
    const timer = { ...source, text: "Check the verification result", board: { rows: [], check_in: true } };
    render(<TeamUpdateLine sources={[timer]} />);
    expect(screen.getByTestId("team-update").textContent).toContain(timer.text);
    const replay = itemsFromMessages([{ role: "user", content: timer.text, source: timer }]);
    expect(foldTeamUpdates([...replay, tool, wake])[0].sources).toHaveLength(2);
  });
  it("renders a quiet disclosure and no worker note", () => {
    render(
      <TeamUpdateLine
        sources={[
          {
            ...source,
            board: {
              rows: [
                {
                  kind: "comment",
                  item: 1,
                  title: "Refund",
                  actor: "sam",
                  note: "PRIVATE LONG NOTE",
                },
              ],
            },
          },
        ]}
      />,
    );
    expect(screen.getByTestId("team-update").tagName).toBe("DETAILS");
    expect(screen.getByTestId("team-update").hasAttribute("open")).toBe(false);
    expect(screen.queryByText(/PRIVATE LONG NOTE/)).toBeNull();
  });
  it.each([
    "assigned",
    "claimed",
    "filed",
    "comment",
    "chat",
    "waiting",
    "future_kind",
  ])("handles %s without raw note parsing", (kind) => {
    const text = summarizeUpdates(
      [{ kind, actor: "sam", note: "DO NOT RENDER" }],
      getI18n().t.bind(getI18n()),
    );
    expect(text).not.toContain("DO NOT RENDER");
    expect(text).not.toContain("teamview.");
  });
  it("uses successful staffing data on replay", () => {
    const items = itemsFromMessages([
      {
        role: "tool",
        content: "ok",
        _display: {
          team_created: {
            team_id: "t",
            workers: [{ actor: "sam", persona: "swe-worker" }],
          },
        },
      },
    ]);
    expect(items[0].kind).toBe("teamcreated");
    render(
      <TeamCreatedLine workers={[{ actor: "sam", persona: "swe-worker" }]} />,
    );
    expect(screen.getByTestId("team-created").textContent).toContain(
      "1 worker",
    );
  });
});

describe("task chips", () => {
  it("uses the current title and a scoped click event", () => {
    const s = sampleTeam();
    const listener = vi.fn();
    window.addEventListener(OPEN_TASK_EVENT, listener);
    const { rerender } = render(
      <TaskBoardContext.Provider
        value={{
          board: { space: s.space, name: "Acme", items: s.items },
          sessionId: s.lead_session,
        }}
      >
        <Markdown text="[Old title](task:1)" />
      </TaskBoardContext.Provider>,
    );
    fireEvent.click(screen.getByTestId("task-chip-1"));
    expect(screen.getByTestId("task-chip-1").textContent).toBe(
      "Fix refund rounding",
    );
    expect((listener.mock.calls[0][0] as CustomEvent).detail).toEqual({
      id: 1,
      sessionId: s.lead_session,
      space: s.space,
    });
    rerender(
      <TaskBoardContext.Provider value={{ board: null, sessionId: "other" }}>
        <Markdown text="[Old title](task:1)" />
      </TaskBoardContext.Provider>,
    );
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.getByText("Old title")).toBeTruthy();
    window.removeEventListener(OPEN_TASK_EVENT, listener);
  });
  it("unknown/malformed ids are plain text and unsafe URLs stay stripped", () => {
    render(
      <Markdown text="[Unknown](task:999) [Malformed](task:-2) [Unsafe](javascript:alert)" />,
    );
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.getByText("Unsafe").getAttribute("href")).toBe("");
  });
});

describe("team view", () => {
  it("quick look opens by click, never automatically; Escape closes and restores focus", () => {
    render(<TeamQuickLook summary={sampleTeam()} onOpen={vi.fn()} />);
    const button = screen.getByRole("button", { name: "Team quick look" });
    expect(screen.queryByRole("region")).toBeNull();
    fireEvent.click(button);
    expect(
      screen.getByRole("region", { name: "Team quick look" }),
    ).toBeTruthy();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("region")).toBeNull();
    expect(document.activeElement).toBe(button);
  });
  it("a hundred workers use role rows in quick look", () => {
    render(<TeamQuickLook summary={sampleTeam(100)} onOpen={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Team quick look" }));
    expect(screen.getAllByTestId(/team-task-/)).toHaveLength(1);
    expect(screen.getByText("swe-worker × 40")).toBeTruthy();
  });
  it("all tabs work, completed tasks are collapsed, large workers expand by role", () => {
    render(
      <TeamView
        summary={sampleTeam(100)}
        sessions={[]}
        sessionId="sample"
        onClose={vi.fn()}
        onRefresh={vi.fn()}
      />,
    );
    expect(screen.getByText("1 task is waiting on you.")).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: "Workers" }));
    expect(screen.getByRole("searchbox")).toBeTruthy();
    expect(screen.getAllByTestId(/^team-worker-/)).toHaveLength(8);
    expect(screen.getByTestId("team-worker-sam")).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: "Stats" }));
    expect(screen.getByText("Top five of 100 workers")).toBeTruthy();
    fireEvent.click(screen.getByText("Show tokens over time"));
    expect(
      screen.getByRole("img", { name: "Cumulative tokens by role over time" }),
    ).toBeTruthy();
    expect(
      within(screen.getByRole("table")).getByText("Staffing"),
    ).toBeTruthy();
  });
});
