import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, "..");
const manifest = JSON.parse(readFileSync(join(root, "manifest.json"), "utf8"));
const worker = readFileSync(join(root, "service-worker.js"), "utf8");
const popup = readFileSync(join(root, "popup.html"), "utf8") + readFileSync(join(root, "popup.js"), "utf8");
const nativeTemplate = JSON.parse(
  readFileSync(join(root, "..", "browser-native-host", "com.openworker.browser.json.template"), "utf8"),
);

const digest = createHash("sha256").update(Buffer.from(manifest.key, "base64")).digest("hex").slice(0, 32);
const extensionId = [...digest].map((value) => String.fromCharCode("a".charCodeAt(0) + Number.parseInt(value, 16))).join("");

assert.equal(extensionId, "djnbhkmnbmjobnphflaopcpfkifbgekl");
assert.ok(manifest.permissions.includes("nativeMessaging"));
assert.equal("host_permissions" in manifest, false);
assert.deepEqual(nativeTemplate.allowed_origins, [`chrome-extension://${extensionId}/`]);
assert.match(worker, /connectNative\(NATIVE_HOST\)/);
assert.doesNotMatch(worker + popup + manifest.description, /pairing code|bridge url|chrome or edge|\bedge\b/i);

console.log(`native contract verified for ${extensionId}`);
