import { afterEach, expect, it, vi } from "vitest";
import { openExternal } from "./tauri";

afterEach(() => vi.unstubAllGlobals());

it("does not bypass a rejected opener scope", async () => {
  const invoke = vi.fn().mockResolvedValue(null);
  vi.stubGlobal("__TAURI__", {
    opener: { openUrl: vi.fn().mockRejectedValue(new Error("ForbiddenUrl")) },
    core: { invoke },
  });
  openExternal("irc://example.invalid/channel");
  await new Promise((resolve) => setTimeout(resolve, 0));
  expect(invoke).not.toHaveBeenCalled();
});

it("uses the scoped plugin when the desktop opener JS API is absent", async () => {
  const invoke = vi.fn().mockRejectedValue(new Error("ForbiddenUrl"));
  vi.stubGlobal("__TAURI__", { core: { invoke } });
  openExternal("irc://example.invalid/channel");
  await new Promise((resolve) => setTimeout(resolve, 0));
  expect(invoke.mock.calls).toEqual([
    ["plugin:opener|open_url", { url: "irc://example.invalid/channel" }],
  ]);
});
