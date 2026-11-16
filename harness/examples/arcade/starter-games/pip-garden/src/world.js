import * as THREE from '../vendor/three.module.js';
// Builds the floating garden island. Returns collision data for gameplay.
export const ISLAND_R = 9, WALK_R = 8.3;
export function buildWorld(scene) {
  scene.background = new THREE.Color(0xbfe8f2);
  scene.fog = new THREE.Fog(0xbfe8f2, 26, 60);
  scene.add(new THREE.HemisphereLight(0xfff4d6, 0x7fb99a, 1.6));
  const sun = new THREE.DirectionalLight(0xffe7b8, 2.6);
  sun.position.set(7, 14, 6); sun.castShadow = true;
  sun.shadow.mapSize.set(1024, 1024); sun.shadow.radius = 4; sun.shadow.bias = -0.0005;
  Object.assign(sun.shadow.camera, { left: -11, right: 11, top: 11, bottom: -11, near: 1, far: 40 });
  scene.add(sun);
  const mat = (c, r = 0.85) => new THREE.MeshStandardMaterial({ color: c, roughness: r, flatShading: true });
  // Island: grass top, soil rim and rocky underside cone.
  const grass = new THREE.Mesh(new THREE.CylinderGeometry(ISLAND_R, ISLAND_R - 0.2, 0.5, 48), mat(0x8fd37a));
  grass.position.y = -0.25; grass.receiveShadow = true; scene.add(grass);
  const soil = new THREE.Mesh(new THREE.CylinderGeometry(ISLAND_R - 0.2, ISLAND_R - 0.8, 1.2, 24), mat(0xb98a5e));
  soil.position.y = -1.1; scene.add(soil);
  const rock = new THREE.Mesh(new THREE.ConeGeometry(ISLAND_R - 0.8, 6, 12), mat(0x9a7a62));
  rock.rotation.x = Math.PI; rock.position.y = -4.7; scene.add(rock);
  // Stepping-stone path (instanced).
  const stones = new THREE.InstancedMesh(new THREE.CylinderGeometry(0.45, 0.5, 0.12, 9), mat(0xe8e0cf, 0.95), 11);
  const m = new THREE.Matrix4(), q = new THREE.Quaternion(), s = new THREE.Vector3(), p = new THREE.Vector3();
  for (let i = 0; i < 11; i++) {
    const t = i / 10, x = Math.sin(t * 5) * 2.2, z = 6.8 - t * 13;
    const k = 0.8 + ((i * 37) % 5) / 12;
    m.compose(p.set(x, 0.03, z), q.setFromAxisAngle(new THREE.Vector3(0, 1, 0), i), s.set(k, 1, k * 0.85));
    stones.setMatrixAt(i, m);
  }
  stones.receiveShadow = true; scene.add(stones);
  // Trees: trunk + foliage clumps. Obstacles are circles in XZ.
  const obstacles = [];
  const trunkG = new THREE.CylinderGeometry(0.18, 0.28, 1.6, 7), leafG = new THREE.IcosahedronGeometry(1, 0);
  const trunkM = mat(0x8a5a3b), leafMs = [mat(0x5fae6a), mat(0x7cc46f), mat(0xf2a7b8)];
  [[-5, -3, 1.1, 0], [5.2, -4.2, 1.3, 1], [-4.2, 4.6, 0.9, 2], [5.6, 3.4, 1, 0], [0.6, -7, 0.85, 1]].forEach(([x, z, k, c]) => {
    const g = new THREE.Group(); g.position.set(x, 0, z); g.scale.setScalar(k);
    const tr = new THREE.Mesh(trunkG, trunkM); tr.position.y = 0.8; tr.castShadow = true; g.add(tr);
    [[0, 2.1, 0, 1.1], [0.6, 1.8, 0.3, 0.75], [-0.5, 1.9, -0.3, 0.8], [0, 2.8, 0, 0.7]].forEach(([a, b, d, r]) => {
      const l = new THREE.Mesh(leafG, leafMs[c]); l.position.set(a, b, d); l.scale.setScalar(r);
      l.castShadow = true; g.add(l);
    });
    scene.add(g); obstacles.push({ x, z, r: 0.45 * k + 0.35 });
  });
  // A few boulders as additional obstacles.
  const boulderG = new THREE.DodecahedronGeometry(0.5, 0), boulderM = mat(0xc9bfae);
  [[2.8, 1.2, 1], [-2.6, -5.8, 0.8], [-6.8, 0.6, 1.2]].forEach(([x, z, k]) => {
    const b = new THREE.Mesh(boulderG, boulderM); b.position.set(x, 0.25 * k, z); b.scale.set(k, k * 0.7, k);
    b.castShadow = b.receiveShadow = true; scene.add(b); obstacles.push({ x, z, r: 0.5 * k + 0.35 });
  });
  // Flowers: instanced stems + blossoms with per-instance colours.
  const N = 90, stemG = new THREE.CylinderGeometry(0.02, 0.02, 0.36, 4), headG = new THREE.IcosahedronGeometry(0.11, 0);
  const stemsI = new THREE.InstancedMesh(stemG, mat(0x4f9a52), N), heads = new THREE.InstancedMesh(headG, mat(0xffffff, 0.6), N);
  const cols = [0xffd2e1, 0xfff1a8, 0xffffff, 0xc9b8ff, 0xffa98a].map(c => new THREE.Color(c));
  let seed = 7; const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
  for (let i = 0; i < N; i++) {
    const a = rnd() * Math.PI * 2, r = 1.5 + Math.sqrt(rnd()) * 6.9, x = Math.cos(a) * r, z = Math.sin(a) * r;
    m.compose(p.set(x, 0.18, z), q.identity(), s.set(1, 1, 1)); stemsI.setMatrixAt(i, m);
    m.compose(p.set(x, 0.38, z), q.identity(), s.setScalar(0.8 + rnd() * 0.5)); heads.setMatrixAt(i, m);
    heads.setColorAt(i, cols[i % cols.length]);
  }
  heads.castShadow = true; scene.add(stemsI, heads);
  // Drifting clouds below the island for depth.
  const cloudM = new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 1, flatShading: true });
  const clouds = new THREE.Group();
  [[-14, -6, -8], [13, -8, -4], [4, -10, 12], [-10, -9, 10]].forEach(([x, y, z]) => {
    for (let j = 0; j < 3; j++) {
      const c = new THREE.Mesh(leafG, cloudM); c.position.set(x + j * 1.4, y + (j % 2) * 0.4, z);
      c.scale.set(1.6, 0.9, 1.2); clouds.add(c);
    }
  });
  scene.add(clouds);
  return { obstacles, clouds };
}
