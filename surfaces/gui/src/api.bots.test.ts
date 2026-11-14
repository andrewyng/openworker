import { afterEach, expect, it, vi } from "vitest";

afterEach(() => { vi.unstubAllGlobals(); vi.resetModules(); });

function network() {
  let online = true;
  const bot = { id: "cloud-bot", name: "Researcher", session_id: "cloud-session", workspace: "/state/bots/one",
    agent: "cowork", enabled: true, execution: "dedicated", instructions: "Review research", cron: "", timezone: "UTC", next_check: null };
  const request = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = new URL(String(input)).pathname;
    let result: unknown = {};
    if (path === "/v1/machines") result = { machines: [{ id: "remote", name: "Cloud VM", connected: online }] };
    else if (path === "/v1/cloud/machines") result = { session: "signed_out", machines: [] };
    else if (path === "/v1/bots") result = { bots: [] };
    else if (path === "/v1/machines/remote/p/v1/bots/runtime") result = { dedicated: true, bot_count: 0 };
    else if (path === "/v1/machines/remote/p/v1/bots" && init?.method === "POST") result = { bot };
    else if (path === "/v1/machines/remote/p/v1/bots") result = { bots: [bot] };
    else if (path.endsWith("/tasks")) {
      if (!online) throw new Error("Host offline");
      result = { job_id: "job-one" };
    } else if (path.endsWith("/cloud-bot")) result = { bot, jobs: [], running: false };
    return { ok: true, json: async () => result };
  });
  vi.stubGlobal("fetch", request);
  return { request, offline: () => { online = false; } };
}

it("creates on an eligible dedicated runtime and registers its conversation route", async () => {
  const { request } = network();
  const api = await import("./api");
  expect((await api.getBotRuntimes()).map(m => m.id)).toEqual(["remote"]);
  const body = { name: "Researcher", instructions: "Review research", cron: "", timezone: "UTC" };
  const result = await api.createBot(body, "remote");
  expect(result.bot.machine_id).toBe("remote");
  expect(api.machineOfSession("cloud-session")).toBe("remote");
  const create = request.mock.calls.find(([url, init]) => String(url).endsWith("/p/v1/bots") && init?.method === "POST");
  expect(JSON.parse(String(create?.[1]?.body))).toEqual({ ...body, dedicated: true });
});

it("never sends an offline cloud bot delegation to the local runtime", async () => {
  const { request, offline } = network();
  const api = await import("./api");
  const rows = await api.getBots();
  expect(rows[0].machine_name).toBe("Cloud VM");
  offline();
  expect((await api.getBots())[0].machine_offline).toBe(true);
  await expect(api.delegateBotTask("cloud-bot", "Private draft")).rejects.toThrow("Host offline");
  const taskCalls = request.mock.calls.filter(([url]) => String(url).endsWith("/tasks"));
  expect(taskCalls).toHaveLength(1);
  expect(String(taskCalls[0][0])).toContain("/v1/machines/remote/p/v1/bots/cloud-bot/tasks");
});

it("refuses unknown bot destinations instead of falling back to this computer", async () => {
  const { request } = network();
  const api = await import("./api");
  await expect(api.delegateBotTask("unknown", "Private draft")).rejects.toThrow("Bot unavailable");
  expect(request.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
});

it("does not create on a shared remote machine", async () => {
  const request = vi.fn(async () => ({ ok: true, json: async () => ({ dedicated: false, bot_count: 0 }) }));
  vi.stubGlobal("fetch", request);
  const api = await import("./api");
  await expect(api.createBot({ name: "A", instructions: "X", cron: "", timezone: "UTC" }, "shared"))
    .rejects.toThrow("unused dedicated");
  expect(request).toHaveBeenCalledTimes(1);
});
