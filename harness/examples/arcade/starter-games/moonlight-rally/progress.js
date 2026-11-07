// Persistence is optional outside Arcade. No browser storage or native access.
const api = globalThis.arcadeSave;
let queue = Promise.resolve();
function ordered(fn) {
  const next = queue.then(fn);
  queue = next.catch(() => {});
  return next;
}
const fields = [
  "x",
  "y",
  "z",
  "yaw",
  "vx",
  "vz",
  "speed",
  "progress",
  "lastS",
  "checkpoint",
  "time",
  "hits",
];
const layout = (d) => JSON.stringify({ points: d.points, width: d.width });
export function restoreLap(value, definition, track) {
  if (
    !value ||
    (value.rules !== undefined && value.rules !== 2) ||
    value.course !== definition.id ||
    value.layout !== layout(definition) ||
    !value.car
  )
    throw new Error(
      "This saved lap is not compatible with this course. Start a new race to replace it.",
    );
  const c = value.car;
  const raceLaps = value.rules === 2 ? c.raceLaps : 1;
  const lapTimes = value.rules === 2 ? c.lapTimes : [],
    lapStartTime = value.rules === 2 ? c.lapStartTime : 0;
  if (
    fields.some((k) => typeof c[k] !== "number" || !Number.isFinite(c[k])) ||
    Math.abs(c.x) > 10000 ||
    Math.abs(c.z) > 10000 ||
    Math.abs(c.y) > 1000 ||
    Math.abs(c.yaw) > 10000 ||
    Math.abs(c.vx) > 40 ||
    Math.abs(c.vz) > 40 ||
    Math.abs(c.speed) > 28.1 ||
    c.time < 0 ||
    c.time > 86400 ||
    !Number.isInteger(raceLaps) ||
    raceLaps < 1 ||
    raceLaps > 9 ||
    !Array.isArray(lapTimes) ||
    lapTimes.length !== Math.floor(c.checkpoint / 12) ||
    lapTimes.some((t) => !Number.isFinite(t) || t <= 0) ||
    !Number.isFinite(lapStartTime) ||
    lapStartTime < 0 ||
    lapStartTime > c.time ||
    Math.abs(lapTimes.reduce((a, b) => a + b, 0) - lapStartTime) > 0.001 ||
    c.progress >= track.length * raceLaps ||
    Math.abs(c.progress) > track.length * 100 ||
    c.lastS < 0 ||
    c.lastS > track.length ||
    !Number.isInteger(c.checkpoint) ||
    c.checkpoint < 0 ||
    c.checkpoint >= 12 * raceLaps ||
    !Number.isInteger(c.hits) ||
    c.hits < 0
  )
    throw new Error("Saved lap is invalid. It has not been changed.");
  return {
    ...Object.fromEntries(fields.map((k) => [k, c[k]])),
    raceLaps,
    lapTimes: [...lapTimes],
    lapStartTime,
    phase: "paused",
    resumePhase: "racing",
    drift: 0,
    wallCooldown: 0,
  };
}
export async function loadProgress(definition, track) {
  if (!api) return { lap: null, best: null };
  return ordered(async () => {
    const best = await api.best(board(definition.id, track.laps));
    const record =
      (await api.load(`race-${definition.id}`, 2)) ??
      (await api.load(`lap-${definition.id}`, 1));
    return {
      lap: record ? restoreLap(record.value, definition, track) : null,
      best: best?.score ?? null,
    };
  });
}
export function saveLap(definition, state) {
  if (!api) return Promise.resolve();
  const data = {
    rules: 2,
    course: definition.id,
    layout: layout(definition),
    car: {
      ...Object.fromEntries(fields.map((k) => [k, state[k]])),
      raceLaps: state.raceLaps,
      lapTimes: [...state.lapTimes],
      lapStartTime: state.lapStartTime,
    },
  };
  return ordered(() => api.save(`race-${definition.id}`, 2, data));
}
export function clearLap(id) {
  return api
    ? ordered(async () => {
        await api.remove(`race-${id}`);
        await api.remove(`lap-${id}`);
      })
    : Promise.resolve();
}
export const board = (id, laps) =>
  laps === 1 ? `lap-${id}-v1` : `race-${id}-${laps}lap-v2`;
export function finishLap(id, time, laps) {
  return api
    ? ordered(async () => {
        const previous = await api.best(board(id, laps));
        const best = await api.submitScore(board(id, laps), time, "lower");
        await api.remove(`race-${id}`);
        await api.remove(`lap-${id}`);
        return { best: best.score, previous: previous?.score ?? null };
      })
    : Promise.resolve({ best: time, previous: null });
}
