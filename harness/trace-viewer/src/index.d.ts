export interface TraceAdapter {
  timeline(query: {
    sessionId: string;
    turnId: string;
    after?: number;
    through?: number;
    limit?: number;
  }): Promise<unknown>;
  summary(query: {
    sessionId: string;
    turnId: string;
    through?: number;
  }): Promise<unknown>;
  children?(query: { sessionId: string; turnId: string }): Promise<unknown>;
  subtreeSummary?(query: {
    sessionId: string;
    turnId: string;
  }): Promise<unknown>;
  evaluations?(query: {
    sessionId: string;
    turnId: string;
    limit?: number;
  }): Promise<unknown>;
}
export interface ChildLink {
  child_session_id: string;
  child_turn_id: string | null;
  agent_id: string;
  phase: string;
}
export function mountTurnTrace(
  root: HTMLElement,
  options: {
    adapter: TraceAdapter;
    sessionId: string;
    turnId: string;
    onChild?: (child: ChildLink) => void;
  },
): { refresh(): void; dispose(): void };
export function mountObservability(
  root: HTMLElement,
  options: {
    request: (path: string, signal?: AbortSignal) => Promise<any>;
    isAdmin?: boolean;
    initialRun?: { session_id: string; turn_id: string } | null;
    directory?: boolean;
    openSession?: boolean;
    children?: boolean;
  },
): () => void;

export interface SessionTracingAdapter {
  search?(
    query: { q?: string; after?: string },
    signal?: AbortSignal,
  ): Promise<{ runs: TraceRun[]; next_cursor: string | null }>;
  turns(
    query: { sessionId: string; after?: string | number },
    signal?: AbortSignal,
  ): Promise<{ turns: TraceRun[]; next_cursor: string | number | null }>;
  timeline(
    query: {
      sessionId: string;
      turnId: string;
      after?: number;
      through?: number;
      limit?: number;
    },
    signal?: AbortSignal,
  ): Promise<any>;
  summary(
    query: { sessionId: string; turnId: string },
    signal?: AbortSignal,
  ): Promise<any>;
  children?(
    query: { sessionId: string; turnId: string },
    signal?: AbortSignal,
  ): Promise<{ children: any[] }>;
  /** Backend-authorized, explicitly captured payloads, correlated to this exact span. */
  stepDetails?(
    query: {
      sessionId: string;
      turnId: string;
      traceId: string;
      spanId: string;
    },
    signal?: AbortSignal,
  ): Promise<{ input?: unknown; output?: unknown }>;
}
export interface TraceRun {
  session_id: string;
  turn_id: string;
  name?: string;
  agent?: string;
  status?: string;
  created_ms?: number;
  label?: string;
}
export function mountSessionTracing(
  root: HTMLElement,
  options: {
    adapter: SessionTracingAdapter;
    initialRun?: TraceRun;
    onOverview?: () => void;
  },
): { refresh(): Promise<void>; dispose(): void };
export function serviceTracingAdapter(
  request: (path: string, signal?: AbortSignal) => Promise<any>,
): SessionTracingAdapter;
