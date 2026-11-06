import {
  readFile,
  writeFile,
  mkdir,
  copyFile,
  chmod,
  access,
} from "node:fs/promises";
import { runtimeArch } from "./runtime-identity.mjs";
import { execFileSync } from "node:child_process";
import { join, resolve } from "node:path";
const config = JSON.parse(await readFile("openworker.json", "utf8"));
// Keep development checkout paths out of the installed application's metadata.
await writeFile(
  "app.json",
  JSON.stringify({ appId: config.appId }, null, 2) + "\n",
);
const name =
  process.platform === "win32"
    ? "openworker-harness.exe"
    : "openworker-harness";
const npm = process.platform === "win32" ? "npm.cmd" : "npm";
if (config.sdkRoot && config.template === "electron-game-builder") {
  try {
    await access(
      join(config.sdkRoot, "typescript-sdk", "node_modules", "typescript"),
    );
  } catch {
    execFileSync(npm, ["ci"], {
      cwd: join(config.sdkRoot, "typescript-sdk"),
      stdio: "inherit",
      shell: process.platform === "win32",
    });
  }
  execFileSync(npm, ["run", "build"], {
    cwd: join(config.sdkRoot, "typescript-sdk"),
    stdio: "inherit",
    shell: process.platform === "win32",
  });
}
let source = config.runtime;
if (!source && !config.sdkRoot) {
  const { resolveRuntimeExecutable } = await import("@openworker/harness");
  source = resolveRuntimeExecutable();
}
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
    { cwd: config.sdkRoot, stdio: "inherit" },
  );
  source = join(config.sdkRoot, "target", "release", name);
}
const architecture = runtimeArch(await readFile(source));
if (config.template === "electron-game-builder") {
  const builder = JSON.parse(await readFile("electron-builder.json", "utf8"));
  const platform = { win32: "win", darwin: "mac", linux: "linux" }[
    process.platform
  ];
  const target = { win32: "nsis", darwin: "dmg", linux: "AppImage" }[
    process.platform
  ];
  builder[platform].target = [{ target, arch: [architecture] }];
  await writeFile(
    "electron-builder.json",
    JSON.stringify(builder, null, 2) + "\n",
  );
}
await mkdir("bin", { recursive: true });
await copyFile(source, join("bin", name));
if (process.platform !== "win32") await chmod(join("bin", name), 0o755);
if (config.template === "tauri-game-builder") {
  execFileSync(
    npm,
    [
      "exec",
      "--offline",
      "--",
      "tauri",
      "icon",
      "icon.svg",
      "--output",
      "src-tauri/icons",
    ],
    { stdio: "inherit", shell: process.platform === "win32" },
  );
  const triple = execFileSync("rustc", ["-vV"], { encoding: "utf8" }).match(
    /^host: (.+)$/m,
  )?.[1];
  if (!triple) throw Error("Rust host target unavailable");
  if (!triple.startsWith(architecture === "arm64" ? "aarch64-" : "x86_64-"))
    throw Error(
      "Prebuilt helper architecture must match the Tauri Rust target",
    );
  await mkdir("src-tauri/binaries", { recursive: true });
  await copyFile(
    source,
    `src-tauri/binaries/openworker-harness-${triple}${process.platform === "win32" ? ".exe" : ""}`,
  );
}
console.log("Bundled runtime: " + resolve("bin", name));
