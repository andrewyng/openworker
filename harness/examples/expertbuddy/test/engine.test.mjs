import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { Store } from "../server/store.mjs";
import { InspectionEngine } from "../server/engine.mjs";
import { AppError } from "../server/domain.mjs";
test("restart reattaches the original run without starting a second agent turn", async () => {
  const dir = await mkdtemp(join(tmpdir(), "inspectionbuddy-recover-"));
  try {
    const store = await new Store(dir).init();
    await store.insert({
      id: "one",
      requestId: "a",
      requestHash: "hash",
      revision: 1,
      status: "analyzing",
      activity: [],
      service: { session: { sessionId: "original" }, submissionId: "saved" },
    });
    let started = 0,
      resumed = 0;
    const agent = {
      async start() {
        started++;
        throw Error("Must not start again");
      },
      async resume(r) {
        resumed++;
        assert.equal(r.service.submissionId, "saved");
        return {};
      },
      async consume() {
        return { report: { title: "Recovered result" }, trace: null };
      },
    };
    await new InspectionEngine(store, agent).assess("one", { recover: true });
    assert.equal(started, 0);
    assert.equal(resumed, 1);
    assert.equal(store.get("one").status, "review");
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
});
test("an uncertain pre-receipt interruption is reported and never automatically resubmitted", async () => {
  const dir = await mkdtemp(join(tmpdir(), "inspectionbuddy-unknown-"));
  try {
    const store = await new Store(dir).init();
    await store.insert({
      id: "one",
      requestId: "a",
      requestHash: "hash",
      revision: 1,
      status: "analyzing",
      activity: [],
    });
    let starts = 0;
    const agent = {
      async start() {
        starts++;
      },
      async resume() {
        throw new AppError(
          409,
          "Outcome needs reconciliation; not resubmitted.",
        );
      },
    };
    await new InspectionEngine(store, agent).assess("one", { recover: true });
    assert.equal(starts, 0);
    assert.equal(store.get("one").status, "error");
    assert.match(store.get("one").error, /not resubmitted/);
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
});
