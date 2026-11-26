import { afterEach, expect, it, vi } from "vitest";
import { pickFolderViaServer } from "./api";

afterEach(() => vi.unstubAllGlobals());

it.each([
  { response: { ok: true, path: "/tmp/project" }, path: "/tmp/project", unavailable: false },
  { response: { ok: false, canceled: true }, path: null, unavailable: false },
  { response: { ok: false, error: "no native folder picker available" }, path: null, unavailable: true },
])("returns $path with unavailable=$unavailable", async ({ response, path, unavailable }) => {
  vi.stubGlobal("fetch", vi.fn(async () => ({ json: async () => response })));
  const onUnavailable = vi.fn();

  await expect(pickFolderViaServer(onUnavailable)).resolves.toBe(path);
  expect(onUnavailable).toHaveBeenCalledTimes(unavailable ? 1 : 0);
});

it("reports a failed request as unavailable", async () => {
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
  const onUnavailable = vi.fn();

  await expect(pickFolderViaServer(onUnavailable)).resolves.toBeNull();
  expect(onUnavailable).toHaveBeenCalledOnce();
});
