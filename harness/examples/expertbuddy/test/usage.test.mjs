import { test } from "node:test";
import assert from "node:assert/strict";
import { usageTotals } from "../public/usage.js";
test("inspection usage includes follow-ups and never double counts reasoning or cache tokens", () => {
  const t = (i, o) => ({
    summary: {
      usage: {
        input: { total: i, unknown: 0 },
        output: { total: o, unknown: 0 },
        reasoning: { total: 100 },
        cache_read: { total: 200 },
      },
    },
  });
  assert.deepEqual(usageTotals([t(1000, 200), t(500, 80)]), {
    input: 1500,
    output: 280,
    total: 1780,
    partial: false,
    turns: 2,
  });
});
test("unknown usage remains unknown or visibly partial", () => {
  assert.equal(usageTotals([{}]).total, null);
  assert.equal(
    usageTotals([
      {
        summary: {
          usage: {
            input: { total: 100, unknown: 0 },
            output: { total: null, unknown: 1 },
          },
        },
      },
    ]).partial,
    true,
  );
});
