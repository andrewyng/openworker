// Real production-package qualification with a local HTTP/SSE model, no live keys.
import { test } from "node:test";
import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { createServer } from "node:http";
import {
  mkdtemp,
  mkdir,
  readFile,
  writeFile,
  copyFile,
  rename,
  rm,
  realpath,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { createHash } from "node:crypto";
import { buildPartner } from "./build-sdk.mjs";
import { Harness } from "../typescript-sdk/dist/index.js";

const here = dirname(fileURLToPath(import.meta.url));
const sdkRoot = resolve(here, "..");
const toml = (value) => JSON.stringify(value.replaceAll("\\", "/"));
const runFile = promisify(execFile);
test(
  "external native tool crate produces SDK and executable usable in Rust and TypeScript",
  { timeout: 900_000 },
  async (t) => {
    const root = await realpath(await mkdtemp(join(tmpdir(), "ow-partner-build-")));
    t.after(() => rm(root, { recursive: true, force: true }));
    let requests = 0;
    let providerError;
    const server = createServer(async (req, res) => {
      try {
        let raw = "";
        for await (const chunk of req) {
          raw += chunk;
          assert.ok(raw.length < 1_000_000);
        }
        const body = JSON.parse(raw);
        assert.equal(req.url, "/v1/responses");
        assert.equal(body.stream, true);
        assert.ok(body.tools.some(tool => tool.name === "partner__note"));
        assert.ok(++requests <= 6, "Unexpected model retry or extra request");
        const done = body.input.some(item => item.type === "function_call_output");
        const output = done
          ? [{type: "message", role: "assistant", content: [{type: "output_text", text: "Partner proof written."}]}]
          : [{type: "function_call", call_id: "partner-note", name: "partner__note", arguments: "{}"}];
        res.writeHead(200, {"Content-Type": "text/event-stream"});
        res.end(`data: ${JSON.stringify({type: "response.completed", response: {status: "completed", output, usage: {input_tokens: 10, output_tokens: 4}}})}\n\n`);
      } catch (error) {
        providerError = error;
        res.writeHead(500);
        res.end();
      }
    });
    await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
    const baseUrl = `http://127.0.0.1:${server.address().port}/v1`;
    t.after(async () => {
      server.closeAllConnections();
      await new Promise(resolve => server.close(resolve));
    });
    const tools = join(root, "custom tools");
    await mkdir(join(tools, "src"), { recursive: true });
    const toolsManifest = `[package]\nname = "independent-partner-tools"\nversion = "0.1.0"\nedition = "2021"\n[dependencies]\nanyhow = "1"\nserde_json = "1"\nopenworker-tools = { path = ${toml(join(sdkRoot, "tools-sdk"))} }\n`;
    await writeFile(join(tools, "Cargo.toml"), toolsManifest);
    await copyFile(
      join(here, "test-fixtures/native-tools.rs"),
      join(tools, "src/lib.rs"),
    );
    const info = await buildPartner({
      name: "fixture",
      toolsCrate: tools,
      sdkRoot,
      outputDir: join(root, "generated package"),
    });
    assert.equal(info.built, true);
    assert.ok(info.compiler.includes("host:"));
    const binary = join(info.directory, info.executable.path);
    assert.equal(
      createHash("sha256")
        .update(await readFile(binary))
        .digest("hex"),
      info.executable.sha256,
    );
    assert.equal(
      createHash("sha256")
        .update(await readFile(join(info.directory, "Cargo.lock")))
        .digest("hex"),
      info.cargo_lock_sha256,
    );
    assert.equal(
      await readFile(join(tools, "Cargo.toml"), "utf8"),
      toolsManifest,
    );
    await assert.rejects(readFile(join(tools, "Cargo.lock")), {
      code: "ENOENT",
    });

    const consumer = join(root, "consumer");
    await mkdir(join(consumer, "src"), { recursive: true });
    await writeFile(
      join(consumer, "Cargo.toml"),
      `[package]\nname = "partner-sdk-consumer"\nversion = "0.1.0"\nedition = "2021"\n[dependencies]\nfixture-sdk = { path = ${toml(join(info.directory, "sdk"))} }\ntokio = { version = "1", features = ["full"] }\n`,
    );
    await copyFile(
      join(here, "test-fixtures/sdk-consumer.rs"),
      join(consumer, "src/main.rs"),
    );
    // Keep Node's event loop free to serve the model while the Rust consumer runs.
    const consumerRun = await runFile(
      "cargo",
      [
        "run",
        "--release",
        "--manifest-path",
        join(consumer, "Cargo.toml"),
        "--",
        info.directory,
        join(root, "rust-tests"),
        baseUrl,
      ],
      {
        cwd: sdkRoot,
        maxBuffer: 10 * 1024 * 1024,
        timeout: 600_000,
      },
    );
    process.stdout.write(consumerRun.stdout);
    process.stderr.write(consumerRun.stderr);

    // Runtime deployment needs the binary, not the custom tool's source directory.
    const install = join(root, "relocated install");
    await mkdir(join(install, "bin"), { recursive: true });
    const relocated = join(install, info.executable.path);
    await copyFile(binary, relocated);
    await rename(tools, join(root, "source unavailable at build path"));
    const work = join(root, "node-work");
    await mkdir(work);
    // Qualification must not enable test-support in a distributable package.
    await assert.rejects(Harness.start({
      executable: relocated,
      stateDir: join(root, "scripted-rejected"),
      sandbox: "off",
      scripted: true,
    }));
    const h = await Harness.start({
      executable: relocated,
      stateDir: join(root, "node-state"),
      sandbox: "off",
      agents: [
        {
          id: "assistant",
          instructions: "Use the test tool.",
          model: { connection: "Fixture", model: "fixture" },
          tools: ["partner.note"],
        },
      ],
    });
    try {
      await h.setModelProvider({
        name: "Fixture", api: "responses", baseUrl,
        credentials: { apiKey: "synthetic-partner-key", storage: "memory" },
      });
      const s = await h.session("assistant", { workspace: work });
      const pending = await s.run("Write the partner proof using your tool.");
      assert.equal(pending.status, "interrupted");
      await assert.rejects(readFile(join(work, "partner-proof.txt")), {
        code: "ENOENT",
      });
      await s.respond(pending.interruptions[0], { decision: "once" });
      assert.equal((await s.resume()).status, "completed");
      assert.equal(
        await readFile(join(work, "partner-proof.txt"), "utf8"),
        "native partner tool\n",
      );
    } finally {
      await h.shutdown();
    }
    console.log(
      "verified relocated binary from TypeScript with native tool source absent",
    );
    assert.equal(providerError, undefined);
    assert.equal(requests, 6, "Embedded Rust, subprocess Rust and TypeScript each used the production provider path");

    // A bad native registration contract must fail the actual build. Never publish
    // a misleading successful wrapper or overwrite partner source to make it work.
    const broken = join(root, "broken tools");
    await mkdir(join(broken, "src"), { recursive: true });
    await writeFile(join(broken, "Cargo.toml"), toolsManifest);
    await writeFile(join(broken, "src/lib.rs"), "pub fn register() {}\n");
    const brokenOutput = join(root, "must-not-publish");
    await assert.rejects(
      buildPartner({
        name: "broken",
        toolsCrate: broken,
        sdkRoot,
        outputDir: brokenOutput,
      }),
      /staging retained/,
    );
    await assert.rejects(readFile(join(brokenOutput, "build-info.json")), {
      code: "ENOENT",
    });
    assert.equal(
      await readFile(join(broken, "src/lib.rs"), "utf8"),
      "pub fn register() {}\n",
    );
  },
);
