// Phase 3 (E26c, V3-16): a joined machine's automations are listed beside this machine's,
// and every call for one goes to the machine it came from, through the controller's proxy.
import { afterEach, describe, expect, it, vi } from "vitest";
import { getAutomation, getAutomations, machineOfAutomation, machineOfSession, runAutomation } from "./api";

afterEach(() => vi.unstubAllGlobals());

const task = (id: string) => ({ id, title: id, instructions: "", schedule: "", workspace: "", agent: "cowork",
  enabled: true, next_run: null, last_run: null, last_status: null, run_count: 0, notify_on_completion: false,
  always_allowed: [] });

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
        body = { task: task("task-r"), runs: [{ run_id: "run-1", session_id: "__run__run-1" }] };
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
});
