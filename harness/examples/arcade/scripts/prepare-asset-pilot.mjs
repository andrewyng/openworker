// Reproducible preparation, not a runtime downloader. CC0 sources recorded below.
// node scripts/prepare-asset-pilot.mjs /path/to/ToyCar.glb /path/to/RobotExpressive.glb
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { createHash } from "node:crypto";
import { resolve } from "node:path";
const sha = (b) => createHash("sha256").update(b).digest("hex");
const carBytes = readFileSync(process.argv[2]),
  robotBytes = readFileSync(process.argv[3]);
if (
  sha(carBytes) !==
    "01a60862de55cd4b9f3acfab0b0def86451800f9c42467fcd61052c16cb9838c" ||
  sha(robotBytes) !==
    "047f5e5fb3bb6d378bd1df16ca6137f2a596c99b3a1b5690b4020c05aaf6f319"
)
  throw Error("Source asset hash changed; inspect before preparing.");
const jsonLength = carBytes.readUInt32LE(12);
const j = JSON.parse(carBytes.toString("utf8", 20, 20 + jsonLength));
const bin = Buffer.from(carBytes.subarray(28 + jsonLength));
function accessor(id) {
  const a = j.accessors[id],
    v = j.bufferViews[a.bufferView],
    n = { SCALAR: 1, VEC2: 2, VEC3: 3, VEC4: 4 }[a.type],
    bytes = { 5126: 4, 5125: 4, 5123: 2 }[a.componentType];
  return {
    a,
    n,
    bytes,
    offset: (v.byteOffset ?? 0) + (a.byteOffset ?? 0),
    stride: v.byteStride ?? n * bytes,
  };
}
function values(id) {
  const { a, n, offset, stride, bytes } = accessor(id),
    read = { 5126: "readFloatLE", 5125: "readUInt32LE", 5123: "readUInt16LE" }[
      a.componentType
    ];
  return Array.from({ length: a.count }, (_, i) =>
    Array.from({ length: n }, (_, k) =>
      bin[read](offset + i * stride + k * bytes),
    ),
  );
}
const primitive = j.meshes[0].primitives[0],
  positions = values(primitive.attributes.POSITION),
  indices = values(primitive.indices).flat();
// Weld only for connected-component classification. Keep original UV seams/normals.
const parent = positions.map((_, i) => i),
  welded = new Map();
function find(i) {
  while (parent[i] !== i) {
    parent[i] = parent[parent[i]];
    i = parent[i];
  }
  return i;
}
function union(a, b) {
  parent[find(a)] = find(b);
}
positions.forEach((p, i) => {
  const k = p.map((v) => v.toFixed(4)).join(",");
  if (welded.has(k)) union(i, welded.get(k));
  else welded.set(k, i);
});
for (let i = 0; i < indices.length; i += 3) {
  union(indices[i], indices[i + 1]);
  union(indices[i], indices[i + 2]);
}
const components = new Map();
for (let i = 0; i < indices.length; i += 3) {
  const key = find(indices[i]);
  if (!components.has(key)) components.set(key, []);
  components.get(key).push(...indices.slice(i, i + 3));
}
const groups = {
  body: [],
  frontLeft: [],
  frontRight: [],
  rearLeft: [],
  rearRight: [],
};
for (const ids of components.values()) {
  const min = [0, 1, 2].map((k) =>
      Math.min(...ids.map((i) => positions[i][k])),
    ),
    max = [0, 1, 2].map((k) => Math.max(...ids.map((i) => positions[i][k])));
  // Four disjoint wheel assemblies, including tire, inner/outer hubcap and center.
  const lateral = min[0] > 55 || max[0] < -55;
  const front = min[1] > 100 && max[1] < 165,
    rear = min[1] > -140 && max[1] < -75;
  const wheel = lateral && (front || rear) && min[2] > -65 && max[2] < -8;
  const key = wheel
    ? `${front ? "front" : "rear"}${max[0] < 0 ? "Right" : "Left"}`
    : "body";
  groups[key].push(...ids);
}
for (const [name, ix] of Object.entries(groups))
  if (name !== "body" && (ix.length < 9000 || ix.length > 20000))
    throw Error(
      `Unexpected wheel geometry: ${name} (${ix.length / 3} triangles)`,
    );
// Bake into metres: Y-up, -Z forward, bottom at y=0. Geometry/speed remain independent.
const floor = Math.min(...positions.map((p) => -p[2] * 0.01));
const transformed = new Set();
for (const mesh of [j.meshes[0], j.meshes[2]])
  for (const p of mesh.primitives)
    for (const semantic of ["POSITION", "NORMAL", "TANGENT"]) {
      const id = p.attributes[semantic];
      if (id === undefined || transformed.has(id)) continue;
      transformed.add(id);
      const old = values(id),
        { a, offset, stride, n } = accessor(id);
      const converted = old.map(([x, y, z, w]) =>
        semantic === "POSITION"
          ? [-x * 0.01, -z * 0.01 - floor, -y * 0.01]
          : [-x, -z, -y, ...(n === 4 ? [w] : [])],
      );
      converted.forEach((v, i) =>
        v.forEach((x, k) => bin.writeFloatLE(x, offset + i * stride + k * 4)),
      );
      if (a.min) {
        a.min = Array.from({ length: n }, (_, k) =>
          Math.min(...converted.map((p) => p[k])),
        );
        a.max = Array.from({ length: n }, (_, k) =>
          Math.max(...converted.map((p) => p[k])),
        );
      }
    }
const chunks = [bin];
let length = bin.length;
function indexAccessor(ix) {
  const pad = (4 - (length % 4)) % 4;
  if (pad) {
    chunks.push(Buffer.alloc(pad));
    length += pad;
  }
  const b = Buffer.alloc(ix.length * 4);
  ix.forEach((v, i) => b.writeUInt32LE(v, i * 4));
  const view =
    j.bufferViews.push({
      buffer: 0,
      byteOffset: length,
      byteLength: b.length,
      target: 34963,
    }) - 1;
  chunks.push(b);
  length += b.length;
  return (
    j.accessors.push({
      bufferView: view,
      componentType: 5125,
      count: ix.length,
      type: "SCALAR",
      min: [ix.reduce((a, b) => Math.min(a, b), Infinity)],
      max: [ix.reduce((a, b) => Math.max(a, b), -Infinity)],
    }) - 1
  );
}
const carMeshes = [],
  nodes = [{ name: "RetroCoupe", children: [] }];
for (const [name, ix] of Object.entries(groups)) {
  const mesh =
    carMeshes.push({
      name,
      primitives: [{ ...primitive, indices: indexAccessor(ix) }],
    }) - 1;
  if (name === "body") {
    nodes[0].children.push(nodes.length);
    nodes.push({ name: "Body", mesh });
    continue;
  }
  const pts = ix.map((i) => positions[i]),
    center = [0, 1, 2].map(
      (k) =>
        (Math.min(...pts.map((p) => p[k])) +
          Math.max(...pts.map((p) => p[k]))) /
        2,
    );
  const pivot = [
      -center[0] * 0.01,
      -center[2] * 0.01 - floor,
      -center[1] * 0.01,
    ],
    steer = nodes.length;
  nodes[0].children.push(steer);
  nodes.push(
    { name: `${name}Steer`, translation: pivot, children: [steer + 1] },
    { name: `${name}Spin`, children: [steer + 2] },
    { name: `${name}Mesh`, translation: pivot.map((v) => -v), mesh },
  );
}
const glass = carMeshes.push(j.meshes[2]) - 1;
nodes[0].children.push(nodes.length);
nodes.push({ name: "Glass", mesh: glass });
j.meshes = carMeshes;
j.nodes = nodes;
j.scenes = [{ name: "Retro Coupe", nodes: [0] }];
j.scene = 0;
delete j.cameras;
// The demo's green transmissive glass is replaced by a cheaper smoked windshield.
j.materials[2] = {
  name: "Glass",
  pbrMetallicRoughness: {
    baseColorFactor: [0.1, 0.19, 0.23, 1],
    metallicFactor: 0.25,
    roughnessFactor: 0.12,
  },
};
j.buffers = [{ byteLength: length }];
j.asset.generator =
  "Arcade curated-asset preparation v1; source Khronos ToyCar CC0";
function encode(json, binary) {
  let s = Buffer.from(JSON.stringify(json));
  s = Buffer.concat([s, Buffer.alloc((4 - (s.length % 4)) % 4, 32)]);
  const data = Buffer.concat([
      binary,
      Buffer.alloc((4 - (binary.length % 4)) % 4),
    ]),
    out = Buffer.alloc(28 + s.length + data.length);
  out.write("glTF");
  out.writeUInt32LE(2, 4);
  out.writeUInt32LE(out.length, 8);
  out.writeUInt32LE(s.length, 12);
  out.writeUInt32LE(0x4e4f534a, 16);
  s.copy(out, 20);
  out.writeUInt32LE(data.length, 20 + s.length);
  out.writeUInt32LE(0x004e4942, 24 + s.length);
  data.copy(out, 28 + s.length);
  return out;
}
const prepared = encode(j, Buffer.concat(chunks)),
  destination = resolve("asset-library");
mkdirSync(destination, { recursive: true });
writeFileSync(resolve(destination, "retro-coupe.glb"), prepared);
writeFileSync(resolve(destination, "pip-robot.glb"), robotBytes);
console.log(
  JSON.stringify(
    {
      sourceCar: sha(carBytes),
      sourceRobot: sha(robotBytes),
      car: {
        bytes: prepared.length,
        sha256: sha(prepared),
        triangles:
          Object.values(groups).reduce((n, a) => n + a.length / 3, 0) + 2494,
        wheels: Object.fromEntries(
          Object.entries(groups)
            .filter(([k]) => k !== "body")
            .map(([k, v]) => [k, v.length / 3]),
        ),
      },
      robot: { bytes: robotBytes.length, sha256: sha(robotBytes) },
    },
    null,
    2,
  ),
);
