import { afterEach, expect, it, vi } from "vitest";
import { openExternal } from "./tauri";

afterEach(() => {
  vi.unstubAllGlobals();
});

it("opens the URL via the Tauri opener plugin when present, not window.open", async () => {
  const openUrl = vi.fn().mockResolvedValue(undefined);
  vi.stubGlobal("__TAURI__", { opener: { openUrl } });
  const windowOpen = vi.fn();
  vi.stubGlobal("open", windowOpen);

  openExternal("https://console.anthropic.com/settings/keys");

  expect(openUrl).toHaveBeenCalledWith("https://console.anthropic.com/settings/keys");
  expect(windowOpen).not.toHaveBeenCalled();
});

it("falls back to window.open if the opener plugin call rejects", async () => {
  const openUrl = vi.fn().mockRejectedValue(new Error("not permitted"));
  vi.stubGlobal("__TAURI__", { opener: { openUrl } });
  const windowOpen = vi.fn();
  vi.stubGlobal("open", windowOpen);

  openExternal("https://console.anthropic.com/settings/keys");
  // the fallback runs in the rejection handler of the async openUrl() call
  await new Promise((resolve) => setTimeout(resolve, 0));

  expect(windowOpen).toHaveBeenCalledWith(
    "https://console.anthropic.com/settings/keys",
    "_blank",
    "noopener,noreferrer",
  );
});

it("uses window.open directly when no Tauri opener plugin is present (browser build)", () => {
  vi.stubGlobal("__TAURI__", undefined);
  const windowOpen = vi.fn();
  vi.stubGlobal("open", windowOpen);

  openExternal("https://ollama.com/download");

  expect(windowOpen).toHaveBeenCalledWith("https://ollama.com/download", "_blank", "noopener,noreferrer");
});
