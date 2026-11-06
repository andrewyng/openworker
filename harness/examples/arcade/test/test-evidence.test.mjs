import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, rm, readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { ToolImageResult } from "@openworker/harness";
import { Library } from "../build/electron/library.js";
import { TestEvidence, CHECKS } from "../build/electron/test-evidence.js";
test("testing facts persist, never imply passes, bind exact digest, and preserve invalid evidence", async (t) => {
  const root = await mkdtemp(join(tmpdir(), "arcade-evidence-")),
    l = new Library(root),
    e = new TestEvidence(l);
  t.after(async () => {
    l.close();
    await rm(root, { recursive: true, force: true });
  });
  const make = () =>
    l.create(
      root,
      "Test",
      { idea: "test", kind: "Arcade", vibe: "Bright", difficulty: "Easy" },
      null,
    );
  const g = make(),
    other = make(),
    d = l.draft(g.id);
  const realDraft = l.draft.bind(l);
  l.draft = () => {
    throw Error("Empty summaries must not load draft assets");
  };
  assert.equal(e.summary(other.id).screenshots, 0);
  assert.equal(e.summary(other.id).error, undefined);
  l.draft = realDraft;
  const checks = () =>
    Object.fromEntries(
      CHECKS.map((k) => [k, { status: "not_tested", note: "Not exercised." }]),
    );
  let c = checks();
  c.movement = { status: "passed", note: "Moved" };
  assert.throws(
    () => e.report(g.id, { digest: d.digest, checks: c }),
    /Missing recorded/,
  );
  const image = new ToolImageResult(
    { digest: d.digest, width: 960, height: 600 },
    { mimeType: "image/jpeg", base64: "PIXELS_MUST_NOT_BE_SAVED" },
  );
  e.record(g.id, "keys", image);
  assert.ok(
    Object.values(e.summary(g.id).checks).every(
      (c) => c.status === "not_tested",
    ),
  );
  e.report(g.id, { digest: d.digest, checks: c });
  assert.equal(
    new TestEvidence(l).summary(g.id).checks.movement.status,
    "passed",
  );
  assert.equal(e.summary(other.id).screenshots, 0);
  assert.ok(
    !(
      await readFile(join(g.directory, "playtest-evidence.json"), "utf8")
    ).includes("PIXELS_MUST_NOT_BE_SAVED"),
  );
  l.writeDraft(g.id, "extra.txt", "change");
  assert.equal(e.summary(g.id).current, false);
  assert.throws(
    () => e.report(g.id, { digest: d.digest, checks: c }),
    /outdated/,
  );
  const next = l.draft(g.id);
  e.record(
    g.id,
    "open",
    new ToolImageResult(
      { digest: next.digest, width: 1280, height: 800 },
      { mimeType: "image/png", base64: "ignored" },
    ),
  );
  c = checks();
  c.compact = { status: "passed", note: "Checked" };
  assert.throws(
    () => e.report(g.id, { digest: next.digest, checks: c }),
    /Missing recorded/,
  );
  assert.equal(e.summary(g.id).checks.movement.status, "not_tested");
  const path = join(g.directory, "playtest-evidence.json");
  await writeFile(path, '{"version":99}');
  assert.match(e.summary(g.id).error, /unavailable/);
  assert.throws(() => e.record(g.id, "open", image), /preserved/);
  assert.equal(await readFile(path, "utf8"), '{"version":99}');
});
