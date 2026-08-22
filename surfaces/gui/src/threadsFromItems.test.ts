import { describe, expect, it } from "vitest";
import { threadsFromItems } from "./App";

// Which brain threads a session touched. The subtlety is that the tool ARGUMENT is not the
// thread id — brain_note accepts an id or a title — so this keys on the RESULT.
const tool = (name: string, extra: Record<string, unknown> = {}) => ({ kind: "tool", name, ...extra });

describe("threadsFromItems", () => {
  it("prefers the _display sidecar, which is exact and never truncated", () => {
    const out = threadsFromItems([
      tool("brain_recall", { display: { threads: ["a", "b", "c"], mode: "read" } }),
    ]);
    expect(out.map((t) => t.id)).toEqual(["a", "b", "c"]);
    expect(out.every((t) => t.read && !t.written)).toBe(true);
  });

  it("does NOT key on the argument, which can be a title that slugifies to nothing real", () => {
    // brain_note(thread: "OpenScienceLab / openEvolve — Phase 2") writes openevolve-phase-2;
    // trusting the argument would invent a thread that has no file.
    const out = threadsFromItems([
      tool("brain_note", {
        args: { thread: "OpenScienceLab / openEvolve — Phase 2" },
        preview: JSON.stringify({ ok: true, thread: "openevolve-phase-2" }),
      }),
    ]);
    expect(out.map((t) => t.id)).toEqual(["openevolve-phase-2"]);
  });

  it("falls back to parsing the result preview when no sidecar is present", () => {
    const out = threadsFromItems([
      tool("brain_recall", { preview: JSON.stringify({ threads: [{ id: "x" }, { id: "y" }] }) }),
    ]);
    expect(out.map((t) => t.id)).toEqual(["x", "y"]);
  });

  it("merges a thread that was recalled and then written", () => {
    const out = threadsFromItems([
      tool("brain_recall", { display: { threads: ["phase-2"] } }),
      tool("brain_note", { display: { threads: ["phase-2"] } }),
    ]);
    expect(out).toEqual([{ id: "phase-2", read: true, written: true }]);
  });

  it("contributes nothing from a truncated or in-flight row rather than guessing", () => {
    // The live event carries a 300-char preview; a recall's real result is ~9KB, so the parse
    // fails. A half-parsed list would under-report while looking complete.
    const out = threadsFromItems([
      tool("brain_recall", { preview: '{"query": "x", "threads": [{"id": "local-model-rel' }),
      tool("brain_note", {}),
    ]);
    expect(out).toEqual([]);
  });

  it("ignores non-brain tools and non-tool items", () => {
    const out = threadsFromItems([
      tool("write_file", { display: { threads: ["nope"] } }),
      { kind: "notice", tone: "info", text: "hi" },
      tool("brain_note", { display: { threads: ["real"] } }),
    ]);
    expect(out.map((t) => t.id)).toEqual(["real"]);
  });

  it("drops empty and non-string ids", () => {
    const out = threadsFromItems([tool("brain_note", { display: { threads: ["", null, 7, "ok"] } })]);
    expect(out.map((t) => t.id)).toEqual(["ok"]);
  });
});
