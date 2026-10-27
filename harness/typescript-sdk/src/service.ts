/** Authenticated remote service handles. The service owns all execution and policy. */
import { randomUUID } from "node:crypto";
import { type Json, type JournalEvent, type RunEvent } from "./types.js";

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
export interface ServiceSessionRef {
  serviceId: string;
  namespace: string;
  sessionId: string;
  agent: string;
  environmentId: string;
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
export class Service {
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
  async agent(id: string): Promise<ServiceAgent> {
    const a = await this.json("GET", `/v1/agents/${segment(id)}`);
    if (a.id !== id) throw new ServiceError("invalid_agent", 0);
    return new ServiceAgent(this, id);
  }
  async resume(ref: ServiceSessionRef): Promise<ServiceSession> {
    if (
      ref.serviceId !== this.capabilities.serviceId ||
      ref.namespace !== this.options.namespace ||
      ref.agent !== this.capabilities.agent
    )
      throw new ServiceError("reference_identity_mismatch", 0);
    const session = new ServiceSession(this, structuredClone(ref));
    const snapshot = await session.snapshot();
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
  async newSession(options: {
    environmentId: string;
    name?: string;
    idempotencyKey?: string;
  }): Promise<ServiceSession> {
    if (
      Object.keys(options).some(
        (k) => !["environmentId", "name", "idempotencyKey"].includes(k),
      )
    )
      throw new ServiceError("unsupported_session_option", 0);
    const key = options.idempotencyKey ?? randomUUID();
    const r = await this.service.json(
      "POST",
      "/v1/sessions",
      {
        agent: this.id,
        environmentId: options.environmentId,
        ...(options.name ? { name: options.name } : {}),
      },
      key,
    );
    if (
      r.serviceId !== this.service.capabilities.serviceId ||
      r.namespace !== this.service.capabilities.namespace ||
      r.agent !== this.id ||
      r.environmentId !== options.environmentId ||
      typeof r.sessionId !== "string"
    )
      throw new ServiceError("invalid_reference", 0, key);
    return new ServiceSession(this.service, r as ServiceSessionRef);
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
  reference(): ServiceSessionRef {
    return structuredClone(this.ref);
  }
  snapshot(): Promise<any> {
    return this.service.json("GET", `/v1/sessions/${segment(this.id)}`);
  }
  async send(
    text: string,
    options: { idempotencyKey?: string } = {},
  ): Promise<ServiceRun> {
    const key = options.idempotencyKey ?? randomUUID();
    const r = await this.service.json(
      "POST",
      `/v1/sessions/${segment(this.id)}/turns`,
      { text },
      key,
    );
    return new ServiceRun(this.service, this.ref, r.submissionId, r.runId);
  }
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
