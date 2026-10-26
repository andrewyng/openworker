import { afterEach, expect, it, vi } from "vitest";
import { getTeamSummary, registerSessionMachine } from "./api";
import { sampleTeam } from "./gallery/states/team-view";
afterEach(() => vi.unstubAllGlobals());
it("routes the summary to the lead's machine, not the local engine", async () => {
  const request = vi.fn(async (_url: string) => ({
    ok: true,
    json: async () => sampleTeam(),
  }));
  vi.stubGlobal("fetch", request);
  registerSessionMachine("remote-lead", "build-box");
  await getTeamSummary("remote-lead", "team-1");
  expect(String(request.mock.calls[0][0])).toContain(
    "/v1/machines/build-box/p/v1/teams/team-1/summary",
  );
});
it("rejects an unavailable or incompatible summary instead of showing invented statistics", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({ ok: true, json: async () => ({ ok: true }) })),
  );
  await expect(getTeamSummary("local-lead", "team-1")).rejects.toThrow(
    "unavailable",
  );
});
