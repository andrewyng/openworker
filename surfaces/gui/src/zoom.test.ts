import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { initZoom } from "./zoom";

const invoke = vi.fn();
let cleanup: (() => void) | undefined;
const key = (value: string, modifiers: KeyboardEventInit = { metaKey: true }) => {
  const event = new KeyboardEvent("keydown", { key: value, bubbles: true, cancelable: true, ...modifiers });
  window.dispatchEvent(event);
  return event;
};
const flush = async () => { await new Promise((resolve) => setTimeout(resolve, 0)); };

beforeEach(() => {
  localStorage.clear();
  invoke.mockReset().mockResolvedValue(undefined);
  vi.stubGlobal("__TAURI__", { core: { invoke } });
});
afterEach(() => { cleanup?.(); cleanup = undefined; vi.unstubAllGlobals(); });

describe("desktop UI zoom", () => {
  it("supports Cmd+=, Cmd+plus, Cmd+minus and Cmd+0, including rapid presses", async () => {
    cleanup = initZoom();
    expect(key("=").defaultPrevented).toBe(true);
    key("+", { metaKey: true, shiftKey: true });
    key("-");
    key("0");
    await flush();
    expect(invoke.mock.calls.map((call) => call[1].factor)).toEqual([1, 1.1, 1.2, 1.1, 1]);
    expect(localStorage.getItem("openwork-ui-zoom")).toBeNull();
  });

  it("supports Ctrl shortcuts while focus is in the composer", async () => {
    cleanup = initZoom();
    const input = document.createElement("textarea");
    document.body.append(input);
    input.focus();
    const event = new KeyboardEvent("keydown", { key: "=", ctrlKey: true, bubbles: true, cancelable: true });
    input.dispatchEvent(event);
    await flush();
    expect(event.defaultPrevented).toBe(true);
    expect(invoke).toHaveBeenLastCalledWith("set_ui_zoom", { factor: 1.1 });
    input.remove();
  });

  it("persists and restores zoom", async () => {
    localStorage.setItem("openwork-ui-zoom", "1.5");
    cleanup = initZoom();
    key("=");
    await flush();
    expect(invoke.mock.calls.map((call) => call[1].factor)).toEqual([1.5, 1.6]);
    expect(localStorage.getItem("openwork-ui-zoom")).toBe("1.6");
  });

  it.each(["NaN", "Infinity", "0", "3", "", "garbage"])("ignores invalid stored zoom %s", async (value) => {
    localStorage.setItem("openwork-ui-zoom", value);
    cleanup = initZoom();
    await flush();
    expect(invoke).toHaveBeenLastCalledWith("set_ui_zoom", { factor: 1 });
  });

  it("clamps zoom to 50–200%", async () => {
    cleanup = initZoom();
    for (let i = 0; i < 30; i++) key("=");
    await flush();
    expect(invoke).toHaveBeenLastCalledWith("set_ui_zoom", { factor: 2 });
    for (let i = 0; i < 30; i++) key("-");
    await flush();
    expect(invoke).toHaveBeenLastCalledWith("set_ui_zoom", { factor: 0.5 });
  });

  it("leaves browser-native zoom untouched", async () => {
    vi.stubGlobal("__TAURI__", undefined);
    cleanup = initZoom();
    expect(key("=").defaultPrevented).toBe(false);
    await flush();
    expect(invoke).not.toHaveBeenCalled();
  });

  it("ignores ordinary typing, Alt shortcuts, composition and unrelated shortcuts", async () => {
    cleanup = initZoom();
    await flush();
    invoke.mockClear();
    for (const event of [key("=", {}), key("=", { metaKey: true, altKey: true }), key("=", { metaKey: true, isComposing: true }), key("b")]) {
      expect(event.defaultPrevented).toBe(false);
    }
    await flush();
    expect(invoke).not.toHaveBeenCalled();
  });

  it("survives unavailable storage", async () => {
    const get = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("blocked"); });
    const set = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("blocked"); });
    try {
      cleanup = initZoom();
      key("=");
      key("=");
      await flush();
      expect(invoke).toHaveBeenLastCalledWith("set_ui_zoom", { factor: 1.2 });
    } finally { get.mockRestore(); set.mockRestore(); }
  });

  it("does not persist failed changes and recovers on the next shortcut", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    try {
      cleanup = initZoom();
      await flush();
      invoke.mockRejectedValueOnce(new Error("unavailable"));
      key("=");
      await flush();
      expect(localStorage.getItem("openwork-ui-zoom")).toBeNull();
      key("=");
      await flush();
      expect(invoke).toHaveBeenLastCalledWith("set_ui_zoom", { factor: 1.1 });
    } finally { warn.mockRestore(); }
  });

  it("removes the shortcut listener on cleanup", async () => {
    cleanup = initZoom();
    await flush();
    cleanup();
    invoke.mockClear();
    expect(key("=").defaultPrevented).toBe(false);
    await flush();
    expect(invoke).not.toHaveBeenCalled();
  });
});
