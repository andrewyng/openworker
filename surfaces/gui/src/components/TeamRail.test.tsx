import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { TeamRail } from "./TeamRail";
import { sampleTeam } from "../gallery/states/team-view";
import type { SessionInfo } from "../types";
afterEach(cleanup);
const summary = sampleTeam(100);
const members = summary.workers.map(
  (w) =>
    ({
      session_id: w.session_id,
      agent: w.role,
      team: { actor: w.actor },
      machine_name: "build-box",
    }) as SessionInfo,
);
it("keeps actions available when collapsed and chat off explained only on request", () => {
  const onChat = vi.fn(),
    onTeam = vi.fn();
  render(
    <TeamRail
      members={members.slice(0, 2)}
      chatEnabled={false}
      onChat={onChat}
      onTeam={onTeam}
      open={false}
      onToggle={vi.fn()}
    />,
  );
  expect(screen.queryByTestId("team-panel")).toBeNull();
  expect(screen.queryByTestId("team-chat-off")).toBeNull();
  fireEvent.click(screen.getByTestId("team-chat-action"));
  expect(screen.getByTestId("team-chat-off")).toBeTruthy();
  expect(onChat).not.toHaveBeenCalled();
  fireEvent.click(screen.getByTestId("rail-open-team-view"));
  expect(onTeam).toHaveBeenCalledOnce();
});
it("bounds large rosters, hands exact filters to Workers and preserves empty search", () => {
  const open = vi.fn();
  render(
    <TeamRail
      members={members}
      summary={summary}
      chatEnabled
      onWorkers={open}
      open
      onToggle={vi.fn()}
    />,
  );
  expect(screen.getAllByTestId(/^team-row-/).length).toBeLessThan(20);
  fireEvent.change(screen.getByRole("searchbox"), {
    target: { value: "build-box" },
  });
  expect(screen.getAllByTestId(/^team-row-/)).toHaveLength(6);
  fireEvent.click(screen.getByText("View all 100 matches"));
  expect(open).toHaveBeenLastCalledWith({ query: "build-box" });
  fireEvent.change(screen.getByRole("searchbox"), {
    target: { value: "not-a-worker" },
  });
  expect(screen.queryAllByTestId(/^team-row-/)).toHaveLength(0);
  expect(screen.getByRole("status").textContent).toBe("0 matching workers");
});
