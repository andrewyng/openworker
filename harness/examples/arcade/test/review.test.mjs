import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Library } from "../build/electron/library.js";
import { DraftReview } from "../build/electron/review.js";
test("comparison pins exact approval bytes, rejects stale drafts, isolates saves and expires", async (t) => {
  const root = await mkdtemp(join(tmpdir(), "arcade-review-")),
    l = new Library(root),
    r = new DraftReview();
  t.after(async () => {
    l.close();
    await rm(root, { recursive: true, force: true });
  });
  const g = l.create(
    root,
    "Review",
    { idea: "test", kind: "Arcade", vibe: "Bright", difficulty: "Easy" },
    null,
  );
  const html = "<!doctype html><html><body><h1>Current</h1></body></html>";
  const rev = l.publish(g.id, html, "first");
  l.writeDraft(g.id, "index.html", html.replace("Current", "Proposed"));
  const d = l.draft(g.id),
    view = r.open(l, g.id, d.digest);
  assert.match(await r.response(view.draftUrl).text(), /Proposed/);
  assert.match(await r.response(view.currentUrl).text(), /Current/);
  assert.ok(
    !(await r.response(view.draftUrl).text()).includes("arcade.save.connect"),
  );
  assert.match(await r.response(view.draftUrl).text(), /const slots=new Map/);
  l.writeDraft(g.id, "index.html", html.replace("Current", "Later"));
  assert.match(await r.response(view.draftUrl).text(), /Proposed/);
  assert.throws(() => r.open(l, g.id, d.digest), /changed/);
  assert.equal(l.get(g.id).revisionId, rev.id);
  assert.equal(
    r.response(view.draftUrl.replace("index.html", "%2e%2e/index.html")).status,
    404,
  );
  assert.equal(r.response(view.draftUrl + "?x=1").status, 404);
  r.close();
  assert.equal(r.response(view.draftUrl).status, 404);
});
