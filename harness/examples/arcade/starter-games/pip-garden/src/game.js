import * as THREE from '../vendor/three.module.js';
import { buildWorld } from './world.js';
import { createFireflies, TOTAL } from './fireflies.js';
import { loadPip } from './actor.js';
import { createInput } from './input.js';
import { newPlayer, movePlayer } from './player.js';
import { chime, fanfare } from './sound.js';

const $ = s => document.querySelector(s);
const canvas = $('#game'), panel = $('#panel'), prog = $('#prog');
const startBtn = $('#start'), pauseBtn = $('#pause'), restartBtn = $('#restart'), scoreEl = $('#score');
function showPanel(title, text, btn, err = false) {
  $('#title').textContent = title; $('#msg').textContent = text; panel.classList.toggle('err', err);
  startBtn.hidden = !btn; if (btn) startBtn.textContent = btn;
  panel.hidden = false;
}
let renderer;
try { renderer = new THREE.WebGLRenderer({ canvas, antialias: true }); }
catch (e) { showPanel('3D unavailable', 'WebGL could not start. Enable hardware acceleration and reload.', null, true); throw e; }
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.shadowMap.enabled = true; renderer.shadowMap.type = THREE.PCFSoftShadowMap;
const scene = new THREE.Scene(), camera = new THREE.PerspectiveCamera(50, 1, 0.1, 120);
const { obstacles, clouds } = buildWorld(scene);
const flies = createFireflies(scene);
let phase = 'loading', p = newPlayer(), score = 0, pip = null, frames = 0, last = performance.now(), clock = 0;
const input = createInput({ onPauseKey: () => phase === 'playing' ? pause() : phase === 'paused' && resume() });

function resize() {
  renderer.setSize(innerWidth, innerHeight, false);
  camera.aspect = innerWidth / innerHeight; camera.fov = camera.aspect < 0.8 ? 64 : 50;
  camera.updateProjectionMatrix();
}
addEventListener('resize', resize); resize();
const focus = new THREE.Vector3(), want = new THREE.Vector3();
function placeCamera(snap) {
  want.set(p.x * 0.6, 0.8, p.z * 0.6);
  snap ? focus.copy(want) : focus.lerp(want, 0.08);
  const back = camera.aspect < 0.8 ? 1.4 : 1;
  camera.position.set(focus.x, focus.y + 7.5 * back, focus.z + 9.5 * back);
  camera.lookAt(focus);
}
placeCamera(true);
const hud = () => { scoreEl.textContent = `Fireflies ${score} / ${TOTAL}`; };
function syncPip() { pip.root.position.set(p.x, p.y, p.z); pip.root.rotation.y = p.yaw; }

function begin() { // Start / Restart: full reset to start position and all fireflies.
  if (!pip) return;
  p = newPlayer(); score = 0; flies.reset(); input.clear(); hud();
  pip.reset(); syncPip(); placeCamera(true);
  phase = 'playing'; panel.hidden = true;
  pauseBtn.disabled = false; restartBtn.disabled = false; pauseBtn.textContent = 'Pause';
  canvas.focus();
}
function pause() {
  if (phase !== 'playing') return;
  phase = 'paused'; input.clear(); pauseBtn.textContent = 'Resume';
  showPanel('Paused', 'Pip is waiting among the flowers.', 'Resume');
}
function resume() {
  if (phase !== 'paused') return;
  phase = 'playing'; input.clear(); panel.hidden = true; pauseBtn.textContent = 'Pause';
  last = performance.now(); canvas.focus();
}
function win() {
  phase = 'won'; input.clear(); pauseBtn.disabled = true;
  pip.play('Dance', 0.3); fanfare();
  setTimeout(() => { if (phase === 'won') showPanel('All fireflies found!', 'Pip dances for joy. The garden glows tonight.', 'Play again'); }, 1800);
}
startBtn.addEventListener('click', () => { if (phase === 'paused') resume(); else if (phase === 'ready' || phase === 'won') begin(); });
pauseBtn.addEventListener('click', () => { phase === 'playing' ? pause() : resume(); pauseBtn.blur(); });
restartBtn.addEventListener('click', () => { begin(); restartBtn.blur(); });
addEventListener('blur', () => { input.clear(); pause(); });
document.addEventListener('visibilitychange', () => { if (document.hidden) { input.clear(); pause(); } });

function step(dt) {
  if (movePlayer(p, input.axis(), input.takeJump(), dt, obstacles)) pip.jump();
  if (!p.jumping) pip.play(p.moving ? 'Running' : 'Idle', 0.2);
  const got = flies.collect(p.x, p.y, p.z);
  if (got) { score += got; hud(); chime(score); if (score >= TOTAL) win(); }
}
function frame(now) {
  requestAnimationFrame(frame);
  const dt = Math.min((now - last) / 1000, 0.05); last = now;
  if (phase !== 'paused') { clock += dt; flies.update(dt, clock); clouds.position.x = Math.sin(clock * 0.05) * 2; }
  if (pip) {
    if (phase === 'playing') step(dt);
    if (phase !== 'paused') pip.mixer.update(dt); // mixer stops while paused
    syncPip();
  }
  placeCamera(false);
  renderer.render(scene, camera); frames++;
}
requestAnimationFrame(frame);

showPanel("Pip's Garden", 'Loading Pip the robot…', 'Loading…'); startBtn.disabled = true;
loadPip(f => { prog.style.width = Math.round(f * 100) + '%'; }).then(robot => {
  pip = robot; scene.add(pip.root); syncPip();
  phase = 'ready'; startBtn.disabled = false; prog.parentElement.hidden = true;
  showPanel("Pip's Garden", 'Help Pip gather the 5 glowing fireflies hidden around the floating garden.', 'Start');
}).catch(err => {
  console.error(err);
  phase = 'error'; prog.parentElement.hidden = true;
  showPanel('Pip could not load', `The robot model (assets/library/pip-robot.glb) failed to load: ${err?.message || err}. Check that the file exists, then reload.`, null, true);
});

// Read-only acceptance telemetry: returns a fresh snapshot, no state changes.
window.pipStatus = () => {
  const mem = renderer.info.memory;
  return {
    phase, x: +p.x.toFixed(3), z: +p.z.toFixed(3), score, total: TOTAL, assetLoaded: !!pip,
    clip: pip ? pip.clip : null, mixerTime: pip ? pip.mixer.time : 0, frames,
    remaining: flies.remaining(), geometries: mem.geometries, textures: mem.textures
  };
};
