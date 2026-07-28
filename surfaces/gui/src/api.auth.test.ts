import { afterEach, expect, it, vi } from "vitest";
import { getHealth, Session } from "./api";

afterEach(() => {
  vi.unstubAllGlobals();
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

  session.userMessage("hello", undefined, "gpt-5.6-sol", "voice_discussion");
  socket.readyState = FakeWebSocket.OPEN;
  socket.onopen?.();
  expect(socket.send).toHaveBeenCalledWith(
    JSON.stringify({
      type: "user_message",
      text: "hello",
      model: "gpt-5.6-sol",
      input_mode: "voice_discussion",
    }),
  );
});
