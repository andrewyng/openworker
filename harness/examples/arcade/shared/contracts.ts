export type Api = "responses" | "chat-completions" | "anthropic";
export interface ProviderSettings {
  name: string;
  preset: string;
  api: Api;
  baseUrl: string;
  model: string;
}
export interface Settings {
  version: 1;
  gamesRoot: string;
  provider: ProviderSettings | null;
}
export interface Brief {
  engine?: "canvas" | "three";
  idea: string;
  kind: string;
  vibe: string;
  difficulty: string;
}
export type GameStatus =
  | "draft"
  | "building"
  | "waiting"
  | "ready"
  | "failed"
  | "cancelled"
  | "interrupted";
export interface Game {
  coverUrl?: string;
  id: string;
  title: string;
  directory: string;
  createdAt: string;
  updatedAt: string;
  brief: Brief;
  session: { session_id: string; agent: string } | null;
  model: { provider: string; name: string } | null;
  revisionId: string | null;
  status: GameStatus;
  error: string | null;
  thumbnail: boolean;
  submissionId: string | null;
}
export interface Revision {
  id: string;
  gameId: string;
  createdAt: string;
  hash: string;
  source: "agent" | "duplicate" | "template";
}
export interface Message {
  id: string;
  role: "user" | "assistant";
  text: string;
}
export interface Activity {
  id: string;
  text: string;
  kind: "status" | "tool" | "error";
  time: string;
}
export interface Pending {
  id: string;
  session_id: string;
  kind: "approval" | "question" | "safety";
  code?: string;
  tool?: string;
  question?: string;
  options?: string[];
}
export interface ProjectView {
  testing?: TestingSummary;
  reviewAvailable?: boolean;
  files?: { path: string; bytes: number }[];
  game: Game;
  messages: Message[];
  partial: string;
  activity: Activity[];
  pending: Pending[];
  active: boolean;
  stage: string;
  revisions: Revision[];
  previewUrl: string | null;
}
export interface ReviewView {
  digest: string;
  baseRevision: string | null;
  draftUrl: string;
  currentUrl: string | null;
  changes: { path: string; kind: string }[];
}
export type TestCheck = "start" | "movement" | "pause" | "compact" | "level";
export interface TestingSummary {
  digest: string;
  current: boolean;
  updatedAt: string;
  screenshots: number;
  keys: number;
  pointer: number;
  viewports: string[];
  hostErrors: number;
  error?: string;
  checks: Record<
    TestCheck,
    { status: "passed" | "failed" | "not_tested"; note: string }
  >;
}
export interface AppSnapshot {
  starters: StarterGame[];
  settings: Settings;
  games: Game[];
  activeGameId: string | null;
  runtime: "idle" | "ready" | "recovering" | "unavailable";
}
export interface StarterGame {
  id: string;
  version: string;
  title: string;
  description: string;
  engine: "canvas" | "three";
  tags: string[];
  license: string;
  coverUrl: string;
  heroUrl: string;
  previewUrl: string;
}
export type AppEvent =
  | { type: "changed" }
  | { type: "project"; view: ProjectView }
  | { type: "runtime"; state: string };
export type Action =
  | { type: "snapshot" }
  | { type: "settings.save"; provider: ProviderSettings; apiKey?: string }
  | { type: "settings.directory" }
  | { type: "provider.test" }
  | { type: "game.create"; title?: string; brief: Brief }
  | { type: "game.open"; id: string }
  | { type: "game.review"; id: string; interactionId: string }
  | { type: "game.play"; id: string }
  | { type: "game.template" }
  | { type: "starter.remix"; id: string }
  | { type: "game.file"; id: string; path: string }
  | { type: "game.import-assets"; id: string }
  | { type: "window.fullscreen" }
  | { type: "game.duplicate"; id: string }
  | { type: "game.send"; id: string; text: string }
  | {
      type: "game.respond";
      id: string;
      interactionId: string;
      decision?: "once" | "session" | "deny";
      answer?: string;
    }
  | { type: "game.cancel"; id: string }
  | { type: "game.restore"; id: string; revisionId: string }
  | { type: "game.export"; id: string }
  | { type: "game.thumbnail"; id: string; revisionId: string };
export interface DesktopBridge {
  gameProgress: (
    url: string,
    request: import("./game-progress.js").ProgressRequest,
  ) => Promise<unknown>;
  action<T = unknown>(input: Action): Promise<T>;
  onEvent(listener: (event: AppEvent) => void): () => void;
}
