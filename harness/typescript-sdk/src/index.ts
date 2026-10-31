import { Service, type ServiceOptions } from "./service.js";
export * from "./service.js";
import { randomUUID } from "node:crypto";
import { isAbsolute } from "node:path";
import { Transport } from "./transport.js";
import { agentWire } from "./config.js";
import {
  ToolImageResult,
  checkMemberErrors,
  record,
  HarnessError,
  type HarnessOptions,
  type SessionRef,
  type Connection,
  type ModelProvider,
  type AccessToken,
  type ProviderAuth,
  type ApplicationTools,
  type ApplicationTool,
  type McpServer,
  type MessageInput,
  type ControlInput,
  type SessionState,
  type Interruption,
  type Response,
  type RunOptions,
  type ResumeOptions,
  type RunResult,
  type RunEvent,
  type Cursors,
  type ItemSpec,
  type ItemStatus,
  type TeamItem,
  type Json,
  type TeamBoard,
  type ConnectionState,
  type AgentDefinition,
  type SessionOptions,
  type ChildSpawnOptions,
} from "./types.js";
export * from "./types.js";

function aborted(signal?: AbortSignal): void {
  if (signal?.aborted)
    throw new HarnessError(
      "Aborted",
      "Stopped waiting; agent execution was not cancelled",
    );
}
function resultOf(value: unknown): RunResult {
  const v = record(value);
  if (
    !["completed", "interrupted", "cancelled", "failed"].includes(
      String(v.status),
    ) ||
    !Array.isArray(v.interruptions) ||
    !Number.isSafeInteger(v.cursor) ||
    !(v.final_output === null || typeof v.final_output === "string") ||
    !(v.submission_id === null || typeof v.submission_id === "string")
  ) {
    throw new HarnessError("Protocol", "Invalid terminal run state");
  }
  return {
    status: v.status as RunResult["status"],
    finalOutput: v.final_output as string | null,
    interruptions: v.interruptions.map(interruptionOf),
    submissionId: v.submission_id as string | null,
    failure: typeof v.failure === "string" ? v.failure : undefined,
    cursor: Number(v.cursor),
    board: (v.board ?? null) as Json,
  };
}
function interruptionOf(value: unknown): Interruption {
  const v = record(value);
  if (
    typeof v.id !== "string" ||
    typeof v.session_id !== "string" ||
    typeof v.agent !== "string" ||
    !(
      (v.kind === "approval" &&
        typeof v.tool === "string" &&
        v.arguments !== undefined) ||
      (v.kind === "question" &&
        typeof v.question === "string" &&
        Array.isArray(v.options) &&
        v.options.every((o) => typeof o === "string"))
    )
  ) {
    throw new HarnessError("Protocol", "Invalid interruption");
  }
  return v as unknown as Interruption;
}

/** Node.js host owner. Agent logic, storage and permission enforcement remain in Rust. */
export class Harness {
  static connect(options: ServiceOptions): Promise<Service> {
    return Service.connect(options);
  }
  private readonly callbackOwner = randomUUID();
  private readonly callbacks = new Map<string, ApplicationTool>();
  private readonly callbackRuns = new Map<string, AbortController>();
  private readonly providerAuth = new Map<string, ProviderAuth>();
  private constructor(
    private readonly transport: Transport,
    public readonly agents: readonly string[],
  ) {
    transport.onApplicationEvent((event) => {
      if (
        event.kind === "provider_auth" ||
        event.kind === "provider_auth_cancel"
      ) {
        void this.providerAuthEvent(event).catch(() => {});
        return;
      }
      void this.applicationEvent(event).catch(() => {});
    });
    transport.onStateChange((state) => {
      if (state !== "ready")
        for (const controller of this.callbackRuns.values()) controller.abort();
    });
  }
  async registerTools(registration: ApplicationTools): Promise<void> {
    if (this.capabilities.application_callbacks !== true)
      throw new HarnessError(
        "Setup",
        "Update the bundled runtime to use application callbacks",
      );
    const descriptors = [];
    for (const [name, tool] of Object.entries(registration.tools)) {
      const qualifiedName = `${registration.namespace}.${name}`;
      if (this.callbacks.has(qualifiedName))
        throw new HarnessError(
          "InvalidInput",
          "Application tool already registered on this handle",
        );
      if (typeof tool.execute !== "function")
        throw new HarnessError(
          "InvalidInput",
          "Application callback is required",
        );
      descriptors.push({
        qualified_name: qualifiedName,
        description: tool.description,
        input_schema: tool.inputSchema,
        version: tool.version ?? "1",
      });
    }
    // Install before registration can make the proxy available to an existing agent.
    for (const [name, tool] of Object.entries(registration.tools))
      this.callbacks.set(`${registration.namespace}.${name}`, tool);
    const wire = { owner: this.callbackOwner, tools: descriptors };
    try {
      await this.request("application/register", wire);
    } catch (error) {
      if (
        error instanceof HarnessError &&
        ["Remote", "InvalidInput"].includes(error.code)
      )
        for (const descriptor of descriptors)
          this.callbacks.delete(descriptor.qualified_name);
      throw error;
    }
    this.transport.rememberApplicationRegistration(wire);
  }
  private async applicationEvent(
    event: Record<string, unknown>,
  ): Promise<void> {
    if (
      event.owner !== this.callbackOwner ||
      typeof event.invocation_id !== "string"
    )
      return;
    const id = event.invocation_id;
    if (event.kind === "cancel") {
      this.callbackRuns.get(id)?.abort();
      return;
    }
    if (
      event.kind !== "call" ||
      this.callbackRuns.has(id) ||
      this.callbackRuns.size >= 64
    )
      return;
    const controller = new AbortController();
    this.callbackRuns.set(id, controller);
    const timeout = setTimeout(() => controller.abort(), 70_000);
    const context = record(event.context);
    try {
      const tool = this.callbacks.get(String(event.tool));
      if (!tool) throw new Error("Executor unavailable");
      const execution = Promise.resolve().then(() =>
        tool.execute(record(event.arguments) as Record<string, Json>, {
          sessionId: String(context.session_id),
          turnId: String(context.turn_id),
          callId: String(context.call_id),
          workspace: String(context.workspace),
          signal: controller.signal,
          progress: async (message, data = null) => {
            if (controller.signal.aborted)
              throw new Error("Invocation cancelled");
            await this.transport.callbackMessage("application/progress", {
              owner: this.callbackOwner,
              invocation_id: id,
              message,
              data,
            });
          },
        }),
      );
      // Release SDK bookkeeping even if application code ignores cancellation.
      // Aborting cannot undo external effects or forcibly stop a JS handler.
      let cancel: () => void = () => {};
      const cancelled = new Promise<never>((_, reject) => {
        cancel = () => reject(new Error("Invocation cancelled"));
        controller.signal.addEventListener("abort", cancel, { once: true });
        if (controller.signal.aborted) cancel();
      });
      let output: Json | ToolImageResult;
      try {
        output = await Promise.race([execution, cancelled]);
      } finally {
        controller.signal.removeEventListener("abort", cancel);
      }
      if (
        output instanceof ToolImageResult &&
        this.capabilities.application_tool_images !== true
      )
        throw new Error("Update the runtime to use application tool images");
      if (!controller.signal.aborted)
        await this.transport.callbackMessage("application/result", {
          owner: this.callbackOwner,
          invocation_id: id,
          output: output instanceof ToolImageResult ? output.data : output,
          ...(output instanceof ToolImageResult
            ? {
                images: [
                  {
                    mime_type: output.image.mimeType,
                    base64: output.image.base64,
                  },
                ],
              }
            : {}),
        });
    } catch {
      if (!controller.signal.aborted)
        await this.transport
          .callbackMessage("application/result", {
            owner: this.callbackOwner,
            invocation_id: id,
            error: { code: "application_error" },
          })
          .catch(() => {});
    } finally {
      clearTimeout(timeout);
      this.callbackRuns.delete(id);
    }
  }
  static async start(options: HarnessOptions = {}): Promise<Harness> {
    const transport = await Transport.start(options);
    try {
      const listed = record(await transport.request("agent/list", {}));
      if (!Array.isArray(listed.agents))
        throw new HarnessError("Protocol", "Invalid agent list");
      const harness = new Harness(
        transport,
        listed.agents.map((a) => String(record(a).id)),
      );
      for (const provider of options.modelProviders ?? [])
        await harness.setModelProvider(provider);
      return harness;
    } catch (e) {
      await transport.stop();
      throw e;
    }
  }
  get pid(): number | undefined {
    return this.transport.pid;
  }
  get state() {
    return this.transport.state;
  }
  get capabilities(): Readonly<Record<string, unknown>> {
    return { ...record(this.transport.info.capabilities) };
  }
  ready(): Promise<void> {
    return this.transport.ready();
  }
  onStateChange(listener: (state: ConnectionState) => void): () => void {
    return this.transport.onStateChange(listener);
  }
  /** Best-effort hints only: recover authoritative events with event/read and a durable cursor. */
  onJournalHint(
    listener: (method: string, params: unknown) => void,
  ): () => void {
    return this.transport.onJournalHint(listener);
  }
  /** Escape hatch. Requests are not retried after disconnect, including mutations. */
  request<T = unknown>(method: string, params: unknown): Promise<T> {
    return this.transport.request<T>(method, params);
  }
  async setConnection(connection: Connection): Promise<void> {
    if (!connection.apiKey)
      throw new HarnessError("InvalidInput", "A nonempty API key is required");
    await this.request("connection/set", {
      coworker: connection.coworker,
      id: connection.id,
      label: connection.label,
      provider: connection.provider,
      api_key: connection.apiKey,
      persistence: connection.persistence ?? "memory",
    });
  }
  async setModelProvider(provider: ModelProvider): Promise<void> {
    const credentials = provider.credentials;
    if (provider.auth && credentials)
      throw new HarnessError(
        "InvalidInput",
        "Use access-token auth or static credentials, not both",
      );
    if (provider.auth && this.capabilities.provider_access_tokens !== true)
      throw new HarnessError(
        "Setup",
        "Update the runtime for access-token authentication",
      );
    if (
      credentials &&
      (credentials.apiKey !== undefined) ===
        (credentials.apiKeyEnv !== undefined)
    )
      throw new HarnessError(
        "InvalidInput",
        "Supply either apiKey or apiKeyEnv",
      );
    const apiKey =
      credentials?.apiKeyEnv !== undefined
        ? process.env[credentials.apiKeyEnv]
        : credentials?.apiKey;
    if (credentials && !apiKey)
      throw new HarnessError("InvalidInput", "Provider key is unavailable");
    await this.request("provider/set", {
      name: provider.name,
      api: provider.api,
      base_url: provider.baseUrl,
      access_token_auth: provider.auth ? true : undefined,
      credentials: credentials
        ? { api_key: apiKey, storage: credentials.storage ?? "secure" }
        : undefined,
    });
    if (provider.auth) {
      const binding = {
        name: provider.name,
        owner: this.callbackOwner,
        refresh: !!provider.auth.getToken,
      };
      this.providerAuth.set(provider.name, provider.auth);
      await this.request("provider/auth/register", binding);
      this.transport.rememberProviderAuth(provider.name, binding);
    }
  }
  /** Proactively replace a bearer access token without changing any session. */
  async updateProviderCredentials(
    provider: string,
    token: AccessToken,
  ): Promise<void> {
    await this.request("provider/auth/update", {
      name: provider,
      access_token: token.accessToken,
      expires_at: token.expiresAt,
    });
  }
  /** Explicit short model request. May incur provider usage; creates no agent session. */
  async testModelProvider(model: import("./types.js").Model): Promise<void> {
    if (this.capabilities.provider_probe !== true)
      throw new HarnessError(
        "Setup",
        "Update the runtime for provider connection tests",
      );
    await this.request("provider/test", {
      connection: model.provider,
      model: model.name,
    });
  }
  private async providerAuthEvent(
    event: Record<string, unknown>,
  ): Promise<void> {
    if (
      event.owner !== this.callbackOwner ||
      typeof event.invocation_id !== "string"
    )
      return;
    const id = event.invocation_id;
    if (event.kind === "provider_auth_cancel") {
      this.callbackRuns.get(id)?.abort();
      return;
    }
    if (this.callbackRuns.has(id) || this.callbackRuns.size >= 64) return;
    const controller = new AbortController();
    this.callbackRuns.set(id, controller);
    const timer = setTimeout(() => controller.abort(), 30_000);
    const response: Record<string, unknown> = {
      owner: this.callbackOwner,
      invocation_id: id,
    };
    try {
      const provider = String(event.provider);
      const callback = this.providerAuth.get(provider)?.getToken;
      if (
        !callback ||
        !["missing", "expiring", "rejected"].includes(String(event.reason))
      )
        throw new Error();
      const token = await Promise.race([
        callback({
          provider,
          reason: event.reason as "missing" | "expiring" | "rejected",
          signal: controller.signal,
        }),
        new Promise<never>((_, reject) =>
          controller.signal.addEventListener(
            "abort",
            () => reject(new Error()),
            { once: true },
          ),
        ),
      ]);
      response.access_token = token.accessToken;
      response.expires_at = token.expiresAt;
    } catch {
      response.error = true; // Never send app exception text or credentials in diagnostics.
    } finally {
      clearTimeout(timer);
      this.callbackRuns.delete(id);
    }
    if (!controller.signal.aborted)
      await this.transport.callbackMessage("provider/auth/result", response);
  }
  async connectMcpServer(
    server: Extract<McpServer, { transport: "streamable-http" }>,
  ): Promise<void> {
    if (server.transport !== "streamable-http")
      throw new HarnessError(
        "InvalidInput",
        "Use startMcpServer for executables",
      );
    await this.request("mcp/register", server);
  }
  async startMcpServer(
    server: Omit<Extract<McpServer, { transport: "stdio" }>, "transport">,
  ): Promise<void> {
    await this.request("mcp/register", { ...server, transport: "stdio" });
  }
  /** Creates a reusable handle. Await ready() or newSession() to confirm registration. */
  createAgent(definition: AgentDefinition): AgentHandle {
    return new AgentHandle(this, structuredClone(definition));
  }
  async listAgents(): Promise<string[]> {
    const result = record(await this.request("agent/list", {}));
    if (!Array.isArray(result.agents))
      throw new HarnessError("Protocol", "Invalid agent list");
    return result.agents.map((a) => String(record(a).id));
  }
  async newTeam(
    lead: string,
    options: SessionOptions & AgentTeamOptions,
  ): Promise<AgentTeam> {
    const session = await this.session(lead, options);
    return this.attachTeam(session, options);
  }
  async attachTeam(
    lead: Session,
    options: AgentTeamOptions,
  ): Promise<AgentTeam> {
    const doc = record(
      await this.request("agent-team/create", {
        session_id: lead.id,
        agent_id: options.agentId ?? "lead",
        agent_types: options.agentTypes,
        policy: {
          communication: options.communication ?? {
            board: true,
            directMessages: false,
          },
          max_instances: options.maxInstances ?? 32,
        },
        ...(options.limits ? { limits: options.limits } : {}),
        ...(options.delegation ? { delegation: {
          tools: options.delegation.tools,
          models: (options.delegation.models ?? []).map(model => ({connection: model.provider, model: model.name})),
          placements: ["team"], profiles: {},
        }} : {}),
      }),
    );
    return new AgentTeam(this, lead, String(doc.id));
  }
  async resumeTeam(reference: AgentTeamRef): Promise<AgentTeam> {
    const lead = await this.resume(reference.lead);
    const team = new AgentTeam(this, lead, reference.teamId);
    if ((await team.snapshot()).id !== reference.teamId)
      throw new HarnessError("InvalidInput", "Team identity mismatch");
    return team;
  }
  async session(agent: string, options: SessionOptions = {}): Promise<Session> {
    if (
      options.autoSkipQuestions !== undefined &&
      this.capabilities.sdk_question_policy !== true
    )
      throw new HarnessError(
        "Setup",
        "Update the host to configure SDK question policy",
      );
    if (options.workspace !== undefined && !isAbsolute(options.workspace))
      throw new HarnessError(
        "InvalidInput",
        "Workspace must be an existing absolute directory",
      );
    const v = record(
      await this.request("agent/session", {
        agent,
        workspace: options.workspace,
        scratch: options.scratch,
        auto_skip_questions: options.autoSkipQuestions,
        name: options.name,
        ...(options.model
          ? {
              model: {
                connection: options.model.provider,
                model: options.model.name,
              },
            }
          : {}),
      }),
    );
    return this.resume(v.session as SessionRef);
  }
  async resume(reference: SessionRef): Promise<Session> {
    const session = new Session(this, structuredClone(reference));
    for (const id of session.memberIds())
      await this.request("session/attach", { session_id: id });
    return session;
  }
  shutdown(): Promise<void> {
    return this.transport.stop();
  }
  /** Read-only recovery: reconnect and repeat a journal/snapshot read, never a mutation. */
  async read<T = unknown>(
    method: "run/wait" | "session/attach",
    params: unknown,
    signal?: AbortSignal,
  ): Promise<T> {
    if (!["run/wait", "session/attach"].includes(method)) {
      throw new HarnessError(
        "InvalidInput",
        "Automatic recovery is restricted to journal/snapshot reads",
      );
    }
    while (true) {
      aborted(signal);
      await this.ready();
      try {
        const v = await this.request<T>(method, params);
        aborted(signal);
        return v;
      } catch (e) {
        if (
          !(e instanceof HarnessError) ||
          !["OutcomeUnknown", "Unavailable"].includes(e.code)
        )
          throw e;
        // Queue backpressure is not a process failure. Do not spin a microtask
        // loop that would prevent the queued I/O from ever completing.
        if (e.code === "Unavailable" && this.state === "ready") throw e;
        await this.ready();
      }
    }
  }
}

/** A definition bound to one harness, not a mutable current-session pointer. */
export class AgentHandle {
  private readonly registration: Promise<void>;
  readonly name: string;
  constructor(
    private readonly harness: Harness,
    definition: AgentDefinition,
  ) {
    this.name = definition.name;
    this.registration = this.register(definition);
    // Callers may construct a handle before awaiting it; surface failure at ready/newSession.
    void this.registration.catch(() => {});
  }
  private async register(definition: AgentDefinition): Promise<void> {
    if (this.harness.capabilities.dynamic_agents !== true)
      throw new HarnessError("Setup", "Host does not support dynamic agents");
    const { name, defaultModel, ...rest } = definition;
    await this.harness.request("agent/register", {
      agent: agentWire({
        ...rest,
        id: name,
        name,
        ...(defaultModel
          ? {
              model: {
                connection: defaultModel.provider,
                model: defaultModel.name,
              },
            }
          : {}),
      }),
    });
  }
  ready(): Promise<void> {
    return this.registration;
  }
  async newSession(options: SessionOptions = {}): Promise<Session> {
    await this.ready();
    return this.harness.session(this.name, options);
  }
  async resumeSession(reference: SessionRef): Promise<Session> {
    await this.ready();
    if (reference.agent !== this.name)
      throw new HarnessError(
        "InvalidInput",
        "Session belongs to a different agent",
      );
    const snapshot = record(
      await this.harness.read("session/attach", {
        session_id: reference.session_id,
      }),
    );
    if (record(snapshot.session).coworker !== this.name)
      throw new HarnessError(
        "InvalidInput",
        "Session identity does not match this agent",
      );
    return this.harness.resume(reference);
  }
}

export class Session {
  constructor(
    private readonly harness: Harness,
    private readonly ref: SessionRef,
  ) {
    if (
      !ref ||
      typeof ref.session_id !== "string" ||
      typeof ref.agent !== "string"
    ) {
      throw new HarnessError("InvalidInput", "Invalid saved session reference");
    }
  }
  get id(): string {
    return this.ref.session_id;
  }
  get agent(): string {
    return this.ref.agent;
  }
  reference(): SessionRef {
    return structuredClone(this.ref);
  }
  memberIds(): string[] {
    return [
      this.id,
      ...(this.ref.team?.workers.map((w) => w.session_id) ?? []),
    ];
  }
  team(): Team | undefined {
    return this.ref.team
      ? new Team(this.harness, this.ref.team.team_id)
      : undefined;
  }
  snapshot(): Promise<unknown> {
    return this.harness.read("session/attach", { session_id: this.id });
  }
  async state(): Promise<SessionState> {
    const value = record(
      await this.harness.request("session/state", { session_id: this.id }),
    );
    return {
      status: value.status as SessionState["status"],
      runId: value.run_id as string | null,
      active: value.active as Json[],
      pending: (value.pending as unknown[]).map(interruptionOf),
      timers: Array.isArray(value.timers)
        ? value.timers.map((v) => {
            const t = record(v);
            return {
              id: String(t.id),
              nextRunAt: Number(t.next_run_at),
              remainingRuns: Number(t.remaining_runs),
            };
          })
        : null,
      capabilities: {
        steering: false,
        timers: record(value.capabilities ?? {}).timers === true,
      },
      canAcceptMessage: value.can_accept_message === true,
      lastOutcome: value.last_outcome as string | null,
      cursor: Number(value.cursor),
    };
  }
  async send(input: MessageInput, options?: RunOptions): Promise<Run>;
  async send(
    input: ControlInput,
    options?: RunOptions,
  ): Promise<{ accepted: true; runId: string | null }>;
  async send(
    input: MessageInput | ControlInput,
    options: RunOptions = {},
  ): Promise<Run | { accepted: true; runId: string | null }> {
    aborted(options.signal);
    if (options.limits && this.harness.capabilities.turn_limits !== true) {
      throw new HarnessError("Setup", "Host does not support turn limits");
    }
    const value = typeof input === "string" ? { text: input } : input;
    const control = value.type === "approval" || value.type === "answer";
    if (control && options.limits !== undefined) {
      throw new HarnessError(
        "InvalidInput",
        "Limits apply only to new message turns",
      );
    }
    const allowed = control
      ? value.type === "approval"
        ? ["type", "interactionId", "targetSessionId", "decision"]
        : ["type", "interactionId", "targetSessionId", "text"]
      : ["type", "text"];
    if (Object.keys(value).some((key) => !allowed.includes(key)))
      throw new HarnessError(
        "InvalidInput",
        "Mixed or unknown session input fields",
      );
    const submissionId = options.submissionId ?? randomUUID();
    const wire = control
      ? {
          ...value,
          interaction_id: value.interactionId,
          target_session_id: value.targetSessionId,
        }
      : value;
    if (control) {
      delete (wire as Record<string, unknown>).interactionId;
      delete (wire as Record<string, unknown>).targetSessionId;
    }
    const result = record(
      await this.harness.request("session/send", {
        session_id: this.id,
        submission_id: submissionId,
        input: wire,
        limits: options.limits,
      }),
    );
    if (control)
      return { accepted: true, runId: result.run_id as string | null };
    return new Run(
      this.harness,
      this,
      submissionId,
      result.cursors as Cursors,
      options,
    );
  }
  async run(input: string, options: RunOptions = {}): Promise<RunResult> {
    const submissionId = options.submissionId ?? randomUUID();
    try {
      await this.start(input, submissionId, options.signal);
      return await this.resume({ ...options, submissionId });
    } catch (e) {
      if (e instanceof HarnessError) {
        throw new HarnessError(e.code, e.message, {
          ...e.context,
          sessionId: this.id,
          submissionId,
        });
      }
      throw e;
    }
  }
  async runWith(
    input: string,
    handler: (i: Interruption) => Response | Promise<Response>,
    options: RunOptions = {},
  ): Promise<RunResult> {
    let result = await this.run(input, options);
    while (result.status === "interrupted") {
      let deferred = false;
      for (const i of result.interruptions) {
        const response = await handler(i);
        deferred ||= "defer" in response;
        await this.respond(i, response);
      }
      if (deferred) return result;
      result = await this.resume({
        ...options,
        submissionId: result.submissionId ?? undefined,
      });
    }
    return result;
  }
  async runStreamed(
    input: string,
    options: RunOptions = {},
  ): Promise<RunStream> {
    const submissionId = options.submissionId ?? randomUUID();
    const started = await this.start(input, submissionId, options.signal);
    return new RunStream(this.harness, this.id, started.cursors as Cursors, {
      ...options,
      submissionId,
    });
  }
  async resume(options: RunOptions = {}): Promise<RunResult> {
    while (true) {
      const v = record(
        await this.harness.read(
          "run/wait",
          {
            session_id: this.id,
            submission_id: options.submissionId,
            wait_ms: 1000,
          },
          options.signal,
        ),
      );
      if (v.status !== "running") return resultOf(v);
    }
  }
  async resumeStreamed(options: ResumeOptions = {}): Promise<RunStream> {
    const cursors: Cursors = options.cursors ? { ...options.cursors } : {};
    if (!options.cursors) {
      for (const id of this.memberIds()) {
        const snapshot = record(
          await this.harness.read(
            "session/attach",
            { session_id: id },
            options.signal,
          ),
        );
        cursors[id] = Number(snapshot.cursor);
      }
    }
    return new RunStream(this.harness, this.id, cursors, options);
  }
  private async start(
    input: string,
    submissionId: string,
    signal?: AbortSignal,
  ): Promise<Record<string, unknown>> {
    aborted(signal);
    try {
      return record(
        await this.harness.request("run/start", {
          session_id: this.id,
          submission_id: submissionId,
          text: input,
        }),
      );
    } catch (e) {
      if (e instanceof HarnessError)
        throw new HarnessError(e.code, e.message, {
          method: "run/start",
          sessionId: this.id,
          submissionId,
        });
      throw e;
    }
  }
  async respond(interruption: Interruption, response: Response): Promise<void> {
    if ("defer" in response) return;
    if (
      (interruption.kind === "approval" && !("decision" in response)) ||
      (interruption.kind === "question" && !("answer" in response))
    ) {
      throw new HarnessError(
        "InvalidInput",
        "Approve/deny approvals; answer questions",
      );
    }
    // Membership can grow while the run streams. The host validates the live
    // roster; a stale local SessionRef must not reject a newly spawned child.
    await this.harness.request("session/send", {
      session_id: this.id,
      submission_id: randomUUID(),
      input: {
        type: interruption.kind === "approval" ? "approval" : "answer",
        target_session_id: interruption.session_id,
        interaction_id: interruption.id,
        ...("decision" in response
          ? { decision: response.decision }
          : { text: response.answer }),
      },
    });
  }
  async cancel(options: { endTeam?: boolean } = {}): Promise<void> {
    checkMemberErrors(
      await this.harness.request("run/cancel", {
        session_id: this.id,
        team: options.endTeam ?? false,
      }),
    );
  }
  async close(): Promise<void> {
    checkMemberErrors(
      await this.harness.request("agent/close", { session_id: this.id }),
    );
  }
}

/** Pull-based journal stream. Only one iterator may consume it at a time. */
/** Journal readers and result waits are independent; neither drives agent execution. */
export class Run {
  constructor(
    private harness: Harness,
    private session: Session,
    public readonly id: string,
    private cursors: Cursors,
    private options: RunOptions,
  ) {}
  events(): RunStream {
    return new RunStream(this.harness, this.session.id, this.cursors, {
      ...this.options,
      submissionId: this.id,
    });
  }
  result(): Promise<RunResult> {
    return this.session.resume({ ...this.options, submissionId: this.id });
  }
}

export class RunStream implements AsyncIterable<RunEvent> {
  private current?: RunResult;
  private consuming = false;
  private saved: Cursors;
  private iterator: AsyncGenerator<RunEvent, RunResult>;
  constructor(
    harness: Harness,
    sessionId: string,
    cursors: Cursors,
    options: RunOptions,
  ) {
    this.saved = { ...cursors };
    this.submissionId = options.submissionId;
    this.iterator = this.produce(harness, sessionId, { ...options });
  }
  /** Save after handling an event. Replays are at-least-once across app crashes. */
  checkpoint(): { submissionId?: string; cursors: Cursors } {
    return { submissionId: this.submissionId, cursors: { ...this.saved } };
  }
  private submissionId?: string;
  private async *produce(
    harness: Harness,
    sessionId: string,
    options: RunOptions,
  ): AsyncGenerator<RunEvent, RunResult> {
    this.submissionId = options.submissionId;
    let cursors = { ...this.saved };
    while (true) {
      const state = record(
        await harness.read(
          "run/wait",
          {
            session_id: sessionId,
            submission_id: options.submissionId,
            cursors,
            wait_ms: 1000,
            max_events: 128,
          },
          options.signal,
        ),
      );
      if (!Array.isArray(state.events) || !state.cursors)
        throw new HarnessError("Protocol", "Missing streamed journal page");
      for (const raw of state.events) {
        aborted(options.signal);
        const item = record(raw);
        const event = record(item.event);
        const sid = String(item.session_id);
        if (!Number.isSafeInteger(event.event_id))
          throw new HarnessError("Protocol", "Invalid journal cursor");
        this.saved[sid] = Number(event.event_id);
        yield {
          agent: String(item.agent),
          ...(typeof item.agent_id === "string"
            ? { agentId: item.agent_id }
            : {}),
          ...(typeof item.team_id === "string" ? { teamId: item.team_id } : {}),
          sessionId: sid,
          event: event as unknown as RunEvent["event"],
          summary: (item.summary ?? null) as RunEvent["summary"],
        };
      }
      cursors = state.cursors as Cursors;
      this.saved = { ...cursors };
      if (state.status !== "running" && state.more_events !== true) {
        this.current = resultOf(state);
        return this.current;
      }
    }
  }
  async *[Symbol.asyncIterator](): AsyncGenerator<RunEvent> {
    if (this.consuming)
      throw new HarnessError("InvalidInput", "Stream already has a consumer");
    this.consuming = true;
    try {
      while (true) {
        const next = await this.iterator.next();
        if (next.done) return;
        yield next.value;
      }
    } finally {
      this.consuming = false;
    }
  }
  /** Drain remaining events if necessary, then return completion or interruptions. */
  async result(): Promise<RunResult> {
    if (this.consuming)
      throw new HarnessError(
        "InvalidInput",
        "Finish consuming the stream before reading its result",
      );
    if (!this.current)
      for await (const _ of this) {
        /* drain */
      }
    if (!this.current)
      throw new HarnessError(
        "Protocol",
        "Stream ended without a terminal state",
      );
    return this.current;
  }
}

export class Team {
  constructor(
    private readonly harness: Harness,
    public readonly id: string,
  ) {}
  async board(): Promise<TeamBoard> {
    const snap = record(
      await this.harness.request("team/snapshot", { team_id: this.id }),
    );
    return (snap.board ?? snap) as TeamBoard;
  }
  events(after = 0): Promise<unknown> {
    return this.harness.request("team/events", { team_id: this.id, after });
  }
  createItem(spec: ItemSpec, requestId?: string): Promise<TeamItem> {
    return this.item(
      "team_create_item",
      { description: "", dependencies: [], ...spec },
      requestId,
    );
  }
  assign(
    itemId: string,
    revision: number,
    workerSession: string,
    requestId?: string,
  ): Promise<TeamItem> {
    return this.item(
      "team_assign",
      {
        item_id: itemId,
        expected_revision: revision,
        session_id: workerSession,
      },
      requestId,
    );
  }
  transition(
    itemId: string,
    revision: number,
    status: ItemStatus,
    requestId?: string,
  ): Promise<TeamItem> {
    return this.item(
      "team_transition",
      { item_id: itemId, expected_revision: revision, status },
      requestId,
    );
  }
  postMessage(
    body: string,
    options: { itemId?: string; recipient?: string; requestId?: string } = {},
  ): Promise<unknown> {
    return this.command(
      "team_post_message",
      {
        body,
        item_id: options.itemId ?? "",
        recipient: options.recipient ?? "",
      },
      options.requestId,
    );
  }
  cancel(requestId?: string): Promise<unknown> {
    return this.command("team_cancel", {}, requestId);
  }
  async command(
    operation: string,
    args: unknown,
    requestId: string = randomUUID(),
  ): Promise<unknown> {
    try {
      return await this.harness.request("team/command", {
        team_id: this.id,
        command: { request_id: requestId, operation, arguments: args },
      });
    } catch (e) {
      if (e instanceof HarnessError)
        throw new HarnessError(e.code, e.message, {
          method: "team/command",
          requestId,
        });
      throw e;
    }
  }
  private async item(
    operation: string,
    args: unknown,
    requestId?: string,
  ): Promise<TeamItem> {
    const value = record(await this.command(operation, args, requestId));
    const item = record(record(value.data).item);
    if (typeof item.id !== "string" || typeof item.revision !== "number")
      throw new HarnessError("Protocol", "Missing team item");
    return item as unknown as TeamItem;
  }
}

/** Named multi-instance teams; the Rust host owns all coordination decisions. */
export interface AgentTeamOptions {
  agentTypes: string[];
  agentId?: string;
  communication?: { board: boolean; directMessages: boolean };
  maxInstances?: number;
  /** App-owned custom-role ceiling; omission disables team_define_type.
   * Custom roles currently share the team's authorized execution environment. */
  delegation?: { tools: string[]; models?: { provider: string; name: string }[] };
  limits?: {
    maxModelCalls: number;
    maxOutputTokens: number;
    summaryTokens?: number;
  };
}
export interface AgentTeamRef {
  lead: SessionRef;
  teamId: string;
}
export class AgentTeam {
  constructor(
    private harness: Harness,
    readonly lead: Session,
    readonly id: string,
  ) {}
  reference(): AgentTeamRef {
    return { lead: this.lead.reference(), teamId: this.id };
  }
  async snapshot(): Promise<Record<string, unknown>> {
    return record(await this.query("snapshot", {}));
  }
  history(
    options: {
      messageId?: string;
      direction?: "before" | "after";
      limit?: number;
    } = {},
  ): Promise<unknown> {
    return this.query("team_read_board", {
      message_id: options.messageId ?? null,
      direction: options.direction ?? "before",
      limit: options.limit ?? 20,
    });
  }
  spawn(
    requestId: string,
    agentType: string,
    agentId: string,
    message: string,
    options: ChildSpawnOptions = {},
  ): Promise<unknown> {
    return this.command(requestId, "team_spawn", {
      ...options,
      agent_type: agentType,
      agent_id: agentId,
      message,
      context: options.context ?? {mode:"fresh"},
    });
  }
  post(requestId: string, message: string): Promise<unknown> {
    return this.command(requestId, "team_post_board", { message });
  }
  sendMessage(
    requestId: string,
    agentId: string,
    message: string,
  ): Promise<unknown> {
    return this.command(requestId, "team_send_message", {
      agent_id: agentId,
      message,
    });
  }
  async stop(requestId: string): Promise<unknown> {
    return this.command(requestId, "stop", {});
  }
  private command(
    request_id: string,
    operation: string,
    args: unknown,
  ): Promise<unknown> {
    return this.query("command", { request_id, operation, arguments: args });
  }
  private query(operation: string, args: unknown): Promise<unknown> {
    return this.harness.request("agent-team/command", {
      session_id: this.lead.id,
      operation,
      arguments: args,
    });
  }
}
