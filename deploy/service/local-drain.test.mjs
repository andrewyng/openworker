import { test } from "node:test";
import assert from "node:assert/strict";
import { waitForEmptyPods } from "./local-drain.mjs";
test("cleanup accepts API pods that already disappeared without kubectl wait on an empty selector", async () => {
  let reads = 0;
  await waitForEmptyPods(
    async () => {
      reads++;
      return { items: [] };
    },
    { timeoutMs: 0 },
  );
  assert.equal(reads, 1);
});
test("cleanup waits for terminating API pods and does not treat API failure as deletion", async () => {
  let reads = 0;
  await waitForEmptyPods(
    async () => ({
      items:
        ++reads === 1 ? [{ metadata: { deletionTimestamp: "pending" } }] : [],
    }),
    { intervalMs: 0 },
  );
  assert.equal(reads, 2);
  await assert.rejects(
    waitForEmptyPods(async () => {
      throw Error("API unavailable");
    }),
    /API unavailable/,
  );
  await assert.rejects(
    waitForEmptyPods(async () => ({})),
    /Invalid pod-list/,
  );
});
test("cleanup stops at its deadline while an API pod remains", async () => {
  await assert.rejects(
    waitForEmptyPods(async () => ({ items: [{}] }), { timeoutMs: 0 }),
    /deadline/,
  );
});
