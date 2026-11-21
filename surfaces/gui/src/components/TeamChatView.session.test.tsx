// The team chat poll follows the session it belongs to: the view stays mounted while
// the user picks another conversation in the sidebar, so an effect keyed on the team
// alone kept polling (and showing) the previous session's chat.
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { cleanup, render } from "@testing-library/react";
import { getTeamChat } from "../api";
import { TeamChatView } from "./TeamChatView";

vi.mock("../api", () => ({
  getTeamChat: vi.fn(async () => ({ messages: [] })),
  postTeamChat: vi.fn(async () => ({ ok: true })),
}));

beforeAll(() => {
  // jsdom has no scrollIntoView; the view calls it on every new message.
  Element.prototype.scrollIntoView = vi.fn();
});

afterEach(cleanup);

describe("TeamChatView polling", () => {
  it("follows the session it is switched to", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const { rerender } = render(
        <TeamChatView teamId="T1" sessionId="s1" onClose={vi.fn()} />,
      );
      await vi.advanceTimersByTimeAsync(3_100);
      const calls = vi.mocked(getTeamChat).mock.calls;
      expect(calls[calls.length - 1]).toEqual(["T1", "s1"]);

      // The user picks another conversation while the team chat stays open.
      rerender(<TeamChatView teamId="T1" sessionId="s2" onClose={vi.fn()} />);
      await vi.advanceTimersByTimeAsync(3_100);
      const after = vi.mocked(getTeamChat).mock.calls;
      expect(after[after.length - 1]).toEqual(["T1", "s2"]);
    } finally {
      vi.useRealTimers();
    }
  });
});
