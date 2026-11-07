import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { AssetCatalog } from "../build/electron/asset-catalog.js";
import { Library } from "../build/electron/library.js";
import { SettingsStore } from "../build/electron/settings.js";
import { StarterCatalog } from "../build/electron/starters.js";
import { fileBytes, validateFiles } from "../build/electron/project.js";
const catalog = new AssetCatalog();
test("catalog models are offline, hash-pinned, licensed, bounded and expose real parts/clips", () => {
  assert.equal(catalog.list().length, 2);
  for (const a of catalog.list()) {
    const files = catalog.files(a.id),
      b = fileBytes(files[a.path]);
    validateFiles(files, false);
    assert.equal(b.length, a.bytes);
    assert.ok(b.length < 8_000_000);
    const j = JSON.parse(b.toString("utf8", 20, 20 + b.readUInt32LE(12)));
    assert.equal(j.extensionsRequired?.length ?? 0, 0);
    assert.match(files[a.licensePath], /CC0/);
    assert.ok(!JSON.stringify(j).includes('"uri"'));
    if (a.id === "retro-coupe") {
      for (const name of [...a.parts.steer, ...a.parts.spin])
        assert.ok(
          j.nodes.find((n) => n.name === name),
          name,
        );
      assert.equal(j.cameras, undefined);
      assert.equal(j.nodes.filter((n) => /Spin$/.test(n.name)).length, 4);
    } else
      for (const clip of a.animations)
        assert.ok(
          j.animations.find((n) => n.name === clip),
          clip,
        );
  }
  assert.throws(() => catalog.files("../private"));
  const list = catalog.list();
  list[0].id = "mutated";
  assert.equal(catalog.list()[0].id, "retro-coupe");
});
test("catalog staging is atomic, idempotent, draft-only and preserves bytes across publication/remix", (t) => {
  const root = mkdtempSync(join(tmpdir(), "arcade-catalog-")),
    l = new Library(root),
    s = new SettingsStore(root),
    starters = new StarterCatalog();
  t.after(() => {
    l.close();
    rmSync(root, { recursive: true, force: true });
  });
  const g = starters.remix("moonlight-rally", l, s.get().gamesRoot),
    before = l.get(g.id).revisionId;
  const files = catalog.files("pip-robot"),
    p = "assets/library/pip-robot.glb";
  const digest = l.stageCatalogAsset(g.id, files);
  assert.equal(l.stageCatalogAsset(g.id, files), digest);
  assert.equal(l.get(g.id).revisionId, before);
  assert.equal(l.project(g.id)[p], undefined);
  l.publishDraft(g.id, digest, "catalog-test");
  assert.deepEqual(fileBytes(l.project(g.id)[p]), fileBytes(files[p]));
  const clone = l.duplicate(g.id, s.get().gamesRoot);
  assert.deepEqual(l.project(clone.id), l.project(g.id));
  l.writeDraft(g.id, "assets/library/pip-robot.json", "{}");
  const changed = l.draft(g.id).digest;
  assert.throws(() => l.stageCatalogAsset(g.id, files), /not overwritten/);
  assert.equal(l.draft(g.id).digest, changed);
  l.restore(g.id, before);
  assert.equal(l.project(g.id)[p], undefined);
  assert.ok(l.project(clone.id)[p]);
});
