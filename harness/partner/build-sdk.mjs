#!/usr/bin/env node
// Build-time partner tooling. Never invoked by an application/harness at runtime.
import { execFileSync } from "node:child_process";
import { createHash, randomUUID } from "node:crypto";
import {
  readFile,
  writeFile,
  mkdir,
  mkdtemp,
  lstat,
  realpath,
  rename,
  copyFile,
} from "node:fs/promises";
import {
  dirname,
  resolve,
  relative,
  join,
  basename,
  isAbsolute,
} from "node:path";
import { fileURLToPath } from "node:url";

const usage =
  "node harness/partner/build-sdk.mjs --name NAME --tools-crate PATH --output-dir PATH [--sdk-root PATH] [--generate-only]";
const quote = (value) => JSON.stringify(value); // TOML basic strings for validated filesystem/name inputs.
const portable = (value) => value.replaceAll("\\", "/");
const hash = (bytes) => createHash("sha256").update(bytes).digest("hex");
function inside(root, path) {
  const rel = portable(relative(root, path));
  return !rel || (rel !== ".." && !rel.startsWith("../") && !isAbsolute(rel));
}

export function parseArgs(args) {
  const result = { generateOnly: false };
  const keys = new Map([
    ["--name", "name"],
    ["--tools-crate", "toolsCrate"],
    ["--output-dir", "outputDir"],
    ["--sdk-root", "sdkRoot"],
  ]);
  const seen = new Set();
  for (let i = 0; i < args.length; i++) {
    const flag = args[i];
    if (seen.has(flag)) throw Error(`Duplicate option: ${flag}`);
    seen.add(flag);
    if (flag === "--generate-only") {
      result.generateOnly = true;
      continue;
    }
    const key = keys.get(flag);
    if (!key || !args[i + 1] || args[i + 1].startsWith("--"))
      throw Error(usage);
    result[key] = args[++i];
  }
  if (!result.name || !result.toolsCrate || !result.outputDir)
    throw Error(usage);
  if (
    !/^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$/.test(result.name) ||
    result.name.length > 40 ||
    /^(con|prn|aux|nul|com[0-9]|lpt[0-9])$/.test(result.name)
  )
    throw Error(
      "Name must be a portable lower-case Cargo package prefix (max 40 characters)",
    );
  return result;
}
function cargo(args, cwd, options = {}) {
  return execFileSync("cargo", args, {
    cwd,
    encoding: "utf8",
    maxBuffer: 16 * 1024 * 1024,
    timeout: 900_000,
    ...options,
  });
}
async function exists(path) {
  try {
    await lstat(path);
    return true;
  } catch (e) {
    if (e.code === "ENOENT") return false;
    throw e;
  }
}
// Resolve an as-yet-uncreated parent without first writing through a symlink
// into an input crate. Missing suffix components are appended after resolution.
async function prospectivePath(path) {
  let current = path;
  const suffix = [];
  while (!(await exists(current))) {
    const parent = dirname(current);
    if (parent === current) throw Error("Cannot resolve output parent");
    suffix.unshift(basename(current));
    current = parent;
  }
  return join(await realpath(current), ...suffix);
}
async function packageAt(path, cwd) {
  const manifest = await realpath(join(path, "Cargo.toml"));
  const metadata = JSON.parse(
    cargo(
      [
        "metadata",
        "--no-deps",
        "--format-version",
        "1",
        "--manifest-path",
        manifest,
      ],
      cwd,
    ),
  );
  const pkg = metadata.packages.find(
    (p) => resolve(p.manifest_path) === manifest,
  );
  if (
    !pkg ||
    !pkg.targets.some((t) => t.kind.includes("lib") || t.kind.includes("rlib"))
  )
    throw Error(
      "Expected a Cargo library crate, not a binary-only crate or virtual workspace",
    );
  return { pkg, manifest };
}
function gitInput(path) {
  try {
    const revision = execFileSync("git", ["-C", path, "rev-parse", "HEAD"], {
      encoding: "utf8",
      stdio: ["ignore", "pipe", "ignore"],
    }).trim();
    const dirty = Boolean(
      execFileSync("git", ["-C", path, "status", "--porcelain"], {
        encoding: "utf8",
        stdio: ["ignore", "pipe", "ignore"],
      }).trim(),
    );
    return { revision, dirty };
  } catch {
    return null;
  }
}
const wrapperSource = `//! Generated partner convenience layer. All runtime behavior stays in OpenWorker.
pub use openworker_sdk::*;
pub mod agents { pub use openworker_sdk::agents::*; }
use std::path::{Path, PathBuf};

#[derive(Clone, Copy, Debug)]
pub enum Deployment { Embedded, Subprocess }

/// Build the same native registry for embedding and the companion executable.
pub fn registry() -> anyhow::Result<ToolRegistry> {
    let mut registry = ToolRegistry::default();
    partner_tools::register(&mut registry)?;
    Ok(registry)
}

/// Construct host configuration. Add selected agents; set model connections after startup.
pub fn config(state_dir: PathBuf, package_dir: &Path) -> HostConfig {
    HostConfig {
        state_dir,
        runner: package_dir.join("bin").join(RUNTIME_NAME),
        storage: Storage::Sqlite,
        coworkers: vec![],
        agents: vec![],
        sandbox: Sandbox::Openshell,
        scripted: false,
        telemetry: None,
    }
}

/// Does not compile tools, provision Docker, migrate PostgreSQL or supply credentials.
pub async fn start(config: HostConfig, deployment: Deployment) -> Result<agents::Harness> {
    let client = match deployment {
        Deployment::Embedded => HarnessClient::embedded(
            config,
            registry().map_err(|_| Error::Setup("Partner tool registration failed".into()))?,
            ClientOptions::default(),
        ).await?,
        Deployment::Subprocess => HarnessClient::managed_stdio(
            HostProcessConfig {
                program: config.runner.clone(), host: config,
                restart: RestartPolicy::default(),
            }, ClientOptions::default(),
        ).await?,
    };
    // Preserve deterministic cleanup if the typed facade handshake fails.
    match agents::Harness::connect(client.clone()).await {
        Ok(harness) => Ok(harness),
        Err(error) => { let _ = client.shutdown().await; Err(error) }
    }
}
`;

export async function buildPartner(options) {
  // Revalidate programmatic callers too.
  parseArgs([
    "--name",
    options.name ?? "",
    "--tools-crate",
    options.toolsCrate ?? "",
    "--output-dir",
    options.outputDir ?? "",
  ]);
  const sdkRoot = await realpath(
    options.sdkRoot ?? resolve(dirname(fileURLToPath(import.meta.url)), ".."),
  );
  const toolsRoot = await realpath(options.toolsCrate);
  const output = resolve(options.outputDir);
  if (await exists(output))
    throw Error("Refusing to overwrite an existing output directory or link");
  // Prevent generated files/target directories from appearing inside input sources.
  const inputs = await Promise.all(
    [toolsRoot, join(sdkRoot, "harness-sdk"), join(sdkRoot, "tools-sdk")].map(
      (path) => realpath(path),
    ),
  );
  if (inputs.some((source) => inside(source, output)))
    throw Error("Output must not be inside an input crate");
  if (process.env.CARGO_BUILD_TARGET)
    throw Error(
      "This generator currently supports native builds only; unset CARGO_BUILD_TARGET",
    );
  const { pkg: tool, manifest: toolManifest } = await packageAt(
    toolsRoot,
    sdkRoot,
  );
  const { pkg: sdk } = await packageAt(join(sdkRoot, "harness-sdk"), sdkRoot);
  const { pkg: toolsSdk } = await packageAt(
    join(sdkRoot, "tools-sdk"),
    sdkRoot,
  );
  if (sdk.name !== "openworker-sdk" || toolsSdk.name !== "openworker-tools")
    throw Error("sdk-root must be the OpenWorker native workspace");
  const toolDep = tool.dependencies.find(
    (d) => d.name === "openworker-tools" && d.kind !== "dev",
  );
  if (
    !toolDep?.path ||
    (await realpath(toolDep.path)) !==
      (await realpath(join(sdkRoot, "tools-sdk")))
  ) {
    throw Error(
      "The tools crate must depend on openworker-tools from this SDK checkout via a path dependency. Git/registry source packaging is not supported by this development generator yet; do not mix ToolRegistry crate identities.",
    );
  }
  const parent = await prospectivePath(dirname(output));
  const destination = join(parent, basename(output));
  if (inputs.some((source) => inside(source, destination)))
    throw Error("Resolved output must not be inside an input crate");
  if (await exists(destination)) throw Error("Output already exists");
  await mkdir(parent, { recursive: true });
  const staging = await mkdtemp(join(parent, ".openworker-partner-"));
  try {
    const sdkPackage = `${options.name}-sdk`;
    const runtimePackage = `${options.name}-harness`;
    const crateAlias = sdkPackage.replaceAll("-", "_");
    const executableName =
      runtimePackage + (process.platform === "win32" ? ".exe" : "");
    // A shared CARGO_TARGET_DIR can otherwise replace target/release/NAME
    // between Cargo exiting and our copy. Publish under the stable public name,
    // but use a unique intermediate binary target for each generated workspace.
    const cargoBinary = `${runtimePackage}-${randomUUID().replaceAll("-", "")}`;
    const cargoExecutable =
      cargoBinary + (process.platform === "win32" ? ".exe" : "");
    const depPath = (path) =>
      quote(portable(relative(join(staging, "sdk"), path)));
    await mkdir(join(staging, "sdk", "src"), { recursive: true });
    await mkdir(join(staging, "runtime", "src"), { recursive: true });
    await writeFile(
      join(staging, "Cargo.toml"),
      `[workspace]\nmembers = ["sdk", "runtime"]\nresolver = "2"\n`,
    );
    await writeFile(
      join(staging, "sdk", "Cargo.toml"),
      `[package]\nname = ${quote(sdkPackage)}\nversion = "0.1.0"\nedition = "2021"\nrust-version = "1.96"\npublish = false\n\n[dependencies]\nanyhow = "1"\nopenworker-sdk = { path = ${depPath(join(sdkRoot, "harness-sdk"))} }\npartner-tools = { package = ${quote(tool.name)}, path = ${depPath(toolsRoot)} }\n`,
    );
    await writeFile(
      join(staging, "sdk", "src", "lib.rs"),
      `${wrapperSource}\nconst RUNTIME_NAME: &str = ${quote(executableName)};\n`,
    );
    await writeFile(
      join(staging, "runtime", "Cargo.toml"),
      `[package]\nname = ${quote(runtimePackage)}\nversion = "0.1.0"\nedition = "2021"\nrust-version = "1.96"\npublish = false\n\n[[bin]]\nname = ${quote(cargoBinary)}\npath = "src/main.rs"\n\n[dependencies]\n${sdkPackage} = { path = "../sdk" }\n`,
    );
    await writeFile(
      join(staging, "runtime", "src", "main.rs"),
      `fn main() {\n    let registry = ${crateAlias}::registry().expect("Partner tool registration failed");\n    ${crateAlias}::cli::main_with_registry(registry);\n}\n`,
    );
    await copyFile(
      join(sdkRoot, "rust-toolchain.toml"),
      join(staging, "rust-toolchain.toml"),
    );
    await copyFile(
      join(sdkRoot, "..", "LICENSE"),
      join(staging, "OPENWORKER-LICENSE"),
    );
    await writeFile(join(staging, ".gitignore"), "/target/\n/bin/\n");
    await writeFile(
      join(staging, "README.md"),
      readme(options.name, executableName, tool.name),
    );
    const buildInfo = {
      format_version: 1,
      name: options.name,
      built: false,
      sdk: {
        package: sdk.name,
        version: sdk.version,
        path: portable(relative(staging, sdkRoot)),
        git: gitInput(sdkRoot),
      },
      tools: {
        package: tool.name,
        version: tool.version,
        path: portable(relative(staging, toolsRoot)),
        git: gitInput(toolsRoot),
        manifest_sha256: hash(await readFile(toolManifest)),
      },
      host_platform: process.platform,
      host_arch: process.arch,
      compiler: null,
      cargo_binary: cargoBinary,
      executable: null,
      cargo_lock_sha256: null,
      distribution:
        "development path-dependency workspace; not a relocatable SDK source archive or signed release",
    };
    if (!options.generateOnly) {
      buildInfo.compiler = execFileSync("rustc", ["-vV"], {
        cwd: staging,
        encoding: "utf8",
        timeout: 60_000,
      }).trim();
      // Preserve the SDK's qualified dependency versions where compatible; add
      // partner/workspace dependencies, then verify a fully locked rebuild.
      await copyFile(join(sdkRoot, "Cargo.lock"), join(staging, "Cargo.lock"));
      cargo(
        [
          "build",
          "--release",
          "--workspace",
          "--manifest-path",
          join(staging, "Cargo.toml"),
        ],
        staging,
        { stdio: "inherit" },
      );
      cargo(
        [
          "build",
          "--locked",
          "--release",
          "--workspace",
          "--manifest-path",
          join(staging, "Cargo.toml"),
        ],
        staging,
        { stdio: "inherit" },
      );
      const metadata = JSON.parse(
        cargo(
          ["metadata", "--locked", "--no-deps", "--format-version", "1"],
          staging,
        ),
      );
      const executable = join(
        metadata.target_directory,
        "release",
        cargoExecutable,
      );
      await mkdir(join(staging, "bin"));
      await copyFile(executable, join(staging, "bin", executableName));
      buildInfo.built = true;
      buildInfo.executable = {
        path: `bin/${executableName}`,
        sha256: hash(await readFile(executable)),
      };
      buildInfo.cargo_lock_sha256 = hash(
        await readFile(join(staging, "Cargo.lock")),
      );
    }
    await writeFile(
      join(staging, "build-info.json"),
      JSON.stringify(buildInfo, null, 2) + "\n",
    );
    // Cooperative output reservation: never rename over a concurrent builder's result.
    // mkdir without recursive fails even for an empty existing directory or symlink.
    await mkdir(destination);
    // Publish entries individually into the directory exclusively reserved above.
    // A process crash can leave a partial output; built manifest is published last.
    const { readdir, rmdir } = await import("node:fs/promises");
    const entries = (await readdir(staging)).filter(
      (n) => n !== "build-info.json",
    );
    for (const entry of entries)
      await rename(join(staging, entry), join(destination, entry));
    await rename(
      join(staging, "build-info.json"),
      join(destination, "build-info.json"),
    );
    await rmdir(staging);
    return { directory: destination, ...buildInfo };
  } catch (error) {
    throw new Error(
      `Partner build failed; staging retained at ${staging}. ${error instanceof Error ? error.message : "Inspect Cargo diagnostics"}`,
    );
  }
}

function readme(name, executable, tools) {
  const crate = `${name.replaceAll("-", "_")}_sdk`;
  return `# ${name} partner SDK (generated development workspace)

This package reuses OpenWorker and the existing ${tools} crate. It contains no
forked agent loop, policy engine or storage adapter. Cargo builds tools at build
time only. The application does not need Cargo to launch the compiled runtime.

## Rust application

Add a path dependency on the generated sdk directory, then:

\`\`\`rust,ignore
use ${crate}::{agents::Agent, Deployment};
let mut config = ${crate}::config(state_dir, &package_dir);
config.agents = vec![Agent::new("assistant")
    .instructions("Help the user.")
    .tool("your_namespace.your_tool")]; // Select an actually registered tool.
let harness = ${crate}::start(config, Deployment::Subprocess).await?;
// Use Deployment::Embedded to run the same registered tools in this application.
// Supply model connections, create sessions and handle approvals with the normal SDK.
harness.shutdown().await?;
\`\`\`

package_dir must be the absolute directory containing bin/${executable}.
state_dir must be an absolute private state directory, separate from workspaces.
The default sandbox is OpenShell; it requires provisioning. Native tools remain
trusted host code, not sandboxed code. Explicit sandbox-off is for trusted testing.
Embedded mode still needs the helper executable. PostgreSQL migration and model
credentials remain explicit app/operator responsibilities. Do not put keys in
agent definitions, build-info or launch arguments.

## TypeScript application

Use @openworker/harness with executable pointing to bin/${executable}. Select the
same native qualified tool names in agents. TypeScript does not reimplement or
wrap those native tools as MCP. TypeScript-authored tools still use MCP.

## Build / distribution

Check build-info.json: built=false means source generation only. To build generated
source manually, use cargo build --release --workspace and install the resulting
executable into bin; regenerate metadata if distributing it. Prefer rerunning the
generator to a NEW output directory for an updated complete package.

The wrapper uses relative PATH dependencies on your existing tool source and SDK
checkout; keep that source layout when checking it into your partner workspace.
This is not a standalone published crate or independently relocatable source SDK.
Applications consuming the wrapper retain their own Cargo.lock. The compiled
runtime can be deployed without its source tree, subject to platform libraries
and a matching target OS/architecture. No cross-compilation, signing, installer,
full SBOM/license audit or kernel isolation is provided by this generator.
OPENWORKER-LICENSE contains the upstream license notice; retain it with the
runtime. It does not replace your own tools' and other dependencies' notices.

The wrapper and runtime use the same registry factory. Do not register native
functions in a parent process and expect them to be transferred to a child.
Tool crates are trusted Rust build input; Cargo build scripts/macros may execute.
Review third-party sources and dependencies before building/distributing them.
`;
}

if (
  process.argv[1] &&
  resolve(process.argv[1]) === fileURLToPath(import.meta.url)
) {
  try {
    console.log(
      JSON.stringify(
        await buildPartner(parseArgs(process.argv.slice(2))),
        null,
        2,
      ),
    );
  } catch (e) {
    console.error(e instanceof Error ? e.message : "Partner build failed");
    process.exitCode = 1;
  }
}
