import { readFileSync } from "node:fs";
import { threeFiles, type ProjectFiles } from "./project.js";
export function starterFiles(engine: "canvas" | "three"): ProjectFiles {
  if (engine === "canvas")
    return {
      "index.html":
        '<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="./styles/game.css"></head><body><main><h1>Your next favorite game</h1><canvas id="game"></canvas></main><script type="module" src="./src/game.js"></script></body></html>',
      "styles/game.css":
        "body{margin:0;background:#12172c;color:#fff;font:18px system-ui}main{display:grid;place-items:center;min-height:100vh}canvas{max-width:100%}",
      "src/game.js":
        "// Build the game here. No external resources are needed.\n",
    };
  // One source for both the old 3D starter action and the curated catalog.
  const files = threeFiles();
  for (const path of [
    "index.html",
    "styles/game.css",
    "src/game.js",
    "LICENSE.txt",
  ])
    files[path] = readFileSync(
      new URL(`../../starter-games/orbit-runner/${path}`, import.meta.url),
      "utf8",
    );
  return files;
}
