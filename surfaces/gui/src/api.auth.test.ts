import { afterEach, expect, it, vi } from "vitest";
import { connectManaged, getHealth, Session } from "./api";

afterEach(() => {
  vi.unstubAllGlobals();
});

it("requests installation consent only for explicit GitHub additions", async () => {
  const bodies: unknown[] = [];
  vi.stubGlobal("fetch", vi.fn(async (_url: string, init?: RequestInit) => {
    bodies.push(JSON.parse(String(init?.body)));
    return { json: async () => ({ ok: true }) } as Response;
  }));
  await connectManaged("github");
  await connectManaged("github", { flow: "install" });
  await connectManaged("hubspot", { access: "read", flow: "install" });
  expect(bodies).toEqual([{}, { flow: "install" }, { access: "read" }]);
});

it("authenticates REST and session WebSocket calls with the launch token", async () => {
  vi.stubGlobal("__COWORKER_API_TOKEN__", "launch-token");
  const request = vi.fn(async (_url: string, init?: RequestInit) => {
    expect(new Headers(init?.headers).get("X-OpenWorker-Token")).toBe("launch-token");
    return { json: async () => ({ status: "ok" }) } as Response;
  });
  vi.stubGlobal("fetch", request);

  class FakeWebSocket {
    static readonly CONNECTING = 0;
    static readonly OPEN = 1;
    readyState = FakeWebSocket.CONNECTING;
    onmessage: ((event: MessageEvent) => void) | null = null;
    onopen: (() => void) | null = null;
    onclose: (() => void) | null = null;
    send = vi.fn();

    constructor(
      public readonly url: string,
      public readonly protocols?: string | string[],
    ) {}
  }
  vi.stubGlobal("WebSocket", FakeWebSocket);

  await getHealth();
  expect(request).toHaveBeenCalledOnce();

  const session = new Session("s1", "/workspace", "code", { onEvent: vi.fn() });
  const socket = (session as unknown as { ws: FakeWebSocket }).ws;
  expect(socket.protocols).toEqual(["openworker", "launch-token"]);
});

it("reads the browser-dev token at call time, below the shell-injected launch token", async () => {
  const sent: (string | null)[] = [];
  vi.stubGlobal("fetch", vi.fn(async (_url: string, init?: RequestInit) => {
    sent.push(new Headers(init?.headers).get("X-OpenWorker-Token"));
    return { json: async () => ({ status: "ok" }) } as Response;
  }));

  // vite.config.ts injects the standalone server's token per page load; a server restart
  // writes a new one, and the next load must carry it rather than a value baked at startup.
  vi.stubGlobal("__COWORKER_DEV_TOKEN__", "dev-token-1");
  await getHealth();
  vi.stubGlobal("__COWORKER_DEV_TOKEN__", "dev-token-2");
  await getHealth();
  vi.stubGlobal("__COWORKER_API_TOKEN__", "launch-token");
  await getHealth();

  expect(sent).toEqual(["dev-token-1", "dev-token-2", "launch-token"]);
});
