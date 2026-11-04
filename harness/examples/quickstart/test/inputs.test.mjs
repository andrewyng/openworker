import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, mkdir, writeFile, symlink, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { directorySnapshot } from "../dist/directory.js";
import { extractPdf } from "../web/pdf.mjs";
import { makePdf } from "./fixtures.mjs";
test("directory snapshot excludes hidden/configured secret names, symlinks, builds, binary and oversized content", async (t) => {
  const root = await mkdtemp(join(tmpdir(), "ow-directory-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  await mkdir(join(root, "src"));
  await mkdir(join(root, "node_modules"));
  for (const [name, value] of Object.entries({
    "src/main.ts": 'export const greeting = "hello";',
    ".env": "secret",
    "credentials.json": "secret",
    "node_modules/index.js": "vendor",
    "large.ts": "x".repeat(13000),
    "binary.ts": "a\0b",
  }))
    await writeFile(join(root, name), value);
  await symlink(join(root, ".env"), join(root, "linked.ts"));
  const snapshot = await directorySnapshot(root);
  assert.deepEqual(
    snapshot.files.map((f) => f.path),
    ["src/main.ts"],
  );
  assert.ok(snapshot.omitted >= 5);
  assert.ok(!snapshot.context.includes("secret"));
});
test("PDF text extraction preserves page numbers and rejects non-text or malformed input", async () => {
  const pdf = await extractPdf(makePdf("Orbit revenue is 42 credits."));
  assert.equal(pdf.pages[0].page, 1);
  assert.match(pdf.pages[0].text, /42 credits/);
  await assert.rejects(extractPdf(Buffer.from("not a pdf")), /Choose a PDF/);
  await assert.rejects(extractPdf(makePdf("")), /OCR/);
  await assert.rejects(extractPdf(Buffer.alloc(8 * 1024 * 1024 + 1)), /8 MB/);
});
