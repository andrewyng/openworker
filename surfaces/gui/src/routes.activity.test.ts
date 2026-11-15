// T3-11: Activity has its own address, so a reload stays on it (and on its tab).
import { afterEach, describe, expect, it, vi } from "vitest";

async function boot(hash: string) {
  window.history.replaceState(null, "", "/" + hash);
  vi.resetModules();
  return import("./routes");
}

afterEach(() => window.history.replaceState(null, "", "/"));

describe("#/activity", () => {
  it("reads the tab a link asks for; a bare #/activity is the Log", async () => {
    expect((await boot("#/activity/usage")).initialActivityTab).toBe("usage");
    expect((await boot("#/activity/log")).initialActivityTab).toBe("log");
    expect((await boot("#/activity")).initialActivityTab).toBe("log");
    expect((await boot("#/settings/general")).initialActivityTab).toBe("");
  });

  it("keeps the link until Activity shows itself, then reflects the open tab", async () => {
    const r = await boot("#/activity/usage");
    r.reflectSession("s1"); // the app's mount still says "session": must not overwrite the link
    expect(window.location.hash).toBe("#/activity/usage");
    r.reflectActivity("usage");
    expect(window.location.hash).toBe("#/activity/usage");
    r.reflectActivity("log");
    expect(window.location.hash).toBe("#/activity/log");
    // leaving Activity for a session hands the address back
    r.reflectSession("s1");
    expect(window.location.hash).toBe("#/s/s1");
  });

  it("Settings after Activity takes the address", async () => {
    const r = await boot("");
    r.reflectActivity("usage");
    r.reflectSettings("appearance");
    expect(window.location.hash).toBe("#/settings/general");
    r.reflectActivity("usage");
    expect(window.location.hash).toBe("#/activity/usage");
  });
});
