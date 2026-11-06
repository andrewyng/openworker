#!/usr/bin/env node
import { cp, readFile, writeFile, mkdir, access } from "node:fs/promises";
import { resolve, dirname, join, basename } from "node:path";
import { fileURLToPath } from "node:url";
const here = dirname(fileURLToPath(import.meta.url));
const args = process.argv.slice(2).filter((v) => v !== "--");
const output = args.shift();
if (!output || output.startsWith("-"))
  throw Error(
    "Usage: create-openworker <directory> --template electron-game-builder|tauri-game-builder [--sdk-root PATH] [--runtime PATH]",
  );
const flags = {};
for (let i = 0; i < args.length; i += 2) {
  if (
    !["--template", "--sdk-root", "--runtime"].includes(args[i]) ||
    !args[i + 1]
  )
    throw Error("Unknown or incomplete option");
  flags[args[i]] = args[i + 1];
}
const template = flags["--template"] ?? "electron-game-builder";
if (!["electron-game-builder", "tauri-game-builder"].includes(template))
  throw Error("Unknown desktop template");
const destination = resolve(output);
try {
  await access(destination);
  throw Error("Destination exists; choose an empty new directory");
} catch (e) {
  if (e.code !== "ENOENT") throw e;
}
const sdkRoot = flags["--sdk-root"] ? resolve(flags["--sdk-root"]) : null;
if (sdkRoot) await access(join(sdkRoot, "harness-sdk", "Cargo.toml"));
const version = JSON.parse(await readFile(join(here,"package.json"),"utf8")).version;
const appId = basename(destination)
  .replace(/[^a-zA-Z0-9_-]/g, "-")
  .slice(0, 60);
if (!appId) throw Error("Choose an application name");
await mkdir(destination, { recursive: true });
await cp(join(here, "templates", "shared"), destination, { recursive: true });
await cp(join(here, "templates", template), destination, { recursive: true });
const pkg = JSON.parse(
  await readFile(join(destination, "package.json"), "utf8"),
);
pkg.name = appId.toLowerCase();
pkg.engines = { node: ">=22.12.0", npm: ">=11" };
pkg.packageManager = "npm@11.21.1";
if (template === "electron-game-builder")
  pkg.dependencies["@openworkerai/harness-sdk"] =
    sdkRoot ? "file:" + join(sdkRoot, "typescript-sdk") : version;
if (template === "tauri-game-builder" && !sdkRoot) pkg.devDependencies["@openworkerai/harness-sdk"] = version;
await writeFile(
  join(destination, "package.json"),
  JSON.stringify(pkg, null, 2) + "\n",
);
await writeFile(
  join(destination, "openworker.json"),
  JSON.stringify(
    {
      appId,
      sdkRoot,
      runtime: flags["--runtime"] ? resolve(flags["--runtime"]) : null,
      template,
    },
    null,
    2,
  ) + "\n",
);
if (template === "electron-game-builder") {
  const file = join(destination, "electron-builder.json");
  const config = JSON.parse(await readFile(file, "utf8"));
  config.appId = "com.openworker." + appId.toLowerCase().replaceAll("_", "-");
  await writeFile(file, JSON.stringify(config, null, 2) + "\n");
}
await writeFile(
  join(destination, "app.json"),
  JSON.stringify({ appId }, null, 2) + "\n",
);
if (template === "tauri-game-builder") {
  const cargo = await readFile(
    join(destination, "src-tauri", "Cargo.toml"),
    "utf8",
  );
  await writeFile(
    join(destination, "src-tauri", "Cargo.toml"),
    sdkRoot ? cargo.replace('"__SDK_PATH__"', JSON.stringify(join(sdkRoot,"harness-sdk").replaceAll("\\","/"))) : cargo.replace('openworker-sdk = { path = "__SDK_PATH__" }', `openworker-sdk = "=${version}"`),
  );
  const config = JSON.parse(
    await readFile(join(destination, "src-tauri", "tauri.conf.json"), "utf8"),
  );
  config.identifier =
    "com.openworker." + appId.toLowerCase().replaceAll("_", "-");
  await writeFile(
    join(destination, "src-tauri", "tauri.conf.json"),
    JSON.stringify(config, null, 2) + "\n",
  );
}
console.log(
  `Created ${template} in ${destination}\n\ncd ${destination}\nnpm install\nnpm run dev\n\nPackage on the target OS with npm run package. Signing/notarization require your release credentials.`,
);
