import { describe, expect, it } from "vitest";
import { teamRoster, filterWorkers } from "./teamRoster";
import { sampleTeam } from "./gallery/states/team-view";
import type { SessionInfo } from "./types";

describe("team roster projection", () => {
  it("merges by session id and searches names, roles, tasks and machines", () => {
    const summary = sampleTeam(100);
    const sam = summary.workers[0];
    const session = {
      session_id: sam.session_id,
      agent: sam.role,
      machine_name: "build-box",
      team: { actor: sam.actor },
      attention: 1,
    } as SessionInfo;
    const entries = teamRoster([session], summary);
    expect(entries).toHaveLength(100);
    expect(filterWorkers(entries, { query: "BUILD-BOX" })).toHaveLength(1);
    expect(
      filterWorkers(entries, { query: "sam" }).some(
        (w) => w.id === sam.session_id,
      ),
    ).toBe(true);
    expect(filterWorkers(entries, { role: sam.role })).toHaveLength(40);
    expect(filterWorkers(entries, { query: "refund" }).length).toBeGreaterThan(
      0,
    );
    expect(
      filterWorkers(entries, { attention: true }).some(
        (w) => w.id === sam.session_id,
      ),
    ).toBe(true);
    expect(filterWorkers(entries, { query: "missing-worker" })).toHaveLength(0);
  });
  it("does not turn a completed task into attention, or infer status from its text", () => {
    const summary = sampleTeam();
    summary.items = summary.items.map((i) => ({
      ...i,
      group: "done",
      title: "waiting for approval",
    }));
    const entries = teamRoster([], summary);
    expect(entries.some((w) => w.attention)).toBe(false);
  });
});
