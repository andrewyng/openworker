import * as THREE from "./vendor/three.module.js";
import { GLTFLoader } from "./vendor/addons/loaders/GLTFLoader.js";
import { createRun, makeCourse, command, step, seeded } from "./rules.js";
const $ = (s) => document.querySelector(s),
  canvas = $("#world"),
  intro = $("#intro"),
  startButton = $("#start");
const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
let renderer;
try {
  renderer = new THREE.WebGLRenderer({
    canvas,
    antialias: true,
    powerPreference: "high-performance",
  });
} catch {
  $("#load-status").textContent =
    "This game needs WebGL graphics acceleration. Try another game or enable graphics acceleration.";
  throw new Error("WebGL unavailable");
}
renderer.setPixelRatio(Math.min(devicePixelRatio, 1.5));
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.25;
const scene = new THREE.Scene();
scene.background = new THREE.Color("#a9cbd4");
scene.fog = new THREE.Fog("#b5ccd1", 45, 185);
const camera = new THREE.PerspectiveCamera(52, 1, 0.1, 260);
camera.position.set(0, 4.7, 10);
camera.lookAt(0, 0.5, -9);
scene.add(new THREE.HemisphereLight("#fff0d2", "#527f78", 2.6));
const sun = new THREE.DirectionalLight("#fff0cb", 3.2);
sun.position.set(-24, 40, -20);
sun.castShadow = true;
sun.shadow.mapSize.set(1024, 1024);
sun.shadow.camera.left = -15;
sun.shadow.camera.right = 15;
sun.shadow.camera.top = 15;
sun.shadow.camera.bottom = -15;
sun.shadow.camera.far = 100;
sun.shadow.bias = -0.001;
scene.add(sun);
scene.add(sun.target);
const mat = (color, extra = {}) =>
  new THREE.MeshStandardMaterial({ color, roughness: 0.9, ...extra });
const stone = mat("#d8c6a3"),
  edge = mat("#a28e78"),
  grass = mat("#6c946b"),
  leaf = mat("#729b72"),
  trunk = mat("#907b62"),
  dark = mat("#47676b"),
  gold = mat("#f5c36b", { metalness: 0.35, roughness: 0.4 }),
  water = mat("#8bc5ce", { transparent: true, opacity: 0.7 });
const boxGeo = new THREE.BoxGeometry(1, 1, 1),
  rockGeo = new THREE.ConeGeometry(1, 1, 7),
  ballGeo = new THREE.IcosahedronGeometry(1, 1);
function box(parent, m, w, h, d, x, y, z) {
  const o = new THREE.Mesh(boxGeo, m);
  o.scale.set(w, h, d);
  o.position.set(x, y, z);
  o.castShadow = true;
  o.receiveShadow = true;
  parent.add(o);
  return o;
}
function rock(parent, m, w, h, d, x, y, z) {
  const o = new THREE.Mesh(rockGeo, m);
  o.scale.set(w, h, d);
  o.rotation.z = Math.PI;
  o.position.set(x, y, z);
  parent.add(o);
  return o;
}
function tree(parent, x, z, size = 1) {
  box(parent, trunk, 0.3, 1.8 * size, 0.3, x, 1 * size, z);
  for (let j = 0; j < 3; j++) {
    const crown = new THREE.Mesh(ballGeo, leaf);
    crown.position.set(
      x + (j - 1) * 0.45 * size,
      (2.2 + (j === 1 ? 0.5 : 0)) * size,
      z,
    );
    crown.scale.set(1 * size, 1.4 * size, 1 * size);
    crown.castShadow = true;
    parent.add(crown);
  }
}
const track = [];
for (let i = 0; i < 23; i++) {
  const g = new THREE.Group();
  box(g, stone, 8.2, 0.5, 10, 0, -0.25, 0);
  box(g, edge, 0.3, 0.9, 10, -4.2, -0.05, 0);
  box(g, edge, 0.3, 0.9, 10, 4.2, -0.05, 0);
  for (const x of [-1.35, 1.35]) box(g, edge, 0.035, 0.012, 10, x, 0.008, 0);
  for (const z of [-4.8, 0]) box(g, edge, 8.1, 0.015, 0.035, 0, 0.012, z);
  if (i % 3 === 0) {
    for (const x of [-4.3, 4.3]) {
      box(g, stone, 0.5, 1, 0.5, x, 0.5, 0);
      const lamp = new THREE.Mesh(new THREE.OctahedronGeometry(0.25), gold);
      lamp.position.set(x, 1.3, 0);
      g.add(lamp);
    }
  }
  if (i % 5 === 0) {
    box(g, grass, 12, 0.35, 10, 0, -0.8, 0);
    rock(g, edge, 7, 12, 7, 0, -7, 0);
  }
  scene.add(g);
  track.push(g);
}
const random = seeded(610),
  islands = [];
for (let i = 0; i < 16; i++) {
  const g = new THREE.Group(),
    side = i % 2 === 0 ? -1 : 1,
    w = 5 + random() * 6;
  g.userData.x = side * (13 + random() * 24);
  g.userData.y = -4 + random() * 7;
  g.userData.z = -i * 17;
  box(g, grass, w, 0.65, w, 0, 0, 0);
  rock(g, stone, w * 0.65, 13 + random() * 12, w * 0.65, 0, -8, 0);
  tree(g, -w * 0.2, 0, 1.4);
  tree(g, w * 0.3, -w * 0.2, 0.85);
  if (i % 3 === 0) {
    box(g, stone, 2.6, 6, 2.6, 0, 3, 2);
    box(g, edge, 3.2, 0.4, 3.2, 0, 6, 2);
    box(g, dark, 0.85, 1.8, 0.05, 0, 4.7, 3.34);
    const roof = new THREE.Mesh(new THREE.ConeGeometry(2.3, 2, 4), gold);
    roof.position.set(0, 7.1, 2);
    roof.rotation.y = Math.PI / 4;
    g.add(roof);
  }
  if (i % 4 === 0) box(g, water, 0.7, 18, 2, w * 0.4, -9, 0);
  scene.add(g);
  islands.push(g);
}
const arch = [],
  archGeometry = new THREE.TorusGeometry(5.2, 0.45, 6, 28, Math.PI);
for (let i = 0; i < 7; i++) {
  const g = new THREE.Group();
  for (const x of [-5.2, 5.2]) {
    box(g, stone, 1, 4, 1, x, 2, 0);
    box(g, edge, 1.3, 0.4, 1.3, x, 3.8, 0);
  }
  const crown = new THREE.Mesh(archGeometry, stone);
  crown.position.y = 4;
  crown.castShadow = true;
  g.add(crown);
  for (let j = 0; j < 3; j++) {
    const vine = box(
      g,
      grass,
      0.14,
      1.3 + j * 0.6,
      0.14,
      -4.2 + j * 0.4,
      6.2 + j * 0.45,
      0.55,
    );
    vine.rotation.z = 0.1;
  }
  g.userData.z = -i * 38 - 25;
  scene.add(g);
  arch.push(g);
}
const destination = new THREE.Group();
box(destination, grass, 30, 0.5, 25, 0, -0.25, -8);
box(destination, stone, 5, 19, 5, 0, 9, -12);
for (let y = 3; y < 18; y += 4)
  box(destination, dark, 1.4, 2, 0.06, 0, y, -9.47);
box(destination, gold, 6, 0.7, 6, 0, 18.5, -12);
const beacon = new THREE.Mesh(
  new THREE.SphereGeometry(1.3, 16, 12),
  new THREE.MeshBasicMaterial({ color: "#ffe8ac" }),
);
beacon.position.set(0, 21, -12);
destination.add(beacon);
scene.add(destination);
// Transparent sprites are generated locally; no network textures or imported scripts.
const glowCanvas = document.createElement("canvas");
glowCanvas.width = glowCanvas.height = 64;
const gc = glowCanvas.getContext("2d"),
  gr = gc.createRadialGradient(32, 32, 0, 32, 32, 32);
gr.addColorStop(0, "rgba(255,238,171,1)");
gr.addColorStop(0.2, "rgba(255,220,130,.6)");
gr.addColorStop(1, "rgba(255,220,130,0)");
gc.fillStyle = gr;
gc.fillRect(0, 0, 64, 64);
const glowTexture = new THREE.CanvasTexture(glowCanvas),
  glowMaterial = new THREE.SpriteMaterial({
    map: glowTexture,
    transparent: true,
    depthWrite: false,
  });
const halos = [];
for (let i = 0; i < 24; i++) {
  const sprite = new THREE.Sprite(glowMaterial);
  sprite.scale.setScalar(0.35 + random() * 0.35);
  sprite.userData = {
    x: (random() - 0.5) * 28,
    y: 1 + random() * 7,
    z: -random() * 160,
  };
  scene.add(sprite);
  halos.push(sprite);
}
let config,
  course,
  state,
  courier,
  mixer,
  action,
  obstacles = [],
  loaded = false,
  last = performance.now(),
  frames = 0,
  elapsed = 0,
  audioContext,
  masterGain,
  muted = true,
  audio = {},
  toastUntil = 0;
const jumpGeo = new THREE.BoxGeometry(1.7, 1.05, 1.5),
  coinGeo = new THREE.TorusGeometry(0.27, 0.085, 8, 16);
function object(row) {
  const g = new THREE.Group();
  if (row.type === "coin") {
    const m = new THREE.Mesh(coinGeo, gold);
    m.position.y = 1;
    g.add(m);
  } else if (row.type === "jump") {
    const m = new THREE.Mesh(jumpGeo, edge);
    m.position.y = 0.525;
    m.castShadow = true;
    g.add(m);
    box(g, gold, 1.8, 0.12, 1.6, 0, 1.1, 0);
    for (const x of [-0.6, 0.6]) box(g, gold, 0.1, 1.15, 1.6, x, 0.55, 0);
  } else {
    for (const x of [-0.85, 0.85]) box(g, dark, 0.13, 2.5, 0.2, x, 1.25, 0);
    box(g, gold, 1.85, 0.85, 0.4, 0, 1.9, 0);
  }
  g.position.x = (row.lane - 1) * 2.6;
  scene.add(g);
  return g;
}
function resize() {
  renderer.setSize(innerWidth, innerHeight, false);
  camera.aspect = innerWidth / innerHeight;
  camera.updateProjectionMatrix();
}
addEventListener("resize", resize);
resize();
$("#quality").onchange = () => {
  const q = $("#quality").value;
  renderer.setPixelRatio(
    q === "low" ? 1 : Math.min(devicePixelRatio, q === "high" ? 2 : 1.5),
  );
  renderer.shadowMap.enabled = q !== "low";
  halos.forEach((x) => (x.visible = q !== "low"));
  resize();
};
const toast = (text) => {
  $("#toast").textContent = text;
  $("#toast").classList.add("visible");
  toastUntil = performance.now() + 1800;
};
function playSound(name) {
  if (muted || !audioContext || !audio[name]) return;
  const source = audioContext.createBufferSource(),
    gain = audioContext.createGain();
  source.buffer = audio[name];
  gain.gain.value = 0.28;
  source.connect(gain);
  gain.connect(masterGain);
  source.onended = () => {
    source.disconnect();
    gain.disconnect();
  };
  source.start();
}
$("#sound").onclick = async () => {
  try {
    muted = !muted;
    if (!muted) {
      if (!audioContext) {
        audioContext = new AudioContext();
        masterGain = audioContext.createGain();
        masterGain.connect(audioContext.destination);
      }
      await audioContext.resume();
      for (const name of ["collect", "delivery", "wind"])
        if (!audio[name])
          audio[name] = await audioContext.decodeAudioData(
            await (await fetch(`./assets/${name}.wav`)).arrayBuffer(),
          );
      playSound("wind");
    }
    if (masterGain) masterGain.gain.setTargetAtTime(muted ? 0 : 1, audioContext.currentTime, .02);
    $("#sound").textContent = muted ? "Sound off" : "Sound on";
    $("#sound").ariaLabel = muted ? "Enable sound" : "Mute sound";
  } catch {
    muted = true;
    toast("Sound is unavailable; you can keep playing.");
  }
};
function begin() {
  if (!loaded) return;
  state = createRun(config);
  state.phase = "running";
  courier.position.set(0, 0, 2);
  intro.hidden = true;
  $("#hud").hidden = false;
  document.body.classList.add("playing");
  $("#pause").disabled = false;
  $("#pause").ariaLabel = "Pause game";
  $("#pause").textContent = "Ⅱ";
  action.reset().play();
  toast("The lighthouse is ahead. Your parcel has three chances.");
  canvas.focus();
}
function finish() {
  intro.hidden = false;
  document.body.classList.remove("playing");
  $("#pause").disabled = true;
  $("#eyebrow").textContent =
    state.phase === "won"
      ? "DELIVERY COMPLETE"
      : "EVERY JOURNEY TAKES PRACTICE";
  $("#title").innerHTML =
    state.phase === "won"
      ? "You brought<br><em>the light home.</em>"
      : "A little stumble.<br><em>Another adventure.</em>";
  $("#description").textContent =
    `${Math.floor(state.distance)} meters traveled · ${state.coins} lights collected. ${state.phase === "won" ? "The lighthouse shines because of you." : "Keep an eye out: crates need a jump, arches need a slide."}`;
  startButton.textContent = "Run again →";
  if (state.phase === "won") playSound("delivery");
}
function pause() {
  if (state?.phase === "running") {
    state.phase = "paused";
    intro.hidden = false;
    $("#eyebrow").textContent = "TAKE A BREATH";
    $("#title").innerHTML = "The skies<br><em>can wait.</em>";
    $("#description").textContent = "Your delivery is right where you left it.";
    startButton.textContent = "Continue delivery →";
    $("#pause").textContent = "▶";
    $("#pause").ariaLabel = "Resume game";
  } else if (state?.phase === "paused") {
    state.phase = "running";
    intro.hidden = true;
    $("#pause").textContent = "Ⅱ";
    $("#pause").ariaLabel = "Pause game";
  }
}
startButton.onclick = () => (state?.phase === "paused" ? pause() : begin());
$("#pause").onclick = pause;
const keys = {
  ArrowLeft: "left",
  a: "left",
  A: "left",
  ArrowRight: "right",
  d: "right",
  D: "right",
  ArrowUp: "jump",
  w: "jump",
  W: "jump",
  " ": "jump",
  ArrowDown: "slide",
  s: "slide",
  S: "slide",
};
addEventListener("keydown", (e) => {
  if (e.target.matches("select,input,textarea")) return;
  if (e.key === "p" || e.key === "P" || e.key === "Escape") {
    e.preventDefault();
    pause();
  }
  if (keys[e.key] && state?.phase === "running") {
    e.preventDefault();
    if (!e.repeat) command(state, keys[e.key], config);
  }
});
for (const b of document.querySelectorAll("[data-action]"))
  b.onclick = () => state && command(state, b.dataset.action, config);
let touchStart;
canvas.addEventListener("pointerdown", (e) => {
  touchStart = [e.clientX, e.clientY];
});
canvas.addEventListener("pointerup", (e) => {
  if (!touchStart || !state) return;
  const dx = e.clientX - touchStart[0],
    dy = e.clientY - touchStart[1];
  touchStart = null;
  if (Math.max(Math.abs(dx), Math.abs(dy)) > 20)
    command(
      state,
      Math.abs(dx) > Math.abs(dy)
        ? dx > 0
          ? "right"
          : "left"
        : dy < 0
          ? "jump"
          : "slide",
      config,
    );
});
addEventListener("blur", () => {
  if (state?.phase === "running") pause();
});
document.addEventListener("visibilitychange", () => {
  if (document.hidden && state?.phase === "running") pause();
});
try {
  config = await (await fetch("./config.json")).json();
  stone.color.set(config.colors.stone);
  grass.color.set(config.colors.grass);
  water.color.set(config.colors.water);
  gold.color.set(config.colors.lantern);
  course = makeCourse(config);
  state = createRun(config);
  obstacles = course.map(object);
  const [sky, model] = await Promise.all([
    new THREE.TextureLoader().loadAsync("./assets/sky.png"),
    new GLTFLoader().loadAsync("./assets/courier.glb"),
  ]);
  sky.colorSpace = THREE.SRGBColorSpace;
  scene.background = sky;
  courier = model.scene;
  courier.position.set(0, 0, 2);
  courier.traverse((o) => {
    if (o.isMesh) {
      o.castShadow = true;
      o.receiveShadow = true;
    }
  });
  scene.add(courier);
  mixer = new THREE.AnimationMixer(courier);
  action = mixer.clipAction(model.animations[0]);
  action.play();
  loaded = true;
  startButton.disabled = false;
  startButton.textContent = "Begin delivery →";
  $("#load-status").textContent =
    config.distance.toLocaleString() +
    " meters · " +
    config.lives +
    " chances · A world of your own";
} catch (e) {
  $("#load-status").textContent =
    "A local game asset could not load. Restart the game or restore its original version.";
  console.error(e);
}
// Read-only telemetry used for frame/performance and gameplay qualification.
window.skyboundStatus = () => ({
  phase: state?.phase,
  distance: state?.distance,
  coins: state?.coins,
  lives: state?.lives,
  lane: state?.lane,
  jump: state?.jump,
  slide: state?.slide,
  loaded,
  frames,
  calls: renderer.info.render.calls,
  geometries: renderer.info.memory.geometries,
  textures: renderer.info.memory.textures,
  upcoming: course?.filter(
    (o) =>
      o.type !== "coin" && o.z > state.distance && o.z < state.distance + 35,
  ),
});
function frame(now) {
  const dt = Math.min(0.05, (now - last) / 1000);
  last = now;
  elapsed += dt;
  frames++;
  if (loaded) {
    const old = state.phase;
    for (const event of step(state, dt, config, course)) {
      if (event === "coin") playSound("collect");
      if (event === "hit") toast("Hold on to your parcel! Find an open lane.");
    }
    if (old === "running" && ["won", "lost"].includes(state.phase)) finish();
    const distance = state.distance,
      animated = state.phase === "running";
    track.forEach((g, i) => (g.position.z = 12 - i * 10 + (distance % 10)));
    islands.forEach((g) => {
      g.position.set(
        g.userData.x,
        g.userData.y,
        g.userData.z + (distance % 272),
      );
      if (g.position.z > 24) g.position.z -= 272;
    });
    arch.forEach((g) => {
      g.position.z = g.userData.z + (distance % 266);
      if (g.position.z > 18) g.position.z -= 266;
    });
    destination.position.z = -(config.distance - distance) + 2;
    course.forEach((o, i) => {
      const g = obstacles[i];
      g.position.z = 2 - (o.z - distance);
      g.visible =
        g.position.z > -160 && g.position.z < 8 && !state.visited.has(i);
      if (o.type === "coin" && !reduced) g.rotation.y = elapsed * 1.2;
    });
    for (const s of halos) {
      s.position.set(
        s.userData.x,
        s.userData.y + (reduced ? 0 : Math.sin(elapsed + s.userData.z) * 0.25),
        (s.userData.z + distance) % 160,
      );
      if (s.position.z > 15) s.position.z -= 160;
    }
    courier.position.x +=
      ((state.lane - 1) * 2.6 - courier.position.x) * Math.min(1, dt * 16);
    courier.position.y =
      state.jump > 0
        ? Math.sin((state.jump / config.jumpSeconds) * Math.PI) * 2
        : 0;
    courier.scale.y = state.slide > 0 ? 0.48 : 1;
    courier.rotation.z = state.slide > 0 ? 0.08 : 0;
    courier.visible =
      state.invincible <= 0 || reduced || Math.floor(elapsed * 12) % 2 === 0;
    if (animated || state.phase === "ready")
      mixer.update(dt * (animated ? 1.35 : 0.55));
    if (!reduced) {
      camera.position.x +=
        (courier.position.x * 0.18 - camera.position.x) * dt * 3;
      camera.position.y +=
        (4.7 +
          (animated ? Math.sin(elapsed * 5) * 0.025 : 0) -
          camera.position.y) *
        dt *
        4;
    }
    camera.lookAt(camera.position.x * 0.3, 0.5, -9);
    $("#distance").textContent =
      Math.min(config.distance, Math.floor(distance)) + " m";
    $("#coins").textContent = state.coins;
    $("#lives").textContent = "◆ ".repeat(Math.max(0, state.lives));
    $("#progress").style.width =
      Math.min(100, (distance / config.distance) * 100) + "%";
    $("#chapter").textContent =
      distance > 850
        ? "THE LIGHTHOUSE"
        : distance > 400
          ? "THE SUNLIT ARCHES"
          : "THE GARDEN BRIDGES";
  }
  if (now > toastUntil) $("#toast").classList.remove("visible");
  renderer.render(scene, camera);
  requestAnimationFrame(frame);
}
requestAnimationFrame(frame);
