import * as THREE from "./vendor/three.module.js";
import { loadVehicle } from "./vehicle.js";
import {
  surface,
  roundedBody,
  palmFrond,
  islandGeometry,
  reflectionEnvironment,
} from "./art.js";
import { loadProgress, saveLap, clearLap, finishLap } from "./progress.js";
import {
  buildTrack,
  createCar,
  stepCar,
  atDistance,
  nearest,
  angleDelta,
  clamp,
  resetToRoad,
} from "./physics.js";
const $ = (s) => document.querySelector(s),
  canvas = $("#world"),
  menu = $("#menu"),
  reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
let renderer;
try {
  renderer = new THREE.WebGLRenderer({
    canvas,
    antialias: true,
    powerPreference: "high-performance",
  });
} catch {
  $("#description").textContent =
    "This game needs WebGL graphics acceleration.";
  throw new Error("WebGL unavailable");
}
renderer.setPixelRatio(Math.min(devicePixelRatio, 1.5));
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
const scene = new THREE.Scene(),
  camera = new THREE.PerspectiveCamera(55, 1, 0.1, 1600),
  world = new THREE.Group();
scene.add(world);
const hemi = new THREE.HemisphereLight("#bacbff", "#182b36", 2);
scene.add(hemi);
const sun = new THREE.DirectionalLight("#b7caff", 2);
sun.position.set(-100, 160, -100);
sun.castShadow = true;
sun.shadow.mapSize.set(1024, 1024);
sun.shadow.camera.left = -35;
sun.shadow.camera.right = 35;
sun.shadow.camera.top = 35;
sun.shadow.camera.bottom = -35;
sun.shadow.camera.far = 400;
sun.shadow.bias = -0.001;
scene.add(sun);
scene.add(sun.target);
const boxGeo = new THREE.BoxGeometry(1, 1, 1),
  cylinderGeo = new THREE.CylinderGeometry(1, 1, 1, 12),
  leafGeo = new THREE.SphereGeometry(1, 8, 5);
const material = (color, extra = {}) =>
  new THREE.MeshStandardMaterial({ color, roughness: 0.8, ...extra });
function box(parent, mat, x, y, z, w, h, d) {
  const m = new THREE.Mesh(boxGeo, mat);
  m.position.set(x, y, z);
  m.scale.set(w, h, d);
  m.castShadow = m.receiveShadow = true;
  parent.add(m);
  return m;
}
function cylinder(parent, mat, x, y, z, r, h) {
  const m = new THREE.Mesh(cylinderGeo, mat);
  m.position.set(x, y, z);
  m.scale.set(r, h, r);
  m.castShadow = true;
  parent.add(m);
  return m;
}
const car = new THREE.Group();
scene.add(car);
const body = new THREE.Group();
car.add(body);
const paint = material("#f39464", {
    metalness: 0.25,
    roughness: 0.3,
    emissive: "#5a2413",
    emissiveIntensity: 0.16,
  }),
  rubber = material("#15222b"),
  glass = new THREE.MeshPhysicalMaterial({
    color: "#233b4b",
    metalness: 0.28,
    roughness: 0.08,
    clearcoat: 1,
    clearcoatRoughness: 0.06,
    envMapIntensity: 1.5,
  }),
  chrome = material("#d1dbcf", { metalness: 0.7, roughness: 0.25 });
const lowerBody = new THREE.Mesh(roundedBody(1.65, 0.46, 3.7, 0.1), paint);
lowerBody.position.y = 0.55;
lowerBody.castShadow = lowerBody.receiveShadow = true;
body.add(lowerBody);
const upperBody = new THREE.Mesh(roundedBody(1.49, 0.29, 2.8, 0.08), paint);
upperBody.position.set(0, 0.88, 0.15);
upperBody.castShadow = true;
body.add(upperBody);
// Sloping coupe glass, rather than a rectangular cabin.
const cabinGeo = new THREE.BufferGeometry();
cabinGeo.setAttribute(
  "position",
  new THREE.Float32BufferAttribute(
    [
      -0.69, 0.94, -0.65, 0.69, 0.94, -0.65, 0.69, 0.94, 1.2, -0.69, 0.94, 1.2,
      -0.6, 1.48, -0.18, 0.6, 1.48, -0.18, 0.6, 1.48, 0.78, -0.6, 1.48, 0.78,
    ],
    3,
  ),
);
cabinGeo.setIndex([
  0, 5, 1, 0, 4, 5, 1, 6, 2, 1, 5, 6, 2, 7, 3, 2, 6, 7, 3, 4, 0, 3, 7, 4, 4, 6,
  5, 4, 7, 6,
]);
cabinGeo.computeVertexNormals();
body.add(new THREE.Mesh(cabinGeo, glass));
box(body, paint, 0, 1.49, 0.3, 1.25, 0.1, 1.02);
box(body, chrome, -0.36, 0.961, -1.12, 0.12, 0.022, 1.2);
box(body, chrome, 0.36, 0.961, -1.12, 0.12, 0.022, 1.2);
box(body, rubber, 0, 0.51, 1.91, 0.52, 0.19, 0.04);
box(body, chrome, 0, 0.66, -1.87, 1.5, 0.13, 0.12);
box(body, chrome, 0, 0.62, 1.87, 1.6, 0.12, 0.12);
// Rear details carry the silhouette in the chase camera: diffuser, wing and lamps.
box(body, rubber, 0, 0.35, 1.84, 1.5, 0.16, 0.25);
for (const x of [-0.55, 0.55]) {
  box(body, paint, x, 0.98, 1.55, 0.07, 0.25, 0.12);
  cylinder(body, chrome, x, 0.32, 1.89, 0.07, 0.18).rotation.x = Math.PI / 2;
}
const wing = new THREE.Mesh(roundedBody(1.7, 0.07, 0.3, 0.03), paint);
wing.position.set(0, 1.1, 1.55);
wing.castShadow = true;
body.add(wing);
for (const x of [-0.78, 0.78])
  box(body, rubber, x, 0.82, -0.6, 0.025, 0.035, 0.7);
box(body, chrome, 0, 1.11, -1.1, 0.16, 0.03, 1.2);
const lampMat = new THREE.MeshBasicMaterial({ color: "#fff4c9" }),
  tailMat = new THREE.MeshBasicMaterial({ color: "#bd304c" });
for (const x of [-0.57, 0.57]) {
  box(body, lampMat, x, 0.73, -1.88, 0.42, 0.21, 0.05);
  box(body, tailMat, x, 0.71, 1.88, 0.4, 0.18, 0.05);
}
const wheels = [];
for (const x of [-0.88, 0.88])
  for (const z of [-1.1, 1.13]) {
    const hub = new THREE.Group();
    hub.position.set(x, 0.38, z);
    body.add(hub);
    const tire = new THREE.Mesh(
      new THREE.CylinderGeometry(0.36, 0.36, 0.24, 32),
      rubber,
    );
    tire.rotation.z = Math.PI / 2;
    hub.add(tire);
    const rim = new THREE.Mesh(
      new THREE.CylinderGeometry(0.24, 0.24, 0.25, 24),
      rubber,
    );
    rim.rotation.z = Math.PI / 2;
    hub.add(rim);
    const face = new THREE.Group();
    hub.add(face);
    const rimRing = new THREE.Mesh(
      new THREE.TorusGeometry(0.25, 0.025, 8, 24),
      chrome,
    );
    rimRing.rotation.y = Math.PI / 2;
    rimRing.position.x = Math.sign(x) * 0.14;
    face.add(rimRing);
    for (let i = 0; i < 5; i++) {
      const spoke = box(
        face,
        chrome,
        Math.sign(x) * 0.145,
        Math.cos((i * Math.PI * 2) / 5) * 0.1,
        Math.sin((i * Math.PI * 2) / 5) * 0.1,
        0.025,
        0.27,
        0.045,
      );
      spoke.rotation.x = (i * Math.PI * 2) / 5;
    }
    wheels.push({ hub, tire, rim, face, front: z < 0 });
  }
const fallbackParts = [...body.children];
let detailedVehicle = null,
  vehicleLoad = "loading";
function applyVehicleQuality() {
  const detailed = detailedVehicle && $("#quality").value !== "low";
  for (const part of fallbackParts) part.visible = !detailed;
  if (detailedVehicle) detailedVehicle.root.visible = !!detailed;
}
void loadVehicle()
  .then((vehicle) => {
    detailedVehicle = vehicle;
    body.add(vehicle.root);
    vehicleLoad = "ready";
    applyVehicleQuality();
  })
  .catch(() => {
    vehicleLoad = "fallback";
    $("#vehicle-status").textContent =
      "Detailed car unavailable · driving the classic coupe";
  });
const beams = [];
for (const x of [-0.62, 0.62]) {
  const light = new THREE.SpotLight("#cff9ff", 100, 45, 0.46, 0.65, 1.4);
  light.position.set(x, 0.8, -1.8);
  const target = new THREE.Object3D();
  target.position.set(x, 0.1, -23);
  car.add(target, light);
  light.target = target;
  beams.push(light);
}
const input = { throttle: 0, brake: 0, steer: 0, drift: false },
  keys = new Set(),
  touch = new Set();
let savedLap = null,
  progressGeneration = 0,
  lastSaveTime = 0,
  saving = false,
  starting = false;
let resultRecord = null,
  resultSaveError = false,
  resultGeneration = 0;
function targets(laps = track.laps) {
  $("#targets").textContent =
    `${laps} laps · Gold ${formatTime(definition.gold * laps)} · Silver ${formatTime(definition.silver * laps)} · Bronze: finish`;
  $("#course-length").textContent =
    `${Math.round(track.length)} m circuit · ${laps} laps`;
}
function saveError(error) {
  $("#save-status").textContent =
    error?.message || "Progress could not be saved.";
}
function persistLap() {
  if (!globalThis.arcadeSave) return;
  if (!loaded || !["racing", "paused"].includes(state.phase) || saving) return;
  saving = true;
  const generation = progressGeneration;
  void saveLap(definition, state)
    .then(() => {
      if (generation === progressGeneration)
        $("#save-status").textContent = "Progress saved on this device";
    })
    .catch(saveError)
    .finally(() => (saving = false));
}
async function refreshProgress() {
  let incompatible = false;
  const generation = ++progressGeneration;
  savedLap = null;
  $("#continue").hidden = true;
  $("#best").textContent = "";
  $("#save-status").textContent = "";
  $("#start").disabled = true;
  try {
    const result = await loadProgress(definition, track);
    if (generation !== progressGeneration) return;
    savedLap = result.lap;
    $("#continue").hidden = !savedLap;
    $("#continue").textContent = savedLap
      ? `Continue saved ${savedLap.raceLaps}-lap race →`
      : "Continue saved race →";
    if (result.best !== null)
      $("#best").textContent =
        `${track.laps}-lap personal best · ${formatTime(result.best)}`;
  } catch (error) {
    incompatible = true;
    if (generation === progressGeneration) saveError(error);
  } finally {
    if (generation === progressGeneration) {
      $("#start").disabled = false;
      $("#start").textContent =
        savedLap || incompatible ? "New race →" : "Start race →";
    }
  }
}
let definitions = {},
  definition,
  track,
  state,
  courseId = "neon",
  loaded = false,
  countdown = 0,
  last = performance.now(),
  elapsed = 0,
  toastTime = 0,
  muted = true,
  audio,
  engine,
  engineGain,
  skidGain,
  master,
  frames = 0,
  waterMaterial,
  gateVisuals = [],
  marks = [],
  markIndex = 0,
  lastMark = 0,
  showcase = 0;
let environmentTarget,
  landmarkWheel,
  smoke = [],
  smokeIndex = 0;
const glowCanvas = document.createElement("canvas");
glowCanvas.width = glowCanvas.height = 64;
const ctx = glowCanvas.getContext("2d"),
  gradient = ctx.createRadialGradient(32, 32, 0, 32, 32, 32);
gradient.addColorStop(0, "#fff");
gradient.addColorStop(0.18, "#ffffffb0");
gradient.addColorStop(1, "#ffffff00");
ctx.fillStyle = gradient;
ctx.fillRect(0, 0, 64, 64);
const glowTexture = new THREE.CanvasTexture(glowCanvas);
function glow(parent, color, x, y, z, size = 3) {
  const m = new THREE.SpriteMaterial({
    map: glowTexture,
    color,
    transparent: true,
    depthWrite: false,
    opacity: 0.65,
  });
  const s = new THREE.Sprite(m);
  s.position.set(x, y, z);
  s.scale.setScalar(size);
  parent.add(s);
  return s;
}
function strip(track, width, yOffset, mat) {
  const vertices = [],
    normals = [],
    uvs = [],
    indices = [];
  for (let i = 0; i <= track.points.length; i++) {
    const p = track.points[i % track.points.length],
      b = track.points[(i + 1) % track.points.length],
      yaw = Math.atan2(b.x - p.x, -(b.z - p.z));
    for (const side of [-1, 1]) {
      vertices.push(
        p.x +
          Math.cos(yaw) *
            (width === 44
              ? 22 +
                2 * Math.sin((i / track.points.length) * Math.PI * 12) +
                0.8 * Math.sin((i / track.points.length) * Math.PI * 26)
              : width * 0.5) *
            side,
        p.y + yOffset,
        p.z +
          Math.sin(yaw) *
            (width === 44
              ? 22 +
                2 * Math.sin((i / track.points.length) * Math.PI * 12) +
                0.8 * Math.sin((i / track.points.length) * Math.PI * 26)
              : width * 0.5) *
            side,
      );
      normals.push(0, 1, 0);
      uvs.push(side === -1 ? 0 : 1, i / 8);
    }
    if (i < track.points.length) {
      const k = i * 2;
      indices.push(k, k + 1, k + 2, k + 1, k + 3, k + 2);
    }
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.Float32BufferAttribute(vertices, 3));
  g.setAttribute("normal", new THREE.Float32BufferAttribute(normals, 3));
  g.setAttribute("uv", new THREE.Float32BufferAttribute(uvs, 2));
  g.setIndex(indices);
  g.computeVertexNormals();
  const m = new THREE.Mesh(g, mat);
  m.receiveShadow = true;
  world.add(m);
  return m;
}
function roadEdge(side) {
  const verts = [],
    indices = [];
  track.points.forEach((p, i) => {
    const b = track.points[(i + 1) % track.points.length],
      yaw = Math.atan2(b.x - p.x, -(b.z - p.z)),
      edge =
        22 +
        2 * Math.sin((i / track.points.length) * Math.PI * 12) +
        0.8 * Math.sin((i / track.points.length) * Math.PI * 26),
      x = p.x + Math.cos(yaw) * edge * side,
      z = p.z + Math.sin(yaw) * edge * side;
    verts.push(x, p.y - 0.3, z, x, -5, z);
    const k = i * 2,
      n = ((i + 1) % track.points.length) * 2;
    indices.push(k, n, k + 1, k + 1, n, n + 1);
  });
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.Float32BufferAttribute(verts, 3));
  geo.setIndex(indices);
  geo.computeVertexNormals();
  world.add(
    new THREE.Mesh(
      geo,
      material(definition.palette.land, { side: THREE.DoubleSide }),
    ),
  );
}
function sign(text, color) {
  const c = document.createElement("canvas");
  c.width = 256;
  c.height = 64;
  const a = c.getContext("2d");
  a.fillStyle = "#0c1727";
  a.fillRect(0, 0, 256, 64);
  a.strokeStyle = color;
  a.lineWidth = 4;
  a.strokeRect(3, 3, 250, 58);
  a.fillStyle = color;
  a.font = "600 27px sans-serif";
  a.textAlign = "center";
  a.textBaseline = "middle";
  a.fillText(text, 128, 33);
  const texture = new THREE.CanvasTexture(c);
  texture.colorSpace = THREE.SRGBColorSpace;
  return new THREE.MeshBasicMaterial({ map: texture });
}
function palm(g, x, z, scale) {
  const bark = material("#706656"),
    leaves = material(courseId === "neon" ? "#386271" : "#698557");
  cylinder(g, bark, x, 2.8 * scale, z, 0.19 * scale, 5.6 * scale);
  {
    leaves.side = THREE.DoubleSide;
    const geo = palmFrond();
    for (let i = 0; i < 9; i++) {
      const l = new THREE.Mesh(geo, leaves);
      l.scale.setScalar(scale);
      l.rotation.y = (i * Math.PI * 2) / 9;
      l.rotation.z = (i % 3) * 0.07;
      l.position.set(x, 5.6 * scale, z);
      l.castShadow = true;
      g.add(l);
    }
  }
}
function disposeWorld() {
  scene.environment = null;
  environmentTarget?.dispose();
  environmentTarget = null;
  landmarkWheel = null;
  smoke = [];
  const geometries = new Set(),
    materials = new Set(),
    textures = new Set();
  world.traverse((o) => {
    if (o.isMesh || o.isSprite || o.isPoints) {
      if (o.isInstancedMesh) o.dispose();
      if (
        o.geometry &&
        o.geometry !== boxGeo &&
        o.geometry !== cylinderGeo &&
        o.geometry !== leafGeo
      )
        geometries.add(o.geometry);
      for (const m of Array.isArray(o.material) ? o.material : [o.material]) {
        if (![paint, rubber, glass, chrome, lampMat, tailMat].includes(m))
          materials.add(m);
        if (m.map && m.map !== glowTexture) textures.add(m.map);
      }
    }
  });
  for (const g of geometries) g.dispose();
  for (const m of materials) m.dispose();
  for (const t of textures) t.dispose();
  world.clear();
  marks = [];
  gateVisuals = [];
  markIndex = 0;
}
// Batch repeated scenery geometry; the car and moving skid marks stay separate.
function batchScenery() {
  world.updateMatrixWorld(true);
  const batches = new Map();
  world.traverse((o) => {
    if (
      !o.isMesh ||
      (![boxGeo, cylinderGeo, leafGeo].includes(o.geometry) &&
        !o.geometry?.userData.staticScenery)
    )
      return;
    const key = `${o.geometry.uuid}:${o.material.uuid}`;
    if (!batches.has(key)) batches.set(key, []);
    batches.get(key).push(o);
  });
  for (const items of batches.values()) {
    if (items.length < 2) continue;
    const mesh = new THREE.InstancedMesh(
      items[0].geometry,
      items[0].material,
      items.length,
    );
    mesh.castShadow = mesh.receiveShadow = true;
    items.forEach((o, i) => {
      mesh.setMatrixAt(i, o.matrixWorld);
      o.removeFromParent();
    });
    mesh.computeBoundingSphere();
    world.add(mesh);
  }
}
function horizon(night) {
  const sky = new THREE.Mesh(
    new THREE.SphereGeometry(1200, 32, 16),
    new THREE.ShaderMaterial({
      side: THREE.BackSide,
      depthWrite: false,
      uniforms: {
        top: { value: new THREE.Color(night ? "#060e27" : "#668fbc") },
        bottom: { value: new THREE.Color(night ? "#29465a" : "#f3bc96") },
      },
      vertexShader:
        "varying vec3 v;void main(){v=position;gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.);}",
      fragmentShader:
        "varying vec3 v;uniform vec3 top,bottom;void main(){float h=clamp(normalize(v).y*2.,0.,1.);gl_FragColor=vec4(mix(bottom,top,h),1.);}",
    }),
  );
  world.add(sky);
  if (night) {
    const vertices = [];
    for (let i = 0; i < 260; i++) {
      const theta = i * 2.399963,
        height = 100 + ((i * 73) % 650),
        r = Math.sqrt(1000 * 1000 - height * height);
      vertices.push(Math.cos(theta) * r, height, Math.sin(theta) * r);
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute(vertices, 3));
    world.add(
      new THREE.Points(
        g,
        new THREE.PointsMaterial({
          color: "#cbd8f2",
          size: 1.4,
          sizeAttenuation: true,
          fog: false,
        }),
      ),
    );
  }
  // Distant islands give both coastlines a horizon without adding asset downloads.
  const rock = material(night ? "#283b4d" : "#879a9b");
  for (let i = 0; i < 8; i++) {
    const mountain = new THREE.Mesh(
      islandGeometry(
        i + 3,
        90 + (i % 3) * 30,
        50 + (i % 4) * 19,
        65 + (i % 2) * 30,
      ),
      rock,
    );
    mountain.position.set(-550 + i * 145, -5, -480 - Math.sin(i) * 90);
    world.add(mountain);
  }
}
function buildWorld(id) {
  resultGeneration++;
  resultRecord = null;
  resultSaveError = false;
  $("#results").hidden = true;
  $("#finish-banner").hidden = true;
  menu.classList.remove("results-open");
  disposeWorld();
  courseId = id;
  definition = definitions[id];
  track = buildTrack(definition);
  state = createCar(track);
  $("#race-hud").hidden =
    $("#telemetry").hidden =
    $(".touch").hidden =
    $("#reset").hidden =
      true;
  $("#pause").disabled = true;
  $("#pause").ariaLabel = "Pause race";
  const p = definition.palette,
    night = id === "neon";
  environmentTarget = reflectionEnvironment(renderer, night);
  scene.environment = environmentTarget.texture;
  scene.environmentIntensity = 0.65;
  document.body.classList.toggle("sunset", !night);
  scene.background = new THREE.Color(p.sky);
  scene.fog = new THREE.Fog(p.fog, 150, 700);
  horizon(night);
  renderer.toneMappingExposure = night ? 1.3 : 1.1;
  hemi.color.set(night ? "#97b5e7" : "#fff4dc");
  hemi.groundColor.set(night ? "#122a38" : "#658d90");
  hemi.intensity = night ? 1.7 : 2.3;
  sun.color.set(night ? "#a2c1ed" : "#ffcf95");
  sun.intensity = night ? 1.3 : 2.7;
  beams.forEach((b) => (b.intensity = night ? 110 : 2));
  const asphalt = material(p.road, {
      roughness: night ? 0.32 : 0.82,
      metalness: night ? 0.25 : 0.05,
    }),
    land = material(p.land);
  strip(track, 44, -0.3, land);
  const harborMaps = true
    ? {
        road: surface("asphalt"),
        wall: surface("plaster"),
        paving: surface("paving"),
        roof: surface("roof"),
      }
    : null;
  if (harborMaps) {
    asphalt.map = harborMaps.road;
    asphalt.color.set(night ? "#667482" : "#92918b");
    asphalt.roughness = 0.58;
    asphalt.metalness = 0.08;
    harborMaps.road.repeat.set(2, 2);
    strip(
      track,
      18,
      -0.025,
      material("#899199", { map: harborMaps.paving, roughness: 0.93 }),
    );
  }
  strip(track, definition.width, 0, asphalt);
  roadEdge(-1);
  roadEdge(1);
  // Shader water: bounded two-wave surface coloration; no remote reflection maps.
  waterMaterial = new THREE.ShaderMaterial({
    uniforms: {
      time: { value: 0 },
      base: { value: new THREE.Color(p.sea) },
      shine: { value: new THREE.Color(night ? "#52a7c5" : "#ffce9a") },
    },
    vertexShader:
      "varying vec3 v; void main(){v=position;gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.);}",
    fragmentShader:
      "varying vec3 v; uniform float time; uniform vec3 base,shine; void main(){float wave=sin(v.x*.045+time*.4)*sin(v.y*.067-time*.3);float ripple=pow(max(0.,sin(v.y*.23+sin(v.x*.09+time*.35)*2.+wave)),24.);float reflection=exp(-pow((v.x+200.)/150.,2.));gl_FragColor=vec4(mix(base,shine,.03+ripple*.13+reflection*(.12+.08*wave)),1.);}",
  });
  const sea = new THREE.Mesh(
    new THREE.PlaneGeometry(2200, 2200),
    waterMaterial,
  );
  sea.rotation.x = -Math.PI / 2;
  sea.position.set(80, -5, 0);
  world.add(sea);
  const coastRock = material(night ? "#465967" : "#b6a286");
  for (let s = 10; s < track.length; s += 38) {
    const q = atDistance(track, s),
      side = -1,
      x = q.x + Math.cos(q.yaw) * 24 * side,
      z = q.z + Math.sin(q.yaw) * 24 * side;
    const rock = new THREE.Mesh(islandGeometry(s, 3.5, 2.2, 2.7), coastRock);
    rock.position.set(x, -5, z);
    world.add(rock);
    const foam = new THREE.Mesh(
      new THREE.PlaneGeometry(7, 4),
      new THREE.MeshBasicMaterial({
        map: glowTexture,
        color: night ? "#6d9eaf" : "#e5f1df",
        transparent: true,
        opacity: 0.24,
        depthWrite: false,
      }),
    );
    foam.rotation.x = -Math.PI / 2;
    foam.position.set(x, -4.96, z);
    world.add(foam);
  }
  const curb = material(
      night ? "#8ca9b7" : p.curb,
      night ? { emissive: p.curb, emissiveIntensity: 0.04 } : {},
    ),
    white = material("#e9e5cb"),
    rail = material(night ? "#496270" : "#e9c79c");
  for (let s = 0; s < track.length; s += 7) {
    const q = atDistance(track, s),
      g = new THREE.Group();
    g.position.set(q.x, q.y, q.z);
    g.rotation.y = -q.yaw;
    world.add(g);
    for (const side of [-1, 1]) {
      box(
        g,
        Math.floor(s / 7) % 2 ? curb : white,
        side * (track.width / 2 + 0.1),
        0.08,
        0,
        0.5,
        0.16,
        6.5,
      );
      if (s % 21 < 7) {
        box(g, rail, side * (track.width / 2 + 1.3), 0.55, 0, 0.12, 1.1, 0.12);
        box(g, rail, side * (track.width / 2 + 1.3), 0.95, 0, 0.15, 0.13, 7);
      }
    }
    if (Math.floor(s / 7) % 2 === 0)
      box(g, white, 0, 0.015, 0, 0.12, 0.02, 2.5);
  }
  for (let s = 20, i = 0; s < track.length; s += 29, i++) {
    const q = atDistance(track, s),
      side = i % 3 === 0 ? -1 : 1,
      g = new THREE.Group();
    g.position.set(q.x, q.y, q.z);
    g.rotation.y = -q.yaw;
    world.add(g);
    const detailed = true;
    const wall = material(
        night ? ["#8f9a9f", "#a69d93", "#899ba6"][i % 3] : p.building[i % 3],
        detailed ? { map: harborMaps.wall } : {},
      ),
      roof = material(p.roof, night ? {} : { map: harborMaps.roof }),
      windows = new THREE.MeshBasicMaterial({
        color: night ? (i % 2 ? "#ffbe7b" : "#72d9df") : "#547d89",
      }),
      h = night ? 5 + (i % 4) * 2 : 4 + (i % 3) * 1.8,
      x = side * 16;
    box(g, wall, x, h / 2, 0, 7, h, 8);
    box(g, roof, x, h + 0.25, 0, 7.8, 0.5, 8.8);
    if (detailed) {
      const trim = material("#8a9ca9"),
        dark = material("#152738", { roughness: 0.25, metalness: 0.25 }),
        stone = material("#6f7c84");
      // Storefront glazing, recessed doorway, mullions, cornices and roof services.
      box(g, stone, x - side * 3.54, 0.35, 0, 0.18, 0.7, 8.1);
      box(g, dark, x - side * 3.57, 1.45, 0, 0.04, 2.2, 5.4);
      for (const z of [-2.7, -0.9, 0.9, 2.7])
        box(g, trim, x - side * 3.62, 1.45, z, 0.09, 2.3, 0.07);
      for (const y of [0.35, 2.5, h - 0.25])
        box(g, trim, x - side * 3.65, y, 0, 0.25, 0.12, 8.15);
      box(g, dark, x - side * 3.63, 1.25, 3.25, 0.03, 1.8, 1.0);
      box(g, trim, x - side * 3.7, 1.2, 3.0, 0.05, 0.4, 0.035);
      box(g, stone, x + 1, h + 0.8, 1, 1.6, 0.7, 1.3);
      cylinder(g, trim, x - 1, h + 1.2, -2, 0.15, 2);
      // Warm pools below the awning; inexpensive sprites, not dozens of lights.
      glow(g, "#ffc790", x - side * 4.0, 0.35, 0, 4);
      for (let y = 3.8; y < h; y += 2.2)
        for (const z of [-2.5, 0, 2.5]) {
          box(g, trim, x - side * 3.59, y - 0.65, z, 0.16, 0.12, 1.45);
          box(g, trim, x - side * 3.55, y, z, 0.05, 1.25, 0.06);
        }
      for (const z of [-5.5, 5.5]) {
        cylinder(g, stone, x - side * 4.4, 0.32, z, 0.45, 0.64);
        const shrub = new THREE.Mesh(leafGeo, material("#415f55"));
        shrub.position.set(x - side * 4.4, 0.9, z);
        shrub.scale.set(0.7, 0.8, 0.7);
        g.add(shrub);
      }
    }
    if (!night) {
      const r = new THREE.Mesh(new THREE.ConeGeometry(5.8, 2, 4), roof);
      r.rotation.y = Math.PI / 4;
      r.scale.z = 1.1;
      r.position.set(x, h + 1.3, 0);
      g.add(r);
    }
    for (let y = 1.8; y < h; y += 2.2)
      for (const z of [-2.5, 0, 2.5]) {
        box(g, windows, x - side * 3.51, y, z, 0.025, 1.15, 1.2);
        if (!night) box(g, roof, x - side * 3.65, y - 0.7, z, 0.3, 0.15, 1.5);
      }
    if (night) {
      const label = ["ARCADE", "NOODLES", "LIDO", "HOTEL", "AFTER HOURS"][
          i % 5
        ],
        m = sign(label, i % 2 ? "#ff9dc8" : "#7ff4e2");
      const board = new THREE.Mesh(new THREE.PlaneGeometry(4, 0.95), m);
      board.rotation.y = side < 0 ? Math.PI / 2 : -Math.PI / 2;
      board.position.set(x - side * 3.6, h - 1, 0);
      g.add(board);
      glow(g, i % 2 ? "#ef75ba" : "#75e5d5", x - side * 3.6, h - 1, 0, 6);
      // Shopfront canopy and a vertical neon edge make the town legible at speed.
      box(g, windows, x - side * 3.56, h / 2, -3.7, 0.05, h - 0.6, 0.08);
      box(g, roof, x - side * 4.3, 2.5, 0, 1.6, 0.18, 5.6);
    } else {
      const awning = material(i % 2 ? "#658f86" : "#d99e73");
      box(g, awning, x - side * 4.4, 2.5, 0, 1.8, 0.15, 5.6);
      const petals = material("#c06f93"),
        pot = material("#aa7056");
      for (const z of [-3.5, 3.5]) {
        cylinder(g, pot, x - side * 4.7, 0.3, z, 0.4, 0.6);
        const bloom = new THREE.Mesh(leafGeo, petals);
        bloom.position.set(x - side * 4.7, 0.9, z);
        bloom.scale.set(0.7, 0.65, 0.7);
        g.add(bloom);
      }
    }
    palm(g, -side * 13, 0, night ? 0.85 : 1.1);
    if (i % 3 === 0) {
      cylinder(g, rail, -side * 9, 3, 4, 0.12, 6);
      box(g, windows, -side * 9, 6, 4, 1, 0.15, 0.5);
      if (night) glow(g, "#ffcf91", -side * 9, 6, 4, 5);
    }
  }
  // Open harbor outside the circuit: moored boats, docks and a lighthouse.
  const wood = material(night ? "#3a4c59" : "#a2876d"),
    boat = material("#e0d8c5");
  for (let i = 0; i < 5; i++) {
    const x = -65 - i * 20,
      z = -40 - i * 24;
    box(world, wood, x, -3.7, z, 9, 0.6, 23);
    for (const side of [-1, 1])
      cylinder(world, wood, x + side * 3.5, -2.7, z + 10, 0.25, 4);
    box(world, boat, x - 10, -3.1, z, 6, 1.8, 13);
    box(world, chrome, x - 10, -1.7, z + 1, 4, 1.4, 5);
    cylinder(world, wood, x - 10, 3, z, 0.12, 11);
    const sail = new THREE.Mesh(
      new THREE.PlaneGeometry(5, 7),
      material("#f1e4cc", { side: THREE.DoubleSide }),
    );
    sail.position.set(x - 7.5, 2, z);
    world.add(sail);
  }
  const tower = new THREE.Group();
  tower.position.set(-130, -3, -170);
  world.add(tower);
  if (night) {
    const pier = material("#405467"),
      accent = new THREE.MeshBasicMaterial({ color: "#80d7d3" });
    box(world, pier, -48, -1, -70, 35, 5, 28);
    const wheel = new THREE.Group();
    wheel.position.set(-48, 17, -70);
    world.add(wheel);
    landmarkWheel = wheel;
    wheel.add(new THREE.Mesh(new THREE.TorusGeometry(14, 0.15, 6, 64), accent));
    const spokeGeo = new THREE.BoxGeometry(0.08, 14, 0.08),
      cabGeo = roundedBody(1.5, 1.8, 1.2, 0.12);
    for (let i = 0; i < 12; i++) {
      const angle = (i * Math.PI) / 6,
        spoke = new THREE.Mesh(spokeGeo, accent);
      spoke.position.set(Math.sin(angle) * 7, Math.cos(angle) * 7, 0);
      spoke.rotation.z = -angle;
      wheel.add(spoke);
      const cabin = new THREE.Mesh(
        cabGeo,
        material(i % 2 ? "#bc818c" : "#93bab9"),
      );
      cabin.position.set(Math.sin(angle) * 14, Math.cos(angle) * 14, 0);
      cabin.userData.cabin = true;
      wheel.add(cabin);
    }
    const support = box(world, pier, -48, 7, -70, 1.1, 20, 1.1);
    support.rotation.z = 0.22;
  } else {
    const q = atDistance(track, 290),
      chapel = new THREE.Group();
    chapel.position.set(
      q.x + Math.cos(q.yaw) * 27,
      q.y,
      q.z + Math.sin(q.yaw) * 27,
    );
    chapel.rotation.y = -q.yaw;
    world.add(chapel);
    const stone = material("#eadab8"),
      tile = material("#b96f50", { map: harborMaps.roof }),
      ground = new THREE.Mesh(
        islandGeometry(8, 15, q.y + 5, 14),
        material("#b4a08a"),
      );
    ground.position.set(chapel.position.x, -5, chapel.position.z);
    world.add(ground);
    box(chapel, stone, 0, 3, 0, 7, 6, 10);
    box(chapel, tile, 0, 6, 0, 8, 0.4, 11);
    cylinder(chapel, stone, -4, 5, 2, 1.8, 10);
    const dome = new THREE.Mesh(
      new THREE.SphereGeometry(2, 24, 12, 0, Math.PI * 2, 0, Math.PI / 2),
      tile,
    );
    dome.position.set(-4, 10, 2);
    chapel.add(dome);
    box(chapel, glass, 0, 2, 5.02, 1.5, 3, 0.05);
    box(chapel, chrome, -4, 12, 2, 0.12, 1.8, 0.12);
    box(chapel, chrome, -4, 12.4, 2, 1, 0.12, 0.12);
  }
  cylinder(tower, boat, 0, 12, 0, 5, 24);
  cylinder(tower, rail, 0, 25, 0, 6, 2);
  const beacon = new THREE.Mesh(
    new THREE.SphereGeometry(2, 12, 8),
    new THREE.MeshBasicMaterial({ color: night ? "#8cefe3" : "#ffe4a3" }),
  );
  beacon.position.y = 28;
  tower.add(beacon);
  if (night) glow(tower, "#8cefe3", 0, 28, 0, 30);
  const disk = new THREE.Mesh(
    new THREE.CircleGeometry(night ? 15 : 45, 40),
    new THREE.MeshBasicMaterial({
      color: night ? "#d5e7ef" : "#ffe5ac",
      fog: false,
    }),
  );
  disk.position.set(-210, night ? 190 : 110, -650);
  world.add(disk);
  // Each checkpoint has a side marker; the finish has a full gantry.
  for (let i = 0; i < 12; i++) {
    const q = atDistance(track, track.gates[i]),
      g = new THREE.Group();
    g.position.set(q.x, q.y, q.z);
    g.rotation.y = -q.yaw;
    world.add(g);
    const gm = new THREE.MeshBasicMaterial({ color: p.accent });
    for (const side of [-1, 1])
      box(g, gm, side * (track.width / 2 - 0.5), 1.7, 0, 0.14, 3.4, 0.14);
    gateVisuals.push({ g, gm });
    if (i === 11) {
      box(g, rail, -8, 3.8, 0, 0.4, 7.6, 0.4);
      box(g, rail, 8, 3.8, 0, 0.4, 7.6, 0.4);
      const banner = new THREE.Mesh(
        new THREE.PlaneGeometry(16, 1.7),
        sign("MOONLIGHT RALLY", p.accent),
      );
      banner.position.set(0, 7, 0);
      banner.rotation.y = Math.PI;
      g.add(banner);
      for (let j = 0; j < 14; j++)
        box(g, j % 2 ? rubber : white, -6 + j * 0.92, 0.025, 0, 0.9, 0.03, 1.1);
    }
  }
  const skidMat = new THREE.MeshBasicMaterial({
    color: night ? "#152331" : "#38454b",
    transparent: true,
    opacity: 0.55,
    depthWrite: false,
  });
  for (let i = 0; i < 100; i++) {
    const m = new THREE.Mesh(new THREE.PlaneGeometry(0.15, 0.85), skidMat);
    m.rotation.x = -Math.PI / 2;
    m.visible = false;
    world.add(m);
    marks.push(m);
  }
  batchScenery();
  for (let i = 0; i < 24; i++) {
    const puff = new THREE.Sprite(
      new THREE.SpriteMaterial({
        map: glowTexture,
        color: night ? "#a8b6c0" : "#d6d0c4",
        transparent: true,
        opacity: 0,
        depthWrite: false,
      }),
    );
    puff.visible = false;
    puff.userData.life = 0;
    world.add(puff);
    smoke.push(puff);
  }
  updateMap();
  resetCamera();
  $("#course-name").textContent = definition.name;
  targets();
  for (const b of document.querySelectorAll("[data-course]"))
    b.setAttribute("aria-pressed", String(b.dataset.course === id));
}
function resetCamera() {
  car.position.set(state.x, state.y + 0.08, state.z);
  car.rotation.y = -state.yaw;
  camera.position.set(
    state.x - Math.sin(state.yaw) * 12,
    state.y + 6,
    state.z + Math.cos(state.yaw) * 12,
  );
  camera.lookAt(state.x, state.y + 1, state.z);
}
function updateMap() {
  const c = $("#map"),
    a = c.getContext("2d"),
    xs = track.points.map((p) => p.x),
    zs = track.points.map((p) => p.z),
    minX = Math.min(...xs) - 10,
    maxX = Math.max(...xs) + 10,
    minZ = Math.min(...zs) - 10,
    maxZ = Math.max(...zs) + 10;
  const scale = Math.min(180 / (maxX - minX), 140 / (maxZ - minZ)),
    ox = (200 - (maxX - minX) * scale) / 2,
    oz = (160 - (maxZ - minZ) * scale) / 2;
  const pos = (p) => [ox + (p.x - minX) * scale, oz + (p.z - minZ) * scale];
  a.clearRect(0, 0, 200, 160);
  a.beginPath();
  track.points.forEach((p, i) => {
    const [x, z] = pos(p);
    i ? a.lineTo(x, z) : a.moveTo(x, z);
  });
  a.closePath();
  a.strokeStyle = "#86a8b780";
  a.lineWidth = 6;
  a.stroke();
  a.strokeStyle = definition.palette.accent;
  a.lineWidth = 1;
  a.stroke();
  const [x, z] = pos(state);
  a.fillStyle = "#fff5c9";
  a.beginPath();
  a.arc(x, z, 4, 0, Math.PI * 2);
  a.fill();
}
function toast(text) {
  $("#toast").textContent = text;
  $("#toast").classList.add("visible");
  toastTime = performance.now() + 1600;
}
function readInput() {
  input.throttle =
    keys.has("ArrowUp") ||
    keys.has("w") ||
    keys.has("W") ||
    touch.has("throttle")
      ? 1
      : 0;
  input.brake =
    keys.has("ArrowDown") ||
    keys.has("s") ||
    keys.has("S") ||
    touch.has("brake")
      ? 1
      : 0;
  input.steer =
    (keys.has("ArrowRight") ||
    keys.has("d") ||
    keys.has("D") ||
    touch.has("right")
      ? 1
      : 0) -
    (keys.has("ArrowLeft") ||
    keys.has("a") ||
    keys.has("A") ||
    touch.has("left")
      ? 1
      : 0);
  input.drift = keys.has(" ") || touch.has("drift");
}
function clearInput() {
  keys.clear();
  touch.clear();
  readInput();
}
async function begin() {
  if (starting) return;
  starting = true;
  $("#start").disabled = $("#continue").disabled = true;
  await clearLap(courseId).catch(saveError);
  savedLap = null;
  $("#continue").hidden = true;
  starting = false;
  $("#start").disabled = $("#continue").disabled = false;
  lastSaveTime = performance.now();
  state = createCar(track);
  resultGeneration++;
  resultRecord = null;
  resultSaveError = false;
  $("#results").hidden = true;
  $("#finish-banner").hidden = true;
  menu.classList.remove("results-open");
  targets();
  state.phase = "countdown";
  countdown = 3;
  clearInput();
  marks.forEach((m) => (m.visible = false));
  smoke.forEach((p) => {
    p.visible = false;
    p.userData.life = 0;
  });
  menu.hidden = true;
  $("#race-hud").hidden =
    $("#telemetry").hidden =
    $(".touch").hidden =
    $("#reset").hidden =
      false;
  $("#pause").disabled = false;
  resetCamera();
  toast("Brake before corners. Follow every checkpoint.");
}
function finish() {
  clearInput();
  $("#pause").disabled = true;
  $(".touch").hidden = $("#reset").hidden = true;
  $("#finish-banner").hidden = false;
  $("#finish-banner").textContent = "FINISH";
  const generation = ++resultGeneration;
  void finishLap(courseId, state.time, state.raceLaps)
    .then((result) => {
      if (generation !== resultGeneration) return;
      resultRecord = result;
      $("#best").textContent = `Personal best · ${formatTime(result.best)}`;
      $("#save-status").textContent = globalThis.arcadeSave
        ? "Result saved on this device"
        : "This play session only";
      resultNumbers();
    })
    .catch((error) => {
      if (generation !== resultGeneration) return;
      resultSaveError = true;
      saveError(error);
      resultNumbers();
    });
  tone(784, 0.2);
  setTimeout(() => tone(988, 0.25), 180);
  setTimeout(() => tone(1175, 0.35), 400);
}
function resultNumbers() {
  $("#result-time").textContent = formatTime(state.time);
  $("#result-detail").textContent = resultRecord
    ? resultRecord.previous === null
      ? "Your first finish. The next target is yours."
      : state.time < resultRecord.previous
        ? `New personal best · ${(resultRecord.previous - state.time).toFixed(2)}s faster`
        : `${(state.time - resultRecord.previous).toFixed(2)}s behind your personal best`
    : resultSaveError
      ? "Could not confirm saving this result."
      : "Saving your result…";
  $("#lap-times").textContent = state.lapTimes
    .map((time, i) => `Lap ${i + 1}  ${formatTime(time)}`)
    .join("   ·   ");
}
function showResults() {
  clearInput();
  $("#finish-banner").hidden = true;
  $("#results").hidden = false;
  menu.classList.add("results-open");
  menu.hidden = false;
  $(".touch").hidden = $("#reset").hidden = true;
  $("#pause").disabled = true;
  $(".course-picker").hidden = false;
  const rank =
    state.time <= definition.gold * state.raceLaps
      ? "GOLD LINE"
      : state.time <= definition.silver * state.raceLaps
        ? "SILVER LINE"
        : "BRONZE FINISH";
  $("#eyebrow").textContent = rank;
  $("#heading").innerHTML = "A coast to remember.<br><em>A time to beat.</em>";
  $("#description").textContent =
    `${definition.name} · ${state.raceLaps} laps · ${state.hits} barrier contacts. Find a cleaner line, or take the other coast.`;
  $("#start").textContent = "Race again →";
  $("#continue").hidden = true;
  targets(state.raceLaps);
  resultNumbers();
}
function pause() {
  if (state.phase === "racing" || state.phase === "countdown") {
    state.resumePhase = state.phase;
    state.phase = "paused";
    clearInput();
    menu.hidden = false;
    $(".course-picker").hidden = true;
    $("#eyebrow").textContent = "TAKE A PIT STOP";
    $("#heading").innerHTML = "The coast<br><em>can wait.</em>";
    $("#description").textContent = "Your lap is right where you left it.";
    $("#start").textContent = "Resume race →";
    $("#pause").ariaLabel = "Resume race";
    persistLap();
  } else if (state.phase === "paused") {
    state.phase = state.resumePhase;
    $(".touch").hidden = $("#reset").hidden = false;
    menu.hidden = true;
    $("#pause").ariaLabel = "Pause race";
  }
}
$("#start").onclick = () => (state.phase === "paused" ? pause() : begin());
$("#continue").onclick = () => {
  if (savedLap) {
    state = { ...savedLap };
    targets(state.raceLaps);
    savedLap = null;
    $("#continue").hidden = true;
    $("#race-hud").hidden = $("#telemetry").hidden = false;
    $("#pause").disabled = false;
    $("#pause").ariaLabel = "Resume race";
    $(".course-picker").hidden = true;
    $("#heading").innerHTML = "Welcome back.<br><em>Your race is waiting.</em>";
    $("#description").textContent =
      "Restored safely in pause. Resume when you are ready.";
    $("#start").textContent = "Resume race →";
    resetCamera();
  }
};
$("#pause").onclick = pause;
$("#reset").onclick = () => {
  if (state.phase === "racing") {
    resetToRoad(state, track);
    clearInput();
    toast("Back on track. Your lap timer keeps running.");
  }
};
for (const b of document.querySelectorAll("[data-course]"))
  b.onclick = () => {
    if (starting) return;
    clearInput();
    buildWorld(b.dataset.course);
    $("#eyebrow").textContent = definition.subtitle;
    $("#heading").innerHTML =
      courseId === "neon"
        ? "Find your line.<br><em>Own the night.</em>"
        : "Chase the coast.<br><em>Catch the sunset.</em>";
    $("#description").textContent = definition.description;
    $("#start").textContent = "Start race →";
    void refreshProgress();
  };
addEventListener("keydown", (e) => {
  if (e.target.matches("select,input,textarea")) return;
  if (e.key === "Escape" || e.key === "p" || e.key === "P") {
    e.preventDefault();
    if (!e.repeat && loaded) pause();
    return;
  }
  if (
    [
      "ArrowUp",
      "ArrowDown",
      "ArrowLeft",
      "ArrowRight",
      "w",
      "W",
      "a",
      "A",
      "s",
      "S",
      "d",
      "D",
      " ",
    ].includes(e.key) &&
    ["racing", "countdown"].includes(state?.phase)
  ) {
    e.preventDefault();
    keys.add(e.key);
    readInput();
  }
});
addEventListener("keyup", (e) => {
  keys.delete(e.key);
  readInput();
});
for (const b of document.querySelectorAll("[data-control]")) {
  b.onpointerdown = (e) => {
    b.setPointerCapture(e.pointerId);
    touch.add(b.dataset.control);
    readInput();
  };
  for (const name of ["pointerup", "pointercancel", "lostpointercapture"])
    b.addEventListener(name, () => {
      touch.delete(b.dataset.control);
      readInput();
    });
}
addEventListener("blur", () => {
  clearInput();
  if (loaded && ["racing", "countdown"].includes(state.phase)) pause();
});
document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    clearInput();
    if (loaded && ["racing", "countdown"].includes(state.phase)) pause();
  }
});
function tone(freq, duration = 0.1) {
  if (muted || !audio) return;
  const o = audio.createOscillator(),
    g = audio.createGain();
  o.frequency.value = freq;
  g.gain.setValueAtTime(0.08, audio.currentTime);
  g.gain.exponentialRampToValueAtTime(0.001, audio.currentTime + duration);
  o.connect(g);
  g.connect(master);
  o.start();
  o.stop(audio.currentTime + duration);
  o.onended = () => {
    o.disconnect();
    g.disconnect();
  };
}
$("#sound").onclick = async () => {
  try {
    muted = !muted;
    if (!audio) {
      audio = new AudioContext();
      master = audio.createGain();
      master.connect(audio.destination);
      engine = audio.createOscillator();
      engine.type = "sawtooth";
      const filter = audio.createBiquadFilter();
      filter.type = "lowpass";
      filter.frequency.value = 350;
      engineGain = audio.createGain();
      engineGain.gain.value = 0;
      engine.connect(filter);
      filter.connect(engineGain);
      engineGain.connect(master);
      engine.start();
      const noise = audio.createBuffer(1, audio.sampleRate, audio.sampleRate),
        samples = noise.getChannelData(0);
      let seed = 77;
      for (let i = 0; i < samples.length; i++) {
        seed = (Math.imul(seed, 1664525) + 1013904223) | 0;
        samples[i] = (seed >>> 0) / 2147483648 - 1;
      }
      const source = audio.createBufferSource(),
        band = audio.createBiquadFilter();
      source.buffer = noise;
      source.loop = true;
      band.type = "bandpass";
      band.frequency.value = 1100;
      band.Q.value = 0.7;
      skidGain = audio.createGain();
      skidGain.gain.value = 0;
      source.connect(band);
      band.connect(skidGain);
      skidGain.connect(master);
      source.start();
    }
    await audio.resume();
    master.gain.setTargetAtTime(muted ? 0 : 1, audio.currentTime, 0.03);
    $("#sound").textContent = muted ? "Sound off" : "Sound on";
    $("#sound").ariaLabel = muted ? "Enable sound" : "Mute sound";
  } catch {
    muted = true;
    toast("Sound unavailable. Keep racing.");
  }
};
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
  applyVehicleQuality();
  resize();
};
const formatTime = (t) =>
  `${Math.floor(t / 60)}:${(t % 60).toFixed(2).padStart(5, "0")}`;
try {
  for (const id of ["neon", "sunset"])
    definitions[id] = await (await fetch(`./courses/${id}.json`)).json();
  buildWorld("neon");
  loaded = true;
  $("#start").disabled = false;
  $("#start").textContent = "Start race →";
  void refreshProgress();
} catch (e) {
  $("#description").textContent =
    "A local course could not load. Restore the original game or restart.";
  console.error(e);
}
// Read-only instrumentation for keyboard-driven acceptance and performance checks.
window.rallyStatus = () => ({
  course: courseId,
  phase: state?.phase,
  x: state?.x,
  z: state?.z,
  yaw: state?.yaw,
  speed: state?.speed,
  time: state?.time,
  drift: state?.drift,
  progress: state?.progress,
  checkpoint: state?.checkpoint,
  raceLaps: state?.raceLaps,
  lapTimes: state?.lapTimes,
  coastTime: state?.coastTime,
  hits: state?.hits,
  length: track?.length,
  frames,
  geometries: renderer.info.memory.geometries,
  textures: renderer.info.memory.textures,
  calls: renderer.info.render.calls,
  triangles: renderer.info.render.triangles,
  vehicleLoad,
  vehicle: detailedVehicle?.root.visible ? "retro-coupe" : "classic-coupe",
  wheelAnimation: detailedVehicle?.status() ?? null,
  smokeActive: smoke.filter((p) => p.visible).length,
  skidGain: skidGain?.gain.value ?? 0,
});
function frame(now) {
  const dt = Math.min(0.04, (now - last) / 1000);
  last = now;
  elapsed += dt;
  frames++;
  if (loaded) {
    if (state.phase === "racing" && now - lastSaveTime > 2000) {
      lastSaveTime = now;
      persistLap();
    }
    if (state.phase === "countdown") {
      countdown -= dt;
      $("#countdown").textContent = countdown > 0 ? Math.ceil(countdown) : "";
      if (countdown <= 0) {
        state.phase = "racing";
        tone(660, 0.2);
      }
    } else $("#countdown").textContent = "";
    for (const event of stepCar(state, input, dt, track)) {
      if (event === "hit") {
        toast("Easy on the barriers. Brake, then turn.");
        tone(110, 0.12);
      }
      if (event === "checkpoint") {
        tone(440 + (state.checkpoint % 12) * 20, 0.07);
        if (state.checkpoint % 12)
          toast(`Checkpoint ${state.checkpoint % 12} / 12`);
      }
      if (event === "lap" && state.checkpoint < state.raceLaps * 12) {
        toast(
          `Lap ${state.lapTimes.length} · ${formatTime(state.lapTimes.at(-1))} — ${state.lapTimes.length === state.raceLaps - 1 ? "Final lap!" : "Keep your rhythm"}`,
        );
        tone(880, 0.18);
      }
      if (event === "finish") finish();
      if (event === "results") showResults();
    }
    car.position.set(state.x, state.y + 0.08, state.z);
    car.rotation.y = -state.yaw;
    const visualProgress = state.coastProgress ?? state.progress;
    const ahead = atDistance(track, visualProgress + 2),
      behind = atDistance(track, visualProgress - 2);
    body.rotation.x = THREE.MathUtils.lerp(
      body.rotation.x,
      Math.atan2(ahead.y - behind.y, 4),
      Math.min(1, dt * 8),
    );
    body.rotation.z = THREE.MathUtils.lerp(
      body.rotation.z,
      -input.steer * Math.min(Math.abs(state.speed) / 500, 0.055),
      dt * 7,
    );
    tailMat.color.set(input.brake ? "#ff455e" : "#a12d43");
    if (state.phase !== "paused")
      detailedVehicle?.update(state.speed, input.steer, dt);
    for (const w of wheels) {
      w.hub.rotation.y = w.front ? -input.steer * 0.35 : 0;
      w.tire.rotation.x += (state.speed * dt) / 0.36;
      w.rim.rotation.x += (state.speed * dt) / 0.36;
      w.face.rotation.x += (state.speed * dt) / 0.36;
    }
    if (
      input.drift &&
      Math.abs(state.speed) > 8 &&
      state.phase === "racing" &&
      elapsed - lastMark > 0.04
    ) {
      lastMark = elapsed;
      for (const side of [-1, 1]) {
        const m = marks[markIndex++ % marks.length];
        m.position.set(
          state.x + Math.cos(state.yaw) * side * 0.8,
          state.y + 0.025,
          state.z + Math.sin(state.yaw) * side * 0.8,
        );
        m.rotation.set(-Math.PI / 2, 0, state.yaw);
        m.visible = true;
        if (!reduced && $("#quality").value !== "low" && state.drift > 0.08) {
          const puff = smoke[smokeIndex++ % smoke.length];
          puff.userData.life = 0.8;
          puff.position.set(
            state.x + Math.cos(state.yaw) * side * 0.8 - Math.sin(state.yaw),
            state.y + 0.25,
            state.z + Math.sin(state.yaw) * side * 0.8 + Math.cos(state.yaw),
          );
          puff.visible = true;
        }
      }
    }
    if (state.phase !== "paused")
      for (const puff of smoke) {
        if (puff.userData.life > 0) {
          puff.userData.life = Math.max(0, puff.userData.life - dt);
          puff.position.y += dt * 0.6;
          puff.position.x += dt * 0.3;
          puff.scale.setScalar(0.5 + (1 - puff.userData.life / 0.8) * 1.5);
          puff.material.opacity = puff.userData.life * 0.18;
          puff.visible = puff.userData.life > 0;
        }
      }
    if (landmarkWheel && !reduced) {
      landmarkWheel.rotation.z = elapsed * 0.025;
      for (const child of landmarkWheel.children)
        if (child.userData.cabin) child.rotation.z = -landmarkWheel.rotation.z;
    }
    const isMenu = state.phase === "ready" || state.phase === "finished";
    const offset = isMenu
      ? new THREE.Vector3(
          -Math.sin(state.yaw) * 9 + 7,
          5,
          Math.cos(state.yaw) * 9,
        )
      : new THREE.Vector3(
          -Math.sin(state.yaw) * 10,
          4.4,
          Math.cos(state.yaw) * 10,
        );
    const targetPos = new THREE.Vector3(
      state.x + offset.x,
      state.y + offset.y,
      state.z + offset.z,
    );
    camera.position.lerp(targetPos, Math.min(1, dt * (reduced ? 14 : 6)));
    camera.lookAt(
      state.x + Math.sin(state.yaw) * (isMenu ? 2 : 7),
      state.y + 1,
      state.z - Math.cos(state.yaw) * (isMenu ? 2 : 7),
    );
    sun.position.set(state.x - 60, state.y + 100, state.z - 70);
    sun.target.position.copy(car.position);
    waterMaterial.uniforms.time.value = elapsed;
    $("#time").textContent = formatTime(state.time);
    $("#checkpoints").textContent =
      `${state.checkpoint === state.raceLaps * 12 ? 12 : state.checkpoint % 12} / 12`;
    $("#lap").textContent =
      `${Math.min(state.raceLaps, Math.floor(state.checkpoint / 12) + 1)} / ${state.raceLaps}`;
    $("#speed").textContent = Math.round(Math.abs(state.speed) * 3.6);
    $("#drift-label").textContent =
      state.drift > 0.08 ? "HOLD YOUR LINE" : "FIND YOUR RHYTHM";
    if (frames % 6 === 0) updateMap();
    gateVisuals.forEach((v, i) =>
      v.gm.color.set(
        i <
          (state.checkpoint === state.raceLaps * 12
            ? 12
            : state.checkpoint % 12)
          ? "#4d7b75"
          : definition.palette.accent,
      ),
    );
    if (engine) {
      skidGain.gain.setTargetAtTime(
        state.phase === "racing" ? Math.min(0.035, state.drift * 0.08) : 0,
        audio.currentTime,
        0.09,
      );
      engine.frequency.setTargetAtTime(
        38 + Math.abs(state.speed) * 3.2,
        audio.currentTime,
        0.06,
      );
      engineGain.gain.setTargetAtTime(
        ["racing", "coasting"].includes(state.phase)
          ? 0.012 + Math.abs(state.speed) * 0.0007
          : 0,
        audio.currentTime,
        0.05,
      );
    }
  }
  if (now > toastTime) $("#toast").classList.remove("visible");
  renderer.render(scene, camera);
  requestAnimationFrame(frame);
}
requestAnimationFrame(frame);
