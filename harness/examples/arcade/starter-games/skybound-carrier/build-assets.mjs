// Original model and sound generator. Run from Arcade: node starter-games/skybound-carrier/build-assets.mjs
import * as THREE from "three";
import { GLTFExporter } from "three/addons/exporters/GLTFExporter.js";
import { writeFileSync } from "node:fs";
globalThis.FileReader = class {
  async readAsArrayBuffer(blob) {
    this.result = await blob.arrayBuffer();
    this.onloadend?.();
  }
  async readAsDataURL(blob) {
    this.result =
      "data:application/octet-stream;base64," +
      Buffer.from(await blob.arrayBuffer()).toString("base64");
    this.onloadend?.();
  }
};
const root = new THREE.Group();
root.name = "Courier";
const mat = (c) =>
  new THREE.MeshStandardMaterial({ color: c, roughness: 0.72 });
const teal = mat("#217a75"),
  cream = mat("#f6e1b7"),
  gold = mat("#e2a34d"),
  dark = mat("#303c4b"),
  red = mat("#dd785c");
function box(parent, name, size, pos, material) {
  const m = new THREE.Mesh(new THREE.BoxGeometry(...size), material);
  m.name = name;
  m.position.set(...pos);
  parent.add(m);
  return m;
}
box(root, "Jacket", [0.64, 0.65, 0.4], [0, 1.02, 0], teal);
box(root, "Belt", [0.68, 0.08, 0.43], [0, 0.76, 0], gold);
box(root, "Head", [0.48, 0.46, 0.46], [0, 1.57, 0], cream);
box(root, "Cap", [0.6, 0.15, 0.55], [0, 1.83, 0], teal);
box(root, "CapBrim", [0.62, 0.055, 0.3], [0, 1.75, -0.29], teal);
box(root, "Scarf", [0.68, 0.13, 0.5], [0, 1.32, 0], red);
box(root, "ScarfTail", [0.18, 0.65, 0.06], [-0.25, 1.13, 0.3], red);
box(root, "Parcel", [0.55, 0.6, 0.35], [0, 1.05, 0.38], gold);
box(root, "ParcelStrap", [0.09, 0.63, 0.37], [0, 1.05, 0.38], cream);
for (const x of [-0.12, 0.12])
  box(root, "Eye" + x, [0.055, 0.06, 0.02], [x, 1.6, -0.24], dark);
const limbs = [];
for (const side of [-1, 1]) {
  const leg = new THREE.Group();
  leg.name = side < 0 ? "LeftLeg" : "RightLeg";
  leg.position.set(side * 0.19, 0.76, 0);
  root.add(leg);
  box(leg, "Trouser" + side, [0.24, 0.48, 0.27], [0, -0.24, 0], dark);
  box(leg, "Boot" + side, [0.29, 0.2, 0.4], [0, -0.62, -0.06], gold);
  limbs.push([leg, side * 0.7]);
  const arm = new THREE.Group();
  arm.name = side < 0 ? "LeftArm" : "RightArm";
  arm.position.set(side * 0.43, 1.29, 0);
  root.add(arm);
  box(arm, "Sleeve" + side, [0.22, 0.4, 0.26], [0, -0.22, 0], teal);
  box(arm, "Hand" + side, [0.21, 0.19, 0.23], [0, -0.5, 0], cream);
  limbs.push([arm, -side * 0.55]);
}
const times = [0, 0.175, 0.35, 0.525, 0.7],
  tracks = [];
for (const [limb, swing] of limbs) {
  const values = times.flatMap((t) =>
    new THREE.Quaternion()
      .setFromAxisAngle(
        new THREE.Vector3(1, 0, 0),
        Math.cos((t / 0.7) * Math.PI * 2) * swing,
      )
      .toArray(),
  );
  tracks.push(
    new THREE.QuaternionKeyframeTrack(limb.name + ".quaternion", times, values),
  );
}
const clip = new THREE.AnimationClip("Run", 0.7, tracks);
const buffer = await new GLTFExporter().parseAsync(root, {
  binary: true,
  animations: [clip],
});
writeFileSync(
  new URL("./assets/courier.glb", import.meta.url),
  Buffer.from(buffer),
);
function sound(name, seconds, notes) {
  const rate = 22050,
    n = Math.floor(rate * seconds),
    b = Buffer.alloc(44 + n * 2);
  b.write("RIFF");
  b.writeUInt32LE(b.length - 8, 4);
  b.write("WAVEfmt ", 8);
  b.writeUInt32LE(16, 16);
  b.writeUInt16LE(1, 20);
  b.writeUInt16LE(1, 22);
  b.writeUInt32LE(rate, 24);
  b.writeUInt32LE(rate * 2, 28);
  b.writeUInt16LE(2, 32);
  b.writeUInt16LE(16, 34);
  b.write("data", 36);
  b.writeUInt32LE(n * 2, 40);
  for (let i = 0; i < n; i++) {
    const t = i / rate;
    let s = 0;
    for (const [start, f, dur] of notes) {
      const a = t - start;
      if (a >= 0 && a < dur)
        s +=
          Math.sin(2 * Math.PI * f * a) *
          Math.min(1, a / 0.012) *
          Math.exp((-a * 6) / dur) *
          0.18;
    }
    b.writeInt16LE(
      Math.max(-32767, Math.min(32767, Math.round(s * 32767))),
      44 + i * 2,
    );
  }
  writeFileSync(new URL("./assets/" + name, import.meta.url), b);
}
sound("collect.wav", 0.45, [
  [0, 880, 0.3],
  [0.08, 1320, 0.35],
]);
sound("delivery.wav", 1.6, [
  [0, 523, 0.8],
  [0.2, 659, 0.8],
  [0.4, 784, 1],
  [0.7, 1046, 0.8],
]);
sound("wind.wav", 4, [
  [0, 130.81, 4],
  [0, 196, 4],
  [0, 261.63, 4],
]);
console.log(
  "Generated original animated courier GLB and three PCM WAV assets.",
);
