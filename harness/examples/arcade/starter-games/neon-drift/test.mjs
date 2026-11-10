// Run from any directory: node /path/to/neon-drift/test.mjs
// Deterministic gameplay tests; no browser or third-party dependencies required.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const source = readFileSync(new URL('./game.js', import.meta.url), 'utf8');

function makeGame(reducedMotion = false) {
  const elements = new Map(), events = {}, documentEvents = {};
  let frame, time = 0, seed = 123456789;
  const seededMath = Object.create(Math);
  seededMath.random = () => ((seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0) / 4294967296);
  const context = new Proxy({}, {
    get(target, property) {
      if (property === 'createRadialGradient') return () => ({ addColorStop() {} });
      return target[property] || (() => {});
    },
    set(target, property, value) { target[property] = value; return true; },
  });
  const element = id => {
    if (!elements.has(id)) elements.set(id, {
      style: {}, hidden: false, disabled: false, textContent: '', innerHTML: '', handlers: {}, attributes: {},
      setAttribute(name, value) { this.attributes[name] = value; }, focus() {},
      getBoundingClientRect() { return { width: 1000, height: 650, left: 0, top: 0 }; },
      getContext() { return context; }, setPointerCapture() {},
      addEventListener(name, handler) { this.handlers[name] = handler; },
    });
    return elements.get(id);
  };
  const sandbox = {
    Math: seededMath, devicePixelRatio: 1,
    document: { querySelector: () => element('arena'), getElementById: element, addEventListener(name, handler) { documentEvents[name] = handler; } },
    window: { matchMedia: () => ({ matches: reducedMotion }), addEventListener(name, handler) { events[name] = handler; } },
    ResizeObserver: class { constructor(handler) { this.handler = handler; } observe() { this.handler(); } },
    requestAnimationFrame(handler) { frame = handler; },
  };
  // Expose state in the VM only; the shipped game contains no testing API.
  const bootstrap = 'reset(); resize(); requestAnimationFrame(tick);';
  assert(source.includes(bootstrap), 'Expected game bootstrap for VM instrumentation');
  vm.runInNewContext(source.replace(bootstrap, `window.probe = () => ({ state, player, shards, enemies, timer, energy, lives, cooldown, trail }); ${bootstrap}`), sandbox);
  const step = (count = 1) => { for (let i = 0; i < count; i++) { time += 20; frame(time); } };
  const probe = () => sandbox.window.probe();
  const away = () => { for (const enemy of probe().enemies) { enemy.x = -10000; enemy.y = -10000; } };
  const advanceSafely = count => { for (let i = 0; i < count; i++) { away(); step(); } };
  return { element, events, documentEvents, step, probe, away, advanceSafely };
}

for (const reducedMotion of [false, true]) {
  const { element, events, step, probe, advanceSafely } = makeGame(reducedMotion);
  step();
  assert.equal(probe().state, 'title');
  assert.equal(probe().shards.length, 6);
  element('start').handlers.click();
  assert.equal(probe().state, 'playing');
  const initialX = probe().player.x;
  events.keydown({ key: 'd', preventDefault() {} });
  step(30);
  events.keyup({ key: 'd' });
  assert(probe().player.x > initialX + 50, 'Keyboard steering moves the player');
  if (reducedMotion) assert.equal(probe().trail.length, 0, 'Reduced motion disables trails');

  element('dash').handlers.click();
  assert.equal(probe().cooldown, 2.8);
  step(3);
  const cooldown = probe().cooldown;
  element('dash').handlers.click();
  assert.equal(probe().cooldown, cooldown, 'Dash cannot be spammed during cooldown');
  element('pause').handlers.click();
  const pausedTime = probe().timer;
  step(30);
  assert.equal(probe().timer, pausedTime, 'Timer stops while paused');
  assert.equal(probe().state, 'paused');
  element('start').handlers.click();
  assert.equal(probe().state, 'playing');

  const beforePointer = probe().player.y;
  element('arena').handlers.pointerdown({ pointerId: 1, clientX: probe().player.x, clientY: beforePointer + 120 });
  step(20);
  element('arena').handlers.pointerup();
  assert(probe().player.y > beforePointer + 30, 'Pointer steering moves the player');

  // Navigate directly to generated pickups to exercise actual collision/scoring logic.
  for (let i = 0; i < 18; i++) {
    const state = probe();
    state.player.x = state.shards[0].x; state.player.y = state.shards[0].y;
    state.player.vx = state.player.vy = 0;
    step();
  }
  assert.equal(probe().state, 'won');
  assert.equal(probe().energy, 18);
  assert(element('message').innerHTML.includes('18 shards'));
  element('start').handlers.click();
  assert.equal(probe().energy, 0);
  assert.equal(probe().lives, 3);
  assert.equal(probe().timer, 60);

  advanceSafely(3100);
  assert.equal(probe().state, 'lost');
  assert.equal(probe().timer, 0, 'Timer expiry ends the run');
  assert.equal(probe().lives, 3, 'Timeout is independent from damage');

  element('start').handlers.click();
  for (let hit = 0; hit < 3; hit++) {
    const state = probe();
    state.enemies[0].x = state.player.x; state.enemies[0].y = state.player.y;
    step();
    assert.equal(probe().lives, 2 - hit);
    advanceSafely(95);
  }
  assert.equal(probe().state, 'lost', 'Three collisions end the run');
  element('start').handlers.click();
  events.blur();
  assert.equal(probe().state, 'paused', 'Losing browser focus pauses the game');
  console.log(`PASS (${reducedMotion ? 'reduced' : 'standard'} motion): render, start, keyboard/pointer steering, dash cooldown, pause/resume, victory, restart, timeout, damage, blur.`);
}
