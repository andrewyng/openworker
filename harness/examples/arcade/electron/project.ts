import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
export type BinaryAsset = { encoding: "base64"; data: string };
export type ProjectFile = string | BinaryAsset;
export type ProjectFiles = Record<string, ProjectFile>;
export const ASSET_LIMIT = 8_000_000;
export const PROJECT_LIMIT = 40_000_000;
export const isBinaryPath = (path: string) =>
  /\.(png|webp|jpe?g|wav|ogg|glb)$/i.test(path);
export const fileBytes = (file: ProjectFile): Buffer =>
  typeof file === "string"
    ? Buffer.from(file, "utf8")
    : Buffer.from(file.data, "base64");
export const fileSize = (file: ProjectFile) =>
  typeof file === "string"
    ? Buffer.byteLength(file)
    : Buffer.byteLength(file.data, "base64");
export function textFile(file: ProjectFile | undefined): string {
  if (typeof file !== "string")
    throw new Error(
      "This is a binary asset. Use its path in game code; its bytes are not text.",
    );
  return file;
}
export const assetMime = (path: string): string =>
  ({
    png: "image/png",
    webp: "image/webp",
    jpg: "image/jpeg",
    jpeg: "image/jpeg",
    wav: "audio/wav",
    ogg: "audio/ogg",
    glb: "model/gltf-binary",
    html: "text/html",
    js: "text/javascript",
    css: "text/css",
    json: "application/json",
    svg: "image/svg+xml",
    txt: "text/plain",
  })[path.split(".").at(-1)!.toLowerCase()] ?? "application/octet-stream";
export function binaryAsset(path: string, bytes: Buffer): BinaryAsset {
  projectPath(path);
  if (!isBinaryPath(path) || bytes.length < 12 || bytes.length > ASSET_LIMIT)
    throw new Error("Unsupported or oversized binary asset (8 MB maximum).");
  const ext = path.split(".").at(-1)!.toLowerCase();
  let valid = false;
  let width = 0,
    height = 0;
  if (ext === "png") {
    valid =
      bytes.length >= 33 &&
      bytes
        .subarray(0, 8)
        .equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10])) &&
      bytes.toString("ascii", 12, 16) === "IHDR";
    if (valid) {
      width = bytes.readUInt32BE(16);
      height = bytes.readUInt32BE(20);
    }
  } else if (ext === "jpg" || ext === "jpeg") {
    valid = bytes[0] === 255 && bytes[1] === 216 && bytes[2] === 255;
    if (valid)
      for (let p = 2; p + 4 < bytes.length; ) {
        if (bytes[p++] !== 255) break;
        while (bytes[p] === 255) p++;
        const marker = bytes[p++];
        if (marker === 0xda || marker === 0xd9) break;
        const size = bytes.readUInt16BE(p);
        if (size < 2 || p + size > bytes.length) break;
        if (
          [
            0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd,
            0xce, 0xcf,
          ].includes(marker) &&
          size >= 8
        ) {
          height = bytes.readUInt16BE(p + 3);
          width = bytes.readUInt16BE(p + 5);
          break;
        }
        p += size;
      }
  } else if (ext === "webp") {
    valid =
      bytes.length >= 30 &&
      bytes.toString("ascii", 0, 4) === "RIFF" &&
      bytes.toString("ascii", 8, 12) === "WEBP";
    if (valid) {
      const kind = bytes.toString("ascii", 12, 16);
      if (kind === "VP8X") {
        width = 1 + bytes.readUIntLE(24, 3);
        height = 1 + bytes.readUIntLE(27, 3);
      } else if (
        kind === "VP8 " &&
        bytes.toString("hex", 23, 26) === "9d012a"
      ) {
        width = bytes.readUInt16LE(26) & 0x3fff;
        height = bytes.readUInt16LE(28) & 0x3fff;
      } else if (kind === "VP8L" && bytes[20] === 0x2f) {
        width = 1 + (bytes.readUInt32LE(21) & 0x3fff);
        height = 1 + ((bytes.readUInt32LE(21) >>> 14) & 0x3fff);
      }
    }
  } else if (ext === "wav")
    valid =
      bytes.toString("ascii", 0, 4) === "RIFF" &&
      bytes.toString("ascii", 8, 12) === "WAVE";
  else if (ext === "ogg") valid = bytes.toString("ascii", 0, 4) === "OggS";
  else if (ext === "glb") {
    valid =
      bytes.length >= 20 &&
      bytes.toString("ascii", 0, 4) === "glTF" &&
      bytes.readUInt32LE(4) === 2 &&
      bytes.readUInt32LE(8) === bytes.length &&
      bytes.readUInt32LE(16) === 0x4e4f534a;
    if (valid) {
      const length = bytes.readUInt32LE(12);
      if (length > bytes.length - 20)
        throw new Error("Malformed GLB JSON chunk.");
      const model = JSON.parse(bytes.toString("utf8", 20, 20 + length).trim());
      if (
        model.asset?.version !== "2.0" ||
        (model.extensionsRequired?.length ?? 0) > 0
      )
        throw new Error("Use an uncompressed glTF 2.0 binary model.");
      const rejectUris = (v: unknown): void => {
        if (v && typeof v === "object")
          for (const [key, value] of Object.entries(v)) {
            if (key === "uri")
              throw new Error(
                "GLB assets must embed their buffers and textures; external or data URIs are not allowed.",
              );
            rejectUris(value);
          }
      };
      rejectUris(model);
    }
  }
  if (!valid)
    throw new Error("Asset contents do not match its file extension.");
  if (
    ["png", "webp", "jpg", "jpeg"].includes(ext) &&
    (!width ||
      !height ||
      width > 8192 ||
      height > 8192 ||
      width * height > 32_000_000)
  )
    throw new Error("Image dimensions are invalid or exceed the limit.");
  return { encoding: "base64", data: bytes.toString("base64") };
}
export const THREE_VERSION = "0.186.1";
export const digest = (value: string | Uint8Array) =>
  createHash("sha256").update(value).digest("hex");
export function projectPath(path: unknown): asserts path is string {
  if (
    typeof path !== "string" ||
    path.length > 160 ||
    !path
      .split("/")
      .every(
        (p) =>
          /^[a-zA-Z0-9][a-zA-Z0-9_.-]*$/.test(p) &&
          !/[. ]$/.test(p) &&
          !/^(con|prn|aux|nul|com[0-9]|lpt[0-9])(?:\.|$)/i.test(p),
      ) ||
    !/\.(html|css|js|json|svg|txt|png|webp|jpe?g|wav|ogg|glb)$/i.test(path) ||
    path.split("/")[0].toLowerCase() === "arcade-project.json"
  )
    throw new Error(
      "Use a supported relative source or asset path without traversal or reserved names.",
    );
}
export function validateFiles(
  input: unknown,
  complete = true,
  vendor = false,
): ProjectFiles {
  if (!input || typeof input !== "object" || Array.isArray(input))
    throw new Error("Invalid project files.");
  const entries = Object.entries(input);
  if (entries.length > 64) throw new Error("Keep projects below 64 files.");
  const names = new Set<string>();
  let total = 0,
    authored = 0;
  const files: ProjectFiles = Object.create(null);
  for (const [path, value] of entries.sort(([a], [b]) => a.localeCompare(b))) {
    projectPath(path);
    if (!vendor && /^vendor\//i.test(path))
      throw new Error("Bundled libraries are managed by Arcade.");
    let file: ProjectFile;
    if (isBinaryPath(path)) {
      const candidate = value as BinaryAsset;
      if (
        !candidate ||
        typeof candidate !== "object" ||
        candidate.encoding !== "base64" ||
        typeof candidate.data !== "string" ||
        candidate.data.length > Math.ceil(ASSET_LIMIT / 3) * 4 ||
        candidate.data.length % 4 !== 0 ||
        !/^[A-Za-z0-9+/]*={0,2}$/.test(candidate.data) ||
        Buffer.from(candidate.data, "base64").toString("base64") !==
          candidate.data
      )
        throw new Error("Invalid binary asset encoding.");
      file = binaryAsset(path, Buffer.from(candidate.data, "base64"));
    } else {
      if (
        typeof value !== "string" ||
        Buffer.byteLength(value) >
          (path.startsWith("vendor/") && vendor ? 3_000_000 : 400_000)
      )
        throw new Error("A project file exceeds the size limit.");
      file = value;
    }
    const key = path.toLowerCase();
    if (
      names.has(key) ||
      [...names].some((n) => n.startsWith(key + "/") || key.startsWith(n + "/"))
    )
      throw new Error("Conflicting project paths.");
    names.add(key);
    total += fileSize(file);
    if (!path.startsWith("vendor/") && typeof file === "string")
      authored += fileSize(file);
    files[path] = file;
  }
  if (authored > 600_000 || total > PROJECT_LIMIT)
    throw new Error("Project exceeds its size limit.");
  if (
    complete &&
    (!/<(?:!doctype\s+html|html)[\s>]/i.test(
      textFile(files["index.html"] ?? ""),
    ) ||
      !/<\/html\s*>/i.test(textFile(files["index.html"] ?? "")))
  )
    throw new Error("A complete index.html is required before publication.");
  return files;
}
export function fingerprint(files: ProjectFiles): string {
  return digest(
    JSON.stringify(
      Object.keys(files)
        .sort()
        .map((path) => [
          path,
          typeof files[path] === "string"
            ? digest(files[path] as string)
            : "binary:" + digest(fileBytes(files[path])),
        ]),
    ),
  );
}
let bundled: ProjectFiles | undefined;
export function threeFiles(): ProjectFiles {
  if (!bundled) {
    const require = createRequire(import.meta.url),
      build = dirname(require.resolve("three"));
    bundled = {
      "vendor/three.module.js": readFileSync(
        join(build, "three.module.js"),
        "utf8",
      ),
      "vendor/three.core.js": readFileSync(
        join(build, "three.core.js"),
        "utf8",
      ),
      "vendor/three-LICENSE.txt": readFileSync(
        join(build, "../LICENSE"),
        "utf8",
      ),
      "vendor/library.json": JSON.stringify({
        name: "three",
        version: THREE_VERSION,
      }),
    };
    for (const path of [
      "loaders/GLTFLoader.js",
      "utils/BufferGeometryUtils.js",
      "utils/SkeletonUtils.js",
    ])
      bundled[`vendor/addons/${path}`] = readFileSync(
        new URL(`../vendor/three-addons/${path}`, import.meta.url),
        "utf8",
      ).replace(/from 'three'/g, "from '../../three.module.js'");
  }
  return { ...bundled };
}
