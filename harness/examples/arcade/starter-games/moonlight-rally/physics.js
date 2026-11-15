export const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
export const angleDelta = (a, b) =>
  Math.atan2(Math.sin(a - b), Math.cos(a - b));
export function buildTrack(definition) {
  const control = definition.points,
    points = [];
  for (let i = 0; i < control.length; i++)
    for (let j = 0; j < 28; j++) {
      const t = j / 28,
        t2 = t * t,
        t3 = t2 * t,
        p0 = control[(i + control.length - 1) % control.length],
        p1 = control[i],
        p2 = control[(i + 1) % control.length],
        p3 = control[(i + 2) % control.length];
      const p = [0, 1, 2].map(
        (k) =>
          0.5 *
          (2 * p1[k] +
            (-p0[k] + p2[k]) * t +
            (2 * p0[k] - 5 * p1[k] + 4 * p2[k] - p3[k]) * t2 +
            (-p0[k] + 3 * p1[k] - 3 * p2[k] + p3[k]) * t3),
      );
      points.push({ x: p[0], y: p[1], z: p[2], s: 0 });
    }
  let length = 0;
  for (let i = 1; i < points.length; i++) {
    length += Math.hypot(
      points[i].x - points[i - 1].x,
      points[i].z - points[i - 1].z,
    );
    points[i].s = length;
  }
  length += Math.hypot(
    points[0].x - points.at(-1).x,
    points[0].z - points.at(-1).z,
  );
  return {
    points,
    length,
    width: definition.width,
    laps: Number.isInteger(definition.laps) ? clamp(definition.laps, 1, 9) : 3,
    gates: Array.from({ length: 12 }, (_, i) => ((i + 1) * length) / 12),
  };
}
export function nearest(track, x, z) {
  let best;
  for (let i = 0; i < track.points.length; i++) {
    const a = track.points[i],
      b = track.points[(i + 1) % track.points.length],
      dx = b.x - a.x,
      dz = b.z - a.z,
      len2 = dx * dx + dz * dz,
      t = clamp(((x - a.x) * dx + (z - a.z) * dz) / len2, 0, 1),
      px = a.x + dx * t,
      pz = a.z + dz * t,
      d = Math.hypot(x - px, z - pz);
    if (!best || d < best.distance)
      best = {
        x: px,
        y: a.y + (b.y - a.y) * t,
        z: pz,
        distance: d,
        s: a.s + Math.sqrt(len2) * t,
        yaw: Math.atan2(dx, -dz),
        index: i,
      };
  }
  return best;
}
export function atDistance(track, s) {
  s = ((s % track.length) + track.length) % track.length;
  let i = 0;
  while (i < track.points.length - 1 && track.points[i + 1].s < s) i++;
  const a = track.points[i],
    b = track.points[(i + 1) % track.points.length],
    n = (s - a.s) / Math.hypot(b.x - a.x, b.z - a.z);
  return {
    x: a.x + (b.x - a.x) * n,
    y: a.y + (b.y - a.y) * n,
    z: a.z + (b.z - a.z) * n,
    yaw: Math.atan2(b.x - a.x, -(b.z - a.z)),
  };
}
export function createCar(track) {
  const p = atDistance(track, 0);
  return {
    x: p.x,
    y: p.y,
    z: p.z,
    yaw: p.yaw,
    vx: 0,
    vz: 0,
    speed: 0,
    progress: 0,
    lastS: 0,
    checkpoint: 0,
    raceLaps: track.laps,
    lapTimes: [],
    lapStartTime: 0,
    time: 0,
    phase: "ready",
    hits: 0,
    wallCooldown: 0,
    drift: 0,
  };
}
export function resetToRoad(state, track) {
  const p = atDistance(track, Math.max(0, state.progress));
  state.x = p.x;
  state.y = p.y;
  state.z = p.z;
  state.yaw = p.yaw;
  state.speed = state.vx = state.vz = 0;
}
export function stepCar(s, input, dt, track) {
  if (s.phase === "coasting") return stepCoast(s, dt, track);
  if (s.phase !== "racing") return [];
  dt = clamp(dt, 0, 0.04);
  s.time += dt;
  s.wallCooldown = Math.max(0, s.wallCooldown - dt);
  const throttle = clamp(input.throttle || 0, 0, 1),
    brake = clamp(input.brake || 0, 0, 1),
    steer = clamp(input.steer || 0, -1, 1),
    drifting = !!input.drift && Math.abs(s.speed) > 7;
  // Brake first; holding brake at rest reverses. Air/rolling resistance bounds speed.
  if (throttle) s.speed += 13 * dt;
  else s.speed -= Math.sign(s.speed) * Math.min(Math.abs(s.speed), 1.9 * dt);
  if (brake) s.speed -= s.speed > 1 ? 28 * dt : 7 * dt;
  s.speed -= s.speed * Math.abs(s.speed) * 0.006 * dt;
  if (drifting) s.speed -= s.speed * 0.22 * dt;
  s.speed = clamp(s.speed, -7, 28);
  const yawRate =
    (1.45 / (1 + Math.abs(s.speed) * 0.018)) * (drifting ? 1.28 : 1);
  s.yaw += steer * yawRate * clamp(s.speed / 7, -1, 1) * dt;
  const grip = drifting ? 2.2 : 9;
  s.vx += (Math.sin(s.yaw) * s.speed - s.vx) * Math.min(1, grip * dt);
  s.vz += (-Math.cos(s.yaw) * s.speed - s.vz) * Math.min(1, grip * dt);
  s.x += s.vx * dt;
  s.z += s.vz * dt;
  const p = nearest(track, s.x, s.z),
    events = [];
  s.y = p.y;
  if (p.distance > track.width / 2 - 0.95) {
    const factor = (track.width / 2 - 0.95) / p.distance;
    s.x = p.x + (s.x - p.x) * factor;
    s.z = p.z + (s.z - p.z) * factor;
    s.vx *= 0.4;
    s.vz *= 0.4;
    s.speed *= 0.65;
    if (s.wallCooldown === 0) {
      s.hits++;
      events.push("hit");
      s.wallCooldown = 0.8;
    }
  }
  let delta = p.s - s.lastS;
  if (delta > track.length / 2) delta -= track.length;
  if (delta < -track.length / 2) delta += track.length;
  if (Math.abs(delta) < 12) s.progress += delta;
  s.lastS = p.s;
  if (
    s.checkpoint < 12 * s.raceLaps &&
    s.progress >= ((s.checkpoint + 1) * track.length) / 12
  ) {
    s.checkpoint++;
    events.push("checkpoint");
    if (s.checkpoint % 12 === 0) {
      s.lapTimes.push(s.time - s.lapStartTime);
      s.lapStartTime = s.time;
      events.push("lap");
    }
  }
  if (
    s.checkpoint === 12 * s.raceLaps &&
    s.progress >= track.length * s.raceLaps
  ) {
    s.phase = "coasting";
    s.coastTime = 0;
    s.coastProgress = s.progress;
    s.finishSpeed = Math.max(0, s.speed);
    s.coastOffsetX = s.x - p.x;
    s.coastOffsetZ = s.z - p.z;
    events.push("finish");
  }
  const direction = Math.atan2(s.vx, -s.vz);
  s.drift = drifting ? Math.abs(angleDelta(s.yaw, direction)) : 0;
  return events;
}
// Presentation-only coast-down: the competitive clock, hits and race distance freeze.
// Gentle course following here avoids a post-finish crash; racing has no assistance.
export function stepCoast(s, dt, track) {
  if (s.phase !== "coasting") return [];
  dt = clamp(dt, 0, 0.04);
  s.coastTime += dt;
  s.speed = s.finishSpeed * Math.max(0, 1 - s.coastTime / 4);
  s.coastProgress += s.speed * dt;
  const q = atDistance(track, s.coastProgress),
    fade = Math.exp(-s.coastTime * 1.5);
  s.x = q.x + s.coastOffsetX * fade;
  s.z = q.z + s.coastOffsetZ * fade;
  s.y = q.y;
  s.yaw += angleDelta(q.yaw, s.yaw) * Math.min(1, dt * 3);
  s.vx = Math.sin(s.yaw) * s.speed;
  s.vz = -Math.cos(s.yaw) * s.speed;
  s.drift = 0;
  if (s.coastTime >= 4) {
    s.phase = "finished";
    s.speed = s.vx = s.vz = 0;
    return ["results"];
  }
  return [];
}
