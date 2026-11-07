// Opt-in local qualification. No Railway account or live model is contacted.
import test from "node:test";
import assert from "node:assert/strict";
import {
  mkdtemp,
  mkdir,
  readFile,
  writeFile,
  copyFile,
  rm,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, dirname, basename } from "node:path";
import { fileURLToPath } from "node:url";
import { execFileSync, spawnSync } from "node:child_process";
import { randomUUID } from "node:crypto";
const here = dirname(fileURLToPath(import.meta.url));
test(
  "Railway container contract: native discovery, auth, streaming, volume resume and shutdown",
  {
    skip: process.env.OW_RAILWAY_CONTAINER_TEST !== "1",
    timeout: 300000,
  },
  async () => {
    const manifest = JSON.parse(
      await readFile(process.env.OW_SDK_PUBLICATION_MANIFEST, "utf8"),
    );
    const skill = JSON.parse(
      await readFile(join(here, "package.json"), "utf8"),
    );
    const id = "ow-railway-" + randomUUID().slice(0, 8),
      image = id + ":test",
      network = id + "-net",
      volume = id + "-data";
    const first = id + "-first",
      second = id + "-second",
      model = id + "-model";
    const root = await mkdtemp(join(tmpdir(), "ow-railway-")),
      app = join(root, "app");
    const docker = (...args) =>
      execFileSync("docker", args, {
        encoding: "utf8",
        maxBuffer: 8 * 1024 * 1024,
        stdio: ["ignore", "pipe", "pipe"],
      });
    try {
      await mkdir(join(app, "vendor"), { recursive: true });
      const deps = {},
        optional = {};
      for (const p of manifest.packages.filter(
        (p) =>
          p.name === "@openworkerai/harness-sdk" ||
          p.name.startsWith("@openworkerai/harness-runtime-"),
      )) {
        assert.equal(p.version, skill.openworkerSdkVersion);
        await copyFile(p.file, join(app, "vendor", basename(p.file)));
        (p.name === "@openworkerai/harness-sdk" ? deps : optional)[p.name] =
          "file:vendor/" + basename(p.file);
      }
      assert.equal(Object.keys(deps).length, 1);
      assert.equal(Object.keys(optional).length, 6);
      await writeFile(
        join(app, "package.json"),
        JSON.stringify({
          name: "railway-fixture",
          private: true,
          type: "module",
          dependencies: deps,
          optionalDependencies: optional,
          scripts: { build: "node build.mjs" },
        }),
      );
      await copyFile(
        join(here, "test/railway-fixture/server.mjs"),
        join(app, "server.mjs"),
      );
      await writeFile(
        join(app, "build.mjs"),
        "import{mkdir,copyFile}from'node:fs/promises';await mkdir('dist',{recursive:true});await copyFile('server.mjs','dist/server.js');\n",
      );
      execFileSync(
        "npm",
        [
          "install",
          "--package-lock-only",
          "--offline",
          "--ignore-scripts",
          "--no-audit",
          "--no-fund",
          "--cache",
          join(root, "cache"),
        ],
        { cwd: app, stdio: "pipe" },
      );
      await mkdir(join(app, "node_modules"), { recursive: true });
      await writeFile(join(app, "node_modules/host-sentinel"), "must not copy");
      await writeFile(join(app, ".env"), "DO_NOT_COPY=fixture");
      // Supply immutable pre-publication tarballs without substituting the helper.
      // The only Dockerfile adaptation is copying this offline dependency vendor dir.
      const recipe = await readFile(
        join(here, "openworker-apps/assets/railway/Dockerfile"),
        "utf8",
      );
      await writeFile(
        join(app, "Dockerfile"),
        recipe.replace(
          "COPY package.json package-lock.json ./",
          "COPY package.json package-lock.json ./\nCOPY vendor ./vendor",
        ),
      );
      await copyFile(
        join(here, "openworker-apps/assets/railway/.dockerignore"),
        join(app, ".dockerignore"),
      );
      docker("build", "--pull=false", "-t", image, app);
      docker("network", "create", "--internal", network);
      docker("volume", "create", volume);
      docker(
        "run",
        "-d",
        "--name",
        model,
        "--network",
        network,
        "--network-alias",
        "model",
        "--mount",
        `type=bind,src=${join(here, "test/railway-fixture")},dst=/fixture,readonly`,
        "node:24-bookworm-slim",
        "node",
        "/fixture/model.mjs",
      );
      const start = (name) => {
        docker(
          "run",
          "-d",
          "--name",
          name,
          "--network",
          "container:" + model,
          "--user",
          "0:0",
          "--mount",
          `type=volume,src=${volume},dst=/data`,
          "-e",
          "PORT=9092",
          "-e",
          "NO_PROXY=model,localhost,127.0.0.1",
          "-e",
          "OPENAI_API_KEY=fixture-key-not-real",
          "-e",
          "APP_ACCESS_TOKEN=local-fixture-only",
          image,
        );
        return "http://model:9092";
      };
      const request = (url, options = {}) =>
        JSON.parse(
          docker(
            "exec",
            model,
            "node",
            "/fixture/probe.mjs",
            url,
            JSON.stringify(options),
          ),
        );
      const ready = async (url, name) => {
        const until = Date.now() + 30000;
        let error;
        while (Date.now() < until) {
          try {
            const r = request(url + "/health", {
              headers: { host: "healthcheck.railway.app" },
            });
            if (r.status === 200) return;
          } catch (e) {
            error = e;
          }
          if (
            docker("inspect", "--format", "{{.State.Running}}", name).trim() !==
            "true"
          )
            throw Error("Application exited before readiness");
          await new Promise((r) => setTimeout(r, 200));
        }
        throw Error("Readiness failed: " + error);
      };
      const ask = async (url, message) => {
        const r = request(url + "/turn", {
          method: "POST",
          headers: {
            authorization: "Bearer local-fixture-only",
            "content-type": "application/json",
          },
          body: JSON.stringify({ message }),
        });
        assert.equal(r.status, 200);
        assert.equal(
          r.modelCompletedAtFirstChunk,
          false,
          "A delta must arrive before the model finishes",
        );
        const events = r.text
          .trim()
          .split("\n")
          .map((line) => JSON.parse(line));
        assert.ok(events.some((e) => e.type === "delta"));
        assert.equal(events.at(-1).status, "completed");
        return events.at(-1).text;
      };
      let url = start(first);
      await ready(url, first);
      assert.equal(
        request(url + "/turn", { method: "POST", body: "{}" }).status,
        401,
      );
      assert.equal(await ask(url, "FIRST_MESSAGE"), "first-done");
      const reference = docker("exec", first, "cat", "/data/app/session.json");
      assert.match(
        docker(
          "exec",
          first,
          "node",
          "--input-type=module",
          "-e",
          "import{resolveRuntimeExecutable}from'@openworkerai/harness-sdk';console.log(resolveRuntimeExecutable())",
        ),
        /harness-runtime-linux-/,
      );
      docker(
        "exec",
        first,
        "node",
        "--input-type=module",
        "-e",
        "import{existsSync}from'node:fs';if(existsSync('/app/.env')||existsSync('/app/node_modules/host-sentinel'))process.exit(1)",
      );
      docker("stop", "--time", "15", first);
      assert.equal(
        docker("inspect", "--format", "{{.State.ExitCode}}", first).trim(),
        "0",
      );
      assert.match(docker("logs", first), /shutdown complete/);
      docker("rm", first);
      url = start(second);
      await ready(url, second);
      assert.equal(
        docker("exec", second, "cat", "/data/app/session.json"),
        reference,
      );
      assert.equal(await ask(url, "SECOND_MESSAGE"), "history-restored");
      docker("stop", "--time", "15", second);
      assert.equal(
        docker("inspect", "--format", "{{.State.ExitCode}}", second).trim(),
        "0",
      );
    } catch (error) {
      for (const name of [first, second, model]) {
        const logs = spawnSync("docker", ["logs", name], { encoding: "utf8" });
        if (logs.status === 0) console.error(name, logs.stdout, logs.stderr);
      }
      throw error;
    } finally {
      for (const name of [first, second, model])
        spawnSync("docker", ["rm", "-f", name], { stdio: "ignore" });
      spawnSync("docker", ["volume", "rm", volume], { stdio: "ignore" });
      spawnSync("docker", ["network", "rm", network], { stdio: "ignore" });
      spawnSync("docker", ["image", "rm", image], { stdio: "ignore" });
      await rm(root, { recursive: true, force: true });
    }
  },
);
