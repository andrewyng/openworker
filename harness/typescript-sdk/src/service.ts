import { validateIntegrationOptions } from "./integration-options.js";
import {stageInput,type UserInput} from "./attachments.js";
import {HarnessError} from "./types.js";
import { ServiceObservability } from "./observability.js";
/** Authenticated remote service handles. The service owns all execution and policy. */
import { randomUUID } from "node:crypto";
import {sandboxWire, serviceDelegationWire, type ServiceTeamDelegation} from "./team-placement.js";
import {
  type Json,
  type JournalEvent,
  type RunEvent,
  type TurnLimits,
  type ChildSpawnOptions,
} from "./types.js";

export interface ServiceOptions {
  url: string;
  fallbackUrls?: string[];
  namespace: string;
  credentials:
    | { tokenEnv: string }
    | {
        getToken: (
          reason: "connect" | "request" | "rejected",
        ) => Promise<string>;
      };
  requestTimeoutMs?: number;
}
export interface RuntimeProfile {
  id: string;
  provider: "openshell";
  cpu: number;
  memoryMB: number;
  sharing: "exclusive" | "shared";
  allowedPrincipals?: string[];
  /** Cluster worker by default. Sandbox placement requires a compatible OpenShell installation. */
  loopPlacement?: "service" | "sandbox";
  /** Workspace-relative directories for trusted first-party file reads. */
  allowWorkspaceReads?: string[];
  approvalMode?: "ask" | "auto-approve" | "bypass";
  autoSkipQuestions?: boolean;
  /** Versioned operator-installed OpenShell Git grant; never a token. */
  gitAccess?: string;
  /** Trusted host repository broker; does not grant agent-side Git credentials. */
  repositoryAccess?: string;
}
/** Exact named sets for a New sandbox or delegation ceiling. Rust validates
 * availability, approval and the lead's actual authority on every admission. */
export function runtimeProfileGrants(profile: RuntimeProfile): {
  files: string[]; network: string[]; credentials: string[]; cpu: number; memoryMB: number;
} {
  if (profile.gitAccess !== undefined &&
      (typeof profile.gitAccess !== "string" || !/^[\w.-]+@[\w.-]+$/.test(profile.gitAccess)))
    throw new Error("Git access requires a versioned operator grant name");
  const named = profile.gitAccess === undefined ? [] : [`git:${profile.gitAccess}`];
  return {files: [], network: [...named], credentials: [...named], cpu: profile.cpu, memoryMB: profile.memoryMB};
}
interface MutationOptions { idempotencyKey?: string }
export interface ClusterModelChoice { connection: string; model: string }
export interface ClusterModelProvider {
  name: string;
  baseUrl: string;
  api: "responses" | "chat-completions" | "anthropic";
  contextLimit: number;
  allowPrivateHttp?: boolean;
}
export interface AgentRelease {
  integrations?: Record<string, import("./types.js").IntegrationOptions>;
  subagents?: { enabled: boolean; maxConcurrent?: number; maxChildren?: number; limits?: TurnLimits };
  limits?: TurnLimits;
  id: string;
  instructions: string;
  tools: string[];
  runtimeProfiles: string[];
  defaultRuntimeProfile?: string;
  allowedPrincipals?: string[];
  modelProfile?: string;
  defaultModel?: { connection: string; model: string; notify_on_session_state?: boolean };
  reviewerModel?: { connection: string; model: string; notify_on_session_state?: boolean };
  modelOverrides?: { connection: string; model: string; notify_on_session_state?: boolean }[];
}
export interface ServiceCatalogEntry {
  id: string;
  kind: string;
  digest: string;
  definition: RuntimeProfile | AgentRelease;
  installation: string;
  activated: boolean;
}
export interface ServiceOperation {
  id: string;
  runId?: string | null;
  submissionId?: string | null;
  sessionId: string;
  environmentId: string;
  releaseId: string;
  profileId: string;
  principal: string;
  project: string;
  status: string;
  receipt: ServiceSessionRef | null;
  error: string | null;
}
export interface ServiceSessionOptions {
  workspaceId?: string;
  integrations?: Record<string, import("./types.js").IntegrationOptions>;
  limits?: TurnLimits;
  environmentId?: string;
  runtimeProfile?: string;
  project?: string;
  model?: { connection: string; model: string; notify_on_session_state?: boolean };
  name?: string;
  idempotencyKey?: string;
  signal?: AbortSignal;
  waitTimeoutMs?: number;
}
export interface ServiceSessionRef {
  loopPlacement?: "service" | "sandbox";
  loopEnvironmentId?: string;
  workspaceId?: string;
  serviceId: string;
  namespace: string;
  sessionId: string;
  agent: string;
  environmentId: string;
  releaseId?: string;
  runtimeProfile?: string;
  operationId?: string;
}
/** Durable interaction identity and policy pins supplied by the service. */
export interface ServiceInteraction {
  id: string;
  turn_id: string;
  kind: string;
  call: Json;
  bundle_digest: string;
  schema_digest: string;
  policy_digest: string;
  resource_policy_digest: string;
  response: string | null;
}
export interface ServiceResult {
  status: "running" | "completed" | "failed" | "cancelled" | "interrupted";
  sessionId: string;
  runId: string;
  submissionId: string;
  cursor: number;
  finalOutput: string | null;
  failure: string | null;
  interruptions: ServiceInteraction[];
}
export class ServiceError extends Error {
  constructor(
    public code: string,
    public status: number,
    public idempotencyKey?: string,
    public operationId?: string,
  ) {
    super(`OpenWorker service: ${code}`);
  }
}
const segment = (s: string) => {
  if (!s || s.length > 256 || /[\u0000-\u001f]/.test(s))
    throw new ServiceError("invalid_identifier", 0);
  return encodeURIComponent(s);
};
export function serviceUrl(raw: string): string {
  const u = new URL(raw);
  const local = ["localhost", "127.0.0.1", "[::1]"].includes(u.hostname);
  if (
    (u.protocol !== "https:" && !(u.protocol === "http:" && local)) ||
    u.username ||
    u.password ||
    u.search ||
    u.hash
  )
    throw new ServiceError("invalid_service_url", 0);
  return u.toString().replace(/\/$/, "");
}
/** Bounded SSE parser; partial UTF-8/lines and comments/heartbeats are supported. */
export async function* serviceFrames(
  response: Response,
  idleTimeoutMs = 45000,
): AsyncGenerator<{ id: number; event: string; data: unknown }> {
  if (!response.body) throw new ServiceError("missing_event_stream", 0);
  const reader = response.body.getReader(),
    decoder = new TextDecoder("utf-8", { fatal: true });
  let pending = "",
    data: string[] = [],
    event = "message",
    id = "",
    size = 0;
  try {
    while (true) {
      let timer: ReturnType<typeof setTimeout> | undefined;
      const r = await Promise.race([
        reader.read(),
        new Promise<never>((_, reject) => {
          timer = setTimeout(
            () => reject(new ServiceError("unavailable", 0)),
            idleTimeoutMs,
          );
        }),
      ]).finally(() => clearTimeout(timer));
      if (r.done) break;
      pending += decoder.decode(r.value, { stream: true });
      if (pending.length > 1048576)
        throw new ServiceError("event_too_large", 0);
      let pos;
      while ((pos = pending.indexOf("\n")) >= 0) {
        let line = pending.slice(0, pos).replace(/\r$/, "");
        pending = pending.slice(pos + 1);
        size += line.length;
        if (size > 1048576) throw new ServiceError("event_too_large", 0);
        if (!line) {
          if (data.length) {
            if (event === "stream_error")
              throw new ServiceError("stream_reconcile", 503);
            if (
              event !== "progress" ||
              !/^\d+$/.test(id) ||
              !Number.isSafeInteger(Number(id))
            )
              throw new ServiceError("invalid_event", 0);
            yield { id: Number(id), event, data: JSON.parse(data.join("\n")) };
          }
          data = [];
          event = "message";
          id = "";
          size = 0;
          continue;
        }
        if (line.startsWith(":")) continue;
        const colon = line.indexOf(":");
        const field = colon < 0 ? line : line.slice(0, colon);
        const value = colon < 0 ? "" : line.slice(colon + 1).replace(/^ /, "");
        if (field === "data") data.push(value);
        else if (field === "event") event = value;
        else if (field === "id") id = value;
      }
    }
    // No partial final frame is accepted; reconnect from the last delivered cursor.
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}
const workspaceErrors = new Set([
  "invalid_workspace_request", "workspace_not_found", "workspace_handle_expired",
  "workspace_data_mismatch", "workspace_profile_mismatch", "workspace_in_use", "workspace_shared_mount_unsupported",
]);
export interface WorkspacePreview { port: number; url: string; accessToken: string }
export interface WorkspaceRef { workspaceId: string; dataId: string; previews?: WorkspacePreview[] }
export interface WorkspaceRequest { key: string; dataId?: string; options?: Record<string, Json> }
export interface WorkspaceProviderOptions { name: string; options?: Record<string, Json> }

export class ServiceWorkspaceProvider {
  private constructor(private readonly service: Service, private handle: string,
                      private readonly restart: () => Promise<string>) {}
  get id(): string { return this.handle; }

  static async start(service: Service, input: WorkspaceProviderOptions): Promise<ServiceWorkspaceProvider> {
    if (service.capabilities.features?.workspaceProviders !== true)
      throw new ServiceError("unsupported_workspace_providers", 0);
    // Startup options can contain credentials. Keep the private copy in memory.
    const settings = structuredClone(input);
    const start = async () => {
      const result = await service.json("POST", "/v1/workspace-providers", settings);
      if (typeof result?.id !== "string") throw new ServiceError("invalid_workspace_provider", 0);
      return result.id as string;
    };
    return new ServiceWorkspaceProvider(service, await start(), start);
  }

  private async call(operation: string, body: unknown): Promise<any> {
    const invoke = () => this.service.json("POST", "/v1/workspace-providers/" + segment(this.handle) + "/" + operation, body);
    try { return await invoke(); }
    catch (error) {
      // Only retry when the server confirms the handle was absent before
      // dispatch. Connection loss alone gives no such assurance.
      if (!(error instanceof ServiceError) || error.code !== "workspace_handle_expired") throw error;
      this.handle = await this.restart();
      return invoke();
    }
  }
  async stopWorkspace(workspaceId: string): Promise<void> {
    await this.call("stop", { workspaceId });
  }
  async deleteData(dataId: string): Promise<void> {
    await this.call("delete-data", { dataId });
  }
  async getOrCreate(request: WorkspaceRequest): Promise<WorkspaceRef> {
    const ref = await this.call("workspaces", request);
    if (typeof ref?.workspaceId !== "string" || typeof ref?.dataId !== "string" ||
        (request.dataId !== undefined && ref.dataId !== request.dataId))
      throw new ServiceError("invalid_workspace_reference", 0);
    if (ref.previews !== undefined && (!Array.isArray(ref.previews) || ref.previews.length > 15 ||
        ref.previews.some((slot: WorkspacePreview, i: number) => !slot || slot.port !== 5100 + i ||
          typeof slot.url !== "string" || !slot.url.startsWith("https://") ||
          typeof slot.accessToken !== "string" || !slot.accessToken))) {
      throw new ServiceError("invalid_workspace_reference", 0);
    }
    return { workspaceId: ref.workspaceId, dataId: ref.dataId, ...(ref.previews && { previews: ref.previews }) };
  }
}
export class Service {
  readonly workspaces = {
    startProvider: (input: WorkspaceProviderOptions) => ServiceWorkspaceProvider.start(this, input),
  };
  get observability():ServiceObservability {return new ServiceObservability(path=>this.json("GET",path));}
  private closed = false;
  private requests = new Set<AbortController>();
  private endpoint = 0;
  private constructor(
    private options: ServiceOptions,
    private urls: string[],
    public readonly capabilities: Record<string, any>,
    public readonly identity: Record<string, any>,
  ) {}
  static async connect(options: ServiceOptions): Promise<Service> {
    if (!options.namespace || options.namespace.length > 80)
      throw new ServiceError("invalid_namespace", 0);
    const urls = [options.url, ...(options.fallbackUrls || [])].map(serviceUrl);
    if (urls.length > 8) throw new ServiceError("too_many_endpoints", 0);
    const s = new Service(options, urls, {}, {});
    try {
      for (let i = 0; i < urls.length; i++) {
        s.endpoint = i;
        const cap = await s.json("GET", "/v1/capabilities");
        if (
          cap.apiVersion !== "1" ||
          cap.namespace !== options.namespace ||
          typeof cap.serviceId !== "string"
        )
          throw new ServiceError("incompatible_service", 0);
        if (i === 0) Object.assign(s.capabilities, cap);
        else if (cap.serviceId !== s.capabilities.serviceId)
          throw new ServiceError("service_identity_mismatch", 0);
      }
      s.endpoint = 0;
      Object.assign(s.identity, await s.json("GET", "/v1/auth/me"));
      Object.freeze(s.capabilities);
      Object.freeze(s.identity);
      return s;
    } catch (e) {
      s.disconnect();
      throw e;
    }
  }
  private async token(
    reason: "connect" | "request" | "rejected",
  ): Promise<string> {
    const c = this.options.credentials;
    const token =
      "tokenEnv" in c ? process.env[c.tokenEnv] : await c.getToken(reason);
    if (!token || !/^[\x21-\x7e]{32,4096}$/.test(token))
      throw new ServiceError("credential_unavailable", 0);
    return token;
  }
  async request(
    method: string,
    path: string,
    body?: unknown,
    key?: string,
    stream = false,
    cursor?: number,
  ): Promise<{ response: Response; close: () => void }> {
    if (this.closed) throw new ServiceError("disconnected", 0);
    if (
      key !== undefined &&
      (!key || key.length > 100 || !/^[\x21-\x7e]+$/.test(key))
    )
      throw new ServiceError("invalid_idempotency_key", 0);
    const controller = new AbortController();
    this.requests.add(controller);
    const close = () => {
      controller.abort();
      this.requests.delete(controller);
    };
    let timer: ReturnType<typeof setTimeout> | undefined = setTimeout(
      () => controller.abort(),
      this.options.requestTimeoutMs ?? 30000,
    );
    try {
      for (let attempt = 0; attempt < 2; attempt++) {
        const token = await this.token(
          attempt
            ? "rejected"
            : this.capabilities.serviceId
              ? "request"
              : "connect",
        );
        const headers: Record<string, string> = {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
          "x-openworker-namespace": this.options.namespace,
        };
        if (this.capabilities.serviceId)
          headers["x-openworker-service-id"] = this.capabilities.serviceId;
        if (key) headers["idempotency-key"] = key;
        if (cursor !== undefined) headers["Last-Event-ID"] = String(cursor);
        const response = await fetch(this.urls[this.endpoint] + path, {
          method,
          headers,
          body: body === undefined ? undefined : JSON.stringify(body),
          signal: controller.signal,
          redirect: "manual",
        });
        if (
          response.status === 401 &&
          attempt === 0 &&
          "getToken" in this.options.credentials
        ) {
          await response.body?.cancel();
          continue;
        }
        if (!response.ok) {
          await response.body?.cancel();
          const workspaceError = response.headers.get("x-openworker-workspace-error");
          if (path.startsWith("/v1/workspace-providers") &&
              response.headers.get("x-openworker-service-id") === this.capabilities.serviceId &&
              workspaceError && workspaceErrors.has(workspaceError))
            throw new ServiceError(workspaceError, response.status, key);
          throw new ServiceError(
            response.status === 401
              ? "unauthorized"
              : response.status === 403
                ? "forbidden"
                : response.status === 409
                  ? "conflict"
                  : "request_rejected",
            response.status,
            key,
          );
        }
        if (
          this.capabilities.serviceId &&
          response.headers.get("x-openworker-service-id") !==
            this.capabilities.serviceId
        )
          throw new ServiceError("service_identity_mismatch", 0);
        if (
          response.headers.get("x-openworker-namespace") !==
          this.options.namespace
        )
          throw new ServiceError("namespace_mismatch", 0);
        if (stream) {
          if (
            !response.headers.get("content-type")?.includes("text/event-stream")
          )
            throw new ServiceError("invalid_event_stream", 0);
          clearTimeout(timer);
          timer = undefined;
        }
        return {
          response,
          close: () => {
            clearTimeout(timer);
            close();
          },
        };
      }
      throw new ServiceError("unauthorized", 401);
    } catch (e) {
      clearTimeout(timer);
      close();
      if (e instanceof ServiceError) throw e;
      throw new ServiceError(
        this.closed
          ? "disconnected"
          : method === "GET"
            ? "unavailable"
            : "outcome_unknown",
        0,
        key,
      );
    }
  }
  async json(
    method: string,
    path: string,
    body?: unknown,
    key?: string,
  ): Promise<any> {
    const { response, close } = await this.request(method, path, body, key);
    try {
      const reader = response.body?.getReader();
      if (!reader) throw new ServiceError("invalid_response", 0);
      let bytes = 0,
        text = "";
      const d = new TextDecoder("utf-8", { fatal: true });
      while (true) {
        const r = await reader.read();
        if (r.done) break;
        bytes += r.value.length;
        if (bytes > 1048576) throw new ServiceError("response_too_large", 0);
        text += d.decode(r.value, { stream: true });
      }
      return JSON.parse(text + d.decode());
    } catch (e) {
      if (e instanceof ServiceError) throw e;
      throw new ServiceError(
        method === "GET" ? "invalid_response" : "outcome_unknown",
        0,
        key,
      );
    } finally {
      close();
    }
  }
  listModelProviders(): Promise<{
    providers: (ClusterModelProvider & { enabled: boolean })[];
    profiles: {name: string; model: ClusterModelChoice}[];
  }> {
    return this.json("GET", "/v1/model-providers");
  }

  async getModelProvider(name: string): Promise<ClusterModelProvider & {enabled: boolean}> {
    const provider = (await this.listModelProviders()).providers.find(p => p.name === name);
    if (!provider) throw new ServiceError("model_provider_not_found", 404);
    return provider;
  }

  registerModelProvider(provider: ClusterModelProvider, apiKey: string, options: MutationOptions = {}): Promise<ClusterModelProvider> {
    return this.json("POST", "/v1/model-providers", {provider, apiKey}, options.idempotencyKey ?? randomUUID());
  }

  rotateModelProviderKey(name: string, apiKey: string, options: MutationOptions = {}): Promise<{name: string}> {
    return this.json("POST", `/v1/model-providers/${segment(name)}/rotate`, {apiKey}, options.idempotencyKey ?? randomUUID());
  }

  disableModelProvider(name: string, options: MutationOptions = {}): Promise<{name: string; enabled: boolean}> {
    return this.json("POST", `/v1/model-providers/${segment(name)}/disable`, {}, options.idempotencyKey ?? randomUUID());
  }

  setModelProfile(name: string, model: ClusterModelChoice, options: MutationOptions = {}): Promise<{name: string; model: ClusterModelChoice}> {
    return this.json("PUT", `/v1/model-profiles/${segment(name)}`, model, options.idempotencyKey ?? randomUUID());
  }

  shareAppAgent(releaseId: string, sharedWith: string[], options: MutationOptions = {}): Promise<{releaseId: string; sharedWith: string[]}> {
    return this.json("PUT", `/v1/app-agents/${segment(releaseId)}/sharing`, {sharedWith}, options.idempotencyKey ?? randomUUID());
  }

  /** Publish a private revision using this app's backend token. */
  async publishAppAgent(name: string, definition: Omit<AgentRelease, "id" | "allowedPrincipals">, options: MutationOptions = {}): Promise<ServiceAgent> {
    const entry = await this.json("POST", `/v1/app-agents/${segment(name)}`, definition, options.idempotencyKey ?? randomUUID());
    return new ServiceAgent(this, entry.id);
  }

  listAppAgents(): Promise<{agents: { owner: string; name: string; revision: number; releaseId: string; sharedWith?: string[] }[]}> {
    return this.json("GET", "/v1/app-agents");
  }

  async appAgent(name: string, owner: string = this.identity.principal): Promise<ServiceAgent> {
    const {agents} = await this.listAppAgents();
    const entry = agents.find(agent => agent.name === name && agent.owner === owner);
    if (!entry) throw new ServiceError("agent_not_found", 404);
    return new ServiceAgent(this, entry.releaseId);
  }

  async agent(id: string): Promise<ServiceAgent> {
    const a = await this.json("GET", `/v1/agents/${segment(id)}`);
    if (typeof a.id !== "string" || !a.id)
      throw new ServiceError("invalid_agent", 0);
    return new ServiceAgent(this, a.id);
  }
  async resume(ref: ServiceSessionRef): Promise<ServiceSession> {
    if (
      ref.serviceId !== this.capabilities.serviceId ||
      ref.namespace !== this.options.namespace
    )
      throw new ServiceError("reference_identity_mismatch", 0);
    // A session-scoped observer can read the pinned session identity without catalog access.
    if (typeof ref.agent !== "string" || !ref.agent)
      throw new ServiceError("reference_identity_mismatch", 0);
    const session = new ServiceSession(this, structuredClone(ref));
    const snapshot = await session.snapshot();
    if ((snapshot.service?.releaseId ?? this.capabilities.agent) !== ref.agent)
      throw new ServiceError("release_identity_mismatch", 0);
    if (
      ref.runtimeProfile &&
      snapshot.service?.runtimeProfile !== ref.runtimeProfile
    )
      throw new ServiceError("profile_identity_mismatch", 0);
    if ((ref.loopPlacement && snapshot.session?.loopPlacement !== ref.loopPlacement) ||
        (ref.loopEnvironmentId && snapshot.session?.loopEnvironmentId !== ref.loopEnvironmentId))
      throw new ServiceError("loop_identity_mismatch", 0);
    if ((snapshot.session?.workspaceId ?? undefined) !== (ref.workspaceId ?? undefined))
      throw new ServiceError("workspace_identity_mismatch", 0);
    if (snapshot.session?.environment?.environment_id !== ref.environmentId)
      throw new ServiceError("environment_identity_mismatch", 0);
    return session;
  }
  async *events(sid: string, after = 0): AsyncGenerator<JournalEvent> {
    if (!Number.isSafeInteger(after) || after < 0)
      throw new ServiceError("invalid_cursor", 0);
    let failures = 0;
    while (!this.closed) {
      let connection: Awaited<ReturnType<Service["request"]>> | undefined;
      try {
        connection = await this.request(
          "GET",
          `/v1/sessions/${segment(sid)}/events`,
          undefined,
          undefined,
          true,
          after,
        );
        for await (const frame of serviceFrames(connection.response)) {
          const e = frame.data as JournalEvent;
          if (
            e.session_id !== sid ||
            e.event_id !== frame.id ||
            typeof e.type !== "string"
          )
            throw new ServiceError("invalid_event", 0);
          if (frame.id <= after) continue;
          after = frame.id;
          failures = 0;
          yield e;
        }
      } catch (e) {
        if (this.closed) return;
        if (
          e instanceof ServiceError &&
          (e.status === 401 ||
            e.status === 403 ||
            !["unavailable", "stream_reconcile"].includes(e.code))
        )
          throw e;
      } finally {
        connection?.close();
      }
      if (++failures > 5) throw new ServiceError("reconnect_exhausted", 0);
      this.endpoint = (this.endpoint + 1) % this.urls.length;
      await new Promise((r) =>
        setTimeout(r, Math.min(100 * 2 ** failures, 2000)),
      );
    }
  }
  async submit(
    agent: string,
    text: string,
    options: Omit<ServiceSessionOptions, "signal" | "waitTimeoutMs"> = {},
  ): Promise<ServiceOperation> {
    if (this.capabilities.features?.combinedSubmission !== true)
      throw new ServiceError("unsupported_combined_submission", 0);
    if (options.environmentId && options.runtimeProfile)
      throw new ServiceError("conflicting_environment_options", 0);
    const { idempotencyKey = randomUUID(), ...session } = options;
    return (
      await this.json(
        "POST",
        "/v1/submissions",
        { agent, text, ...session },
        idempotencyKey,
      )
    ).operation;
  }
  registerRuntimeProfile(
    profile: RuntimeProfile,
    options: { idempotencyKey?: string } = {},
  ): Promise<ServiceCatalogEntry> {
    return this.json(
      "POST",
      "/v1/profiles",
      profile,
      options.idempotencyKey ?? randomUUID(),
    );
  }
  activateRuntimeProfile(
    id: string,
    options: { alias?: string; idempotencyKey?: string } = {},
  ): Promise<ServiceCatalogEntry> {
    return this.json(
      "POST",
      `/v1/profiles/${segment(id)}/activate`,
      { alias: options.alias ?? null },
      options.idempotencyKey ?? randomUUID(),
    );
  }
  deployAgent(
    release: AgentRelease,
    options: { idempotencyKey?: string } = {},
  ): Promise<ServiceCatalogEntry> {
    return this.json(
      "POST",
      "/v1/releases",
      release,
      options.idempotencyKey ?? randomUUID(),
    );
  }
  activateAgent(
    id: string,
    options: { alias?: string; idempotencyKey?: string } = {},
  ): Promise<ServiceCatalogEntry> {
    return this.json(
      "POST",
      `/v1/releases/${segment(id)}/activate`,
      { alias: options.alias ?? null },
      options.idempotencyKey ?? randomUUID(),
    );
  }
  environment(id: string): Promise<any> {
    return this.json("GET", `/v1/environments/${segment(id)}`);
  }
  operation(id: string): Promise<ServiceOperation> {
    return this.json("GET", `/v1/operations/${segment(id)}`);
  }
  async waitOperation(
    id: string,
    options: {
      signal?: AbortSignal;
      timeoutMs?: number;
      idempotencyKey?: string;
    } = {},
  ): Promise<ServiceOperation> {
    const until = Date.now() + (options.timeoutMs ?? 300000);
    while (true) {
      if (options.signal?.aborted || this.closed)
        throw new ServiceError("wait_cancelled", 0, options.idempotencyKey, id);
      if (Date.now() >= until)
        throw new ServiceError(
          "provisioning_pending",
          0,
          options.idempotencyKey,
          id,
        );
      let op: ServiceOperation;
      try {
        op = await this.operation(id);
      } catch (error) {
        if (error instanceof ServiceError) {
          error.operationId = id;
          error.idempotencyKey ??= options.idempotencyKey;
        }
        throw error;
      }
      if (op.status === "complete") return op;
      if (op.status === "failed")
        throw new ServiceError(
          "provisioning_failed",
          0,
          options.idempotencyKey,
          id,
        );
      // Management readiness only; tool output still uses cursor-based SSE.
      await new Promise((r) => setTimeout(r, 250));
    }
  }
  disconnect(): void {
    this.closed = true;
    for (const c of this.requests) c.abort();
    this.requests.clear();
  }
}
export class ServiceAgent {
  constructor(
    private service: Service,
    public readonly id: string,
  ) {}
  async newSession(
    options: Omit<ServiceSessionOptions, "limits"> = {},
  ): Promise<ServiceSession> {
    validateIntegrationOptions(options.integrations);
    if (
      Object.keys(options).some(
        (k) =>
          ![
            "workspaceId",
            "environmentId",
            "runtimeProfile",
            "integrations",
            "name",
            "project",
            "model",
            "idempotencyKey",
            "signal",
            "waitTimeoutMs",
          ].includes(k),
      )
    )
      throw new ServiceError("unsupported_session_option", 0);
    if (options.environmentId && options.runtimeProfile)
      throw new ServiceError("conflicting_environment_options", 0);
    const {
      idempotencyKey = randomUUID(),
      signal,
      waitTimeoutMs,
      ...parameters
    } = options;
    let r = await this.service.json(
      "POST",
      "/v1/sessions",
      { agent: this.id, ...parameters },
      idempotencyKey,
    );
    if (r.operation) {
      r = (
        await this.service.waitOperation(r.operation.id, {
          signal,
          timeoutMs: waitTimeoutMs,
          idempotencyKey,
        })
      ).receipt;
    }
    if (
      r?.serviceId !== this.service.capabilities.serviceId ||
      r?.namespace !== this.service.capabilities.namespace ||
      r?.agent !== this.id ||
      typeof r?.environmentId !== "string" ||
      (options.workspaceId && r.workspaceId !== options.workspaceId) ||
      (options.environmentId && r.environmentId !== options.environmentId) ||
      typeof r?.sessionId !== "string"
    )
      throw new ServiceError("invalid_reference", 0, idempotencyKey);
    return new ServiceSession(this.service, r);
  }
}
export class ServiceSession {
  constructor(
    private service: Service,
    private ref: ServiceSessionRef,
  ) {}
  get id() {
    return this.ref.sessionId;
  }
  get environmentId() {
    return this.ref.environmentId;
  }
  async close(options: { idempotencyKey?: string } = {}): Promise<void> {
    await this.service.json("POST", `/v1/sessions/${segment(this.id)}/close`, {}, options.idempotencyKey ?? randomUUID());
  }
  async createTeam(
    options: ServiceAgentTeamOptions,
  ): Promise<ServiceAgentTeam> {
    await this.service.json("POST", `/v1/sessions/${segment(this.id)}/team`, {
      agentId: options.agentId ?? "lead",
      agentTypes: options.agentTypes,
      ...(options.workspaceDefaults ? {workspaceDefaults:{repository:options.workspaceDefaults.repository,base:options.workspaceDefaults.base,agent_types:options.workspaceDefaults.agentTypes}} : {}),
      ...(options.delegation ? {delegation: serviceDelegationWire(options.delegation)} : {}),
      ...(options.coding ? {coding:{source:{repository:options.coding.source.repository,directory:options.coding.source.directory,commit:options.coding.source.commit,object_format:options.coding.source.objectFormat,...(options.coding.source.remoteRef?{remote_ref:options.coding.source.remoteRef}:{})},agent_types:Object.fromEntries(Object.entries(options.coding.agentTypes).map(([name,value])=>[name,{source:value.source,sandbox:sandboxWire(value.sandbox)}]))}} : {}),
      policy: {
        communication: options.communication ?? {
          board: true,
          directMessages: false,
        },
        max_instances: options.maxInstances ?? 32,
      },
      limits: options.limits,
    });
    return new ServiceAgentTeam(this.service, this);
  }
  async resumeTeam(): Promise<ServiceAgentTeam> {
    const team = new ServiceAgentTeam(this.service, this);
    await team.snapshot();
    return team;
  }

  reference(): ServiceSessionRef {
    return structuredClone(this.ref);
  }
  snapshot(): Promise<any> {
    return this.service.json("GET", `/v1/sessions/${segment(this.id)}`);
  }
  async send(
    text: string | UserInput,
    options: { idempotencyKey?: string; limits?: TurnLimits } = {},
  ): Promise<ServiceRun> {
    const key = options.idempotencyKey ?? randomUUID();
    let content:Record<string,unknown>[]|undefined;
    if(typeof text!=="string"){
      if(this.service.capabilities.features?.attachments!==true)throw new HarnessError("Setup","Service does not support attachments");
      content=await stageInput(text,this.id,(_method,args)=>this.service.json("POST",`/v1/sessions/${segment(this.id)}/attachments/${segment(String(args.id))}`,args.upload));
    }
    const r = await this.service.json(
      "POST",
      `/v1/sessions/${segment(this.id)}/turns`,
      { text:typeof text==="string"?text:undefined, content, limits: options.limits },
      key,
    );
    return new ServiceRun(this.service, this.ref, r.submissionId, r.runId);
  }
  async resumeSafety(runId:string,idempotencyKey?:string):Promise<any>{return this.control(runId,{kind:"resume_safety"},{idempotencyKey});}
  async resumeRun(submissionId: string): Promise<ServiceRun> {
    const r = await this.service.json(
      "GET",
      `/v1/sessions/${segment(this.id)}/turns/${segment(submissionId)}`,
    );
    return new ServiceRun(this.service, this.ref, submissionId, r.runId);
  }
  control(
    runId: string,
    action: Json,
    options: { idempotencyKey?: string } = {},
  ): Promise<any> {
    return this.service.json(
      "POST",
      `/v1/sessions/${segment(this.id)}/controls`,
      { runId, action },
      options.idempotencyKey ?? randomUUID(),
    );
  }
  controlReceipt(id: string): Promise<any> {
    return this.service.json(
      "GET",
      `/v1/sessions/${segment(this.id)}/controls/${segment(id)}`,
    );
  }
}
export class ServiceRun {
  constructor(
    private service: Service,
    private ref: ServiceSessionRef,
    public readonly submissionId: string,
    public readonly id: string,
  ) {}
  snapshot(): Promise<ServiceResult> {
    return this.service.json(
      "GET",
      `/v1/sessions/${segment(this.ref.sessionId)}/turns/${segment(this.submissionId)}`,
    );
  }
  async *events(options: { after?: number } = {}): AsyncGenerator<RunEvent> {
    const state = await this.snapshot();
    const after = options.after ?? 0;
    if (state.status !== "running" && after >= state.cursor) return;
    for await (const event of this.service.events(this.ref.sessionId, after)) {
      if (event.turn_id === this.id) {
        yield {
          agent: this.ref.agent,
          sessionId: this.ref.sessionId,
          event,
          summary: null,
        };
        if (
          event.type === "turn_completed" ||
          (event.type === "turn_recovered" &&
            ["failed", "cancelled", "interrupted", "truncated"].includes(
              String((event.data as { status?: unknown } | null)?.status),
            ))
        )
          return;
      }
      if (event.event_id >= state.cursor && state.status !== "running") return;
      if (
        event.turn_id === this.id &&
        [
          "approval_requested",
          "question_requested",
          "interaction_required",
          "safety_waiting",
        ].includes(event.type)
      ) {
        if ((await this.snapshot()).status === "interrupted") return;
      }
    }
  }
  async result(): Promise<ServiceResult> {
    const initial = await this.snapshot();
    if (initial.status !== "running") return initial;
    for await (const _ of this.events({ after: initial.cursor })) {
    }
    return this.snapshot();
  }
}

export interface CodingSource {
  repository: string;
  directory: string;
  objectFormat: "sha1" | "sha256";
  commit: string;
  /** App-owned permitted branch ref; pin commit too. No URL or credential. */
  remoteRef?: string;
}
export interface CodingTeamDefaults {
  source: CodingSource;
  agentTypes: Record<string, {sandbox: NonNullable<ChildSpawnOptions["sandbox"]>; source: "initial" | "require_submission"}>;
}
export interface ServiceAgentTeamOptions {
  /** Artifact-first independent coding checkouts; service artifact store required. */
  coding?: CodingTeamDefaults;
  workspaceDefaults?: import("./types.js").WorkspaceDefaults;
  /** Scoped custom types, bounded by the lead's activated release. */
  delegation?: ServiceTeamDelegation;
  agentId?: string;
  /** Type name → activated immutable release ID, e.g. builder → builder@1. */
  agentTypes: Record<string, string>;
  communication?: { board: boolean; directMessages: boolean };
  maxInstances?: number;
  limits?: TurnLimits;
}
export class ServiceAgentTeam {
  /** Authorize future delegations with the current credential for the same app. */
  reauthorize(): Promise<{sessionId: string; credentialId: string}> {
    return this.service.json("POST", `/v1/sessions/${segment(this.lead.id)}/team/reauthorize`, {});
  }
  constructor(
    private service: Service,
    readonly lead: ServiceSession,
  ) {}
  snapshot(): Promise<any> {
    return this.service.json(
      "GET",
      `/v1/sessions/${segment(this.lead.id)}/team`,
    );
  }
  stop(key: string): Promise<any> {
    return this.command(key, "stop", {});
  }
  spawn(
    key: string,
    agentType: string,
    agentId: string,
    message: string,
    options: ChildSpawnOptions = {},
  ): Promise<any> {
    return this.command(key, "team_spawn", {
      ...options,
      model: options.model ? {connection:options.model.provider,model:options.model.name} : undefined,
      sandbox:sandboxWire(options.sandbox),
      agent_type: agentType,
      agent_id: agentId,
      message,
      context: options.context ?? {mode:"fresh"},
    });
  }
  members(options: {after?: string; limit?: number} = {}): Promise<any> {
    return this.service.json("POST",`/v1/sessions/${segment(this.lead.id)}/team/commands`,
      {operation:"team_manage",arguments:{...options,action:"list"}});
  }
  /** Bounded same-team handoffs; reports remain attributed claims. */
  submissions(options: {after?: string; limit?: number} = {}): Promise<any> {
    return this.service.json("POST",`/v1/sessions/${segment(this.lead.id)}/team/commands`,
      {operation:"team_list_submissions",arguments:{after:options.after??null,limit:options.limit??20}});
  }
  retire(key:string,agentId:string,mode:"finish_current"|"cancel"="finish_current"):Promise<any>{
    return this.command(key,"team_manage",{action:"retire",agent_id:agentId,mode});
  }
  post(key: string, message: string): Promise<any> {
    return this.command(key, "team_post_board", { message });
  }
  sendMessage(key: string, agentId: string, message: string): Promise<any> {
    return this.command(key, "team_send_message", {
      agent_id: agentId,
      message,
    });
  }
  history(options:{messageId?:string;direction?:"before"|"after";limit?:number}={}):Promise<any>{
    const query=new URLSearchParams({direction:options.direction??"before",limit:String(options.limit??20)});
    if(options.messageId!==undefined)query.set("message_id",options.messageId);
    return this.service.json("GET",`/v1/sessions/${segment(this.lead.id)}/team/messages?${query}`);
  }

  private command(key: string, operation: string, args: unknown): Promise<any> {
    return this.service.json(
      "POST",
      `/v1/sessions/${segment(this.lead.id)}/team/commands`,
      { operation, arguments: args },
      key,
    );
  }
}

export type { ServiceTeamDelegation } from "./team-placement.js";
