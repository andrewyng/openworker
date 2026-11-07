import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { restoreLap } from "./progress.js";
import {
  buildTrack,
  createCar,
  stepCar,
  atDistance,
  angleDelta,
  clamp,
  nearest,
  resetToRoad,
} from "./physics.js";
export const courses = Object.fromEntries(
  ["neon", "sunset"].map((id) => [
    id,
    buildTrack(
      JSON.parse(
        readFileSync(new URL(`./courses/${id}.json`, import.meta.url)),
      ),
    ),
  ]),
);
// Test driver only. Production has no steering or throttle assistance.
export function drive(s, track) {
  const q = atDistance(track, s.progress + 8 + Math.abs(s.speed) * 0.3);
  const error = angleDelta(Math.atan2(q.x - s.x, -(q.z - s.z)), s.yaw);
  const curve = Math.abs(
    angleDelta(atDistance(track, s.progress + 25).yaw, s.yaw),
  );
  const target = curve > 0.6 ? 12 : curve > 0.3 ? 18 : 27;
  return {
    throttle: s.speed < target ? 1 : 0,
    brake: s.speed > target + 2 ? 1 : 0,
    steer: clamp(error * 3, -1, 1),
  };
}
for (const [id, track] of Object.entries(courses))
  test(`${id}: three laps use 36 ordered checkpoints then coast without changing the result`, () => {
    const s = createCar(track);
    s.phase = "racing";
    let gates = 0,
      finished = 0;
    for (let f = 0; f < 9000 && s.phase === "racing"; f++)
      for (const event of stepCar(s, drive(s, track), 1 / 60, track)) {
        if (event === "checkpoint") gates++;
        if (event === "finish") finished++;
      }
    assert.equal(s.phase, "coasting");
    assert.equal(gates, 36);
    assert.equal(finished, 1);
    assert.equal(s.hits, 0);
    assert.ok(s.time > 80 && s.time < 150);
    assert.equal(s.lapTimes.length, 3);
    const time = s.time,
      progress = s.progress,
      x = s.x,
      z = s.z,
      hits = s.hits;
    for (let i = 0; i < 260; i++)
      stepCar(s, { throttle: 1, steer: -1, brake: 1 }, 1 / 60, track);
    assert.equal(s.phase, "finished");
    assert.equal(s.time, time);
    assert.equal(s.progress, progress);
    assert.equal(s.hits, hits);
    assert.equal(s.speed, 0);
    assert.ok(Math.hypot(s.x - x, s.z - z) > 10);
    assert.deepEqual(stepCar(s, { throttle: 1 }, 1 / 60, track), []);
  });
test("reverse crossing the start cannot finish or award checkpoints", () => {
  const t = courses.neon,
    s = createCar(t);
  s.phase = "racing";
  for (let i = 0; i < 120; i++) stepCar(s, { brake: 1 }, 1 / 60, t);
  assert.ok(s.progress < 0);
  assert.equal(s.checkpoint, 0);
  assert.equal(s.phase, "racing");
});
test("resume on lap three retains split times and finishes exactly once", () => {
  const d = JSON.parse(
      readFileSync(new URL("./courses/neon.json", import.meta.url)),
    ),
    t = buildTrack(d),
    s = createCar(t);
  s.phase = "racing";
  while (s.checkpoint < 25) stepCar(s, drive(s, t), 1 / 60, t);
  const value = {
      rules: 2,
      course: d.id,
      layout: JSON.stringify({ points: d.points, width: d.width }),
      car: s,
    },
    r = restoreLap(value, d, t);
  assert.equal(r.phase, "paused");
  assert.equal(r.lapTimes.length, 2);
  assert.equal(r.raceLaps, 3);
  r.phase = "racing";
  let finishes = 0;
  for (let i = 0; i < 3000 && r.phase === "racing"; i++)
    finishes += stepCar(r, drive(r, t), 1 / 60, t).filter(
      (e) => e === "finish",
    ).length;
  assert.equal(finishes, 1);
  assert.equal(r.checkpoint, 36);
  assert.equal(r.lapTimes.length, 3);
});
test("barriers contain car, penalize speed, and reset retains time/progress", () => {
  const t = courses.neon,
    s = createCar(t);
  s.phase = "racing";
  s.speed = 20;
  s.x += 10;
  stepCar(s, { throttle: 1 }, 1 / 60, t);
  assert.equal(s.hits, 1);
  assert.ok(s.speed < 15);
  assert.ok(nearest(t, s.x, s.z).distance < t.width / 2);
  const before = {
    time: s.time,
    progress: s.progress,
    checkpoint: s.checkpoint,
  };
  resetToRoad(s, t);
  assert.equal(s.speed, 0);
  assert.equal(s.time, before.time);
  assert.equal(s.progress, before.progress);
  assert.equal(s.checkpoint, before.checkpoint);
});
test("paused state cannot move and handbrake produces slip", () => {
  const t = courses.neon,
    s = createCar(t);
  s.phase = "paused";
  const frozen = { ...s };
  stepCar(s, { throttle: 1 }, 1 / 60, t);
  assert.deepEqual(s, frozen);
  s.phase = "racing";
  s.speed = 20;
  for (let i = 0; i < 15; i++)
    stepCar(s, { throttle: 1, steer: 1, drift: true }, 1 / 60, t);
  assert.ok(s.drift > 0.08);
});
test("courses are genuinely distinct and sunset has elevation", () => {
  assert.notEqual(courses.neon.length, courses.sunset.length);
  assert.ok(Math.max(...courses.sunset.points.map((p) => p.y)) > 17);
  assert.equal(Math.max(...courses.neon.points.map((p) => p.y)), 0);
});
test("lap restore validates state/layout and always returns paused", () => {
  const d = JSON.parse(
      readFileSync(new URL("./courses/neon.json", import.meta.url)),
    ),
    track = buildTrack(d),
    car = createCar(track);
  car.phase = "racing";
  car.time = 4;
  car.progress = 10;
  const value = {
    course: d.id,
    layout: JSON.stringify({ points: d.points, width: d.width }),
    car,
  };
  assert.equal(restoreLap(value, d, track).phase, "paused");
  assert.equal(restoreLap(value, d, track).time, 4);
  assert.throws(
    () => restoreLap({ ...value, car: { ...car, x: NaN } }, d, track),
    /invalid/,
  );
  assert.throws(
    () => restoreLap({ ...value, car: { ...car, checkpoint: 12 } }, d, track),
    /invalid/,
  );
  assert.throws(
    () => restoreLap({ ...value, layout: "old-layout" }, d, track),
    /compatible/,
  );
  assert.equal(value.car.phase, "racing");
});
test("legacy one-lap saves and records remain separate from three-lap races", async () => {
  const d = JSON.parse(
      readFileSync(new URL("./courses/neon.json", import.meta.url)),
    ),
    t = buildTrack(d),
    car = createCar(t);
  car.time = 8;
  car.progress = 40;
  const data = new Map([
      [
        "lap-neon",
        {
          version: 1,
          value: {
            course: "neon",
            layout: JSON.stringify({ points: d.points, width: d.width }),
            car,
          },
        },
      ],
    ]),
    scores = new Map([["lap-neon-v1", { score: 40 }]]);
  globalThis.arcadeSave = {
    load: async (k, v) => {
      const r = data.get(k);
      if (r && r.version !== v) throw Error("version");
      return r ?? null;
    },
    save: async (k, v, value) =>
      data.set(k, { version: v, value: structuredClone(value) }),
    remove: async (k) => data.delete(k),
    best: async (k) => scores.get(k) ?? null,
    submitScore: async (k, score) => {
      scores.set(k, {
        score: Math.min(scores.get(k)?.score ?? Infinity, score),
      });
      return scores.get(k);
    },
  };
  try {
    const api = await import("./progress.js?legacy-test");
    const loaded = await api.loadProgress(d, t);
    assert.equal(loaded.best, null);
    assert.equal(loaded.lap.raceLaps, 1);
    assert.equal(loaded.lap.time, 8);
    await api.saveLap(d, loaded.lap);
    assert.equal(data.get("race-neon").version, 2);
    assert.equal(data.get("race-neon").value.car.raceLaps, 1);
    const result = await api.finishLap("neon", 38, 1);
    assert.equal(result.previous, 40);
    assert.equal(result.best, 38);
    assert.equal(scores.has("race-neon-3lap-v2"), false);
    assert.equal(data.size, 0);
    await api.finishLap("neon", 110, 3);
    assert.equal(scores.get("lap-neon-v1").score, 38);
    assert.equal(scores.get("race-neon-3lap-v2").score, 110);
  } finally {
    delete globalThis.arcadeSave;
  }
});
