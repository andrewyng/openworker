import { WALK_R } from './world.js';
// Pure gameplay movement: XZ walking, circle collision, island edge, jump physics.
export const SPEED = 3.6, JUMP_V = 5.4, GRAVITY = 15;
export const START = { x: 0, z: 6, yaw: Math.PI };
export function newPlayer() {
  return { x: START.x, y: 0, z: START.z, vy: 0, yaw: START.yaw, moving: false, jumping: false };
}
// Returns true when a jump began this step.
export function movePlayer(p, axis, wantJump, dt, obstacles) {
  p.moving = axis.x !== 0 || axis.z !== 0;
  if (p.moving) {
    let nx = p.x + axis.x * SPEED * dt, nz = p.z + axis.z * SPEED * dt;
    for (const o of obstacles) {
      const dx = nx - o.x, dz = nz - o.z, d = Math.hypot(dx, dz);
      if (d < o.r && d > 1e-4) { nx = o.x + dx / d * o.r; nz = o.z + dz / d * o.r; }
    }
    const r = Math.hypot(nx, nz);
    if (r > WALK_R) { nx *= WALK_R / r; nz *= WALK_R / r; }
    p.x = nx; p.z = nz;
    const target = Math.atan2(axis.x, axis.z); // model faces +Z, so yaw = atan2(x, z)
    let diff = target - p.yaw; diff = Math.atan2(Math.sin(diff), Math.cos(diff));
    p.yaw += diff * Math.min(1, dt * 12);
  }
  let jumped = false;
  if (wantJump && p.y <= 0 && p.vy <= 0) { p.vy = JUMP_V; p.jumping = true; jumped = true; }
  p.vy -= GRAVITY * dt; p.y += p.vy * dt;
  if (p.y < 0) { p.y = 0; p.vy = 0; if (!jumped) p.jumping = false; }
  return jumped;
}
