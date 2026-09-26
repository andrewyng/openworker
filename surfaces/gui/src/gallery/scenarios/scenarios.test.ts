// Scenario files are data the real app boots on, so a malformed one fails silently in the
// browser. These checks catch the mistakes that would: ids, session wiring, and a
// transcript the app's own message parser accepts.
import { describe, expect, it } from "vitest";

import { itemsFromMessages } from "../../itemsFromMessages";
import { SCENARIOS } from ".";

describe("gallery scenarios", () => {
  it("have unique ids and session ids", () => {
    expect(new Set(SCENARIOS.map((s) => s.id)).size).toBe(SCENARIOS.length);
    expect(new Set(SCENARIOS.map((s) => s.session.session_id)).size).toBe(SCENARIOS.length);
    for (const s of SCENARIOS) expect(s.id).toMatch(/^[a-z0-9-]+$/);
  });

  for (const s of SCENARIOS) {
    it(`${s.id}: pending items belong to the session and are pending`, () => {
      for (const p of s.pending ?? []) {
        expect(p.session_id).toBe(s.session.session_id);
        expect(p.state).toBe("pending");
      }
    });

    it(`${s.id}: the transcript parses into items, every tool call answered or last`, () => {
      const items = itemsFromMessages(s.messages as any);
      expect(items.length).toBeGreaterThan(0);
      const calls = s.messages.flatMap((m: any) => (m.tool_calls ?? []).map((c: any) => c.id));
      const answered = new Set(s.messages.filter((m: any) => m.role === "tool").map((m: any) => m.tool_call_id));
      // Only the trailing calls may be unanswered: that is the moment the scenario freezes on.
      const open = calls.filter((id: string) => !answered.has(id));
      expect(open.length).toBeLessThanOrEqual(1);
      if (open.length) expect(open[0]).toBe(calls[calls.length - 1]);
    });

    it(`${s.id}: no secrets, emails or home paths`, () => {
      const text = JSON.stringify(s);
      expect(text).not.toMatch(/sk-[A-Za-z0-9]{16,}|ghp_[A-Za-z0-9]{20,}|xox[bp]-|AKIA[0-9A-Z]{16}/);
      // The export script rewrites every address to user@example.com; anything else is a leak.
      expect(text.replace(/user@example\.com/g, "")).not.toMatch(/[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[a-z]{2,}/);
      expect(text).not.toMatch(/\/Users\/[a-z]+\//);
    });
  }
});
