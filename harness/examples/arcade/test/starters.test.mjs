import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { StarterCatalog } from "../build/electron/starters.js";
import { Library } from "../build/electron/library.js";
import { SettingsStore } from "../build/electron/settings.js";
import { previewResponse } from "../build/electron/preview.js";

test("bundled catalog serves pinned offline projects and independent licensed remixes", async (t) => {
  const root = mkdtempSync(join(tmpdir(), "arcade-starters-"));
  const library = new Library(root),
    settings = new SettingsStore(root),
    catalog = new StarterCatalog();
  t.after(() => {
    library.close();
    rmSync(root, { recursive: true, force: true });
  });
  assert.equal(catalog.list().length, 6);
  for (const starter of catalog.list()) {
    const response = previewResponse(library, starter.previewUrl, catalog);
    assert.equal(response.status, 200);
    assert.match(await response.text(), /<html/i);
    const csp = response.headers.get("content-security-policy");
    assert.ok(!csp.includes("https:"));
    assert.match(csp, /default-src 'none'/);
    assert.match(csp, /connect-src arcade-preview:\/\/[^;]+ blob:;/);
    assert.match(csp, /worker-src 'none'/);
    assert.ok(!csp.includes("connect-src *"));
    assert.equal(
      previewResponse(library, starter.coverUrl, catalog).status,
      200,
    );
    assert.equal(
      previewResponse(
        library,
        starter.previewUrl.replace("index.html", "missing.js"),
        catalog,
      ).status,
      404,
    );
    assert.equal(
      previewResponse(
        library,
        starter.previewUrl.replace(
          `.v${starter.version.replaceAll(".", "-")}`,
          ".v99-0-0",
        ),
        catalog,
      ).status,
      404,
    );
    assert.equal(
      previewResponse(
        library,
        starter.previewUrl.replace("index.html", "%2e%2e/private.json"),
        catalog,
      ).status,
      404,
    );
    const one = catalog.remix(starter.id, library, settings.get().gamesRoot);
    const two = catalog.remix(starter.id, library, settings.get().gamesRoot);
    assert.notEqual(one.id, two.id);
    assert.equal(one.session, null);
    assert.equal(one.model, null);
    const original = library.project(one.id);
    assert.deepEqual(original, library.project(two.id));
    assert.match(original["LICENSE.txt"], /MIT License/);
    assert.equal(
      JSON.parse(original["starter-origin.json"]).version,
      starter.version,
    );
    assert.equal(JSON.parse(original["starter-origin.json"]).id, starter.id);
    library.writeDraft(
      one.id,
      "index.html",
      "<!doctype html><html><body>My remix</body></html>",
    );
    library.publishDraft(
      one.id,
      library.draft(one.id).digest,
      "my-edit:" + one.id,
    );
    assert.deepEqual(library.project(two.id), original);
    assert.equal(
      catalog.asset(new URL(starter.previewUrl).host, "index.html"),
      original["index.html"],
    );
  }
  assert.throws(() =>
    catalog.remix("../escape", library, settings.get().gamesRoot),
  );
  assert.throws(() =>
    catalog.asset(new URL(catalog.list()[0].previewUrl).host, "../index.html"),
  );
  const metadata = catalog.list();
  metadata[0].title = "changed";
  metadata[0].tags.push("injected");
  assert.notEqual(catalog.list()[0].title, "changed");
  assert.ok(!catalog.list()[0].tags.includes("injected"));
});
