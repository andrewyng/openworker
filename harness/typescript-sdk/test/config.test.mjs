import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, mkdir, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { resolveOptions, hostConfig, agentWire } from "../dist/config.js";

test("trusted coworker packages preserve policy and server registration", () => {
  const root = join(tmpdir(), "ow-package-config");
  const paths = {
    bundle: join(root, "bundle"),
    servers: join(root, "servers.json"),
    toolPolicy: join(root, "tools.json"),
    runtimePolicy: join(root, "policy.json"),
  };
  const options = {
    executable: join(root, "harness"),
    stateDir: join(root, "state"),
    coworkers: [{ ...paths, model: "model", provider: "anthropic" }],
  };
  assert.deepEqual(hostConfig(options).coworkers, [
    {
      bundle: paths.bundle,
      model: "model",
      provider: "anthropic",
      servers: paths.servers,
      tool_policy: paths.toolPolicy,
      runtime_policy: paths.runtimePolicy,
    },
  ]);
  assert.throws(
    () =>
      hostConfig({
        ...options,
        coworkers: [{ ...options.coworkers[0], bundle: "relative" }],
      }),
    { code: "InvalidInput" },
  );
});

for (const kind of ["directory", "file", "resources"])
  test(`bundled executable discovery: ${kind}`, async (t) => {
    const root = await mkdtemp(join(tmpdir(), "ow-sdk-path-"));
    const original = process.argv[1],
      environment = process.env.OPENWORKER_EXECUTABLE;
    const resources = Object.getOwnPropertyDescriptor(process, "resourcesPath");
    t.after(async () => {
      process.argv[1] = original;
      if (environment === undefined) delete process.env.OPENWORKER_EXECUTABLE;
      else process.env.OPENWORKER_EXECUTABLE = environment;
      if (resources) Object.defineProperty(process, "resourcesPath", resources);
      else delete process.resourcesPath;
      await rm(root, { recursive: true, force: true });
    });
    delete process.env.OPENWORKER_EXECUTABLE;
    const bin = join(root, "bin");
    await mkdir(bin);
    const runtime = join(
      bin,
      process.platform === "win32"
        ? "openworker-harness.exe"
        : "openworker-harness",
    );
    await writeFile(runtime, "discovery fixture; never executed");
    await writeFile(join(root, "main.mjs"), "");
    process.argv[1] = kind === "directory" ? root : join(root, "main.mjs");
    Object.defineProperty(process, "resourcesPath", {
      value: kind === "resources" ? bin : undefined,
      configurable: true,
    });
    assert.equal(
      resolveOptions({ appId: "discovery-test" }).executable,
      runtime,
    );
  });

 test("agent budgets retain their typed wire values",()=>{const limits={maxModelCalls:40,maxOutputTokens:64000,summaryTokens:512};const wire=agentWire({id:"builder",instructions:"Build",tools:["coding.read_file"],limits});assert.deepEqual(wire.limits,limits);});
