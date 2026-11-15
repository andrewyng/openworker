import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { Library } from "../build/electron/library.js";
import { SettingsStore } from "../build/electron/settings.js";
import { starterFiles } from "../build/electron/templates.js";
import { previewResponse, previewUrl } from "../build/electron/preview.js";
import { projectPath, validateFiles } from "../build/electron/project.js";
import { DatabaseSync } from "node:sqlite";
const brief = {
  idea: "A project",
  kind: "Arcade",
  vibe: "Neon",
  difficulty: "Easy",
};
function setup(t, fault) {
  const root = mkdtempSync(join(tmpdir(), "arcade-project-"));
  const settings = new SettingsStore(root);
  const l = new Library(root, fault);
  t.after(() => {
    try {
      l.close();
    } catch {}
    rmSync(root, { recursive: true, force: true });
  });
  const g = l.create(settings.get().gamesRoot, "Project", brief, null);
  return { l, g, root };
}
test("whole snapshots, restored draft, independent clone and pinned Three library", (t) => {
  const { l, g } = setup(t);
  const files = starterFiles("three"),
    r = l.publishProject(g.id, files, "1");
  assert.ok(Object.keys(l.project(g.id)).length > 4);
  assert.match(l.project(g.id)["vendor/library.json"], /0.186.1/);
  let d = l.draft(g.id);
  const old = d.digest;
  l.writeDraft(g.id, "src/game.js", "// updated");
  assert.equal(l.project(g.id)["src/game.js"], files["src/game.js"]);
  assert.throws(() => l.publishDraft(g.id, old, "2"), /changed/);
  d = l.draft(g.id);
  const v = l.publishDraft(g.id, d.digest, "2");
  assert.equal(l.publishDraft(g.id, d.digest, "2").id, v.id);
  assert.equal(l.revisions(g.id).length, 2);
  l.restore(g.id, r.id);
  assert.equal(l.draft(g.id).files["src/game.js"], files["src/game.js"]);
  const copy = l.duplicate(g.id, l.get(g.id).directory);
  assert.notEqual(copy.id, g.id);
  assert.deepEqual(l.project(copy.id), l.project(g.id));
  assert.equal(copy.session, null);
  assert.throws(
    () => l.writeDraft(g.id, "vendor/three.module.js", "bad"),
    /read-only/,
  );
});
test("project paths, byte bounds, manifest tampering and unpublished source isolation", async (t) => {
  const { l, g } = setup(t);
  for (const p of [
    "../x.js",
    "/x.js",
    "a/../../x.js",
    "a\\x.js",
    "a:x.js",
    "CON.js",
    "con/x.js",
    "AUX/x.css",
    "a./x.js",
    "arcade-project.json",
    "ARCADE-project.json",
    "arcade-project.json/evil.js",
    "test.exe",
    "%2e%2e/a.js",
  ])
    assert.throws(() => projectPath(p), p);
  assert.throws(() =>
    validateFiles({ "index.html": "x", "a.js": "x", "A.js": "x" }),
  );
  assert.throws(() =>
    validateFiles({ "index.html": "x", "a.js": "x", "a.js/x.js": "x" }),
  );
  assert.throws(() =>
    validateFiles({
      "index.html": "x",
      "a.js": "a".repeat(400000),
      "b.js": "a".repeat(400000),
    }),
  );
  const files = starterFiles("canvas");
  const r = l.publishProject(g.id, files, "one");
  l.writeDraft(g.id, "secret.txt", "draft-only");
  const url = previewUrl(g.id, r.id);
  assert.equal((await previewResponse(l, url)).status, 200);
  assert.equal(
    (await previewResponse(l, new URL("secret.txt", url).href)).status,
    404,
  );
  const js = previewResponse(l, new URL("src/game.js", url).href);
  assert.match(js.headers.get("content-type"), /javascript/);
  assert.equal(js.headers.get("access-control-allow-origin"), "*");
  assert.ok(!js.headers.get("content-security-policy").includes("https:"));
  writeFileSync(
    join(g.directory, "revisions", r.id, "src/game.js"),
    "tampered",
  );
  assert.throws(() => l.project(g.id), /integrity/);
});
for (const point of ["revision_written", "catalog_committed"])
  test(`multi-file recovery at ${point}`, (t) => {
    let fail = true;
    const { l, g } = setup(t, (p) => {
      if (fail && p === point) {
        fail = false;
        throw Error("injected");
      }
    });
    const files = starterFiles("canvas");
    assert.throws(() => l.publishProject(g.id, files, "same"), /injected/);
    const r = l.publishProject(g.id, files, "same");
    assert.equal(l.revisions(g.id).length, 1);
    assert.equal(l.get(g.id).revisionId, r.id);
    assert.deepEqual(l.project(g.id), validateFiles(files, true, true));
  });
test("schema 2 upgrade retains legacy single-file games and history", (t) => {
  const { l, g, root } = setup(t);
  const html = "<!doctype html><html><body>Original snake</body></html>";
  const r = l.publish(g.id, html, "old");
  l.close();
  const db = new DatabaseSync(join(root, "library.sqlite"));
  db.exec("ALTER TABLE revisions DROP COLUMN format; PRAGMA user_version=2");
  db.close();
  const upgraded = new Library(root);
  assert.equal(upgraded.html(g.id), html);
  assert.equal(upgraded.revisions(g.id)[0].id, r.id);
  assert.equal(upgraded.draft(g.id).files["index.html"], html);
  upgraded.close();
});
