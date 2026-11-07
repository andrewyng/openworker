import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRun, command, step, makeCourse } from "./rules.js";
const config = JSON.parse(
  readFileSync(new URL("./config.json", import.meta.url), "utf8"),
);
test("course is deterministic and every obstacle row leaves an open lane", () => {
  const a = makeCourse(config);
  assert.deepEqual(a, makeCourse(config));
  for (const row of a.filter((o) => o.type !== "coin"))
    assert.ok(
      new Set(
        a.filter((o) => o.z === row.z && o.type !== "coin").map((o) => o.lane),
      ).size <= 2,
    );
});
test("jump and slide each clear the matching obstacle and fail the wrong one", () => {
  for (const type of ["jump", "slide"]) {
    const s = createRun(config);
    s.phase = "running";
    command(s, type, config);
    step(s, 0.04, config, [{ z: 0.5, lane: 1, type }]);
    assert.equal(s.lives, 3);
    const wrong = createRun(config);
    wrong.phase = "running";
    command(wrong, type === "jump" ? "slide" : "jump", config);
    step(wrong, 0.04, config, [{ z: 0.5, lane: 1, type }]);
    assert.equal(wrong.lives, 2);
  }
});
test("coins are counted once, damage has grace period, loss stops motion, pause freezes", () => {
  const s = createRun(config);
  s.phase = "running";
  const course = [
    { z: 0.5, lane: 1, type: "coin" },
    { z: 0.5, lane: 1, type: "jump" },
    { z: 0.5, lane: 1, type: "slide" },
  ];
  step(s, 0.04, config, course);
  assert.equal(s.coins, 1);
  assert.equal(s.lives, 2);
  step(s, 0.04, config, course);
  assert.equal(s.coins, 1);
  s.phase = "paused";
  const d = s.distance;
  step(s, 0.04, config, course);
  assert.equal(s.distance, d);
  s.phase = "running";
  s.lives = 1;
  s.invincible = 0;
  step(s, 0.04, config, [
    ...course,
    { z: s.distance + 0.2, lane: 1, type: "jump" },
  ]);
  assert.equal(s.phase, "lost");
  const end = s.distance;
  step(s, 0.04, config, course);
  assert.equal(s.distance, end);
});
test("full delivery has a playable safe-lane solution and restart resets state", () => {
  const s = createRun(config),
    course = makeCourse(config);
  s.phase = "running";
  for (let i = 0; i < 5000 && s.phase === "running"; i++) {
    const next = course.find((o) => o.type !== "coin" && o.z > s.distance);
    if (next) {
      const blocked = course
        .filter((o) => o.type !== "coin" && o.z === next.z)
        .map((o) => o.lane);
      s.lane = [0, 1, 2].find((n) => !blocked.includes(n));
    }
    step(s, 0.02, config, course);
  }
  assert.equal(s.phase, "won");
  assert.equal(s.lives, 3);
  assert.equal(createRun(config).distance, 0);
});
