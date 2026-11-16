import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync, writeFileSync, mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { Library } from "../build/electron/library.js";
import { SettingsStore } from "../build/electron/settings.js";
import { StarterCatalog } from "../build/electron/starters.js";
import {
  binaryAsset,
  fileBytes,
  validateFiles,
  digest,
  fingerprint,
} from "../build/electron/project.js";
import { previewResponse, previewUrl } from "../build/electron/preview.js";
const png = readFileSync("starter-games/skybound-carrier/assets/sky.png");
const glb = readFileSync("starter-games/skybound-carrier/assets/courier.glb");
function fixture(t) {
  const root = mkdtempSync(join(tmpdir(), "arcade-assets-")),
    s = new SettingsStore(root),
    l = new Library(root),
    catalog = new StarterCatalog(),
    game = catalog.remix("skybound-carrier", l, s.get().gamesRoot);
  t.after(() => {
    l.close();
    rmSync(root, { recursive: true, force: true });
  });
  return { root, s, l, game, catalog };
}
test("binary revisions, staged import, restore, clone and preview preserve exact bytes", async (t) => {
  const { s, l, game } = fixture(t),
    revision = game.revisionId;
  assert.deepEqual(
    fileBytes(l.asset(game.id, revision, "assets/sky.png")),
    png,
  );
  const url = new URL("assets/courier.glb", previewUrl(game.id, revision));
  const response = previewResponse(l, url.href);
  assert.equal(response.headers.get("content-type"), "model/gltf-binary");
  assert.deepEqual(Buffer.from(await response.arrayBuffer()), glb);
  const before = l.draft(game.id).digest;
  l.importAssets(game.id, {
    "assets/imported/sky.png": binaryAsset("sky.png", png),
  });
  assert.notEqual(before, l.draft(game.id).digest);
  assert.equal(l.get(game.id).revisionId, revision);
  assert.equal(
    previewResponse(
      l,
      new URL("assets/imported/sky.png", previewUrl(game.id, revision)).href,
    ).status,
    404,
  );
  const imported = l.draft(game.id).digest;
  assert.throws(() => l.publishDraft(game.id, before, "stale"), /changed/);
  assert.throws(
    () =>
      l.importAssets(game.id, {
        "assets/imported/sky.png": binaryAsset("sky.png", png),
      }),
    /already exists/,
  );
  assert.equal(l.draft(game.id).digest, imported);
  assert.throws(() =>
    l.importAssets(game.id, {
      "assets/imported/a.png": binaryAsset("a.png", png),
      "assets/imported/b.png": { encoding: "base64", data: "not valid" },
    }),
  );
  assert.equal(l.draft(game.id).digest, imported);
  const r = l.publishDraft(game.id, imported, "imported");
  assert.deepEqual(
    fileBytes(l.asset(game.id, r.id, "assets/imported/sky.png")),
    png,
  );
  const clone = l.duplicate(game.id, s.get().gamesRoot);
  assert.deepEqual(fileBytes(l.project(clone.id)["assets/courier.glb"]), glb);
  assert.equal(clone.session, null);
  l.restore(game.id, revision);
  assert.equal(l.draft(game.id).files["assets/imported/sky.png"], undefined);
  assert.ok(l.project(clone.id)["assets/imported/sky.png"]);
  assert.throws(() => l.writeDraft(game.id, "assets/x.png", "text"), /binary/);
  assert.throws(() =>
    l.importAssets(game.id, { "vendor/a.png": binaryAsset("a.png", png) }),
  );
  const bad = Buffer.from(glb);
  bad[bad.length - 1] ^= 1;
  writeFileSync(
    join(game.directory, "revisions", revision, "assets/courier.glb"),
    bad,
  );
  assert.throws(() => l.project(game.id), /integrity/);
});
test("reject unsupported, malformed, oversized and externally referenced assets", () => {
  for (const path of ["../x.png", "assets/x.exe", "a.js"])
    assert.throws(() => binaryAsset(path, png));
  assert.throws(() =>
    binaryAsset("x.png", Buffer.from("<html>not an image</html>")),
  );
  assert.throws(() => binaryAsset("x.png", Buffer.alloc(8_000_001)));
  const huge = Buffer.from(png);
  huge.writeUInt32BE(9000, 16);
  assert.throws(() => binaryAsset("x.png", huge), /dimensions/);
  assert.throws(() =>
    validateFiles({ "assets/x.png": png.toString("base64") }, false),
  );
  assert.throws(() =>
    validateFiles(
      { "assets/x.png": { encoding: "base64", data: "YQ=" } },
      false,
    ),
  );
  const json = Buffer.from(
    JSON.stringify({
      asset: { version: "2.0" },
      buffers: [{ uri: "https://example.com/model.bin" }],
    }).padEnd(160, " "),
  );
  const external = Buffer.alloc(20 + json.length);
  external.write("glTF");
  external.writeUInt32LE(2, 4);
  external.writeUInt32LE(external.length, 8);
  external.writeUInt32LE(json.length, 12);
  external.writeUInt32LE(0x4e4f534a, 16);
  json.copy(external, 20);
  assert.throws(() => binaryAsset("x.glb", external), /embed/);
  const files = {
    "index.html": "<!doctype html><html></html>",
    "a.js": "// old source",
  };
  const previous = digest(
    JSON.stringify(
      Object.keys(files)
        .sort()
        .map((path) => [path, digest(files[path])]),
    ),
  );
  assert.equal(fingerprint(files), previous);
});
