import * as T from "../vendor/three.module.js";
import { GLTFLoader } from "../vendor/addons/loaders/GLTFLoader.js";
import { mergeGeometries } from "../vendor/addons/utils/BufferGeometryUtils.js";
import { PARTS, FIREFLIES, LANTERNS, TREES, HEART, BRIDGE } from "./journey.js";
const mat = (color, extra = {}) =>
  new T.MeshStandardMaterial({ color, roughness: 0.83, ...extra });
// Round each disconnected leaf clump, keeping the curated trunk/branch structure.
function softenCanopy(g) {
  const p = g.attributes.position,
    parents = Array.from({ length: p.count }, (_, i) => i),
    keys = new Map(),
    indices = g.index?.array ?? parents;
  function find(i) {
    while (parents[i] !== i) {
      parents[i] = parents[parents[i]];
      i = parents[i];
    }
    return i;
  }
  const union = (a, b) => (parents[find(a)] = find(b));
  for (let i = 0; i < p.count; i++) {
    const key = [p.getX(i), p.getY(i), p.getZ(i)]
      .map((v) => v.toFixed(4))
      .join(",");
    if (keys.has(key)) union(i, keys.get(key));
    else keys.set(key, i);
  }
  for (let i = 0; i < indices.length; i += 3) {
    union(indices[i], indices[i + 1]);
    union(indices[i], indices[i + 2]);
  }
  const boxes = new Map();
  for (let i = 0; i < p.count; i++) {
    const key = find(i);
    if (!boxes.has(key)) boxes.set(key, new T.Box3());
    boxes
      .get(key)
      .expandByPoint(new T.Vector3(p.getX(i), p.getY(i), p.getZ(i)));
  }
  const pieces = [];
  for (const b of boxes.values()) {
    const size = b.getSize(new T.Vector3()),
      c = b.getCenter(new T.Vector3()),
      s = new T.SphereGeometry(1, 14, 10),
      v = s.attributes.position;
    for (let i = 0; i < v.count; i++) {
      const x = v.getX(i),
        y = v.getY(i),
        z = v.getZ(i),
        k = 1 + 0.07 * Math.sin(x * 5 + y * 3) * Math.cos(z * 5);
      v.setXYZ(i, x * k, y * k, z * k);
    }
    s.computeVertexNormals();
    s.scale(size.x * 0.57, size.y * 0.56, size.z * 0.57);
    s.translate(c.x, c.y, c.z);
    pieces.push(s);
  }
  const result = mergeGeometries(pieces);
  pieces.forEach((p) => p.dispose());
  return result;
}
export async function buildWorld(scene, renderer) {
  const loader = new T.TextureLoader(),
    gltf = new GLTFLoader();
  const [grass, sky, ...models] = await Promise.all([
    loader.loadAsync("./assets/meadow.png"),
    loader.loadAsync("./assets/sky.png"),
    ...[
      "tree_detailed",
      "tree_pineRoundC",
      "mushroom_redGroup",
      "flower_purpleA",
    ].map((n) => gltf.loadAsync(`./assets/${n}.glb`)),
  ]);
  // This older CC0 pack marks leaves/bark as metallic. Use diffuse storybook materials.
  for (const glb of models)
    glb.scene.traverse((o) => {
      if (!o.isMesh) return;
      for (const m of Array.isArray(o.material) ? o.material : [o.material]) {
        m.metalness = 0;
        m.roughness = 0.85;
        if (/leaf/i.test(m.name)) m.color.set("#719557");
        if (/wood|bark/i.test(m.name)) m.color.set("#8e6945");
      }
    });
  models[0].scene.traverse((o) => {
    if (o.isMesh && /leaf/i.test(o.material?.name)) {
      const old = o.geometry;
      o.geometry = softenCanopy(old);
      old.dispose();
    }
  });
  grass.colorSpace = sky.colorSpace = T.SRGBColorSpace;
  grass.wrapS = grass.wrapT = T.RepeatWrapping;
  grass.repeat.set(10, 10);
  grass.anisotropy = Math.min(8, renderer.capabilities.getMaxAnisotropy());
  scene.background = sky;
  scene.fog = new T.Fog("#dfebdc", 42, 135);
  scene.add(new T.HemisphereLight("#fff3d5", "#7a9a8d", 2));
  const sun = new T.DirectionalLight("#fff0ca", 3.2);
  sun.position.set(-25, 45, 22);
  sun.castShadow = true;
  sun.shadow.mapSize.set(2048, 2048);
  Object.assign(sun.shadow.camera, {
    left: -28,
    right: 28,
    top: 28,
    bottom: -28,
    near: 1,
    far: 100,
  });
  sun.shadow.bias = -0.0005;
  sun.shadow.normalBias = 0.05;
  scene.add(sun, sun.target);
  const root = new T.Group();
  scene.add(root);
  const groundMat = mat("#c6d8a4", { map: grass }),
    rockMat = mat("#b5aa8d"),
    wood = mat("#a57648"),
    darkWood = mat("#684b35"),
    stone = mat("#e4d8bc"),
    copper = mat("#e7aa57", { metalness: 0.4, roughness: 0.4 }),
    mint = mat("#8ec9aa");
  function mesh(geo, m, parent = root) {
    const o = new T.Mesh(geo, m);
    o.castShadow = o.receiveShadow = true;
    parent.add(o);
    return o;
  }
  function box(x, y, z, w, h, d, m, parent = root) {
    const o = mesh(new T.BoxGeometry(w, h, d), m, parent);
    o.position.set(x, y, z);
    return o;
  }
  function cylinder(x, y, z, top, bottom, height, m, parent = root) {
    const o = mesh(new T.CylinderGeometry(top, bottom, height, 16), m, parent);
    o.position.set(x, y, z);
    return o;
  }
  function island(x, z, rx, rz) {
    const top = mesh(new T.CircleGeometry(1, 96), groundMat);
    top.rotation.x = -Math.PI / 2;
    top.scale.set(rx, rz, 1);
    top.position.set(x, 0, z);
    const g = new T.CylinderGeometry(1, 0.68, 7, 64, 4),
      p = g.attributes.position;
    for (let i = 0; i < p.count; i++) {
      const a = Math.atan2(p.getZ(i), p.getX(i)),
        h = p.getY(i);
      const noise =
        1 + (h < 3.49 ? 0.035 * Math.sin(a * 7) + 0.02 * Math.cos(a * 13) : 0);
      p.setXYZ(i, p.getX(i) * rx * noise, h, p.getZ(i) * rz * noise);
    }
    g.computeVertexNormals();
    const cliff = mesh(g, rockMat);
    cliff.position.set(x, -3.55, z);
  }
  island(0, 8, 20, 20);
  island(0, -52, 26, 23);
  let pathLayer = 0;
  function path(points, width = 1.6) {
    const y = 0.03 + pathLayer++ * 0.005;
    const curve = new T.CatmullRomCurve3(
        points.map(([x, z]) => new T.Vector3(x, y, z)),
      ),
      pts = curve.getPoints(90),
      vertices = [];
    for (let i = 0; i < pts.length; i++) {
      const t = curve.getTangent(i / (pts.length - 1)),
        nx = (t.z * width) / 2,
        nz = (-t.x * width) / 2;
      vertices.push(
        pts[i].x + nx,
        y,
        pts[i].z + nz,
        pts[i].x - nx,
        y,
        pts[i].z - nz,
      );
    }
    const index = [];
    for (let i = 0; i < pts.length - 1; i++) {
      const a = i * 2;
      index.push(a, a + 1, a + 2, a + 1, a + 3, a + 2);
    }
    const geo = new T.BufferGeometry();
    geo.setAttribute("position", new T.Float32BufferAttribute(vertices, 3));
    geo.setIndex(index);
    geo.computeVertexNormals();
    const ribbon = mesh(geo, mat("#d9c69a"));
    ribbon.castShadow = false;
  }
  path(
    [
      [0, 24],
      [0, 16],
      [-1, 8],
      [2, 1],
      [0, -10],
    ],
    2.3,
  );
  path(
    [
      [0, 16],
      [-7, 13],
      [-12, 12],
    ],
    1.5,
  );
  path(
    [
      [0, 12],
      [6, 10],
      [11, 11],
    ],
    1.5,
  );
  path(
    [
      [0, 2],
      [-5, -1],
      [-8, -3],
    ],
    1.4,
  );
  path(
    [
      [0, -31],
      [-2, -37],
      [-10, -43],
      [-1, -47],
      [9, -51],
      [4, -57],
      [-6, -63],
      [0, -69],
    ],
    2.1,
  );
  // A cottage, porch and fenced beds give the garden a lived-in scale.
  box(-10, 1.6, 22, 4.2, 3.2, 3.5, mat("#f1ddbb"));
  box(-10, 0.13, 19.6, 5.2, 0.26, 1.6, wood);
  const roof = mesh(new T.ConeGeometry(3.5, 2, 4), mat("#bd7357"));
  roof.rotation.y = Math.PI / 4;
  roof.scale.z = 0.9;
  roof.position.set(-10, 4, 22);
  box(-10, 1.15, 20.22, 1.1, 2.25, 0.08, mint);
  for (const x of [-11.5, -8.5]) {
    box(x, 1.8, 20.2, 0.8, 1, 0.08, mat("#778e88"));
    box(x, 1.2, 20, 0.95, 0.2, 0.4, wood);
  }
  cylinder(-7.6, 1.1, 18.5, 0.1, 0.13, 2.2, darkWood);
  box(-7.6, 2.1, 18.5, 0.8, 0.15, 0.6, copper);
  for (const [cx, cz] of [
    [-11, 7],
    [8, 17],
  ]) {
    box(cx, 0.12, cz, 4.5, 0.24, 2.2, mat("#705b3c"));
    for (const side of [-1, 1])
      box(cx, 0.25, cz + side * 1.2, 4.9, 0.35, 0.12, wood);
    for (let i = 0; i < 5; i++)
      cylinder(cx - 1.7 + i * 0.8, 0.22, cz, 0.28, 0.22, 0.35, mat("#99ba68"));
  }
  const bridge = new T.Group();
  bridge.position.set(0, 0, -10);
  root.add(bridge);
  for (let i = 0; i < 29; i++)
    box(0, -0.08, -i * 0.78, 4.8, 0.2, 0.7, i % 3 ? wood : darkWood, bridge);
  for (const side of [-1, 1]) {
    for (let i = 0; i < 8; i++)
      cylinder(side * 2.45, 0.55, -i * 3.1, 0.065, 0.09, 1.5, wood, bridge);
    box(side * 2.45, 0.95, -11, 0.08, 0.1, 23, darkWood, bridge);
    box(side * 2.45, 0.45, -11, 0.08, 0.07, 23, wood, bridge);
  }
  const gate = new T.Group();
  root.add(gate);
  for (const x of [-2.4, 2.4])
    cylinder(x, 0.7, -10.3, 0.14, 0.19, 1.5, wood, gate);
  box(0, 1, -10.3, 4.7, 0.16, 0.15, copper, gate);
  const mechanism = new T.Group();
  mechanism.position.set(BRIDGE.x, 0, BRIDGE.z);
  root.add(mechanism);
  cylinder(0, 0.3, 0, 0.65, 0.85, 0.6, stone, mechanism);
  const cog = mesh(new T.TorusGeometry(0.42, 0.12, 8, 12), copper, mechanism);
  cog.rotation.x = Math.PI / 2;
  cog.position.y = 0.72;
  function model(index, x, z, height, turn = 0) {
    const o = models[index].scene.clone(true),
      b = new T.Box3().setFromObject(o),
      sz = b.getSize(new T.Vector3());
    const k = height / sz.y;
    o.scale.setScalar(k);
    o.position.set(x, -b.min.y * k, z);
    o.rotation.y = turn;
    o.traverse((n) => {
      if (n.isMesh) {
        n.castShadow = n.receiveShadow = true;
      }
    });
    root.add(o);
    return o;
  }
  TREES.forEach(([x, z, h], i) =>
    model(z < -30 && i % 3 ? 1 : 0, x, z, h, i * 1.7),
  );
  model(0, HEART.x, HEART.z - 2, 7.5, 0.3);
  const decoration = new T.Group();
  root.add(decoration);
  for (let i = 0; i < 30; i++) {
    const a = i * 2.3999,
      r = 12 + (i % 4) * 1.7,
      x = Math.sin(a) * r,
      z = 8 + Math.cos(a) * r;
    const o = model(3, x, z, 0.35 + (i % 3) * 0.1, i);
    decoration.attach(o);
  }
  for (const [x, z] of [
    [-12, -39],
    [13, -55],
    [-17, -63],
    [5, -70],
    [-4, 18],
  ]) {
    const o = model(2, x, z, 0.65);
    decoration.attach(o);
  }
  for (let i = 0; i < 22; i++) {
    const a = i * 2.4,
      forest = i % 2 === 0,
      rx = forest ? 24 : 18,
      rz = forest ? 21 : 18,
      cz = forest ? -52 : 8;
    const o = mesh(
      new T.DodecahedronGeometry(0.7 + (i % 3) * 0.25, 1),
      rockMat,
    );
    o.position.set(Math.cos(a) * rx, 0.1, cz + Math.sin(a) * rz);
    o.scale.set(1.6, 0.8, 1);
  }
  function glow(color) {
    const c = document.createElement("canvas");
    c.width = c.height = 64;
    const ctx = c.getContext("2d"),
      g = ctx.createRadialGradient(32, 32, 0, 32, 32, 32);
    g.addColorStop(0, color);
    g.addColorStop(0.25, color);
    g.addColorStop(1, "#ffffff00");
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, 64, 64);
    const t = new T.CanvasTexture(c);
    t.colorSpace = T.SRGBColorSpace;
    return new T.SpriteMaterial({
      map: t,
      transparent: true,
      depthWrite: false,
    });
  }
  const glowGold = glow("#ffe9a3"),
    glowMint = glow("#abffe0");
  const parts = PARTS.map(({ x, z }) => {
    const o = mesh(new T.TorusGeometry(0.45, 0.14, 7, 12), copper);
    o.position.set(x, 0.9, z);
    const s = new T.Sprite(glowGold);
    s.scale.setScalar(2.3);
    o.add(s);
    return o;
  });
  const flies = FIREFLIES.map(({ x, z }) => {
    const o = new T.Sprite(glowGold);
    o.position.set(x, 1.1, z);
    o.scale.setScalar(0.9);
    root.add(o);
    return o;
  });
  const lanterns = LANTERNS.map(({ x, z, target }) => {
    const g = new T.Group();
    g.position.set(x, 0, z);
    root.add(g);
    cylinder(0, 0.3, 0, 0.8, 1, 0.6, stone, g);
    cylinder(0, 1, 0, 0.13, 0.17, 1, wood, g);
    const rotator = new T.Group();
    rotator.position.y = 1.5;
    g.add(rotator);
    const lamp = mesh(
      new T.SphereGeometry(0.27, 16, 8),
      mat("#fff0b0", { emissive: "#ffe6a3", emissiveIntensity: 0.1 }),
      rotator,
    );
    const arrow = mesh(new T.ConeGeometry(0.22, 0.55, 3), copper, rotator);
    arrow.rotation.x = -Math.PI / 2;
    arrow.position.z = -0.55;
    const halo = new T.Sprite(glowMint);
    halo.scale.setScalar(2.5);
    halo.position.y = 1.6;
    g.add(halo);
    const c = document.createElement("canvas");
    c.width = c.height = 128;
    const ctx = c.getContext("2d");
    ctx.fillStyle = "#e9d9b5";
    ctx.fillRect(0, 0, 128, 128);
    ctx.fillStyle = "#536a4e";
    ctx.font = "bold 72px system-ui";
    ctx.textAlign = "center";
    ctx.fillText(["N", "E", "S", "W"][target], 64, 90);
    const t = new T.CanvasTexture(c);
    t.colorSpace = T.SRGBColorSpace;
    const plaque = mesh(
      new T.PlaneGeometry(0.8, 0.8),
      new T.MeshBasicMaterial({ map: t }),
    );
    plaque.rotation.x = -Math.PI / 2;
    plaque.position.set(x, 0.04, z + 1.3);
    return { rotator, lamp, halo };
  });
  const heart = new T.Group();
  heart.position.set(0, 0, -69);
  root.add(heart);
  for (let i = 0; i < 3; i++) {
    const a = (i * Math.PI * 2) / 3;
    const o = mesh(new T.TorusGeometry(0.55, 0.07, 8, 32), copper, heart);
    o.position.set(Math.sin(a) * 0.8, 1.1, Math.cos(a) * 0.8);
  }
  const heartGlow = new T.Sprite(glowMint);
  heartGlow.scale.setScalar(6);
  heartGlow.position.y = 2.2;
  heart.add(heartGlow);
  const particles = new T.Group();
  root.add(particles);
  for (let i = 0; i < 24; i++) {
    const s = new T.Sprite(glowMint);
    s.scale.setScalar(0.12);
    s.userData = { a: i * 2.4, r: 2 + (i % 6) };
    particles.add(s);
  }
  let bridgeAngle = -Math.PI * 0.43;
  return {
    loadedModels: models.length,
    textureCount: 2,
    update(s, time, dt, reduced) {
      const target = s.bridge ? 0 : -Math.PI * 0.43;
      bridgeAngle += (target - bridgeAngle) * Math.min(1, dt * 2.5);
      bridge.rotation.x = bridgeAngle;
      gate.visible = !s.bridge;
      parts.forEach((o, i) => {
        o.visible = !s.parts[i];
        o.rotation.y = reduced ? 0 : time * 0.8;
        o.position.y = 0.9 + (reduced ? 0 : Math.sin(time * 2 + i) * 0.1);
      });
      flies.forEach((o, i) => {
        o.visible = !s.fireflies[i];
        o.position.y = 1.1 + (reduced ? 0 : Math.sin(time * 1.5 + i) * 0.15);
      });
      lanterns.forEach((l, i) => {
        l.rotator.rotation.y = (-s.dials[i] * Math.PI) / 2;
        const ok = s.dials[i] === LANTERNS[i].target;
        l.lamp.material.emissiveIntensity = ok ? 1.6 : 0.1;
        l.halo.visible = ok;
      });
      heartGlow.visible = s.completed;
      particles.visible = s.completed && !reduced;
      particles.children.forEach((o, i) => {
        const a = o.userData.a + time * 0.15;
        o.position.set(
          Math.sin(a) * o.userData.r,
          1 + ((time * 0.4 + i * 0.2) % 4),
          HEART.z + Math.cos(a) * o.userData.r,
        );
      });
    },
    follow(s) {
      sun.position.set(s.x - 25, 45, s.z + 22);
      sun.target.position.set(s.x, 0, s.z);
    },
    quality: (q) => {
      decoration.visible = q !== "low";
      renderer.shadowMap.enabled = q !== "low";
    },
  };
}
