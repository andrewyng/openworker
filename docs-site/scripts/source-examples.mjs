import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { join } from "node:path";

// Explicit manifests: never publish directory scans, local state or credentials.
export const manifests = {
  desktop: [
    "README.md",
    "src/agent.ts",
    "src/directory.ts",
    "desktop/main.mjs",
    "desktop/preload.cjs",
    "public/desktop.html",
    "public/desktop.js",
    "public/style.css",
    "rust/src/main.rs",
    "rust/Cargo.toml",
    "package.json",
    "tsconfig.json",
    "scripts/export-context.mjs",
    "scripts/setup.mjs",
    "scripts/bundle-runtime.mjs",
  ],
  web: [
    "README.md",
    "src/agent.ts",
    "web/server.mjs",
    "web/pdf.mjs",
    "web/pdf-worker.mjs",
    "public/pdf.html",
    "public/pdf.js",
    "public/style.css",
    "rust/src/main.rs",
    "rust/Cargo.toml",
    "package.json",
    "tsconfig.json",
    "scripts/export-context.mjs",
    "Dockerfile",
    "Dockerfile.dockerignore",
  ],
};
export function writeSources(root, publicDir) {
  const output = join(publicDir, "examples");
  mkdirSync(output, { recursive: true });
  for (const [track, paths] of Object.entries(manifests)) {
    const files = paths.map((path) => ({
      path,
      content: readFileSync(
        join(root, "harness/examples/quickstart", path),
        "utf8",
      ),
    }));
    writeFileSync(
      join(output, `${track}.json`),
      JSON.stringify({ track, files }),
    );
  }
}
export function renderSourceButton(markdown, base) {
  return markdown.replace(
    /<!-- sdk-source: (desktop|web) -->/g,
    (_, track) =>
      `<button class="example-source-button" data-example-source="${base}/examples/${track}.json" type="button">Browse example source <span aria-hidden="true">↗</span></button>`,
  );
}
