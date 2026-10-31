export type Json =
  null | boolean | number | string | Json[] | { [key: string]: Json };
export type Provider = "openai" | "anthropic";
export interface AccessToken {
  accessToken: string;
  /** Unix timestamp in seconds, supplied by the issuer (not milliseconds). */
  expiresAt: number;
}
export interface ProviderAuth {
  /** Optional when the app only pushes tokens proactively. Runs in the app. */
  getToken?: (context: {
    provider: string;
    reason: "missing" | "expiring" | "rejected";
    signal: AbortSignal;
  }) => Promise<AccessToken>;
}
export interface ModelProvider {
  name: string;
  api: "responses" | "chat-completions" | "anthropic";
  baseUrl?: string;
  /** Memory-only bearer access tokens; mutually exclusive with credentials. */
  auth?: ProviderAuth;
  credentials?: (
    | { apiKey: string; apiKeyEnv?: never }
    | { apiKeyEnv: string; apiKey?: never }
  ) & {
    /** Secure platform storage is the default; memory must be resupplied after restart. */
    storage?: "secure" | "memory";
  };
}
export interface ToolContext {
  sessionId: string;
  turnId: string;
  callId: string;
  workspace: string;
  signal: AbortSignal;
  progress(message: string, data?: Json): Promise<void>;
}
export interface ApplicationTool {
  description: string;
  inputSchema: Json;
  version?: string;
  execute(
    arguments_: Record<string, Json>,
    context: ToolContext,
  ): Promise<Json | ToolImageResult> | Json | ToolImageResult;
}
/** Explicit image output. Pixels are ephemeral model context, not journal data. */
export class ToolImageResult {
  constructor(
    public readonly data: Json,
    public readonly image: {
      mimeType: "image/jpeg" | "image/png";
      base64: string;
    },
  ) {}
}
export interface ApplicationTools {
  namespace: string;
  tools: Record<string, ApplicationTool>;
}
export type ConnectionState = "ready" | "recovering" | "closed" | "unavailable";
export type Approvals =
  | { mode: "auto"; reviewerModel: Model }
  | { mode: "always" | "reviewer" | "never" }
  | { mode: "only"; tools: string[] };

/** Definitions are fixed at host startup. Tools are qualified registered names. */
export interface TurnLimits {
  maxModelCalls: number;
  maxOutputTokens: number;
  /** Reserved inside maxOutputTokens for one additional tool-free summary. Default 512. */
  summaryTokens?: number;
}
export interface Agent {
  limits?: TurnLimits;
  id: string;
  name?: string;
  description?: string;
  instructions: string;
  tools: string[];
  model?: { connection: string; model: string };
  provider?: Provider;
  fallbackModel?: string;
  planning?: boolean;
  memory?: boolean;
  /** Application-owned scope, never a model-selected learner/workspace ID. */
  memoryScope?: string;
  scheduling?: boolean;
  approvals?: Approvals;
  /** Skip structured ask_user calls without changing tool approvals. */
  autoSkipQuestions?: boolean;
  subagents?: Agent[];
  servers?: string;
  toolPolicy?: string;
  runtimePolicy?: string;
}
/** Reusable desktop agent definition. Credentials are never part of a definition. */
export interface AgentDefinition extends Omit<Agent, "id" | "name" | "model"> {
  name: string;
  defaultModel?: Model;
}
export interface Model {
  provider: string;
  name: string;
}
export interface SessionOptions {
  /** Overrides the agent default for this new session. */
  autoSkipQuestions?: boolean;
  workspace?: string;
  scratch?: boolean;
  name?: string;
  model?: Model;
}
export type Storage =
  | { type: "sqlite" }
  | {
      type: "postgres";
      connectionEnv: string;
      schema: string;
      caFile?: string;
      /** Test-only opt-in. Never use plaintext for remote databases. */
      loopbackPlaintext?: boolean;
    };
export interface Telemetry {
  endpoint: string;
  deployment?: string;
  headers?: (
    { name: string; value: string } | { name: string; value_env: string }
  )[];
  signals?: ("traces" | "metrics" | "logs")[];
  content?: {
    capture: "off" | "metadata" | "full";
    max_bytes?: number;
    redact_secrets?: boolean;
  };
}
export interface HarnessOptions {
  /** App-owned immutable coworker bundles; same host policy as dynamically defined agents. */
  coworkers?: {
    bundle: string;
    model: string;
    provider?: Provider;
    servers?: string;
    toolPolicy?: string;
    runtimePolicy?: string;
  }[];
  executable?: string;
  stateDir?: string;
  appId?: string;
  sessions?: { persist?: boolean; directory?: string };
  scratch?: { directory: string };
  compaction?: { triggerAtContextFraction?: number; contextWindow?: number };
  modelProviders?: ModelProvider[];
  runner?: string;
  agents?: Agent[];
  storage?: Storage;
  sandbox?: "openshell" | "off" | { provider: "openshell" };
  mcpServers?: McpServer[];
  telemetry?: Telemetry;
  restart?: { maxRestarts?: number; delayMs?: number };
  requestTimeoutMs?: number;
  shutdownTimeoutMs?: number;
  /** Names only; values are read at launch, never included in configuration. */
  inheritEnv?: string[];
  /** Only with a development harness compiled with test-support. */
  scripted?: boolean;
}
export type McpServer =
  | { name: string; transport: "streamable-http"; url: string }
  | { name: string; transport: "stdio"; program: string; args?: string[] };
export type ResolvedHarnessOptions = HarnessOptions & {
  executable: string;
  stateDir: string;
};
export interface SessionRef {
  session_id: string;
  agent: string;
  team?: { team_id: string; workers: { session_id: string; agent: string }[] };
}
export type Interruption = {
  id: string;
  session_id: string;
  agent: string;
} & (
  | { kind: "approval"; tool: string; arguments: Json; category?: string }
  | { kind: "question"; question: string; options: string[] }
);
export type Response =
  | { decision: "once" | "session" | "deny" }
  | { answer: string }
  | { defer: true };
export type RunStatus = "completed" | "interrupted" | "cancelled" | "failed";
export interface RunResult {
  status: RunStatus;
  finalOutput: string | null;
  interruptions: Interruption[];
  submissionId: string | null;
  failure?: string;
  cursor: number;
  board: Json | null;
}
export type Cursors = Record<string, number>;
export interface RunOptions {
  limits?: TurnLimits;
  /** Persist before sending if the application must survive a crash. */
  submissionId?: string;
  /** Stops waiting only. Call session.cancel() to cancel agent execution. */
  signal?: AbortSignal;
}
export type MessageInput = string | { type?: "message"; text: string };
export type ControlInput =
  | {
      type: "approval";
      interactionId: string;
      targetSessionId?: string;
      decision: "once" | "session" | "deny";
    }
  | {
      type: "answer";
      interactionId: string;
      targetSessionId?: string;
      text: string;
    };
export interface SessionState {
  status: "idle" | "running" | "waiting" | "closed";
  runId: string | null;
  active: Json[];
  pending: Interruption[];
  timers: { id: string; nextRunAt: number; remainingRuns: number }[] | null;
  capabilities: { steering: false; timers: boolean };
  canAcceptMessage: boolean;
  lastOutcome: string | null;
  cursor: number;
}
export interface ResumeOptions extends RunOptions {
  /** Last successfully consumed journal positions, for streaming replay. */
  cursors?: Cursors;
}
export interface JournalEvent {
  event_id: number;
  session_id: string;
  turn_id: string | null;
  type: string;
  data: Json;
}
export interface RunEvent {
  agentId?: string;
  teamId?: string;
  agent: string;
  sessionId: string;
  event: JournalEvent;
  summary:
    | (
        | { type: "message"; text: string }
        | {
            type: "tool_started" | "tool_finished" | "auto_approved";
            tool: string;
          }
        | { type: "plan_updated"; plan: Json }
      )
    | null;
}
export interface Connection {
  coworker: string;
  id: string;
  label: string;
  provider: Provider;
  apiKey: string;
  persistence?: "memory" | "secure";
}
export type ItemStatus =
  "open" | "in_progress" | "review" | "done" | "blocked" | "cancelled";
export interface ItemSpec {
  title: string;
  criteria: string;
  description?: string;
  dependencies?: string[];
}
export interface TeamItem {
  id: string;
  revision: number;
  title: string;
  status: ItemStatus;
  [key: string]: unknown;
}
export interface TeamBoard {
  id: string;
  name: string;
  lead: string;
  cancelled: boolean;
  deliveries: number;
  members: Record<
    string,
    { session_id: string; name: string; role: "lead" | "worker" }
  >;
  items: Record<string, TeamItem>;
}

export type ErrorCode =
  | "InvalidInput"
  | "Setup"
  | "Remote"
  | "Unavailable"
  | "OutcomeUnknown"
  | "Closed"
  | "Aborted"
  | "Protocol";
export class HarnessError extends Error {
  constructor(
    public readonly code: ErrorCode,
    message: string,
    public readonly context?: {
      method?: string;
      sessionId?: string;
      submissionId?: string;
      requestId?: string;
    },
  ) {
    super(message);
    this.name = "HarnessError";
  }
}

export function record(v: unknown): Record<string, unknown> {
  if (!v || typeof v !== "object" || Array.isArray(v))
    throw new HarnessError("Protocol", "Expected a protocol object");
  return v as Record<string, unknown>;
}
export function checkMemberErrors(v: unknown): void {
  const errors = record(v).errors;
  if (errors && Object.keys(record(errors)).length) {
    throw new HarnessError(
      "Remote",
      "Some member operations failed; inspect session snapshots before retrying",
    );
  }
}
