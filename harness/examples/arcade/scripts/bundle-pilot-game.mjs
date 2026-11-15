// Promote the reviewed, live-agent pilot as a self-contained bundled example.
// Does not edit the original user's/test library or overwrite an existing starter.
import {
  readFileSync,
  existsSync,
  mkdirSync,
  writeFileSync,
  copyFileSync,
} from "node:fs";
import { resolve, dirname, join } from "node:path";
import { Library } from "../build/electron/library.js";
import { fileBytes, digest, projectPath } from "../build/electron/project.js";
const result = JSON.parse(
  readFileSync("test-results/asset-pilot/live.json", "utf8"),
);
const out = resolve("starter-games/pip-garden");
if (existsSync(out))
  throw Error(
    "Starter already exists; preserve it and review changes explicitly.",
  );
const l = new Library(result.root);
try {
  if (l.get(result.id).revisionId !== result.revision)
    throw Error("Pilot revision changed since qualification");
  const project = l.project(result.id),
    files = [],
    hashes = {};
  mkdirSync(join(out, "assets"), { recursive: true });
  for (const [path, file] of Object.entries(project)) {
    if (path.startsWith("vendor/") || path.startsWith("assets/library/"))
      continue;
    projectPath(path);
    const bytes = fileBytes(file);
    mkdirSync(dirname(join(out, path)), { recursive: true });
    writeFileSync(join(out, path), bytes);
    files.push(path);
    hashes[path] = digest(bytes);
  }
  copyFileSync(
    "test-results/asset-pilot/pip-cover.png",
    join(out, "assets/cover.png"),
  );
  files.push("assets/cover.png", "generation.json");
  writeFileSync(
    join(out, "generation.json"),
    JSON.stringify(
      {
        generator: "Arcade AgentService through OpenWorker Harness SDK",
        model: result.model,
        agent: "arcade-studio-builder-v5",
        asset: "pip-robot",
        assetSource: "Quaternius RobotExpressive (CC0)",
        generation:
          "One build followed by one continuation after output-token truncation. Continuation requested smaller ES modules; no game code supplied in the prompts.",
        authoredFileHashes: hashes,
        qualification:
          "Real Electron keyboard playthrough, Idle/Running/Jump/Dance, five collectibles, pause/restart and offline loading. See implementation ledger for later app-side refinements.",
      },
      null,
      2,
    ),
  );
  writeFileSync(
    join(out, "manifest.json"),
    JSON.stringify(
      {
        id: "pip-garden",
        version: "1.0.0",
        title: "Pip's Garden",
        description:
          "A little robot. A floating garden. Five fireflies waiting to be found.",
        engine: "three",
        entrypoint: "index.html",
        license: "MIT",
        tags: ["Explore", "Animated 3D", "Cozy"],
        cover: "assets/cover.png",
        assets: ["pip-robot"],
        files,
      },
      null,
      2,
    ),
  );
  console.log("Bundled reviewed pilot:", out);
} finally {
  l.close();
}
