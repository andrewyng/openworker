import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { binaryAsset, digest, type ProjectFiles } from "./project.js";
import type { Json } from "@openworker/harness";

// App-shipped, reviewed assets only. No URL, local path, account or executable input.
export class AssetCatalog {
  private entries = new Map<
    string,
    { metadata: Record<string, Json>; files: ProjectFiles }
  >();
  constructor(
    root = fileURLToPath(new URL("../../asset-library/", import.meta.url)),
  ) {
    const manifest = JSON.parse(
      readFileSync(join(root, "catalog.json"), "utf8"),
    );
    if (manifest.version !== 1 || manifest.assets.length !== 2)
      throw Error("Invalid asset catalog");
    const license = readFileSync(join(root, "LICENSE.txt"), "utf8");
    for (const m of manifest.assets) {
      if (
        !["retro-coupe", "pip-robot"].includes(m.id) ||
        m.file !== `${m.id}.glb` ||
        this.entries.has(m.id)
      )
        throw Error("Invalid asset entry");
      const bytes = readFileSync(join(root, m.file));
      if (bytes.length !== m.bytes || digest(bytes) !== m.sha256)
        throw Error(`Asset integrity failure: ${m.id}`);
      const path = `assets/library/${m.file}`;
      const metadata = {
        ...m,
        path,
        metadataPath: `assets/library/${m.id}.json`,
        licensePath: `assets/library/${m.id}.LICENSE.txt`,
      };
      this.entries.set(m.id, {
        metadata,
        files: {
          [path]: binaryAsset(path, bytes),
          [metadata.metadataPath]: JSON.stringify(metadata, null, 2),
          [metadata.licensePath]: license,
        },
      });
    }
  }
  list() {
    return structuredClone([...this.entries.values()].map((e) => e.metadata));
  }
  files(id: string): ProjectFiles {
    const entry = this.entries.get(id);
    if (!entry) throw Error("Unknown bundled asset");
    return structuredClone(entry.files);
  }
}
