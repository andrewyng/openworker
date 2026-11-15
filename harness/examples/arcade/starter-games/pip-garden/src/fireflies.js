import * as THREE from '../vendor/three.module.js';
// Five glowing fireflies at fixed, reachable spots (clear of trees/boulders).
const SPOTS = [[-2.5, -1.5], [3.8, -1.8], [-6.2, -4.2], [2.5, 5.8], [-1.2, -8.0]];
export const TOTAL = SPOTS.length;
function glowTexture() {
  const c = document.createElement('canvas'); c.width = c.height = 64;
  const g = c.getContext('2d'), gr = g.createRadialGradient(32, 32, 0, 32, 32, 32);
  gr.addColorStop(0, 'rgba(255,255,220,1)'); gr.addColorStop(0.25, 'rgba(255,230,120,.8)');
  gr.addColorStop(1, 'rgba(255,200,80,0)');
  g.fillStyle = gr; g.fillRect(0, 0, 64, 64);
  const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; return t;
}
export function createFireflies(scene) {
  const tex = glowTexture();
  const spriteM = new THREE.SpriteMaterial({ map: tex, blending: THREE.AdditiveBlending, depthWrite: false, transparent: true });
  const bodyG = new THREE.SphereGeometry(0.09, 10, 8);
  const bodyM = new THREE.MeshBasicMaterial({ color: 0xfff3a0 });
  const ringG = new THREE.RingGeometry(0.28, 0.36, 24);
  const ringM = new THREE.MeshBasicMaterial({ color: 0xffe79a, transparent: true, opacity: 0.5, depthWrite: false });
  const flies = SPOTS.map(([x, z], i) => {
    const g = new THREE.Group(); g.position.set(x, 1, z);
    const glow = new THREE.Sprite(spriteM); glow.scale.setScalar(1.1);
    const body = new THREE.Mesh(bodyG, bodyM);
    const ring = new THREE.Mesh(ringG, ringM); ring.rotation.x = -Math.PI / 2;
    g.add(glow, body); scene.add(g);
    ring.position.set(x, 0.02, z); scene.add(ring);
    return { x, z, g, ring, phase: i * 1.7, alive: true, pop: 0 };
  });
  return {
    flies,
    reset() { for (const f of flies) { f.alive = true; f.pop = 0; f.g.visible = f.ring.visible = true; f.g.scale.setScalar(1); } },
    remaining() { return flies.filter(f => f.alive).map(f => ({ x: f.x, z: f.z })); },
    // Returns number collected this step. Collection uses XZ distance + height.
    collect(px, py, pz) {
      let n = 0;
      for (const f of flies) {
        if (!f.alive) continue;
        if (Math.hypot(f.x - px, f.z - pz) < 0.85 && py < 1.8) { f.alive = false; f.pop = 0.001; f.ring.visible = false; n++; }
      }
      return n;
    },
    update(dt, time) {
      for (const f of flies) {
        if (f.alive) {
          const t = time + f.phase;
          f.g.position.set(f.x + Math.sin(t * 1.3) * 0.15, 1 + Math.sin(t * 2.1) * 0.18, f.z + Math.cos(t * 1.1) * 0.15);
          f.g.children[0].material.opacity = 1;
          f.g.children[0].scale.setScalar(1 + Math.sin(t * 5) * 0.15);
          f.ring.scale.setScalar(1 + Math.sin(t * 3) * 0.08);
        } else if (f.pop > 0) {
          f.pop += dt;
          f.g.position.y += dt * 2.5; f.g.scale.setScalar(Math.max(0.01, 1 + f.pop * 2 - f.pop * f.pop * 8));
          if (f.pop > 0.45) { f.pop = 0; f.g.visible = false; }
        }
      }
    }
  };
}
