import * as T from "../vendor/three.module.js";
import { buildWorld } from "./adventure-world.js";
import { loadPip } from "./actor.js";
import {
  newJourney,
  step,
  interact,
  nearby,
  objective,
  respawn,
  PARTS,
  FIREFLIES,
  LANTERNS,
  DIRECTIONS,
  BRIDGE,
  HEART,
} from "./journey.js";
import { progressStore } from "./progress.js";
import { createInput } from "./adventure-input.js";
const $ = (s) => document.querySelector(s),
  canvas = $("#game"),
  panel = $("#panel"),
  store = progressStore();
const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
if (!store.available) $(".controls").textContent = "WASD / arrows to move · Space to jump · E to interact · Esc to pause. This exported copy plays without saved checkpoints.";
let state = newJourney(),
  pip,
  world,
  renderer,
  loaded = false,
  saved = null,
  loadError = false,
  busy = false,
  last = performance.now(),
  time = 0,
  frames = 0,
  autosave = 0,
  saveState = "Loading",
  toastUntil = 0,
  completionAt = Infinity,
  resetConfirmation = false,
  bridgeHoldUntil = 0;
const scene = new T.Scene(),
  camera = new T.PerspectiveCamera(48, 1, 0.1, 220);
const input = createInput(() => togglePause());
try {
  renderer = new T.WebGLRenderer({
    canvas,
    antialias: true,
    powerPreference: "high-performance",
  });
} catch {
  $("#panel-title").textContent = "Graphics unavailable";
  $("#panel-copy").textContent =
    "This adventure needs WebGL. Enable graphics acceleration and reopen the game.";
  throw Error("WebGL unavailable");
}
renderer.outputColorSpace = T.SRGBColorSpace;
renderer.toneMapping = T.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.05;
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = T.PCFSoftShadowMap;
function resize() {
  renderer.setPixelRatio(
    Math.min(
      devicePixelRatio,
      $("#quality").value === "high"
        ? 2
        : $("#quality").value === "low"
          ? 1
          : 1.5,
    ),
  );
  renderer.setSize(innerWidth, innerHeight, false);
  camera.aspect = innerWidth / innerHeight;
  camera.updateProjectionMatrix();
}
addEventListener("resize", resize);
resize();
const focus = new T.Vector3(0, 0, 20),
  want = new T.Vector3();
function placeCamera(dt, snap = false) {
  want.set(state.x, 0, state.z - 2.3);
  if (snap) focus.copy(want);
  else focus.lerp(want, 1 - Math.exp(-dt * 5));
  const wide = innerWidth < 700 ? 1.2 : 1;
  camera.position.set(focus.x + 2, focus.y + 9.5 * wide, focus.z + 13 * wide);
  camera.lookAt(focus.x, 1, focus.z - 1);
}
placeCamera(0, true);
function toast(text) {
  $("#toast").textContent = text;
  toastUntil = performance.now() + 5500;
  $("#toast").hidden = false;
}
let audio = null,
  muted = true;
function note(freq = 660) {
  if (muted) return;
  try {
    audio ??= new AudioContext();
    void audio.resume();
    const o = audio.createOscillator(),
      g = audio.createGain(),
      t = audio.currentTime;
    o.type = "sine";
    o.frequency.setValueAtTime(freq, t);
    g.gain.setValueAtTime(0.06, t);
    g.gain.exponentialRampToValueAtTime(0.001, t + 0.5);
    o.connect(g).connect(audio.destination);
    o.start();
    o.stop(t + 0.52);
  } catch {
    muted = true;
  }
}
$("#sound").onclick = () => {
  muted = !muted;
  $("#sound").textContent = muted ? "Sound off" : "Sound on";
  note();
};
function sync() {
  if (!pip) return;
  pip.root.position.set(state.x, state.y, state.z);
  pip.root.rotation.y = state.yaw;
}
async function save() {
  if (!loaded || loadError) return;
  try {
    saveState = store.available ? "Saving…" : "This play only";
    const ok = await store.save(state);
    saveState = ok ? "Saved on this device" : "This play only";
  } catch (e) {
    saveState = "Could not save";
    toast(e.message);
  }
}
function show(title, copy, action) {
  $("#panel-title").textContent = title;
  $("#panel-copy").textContent = copy;
  $("#start").textContent = action;
  $("#start").hidden = !action;
  $("#start").disabled = busy;
  panel.hidden = false;
}
function hide() {
  panel.hidden = true;
  resetConfirmation = false;
  $("#new").hidden = true;
  $("#checkpoint").hidden = true;
  $("#cancel").hidden = true;
  input.clear();
  canvas.focus();
}
function pause() {
  if (state.phase !== "playing") return;
  state.phase = "paused";
  input.clear();
  void save();
  show(
    "Take a breather.",
    "Your progress stays with you. Continue whenever you’re ready.",
    "Resume",
  );
  $("#new").hidden = false;
  $("#checkpoint").hidden = false;
}
function togglePause() {
  if (state.phase === "playing") pause();
  else if (state.phase === "paused" && !resetConfirmation) resume();
}
function resume() {
  if (!loaded) return;
  state.phase = "playing";
  completionAt = Infinity;
  hide();
  last = performance.now();
}
async function fresh() {
  if (busy) return;
  busy = true;
  $("#start").disabled = true;
  try {
    await store.reset();
    state = newJourney();
    loadError = false;
    saved = null;
    state.phase = "playing";
    pip.reset();
    sync();
    placeCamera(0, true);
    completionAt = Infinity;
    hide();
    await save();
  } catch (e) {
    toast(`Could not start a new journey: ${e.message}`);
  } finally {
    busy = false;
    $("#start").disabled = false;
  }
}
$("#start").onclick = () => {
  if (resetConfirmation) {
    void fresh();
    return;
  }
  if (state.phase === "paused") {
    resume();
    return;
  }
  if (saved) {
    state = saved;
    saved = null;
    pip.reset();
    world.update(state, time, 1, reduced);
    sync();
    placeCamera(0, true);
    show(
      "Welcome back.",
      `${state.checkpoint === "home" ? "Home Garden" : "Woodland Trail"} is waiting. Your checkpoint has been restored safely in pause.`,
      "Resume",
    );
    $("#new").hidden = false;
    $("#checkpoint").hidden = false;
    return;
  }
  if (state.completed) {
    resume();
    return;
  }
  void fresh();
};
$("#new").onclick = () => {
  resetConfirmation = true;
  show(
    "A fresh beginning?",
    "This replaces the saved adventure on this device. Your other games are not affected.",
    "Start new adventure",
  );
  $("#new").hidden = true;
  $("#checkpoint").hidden = true;
  $("#cancel").hidden = false;
};
$("#cancel").onclick = () => {
  resetConfirmation = false;
  $("#cancel").hidden = true;
  if (state.phase === "ready") {
    show(
      "Pip’s Adventure",
      loadError
        ? "The saved checkpoint could not be read. It has not been changed."
        : "A little care. A longer journey.",
      saved ? "Continue adventure" : null,
    );
    $("#new").hidden = false;
  } else {
    show("Take a breather.", "Your journey is still here.", "Resume");
    $("#new").hidden = false;
    $("#checkpoint").hidden = false;
  }
};
$("#pause").onclick = togglePause;
$("#checkpoint").onclick = () => {
  respawn(state);
  sync();
  placeCamera(0, true);
  void save();
  resume();
};
$("#quality").onchange = () => {
  resize();
  world?.quality($("#quality").value);
};
addEventListener("blur", () => {
  input.clear();
  pause();
});
document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    input.clear();
    pause();
  }
});
function event(e) {
  if (e.type === "jump") {
    pip.jump();
    return;
  }
  note(e.type === "part" ? 880 : e.type === "firefly" ? 1046 : 660);
  if (e.type === "bridge") {
    bridgeHoldUntil = performance.now() + 1800;
    state.moving = false;
    input.clear();
  }
  if (e.type === "part")
    toast(`Copper part found · ${state.parts.filter(Boolean).length} / 3`);
  else if (e.type === "firefly")
    toast(
      `A little light · ${state.fireflies.filter(Boolean).length} / ${FIREFLIES.length}`,
    );
  else if (e.type === "checkpoint")
    toast("Woodland Trail · checkpoint reached");
  else if (e.text) toast(e.text);
  if (e.type === "complete") {
    pip.play("Dance");
    input.clear();
    completionAt = performance.now() + 2200;
  }
  if (e.type !== "hint") void save();
}
const map = $("#map"),
  ctx = map.getContext("2d");
function drawMap() {
  const w = map.width,
    h = map.height,
    px = (x) => w / 2 + x * 2.2,
    pz = (z) => 12 + (z + 78) * 2.2;
  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = "#354f48";
  ctx.fillRect(0, 0, w, h);
  ctx.fillStyle = "#809f73";
  ctx.beginPath();
  ctx.ellipse(px(0), pz(8), 44, 44, 0, 0, 7);
  ctx.fill();
  ctx.beginPath();
  ctx.ellipse(px(0), pz(-52), 57, 50, 0, 0, 7);
  ctx.fill();
  ctx.fillStyle = state.bridge ? "#d6bd8e" : "#53675d";
  ctx.fillRect(px(-2.4), pz(-32), 10.6, 49);
  const dot = (p, color, r = 3) => {
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.arc(px(p.x), pz(p.z), r, 0, 7);
    ctx.fill();
  };
  PARTS.forEach((p, i) => {
    if (!state.parts[i]) dot(p, "#ffd178", 4);
  });
  LANTERNS.forEach((p, i) =>
    dot(p, state.dials[i] === p.target ? "#b7ffe0" : "#eee5c8", 3),
  );
  dot(HEART, "#edcf7b", 4);
  dot(state, "#fff", 4);
  ctx.fillStyle = "#fff";
  ctx.font = "10px system-ui";
  ctx.fillText("N ↑", 9, 14);
}
function hud() {
  const [title, copy] = objective(state);
  $("#objective-title").textContent = title;
  $("#objective-copy").textContent = copy;
  $("#area").textContent =
    state.z < -30
      ? "Woodland Trail"
      : state.z < -10
        ? "Old Garden Bridge"
        : "Home Garden";
  $("#lights").textContent =
    `${state.fireflies.filter(Boolean).length} / ${FIREFLIES.length} lights`;
  $("#save").textContent = saveState;
  const n = state.phase === "playing" ? nearby(state) : null;
  $("#interaction").hidden = !n;
  if (n) {
    $("#interaction-label").textContent = n.label;
    $("#clue").textContent =
      n.kind === "lantern"
        ? `${LANTERNS[n.index].clue} Now facing ${DIRECTIONS[state.dials[n.index]]}.`
        : "";
  }
  $("#pause").disabled =
    !loaded || !["playing", "paused"].includes(state.phase);
  $("#pause").textContent = state.phase === "paused" ? "Resume" : "Pause";
}
async function init() {
  try {
    [world, pip] = await Promise.all([
      buildWorld(scene, renderer),
      loadPip(() => {}),
    ]);
    scene.add(pip.root);
    loaded = true;
    sync();
    world.quality($("#quality").value);
    try {
      saved = await store.load();
      saveState = store.available
        ? "Progress saves automatically"
        : "This play only";
    } catch (e) {
      loadError = true;
      saveState = "Saved progress preserved";
      toast(e.message);
    }
    show(
      "Pip’s Adventure",
      loadError
        ? "Your checkpoint could not be read. It has not been changed."
        : saved
          ? "A saved journey is waiting. Pick up where you left off."
          : "Find the missing bridge parts. Follow a woodland trail. Bring a little light back to the world.",
      loadError ? null : saved ? "Continue adventure" : "Begin adventure",
    );
    $("#new").hidden = !(saved || loadError);
    $("#start").disabled = false;
  } catch (e) {
    show(
      "The garden could not load.",
      "A local art file or model is missing. Reopen the game or restore its original files.",
      null,
    );
    $("#save").textContent = e.message;
  }
}
void init();
function frame(now) {
  requestAnimationFrame(frame);
  const dt = Math.max(0, Math.min(0.04, (now - last) / 1000));
  last = now;
  frames++;
  if (loaded) {
    if (state.phase === "playing") {
      if (now >= bridgeHoldUntil) {
        const events = step(state, input.axis(), input.takeJump(), dt);
        events.forEach(event);
        if (input.takeInteract()) {
          const e = interact(state);
          if (e) event(e);
        }
      }
      if (now - autosave > 4000) {
        autosave = now;
        void save();
      }
    }
    if (state.phase !== "paused") {
      time += dt;
      if (state.phase !== "won")
        pip.play(
          state.y > 0 ? "Jump" : state.moving ? "Running" : "Idle",
          0.18,
        );
      pip.mixer.update(dt);
      world.update(state, time, dt, reduced);
    }
    world.follow(state);
    sync();
    placeCamera(dt);
    if (frames % 6 === 0) {
      hud();
      drawMap();
    }
    if (now > completionAt) {
      completionAt = Infinity;
      show(
        "A little light returns.",
        "The bridge is mended and the woodland is awake. You can keep exploring for the remaining fireflies.",
        "Keep exploring",
      );
      $("#new").hidden = false;
    }
  }
  if (now > toastUntil) $("#toast").hidden = true;
  renderer.render(scene, camera);
}
requestAnimationFrame(frame);
window.pipStatus = () => ({
  phase: state.phase,
  x: state.x,
  z: state.z,
  y: state.y,
  elapsed: state.elapsed,
  parts: [...state.parts],
  bridge: state.bridge,
  dials: [...state.dials],
  fireflies: [...state.fireflies],
  checkpoint: state.checkpoint,
  completed: state.completed,
  assetLoaded: loaded,
  clip: pip?.clip,
  mixerTime: pip?.mixer.time,
  frames,
  saveState,
  models: world?.loadedModels,
  geometries: renderer.info.memory.geometries,
  textures: renderer.info.memory.textures,
  triangles: renderer.info.render.triangles,
});
