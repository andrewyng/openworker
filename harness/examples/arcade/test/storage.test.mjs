import test from "node:test";
import assert from "node:assert/strict";
import {
  mkdtempSync,
  mkdirSync,
  rmSync,
  readFileSync,
  writeFileSync,
  symlinkSync,
  unlinkSync,
  renameSync,
  chmodSync,
  readdirSync,
  realpathSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { Library } from "../build/electron/library.js";
import { SettingsStore, validateProvider } from "../build/electron/settings.js";
import { atomicWrite, scoped } from "../build/electron/files.js";
const brief = {
  idea: "A little game",
  kind: "Arcade",
  vibe: "Neon",
  difficulty: "Easy",
};
const html = (text) =>
  `<!doctype html><html><body><button>${text}</button></body></html>`;
function setup(t, fault) {
  const root = realpathSync(mkdtempSync(join(tmpdir(), "arcade-storage-")));
  const settings = new SettingsStore(join(root, "app"));
  let library = new Library(settings.dataDir, fault);
  t.after(() => {
    try {
      library.close();
    } catch {}
    rmSync(root, { recursive: true, force: true });
  });
  return {
    root,
    settings,
    get library() {
      return library;
    },
    reopen() {
      library.close();
      library = new Library(settings.dataDir);
      return library;
    },
  };
}
test("two games survive restart; a new root affects new games only; duplicate is independent", (t) => {
  const f = setup(t);
  const a = f.library.create(
    f.settings.get().gamesRoot,
    "同じ ✨ name",
    brief,
    null,
  );
  const b = f.library.create(
    f.settings.get().gamesRoot,
    "同じ ✨ name",
    brief,
    null,
  );
  f.library.publish(a.id, html("One"), "s1:c1");
  f.library.publish(b.id, html("Two"), "s2:c1");
  assert.notEqual(a.directory, b.directory);
  const other = join(f.root, "New games with spaces");
  mkdirSync(other);
  f.settings.gamesRoot(other);
  const c = f.library.create(f.settings.get().gamesRoot, "Third", brief, null);
  assert.equal(c.directory.startsWith(other), true);
  assert.equal(a.directory.startsWith(other), false);
  const copy = f.library.duplicate(a.id, other);
  assert.equal(copy.session, null);
  assert.equal(f.library.html(copy.id), html("One"));
  f.library.publish(copy.id, html("Changed copy"), "copy:c2");
  assert.equal(f.library.html(a.id), html("One"));
  f.reopen();
  assert.equal(f.library.list().length, 4);
  assert.equal(f.library.html(b.id), html("Two"));
  assert.equal(new SettingsStore(f.settings.dataDir).get().gamesRoot, other);
});
test("immutable revisions, idempotent callback and restoring the current revision", (t) => {
  const f = setup(t);
  const g = f.library.create(f.settings.get().gamesRoot, "Game", brief, null);
  const a = f.library.publish(g.id, html("a"), "session:call1");
  assert.equal(f.library.publish(g.id, html("a"), "session:call1").id, a.id);
  assert.throws(() =>
    f.library.publish(g.id, html("different"), "session:call1"),
  );
  const b = f.library.publish(g.id, html("b"), "session:call2");
  f.library.restore(g.id, a.id);
  assert.equal(f.library.html(g.id), html("a"));
  assert.equal(f.library.html(g.id, b.id), html("b"));
  assert.equal(
    readFileSync(join(g.directory, "index.html"), "utf8"),
    html("a"),
  );
  const png = Buffer.from([137, 80, 78, 71, 0, 255]);
  f.library.thumbnail(g.id, a.id, png);
  assert.deepEqual(f.library.thumbnailBytes(g.id), png);
});
for (const point of ["revision_written", "catalog_committed"])
  test(`publish interruption at ${point} retains files and reconciles without duplicate effects`, (t) => {
    let fail = false;
    const f = setup(t, (p) => {
      if (fail && p === point) throw new Error("injected crash");
    });
    const g = f.library.create(
      f.settings.get().gamesRoot,
      "Fault test",
      brief,
      null,
    );
    f.library.publish(g.id, html("old"), "original");
    fail = true;
    assert.throws(() => f.library.publish(g.id, html("new"), "new-call"));
    f.reopen();
    f.library.repair(g.id);
    assert.equal(
      f.library.html(g.id),
      point === "catalog_committed" ? html("new") : html("old"),
    );
    const r = f.library.publish(g.id, html("new"), "new-call");
    assert.equal(f.library.revisions(g.id).length, 2);
    assert.equal(f.library.get(g.id).revisionId, r.id);
    assert.ok(readdirSync(join(g.directory, "revisions")).length >= 2);
  });
test("missing roots and tampered artifacts produce repairable errors without overwriting originals", (t) => {
  const f = setup(t);
  const root = f.settings.get().gamesRoot;
  const g = f.library.create(root, "Lost root", brief, null);
  f.library.publish(g.id, html("good"), "a");
  renameSync(root, root + "-unmounted");
  assert.throws(() => f.library.create(root, "Bad", brief, null));
  assert.throws(() => f.library.html(g.id));
  renameSync(root + "-unmounted", root);
  const artifact = join(
    g.directory,
    "revisions",
    f.library.get(g.id).revisionId,
    "index.html",
  );
  writeFileSync(artifact, html("tampered"));
  assert.throws(() => f.library.html(g.id));
  assert.equal(readFileSync(artifact, "utf8"), html("tampered"));
});
test("path traversal, links and unapproved output destinations are rejected", (t) => {
  const f = setup(t);
  const outside = join(f.root, "outside");
  mkdirSync(outside);
  const secret = join(outside, "keep.txt");
  writeFileSync(secret, "keep");
  assert.throws(() => scoped(f.settings.get().gamesRoot, "..", "outside"));
  const g = f.library.create(f.settings.get().gamesRoot, "Links", brief, null);
  const link = join(g.directory, "escape");
  symlinkSync(outside, link, process.platform === "win32" ? "junction" : "dir");
  assert.throws(() => scoped(g.directory, "escape", "keep.txt"));
  const dest = join(g.directory, "index.html");
  symlinkSync(secret, dest);
  assert.throws(() => atomicWrite(dest, "bad"));
  assert.equal(readFileSync(secret, "utf8"), "keep");
  unlinkSync(dest);
  symlinkSync(join(outside, "missing"), dest);
  assert.throws(() => atomicWrite(dest, "bad"));
  assert.throws(() => f.library.get("../outside"));
  assert.throws(() => f.library.publish(g.id, "not HTML", "invalid"));
});
test("corrupted catalog and settings never get reset", (t) => {
  const f = setup(t);
  f.library.close();
  const path = join(f.settings.dataDir, "library.sqlite");
  writeFileSync(path, "not a database");
  assert.throws(() => new Library(f.settings.dataDir));
  assert.equal(readFileSync(path, "utf8"), "not a database");
  const settings = join(f.settings.dataDir, "settings.json");
  writeFileSync(settings, "broken");
  assert.throws(() => new SettingsStore(f.settings.dataDir));
  assert.equal(readFileSync(settings, "utf8"), "broken");
});
test("unknown schema is preserved and credential values cannot enter provider preferences", (t) => {
  const f = setup(t);
  f.library.close();
  const path = join(f.settings.dataDir, "library.sqlite");
  const db = new DatabaseSync(path);
  db.exec("PRAGMA user_version=999");
  db.close();
  assert.throws(() => new Library(f.settings.dataDir));
  f.settings.provider(
    validateProvider({
      name: "Ollama",
      preset: "ollama",
      api: "chat-completions",
      baseUrl: "http://127.0.0.1:11434/v1",
      model: "local-model",
      apiKey: "never-persist",
    }),
  );
  assert.equal(
    readFileSync(join(f.settings.dataDir, "settings.json"), "utf8").includes(
      "never-persist",
    ),
    false,
  );
  assert.throws(() =>
    validateProvider({
      name: "Custom",
      preset: "custom",
      api: "responses",
      baseUrl: "https://secret@evil.test",
      model: "model",
    }),
  );
});
test(
  "permission failures do not create a catalog entry",
  { skip: process.platform === "win32" },
  (t) => {
    const f = setup(t);
    const dir = join(f.root, "readonly");
    mkdirSync(dir);
    chmodSync(dir, 0o500);
    try {
      assert.throws(() => f.library.create(dir, "Denied", brief, null));
      assert.equal(f.library.list().length, 0);
    } finally {
      chmodSync(dir, 0o700);
    }
  },
);
