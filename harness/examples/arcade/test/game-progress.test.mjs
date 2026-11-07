import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, rmSync, readdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { Library } from "../build/electron/library.js";
import { StarterCatalog } from "../build/electron/starters.js";
import { progressScope } from "../build/electron/game-progress.js";
function fixture(t) {
  const root = mkdtempSync(join(tmpdir(), "arcade-progress-"));
  let l = new Library(root);
  t.after(() => {
    l.close();
    rmSync(root, { recursive: true, force: true });
  });
  return {
    get l() {
      return l;
    },
    root,
    reopen() {
      l.close();
      l = new Library(root);
    },
  };
}
test("checkpoint and best score survive reopening and stay isolated across games", (t) => {
  const f = fixture(t),
    scope = "starter:moonlight-rally";
  f.l.progress.request(scope, "v1-1-0", {
    op: "save",
    slot: "lap-neon",
    version: 1,
    value: { level: 2, position: [1, 2, 3] },
  });
  for (const score of [50, 40, 45])
    f.l.progress.request(scope, "v1-1-0", {
      op: "score",
      board: "time",
      score,
      order: "lower",
    });
  f.reopen();
  assert.equal(
    f.l.progress.request(scope, "v1-2-0", {
      op: "load",
      slot: "lap-neon",
      version: 1,
    }).value.level,
    2,
  );
  assert.equal(
    f.l.progress.request(scope, "v1-2-0", { op: "best", board: "time" }).score,
    40,
  );
  assert.equal(
    f.l.progress.request("game:game-remix", "revision", {
      op: "load",
      slot: "lap-neon",
      version: 1,
    }),
    null,
  );
  assert.equal(
    f.l.progress.request("starter:other", "v1", { op: "best", board: "time" }),
    null,
  );
  assert.throws(
    () =>
      f.l.progress.request(scope, "v1", {
        op: "score",
        board: "time",
        score: 99,
        order: "higher",
      }),
    /ordering/,
  );
});
test("incompatible formats preserve old data until explicit reset", (t) => {
  const f = fixture(t),
    p = f.l.progress,
    s = "starter:example";
  p.request(s, "v1", {
    op: "save",
    slot: "campaign",
    version: 1,
    value: { level: 3 },
  });
  for (const op of ["load", "save"])
    assert.throws(
      () =>
        p.request(s, "v2", {
          op,
          slot: "campaign",
          version: 2,
          ...(op === "save" ? { value: { level: 1 } } : {}),
        }),
      /INCOMPATIBLE_SAVE/,
    );
  assert.equal(
    p.request(s, "v1", { op: "load", slot: "campaign", version: 1 }).value
      .level,
    3,
  );
  p.request(s, "v2", { op: "remove", slot: "campaign" });
  p.request(s, "v2", {
    op: "save",
    slot: "campaign",
    version: 2,
    value: { level: 1 },
  });
  assert.equal(
    p.request(s, "v2", { op: "load", slot: "campaign", version: 2 }).version,
    2,
  );
});
test("requests, slot counts, bytes and scores are bounded", (t) => {
  const { l } = fixture(t),
    p = l.progress,
    s = "starter:example";
  for (const request of [
    { op: "save", slot: "../x", version: 1, value: 1 },
    { op: "save", slot: "x", version: 0, value: 1 },
    { op: "save", slot: "x", version: 1, value: "😀".repeat(70000) },
    { op: "score", board: "x", score: Infinity, order: "lower" },
    { op: "load", slot: "x", version: 1, scope: "starter:other" },
  ])
    assert.throws(() => p.request(s, "v1", request));
  for (let i = 0; i < 16; i++)
    p.request(s, "v1", { op: "save", slot: `s${i}`, version: 1, value: i });
  assert.throws(
    () =>
      p.request(s, "v1", {
        op: "save",
        slot: "overflow",
        version: 1,
        value: 1,
      }),
    /quota/,
  );
  p.request(s, "v1", { op: "save", slot: "s0", version: 1, value: 99 });
  assert.equal(
    p.request(s, "v1", { op: "load", slot: "s0", version: 1 }).value,
    99,
  );
  for (let i = 0; i < 4; i++)
    p.request("starter:bytes", "v1", {
      op: "save",
      slot: `s${i}`,
      version: 1,
      value: "x".repeat(256 * 1024 - 2),
    });
  assert.throws(
    () =>
      p.request("starter:bytes", "v1", {
        op: "save",
        slot: "s4",
        version: 1,
        value: 1,
      }),
    /quota/,
  );
});
test("schema 3 catalog upgrades additively and preview identity is validated", (t) => {
  const f = fixture(t);
  const db = new DatabaseSync(join(f.root, "library.sqlite"));
  db.exec(
    "DROP TABLE game_saves; DROP TABLE game_scores; PRAGMA user_version=3",
  );
  db.close();
  f.reopen();
  const c = new StarterCatalog(),
    starter = c.list()[0],
    binding = progressScope(starter.previewUrl, f.l, c);
  assert.equal(binding.scope, "starter:moonlight-rally");
  const backup = readdirSync(f.root).find((name) =>
    name.startsWith("library-before-progress-v4-"),
  );
  assert.ok(backup);
  const previous = new DatabaseSync(join(f.root, backup), { readOnly: true });
  assert.equal(previous.prepare("PRAGMA user_version").get().user_version, 3);
  previous.close();
  for (const url of [
    "https://example.com/index.html",
    starter.previewUrl + "?scope=other",
    starter.previewUrl.replace("index.html", "game.js"),
    starter.previewUrl.replace(`.v${starter.version.replaceAll('.', '-')}`, ".v99-0-0"),
  ])
    assert.throws(() => progressScope(url, f.l, c));
});
