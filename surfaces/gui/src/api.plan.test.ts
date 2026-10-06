import { afterEach, expect, it, vi } from "vitest";
import { machineOfSession, registerSessionMachine, replayPlan } from "./api";
afterEach(() => { vi.unstubAllGlobals(); registerSessionMachine("remote-plan", null); registerSessionMachine("replayed-plan", null); });
it("replays on the origin machine and routes the resulting session there", async () => {
  const request = vi.fn(async (_url: string, _init?: RequestInit) => ({ ok: true, json: async () => ({ session_id: "replayed-plan", workspace: "/work", agent: "code" }) }));
  vi.stubGlobal("fetch", request);
  registerSessionMachine("remote-plan", "build-box");
  await replayPlan("remote-plan", "older-approved");
  expect(String(request.mock.calls[0][0])).toContain("/v1/machines/build-box/p/v1/sessions/remote-plan/plan/replay");
  expect(machineOfSession("replayed-plan")).toBe("build-box");
});
