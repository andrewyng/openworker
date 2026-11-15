// Pure progression and collision rules. Rendering, storage and input are separate.
export const LAYOUT = "pip-adventure-home-woodland-1";
export const PARTS = [
  { x: -11, z: 12 },
  { x: 10, z: 11 },
  { x: -7, z: -3 },
];
export const FIREFLIES = [
  { x: -15, z: 1 },
  { x: 14, z: 20 },
  { x: 4, z: -37 },
  { x: -17, z: -53 },
  { x: 17, z: -62 },
  { x: 0, z: -72 },
];
export const LANTERNS = [
  {
    x: -10,
    z: -43,
    name: "Sunrise",
    target: 1,
    clue: "Face east, toward the sunrise.",
  },
  {
    x: 9,
    z: -51,
    name: "Sunset",
    target: 3,
    clue: "Face west, toward the sunset.",
  },
  {
    x: -6,
    z: -63,
    name: "North Star",
    target: 0,
    clue: "Face north, toward the mountains.",
  },
];
export const DIRECTIONS = ["North", "East", "South", "West"];
export const BRIDGE = { x: -2, z: -8 };
export const HEART = { x: 0, z: -69 };
export const CHECKPOINTS = {
  home: { x: 0, z: 22 },
  woodland: { x: 0, z: -34 },
  heart: { x: 0, z: -65 },
};
export const TREES = [
  [-16, 16, 2.8],
  [-17, 8, 3.4],
  [16, 10, 3.2],
  [15, 0, 3.6],
  [-14, -5, 3.1],
  [8, 24, 2.7],
  [-8, 25, 2.8],
  [-19, -38, 4.8],
  [-15, -35, 4.2],
  [15, -36, 4.6],
  [19, -42, 5.2],
  [-21, -48, 4.5],
  [20, -54, 4.8],
  [-21, -60, 4.3],
  [13, -68, 4.8],
  [-15, -69, 5.2],
  [9, -73, 4.4],
  [-8, -74, 4.6],
  [-16, -57, 3.8],
  [16, -47, 4.2],
];
export const OBSTACLES = [
  ...TREES.map(([x, z]) => ({ x, z, r: 0.9 })),
  ...LANTERNS.map((p) => ({ ...p, r: 1.15 })),
  { ...BRIDGE, r: 1 },
  { ...HEART, r: 1.1 },
  { x: -10, z: 22, r: 3 },
  { x: 13, z: -6, r: 1.3 },
];
export function walkable(x, z, bridge) {
  const home = (x / 19.4) ** 2 + ((z - 8) / 19.4) ** 2 <= 1;
  const crossing = bridge && Math.abs(x) <= 2.35 && z <= -9 && z >= -33;
  const forest = bridge && (x / 25.4) ** 2 + ((z + 52) / 22.4) ** 2 <= 1;
  return home || crossing || forest;
}
function clear(x, z, bridge) {
  return (
    walkable(x, z, bridge) &&
    OBSTACLES.every((o) => Math.hypot(x - o.x, z - o.z) >= o.r)
  );
}
export function newJourney() {
  return {
    phase: "ready",
    x: 0,
    z: 22,
    y: 0,
    vy: 0,
    yaw: Math.PI,
    moving: false,
    elapsed: 0,
    parts: [false, false, false],
    bridge: false,
    dials: [0, 1, 2],
    fireflies: FIREFLIES.map(() => false),
    checkpoint: "home",
    completed: false,
  };
}
export function step(s, axis, jump, dt) {
  if (s.phase !== "playing") return [];
  dt = Math.max(0, Math.min(0.05, dt));
  s.elapsed += dt;
  const events = [],
    mag = Math.hypot(axis.x, axis.z),
    factor = mag > 1 ? 1 / mag : 1;
  const dx = axis.x * factor * 5.2 * dt,
    dz = axis.z * factor * 5.2 * dt;
  const beforeX = s.x,
    beforeZ = s.z;
  if (clear(s.x + dx, s.z + dz, s.bridge)) {
    s.x += dx;
    s.z += dz;
  } else {
    if (clear(s.x + dx, s.z, s.bridge)) s.x += dx;
    if (clear(s.x, s.z + dz, s.bridge)) s.z += dz;
  }
  s.moving = Math.hypot(s.x - beforeX, s.z - beforeZ) > 0.00001;
  if (mag) {
    const target = Math.atan2(axis.x, axis.z);
    s.yaw +=
      Math.atan2(Math.sin(target - s.yaw), Math.cos(target - s.yaw)) *
      Math.min(1, dt * 12);
  }
  if (jump && s.y === 0) {
    s.vy = 5.4;
    events.push({ type: "jump" });
  }
  s.vy -= 14 * dt;
  s.y = Math.max(0, s.y + s.vy * dt);
  if (!s.y) s.vy = 0;
  PARTS.forEach((p, i) => {
    if (!s.parts[i] && Math.hypot(s.x - p.x, s.z - p.z) < 1.25) {
      s.parts[i] = true;
      events.push({ type: "part", index: i });
    }
  });
  FIREFLIES.forEach((p, i) => {
    if (!s.fireflies[i] && Math.hypot(s.x - p.x, s.z - p.z) < 1.15) {
      s.fireflies[i] = true;
      events.push({ type: "firefly", index: i });
    }
  });
  if (s.bridge && s.z < -33 && s.checkpoint === "home") {
    s.checkpoint = "woodland";
    events.push({ type: "checkpoint" });
  }
  return events;
}
export function nearby(s) {
  const near = (p) => Math.hypot(s.x - p.x, s.z - p.z) < 2.8;
  if (near(BRIDGE) && !s.bridge)
    return {
      kind: "bridge",
      label: s.parts.every(Boolean)
        ? "Repair the bridge"
        : "Bridge needs three copper parts",
    };
  if (s.bridge) {
    const index = LANTERNS.findIndex(near);
    if (index >= 0)
      return {
        kind: "lantern",
        index,
        label: `${LANTERNS[index].name} lantern · turn clockwise`,
      };
    if (near(HEART) && !s.completed)
      return {
        kind: "heart",
        label: solved(s)
          ? "Restore the heart tree"
          : "The heart tree needs three aligned lanterns",
      };
  }
  return null;
}
export const solved = (s) => s.dials.every((v, i) => v === LANTERNS[i].target);
export function interact(s) {
  if (s.phase !== "playing") return null;
  const n = nearby(s);
  if (!n) return null;
  if (n.kind === "bridge") {
    if (!s.parts.every(Boolean))
      return {
        type: "hint",
        text: "Find the three glowing copper parts around Home Garden.",
      };
    s.bridge = true;
    return {
      type: "bridge",
      text: "Bridge restored. Follow the trail into the woods.",
    };
  }
  if (n.kind === "lantern") {
    s.dials[n.index] = (s.dials[n.index] + 1) % 4;
    return {
      type: "lantern",
      index: n.index,
      text: `${LANTERNS[n.index].name}: facing ${DIRECTIONS[s.dials[n.index]]}. ${LANTERNS[n.index].clue}`,
    };
  }
  if (!solved(s))
    return {
      type: "hint",
      text: "Read each lantern’s carved direction, then turn it to match.",
    };
  s.completed = true;
  s.checkpoint = "heart";
  s.phase = "won";
  s.moving = false;
  return {
    type: "complete",
    text: "The woodland is glowing again. A little care goes a long way.",
  };
}
export function objective(s) {
  if (s.completed)
    return [
      "Woodland restored",
      "Explore the garden or start a new adventure.",
    ];
  if (!s.parts.every(Boolean))
    return [
      "Mend the old bridge",
      `Find three copper parts · ${s.parts.filter(Boolean).length} / 3`,
    ];
  if (!s.bridge)
    return [
      "Return to the bridge",
      "Press E by the bridge mechanism to repair it.",
    ];
  if (s.checkpoint === "home")
    return [
      "A path into the woods",
      "Cross the bridge to reach Woodland Trail.",
    ];
  if (!solved(s))
    return [
      "Wake the woodland lanterns",
      `Match the carved directions · ${s.dials.filter((v, i) => v === LANTERNS[i].target).length} / 3 aligned`,
    ];
  return [
    "Restore the heart tree",
    "Follow the lights north. Press E beside the tree.",
  ];
}
export function respawn(s) {
  Object.assign(s, CHECKPOINTS[s.checkpoint], { y: 0, vy: 0, moving: false });
}
export function snapshot(s) {
  return {
    layout: LAYOUT,
    x: s.x,
    z: s.z,
    yaw: s.yaw,
    elapsed: s.elapsed,
    parts: [...s.parts],
    bridge: s.bridge,
    dials: [...s.dials],
    fireflies: [...s.fireflies],
    checkpoint: s.checkpoint,
    completed: s.completed,
  };
}
export function restore(v) {
  const bools = (a, n) =>
    Array.isArray(a) &&
    a.length === n &&
    a.every((x) => typeof x === "boolean");
  if (
    !v ||
    v.layout !== LAYOUT ||
    !bools(v.parts, 3) ||
    !bools(v.fireflies, FIREFLIES.length) ||
    typeof v.bridge !== "boolean" ||
    typeof v.completed !== "boolean" ||
    !Array.isArray(v.dials) ||
    v.dials.length !== 3 ||
    v.dials.some((n) => !Number.isInteger(n) || n < 0 || n > 3) ||
    !Object.hasOwn(CHECKPOINTS, v.checkpoint) ||
    !["x", "z", "yaw", "elapsed"].every(
      (k) => typeof v[k] === "number" && Number.isFinite(v[k]),
    ) ||
    Math.abs(v.yaw) > 10000 ||
    v.elapsed < 0 ||
    v.elapsed > 1e7 ||
    !clear(v.x, v.z, v.bridge) ||
    (v.bridge && !v.parts.every(Boolean)) ||
    (!v.bridge &&
      (v.checkpoint !== "home" ||
        v.dials.some((d, i) => d !== [0, 1, 2][i]))) ||
    (v.completed && (!v.bridge || !solved(v) || v.checkpoint !== "heart")) ||
    (!v.completed && v.checkpoint === "heart")
  )
    throw Error(
      "This checkpoint is incompatible or invalid. It has not been changed.",
    );
  return { ...newJourney(), ...snapshot(v), phase: "paused" };
}
