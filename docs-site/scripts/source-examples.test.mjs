import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { join, resolve } from "node:path";
import { tmpdir } from "node:os";
import {
  manifests,
  writeSources,
  renderSourceButton,
} from "./source-examples.mjs";
test("source drawer contains exact allowlisted example files and honors deployment base", () => {
  const root = resolve(import.meta.dirname, "../.."),
    out = mkdtempSync(join(tmpdir(), "ow-doc-source-"));
  try {
    writeSources(root, out);
    for (const [track, paths] of Object.entries(manifests)) {
      const data = JSON.parse(
        readFileSync(join(out, "examples", track + ".json"), "utf8"),
      );
      assert.deepEqual(
        data.files.map((f) => f.path),
        paths,
      );
      for (const file of data.files) {
        assert.ok(
          !/(^|\/)(\.state|\.env|node_modules|bin)(\/|$)/.test(file.path),
        );
        assert.equal(
          file.content,
          readFileSync(
            join(root, "harness/examples/quickstart", file.path),
            "utf8",
          ),
        );
      }
      for (const base of ["/docs/sdk", "/docs/sdk/0.1.0-beta.1"])
        assert.ok(
          renderSourceButton(`<!-- sdk-source: ${track} -->`, base).includes(
            `${base}/examples/${track}.json`,
          ),
        );
    }
  } finally {
    rmSync(out, { recursive: true, force: true });
  }
});
