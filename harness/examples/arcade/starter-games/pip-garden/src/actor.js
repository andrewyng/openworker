import * as THREE from '../vendor/three.module.js';
import { GLTFLoader } from '../vendor/addons/loaders/GLTFLoader.js';
// Pip the robot: `root` owns gameplay position/yaw; `visual` normalizes scale/ground.
export const MODEL_URL = './assets/library/pip-robot.glb';
export const TARGET_HEIGHT = 1.6; // metadata collision.recommendedHeight
export function loadPip(onProgress) {
  return new Promise((resolve, reject) => {
    new GLTFLoader().load(MODEL_URL, gltf => {
      try { resolve(setupPip(gltf)); } catch (e) { reject(e); }
    }, ev => { if (ev.lengthComputable) onProgress(ev.loaded / ev.total); }, reject);
  });
}
function setupPip(gltf) {
  const model = gltf.scene;
  model.traverse(o => { if (o.isMesh) { o.castShadow = true; o.frustumCulled = false; } });
  const mixer = new THREE.AnimationMixer(model);
  const actions = {};
  for (const clip of gltf.animations) actions[clip.name] = mixer.clipAction(clip);
  for (const n of ['Idle', 'Running', 'Jump', 'Dance']) if (!actions[n]) throw new Error('Missing animation clip: ' + n);
  const jump = actions.Jump; jump.setLoop(THREE.LoopOnce, 1); jump.clampWhenFinished = true;
  // Start Idle, evaluate, then measure the posed bounds for normalization.
  actions.Idle.play(); mixer.update(0); model.updateMatrixWorld(true);
  const box = new THREE.Box3().setFromObject(model), size = box.getSize(new THREE.Vector3());
  if (!(size.y > 0)) throw new Error('Robot model has no measurable height');
  const visual = new THREE.Group(); visual.scale.setScalar(TARGET_HEIGHT / size.y);
  model.position.y -= box.min.y; // feet on the ground (model units, inside the scaled wrapper)
  visual.add(model);
  const root = new THREE.Group(); root.add(visual);
  let current = 'Idle';
  const pip = {
    root, mixer, actions, onJumpDone: null,
    get clip() { return current; },
    // Crossfade only on actual state changes.
    play(name, fade = 0.25) {
      if (name === current) return;
      const next = actions[name], prev = actions[current];
      next.reset(); next.setEffectiveWeight(1); next.play();
      if (prev) next.crossFadeFrom(prev, fade, false);
      current = name;
    },
    jump() { if (current === 'Jump') jump.reset().play(); else pip.play('Jump', 0.1); },
    reset() { mixer.stopAllAction(); actions.Idle.reset().play(); current = 'Idle'; },
  };
  mixer.addEventListener('finished', e => { if (e.action === jump && pip.onJumpDone) pip.onJumpDone(); });
  return pip;
}
