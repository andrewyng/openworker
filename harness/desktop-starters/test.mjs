import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { spawnSync } from "node:child_process";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { runtimeArch } from "./templates/shared/scripts/runtime-identity.mjs";
const here = dirname(fileURLToPath(import.meta.url));
test("packaging follows the native helper architecture, not an emulated Node process", () => {
  const pe = Buffer.alloc(128);
  pe.write("MZ");
  pe.writeUInt32LE(80, 60);
  pe.write("PE\0\0", 80);
  pe.writeUInt16LE(0xaa64, 84);
  assert.equal(runtimeArch(pe, "win32"), "arm64");
  pe.writeUInt16LE(0x8664, 84);
  assert.equal(runtimeArch(pe, "win32"), "x64");
  const macho = Buffer.alloc(8);
  macho.writeUInt32LE(0xfeedfacf);
  macho.writeUInt32LE(0x100000c, 4);
  assert.equal(runtimeArch(macho, "darwin"), "arm64");
  assert.throws(() => runtimeArch(pe, "darwin"));
  assert.throws(() => runtimeArch(Buffer.from("not a native helper"), "win32"));
});
for (const template of ["electron-game-builder", "tauri-game-builder"])
  test(
    template + " scaffolds a bounded desktop app without overwriting files",
    async (t) => {
      const root = await mkdtemp(join(tmpdir(), "ow-starter-"));
      t.after(() => rm(root, { recursive: true, force: true }));
      const app = join(root, "arcade-test");
      const result = spawnSync(
        process.execPath,
        [join(here, "create.mjs"), app, "--template", template],
        { encoding: "utf8" },
      );
      assert.equal(result.status, 0, result.stderr);
      const package_ = JSON.parse(
        await readFile(join(app, "package.json"), "utf8"),
      );
      assert.equal(package_.name, "arcade-test");
      assert.ok(package_.scripts.dev);
      assert.ok(package_.scripts.package);
      const ui = await readFile(join(app, "src/main.tsx"), "utf8");
      assert.ok(ui.includes('sandbox="allow-scripts"'));
      assert.ok(!ui.includes("allow-same-origin"));
      assert.ok(
        (await readFile(join(app, "index.html"), "utf8")).includes(
          "frame-src arcade-preview:",
        ),
      );
      if (template === "electron-game-builder") {
        const main = await readFile(join(app, "electron/main.mjs"), "utf8");
        assert.match(main, /contextIsolation:\s*true/);
        assert.match(main, /nodeIntegration:\s*false/);
        assert.ok(main.includes("senderFrame"));
        assert.equal(
          JSON.parse(await readFile(join(app, "electron-builder.json"), "utf8"))
            .appId,
          "com.openworker.arcade-test",
        );
      } else {
        const config = JSON.parse(
          await readFile(join(app, "src-tauri/tauri.conf.json"), "utf8"),
        );
        assert.equal(config.identifier, "com.openworker.arcade-test");
        assert.ok(
          config.app.security.capabilities[0].permissions.includes(
            "allow-action",
          ),
        );
        assert.ok(
          !(await readFile(join(app, "src-tauri/Cargo.toml"), "utf8")).includes(
            "__SDK_PATH__",
          ),
        );
      }
      await writeFile(join(app, "keep.txt"), "untouched");
      assert.notEqual(
        spawnSync(process.execPath, [join(here, "create.mjs"), app]).status,
        0,
      );
      assert.equal(await readFile(join(app, "keep.txt"), "utf8"), "untouched");
    },
  );
