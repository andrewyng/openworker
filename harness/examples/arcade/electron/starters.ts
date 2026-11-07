import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import type { StarterGame } from "../shared/contracts.js";
import {
  validateFiles,
  fingerprint,
  projectPath,
  threeFiles,
  type ProjectFiles,
  type ProjectFile,
  binaryAsset,
  isBinaryPath,
} from "./project.js";
import type { Library } from "./library.js";
import { AssetCatalog } from "./asset-catalog.js";

// Explicit allowlist of app-shipped packages, not a downloader or plugin loader.
const IDS = [
  "moonlight-rally",
  "pip-garden",
  "skybound-carrier",
  "neon-drift",
  "lumen-garden",
  "orbit-runner",
] as const;
export class StarterCatalog {
  private packages = new Map<
    string,
    { game: StarterGame; files: ProjectFiles }
  >();
  constructor(
    root = fileURLToPath(new URL("../../starter-games/", import.meta.url)),
  ) {
    for (const id of IDS) {
      const manifest = JSON.parse(
        readFileSync(join(root, id, "manifest.json"), "utf8"),
      );
      if (
        manifest.id !== id ||
        !/^\d+\.\d+\.\d+$/.test(manifest.version) ||
        manifest.entrypoint !== "index.html" ||
        !["canvas", "three"].includes(manifest.engine) ||
        manifest.license !== "MIT" ||
        typeof manifest.title !== "string" ||
        typeof manifest.description !== "string" ||
        !Array.isArray(manifest.tags) ||
        !manifest.tags.every((tag: unknown) => typeof tag === "string") ||
        !Array.isArray(manifest.files) ||
        manifest.files.length > 64
      )
        throw new Error(`Invalid bundled starter: ${id}`);
      const files: ProjectFiles =
        manifest.engine === "three" ? threeFiles() : Object.create(null);
      if (manifest.assets) {
        if (!Array.isArray(manifest.assets) || manifest.assets.length > 2)
          throw Error("Invalid starter assets");
        const catalog = new AssetCatalog();
        for (const id of manifest.assets)
          Object.assign(files, catalog.files(id));
      }
      for (const path of manifest.files) {
        projectPath(path);
        const bytes = readFileSync(join(root, id, path));
        files[path] = isBinaryPath(path)
          ? binaryAsset(path, bytes)
          : bytes.toString("utf8");
      }
      projectPath(manifest.cover);
      projectPath(manifest.hero ?? manifest.cover);
      if (!files[manifest.hero ?? manifest.cover])
        throw new Error("Missing hero illustration.");
      if (!files[manifest.cover] || !files["LICENSE.txt"])
        throw new Error(`Missing starter assets: ${id}`);
      files["starter-origin.json"] = JSON.stringify(
        {
          id,
          version: manifest.version,
          license: manifest.license,
          source: "bundled",
          cover: manifest.cover,
          contentHash: fingerprint(files),
        },
        null,
        2,
      );
      const origin = `arcade-preview://starter-${id}.v${manifest.version.replaceAll(".", "-")}`;
      this.packages.set(id, {
        game: {
          id,
          version: manifest.version,
          title: manifest.title,
          description: manifest.description,
          engine: manifest.engine,
          tags: manifest.tags,
          license: manifest.license,
          coverUrl: `${origin}/${manifest.cover}`,
          heroUrl: `${origin}/${manifest.hero ?? manifest.cover}`,
          previewUrl: `${origin}/index.html`,
        },
        files: Object.freeze(validateFiles(files, true, true)),
      });
    }
  }
  list(): StarterGame[] {
    return [...this.packages.values()].map(({ game }) => ({
      ...game,
      tags: [...game.tags],
    }));
  }
  asset(host: string, path: string): ProjectFile {
    projectPath(path);
    const entry = [...this.packages.values()].find(
      ({ game }) => new URL(game.previewUrl).host === host,
    );
    if (!entry || !Object.hasOwn(entry.files, path))
      throw new Error("Starter asset not found.");
    return entry.files[path];
  }
  remix(id: string, library: Library, root: string) {
    const entry = this.packages.get(id);
    if (!entry) throw new Error("Unknown starter game.");
    const { game: starter, files } = entry;
    const game = library.create(
      root,
      `${starter.title} remix`,
      {
        idea: `Make ${starter.title} my own. ${starter.description}`,
        engine: starter.engine,
        kind: starter.tags[0] ?? "Arcade",
        vibe: "You decide",
        difficulty: "You decide",
      },
      null,
    );
    library.publishProject(
      game.id,
      { ...files },
      `starter:${game.id}`,
      "template",
    );
    library.status(game.id, "ready");
    return library.get(game.id);
  }
}
