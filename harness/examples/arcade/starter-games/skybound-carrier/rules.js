export function seeded(seed) {
  return () => {
    seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
    return seed / 4294967296;
  };
}
export function makeCourse(config) {
  const random = seeded(config.seed),
    rows = [];
  for (let z = 60; z < config.distance - 65; z += 32) {
    const safe = Math.floor(random() * 3),
      type = random() < 0.5 ? "jump" : "slide";
    rows.push({ z, type, lane: (safe + 1) % 3 });
    if (z > 200 && random() > 0.45)
      rows.push({
        z,
        type: random() < 0.5 ? "jump" : "slide",
        lane: (safe + 2) % 3,
      });
    for (let i = 0; i < 3; i++)
      rows.push({ z: z - 12 - i * 3, type: "coin", lane: safe });
  }
  return rows;
}
export function createRun(config) {
  return {
    phase: "ready",
    distance: 0,
    lane: 1,
    jump: 0,
    slide: 0,
    lives: config.lives,
    coins: 0,
    invincible: 0,
    visited: new Set(),
    hits: 0,
  };
}
export function command(s, name, config) {
  if (s.phase !== "running") return;
  if (name === "left") s.lane = Math.max(0, s.lane - 1);
  if (name === "right") s.lane = Math.min(2, s.lane + 1);
  if (name === "jump" && !s.jump && !s.slide) s.jump = config.jumpSeconds;
  if (name === "slide" && !s.jump && !s.slide) s.slide = config.slideSeconds;
}
export function step(s, dt, config, course) {
  if (s.phase !== "running") return [];
  dt = Math.min(0.05, Math.max(0, dt));
  s.distance +=
    dt * Math.min(config.maxSpeed, config.startSpeed + s.distance / 150);
  s.jump = Math.max(0, s.jump - dt);
  s.slide = Math.max(0, s.slide - dt);
  s.invincible = Math.max(0, s.invincible - dt);
  const events = [];
  for (let i = 0; i < course.length; i++) {
    const o = course[i];
    if (s.visited.has(i) || o.z > s.distance + 0.7) continue;
    s.visited.add(i);
    if (Math.abs(o.z - s.distance) > 2 || o.lane !== s.lane) continue;
    if (o.type === "coin") {
      s.coins++;
      events.push("coin");
    } else if (
      !s.invincible &&
      !(o.type === "jump" && s.jump > 0.15) &&
      !(o.type === "slide" && s.slide > 0)
    ) {
      s.lives--;
      s.hits++;
      s.invincible = 1.8;
      events.push("hit");
      if (s.lives <= 0) {
        s.phase = "lost";
        break;
      }
    }
  }
  if (s.phase === "running" && s.distance >= config.distance) {
    s.phase = "won";
    events.push("won");
  }
  return events;
}
