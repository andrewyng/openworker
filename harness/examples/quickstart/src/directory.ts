import { readdir, open, realpath } from "node:fs/promises";
import { constants } from "node:fs";
import { join, relative, extname } from "node:path";
const excluded = new Set([
  ".git",
  "node_modules",
  "target",
  "dist",
  "build",
  ".next",
  "vendor",
  ".venv",
]);
const extensions = new Set([
  ".ts",
  ".tsx",
  ".js",
  ".jsx",
  ".mjs",
  ".cjs",
  ".rs",
  ".py",
  ".go",
  ".java",
  ".md",
  ".toml",
  ".json",
  ".css",
  ".html",
  ".yml",
  ".yaml",
]);
const limits = {
  files: 60,
  bytesPerFile: 12_000,
  totalBytes: 180_000,
  entries: 5000,
};
export async function directorySnapshot(directory: string) {
  const root = await realpath(directory);
  const files: { path: string; text: string }[] = [];
  let total = 0,
    visited = 0,
    omitted = 0;
  async function walk(folder: string, depth: number): Promise<void> {
    if (depth > 8) {
      omitted++;
      return;
    }
    const entries = await readdir(folder, { withFileTypes: true });
    entries.sort((a, b) => a.name.localeCompare(b.name));
    for (const entry of entries) {
      if (
        ++visited > limits.entries ||
        files.length >= limits.files ||
        total >= limits.totalBytes
      ) {
        omitted++;
        break;
      }
      if (
        entry.name.startsWith(".") ||
        excluded.has(entry.name) ||
        /(?:secret|credential|password|token|private.?key)/i.test(entry.name)
      ) {
        omitted++;
        continue;
      }
      const path = join(folder, entry.name);
      if (entry.isSymbolicLink()) {
        omitted++;
        continue;
      }
      if (entry.isDirectory()) {
        await walk(path, depth + 1);
        continue;
      }
      if (!entry.isFile() || !extensions.has(extname(entry.name))) {
        omitted++;
        continue;
      }
      // No final-component symlinks; this is a user-selected local snapshot, not a sandbox.
      const handle = await open(
        path,
        constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0),
      );
      try {
        const stat = await handle.stat();
        if (
          !stat.isFile() ||
          stat.size > limits.bytesPerFile ||
          total + stat.size > limits.totalBytes
        ) {
          omitted++;
          continue;
        }
        const buffer = Buffer.alloc(
          Math.min(limits.bytesPerFile + 1, stat.size + 1),
        );
        const { bytesRead } = await handle.read(buffer, 0, buffer.length, 0);
        if (
          bytesRead > limits.bytesPerFile ||
          buffer.subarray(0, bytesRead).includes(0)
        ) {
          omitted++;
          continue;
        }
        files.push({
          path: relative(root, path),
          text: buffer.subarray(0, bytesRead).toString("utf8"),
        });
        total += bytesRead;
      } finally {
        await handle.close();
      }
    }
  }
  await walk(root, 0);
  return {
    files,
    omitted,
    bytes: total,
    context: JSON.stringify({ files, omitted, limits }),
  };
}
