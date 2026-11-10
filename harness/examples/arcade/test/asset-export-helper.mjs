import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { unzipSync } from "fflate";
export async function verifyAssetExport(page, app, root, id) {
  await page
    .getByRole("button", { name: "Make it yours", exact: true })
    .click();
  const path = join(root, `${id}.zip`);
  await app.evaluate(({ dialog }, path) => {
    dialog.showSaveDialog = async () => ({ canceled: false, filePath: path });
  }, path);
  await page.getByRole("button", { name: "Export", exact: true }).click();
  let bytes;
  for (let attempt = 0; attempt < 100; attempt++) {
    try {
      bytes = await readFile(path);
      break;
    } catch {
      await page.waitForTimeout(100);
    }
  }
  assert.ok(bytes, "asset ZIP was written");
  const files = unzipSync(bytes);
  assert.deepEqual(
    Buffer.from(files[`assets/library/${id}.glb`]),
    await readFile(`asset-library/${id}.glb`),
  );
  assert.match(
    Buffer.from(files[`assets/library/${id}.LICENSE.txt`]).toString(),
    /CC0/,
  );
  assert.equal(
    JSON.parse(Buffer.from(files[`assets/library/${id}.json`]).toString()).id,
    id,
  );
}
