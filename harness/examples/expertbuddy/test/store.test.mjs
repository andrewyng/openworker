import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Store } from "../server/store.mjs";
test("durable records survive reopening, reconcile exact requests and reject changed duplicates", async () => {
  const dir = await mkdtemp(join(tmpdir(), "inspectionbuddy-"));
  try {
    const s = await new Store(dir).init();
    await s.insert({
      id: "one",
      requestId: "a",
      requestHash: "hash",
      revision: 1,
    });
    assert.equal(
      (await s.insert({ id: "two", requestId: "a", requestHash: "hash" })).id,
      "one",
    );
    await assert.rejects(
      s.insert({ id: "three", requestId: "a", requestHash: "different" }),
    );
    await s.update("one", (r) => {
      r.status = "reviewed";
    });
    const fresh = await new Store(dir).init();
    assert.equal(fresh.get("one").status, "reviewed");
    assert.equal(fresh.get("one").revision, 2);
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
});
test("simultaneous updates serialize and failed mutations do not partially commit", async () => {
  const dir = await mkdtemp(join(tmpdir(), "inspectionbuddy-"));
  try {
    const s = await new Store(dir).init();
    await s.insert({
      id: "one",
      requestId: "a",
      requestHash: "hash",
      revision: 1,
      count: 0,
    });
    await Promise.all(
      Array.from({ length: 8 }, () =>
        s.update("one", (r) => {
          r.count++;
        }),
      ),
    );
    assert.equal(s.get("one").count, 8);
    await assert.rejects(
      s.update("one", (r) => {
        r.count = 100;
        throw Error("stop");
      }),
    );
    assert.equal((await new Store(dir).init()).get("one").count, 8);
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
});
