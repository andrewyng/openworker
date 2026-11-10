import { test } from "node:test";
import assert from "node:assert/strict";
import {
  mkdtemp,
  mkdir,
  readFile,
  writeFile,
  rm,
  symlink,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { buildPartner, parseArgs } from "./build-sdk.mjs";

const sdkRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const toml = (value) => JSON.stringify(value.replaceAll("\\", "/"));
async function fixture(t, { binary = false, wrongSdk = false } = {}) {
  const root = await mkdtemp(join(tmpdir(), "ow-partner-unit-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  const tools = join(root, "tools with spaces");
  await mkdir(join(tools, "src"), { recursive: true });
  const dependency = wrongSdk
    ? 'openworker-tools = "0.1"'
    : `openworker-tools = { path = ${toml(join(sdkRoot, "tools-sdk"))} }`;
  await writeFile(
    join(tools, "Cargo.toml"),
    `[package]\nname = "custom-tool-fixture"\nversion = "0.1.0"\nedition = "2021"\n[dependencies]\n${dependency}\n`,
  );
  await writeFile(
    join(tools, "src", binary ? "main.rs" : "lib.rs"),
    binary
      ? "fn main() {}"
      : "// Generation-only fixture; compilation is covered by the real-host test.\n",
  );
  return {
    root,
    tools,
    options: {
      name: "acme",
      toolsCrate: tools,
      sdkRoot,
      outputDir: join(root, "output with spaces"),
      generateOnly: true,
    },
  };
}

test("validates portable names, required values and duplicate arguments", () => {
  const args = [
    "--name",
    "acme-tools",
    "--tools-crate",
    "/tools",
    "--output-dir",
    "/out",
  ];
  assert.equal(parseArgs(args).name, "acme-tools");
  for (const name of [
    "../escape",
    "Bad Name",
    "con",
    'x"\n[dependencies]',
    "x".repeat(41),
  ]) {
    assert.throws(() =>
      parseArgs(["--name", name, "--tools-crate", "/t", "--output-dir", "/o"]),
    );
  }
  assert.throws(() => parseArgs([...args, "--name", "other"]));
  assert.throws(() => parseArgs([...args, "--force"]));
  assert.throws(() => parseArgs(["--name", "acme"]));
});
test("generates matching wrapper and runtime without modifying input sources", async (t) => {
  const { tools, options } = await fixture(t);
  const before = await readFile(join(tools, "Cargo.toml"), "utf8");
  const result = await buildPartner(options);
  assert.equal(result.built, false);
  assert.equal(result.executable, null);
  assert.match(
    await readFile(join(result.directory, "OPENWORKER-LICENSE"), "utf8"),
    /MIT License/,
  );
  const wrapper = await readFile(
    join(result.directory, "sdk/src/lib.rs"),
    "utf8",
  );
  assert.ok(wrapper.includes("partner_tools::register(&mut registry)"));
  assert.ok(wrapper.includes("Deployment::Embedded"));
  assert.ok(wrapper.includes("Deployment::Subprocess"));
  const main = await readFile(
    join(result.directory, "runtime/src/main.rs"),
    "utf8",
  );
  assert.ok(main.includes("acme_sdk::registry()"));
  assert.ok(main.includes("acme_sdk::cli::main_with_registry(registry)"));
  const manifest = await readFile(
    join(result.directory, "sdk/Cargo.toml"),
    "utf8",
  );
  assert.ok(manifest.includes("custom-tool-fixture"));
  assert.ok(manifest.includes("tools with spaces"));
  assert.equal(await readFile(join(tools, "Cargo.toml"), "utf8"), before);
  await assert.rejects(readFile(join(tools, "Cargo.lock")), { code: "ENOENT" });
  await assert.rejects(readFile(join(result.directory, "bin/acme-harness")), {
    code: "ENOENT",
  });
  await assert.rejects(buildPartner(options), /overwrite/);
});
test("rejects non-library crates and mismatched Tool SDK identities", async (t) => {
  const binary = await fixture(t, { binary: true });
  await assert.rejects(buildPartner(binary.options), /library crate/);
  const wrong = await fixture(t, { wrongSdk: true });
  await assert.rejects(
    buildPartner(wrong.options),
    /ToolRegistry crate identities/,
  );
});
test("refuses output links, input descendants and resolved input descendants", async (t) => {
  const { root, tools, options } = await fixture(t);
  const link = join(root, "existing-link");
  await symlink(join(root, "missing"), link);
  await assert.rejects(
    buildPartner({ ...options, outputDir: link }),
    /overwrite/,
  );
  await assert.rejects(
    buildPartner({
      ...options,
      outputDir: join(tools, "..hidden", "generated"),
    }),
    /inside an input crate/,
  );
  const parent = join(root, "parent-link");
  await symlink(tools, parent);
  await assert.rejects(
    buildPartner({ ...options, outputDir: join(parent, "generated") }),
    /inside an input crate/,
  );
  await assert.rejects(
    buildPartner({
      ...options,
      outputDir: join(parent, "new-subdir", "generated"),
    }),
    /inside an input crate/,
  );
  await assert.rejects(readFile(join(tools, "new-subdir")), { code: "ENOENT" });
});
test("concurrent generation never overwrites another result", async (t) => {
  const { options } = await fixture(t);
  const results = await Promise.allSettled([
    buildPartner(options),
    buildPartner(options),
  ]);
  assert.equal(results.filter((r) => r.status === "fulfilled").length, 1);
  const info = JSON.parse(
    await readFile(join(options.outputDir, "build-info.json"), "utf8"),
  );
  assert.equal(info.name, "acme");
  assert.equal(info.built, false);
});
