import test from "node:test";
import assert from "node:assert/strict";
import {
  newJourney,
  step,
  interact,
  walkable,
  snapshot,
  restore,
  respawn,
  PARTS,
  LANTERNS,
  HEART,
} from "./src/journey.js";
import { progressStore } from "./src/progress.js";
function move(s, x, z) {
  for (let i = 0; i < 5000; i++) {
    const dx = x - s.x,
      dz = z - s.z;
    if (Math.hypot(dx, dz) < 0.1) return;
    step(
      s,
      {
        x: Math.abs(dx) < 0.04 ? 0 : Math.sign(dx),
        z: Math.abs(dz) < 0.04 ? 0 : Math.sign(dz),
      },
      false,
      1 / 120,
    );
  }
  throw Error(`Unreachable ${x},${z} from ${s.x},${s.z}`);
}
function bridge(s) {
  s.phase = "playing";
  for (const p of PARTS) move(s, p.x, p.z);
  move(s, 0, -4);
  move(s, 0, -8);
  assert.equal(interact(s)?.type, "bridge");
}
test("bridge is gated, boundaries hold even while jumping, and every home part is reachable", () => {
  const s = newJourney();
  s.phase = "playing";
  for (let i = 0; i < 1000; i++) step(s, { x: 0, z: -1 }, true, 1 / 60);
  assert.ok(s.z >= -11.41);
  assert.equal(s.bridge, false);
  assert.equal(s.checkpoint, "home");
  bridge(s);
  assert.ok(s.bridge);
  assert.ok(s.parts.every(Boolean));
  move(s, 0, -35);
  assert.equal(s.checkpoint, "woodland");
});
test("complete keyboard-equivalent route, direction puzzle and optional roaming", () => {
  const s = newJourney();
  bridge(s);
  move(s, 0, -35);
  for (let i = 0; i < LANTERNS.length; i++) {
    const p = LANTERNS[i];
    move(s, p.x, p.z + 1.8);
    for (let n = 0; n < 4 && s.dials[i] !== p.target; n++)
      assert.equal(interact(s).type, "lantern");
  }
  move(s, HEART.x, HEART.z + 1.8);
  assert.equal(interact(s).type, "complete");
  assert.equal(s.phase, "won");
  assert.ok(s.completed);
  assert.equal(s.checkpoint, "heart");
  const t = s.elapsed;
  step(s, { x: 1, z: 1 }, false, 0.05);
  assert.equal(s.elapsed, t);
  const loaded = restore(snapshot(s));
  assert.equal(loaded.phase, "paused");
  assert.ok(loaded.completed);
});
test("pause and checkpoint reset never lose puzzle/collection state", () => {
  const s = newJourney();
  bridge(s);
  move(s, 0, -35);
  const before = snapshot(s);
  s.phase = "paused";
  step(s, { x: 1, z: 1 }, true, 0.04);
  assert.deepEqual(snapshot(s), before);
  respawn(s);
  assert.equal(s.z, -34);
  assert.ok(s.bridge);
  assert.deepEqual(s.parts, before.parts);
});
test("strict save validation preserves the meaning of unlocked areas", () => {
  const s = newJourney();
  s.phase = "playing";
  move(s, 1, 20);
  const good = snapshot(s);
  assert.equal(restore(good).phase, "paused");
  for (const v of [
    { ...good, x: NaN },
    { ...good, x: 999 },
    { ...good, z: -50 },
    { ...good, bridge: true },
    { ...good, completed: true },
    { ...good, dials: [4, 0, 0] },
    { ...good, layout: "future" },
    { ...good, parts: [1, true, true] },
    { ...good, checkpoint: "unknown" },
  ])
    assert.throws(() => restore(v));
  assert.equal(walkable(0, -20, false), false);
  assert.equal(walkable(0, -20, true), true);
});
test("save queue snapshots values and explicit reset runs after prior writes", async () => {
  const calls = [],
    api = {
      load: async () => null,
      save: async (_s, _v, v) => {
        await new Promise((r) => setTimeout(r, 5));
        calls.push(v);
      },
      remove: async () => calls.push("removed"),
    };
  const p = progressStore(api),
    s = newJourney();
  const a = p.save(s);
  s.x = 1;
  const b = p.reset();
  const c = p.save(s);
  await Promise.all([a, b, c]);
  assert.equal(calls[0].x, 0);
  assert.equal(calls[1], "removed");
  assert.equal(calls[2].x, 1);
  const offline = progressStore(null);
  assert.equal(await offline.load(), null);
  assert.equal(await offline.save(s), false);
  const fail = progressStore({
    load: async () => {
      throw Error("INCOMPATIBLE_SAVE");
    },
  });
  await assert.rejects(fail.load(), /INCOMPATIBLE/);
});
