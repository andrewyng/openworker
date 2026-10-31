import { isAbsolute, dirname, join, resolve } from "node:path";
import { existsSync, readFileSync, statSync } from "node:fs";
import { homedir } from "node:os";
import { createHash } from "node:crypto";
import {
  Agent,
  HarnessError,
  HarnessOptions,
  ResolvedHarnessOptions,
} from "./types.js";

/** Resolve only trusted bundled locations, never an executable from PATH. */
export function resolveOptions(
  options: HarnessOptions = {},
): ResolvedHarnessOptions {
  const entryPoint = resolve(process.argv[1] ?? process.cwd());
  let entry = dirname(entryPoint);
  try {
    // Electron development launch accepts an application directory, unlike node file.js.
    if (statSync(entryPoint).isDirectory()) entry = entryPoint;
  } catch {
    /* File-based entry resolution still works before startup. */
  }
  const resources = (process as NodeJS.Process & { resourcesPath?: string })
    .resourcesPath;
  const filename =
    process.platform === "win32"
      ? "openworker-harness.exe"
      : "openworker-harness";
  const executable =
    options.executable ??
    process.env.OPENWORKER_EXECUTABLE ??
    [
      resources && join(resources, filename),
      join(entry, filename),
      join(entry, "bin", filename),
      join(entry, "..", "bin", filename),
    ].find((p): p is string => !!p && existsSync(p));
  if (!executable)
    throw new HarnessError(
      "InvalidInput",
      "Bundled harness not found; use a desktop starter or specify executable",
    );
  let appId = options.appId ?? process.env.OPENWORKER_APP_ID;
  if (!appId && !options.stateDir) {
    let dir = entry;
    for (let i = 0; i < 12; i++) {
      try {
        const pkg = JSON.parse(readFileSync(join(dir, "package.json"), "utf8"));
        if (typeof pkg.name === "string" && pkg.name.length) {
          appId =
            pkg.name.replace(/[^a-zA-Z0-9_-]/g, "-").slice(0, 40) +
            "-" +
            createHash("sha256").update(pkg.name).digest("hex").slice(0, 10);
          break;
        }
      } catch {
        /* Keep looking for the owning application package. */
      }
      if (dirname(dir) === dir) break;
      dir = dirname(dir);
    }
  }
  if (!options.stateDir && (!appId || !/^[a-zA-Z0-9_-]{1,80}$/.test(appId)))
    throw new HarnessError(
      "InvalidInput",
      "Specify appId or stateDir outside a desktop starter/package",
    );
  return {
    ...options,
    executable,
    stateDir:
      options.stateDir ?? join(homedir(), ".openworker", appId!, "state"),
  };
}

export function hostConfig(options: ResolvedHarnessOptions): object {
  for (const p of [
    options.executable,
    options.runner ?? options.executable,
    options.stateDir,
    ...(options.sessions?.directory ? [options.sessions.directory] : []),
    ...(options.scratch?.directory ? [options.scratch.directory] : []),
    ...(options.coworkers ?? []).flatMap((c) =>
      [c.bundle, c.servers, c.toolPolicy, c.runtimePolicy].filter(
        (p): p is string => p !== undefined,
      ),
    ),
  ]) {
    if (!isAbsolute(p))
      throw new HarnessError(
        "InvalidInput",
        "Executable, runner, storage and coworker configuration paths must be absolute",
      );
  }
  const storage = options.storage;
  return {
    state_dir: options.stateDir,
    desktop: {
      mcp_servers: options.mcpServers ?? [],
      persist_sessions: options.sessions?.persist ?? true,
      session_dir: options.sessions?.directory,
      scratch_dir: options.scratch?.directory,
      compaction: {
        threshold_pct: options.compaction?.triggerAtContextFraction ?? 0.8,
        context_window: options.compaction?.contextWindow ?? 128000,
      },
    },
    runner: options.runner ?? options.executable,
    coworkers: (options.coworkers ?? []).map((c) => ({
      bundle: c.bundle,
      model: c.model,
      provider: c.provider ?? "openai",
      servers: c.servers,
      tool_policy: c.toolPolicy,
      runtime_policy: c.runtimePolicy,
    })),
    agents: (options.agents ?? []).map((a) => agentWire(a)),
    storage:
      storage?.type === "postgres"
        ? {
            kind: "postgres",
            connection_env: storage.connectionEnv,
            schema: storage.schema,
            ca_file: storage.caFile ?? null,
            loopback_plaintext: storage.loopbackPlaintext ?? false,
          }
        : { kind: "sqlite" },
    sandbox:
      typeof options.sandbox === "object"
        ? options.sandbox.provider
        : (options.sandbox ?? "openshell"),
    scripted: options.scripted ?? false,
    telemetry: options.telemetry ?? null,
  };
}
export function agentWire(a: Agent, depth = 0): object {
  if (depth > 1)
    throw new HarnessError(
      "InvalidInput",
      "Only one level of subagents is supported",
    );
  // Policy and full definition validation remain authoritative in Rust.
  return {
    id: a.id,
    name: a.name,
    description: a.description,
    instructions: a.instructions,
    limits: a.limits,
    tools: a.tools,
    model: a.model,
    provider: a.provider,
    fallback_model: a.fallbackModel,
    planning: a.planning,
    memory: a.memory ?? (a.memoryScope !== undefined ? true : undefined),
    memory_scope: a.memoryScope,
    scheduling: a.scheduling,
    auto_skip_questions: a.autoSkipQuestions,
    approvals:
      a.approvals?.mode === "auto"
        ? {
            mode: "auto",
            tools: {
              connection: a.approvals.reviewerModel.provider,
              model: a.approvals.reviewerModel.name,
            },
          }
        : a.approvals,
    subagents: a.subagents?.map((child) => agentWire(child, depth + 1)),
    servers: a.servers,
    tool_policy: a.toolPolicy,
    runtime_policy: a.runtimePolicy,
  };
}
export function environment(options: HarnessOptions): NodeJS.ProcessEnv {
  const env: NodeJS.ProcessEnv = {};
  const explicit = [...(options.inheritEnv ?? [])];
  if (options.storage?.type === "postgres")
    explicit.push(options.storage.connectionEnv);
  for (const header of options.telemetry?.headers ?? [])
    if ("value_env" in header) explicit.push(header.value_env);
  for (const key of explicit) {
    if (
      !/^[A-Z_][A-Z0-9_]{0,99}$/.test(key) ||
      process.env[key] === undefined
    ) {
      throw new HarnessError(
        "InvalidInput",
        "Missing or invalid explicitly inherited environment variable",
      );
    }
  }
  for (const key of [
    "PATH",
    "SystemRoot",
    "WINDIR",
    "USERPROFILE",
    "HOME",
    "TMPDIR",
    "TEMP",
    "TMP",
    "LOCALAPPDATA",
    "LANG",
    "DOCKER_HOST",
    "DOCKER_CONTEXT",
    "DOCKER_CONFIG",
    "OPENSHELL_GATEWAY",
    ...explicit,
  ]) {
    if (process.env[key] !== undefined) env[key] = process.env[key];
  }
  return env;
}
