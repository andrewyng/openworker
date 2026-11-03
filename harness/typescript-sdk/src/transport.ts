import { spawn, ChildProcessWithoutNullStreams } from "node:child_process";
import { mkdtemp, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { setTimeout as delay } from "node:timers/promises";
import { environment, hostConfig, resolveOptions } from "./config.js";
import {
  HarnessError,
  HarnessOptions,
  ResolvedHarnessOptions,
  record,
  type ConnectionState,
} from "./types.js";

const MAX_FRAME = 1024 * 1024;
type Pending = {
  id: number;
  resolve: (v: unknown) => void;
  reject: (e: Error) => void;
  timer: NodeJS.Timeout;
};

/** Private, single-owner process transport. Mutations are NEVER resubmitted. */
export class Transport {
  private child?: ChildProcessWithoutNullStreams;
  private pending?: Pending;
  private sequence = 0;
  private queue: Promise<unknown> = Promise.resolve();
  private queued = 0;
  private closed = false;
  private healthy = false;
  private generation = 0;
  private restarts = 0;
  private recovery?: Promise<void>;
  private recovering = false;
  private listeners = new Set<(state: ConnectionState) => void>();
  private applicationListeners = new Set<
    (event: Record<string, unknown>) => void
  >();
  private applicationRegistrations: unknown[] = [];
  private providerAuthRegistrations = new Map<string, unknown>();
  private safetyRegistrations = new Map<string, unknown>();
  rememberSafetyRegistration(key:string,value:unknown,method="safety/register"):void{this.safetyRegistrations.set(key,{method,value:structuredClone(value)});}
  rememberProviderAuth(name: string, value: unknown): void {
    this.providerAuthRegistrations.set(name, structuredClone(value));
  }
  private journalListeners = new Set<
    (method: string, params: unknown) => void
  >();
  onJournalHint(
    listener: (method: string, params: unknown) => void,
  ): () => void {
    this.journalListeners.add(listener);
    return () => {
      this.journalListeners.delete(listener);
    };
  }
  rememberApplicationRegistration(value: unknown): void {
    this.applicationRegistrations.push(structuredClone(value));
  }
  /** Reserved notifications bypass normal request waits. The tool journal, not
   * a successful pipe write, is the authoritative execution outcome. */
  async callbackMessage(method: string, params: unknown): Promise<void> {
    if (
      ![
        "application/result",
        "application/progress",
        "provider/auth/result",
        "safety/result",
      ].includes(method)
    )
      throw new HarnessError("InvalidInput", "Unsupported callback control");
    const child = this.child;
    if (
      this.closed ||
      !child ||
      child.exitCode !== null ||
      child.signalCode !== null
    )
      throw new HarnessError("Unavailable", "Callback connection closed");
    const line = JSON.stringify({ jsonrpc: "2.0", method, params }) + "\n";
    if (
      Buffer.byteLength(line) > MAX_FRAME ||
      child.stdin.writableLength > 2 * MAX_FRAME
    )
      throw new HarnessError(
        "InvalidInput",
        "Callback control exceeds buffer limit",
      );
    await new Promise<void>((resolve, reject) => {
      const timer = setTimeout(
        () =>
          reject(
            new HarnessError("OutcomeUnknown", "Callback delivery timed out"),
          ),
        10000,
      );
      child.stdin.write(line, (error) => {
        clearTimeout(timer);
        if (error)
          reject(
            new HarnessError("OutcomeUnknown", "Callback delivery failed"),
          );
        else resolve();
      });
    });
  }
  onApplicationEvent(
    listener: (event: Record<string, unknown>) => void,
  ): () => void {
    this.applicationListeners.add(listener);
    return () => {
      this.applicationListeners.delete(listener);
    };
  }
  private stopPromise?: Promise<void>;
  private directory = "";
  private configPath = "";
  private disconnect?: (message: string) => void;
  private readonly timeout: number;
  private readonly shutdownTimeout: number;
  private readonly maxRestarts: number;
  private readonly restartDelay: number;
  info: Record<string, unknown> = {};

  private constructor(private readonly options: ResolvedHarnessOptions) {
    this.timeout = options.requestTimeoutMs ?? 180_000;
    this.shutdownTimeout = options.shutdownTimeoutMs ?? 20_000;
    this.maxRestarts = options.restart?.maxRestarts ?? 3;
    this.restartDelay = options.restart?.delayMs ?? 250;
    for (const n of [this.timeout, this.shutdownTimeout]) {
      if (!Number.isInteger(n) || n < 1 || n > 600_000)
        throw new HarnessError(
          "InvalidInput",
          "Timeout must be between 1 and 600000 ms",
        );
    }
    if (
      !Number.isInteger(this.maxRestarts) ||
      this.maxRestarts < 0 ||
      this.maxRestarts > 100 ||
      !Number.isInteger(this.restartDelay) ||
      this.restartDelay < 0 ||
      this.restartDelay > 60_000
    ) {
      throw new HarnessError("InvalidInput", "Invalid bounded restart policy");
    }
  }
  static async start(options: HarnessOptions, safety?: unknown): Promise<Transport> {
    const resolved = resolveOptions(options);
    // Provider credentials never belong in persisted launch configuration.
    delete resolved.modelProviders;
    const transport = new Transport(structuredClone(resolved));
    const launch=hostConfig(resolved) as {desktop:Record<string,unknown>};
    if(safety!==undefined)launch.desktop.safety=safety;
    const config = JSON.stringify(launch);
    environment(resolved); // Reject missing declarations before spawning.
    transport.directory = await mkdtemp(join(tmpdir(), "openworker-ts-"));
    transport.configPath = join(transport.directory, "host.json");
    try {
      // Temp directory is private; @file avoids argv limits and leaking prompts in ps.
      await writeFile(transport.configPath, config, { mode: 0o600 });
      transport.recovery = transport.launch();
      await transport.recovery;
      return transport;
    } catch (e) {
      await transport.stop();
      throw e;
    }
  }
  get pid(): number | undefined {
    return this.child?.pid;
  }
  get state(): ConnectionState {
    return this.closed
      ? "closed"
      : this.healthy
        ? "ready"
        : this.recovering
          ? "recovering"
          : "unavailable";
  }
  onStateChange(listener: (state: ConnectionState) => void): () => void {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }
  private notify(): void {
    for (const listener of this.listeners) {
      try {
        listener(this.state);
      } catch {
        /* UI callback must not break transport ownership. */
      }
    }
  }
  async ready(): Promise<void> {
    if (this.closed) throw new HarnessError("Closed", "Harness is closed");
    if (this.healthy) return;
    await this.recovery;
    if (!this.healthy)
      throw new HarnessError("Unavailable", "Harness restart budget exhausted");
  }
  request<T = unknown>(method: string, params: unknown): Promise<T> {
    if (this.closed)
      return Promise.reject(new HarnessError("Closed", "Harness is closed"));
    if (!this.healthy)
      return Promise.reject(
        new HarnessError(
          "Unavailable",
          "Harness is recovering; request was not sent",
        ),
      );
    if (this.queued >= 64)
      return Promise.reject(
        new HarnessError(
          "Unavailable",
          "Client request queue is full; request was not sent",
        ),
      );
    this.queued++;
    const generation = this.generation;
    const task = this.queue
      .then(() => {
        if (this.closed) throw new HarnessError("Closed", "Harness is closed");
        if (!this.healthy || generation !== this.generation) {
          throw new HarnessError(
            "Unavailable",
            "Process changed before the queued request was sent",
          );
        }
        return this.call(method, params);
      })
      .finally(() => {
        this.queued--;
      });
    this.queue = task.catch(() => {});
    return task as Promise<T>;
  }
  private async launch(): Promise<void> {
    if (this.closed) throw new HarnessError("Closed", "Harness is closed");
    this.healthy = false;
    this.generation++;
    const child = spawn(
      this.options.executable,
      ["harness-stdio", `@${this.configPath}`],
      {
        env: environment(this.options),
        stdio: ["pipe", "pipe", "pipe"],
        windowsHide: true,
      },
    );
    this.child = child;
    let buffer = Buffer.alloc(0);
    let failed = false;
    let initialized = false;
    // Descendants may inherit pipe descriptors. Their eventual EOF is not the
    // harness lifetime: wait for the owned process to exit and release its locks.
    const exited = new Promise<void>((resolve) => {
      child.once("exit", () => resolve());
      child.once("error", () => resolve());
    });
    const lose = (message: string) => {
      if (failed) return;
      failed = true;
      this.healthy = false;
      const pending = this.pending;
      this.pending = undefined;
      if (pending) {
        clearTimeout(pending.timer);
        pending.reject(new HarnessError("OutcomeUnknown", message));
      }
      // Wait for process exit before a replacement can take its durable locks.
      if (!this.closed && child.exitCode === null && child.signalCode === null)
        child.kill("SIGKILL");
      if (initialized && !this.closed && this.child === child)
        this.scheduleRecovery(exited);
    };
    this.disconnect = lose;
    child.stdout.on("data", (chunk: Buffer) => {
      if (failed) return;
      buffer = Buffer.concat([buffer, chunk]);
      let end: number;
      while ((end = buffer.indexOf(10)) >= 0) {
        if (end > MAX_FRAME)
          return lose(
            "Harness exceeded the frame limit; reconcile durable state",
          );
        const raw = buffer.subarray(0, end);
        buffer = buffer.subarray(end + 1);
        try {
          const frame = record(
            JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(raw)),
          );
          if (frame.jsonrpc !== "2.0") throw new Error("version");
          if (typeof frame.method === "string" && frame.id === undefined) {
            if (frame.method === "application/event") {
              for (const listener of this.applicationListeners)
                listener(record(frame.params));
            }
            if (["session/event", "stream/gap"].includes(frame.method)) {
              for (const listener of this.journalListeners) {
                try {
                  listener(frame.method, frame.params);
                } catch {
                  /* App observers cannot break the transport. */
                }
              }
            }
            continue; // Other hints replay through the journal.
          }
          if (
            !this.pending ||
            frame.id !== this.pending.id ||
            Object.hasOwn(frame, "result") === Object.hasOwn(frame, "error")
          )
            throw new Error("response");
          const p = this.pending;
          this.pending = undefined;
          clearTimeout(p.timer);
          if (frame.error) {
            // Do not reflect untrusted diagnostics, tool arguments or credentials in errors.
            p.reject(
              new HarnessError(
                "Remote",
                "Harness rejected the request; inspect its configuration or durable session state",
              ),
            );
          } else p.resolve(frame.result);
        } catch {
          return lose("Malformed harness protocol; reconcile durable state");
        }
      }
      if (buffer.length > MAX_FRAME)
        lose("Harness exceeded the frame limit; reconcile durable state");
    });
    child.stderr.on("data", () => {}); // Drain without leaking diagnostics into app logs.
    child.stdin.on("error", () =>
      lose("Harness pipe lost; request outcome may be unknown"),
    );
    child.on("error", () =>
      lose("Could not launch or communicate with the harness"),
    );
    child.on("exit", () => {
      lose("Harness exited; reconcile durable state before retrying mutations");
      child.stdin.destroy();
      child.stdout.destroy();
      child.stderr.destroy();
    });
    child.on("close", () => lose("Harness closed"));
    const info = record(
      await this.call("initialize", { protocol_version: "0.1" }),
    );
    if (
      info.protocol_version !== "0.1" ||
      record(info.capabilities).agent_runs !== true
    ) {
      child.kill("SIGKILL");
      throw new HarnessError(
        "Setup",
        "Runtime does not support protocol 0.1 agent runs",
      );
    }
    if (failed || this.closed)
      throw new HarnessError(
        "Unavailable",
        "Harness stopped during initialization",
      );
    this.info = info;
    for (const registration of this.applicationRegistrations)
      await this.call("application/register", registration);
    for (const registration of this.safetyRegistrations.values()){const r=registration as {method:string;value:unknown};await this.call(r.method,r.value);}
    for (const registration of this.providerAuthRegistrations.values())
      await this.call("provider/auth/register", registration);
    initialized = true;
    this.healthy = true;
    this.notify();
  }
  private scheduleRecovery(exited: Promise<void>): void {
    this.recovering = true;
    this.notify();
    this.recovery = (async () => {
      await exited;
      while (!this.closed && this.restarts < this.maxRestarts) {
        this.restarts++;
        await delay(this.restartDelay);
        if (this.closed) return;
        try {
          await this.launch();
          return;
        } catch {
          await this.terminate();
        }
      }
    })().finally(() => {
      this.recovering = false;
      if (!this.healthy && !this.closed) this.notify();
    });
    // A caller can await ready(); do not create unhandled background rejections.
    void this.recovery.catch(() => {});
  }
  private call(method: string, params: unknown): Promise<unknown> {
    const id = ++this.sequence;
    let line: string;
    try {
      line = JSON.stringify({ jsonrpc: "2.0", id, method, params }) + "\n";
    } catch {
      return Promise.reject(
        new HarnessError("InvalidInput", "Request is not JSON serializable"),
      );
    }
    if (Buffer.byteLength(line) > MAX_FRAME)
      return Promise.reject(
        new HarnessError(
          "InvalidInput",
          "Request exceeds the protocol frame limit",
        ),
      );
    return new Promise((resolve, reject) => {
      const child = this.child;
      if (!child || child.exitCode !== null || child.signalCode !== null) {
        reject(new HarnessError("Unavailable", "Harness is not running"));
        return;
      }
      const timer = setTimeout(() => {
        if (this.pending?.id !== id) return;
        this.disconnect?.(
          "Request timed out; reconcile durable state before retrying",
        );
      }, this.timeout);
      this.pending = { id, resolve, reject, timer };
      child.stdin.write(line, (err) => {
        if (err && this.pending?.id === id) {
          this.disconnect?.("Request pipe failed; reconcile durable state");
        }
      });
    });
  }
  private async terminate(): Promise<void> {
    const child = this.child;
    if (!child || child.pid === undefined || child.exitCode !== null || child.signalCode !== null) return;
    await new Promise<void>((resolve) => {
      child.once("exit", () => {
        clearTimeout(timer);
        resolve();
      });
      const timer = setTimeout(
        () => child.kill("SIGKILL"),
        this.shutdownTimeout,
      );
      child.stdin.end();
    });
  }
  stop(): Promise<void> {
    this.stopPromise ??= (async () => {
      this.closed = true;
      this.healthy = false;
      this.notify();
      await this.terminate();
      await this.recovery?.catch(() => {});
      if (this.directory)
        await rm(this.directory, { recursive: true, force: true });
    })();
    return this.stopPromise;
  }
}
