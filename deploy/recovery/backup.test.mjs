import test, { after } from "node:test";
import assert from "node:assert/strict";
import {
  mkdtemp,
  readFile,
  writeFile,
  unlink,
  symlink,
  rm,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { capture, verify, hash, MAX_DUMP } from "./backup.mjs";
const temporary = [];
after(async () => {
  for (const path of temporary) await rm(path, { recursive: true });
});
async function fixture() {
  const parent = await mkdtemp(join(tmpdir(), "ow-backup-test-")),
    root = join(parent, "archive");
  temporary.push(parent);
  const data = Buffer.from("recovered artifact"),
    expected = { size: data.length, sha256: hash(data) };
  const manifest = Buffer.from(
    JSON.stringify({
      version: 1,
      expected,
      parts: [{ size: data.length, sha256: hash(data) }],
    }),
  );
  const a = {
    id: "a_one",
    key: "drill/s_one/a_one",
    binding: { deployment: "drill", identity: hash("store") },
    registration: { session_id: "s_one", expected },
    status: "ready",
    proof: { expected, manifest_sha256: hash(manifest) },
  };
  const objects = new Map([
    [a.key + "/manifest", manifest],
    [a.key + "/part-00", data],
  ]);
  await capture(root, Buffer.from("trusted dump fixture"), [a], async (key) =>
    objects.get(key),
  );
  return { root, parent, a, objects };
}
test("complete backup verifies whole artifact and preserves catalog binding", async () => {
  const { root, a } = await fixture();
  const m = await verify(root);
  assert.deepEqual(m.catalog, [a]);
  assert.equal(m.objects.length, 2);
});
test("capture refuses an existing destination", async () => {
  const { root } = await fixture();
  await assert.rejects(capture(root, Buffer.from("x"), [], () => {}));
});
test("missing completion manifest fails closed", async () => {
  const { root } = await fixture();
  await unlink(join(root, "manifest.json"));
  await assert.rejects(verify(root));
});
test("changed dump is rejected before restoration", async () => {
  const { root } = await fixture();
  await writeFile(join(root, "database.dump"), "corrupt");
  await assert.rejects(verify(root));
});
test("missing and corrupt object bytes are rejected", async () => {
  for (const missing of [true, false]) {
    const { root } = await fixture();
    const m = await verify(root),
      p = join(root, m.objects[1].file);
    if (missing) await unlink(p);
    else await writeFile(p, "corrupt");
    await assert.rejects(verify(root));
  }
});
test("symlinked archive files are rejected", async () => {
  const { root, parent } = await fixture();
  const p = join(root, "database.dump"),
    bytes = await readFile(p),
    outside = join(parent, "outside");
  await writeFile(outside, bytes);
  await unlink(p);
  await symlink(outside, p);
  await assert.rejects(verify(root));
});
test("path traversal and duplicate object keys are rejected", async () => {
  for (const duplicate of [true, false]) {
    const { root } = await fixture();
    const m = await verify(root);
    if (duplicate) m.objects.push(m.objects[0]);
    else m.objects[0].file = "../outside";
    await writeFile(join(root, "manifest.json"), JSON.stringify(m));
    await assert.rejects(verify(root));
  }
});
test("partial upload stays pending without publishing bytes", async () => {
  const { parent, a } = await fixture();
  a.status = "pending";
  a.proof = null;
  await capture(join(parent, "pending"), Buffer.from("dump"), [a], () => {
    throw Error("must not read pending object");
  });
  assert.equal(
    (await verify(join(parent, "pending"))).catalog[0].status,
    "pending",
  );
});
test("artifact corruption cannot be hidden by only updating file checksums", async () => {
  const { root } = await fixture();
  const m = await verify(root),
    e = m.objects[1],
    b = Buffer.alloc(e.size, 120);
  await writeFile(join(root, e.file), b);
  e.sha256 = hash(b);
  await writeFile(join(root, "manifest.json"), JSON.stringify(m));
  await assert.rejects(verify(root));
});
test("oversized dump rejected before directory creation", async () => {
  const { parent } = await fixture();
  await assert.rejects(
    capture(join(parent, "too-big"), { length: MAX_DUMP + 1 }, [], () => {}),
  );
});
