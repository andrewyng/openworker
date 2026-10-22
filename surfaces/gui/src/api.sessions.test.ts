import { afterEach, expect, it, vi } from "vitest";
import { announceCloudChanged, getAllSessions, type SessionListProgress } from "./api";
import type { SessionInfo } from "./types";

afterEach(() => vi.unstubAllGlobals());
const local = { session_id: "local", agent: "cowork", title: "Local" } as SessionInfo;
const remote = { session_id: "remote", agent: "cowork", title: "Old remote", machine: "cloud:m1", machine_name: "Remote" } as SessionInfo;

it("publishes local rows before remote discovery finishes", async () => {
  let finish!: (r: Response) => void;
  const cloud = new Promise<Response>(resolve => { finish = resolve; });
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (url.endsWith("/v1/cloud/machines")) return cloud;
    if (url.endsWith("/v1/machines")) return Response.json({ machines: [] });
    return Response.json({ sessions: [local] });
  }));
  const progress: SessionListProgress[] = [];
  let done = false;
  const result = getAllSessions(p => progress.push(p)).then(rows => { done = true; return rows; });
  await vi.waitFor(() => expect(progress).toContainEqual({ type: "local", sessions: [local] }));
  expect(done).toBe(false);
  expect(progress).toContainEqual({ type: "remote", state: "loading" });
  finish(Response.json({ session: "ok", machines: [] }));
  await expect(result).resolves.toEqual([local]);
  expect(progress[progress.length - 1]).toEqual({ type: "remote", state: "idle" });
});

it.each(["failure", "empty", "signed_out"])("remote %s retains or removes old rows correctly", async state => {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (url.endsWith("/v1/cloud/machines")) return state === "failure"
      ? Response.json({ detail: "failed" }, { status: 503 })
      : Response.json({ session: state === "empty" ? "ok" : "signed_out", machines: [] });
    if (url.endsWith("/v1/machines")) return Response.json({ machines: [] });
    return Response.json({ sessions: [] });
  }));
  const progress: SessionListProgress[] = [];
  const rows = await getAllSessions(p => progress.push(p), [local, remote]);
  expect(rows).toEqual(state === "failure" ? [{ ...remote, machine_offline: true }] : []);
  expect(progress[progress.length - 1]).toEqual({ type: "remote", state: state === "failure" ? "error" : "idle" });
});

it("a previous account's discovery cannot complete into the new scope", async () => {
  let finish!: (r: Response) => void;
  const cloud = new Promise<Response>(resolve => { finish = resolve; });
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (url.endsWith("/v1/cloud/machines")) return cloud;
    if (url.endsWith("/v1/machines")) return Response.json({ machines: [] });
    return Response.json({ sessions: [local] });
  }));
  const progress: SessionListProgress[] = [];
  const result = getAllSessions(p => progress.push(p), [remote]);
  const check = expect(result).rejects.toThrow("scope changed");
  await vi.waitFor(() => expect(progress).toContainEqual({ type: "remote", state: "loading" }));
  announceCloudChanged();
  finish(Response.json({ session: "ok", machines: [] }));
  await check;
  expect(progress[progress.length - 1]).toEqual({ type: "remote", state: "loading" });
});
