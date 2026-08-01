import { describe, expect, it, vi } from "vitest";

// Drives the REAL main.tsx: stub #root, make App throw, and assert the boundary paints. React 18
// unmounts the whole tree on an uncaught render throw, so what this pins is that a bad payload can
// no longer become a blank window with nothing on screen to report.
vi.mock("./App", () => ({
  App: () => {
    throw new Error("Objects are not valid as a React child (found: object with keys {content})");
  },
}));
vi.mock("./tailwind.css", () => ({}));
vi.mock("./styles.css", () => ({}));

describe("root error boundary", () => {
  it("shows the error and a way out instead of a blank window", async () => {
    const root = document.createElement("div");
    root.id = "root";
    document.body.appendChild(root);
    // React logs the caught error itself; silence it so a passing run stays quiet.
    const noise = vi.spyOn(console, "error").mockImplementation(() => {});
    await import("./main");
    await new Promise((r) => setTimeout(r, 100));
    noise.mockRestore();

    expect(root.textContent).toContain("OpenWorker hit an error and stopped.");
    // The message is the actionable part — it's what a user can paste into a bug report.
    expect(root.textContent).toContain("Objects are not valid as a React child");
    expect(root.querySelector("button")?.textContent).toBe("Reload");
  });
});
