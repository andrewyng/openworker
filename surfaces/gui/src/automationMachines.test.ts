// Phase 3 (E26c, V3-16): a joined machine's automations are listed beside this machine's,
// and every call for one goes to the machine it came from, through the controller's proxy.
import { afterEach, describe, expect, it, vi } from "vitest";
import { getAutomation, getAutomations, machineOfAutomation, machineOfSession, runAutomation } from "./api";

afterEach(() => vi.unstubAllGlobals());

const task = (id: string) => ({ id, title: id, instructions: "", schedule: "", workspace: "", agent: "cowork",
  enabled: true, next_run: null, last_run: null, last_status: null, run_count: 0, notify_on_completion: false,
  always_allowed: [] });

const run = {
  run_id: "run-1", task_id: "task-r", session_id: "__run__run-1", started_at: 100,
  finished_at: 110, status: "ok", result_text: null, artifacts: [], error: null, trigger: "schedule",
};

describe("automations on joined machines", () => {
  it("lists remote automations with their machine and routes their calls there", async () => {
    const urls: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (u: string) => {
      urls.push(String(u));
      const url = String(u);
      let body: any;
      if (url.endsWith("/v1/machines")) {
        body = { machines: [{ id: "m1", name: "build-box-1", connected: true }, { id: "m2", name: "off", connected: false }] };
      } else if (url.includes("/v1/machines/m1/p/v1/automations/task-r/run")) {
        body = { ok: true, run_id: "run-9", session_id: "__run__run-9", workspace: "", agent: "cowork", prompt: "go" };
      } else if (url.includes("/v1/machines/m1/p/v1/automations/task-r")) {
        body = { task: task("task-r"), runs: [run] };
      } else if (url.includes("/v1/machines/m1/p/v1/automations")) {
        body = { tasks: [task("task-r")] };
      } else {
        body = { tasks: [task("task-l")] };
      }
      return { ok: true, json: async () => body } as any;
    }));
    const tasks = await getAutomations();
    expect(tasks.map((t) => [t.id, t.machine_name ?? null])).toEqual([["task-l", null], ["task-r", "build-box-1"]]);
    expect(urls.some((u) => u.includes("/machines/m2/"))).toBe(false); // offline: not asked
    expect(machineOfAutomation("task-r")).toBe("m1");
    expect(machineOfAutomation("task-l")).toBeNull();
    await getAutomation("task-r");
    expect(urls[urls.length - 1]).toContain("/v1/machines/m1/p/v1/automations/task-r");
    expect(machineOfSession("__run__run-1")).toBe("m1");
    await runAutomation("task-r");
    expect(machineOfSession("__run__run-9")).toBe("m1");
  });

  it("can retry discovery when an unknown remote task's machine temporarily fails", async () => {
    const remoteTask = task("task-remote-retry");
    let lookupCount = 0;
    vi.stubGlobal("fetch", async (url: string) => {
      const path = new URL(url).pathname;
      const response = (body: object, status = 200) => new Response(JSON.stringify(body), { status });
      if (path === "/v1/machines") return response({ machines: [{ id: "m1", name: "remote", connected: true }] });
      if (path === "/v1/machines/m1/p/v1/automations") {
        lookupCount += 1;
        return lookupCount === 1 ? response({ error: "unavailable" }, 503) : response({ tasks: [remoteTask] });
      }
      if (path === "/v1/machines/m1/p/v1/automations/task-remote-retry") return response({ task: remoteTask, runs: [] });
      if (path === "/v1/automations/task-remote-retry") return response({ error: "not found" });
      return response({ tasks: [] });
    });

    await expect(getAutomation(remoteTask.id)).rejects.toThrow();
    await expect(getAutomation(remoteTask.id)).resolves.toMatchObject({ task: { id: remoteTask.id, machine_id: "m1" } });
  });
});

describe("automation detail responses", () => {
  it.each([
    { task: {} },
    { task: "unknown" },
    { task: task("some-other-task") },
    { task: task("task-detail"), runs: {} },
    { task: task("task-detail"), runs: [{ run_id: "wrong-shape" }] },
  ])("rejects a malformed success response %#", async (body) => {
    vi.stubGlobal("fetch", async (url: string) => {
      const data = url.endsWith("/v1/automations/task-detail") ? body : {};
      return new Response(JSON.stringify(data));
    });
    await expect(getAutomation("task-detail")).rejects.toThrow();
  });
});
