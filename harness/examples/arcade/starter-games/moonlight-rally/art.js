import * as THREE from "./vendor/three.module.js";
// Small, deterministic surface maps generated locally; no downloaded artwork.
export function surface(kind) {
  const c = document.createElement("canvas");
  c.width = c.height = 512;
  const a = c.getContext("2d");
  let seed = 73411;
  const random = () =>
    ((seed = (Math.imul(seed, 1664525) + 1013904223) | 0) >>> 0) / 4294967296;
  a.fillStyle =
    kind === "asphalt" ? "#acb0b5" : kind === "paving" ? "#a6a9aa" : "#c3c1bd";
  a.fillRect(0, 0, 512, 512);
  for (let i = 0; i < 26000; i++) {
    const n = Math.floor(100 + random() * 150);
    a.fillStyle = `rgba(${n},${n},${n},${kind === "asphalt" ? 0.24 : 0.07})`;
    a.fillRect(
      random() * 512,
      random() * 512,
      1 + random() * 2,
      1 + random() * 2,
    );
  }
  if (kind === "roof") {
    a.fillStyle = "#bd8a6b";
    a.fillRect(0, 0, 512, 512);
    a.strokeStyle = "#754a3848";
    a.lineWidth = 3;
    for (let y = 0; y < 512; y += 48)
      for (let x = ((y / 48) % 2) * 16; x < 512; x += 32) {
        a.beginPath();
        a.ellipse(x + 16, y + 22, 13, 25, 0, 0, Math.PI);
        a.stroke();
        a.fillStyle = "#e2b18a44";
        a.fillRect(x + 6, y + 2, 4, 36);
      }
  } else if (kind === "paving") {
    a.strokeStyle = "#686f7770";
    a.lineWidth = 2;
    for (let y = 0; y <= 512; y += 64) {
      a.beginPath();
      a.moveTo(0, y);
      a.lineTo(512, y);
      a.stroke();
      for (let x = ((y / 64) % 2) * 64; x < 512; x += 128) {
        a.beginPath();
        a.moveTo(x, y);
        a.lineTo(x, y + 64);
        a.stroke();
      }
    }
  } else if (kind === "plaster") {
    for (let i = 0; i < 14; i++) {
      const x = random() * 512,
        y = random() * 512,
        r = 20 + random() * 80,
        g = a.createRadialGradient(x, y, 0, x, y, r);
      g.addColorStop(0, "#60595113");
      g.addColorStop(1, "#60595100");
      a.fillStyle = g;
      a.fillRect(x - r, y - r, r * 2, r * 2);
    }
  } else {
    a.strokeStyle = "#5d646a36";
    a.lineWidth = 2;
    for (let i = 0; i < 7; i++) {
      let x = random() * 512,
        y = random() * 512;
      a.beginPath();
      a.moveTo(x, y);
      for (let j = 0; j < 5; j++) {
        x += random() * 35 - 17;
        y += random() * 30;
        a.lineTo(x, y);
      }
      a.stroke();
    }
  }
  const map = new THREE.CanvasTexture(c);
  map.colorSpace = THREE.SRGBColorSpace;
  map.wrapS = map.wrapT = THREE.RepeatWrapping;
  map.anisotropy = 4;
  return map;
}
export function islandGeometry(seed, width, height, depth) {
  const g = new THREE.SphereGeometry(1, 28, 14),
    p = g.attributes.position;
  for (let i = 0; i < p.count; i++) {
    const x = p.getX(i),
      y = p.getY(i),
      z = p.getZ(i),
      angle = Math.atan2(z, x),
      rough =
        1 +
        0.13 * Math.sin(angle * 3 + seed) +
        0.06 * Math.cos(angle * 7 - seed);
    p.setXYZ(
      i,
      x * width * rough,
      y > 0
        ? Math.pow(y, 0.8) * height * (0.82 + 0.18 * Math.sin(angle * 2 + seed))
        : -7,
      z * depth * rough,
    );
  }
  g.computeVertexNormals();
  return g;
}
export function reflectionEnvironment(renderer, night) {
  const c = document.createElement("canvas");
  c.width = 512;
  c.height = 256;
  const a = c.getContext("2d"),
    g = a.createLinearGradient(0, 0, 0, 256);
  g.addColorStop(0, night ? "#172b4c" : "#87b5dc");
  g.addColorStop(0.46, night ? "#96b9d1" : "#ffe7c8");
  g.addColorStop(0.52, night ? "#33434c" : "#8b8480");
  g.addColorStop(1, "#15222b");
  a.fillStyle = g;
  a.fillRect(0, 0, 512, 256);
  for (let i = 0; i < 12; i++) {
    a.fillStyle = night ? (i % 2 ? "#dfabac" : "#a6ddd3") : "#fff6df";
    a.fillRect(i * 43, 93 + (i % 3) * 6, 5, 20);
  }
  const texture = new THREE.CanvasTexture(c);
  texture.mapping = THREE.EquirectangularReflectionMapping;
  texture.colorSpace = THREE.SRGBColorSpace;
  const generator = new THREE.PMREMGenerator(renderer),
    target = generator.fromEquirectangular(texture);
  texture.dispose();
  generator.dispose();
  return target;
}
export function roundedBody(w, h, d, r = 0.08) {
  const s = new THREE.Shape(),
    x = -w / 2,
    y = -h / 2;
  s.moveTo(x + r, y);
  s.lineTo(x + w - r, y);
  s.quadraticCurveTo(x + w, y, x + w, y + r);
  s.lineTo(x + w, y + h - r);
  s.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
  s.lineTo(x + r, y + h);
  s.quadraticCurveTo(x, y + h, x, y + h - r);
  s.lineTo(x, y + r);
  s.quadraticCurveTo(x, y, x + r, y);
  const g = new THREE.ExtrudeGeometry(s, {
    depth: d - 2 * r,
    bevelEnabled: true,
    bevelSegments: 3,
    steps: 1,
    bevelSize: r,
    bevelThickness: r,
    curveSegments: 6,
  });
  g.translate(0, 0, -(d - 2 * r) / 2);
  g.computeVertexNormals();
  return g;
}
export function palmFrond() {
  const vertices = [],
    indices = [];
  for (let i = 0; i <= 12; i++) {
    const t = i / 12,
      x = t * 3.2,
      y = Math.sin(t * Math.PI) * 0.45 - t * t * 0.7,
      w = Math.sin(t * Math.PI) * 0.42 + 0.015;
    vertices.push(x, y, -w, x, y, w);
    if (i < 12) {
      const n = i * 2;
      indices.push(n, n + 1, n + 2, n + 1, n + 3, n + 2);
    }
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.Float32BufferAttribute(vertices, 3));
  g.setIndex(indices);
  g.computeVertexNormals();
  g.userData.staticScenery = true;
  return g;
}
