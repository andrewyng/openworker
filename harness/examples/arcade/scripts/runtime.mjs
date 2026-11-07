import { copyFile, mkdir, chmod, readFile } from "node:fs/promises";
import { execFileSync } from "node:child_process";
import { resolve, dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const native = resolve(process.env.OPENWORKER_SDK_ROOT ?? join(root, "../.."));
const name =
  process.platform === "win32"
    ? "openworker-harness.exe"
    : "openworker-harness";
let source = process.env.OPENWORKER_RUNTIME;
if (!source) {
  execFileSync(
    "cargo",
    [
      "build",
      "--locked",
      "--release",
      "-p",
      "openworker-sdk",
      "--bin",
      "openworker-harness",
    ],
    { cwd: native, stdio: "inherit" },
  );
  source = join(native, "target/release", name);
}
// Do not rename binaries across architectures. Electron Builder must follow this helper.
const bytes = await readFile(source);
let arch;
if (process.platform === "darwin" && bytes.readUInt32LE(0) === 0xfeedfacf)
  arch =
    bytes.readUInt32LE(4) === 0x100000c
      ? "arm64"
      : bytes.readUInt32LE(4) === 0x1000007
        ? "x64"
        : undefined;
if (process.platform === "win32" && bytes.subarray(0, 2).toString() === "MZ") {
  const pe = bytes.readUInt32LE(60),
    m = bytes.readUInt16LE(pe + 4);
  arch = m === 0xaa64 ? "arm64" : m === 0x8664 ? "x64" : undefined;
}
if (!arch)
  throw new Error(
    "Supply a native x64/ARM64 harness for this operating system.",
  );
const targetArch = process.env.ARCADE_TARGET_ARCH ?? process.arch;
if (!["arm64", "x64"].includes(targetArch) || arch !== targetArch)
  throw new Error(
    "The bundled harness must match this Electron build architecture.",
  );
await mkdir(join(root, "bin"), { recursive: true });
await copyFile(source, join(root, "bin", name));
if (process.platform !== "win32") await chmod(join(root, "bin", name), 0o755);
console.log(`Bundled ${name} (${arch})`);
