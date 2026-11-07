// Bounded reference archive for trusted-operator PostgreSQL dumps and immutable
// harness objects. Checksums detect corruption, NOT malicious manifest replacement.
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdir, writeFile, readFile, lstat } from "node:fs/promises";
import { join } from "node:path";
export const MAX_TOTAL = 128 * 1024 * 1024,
  MAX_DUMP = 64 * 1024 * 1024;
export const hash = (b) => createHash("sha256").update(b).digest("hex");
const digest = (s) => typeof s === "string" && /^[a-f0-9]{64}$/.test(s);
const safe = (s) => typeof s === "string" && /^[a-zA-Z0-9_-]{1,80}$/.test(s);
export async function privateWrite(path, bytes) {
  await writeFile(path, bytes, { mode: 0o600, flag: "wx" });
}
async function file(root, entry, max) {
  assert.ok(
    /^(database\.dump|objects\/[a-f0-9]{64})$/.test(entry.file),
    "Invalid archive path",
  );
  assert.ok(
    Number.isSafeInteger(entry.size) &&
      entry.size >= 0 &&
      entry.size <= max &&
      digest(entry.sha256),
    "Invalid file descriptor",
  );
  const path = join(root, entry.file),
    st = await lstat(path);
  assert.ok(
    st.isFile() && !st.isSymbolicLink() && st.size === entry.size,
    "Invalid/missing archive file",
  );
  const bytes = await readFile(path);
  assert.equal(hash(bytes), entry.sha256, "Archive checksum mismatch");
  return bytes;
}
function artifact(a) {
  assert.ok(
    safe(a.binding?.deployment) && digest(a.binding.identity),
    "Invalid artifact binding",
  );
  assert.ok(
    safe(a.id) && safe(a.registration?.session_id),
    "Invalid artifact identity",
  );
  assert.equal(
    a.key,
    `${a.binding.deployment}/${a.registration.session_id}/${a.id}`,
  );
  const e = a.registration.expected;
  assert.ok(
    Number.isSafeInteger(e.size) &&
      e.size >= 0 &&
      e.size <= 64 * 1024 * 1024 &&
      digest(e.sha256),
    "Invalid artifact size/hash",
  );
  assert.ok(["ready", "pending"].includes(a.status), "Invalid artifact status");
  if (a.status === "ready") {
    assert.deepEqual(a.proof?.expected, e);
    assert.ok(digest(a.proof?.manifest_sha256));
  } else {
    assert.equal(
      a.proof,
      null,
      "Pending artifact must not carry publication proof",
    );
  }
}
export async function verifyArtifacts(catalog, readObject) {
  assert.ok(
    Array.isArray(catalog) && catalog.length <= 10000,
    "Invalid artifact catalog",
  );
  const seen = new Set();
  for (const a of catalog) {
    artifact(a);
    assert.ok(!seen.has(a.id), "Duplicate artifact");
    seen.add(a.id);
    if (a.status !== "ready") continue; // Incomplete uploads stay pending, never promoted.
    const bytes = await readObject(a.key + "/manifest", 16384);
    assert.equal(
      hash(bytes),
      a.proof.manifest_sha256,
      "Artifact manifest checksum mismatch",
    );
    const m = JSON.parse(bytes);
    assert.equal(m.version, 1);
    assert.deepEqual(m.expected, a.registration.expected);
    assert.ok(Array.isArray(m.parts) && m.parts.length <= 64);
    const whole = createHash("sha256");
    let size = 0;
    for (const [n, p] of m.parts.entries()) {
      assert.ok(
        Number.isSafeInteger(p.size) &&
          p.size > 0 &&
          p.size <= 1048576 &&
          digest(p.sha256),
      );
      if (n + 1 < m.parts.length) assert.equal(p.size, 1048576);
      const b = await readObject(
        `${a.key}/part-${String(n).padStart(2, "0")}`,
        p.size,
      );
      assert.equal(b.length, p.size);
      assert.equal(hash(b), p.sha256, "Artifact part checksum mismatch");
      whole.update(b);
      size += b.length;
    }
    assert.equal(size, m.expected.size);
    assert.equal(
      whole.digest("hex"),
      m.expected.sha256,
      "Artifact whole checksum mismatch",
    );
  }
}
export async function capture(root, dump, catalog, readObject) {
  assert.ok(dump.length > 0 && dump.length <= MAX_DUMP, "Dump limit");
  await mkdir(root, { mode: 0o700 });
  await mkdir(join(root, "objects"), { mode: 0o700 });
  const db = { file: "database.dump", size: dump.length, sha256: hash(dump) };
  await privateWrite(join(root, db.file), dump);
  let total = dump.length;
  const objects = [];
  await verifyArtifacts(catalog, async (key, limit) => {
    const bytes = await readObject(key, limit);
    assert.ok(bytes.length <= limit);
    total += bytes.length;
    assert.ok(total <= MAX_TOTAL, "Archive size limit");
    const entry = {
      key,
      file: "objects/" + hash(key),
      size: bytes.length,
      sha256: hash(bytes),
    };
    await privateWrite(join(root, entry.file), bytes);
    objects.push(entry);
    return bytes;
  });
  const manifest = {
    version: 1,
    completed_at: new Date().toISOString(),
    database: db,
    catalog,
    objects,
    total_bytes: total,
    workspace_included: false,
    deployment_secrets_included: false,
  };
  await privateWrite(
    join(root, "manifest.json"),
    JSON.stringify(manifest, null, 2),
  );
  return manifest;
}
export async function verify(root) {
  const dir = await lstat(root),
    objectDir = await lstat(join(root, "objects"));
  assert.ok(
    dir.isDirectory() &&
      !dir.isSymbolicLink() &&
      objectDir.isDirectory() &&
      !objectDir.isSymbolicLink(),
  );
  const st = await lstat(join(root, "manifest.json"));
  assert.ok(
    st.isFile() && !st.isSymbolicLink() && st.size < 8 * 1024 * 1024,
    "Invalid completion manifest",
  );
  const m = JSON.parse(await readFile(join(root, "manifest.json"), "utf8"));
  assert.equal(m.version, 1);
  assert.equal(m.workspace_included, false);
  assert.equal(m.deployment_secrets_included, false);
  assert.equal(m.database.file, "database.dump");
  await file(root, m.database, MAX_DUMP);
  assert.ok(Array.isArray(m.objects) && m.objects.length <= 650000);
  const index = new Map();
  let total = m.database.size;
  for (const e of m.objects) {
    assert.ok(
      typeof e.key === "string" && e.key.length <= 260 && !index.has(e.key),
      "Duplicate/invalid object",
    );
    assert.equal(e.file, "objects/" + hash(e.key));
    index.set(e.key, e);
    total += e.size;
    assert.ok(
      Number.isSafeInteger(total) && total <= MAX_TOTAL,
      "Archive size limit",
    );
  }
  assert.equal(total, m.total_bytes);
  const consumed = new Set();
  await verifyArtifacts(m.catalog, async (key, max) => {
    const e = index.get(key);
    assert.ok(e, "Missing object");
    consumed.add(key);
    return file(root, e, max);
  });
  assert.equal(consumed.size, index.size, "Unexpected archive objects");
  return m;
}
