import { useCallback, useEffect, useLayoutEffect, useRef, useState, type PointerEvent } from "react";
import { useTranslation } from "react-i18next";
import {
  announceInboxUnlock,
  createTempWorkspace,
  finalizeAutomationRun,
  boardComment,
  boardTransition,
  fetchBoardAttachment,
  getBoardItem,
  getArtifacts,
  getBoard,
  type Board,
  getHealth,
  getRecentWorkspaces,
  getSessionMessages,
  getSessions,
  announceAutomationsChanged,
  announceMemoryChanged,
  connectEvents,
  deleteMemory,
  updateMemory,
  getSettings,
  getPersonas,
  getInbox,
  getUnattended,
  PERSONAS_CHANGED,
  resolveInboxItem,
  deleteSession,
  renameSession,
  runAutomation,
  saveSessionAsProject,
  setSessionFlags,
  setUnattended,
  Session,
  type InboxItem,
  type MessageSource,
  type Persona,
  type RecentWorkspace,
  type SurfaceVisibility,
  type WorkspaceCommandTrust,
} from "./api";
import type {
  ApprovalDecision,
  Attachment,
  Item,
  SessionInfo,
  SessionUsage,
  TodoItem,
  WsEvent,
} from "./types";
import { fullPersonaName, isProjectScoped } from "./personaScope";
import { baseName } from "./paths";
import { itemsFromMessages } from "./itemsFromMessages";
import { addTurnUsage, emptyUsage, usageFromMessages } from "./usage";
import { streamMode } from "./streamGate";
import { InboxItemCard } from "./components/InboxItemCard";
import { chooseFolder, isTauri, platformOS, startWindowDrag } from "./tauri";
import { Icon } from "./components/Icon";
import { Sidebar } from "./components/Sidebar";
import { ThinkingBlock, Transcript } from "./components/Transcript";
import { Composer } from "./components/Composer";
import { Markdown } from "./components/Markdown";
import { SearchModal } from "./components/SearchModal";
import { SessionIntro } from "./components/SessionIntro";
import { FolderGate } from "./components/FolderGate";
import { SessionSetupRow } from "./components/SessionSetupRow";
import { SendFolderDialog } from "./components/SendFolderDialog";
import { Onboarding } from "./components/Onboarding";
import { UpdateBanner } from "./components/UpdateBanner";
import { ScheduledView } from "./components/ScheduledView";
import { RightRail } from "./components/RightRail";
import { IntegrationsView } from "./components/IntegrationsView";
import { SettingsView } from "./components/SettingsView";
import { PersonaView } from "./components/PersonaView";
import { AuditView } from "./components/AuditView";
import { InboxView } from "./components/InboxView";
import { ApprovalCard } from "./components/ApprovalCard";
import { ToolRequestCard } from "./components/ToolRequestCard";
import { DirectoryRequestCard } from "./components/DirectoryRequestCard";
import { PlanCard } from "./components/PlanCard";
import { BoardOverlay } from "./components/BoardPanel";
import { TeamRequestCard } from "./components/TeamRequestCard";
import { WorkItemsCard } from "./components/WorkItemsCard";
import { TeamChatView } from "./components/TeamChatView";
import { WorkspaceTrustPrompt } from "./components/WorkspaceTrustPrompt";

const newId = () =>
  (crypto as any).randomUUID ? crypto.randomUUID().slice(0, 12) : Math.random().toString(36).slice(2, 14);

// Hero task suggestions — translated at call time (module scope can't see React hooks).
// Keys live under `hero.suggest_*`; resolved in the component via useTranslation.
const SUGGESTION_KEYS = [
  { ico: "⚙", key: "hero.suggest_tests" },
  { ico: "✦", key: "hero.suggest_overview" },
  { ico: "↻", key: "hero.suggest_fix_build" },
];

// Tools whose success means a new/changed file should show up under Artifacts right away.
const FILE_WRITE_TOOLS = new Set(["write_file", "apply_patch", "apply_unified_diff", "replace_in_file"]);

// Models sometimes pass todo items as bare strings instead of {content, status} objects (the
// backend tool normalizes them the same way; the GUI reads the raw proposal args, so mirror it).
function normalizeTodos(raw: unknown): TodoItem[] {
  if (!Array.isArray(raw)) return [];
  const statuses = new Set(["pending", "in_progress", "done"]);
  return raw.map((entry: any) => {
    if (entry && typeof entry === "object") {
      const status = entry.status === "completed" ? "done" : entry.status; // common model alias
      return {
        content: String(entry.content ?? ""),
        status: statuses.has(status) ? status : "pending",
      };
    }
    return { content: String(entry ?? ""), status: "pending" as const };
  });
}

// Fallback used only before the persona list loads (the in-component gatesWorkspace
// consults the real persona's requires_folder once available).
const gatesWorkspaceFallback = (a: string) => a === "code";
const LAST_SESSION_KEY = "coworker:last-session-by-agent:v1";
const RAIL_HIDDEN_KEY = "coworker:rail-hidden:v1";
const NAV_COLLAPSED_KEY = "coworker:nav-collapsed:v1";

type LastSession = { sessionId: string; workspace: string; updatedAt: number };

function readLastSessions(): Record<string, LastSession> {
  try {
    const raw = localStorage.getItem(LAST_SESSION_KEY);
    return raw ? JSON.parse(raw) : {};
  } catch {
    return {};
  }
}

function rememberLastSession(agent: string, sessionId: string, workspace: string | null) {
  if (!agent || !sessionId) return;
  try {
    const all = readLastSessions();
    all[agent] = { sessionId, workspace: workspace || "", updatedAt: Date.now() };
    localStorage.setItem(LAST_SESSION_KEY, JSON.stringify(all));
  } catch {
    /* localStorage may be unavailable; session restore is best effort. */
  }
}

function sessionTs(s: SessionInfo): number {
  return Date.parse(s.updated_at || "") || Number(s.updated_at) || 0;
}

function resumeTargetForAgent(agent: string, sessions: SessionInfo[]): LastSession | null {
  const remembered = readLastSessions()[agent];
  if (remembered?.sessionId) {
    const live = sessions.find((s) => s.session_id === remembered.sessionId && s.agent === agent);
    if (live || remembered.workspace) {
      return {
        sessionId: remembered.sessionId,
        workspace: live?.workspace ?? remembered.workspace ?? "",
        updatedAt: live ? sessionTs(live) : remembered.updatedAt,
      };
    }
  }
  const recent = sessions
    .filter((s) => s.agent === agent && s.session_id && !s.session_id.startsWith("__"))
    .sort((a, b) => sessionTs(b) - sessionTs(a))[0];
  return recent ? { sessionId: recent.session_id, workspace: recent.workspace || "", updatedAt: sessionTs(recent) } : null;
}

function fallbackWorkspace(current: string | null, projects: RecentWorkspace[]): string {
  if (current) return current;
  const existing = projects.find((p) => p.exists);
  return existing?.path || projects[0]?.path || "";
}

export function App() {
  const { t } = useTranslation();
  const [workspace, setWorkspace] = useState<string | null>(null);
  const [branch, setBranch] = useState<string | null>(null);
  // UX-029: the active session runs in a temporary folder (never show its raw path —
  // the header says "Temporary folder" and offers Save as project…). Set locally when a
  // temp dir is created at send, corrected by every `ready` event (server truth).
  const [tempWorkspace, setTempWorkspace] = useState(false);
  // The draft folder came from the user's own chip pick (not boot-resume or scratch
  // adoption). Only such a pick survives a coworker change (owner catch 2026-08-24).
  const [draftFolderPicked, setDraftFolderPicked] = useState(false);
  // §8.4 breaker tripped this turn — the mode chip shows "· paused" until the turn ends
  // or an ask_user answer resets the reviewer's denial streak (engine semantics).
  const [reviewerPaused, setReviewerPaused] = useState(false);
  // UX-029 send-time folder enforcement: the stashed message while the folder dialog is
  // up. The message goes out the moment the dialog resolves; Escape restores the draft.
  const [sendGate, setSendGate] = useState<{
    text: string;
    attachments?: Attachment[];
    skill?: string;
  } | null>(null);
  // Bumped to force a socket rebuild on the SAME session id (Save as project… moves the
  // folder server-side; the engine rebinds on reconnect).
  const [connectNonce, setConnectNonce] = useState(0);
  const [showGate, setShowGate] = useState(false);
  const [workspaceTrustRequest, setWorkspaceTrustRequest] =
    useState<WorkspaceCommandTrust | null>(null);
  const [agent, setAgent] = useState("cowork");
  const [model, setModel] = useState("gpt-5.6-sol");
  const [models, setModels] = useState<string[]>([]);
  const [modelLabels, setModelLabels] = useState<Record<string, string>>({});
  // {full model id → context window in tokens} from the curated matrix (verified only);
  // drives the composer usage chip's context-fill meter.
  const [modelContextWindows, setModelContextWindows] = useState<Record<string, number>>({});
  // Settings: show the composer's context-window fill bar. OFF by default (owner ask),
  // so an older backend without the field also shows the session total.
  const [contextBar, setContextBar] = useState(false);
  // Per-session token usage (OPE-42): rebuilt from the transcript on session load,
  // accumulated live from assistant_message events, reset with the transcript.
  const [usage, setUsage] = useState<SessionUsage>(emptyUsage());
  const [surfaces, setSurfaces] = useState<SurfaceVisibility>({ cowork: true, chat: false, code: false });
  const [mode, setMode] = useState("interactive");
  const [connected, setConnected] = useState(false);
  const [running, setRunning] = useState(false);
  // Transient "Compacting context…" indicator (OPE-27): set by the `compacting` event,
  // cleared by whatever the engine emits next — the summarizer call is otherwise a
  // multi-second silent stall mid-turn.
  const [compacting, setCompacting] = useState(false);
  const [items, setItems] = useState<Item[]>([]);
  const [streaming, setStreamingState] = useState("");
  // Ref mirror of `streaming`: the WS handler closure is built once per socket and can't read
  // fresh state — the interrupted/error flush below needs the live buffer at event time.
  // Mode markers in the transcript: which session has already seen the full Auto-approve
  // explanation, and what mode the transcript last recorded (so a switch can be told apart
  // Which session the current `mode` value is CONFIRMED for. On a session switch, `mode`
  // still holds the previous session's value until the server's `ready` event delivers the
  // real one — announcing anything in that window posts the old session's banner into the
  // new transcript (seen 2026-08-22: a fresh Ask-for-approval session opened with the
  // Auto-approve banner, then a stray "Ask for approval is on." marker when `ready` landed).
  const streamingRef = useRef("");
  const setStreaming = (value: string | ((s: string) => string)) => {
    streamingRef.current = typeof value === "function" ? value(streamingRef.current) : value;
    setStreamingState(streamingRef.current);
  };
  // The turn's live thinking text (reasoning_delta events) — same ref-mirror pattern.
  // Folded onto the assistant item when the message finalizes; cleared on turn_start.
  const [reasoningStream, setReasoningStreamState] = useState("");
  const reasoningRef = useRef("");
  const setReasoningStream = (value: string) => {
    reasoningRef.current = value;
    setReasoningStreamState(value);
  };
  const [todo, setTodo] = useState<TodoItem[]>([]);
  const [sessions, setSessions] = useState<SessionInfo[]>([]);
  const [projects, setProjects] = useState<RecentWorkspace[]>([]);
  const [sessionId, setSessionId] = useState<string>(newId());
  // Automation-run context (§ owner ask 2026-07-04): which task an open __run__ session belongs
  // to, driving the banner + "Back to runs". Best-effort — a run session without context still
  // shows a generic banner (detected by its __run__ id).
  const [runContext, setRunContext] = useState<{ id: string; title: string } | null>(null);
  // Which automation the Automations surface opens on (set by the banner's Back link
  // or a sidebar Scheduled-band click). Cleared on leaving the surface: a remembered
  // id going stale (e.g. the automation was deleted) reopened a dead detail —
  // "Loading…" forever (owner-hit 2026-07-20). Nav re-entry should land on the list.
  const [scheduledOpenId, setScheduledOpenId] = useState<string | null>(null);
  const [gateCreate, setGateCreate] = useState(false);
  // Which Settings section the full-page Settings surface opens on (§ Settings-as-page).
  const [settingsTab, setSettingsTab] = useState<
    "appearance" | "models" | "skills" | "voice" | "memory" | "personas"
  >("appearance");
  const openSettings = (
    tab: "appearance" | "models" | "skills" | "voice" | "memory" | "personas" = "appearance",
  ) => {
    setSettingsTab(tab);
    setSurface("settings");
  };
  // Whether the default model's provider is actually configured (any provider). Drives the
  // composer's "No model connected" chip. Default true so we don't flash the chip before settings
  // load; corrected by loadSettings.
  const [modelReady, setModelReady] = useState(true);
  const [surface, setSurface] = useState<
    "session" | "scheduled" | "integrations" | "audit" | "inbox" | "persona" | "settings"
  >("session");
  // A remembered Scheduled-detail target must not outlive the surface (see the
  // scheduledOpenId comment above): nav re-entry lands on the list, never a
  // possibly-deleted automation's dead detail.
  useEffect(() => {
    if (surface !== "scheduled") setScheduledOpenId(null);
  }, [surface]);
  // The persona whose detail page is showing (surface === "persona"); empty falls back to the
  // active session's persona. Phase 5 wires the grouped-nav gear + "Manage personas…" entry points.
  const [personaViewId, setPersonaViewId] = useState<string>("");
  // Where the persona page returns on "back": the active session, or Settings ▸ Personas when it
  // was opened from there (persona config now lives in Settings).
  const [personaViewReturn, setPersonaViewReturn] = useState<"session" | "settings">("session");
  const openPersona = (id: string, from: "session" | "settings" = "session") => {
    setPersonaViewReturn(from);
    setPersonaViewId(id);
    setSurface("persona");
  };
  const [browserRefreshKey, setBrowserRefreshKey] = useState(0);
  // Agent teams (OPE-96): board for the current session's workspace space.
  const [board, setBoard] = useState<Board | null>(null);
  const [boardOpen, setBoardOpen] = useState(false);
  // A rail row click deep-opens the overlay on that item's detail pane.
  const [boardDetailId, setBoardDetailId] = useState<number | null>(null);
  // # team chat overlay — opened from the team entry's chat row.
  const [chatTeam, setChatTeam] = useState<string | null>(null);
  // UX-038 follow-up (owner ruling 2026-08-21): the rail starts HIDDEN and the
  // topbar toggle persists per-device. Deep links (artifact/board chips, Access)
  // still force-show transiently — they never overwrite the stored preference.
  const [railHidden, setRailHidden] = useState<boolean>(() => {
    try { return localStorage.getItem(RAIL_HIDDEN_KEY) !== "0"; } catch { return true; }
  });
  const setRailHiddenPersist = useCallback((v: boolean) => {
    setRailHidden(v);
    try { localStorage.setItem(RAIL_HIDDEN_KEY, v ? "1" : "0"); } catch { /* best effort */ }
  }, []);
  // Left-nav collapse (⌘B): when collapsed the sidebar leaves the grid so content reclaims the
  // width; hovering the left edge peeks it back as a floating overlay. Persisted per-device.
  const [navCollapsed, setNavCollapsed] = useState<boolean>(() => {
    try { return localStorage.getItem(NAV_COLLAPSED_KEY) === "1"; } catch { return false; }
  });
  const [navPeek, setNavPeek] = useState(false);
  // While an artifact preview is open we auto-collapse the nav (#3). Remember the pre-preview
  // collapse state so we can restore it on close — unless the user re-opened the nav meanwhile.
  const navBeforePreview = useRef<boolean | null>(null);
  const setNavCollapsedPersist = useCallback((v: boolean) => {
    setNavCollapsed(v);
    try { localStorage.setItem(NAV_COLLAPSED_KEY, v ? "1" : "0"); } catch { /* best effort */ }
  }, []);
  const toggleNav = useCallback(() => {
    setNavPeek(false);
    navBeforePreview.current = null; // a manual toggle takes control from the artifact auto-collapse
    setNavCollapsedPersist(!navCollapsed);
  }, [navCollapsed, setNavCollapsedPersist]);
  // #3: collapse the nav while a full artifact preview is open, restore it on close (unless the
  // user manually toggled meanwhile). The collapse is transient — it never overwrites the pref.
  // STABLE identity (no deps): depending on navCollapsed changed this callback's identity on
  // every nav toggle, which re-ran the rail's notify effect with the viewer still open and
  // re-collapsed the nav the instant the user expanded it (owner-hit 2026-08-21). The current
  // collapse state is read through the functional updater instead.
  const onArtifactPreview = useCallback((open: boolean) => {
    if (open) {
      setNavPeek(false);
      setNavCollapsed((cur) => {
        if (navBeforePreview.current === null) navBeforePreview.current = cur;
        return true;
      });
    } else if (navBeforePreview.current !== null) {
      setNavCollapsed(navBeforePreview.current);
      navBeforePreview.current = null;
    }
  }, []);
  // Layout effect on purpose: a passive effect registers after paint, leaving a boot-splash
  // window where the app is visible but ⌘B/⌘, are dead (input arriving right after load was
  // dropped). Registering at commit closes that gap.
  useLayoutEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "b") {
        e.preventDefault();
        toggleNav();
      }
      // ⌘, — the platform Settings shortcut (advertised in the account menu, §26).
      if ((e.metaKey || e.ctrlKey) && e.key === ",") {
        e.preventDefault();
        setSurface("settings");
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [toggleNav]);
  // Count of files this Cowork conversation has produced — surfaces an "Artifacts (N)" button in
  // the topbar when the side panel is hidden, so produced files are never buried.
  const [artifactCount, setArtifactCount] = useState(0);
  // §32 deep link into the rail's Access section (the former Session-settings drawer): bumping
  // the key expands the section and scrolls it into view. Callers also un-hide the rail.
  const [accessKey, setAccessKey] = useState(0);
  const openAccess = () => {
    setRailHidden(false);
    setAccessKey((k) => k + 1);
  };
  // §34 (UX-016): clicking an artifact chip in the transcript must land somewhere visible —
  // RightRail opens the viewer; this just makes sure the rail isn't hidden.
  useEffect(() => {
    const show = () => setRailHidden(false);
    window.addEventListener("ocw-open-artifact", show);
    return () => window.removeEventListener("ocw-open-artifact", show);
  }, []);
  // Seventeenth pass: the lead's one-time [Board · N items](board:) chip — un-hide the
  // rail and bump the key that expands its Board section.
  const [boardRailKey, setBoardRailKey] = useState(0);
  useEffect(() => {
    const show = () => {
      setRailHidden(false);
      setBoardRailKey((k) => k + 1);
    };
    window.addEventListener("ocw-open-board", show);
    return () => window.removeEventListener("ocw-open-board", show);
  }, []);
  // The command-palette search, openable from the collapsed-sidebar topbar cluster (§22). The
  // expanded sidebar owns its own instance; this one exists so search never disappears with it.
  const [searchOpen, setSearchOpen] = useState(false);
  // A pending composer prefill (text + attachments) pushed from the session start panel.
  // Auto-Approve metering (§1.7): live reviewer counts for the composer badge. Polled with
  // the session inbox; null until the first fetch (badge hidden).
  const [composerPrefill, setComposerPrefill] = useState<{ text: string; attachments?: Attachment[]; nonce: number }>();

  // Persona metadata drives workspace behavior by FAMILY, not by hardcoded id (so a DevOps/SecOps
  // code-family persona gates a folder like Code, and a knowledge persona starts orphan like Cowork).
  const [personas, setPersonas] = useState<Persona[] | null>(null);
  const loadPersonas = useCallback(() => {
    getPersonas().then(setPersonas).catch(() => {});
  }, []);
  useEffect(() => {
    loadPersonas();
    // The composer's coworker picker is always mounted on a fresh session — refetch on
    // mutations (enable/install from Settings) instead of going stale.
    window.addEventListener(PERSONAS_CHANGED, loadPersonas);
    return () => window.removeEventListener(PERSONAS_CHANGED, loadPersonas);
  }, [loadPersonas]);
  const personaOf = (a: string) => personas?.find((p) => p.id === a);

  // Pending Inbox items for the ACTIVE session — surfaced inline above the composer so an
  // unattended session's blocking question/approval can be answered in context (resolving the
  // same item the Inbox shows; first responder wins).
  const [sessionInbox, setSessionInbox] = useState<InboxItem[]>([]);
  // Whether the active session is Unattended — when true, the agent's prompts route to the Inbox,
  // so we suppress the inline live cards (the Inbox / answer-in-context path shows them instead).
  // A ref too, because the WS event handler closes over stale state.
  const [unattended, setUnattendedState] = useState(false);
  const unattendedRef = useRef(false);
  const markUnattended = useCallback((on: boolean) => {
    unattendedRef.current = on;
    setUnattendedState(on);
  }, []);
  // The Mode menu's "Send approvals to Inbox" toggle (§22 — the old InboxControl, folded in).
  const toggleUnattended = async (on: boolean) => {
    await setUnattended(sessionId, on);
    markUnattended(on);
    // First Unattended enable = Inbox machinery engaged → the account row's chip unlocks (§26).
    if (on) announceInboxUnlock();
  };
  const resolveSessionInbox = async (id: string, resolution: string) => {
    await resolveInboxItem(id, resolution);
    getInbox(sessionId, "pending").then(setSessionInbox).catch(() => setSessionInbox([]));
    refreshSessions(); // attention badge should drop right away
  };
  // MUST pick a folder before starting — requires_folder personas (git-bound Code, the
  // security coworkers). Everything else starts orphan: the server auto-provisions a
  // per-conversation scratch dir and reports it in the `ready` event.
  const gatesWorkspace = (a: string) => {
    const p = personaOf(a);
    return p ? isProjectScoped(p) : gatesWorkspaceFallback(a);
  };

  // The desktop tray's "Settings" item dispatches this on the window.
  useEffect(() => {
    const open = () => openSettings("appearance");
    window.addEventListener("coworker:open-settings", open);
    return () => window.removeEventListener("coworker:open-settings", open);
  }, []);

  // "Run setup again" (from Settings) re-opens the wizard.
  useEffect(() => {
    const open = () => {
      setOnboarding(true);
    };
    window.addEventListener("coworker:open-onboarding", open);
    return () => window.removeEventListener("coworker:open-onboarding", open);
  }, []);

  const sessionRef = useRef<Session | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  // A message to auto-send once the next session connects — "Run now" task prompts, and
  // UX-029's deferred first send (folder resolved at send time → reconnect → message goes).
  const pendingPromptRef = useRef<{
    text: string;
    attachments?: Attachment[];
    skill?: string;
    model?: string;
    notice?: string; // e.g. "Temporary folder created · git initialized", shown after the message
  } | null>(null);
  // The in-flight manual run to finalize after its first turn ({taskId, runId, sessionId}).
  const activeRunRef = useRef<{ taskId: string; runId: string; sessionId: string } | null>(null);

  // Fetch ALL sessions + known projects so the sidebar can group them.
  const refreshSessions = useCallback(() => {
    getSessions().then(setSessions).catch(() => setSessions([]));
    getRecentWorkspaces().then(setProjects).catch(() => setProjects([]));
  }, []);

  // initial: adopt the server's seed workspace if any, else force the gate.
  // Retry health for a while: the desktop shell starts its sidecar in parallel, so the
  // server may not answer for a second or two. Only fall back to the gate once it's truly up.
  const [booting, setBooting] = useState(true);
  const [onboarding, setOnboarding] = useState(false);
  // True once we've resumed a prior conversation on boot (drives the splash wording).
  const [resumedExisting, setResumedExisting] = useState(false);
  // Latched: keep the boot splash up until the restored session is actually CONNECTED (not just
  // until `booting` clears), so an early click can't land on a session that's still settling.
  const [uiReady, setUiReady] = useState(false);

  // On boot with no seeded workspace, reopen the last thing the user had — most recent
  // conversation (restores its folder + agent + transcript), else the most recent project
  // folder. Only a true first run (nothing to resume) falls through to the folder gate.
  const resumeLastOrGate = async () => {
    let loadedSessions: SessionInfo[] = [];
    try {
      loadedSessions = (await getSessions()).filter((s) => s.session_id && !s.session_id.startsWith("__"));
      setSessions(loadedSessions);
      const sess = loadedSessions;
      const ts = (s: SessionInfo) => Date.parse(s.updated_at || "") || Number(s.updated_at) || 0;
      const last = [...sess].sort((a, b) => ts(b) - ts(a))[0];
      if (last) {
        setResumedExisting(true);
        if (last.agent) setAgent(last.agent);
        if (last.workspace) {
          setWorkspace(last.workspace);
          setBranch(null);
        }
        try {
          const messages = await getSessionMessages(last.session_id);
          setItems(itemsFromMessages(messages));
          setUsage(usageFromMessages(messages));
        } catch {
          setItems([]);
          setUsage(emptyUsage());
        }
        setSessionId(last.session_id);
        setShowGate(false);
        return;
      }
    } catch {
      /* fall through */
    }
    try {
      const recents = await getRecentWorkspaces();
      setProjects(recents);
      // Only auto-adopt a recent folder for gated surfaces (Code). Cowork starts orphan.
      if (gatesWorkspace(agent)) {
        const ws = recents.find((w) => w.exists) || recents[0];
        if (ws) {
          setWorkspace(ws.path);
          setShowGate(false);
          return;
        }
      }
    } catch {
      /* fall through */
    }
    setShowGate(gatesWorkspace(agent)); // only Code forces a first-run folder gate
  };

  useEffect(() => {
    let cancelled = false;
    const attempt = (tries: number) => {
      getHealth()
        .then(async (h) => {
          if (cancelled) return;
          setModel(h.model);
          // First-run setup wizard (desktop): show until the user completes/dismisses it.
          if (isTauri()) {
            getSettings()
              .then((s) => !cancelled && !s.onboarded && setOnboarding(true))
              .catch(() => {});
          }
          // Settle the active session BEFORE clearing `booting` (which unblocks the connection
          // effect). resumeLastOrGate is async — if we cleared `booting` first, the throwaway
          // initial sessionId would connect against an empty/stale workspace and the server
          // would provision a junk per-conversation scratch dir for it before resume could
          // flip to the real session. Cowork ignores default_workspace (a Code concept).
          if (h.default_workspace && gatesWorkspace(agent)) setWorkspace(h.default_workspace);
          else await resumeLastOrGate();
          // The mount-time loadSettings races the sidecar boot and swallows its failure —
          // on a cold start that left "Loading models…" stuck until the user visited
          // Settings (owner-hit 2026-07-23). Health just answered, so this one lands.
          loadSettings();
          // Same race, same fix: the mount-time persona fetch loses to the sidecar boot in
          // the packaged app, and its only other trigger is PERSONAS_CHANGED — so the
          // composer's coworker picker stayed empty for the whole session while Settings
          // (mounted later) looked fine (owner-hit 2026-08-13).
          loadPersonas();
          if (!cancelled) setBooting(false);
        })
        .catch(() => {
          if (cancelled) return;
          if (tries <= 0) {
            setBooting(false);
            setShowGate(true);
          } else {
            setTimeout(() => attempt(tries - 1), 500);
          }
        });
    };
    attempt(40); // ~20s of 500ms retries
    return () => {
      cancelled = true;
    };
  }, []);

  // Reveal the UI once boot has settled AND the restored session is connected (or we're showing
  // the folder gate). Latched, so later reconnects never flash the splash again.
  useEffect(() => {
    if (uiReady || booting) return;
    if (connected || showGate) setUiReady(true);
  }, [uiReady, booting, connected, showGate]);
  // Safety net: if the restored session never reports connected (backend slow/unreachable), reveal
  // the UI anyway. Boot already passed the health check, so a live connect is sub-second; this only
  // bites in the failure case, so keep it short.
  useEffect(() => {
    if (uiReady || booting) return;
    const t = setTimeout(() => setUiReady(true), 1500);
    return () => clearTimeout(t);
  }, [uiReady, booting]);

  const loadSettings = () =>
    getSettings()
      .then((s) => {
        setModels(s.models || []);
        setModelLabels(s.model_labels || {});
        setModelContextWindows(s.model_context_windows || {});
        setContextBar(s.context_bar === true);
        setModelReady(s.model_ready);
        if (s.surfaces) setSurfaces(s.surfaces);
      })
      .catch(() => {});

  // Open Settings → Configure Models (from the composer's "No model connected" chip).
  const openModelSetup = () => openSettings("models");

  // Leaving the Settings page: pick up any model/surface changes for the composer (the modal used to
  // do this on close).
  useEffect(() => {
    if (surface !== "settings") loadSettings();
  }, [surface]);

  useEffect(() => {
    refreshSessions();
    loadSettings(); // selectable models + which session surfaces are visible
  }, [refreshSessions]);

  // Poll the session list so the attention/liveness badges stay live and sessions created
  // out-of-band (unattended work, messaging, automations) appear without a manual refresh.
  useEffect(() => {
    const t = setInterval(refreshSessions, 5000);
    return () => clearInterval(t);
  }, [refreshSessions]);

  // Persona toggles can archive sessions server-side (disable-archives, §18): refetch on the
  // personas-changed event so the sidebar section disappears immediately, not on the next poll.
  useEffect(() => {
    const onPersonas = () => refreshSessions();
    window.addEventListener(PERSONAS_CHANGED, onPersonas);
    return () => window.removeEventListener(PERSONAS_CHANGED, onPersonas);
  }, [refreshSessions]);

  // If the active persona is DISABLED (turned off in Settings, or a resumed session landed
  // on one), fall back to Cowork. This used to key on the legacy sidebar-visibility prefs
  // (show_chat/show_code) — with the composer picker shipped (UX-029), enablement is the
  // one visibility axis, and a deliberately picked coworker must never be reverted.
  useEffect(() => {
    const p = personaOf(agent);
    if (p && !p.enabled) switchAgent("cowork");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agent, personas]);

  useEffect(() => {
    if (surface === "session") rememberLastSession(agent, sessionId, workspace);
  }, [surface, agent, sessionId, workspace]);

  // (re)connect when workspace, session, or agent changes
  useEffect(() => {
    if (booting) return; // wait until boot/resume settles the session before connecting
    if (gatesWorkspace(agent) && !workspace) return; // Code needs a folder (gate handles it)
    const handleEvent = (ev: WsEvent) => {
      const d = ev.data || {};
      // An interrupted/errored turn never emits assistant_message, so its streamed partial
      // would otherwise live only in the ephemeral buffer until the next turn_start wipes it
      // (owner-hit 2026-07-22). Promote it to a durable transcript item — the engine persists
      // the same text server-side, so the live view and a session reload now agree.
      const flushPartialStream = () => {
        const partial = streamingRef.current;
        const thinking = reasoningRef.current;
        if (!partial && !thinking) return;
        setStreaming("");
        setReasoningStream("");
        setItems((p) => [
          ...p,
          {
            kind: "assistant",
            text: partial,
            ts: Date.now() / 1000,
            ...(thinking ? { reasoning: thinking } : {}),
          },
        ]);
      };
      // Any engine event after `compacting` means the summarizer finished (compacted /
      // silent no-op / failure prompt) — the transient must never outlive it.
      if (ev.type !== "compacting") setCompacting(false);
      switch (ev.type) {
        case "ready":
          setConnected(true);
          if (d.model) setModel(d.model);
          if (d.mode) setMode(d.mode);
          if (d.command_trust?.required) setWorkspaceTrustRequest(d.command_trust);
          // Cowork: adopt the server-provisioned scratch dir (only when we don't already have one).
          if (d.workspace) setWorkspace((cur) => cur || d.workspace);
          // UX-029: server truth on whether this session runs in a temporary folder.
          if (typeof d.temp_workspace === "boolean") setTempWorkspace(d.temp_workspace);
          // Server truth on a live turn: a reconnect mid-turn never sees turn_start, so
          // without this the Stop button and waiting row vanish (owner catch 2026-08-24).
          if (typeof d.running === "boolean") setRunning(d.running);
          break;
        case "turn_start":
          setRunning(true);
          setReviewerPaused(false); // a fresh user message resets the denial streak
          setStreaming("");
          setReasoningStream("");
          // Background-delivered turns (channel message, self-wake, durable resume) have no local
          // send(), so the triggering message isn't in `items` yet — surface it. A connector message
          // carries a structured `source` (§3.1) → render the rich card; otherwise a plain user item.
          // Foreground turns already appended it in send(); skip the duplicate.
          if (d.source?.connector) {
            const src = d.source as MessageSource;
            setItems((p) => {
              const last = p[p.length - 1];
              return last && last.kind === "connector" && last.source.ts === src.ts && last.source.text === src.text
                ? p
                : [...p, { kind: "connector", source: src }];
            });
          } else if (typeof d.input === "string" && d.input) {
            // `display` (force-run) is the user's literal "/name …" line; the framed
            // `input` is model-facing. Surface/dedupe on what the user actually sees.
            const shown = (typeof d.display === "string" && d.display) || (d.input as string);
            setItems((p) => {
              // Look past trailing notices — the UX-029 "Temporary folder created" line
              // sits between the local echo and this event's arrival.
              let i = p.length - 1;
              while (i >= 0 && p[i].kind === "notice") i--;
              const last = p[i];
              return last && last.kind === "user" && last.text === shown
                ? p
                : [...p, { kind: "user", text: shown, ts: Date.now() / 1000 }];
            });
          }
          break;
        case "assistant_delta":
          setStreaming((s) => s + (d.text || ""));
          break;
        case "reasoning_delta":
          setReasoningStream(reasoningRef.current + (d.text || ""));
          break;
        case "assistant_message": {
          if (d.usage) setUsage((u) => addTurnUsage(u, d.usage));
          // The event's reasoning is authoritative (covers background-delivered turns);
          // the local buffer is the fallback for older servers.
          const reasoning = d.reasoning || reasoningRef.current;
          if (d.text || reasoning)
            setItems((p) => [
              ...p,
              {
                kind: "assistant",
                text: d.text || "",
                ts: Date.now() / 1000,
                ...(reasoning ? { reasoning } : {}),
              },
            ]);
          setStreaming(""); // finalized into items (or empty tool-only turn)
          setReasoningStream("");
          break;
        }
        case "tool_proposed":
          if (d.name === "todo_write" && (d.arguments?.todos || d.arguments?.items))
            setTodo(normalizeTodos(d.arguments.todos ?? d.arguments.items));
          setItems((p) => [
            ...p,
            { kind: "tool", id: newId(), name: d.name, args: d.arguments, status: "…" },
          ]);
          break;
        case "permission_required":
          // Unattended → the backend parked it in the Inbox; don't also surface a live card.
          if (unattendedRef.current) break;
          setItems((p) => [
            ...p,
            {
              kind: "approval",
              name: d.name,
              args: d.arguments,
              reason: d.reason,
              category: d.category,
              standingTarget: d.standing_target || undefined,
              searchProvider: d.search_provider || undefined,
              provenance: d.provenance || undefined,
              reviewerUnsure: d.reviewer_unsure || undefined,
              readonlyOk: !!d.readonly_ok,
            },
          ]);
          break;
        case "directory_requested":
          if (unattendedRef.current) break;
          setItems((p) => [
            ...p,
            { kind: "dirreq", reason: d.reason || "", path: d.path || "", writable: !!d.writable, primary: !!d.primary },
          ]);
          break;
        case "tool_requested":
          if (unattendedRef.current) break;
          setItems((p) => [
            ...p,
            {
              kind: "toolreq",
              tool: d.name || "",
              reason: d.reason || "",
              // Fail CLOSED: only offer Install when the event says a pinned build exists.
              installable: d.installable === true,
              version: d.version || "",
              summary: d.summary || "",
              source: d.source || "",
            },
          ]);
          break;
        case "plan_proposed":
          if (unattendedRef.current) break;
          setItems((p) => [...p, { kind: "planreq", plan: d.plan || "" }]);
          break;
        case "team_proposed":
          // The staffing gate (agent teams) — approval pre-spawns the worker sessions.
          if (unattendedRef.current) break;
          setItems((p) => [
            ...p,
            {
              kind: "teamreq",
              members: Array.isArray(d.members) ? d.members : [],
              enable_chat: !!d.enable_chat,
              note: d.note || "",
            },
          ]);
          break;
        case "items_proposed":
          // The decomposition gate — approval creates the items on the board.
          if (unattendedRef.current) break;
          setItems((p) => [
            ...p,
            {
              kind: "itemsreq",
              items: Array.isArray(d.items) ? d.items : [],
              note: d.note || "",
            },
          ]);
          break;
        case "question_requested":
          // ask_user in an attended session — answered inline (not routed to the Inbox).
          setItems((p) => [
            ...p,
            {
              kind: "question",
              question: d.question || "",
              options: d.options || [],
              allow_text: d.allow_text !== false,
              multi: !!d.multi,
              header: d.header || "",
              questions: d.questions || [],
            },
          ]);
          break;
        case "tool_finished":
          setItems((p) =>
            updateLastTool(
              p,
              d.name,
              d.status,
              d.result_preview || d.reason,
              d.display?.hidden_by_filters,
              d.standing_rule,
              d.reviewer_reason,
              d.allow_anyway,
              d.approval_origin,
              d.approval_note,
            ),
          );
          // §8.4 breaker: the reviewer paused itself for the rest of the turn — say so
          // where the user is looking (persisted server-side for reloads) and on the
          // composer's mode chip.
          if (d.reviewer_paused) {
            setReviewerPaused(true);
            setItems((p) => [...p, { kind: "notice", tone: "info", text: String(d.reviewer_paused) }]);
          }
          // Refresh the right rail when something it shows may have changed: browser state, or a
          // file write that should appear under Artifacts immediately (not only after the turn).
          if (String(d.name || "").startsWith("browser_") || FILE_WRITE_TOOLS.has(d.name)) {
            setBrowserRefreshKey((k) => k + 1);
          }
          break;
        case "turn_end":
          if (d.status === "max_iterations_exceeded")
            setItems((p) => [...p, { kind: "notice", tone: "warn", text: t("app.notice.max_iterations") }]);
          break;
        case "mode_notice":
          // Server-authored + persisted (owner ruling 2026-08-24): the Auto-Approve
          // explainer once per session ever, one-line markers for later switches.
          setItems((p) => [
            ...p,
            { kind: "notice", tone: "info", ...(d.title ? { title: d.title } : {}), text: d.text || "" },
          ]);
          break;
        case "model_changed":
          // Mid-session switch (server-applied): update the header fact and drop the
          // persisted marker into the live transcript (replay renders it from history).
          if (d.model) setModel(d.model);
          setItems((p) => [...p, { kind: "notice", tone: "info", text: d.text || t("app.notice.model_switched") }]);
          break;
        case "memory_saved":
          // §5.1 save notice — inline in the transcript, where the user is already
          // looking and where it keeps until they act (a corner toast disappeared
          // before it could be read or undone — owner-hit 2026-07-28). Summary is the
          // friendly one-liner; content is the fallback when the model skipped it.
          setItems((p) => [
            ...p,
            {
              kind: "memory",
              id: Number(d.id),
              text: String(d.summary || d.content || ""),
              // Present when an existing memory was edited rather than added — the
              // notice says so, and Undo restores this text instead of deleting.
              ...(d.previous ? { previous: String(d.previous) } : {}),
            },
          ]);
          announceMemoryChanged(); // Settings ▸ Memory, if open, is now stale
          break;
        case "compacting":
          setCompacting(true);
          break;
        case "compacted":
          // Auto-compaction marker (OPE-27): outbound-only — the transcript stays intact,
          // this divider just shows where the model's memory was summarized.
          setItems((p) => [...p, { kind: "notice", tone: "info", text: d.text || t("app.notice.context_compacted") }]);
          break;
        case "interrupted":
          flushPartialStream();
          setItems((p) => [...p, { kind: "notice", tone: "warn", text: t("app.notice.interrupted") }]);
          break;
        case "error":
          flushPartialStream();
          setItems((p) => [
            ...p,
            { kind: "notice", tone: "warn", text: t("app.notice.error") + (d.error || t("app.notice.unknown")), retriable: true },
          ]);
          break;
        case "input_rejected":
          setItems((p) => [
            ...p,
            { kind: "notice", tone: "warn", text: d.error || t("app.notice.input_rejected") },
          ]);
          break;
        case "turn_done":
          setRunning(false);
          setReviewerPaused(false); // the pause is scoped to the turn
          refreshSessions();
          // Catch-all artifact refresh: files created via shell or on a brand-new session (whose
          // record only exists after the first save) appear once the turn completes.
          setBrowserRefreshKey((k) => k + 1);
          // Finalize a manual run after its first turn completes (mark it ok in history).
          {
            const ar = activeRunRef.current;
            if (ar && ar.sessionId === sessionId) {
              activeRunRef.current = null;
              finalizeAutomationRun(ar.taskId, ar.runId).catch(() => {});
            }
          }
          break;
      }
    };

    const session = new Session(sessionId, workspace || "", agent, {
      onEvent: handleEvent,
      onOpen: () => {
        setConnected(true);
        // Auto-send the pending message once the session connects ("Run now" prompts and
        // UX-029's deferred first send).
        const p = pendingPromptRef.current;
        if (p) {
          pendingPromptRef.current = null;
          const shown = p.skill ? `/${p.skill}${p.text ? ` ${p.text}` : ""}` : p.text;
          setItems((prev) => [
            ...prev,
            { kind: "user", text: shown, attachments: p.attachments, ts: Date.now() / 1000 },
            ...(p.notice
              ? [{ kind: "notice", tone: "info", text: p.notice } as Item]
              : []),
          ]);
          sessionRef.current?.userMessage(p.text, p.attachments, p.model, p.skill);
        }
      },
      onClose: () => setConnected(false),
    });
    sessionRef.current = session;
    return () => session.close();
    // NOTE: `workspace` is intentionally NOT a dependency. Every real workspace change
    // (pick folder, select/switch session, new session) is paired with a `sessionId`
    // change, so the socket still reconnects when it should. The one workspace-only change
    // is the `ready` handler adopting the server's provisioned Cowork scratch dir — listing
    // `workspace` here made that adoption tear down and rebuild the socket immediately after
    // first connect, dropping the user's first message (the "send twice" bug). The scratch
    // dir is deterministic from `sessionId` server-side, so skipping that reconnect is safe.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [booting, sessionId, agent, refreshSessions, connectNonce]);

  // Stream-following (FB-004): auto-scroll only while the user is AT the bottom, so scrolling
  // up to read during a streaming turn sticks. `atBottomRef` is the live truth (per scroll
  // event, no re-render); `following` mirrors it into state for the jump-to-latest pill.
  // Programmatic smooth-scrolls fire scroll events of their own — while one is in flight
  // (`autoScrollingRef`) they must not read as "the user scrolled up", or every stream tick
  // would disengage its OWN follow. The animation only moves down, so a decreasing scrollTop
  // mid-flight can only be the user taking over.
  const atBottomRef = useRef(true);
  const autoScrollingRef = useRef(false);
  const lastScrollTopRef = useRef(0);
  const [following, setFollowing] = useState(true);
  const scrollToBottom = () => {
    const el = scrollRef.current;
    if (!el) return;
    autoScrollingRef.current = true;
    el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  };
  const followLatest = () => {
    atBottomRef.current = true;
    setFollowing(true);
    scrollToBottom();
  };
  const handleScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    const top = el.scrollTop;
    const atBottom = el.scrollHeight - top - el.clientHeight < 48;
    if (autoScrollingRef.current) {
      if (atBottom) autoScrollingRef.current = false; // landed
      else if (top >= lastScrollTopRef.current) {
        lastScrollTopRef.current = top; // still animating down — not the user
        return;
      } else autoScrollingRef.current = false; // moved UP mid-flight — user takeover
    }
    lastScrollTopRef.current = top;
    atBottomRef.current = atBottom;
    setFollowing(atBottom);
  };
  // A different session is a fresh viewport — never inherit a scrolled-up state. Declared
  // BEFORE the auto-scroll effect: when a session switch and its hydrated items land in one
  // commit, the reset must run first or the stale ref would skip the initial bottom-scroll.
  useEffect(() => {
    atBottomRef.current = true;
    setFollowing(true);
  }, [sessionId]);

  useEffect(() => {
    if (atBottomRef.current) scrollToBottom();
  }, [items, streaming]);

  // Track produced-file count for the topbar "Artifacts" affordance (works even when the rail is
  // hidden, where the rail itself doesn't fetch). Cowork only; refreshes on file writes/turn end.
  useEffect(() => {
    if (agent !== "cowork" || surface !== "session") {
      setArtifactCount(0);
      return;
    }
    getArtifacts(sessionId).then((a) => setArtifactCount(a.length)).catch(() => {});
  }, [agent, surface, sessionId, browserRefreshKey]);

  // Agent teams (OPE-96): the session's board — drives the rail section, the plan
  // gate, and the expanded overlay. Refreshes with the same cycle as artifacts
  // (session change + turn end) so items the agent just created appear.
  useEffect(() => {
    if (surface !== "session" || agent === "chat") {
      setBoard(null);
      return;
    }
    getBoard(sessionId).then(setBoard).catch(() => setBoard(null));
  }, [agent, surface, sessionId, browserRefreshKey, running]);

  const refreshBoard = () => getBoard(sessionId).then(setBoard).catch(() => {});
  const moveBoardItem = async (item: number, to: string, comment = "") => {
    await boardTransition(sessionId, item, to, comment);
    await refreshBoard();
  };

  // Seventeenth pass: the drawer's Team panel — this session's staff (workers whose
  // lead is the current session). The sidebar shows ONE entry per team; members live here.
  const curSession = sessions.find((s) => s.session_id === sessionId);
  const teamMembers = sessions.filter(
    (s) => s.team?.role === "worker" && s.team.lead_session === sessionId,
  );

  // Keep the active session's pending Inbox items fresh (answer-in-context card). Loads on session
  // change + after each turn, plus a slow poll so an unattended agent's new question surfaces.
  useEffect(() => {
    if (surface !== "session") return;
    const load = () => {
      getInbox(sessionId, "pending").then(setSessionInbox).catch(() => setSessionInbox([]));
      getUnattended(sessionId).then(markUnattended).catch(() => markUnattended(false));
    };
    load();
    const t = setInterval(load, 4000);
    return () => clearInterval(t);
  }, [surface, sessionId, browserRefreshKey, markUnattended]);

  const send = (text: string, attachments?: Attachment[], skill?: string) => {
    // UX-029: folder enforcement AT SEND. A code-family session with no folder has no
    // socket yet (the connect effect waits) — stash the message and ask where to work;
    // it goes out the moment the dialog resolves.
    if (gatesWorkspace(agent) && !workspace) {
      setSendGate({ text, attachments, skill });
      return;
    }
    // A typed message while a proposal gate is pending IS the answer: it resolves
    // the gate as decline-with-feedback, so "use gpt-5.6-sol for all workers"
    // reaches the lead instead of bouncing off a blocked composer (owner-hit
    // 2026-08-16). The card buttons stay the approve/plain-decline paths.
    if (!unattended && pendingTeam?.kind === "teamreq" && !pendingTeam.resolved) {
      setItems((p) => [...p, { kind: "user", text, ts: Date.now() / 1000 }]);
      respondTeam(false, text);
      return;
    }
    if (
      !unattended &&
      pendingItemsReq?.kind === "itemsreq" &&
      !pendingItemsReq.resolved
    ) {
      setItems((p) => [...p, { kind: "user", text, ts: Date.now() / 1000 }]);
      respondItemsReq(false, text);
      return;
    }
    // Force-run shows exactly what the user typed: "/name rest". Must match the server's
    // `display` sidecar formula so the turn_start dedupe recognizes the local echo.
    const shown = skill ? `/${skill}${text ? ` ${text}` : ""}` : text;
    setItems((p) => [...p, { kind: "user", text: shown, attachments, ts: Date.now() / 1000 }]);
    // The visible model rides along with the message (single source of truth per turn).
    sessionRef.current?.userMessage(text, attachments, model, skill);
    followLatest(); // sending always re-engages stream-following, wherever the user had scrolled
  };
  // Resolving a LIVE prompt also resolves its parked Inbox mirror server-side, but the polled
  // `sessionInbox` copy stays "pending" for up to a poll cycle — long enough for the docked
  // answer-in-context card to flash the SAME request again right after the user answered it
  // (tester catch 2026-07-12: a Slack send "asked twice"). Drop the mirror optimistically;
  // the 4s poll restores anything genuinely still pending.
  const dropSessionInbox = (kind: string) =>
    setSessionInbox((cur) => cur.filter((it) => it.kind !== kind));
  // §8.4 "Allow anyway" on a reviewer-denied tool: register the one-shot exact-action
  // approval, then send a visible user message so the agent retries. The engine runs the
  // identical re-proposal without the reviewer or a card; anything different still asks.
  const allowAnyway = (name: string, args: any) => {
    sessionRef.current?.allowAnyway(name, args);
    send(t("app.allow_anyway_message", { name }));
  };
  const approve = (decision: ApprovalDecision) => {
    setItems((p) => resolveLastApproval(p, decision));
    dropSessionInbox("approval");
    sessionRef.current?.approve(decision);
  };
  const respondPlan = (approved: boolean, mode?: string, feedback?: string) => {
    setItems((p) => resolveLastPlan(p, approved ? "approved" : "rejected"));
    dropSessionInbox("plan");
    sessionRef.current?.respondPlan(approved, mode, feedback);
    if (approved && mode) setMode(mode); // the server flips the live engine to this mode
  };
  const respondTeam = (approved: boolean, feedback?: string, enableChat?: boolean) => {
    setItems((p) => resolveLastTeam(p, approved ? "approved" : "rejected"));
    dropSessionInbox("plan"); // the gate parks as a plan-kind Inbox item
    sessionRef.current?.respondTeam(approved, feedback, enableChat);
  };
  const respondItemsReq = (approved: boolean, feedback?: string) => {
    setItems((p) => resolveLastItemsReq(p, approved ? "approved" : "rejected"));
    dropSessionInbox("plan");
    sessionRef.current?.respondItems(approved, feedback);
    if (approved) setTimeout(refreshBoard, 400); // the items just landed
  };
  const respondDirectory = (granted: boolean, path?: string, writable?: boolean) => {
    setItems((p) => resolveLastDirReq(p, granted ? "granted" : "denied"));
    dropSessionInbox("directory");
    sessionRef.current?.respondDirectory(granted, path, writable);
  };
  const respondTool = (approved: boolean) => {
    setItems((p) => resolveLastToolReq(p, approved ? "installed" : "skipped"));
    dropSessionInbox("tool");
    sessionRef.current?.respondTool(approved);
  };
  const answerQuestion = (answer: string) => {
    setReviewerPaused(false); // an answered question resets the reviewer's streak
    setItems((p) => resolveLastQuestion(p, answer));
    dropSessionInbox("question");
    sessionRef.current?.respondQuestion(answer);
  };
  const prefillComposer = (text: string, attachments?: Attachment[]) =>
    setComposerPrefill((p) => ({ text, attachments, nonce: (p?.nonce ?? 0) + 1 }));
  const interrupt = () => sessionRef.current?.interrupt();
  const retry = () => {
    // Optimistic running: turn_start confirms; a rejected retry still ends in turn_done.
    setRunning(true);
    sessionRef.current?.retry();
  };
  const changeMode = (m: string) => {
    setMode(m);
    sessionRef.current?.setMode(m);
  };
  const changeModel = (m: string) => {
    if (running) return; // the server refuses mid-turn rebinds — don't let the header lie
    setModel(m);
    sessionRef.current?.setModel(m);
  };

  const startNewSession = (forAgent?: string) => {
    const target = forAgent || agent;
    setSurface("session"); // return to the conversation view if we were on a sub-view
    setItems([]);
    setUsage(emptyUsage());
    setStreaming("");
    setTodo([]);
    setRunning(false);
    // "New session" under a browsed persona switches to it (expand≠switch: the header alone
    // doesn't switch; this explicit action does).
    if (target !== agent) {
      setAgent(target);
      if (gatesWorkspace(target)) {
        // Never inherit the previous persona's folder — it may be a scratch dir. Clearing
        // it also blocks the connection effect; the setup row's folder chip (or the
        // send-time dialog) provides the folder — no modal gate up front (UX-029).
        setWorkspace(null);
        setBranch(null);
      }
      setShowGate(false);
    }
    // Knowledge family: a new conversation starts fresh (orphan) — clear the workspace so the
    // server provisions a NEW scratch dir for the new session id. Code keeps its repo — but
    // never a TEMPORARY dir (per-conversation by definition; the next session picks anew).
    if (!gatesWorkspace(target) || tempWorkspace) {
      setWorkspace(null);
      setBranch(null);
    }
    setDraftFolderPicked(false);
    setTempWorkspace(false);
    setSessionId(newId());
  };
  // UX-029: re-target the DRAFT session (no messages yet) to another coworker. Unlike
  // switchAgent this never resumes that coworker's last conversation — the user is
  // composing a new one. A fresh id keeps knowledge families' per-conversation scratch
  // dirs clean and re-triggers the connection effect.
  const pickCoworker = (id: string) => {
    if (id === agent) return;
    setAgent(id);
    // An explicit draft folder pick survives a coworker change (owner catch
    // 2026-08-24). Anything inherited — boot-resume, scratch adoption, temp
    // dirs — still resets; the "never inherit" rule exists for those.
    if (!gatesWorkspace(id) || tempWorkspace || !draftFolderPicked || !workspace) {
      setWorkspace(null);
      setBranch(null);
    }
    setTempWorkspace(false);
    setShowGate(false);
    setSessionId(newId());
  };
  // UX-029: the setup row's folder chip — bind the draft to a folder before the first
  // message. A fresh id re-triggers the connection effect with the folder attached.
  const pickDraftFolder = (path: string, b?: string | null) => {
    setWorkspace(path);
    setBranch(b ?? null);
    setDraftFolderPicked(true);
    setTempWorkspace(false);
    setSessionId(newId());
    getRecentWorkspaces().then(setProjects).catch(() => {});
  };
  // UX-029 send-time dialog resolutions: bind the folder, park the stashed message for
  // the reconnect's onOpen, and let it fly. The user's send already happened — no second
  // click needed.
  const resolveSendFolder = (path: string, b?: string | null) => {
    const gate = sendGate;
    if (!gate) return;
    setSendGate(null);
    setWorkspace(path);
    setBranch(b ?? null);
    setTempWorkspace(false);
    pendingPromptRef.current = { ...gate, model };
    setSessionId(newId());
    getRecentWorkspaces().then(setProjects).catch(() => {});
  };
  const startTempAndSend = async () => {
    const gate = sendGate;
    if (!gate) return;
    const sid = newId();
    const res = await createTempWorkspace(sid, true);
    if (!res.ok || !res.path) {
      setSendGate(null);
      setItems((p) => [
        ...p,
        { kind: "notice", tone: "warn", text: res.error || t("app.temp_folder_failed") },
      ]);
      prefillComposer(gate.skill ? `/${gate.skill} ${gate.text}` : gate.text, gate.attachments);
      return;
    }
    setSendGate(null);
    setWorkspace(res.path);
    setBranch(null);
    setTempWorkspace(true);
    pendingPromptRef.current = {
      ...gate,
      model,
      notice: res.git ? t("app.temp_folder_created_git") : t("app.temp_folder_created"),
    };
    setSessionId(sid);
  };
  const cancelSendGate = () => {
    const gate = sendGate;
    setSendGate(null);
    // Give the draft back — the composer cleared it when the user hit send.
    if (gate) prefillComposer(gate.skill ? `/${gate.skill} ${gate.text}` : gate.text, gate.attachments);
  };
  // UX-029 "Save as project…": move the temporary folder somewhere real, then reconnect
  // so the engine rebinds to the new path (same session id — the transcript stays).
  const saveAsProject = async () => {
    if (running) return;
    const dest = await chooseFolder();
    if (!dest) return;
    const res = await saveSessionAsProject(sessionId, dest);
    if (!res.ok || !res.path) {
      setItems((p) => [
        ...p,
        { kind: "notice", tone: "warn", text: res.error || t("app.save_project_failed") },
      ]);
      return;
    }
    const newPath = res.path;
    setWorkspace(newPath);
    setBranch(null);
    setTempWorkspace(false);
    setItems((p) => [
      ...p,
      { kind: "notice", tone: "info", text: t("app.saved_as_project", { name: baseName(newPath) }) },
    ]);
    setConnectNonce((n) => n + 1);
    refreshSessions();
  };
  // Inbox → session: the item carries its session's workspace/agent, so open it directly.
  // UX-026: 5s top-right toast when a SCHEDULED automation run starts (never for
  // manual Run-now — the user is already watching). Rides the app-wide /ws/events
  // stream; View run opens the run's live session.
  const [runToast, setRunToast] = useState<{
    title: string; sessionId: string; workspace: string; agent: string; time: string;
  } | null>(null);
  useEffect(() => {
    const stop = connectEvents((msg) => {
      if (msg.type !== "automation_run_started") return;
      const d = (msg.data ?? {}) as Record<string, string>;
      setRunToast({
        title: d.task_title || t("toast.automation_fallback"),
        sessionId: d.session_id || "",
        workspace: d.workspace || "",
        agent: d.agent || "cowork",
        time: new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }),
      });
      announceAutomationsChanged(); // the Scheduled band's badge is now stale
    });
    return stop;
  }, []);
  useEffect(() => {
    if (!runToast) return;
    const t = window.setTimeout(() => setRunToast(null), 5000);
    return () => window.clearTimeout(t);
  }, [runToast]);

  // MEMORY-SPEC §5.1: undo a write the transcript just announced. A new memory is
  // deleted; an EDIT is rolled back to its previous text (deleting there would throw
  // away whatever the memory already held). The notice confirms in place either way.
  const undoMemorySave = async (id: number, previous?: string) => {
    if (previous) await updateMemory(id, previous).catch(() => {});
    else await deleteMemory(id).catch(() => {});
    announceMemoryChanged();
    setItems((p) =>
      p.map((it) => (it.kind === "memory" && it.id === id ? { ...it, undone: true } : it)),
    );
  };

  const openSessionFromInbox = (sid: string, ws: string, ag: string) => selectSession(sid, ws, ag);
  const selectSession = async (id: string, ws: string, ag: string) => {
    setSurface("session"); // selecting a conversation always returns to the conversation view
    setTodo([]);
    setStreaming("");
    setRunning(false);
    if (ag) setAgent(ag);
    setReviewerPaused(false);
    setDraftFolderPicked(false); // a resumed session's folder is inherited, not a pick
    setTempWorkspace(false); // the `ready` event restores the truth for temp sessions
    if (!gatesWorkspace(ag)) setShowGate(false);
    if (ws && ws !== workspace) {
      setWorkspace(ws); // switch project to the session's folder
      setBranch(null);
    }
    setSessionId(id);
    try {
      const messages = await getSessionMessages(id);
      setItems(itemsFromMessages(messages));
      setUsage(usageFromMessages(messages));
    } catch {
      setItems([]);
      setUsage(emptyUsage());
    }
  };
  const switchAgent = async (name: string) => {
    setSurface("session");
    if (name === agent) return;
    setDraftFolderPicked(false); // leaving the draft — any pick belonged to it
    rememberLastSession(agent, sessionId, workspace);
    const knownSessions = sessions.length ? sessions : await getSessions().catch(() => []);
    const knownProjects = projects.length ? projects : await getRecentWorkspaces().catch(() => []);
    const target = resumeTargetForAgent(name, knownSessions);

    setAgent(name);
    setItems([]);
    setUsage(emptyUsage());
    setStreaming("");
    setTodo([]);
    setRunning(false);

    // The live workspace is only a valid fallback for a gated persona if it came from
    // another gated persona — a knowledge persona's workspace is a scratch dir, and a
    // code-family session must never adopt one. Same for a code session's TEMPORARY dir:
    // per-conversation, never inherited. (`agent` is still the previous persona here.)
    const inheritable = gatesWorkspace(agent) && !tempWorkspace ? workspace : null;

    if (target) {
      // Code falls back to a recent folder; Cowork resumes its scratch (target.workspace) or
      // starts orphan ("" → server provisions). Chat has no workspace.
      const targetWorkspace = gatesWorkspace(name)
        ? target.workspace || fallbackWorkspace(inheritable, knownProjects)
        : target.workspace || "";
      if (targetWorkspace && targetWorkspace !== workspace) {
        setWorkspace(targetWorkspace);
        setBranch(null);
      } else if (!targetWorkspace) {
        setWorkspace(null); // orphan cowork: clear so the next `ready` adopts a fresh scratch
      }
      if (!gatesWorkspace(name)) setShowGate(false);
      else if (targetWorkspace) setShowGate(false);
      else setShowGate(true);
      setSessionId(target.sessionId);
      try {
        const messages = await getSessionMessages(target.sessionId);
        setItems(itemsFromMessages(messages));
        setUsage(usageFromMessages(messages));
      } catch {
        setItems([]);
        setUsage(emptyUsage());
      }
      return;
    }

    const id = newId();
    const fallback = gatesWorkspace(name) ? fallbackWorkspace(inheritable, knownProjects) : "";
    if (fallback && fallback !== workspace) {
      setWorkspace(fallback);
      setBranch(null);
    } else if (!fallback) {
      setWorkspace(null); // orphan cowork: server provisions a fresh scratch on connect
    }
    setSessionId(id);
    rememberLastSession(name, id, fallback);
    if (!gatesWorkspace(name)) setShowGate(false);
    else setShowGate(!fallback);
  };
  const chooseWorkspace = (path: string, b?: string | null) => {
    setWorkspace(path);
    setBranch(b ?? null);
    setShowGate(false);
    setGateCreate(false);
    setItems([]);
    setUsage(emptyUsage());
    setStreaming("");
    setTodo([]);
    setSessionId(newId());
    getRecentWorkspaces().then(setProjects).catch(() => {});
  };
  // "New project" lives under a project-scoped persona's accordion. Switch to that persona, start a
  // fresh session with no folder yet, and open the gate in create mode — so the gate's
  // surface==="session" && gatesWorkspace(agent) guard passes even if the active session was Chat/Cowork.
  const newProject = (forAgent?: string) => {
    const target = forAgent || agent;
    setSurface("session");
    setItems([]);
    setUsage(emptyUsage());
    setStreaming("");
    setTodo([]);
    setRunning(false);
    if (target !== agent) setAgent(target);
    setWorkspace(null);
    setBranch(null);
    setSessionId(newId());
    setGateCreate(true);
    setShowGate(true);
  };
  const renameConversation = async (id: string, title: string) => {
    const res = await renameSession(id, title);
    if (res.ok) refreshSessions();
  };
  const togglePinned = async (id: string, pinned: boolean) => {
    await setSessionFlags(id, { pinned });
    refreshSessions();
  };
  const toggleArchived = async (id: string, archived: boolean) => {
    await setSessionFlags(id, { archived });
    refreshSessions();
    // Archiving the open chat: leave it and start fresh (it moves to the Archived section).
    if (archived && id === sessionId) {
      setItems([]);
      setUsage(emptyUsage());
      setStreaming("");
      setTodo([]);
      setRunning(false);
      setSessionId(newId());
    }
  };
  const deleteConversation = async (id: string) => {
    const res = await deleteSession(id);
    if (!res.ok) return;
    refreshSessions();
    if (id === sessionId) {
      setItems([]);
      setUsage(emptyUsage());
      setStreaming("");
      setTodo([]);
      setRunning(false);
      setSessionId(newId());
    }
  };

  // "Run now": prepare a manual run, open its session, and auto-send the task so the agent
  // runs LIVE in the main view; finalize it in history once the first turn finishes.
  const openRunSession = (
    sessionId: string,
    ws: string,
    ag: string,
    task?: { id: string; title: string },
  ) => {
    setRunContext(task ?? null);
    setSurface("session");
    setShowGate(false);
    selectSession(sessionId, ws, ag);
  };
  const runTaskNow = async (taskId: string, title?: string) => {
    const r = await runAutomation(taskId);
    if (!r || !r.ok) return;
    pendingPromptRef.current = { text: r.prompt };
    activeRunRef.current = { taskId, runId: r.run_id, sessionId: r.session_id };
    openRunSession(r.session_id, r.workspace, r.agent, { id: taskId, title: title || "" });
  };

  // `running` too: a mid-turn reconnect may land before any item is rebuilt — a live
  // session must show the transcript (waiting row, Stop), never the intro hero.
  const idle = items.length === 0 && !streaming && !running;
  const pendingApproval = [...items].reverse().find((i) => i.kind === "approval" && !i.resolved);
  const pendingDirReq = [...items].reverse().find((i) => i.kind === "dirreq" && !i.resolved);
  const pendingToolReq = [...items].reverse().find((i) => i.kind === "toolreq" && !i.resolved);
  const pendingPlan = [...items].reverse().find((i) => i.kind === "planreq" && !i.resolved);
  const pendingTeam = [...items].reverse().find((i) => i.kind === "teamreq" && !i.resolved);
  const pendingItemsReq = [...items].reverse().find((i) => i.kind === "itemsreq" && !i.resolved);
  const pendingQuestion = [...items].reverse().find((i) => i.kind === "question" && !i.resolved);
  // Facts subtitle (§22): the session's FIXED facts, not controls — model (+ the
  // workspace folder for project-scoped sessions). Renders only once the session has history;
  // until then the model is still choosable in the composer, so there's no locked fact to state.
  const hasHistory = items.length > 0;
  // Curated labels read "Claude Opus 4.8 · Anthropic" — the provider suffix is dropdown context,
  // noise in a facts line. Fall back to the raw id without its provider prefix.
  const modelDisplay =
    modelLabels[model]?.split(" · ")[0] ||
    (model.includes(":") ? model.split(":").slice(1).join(":") : model);
  // UX-029: with the coworker picker shipping, the coworker's name is a fixed fact again
  // (it was dropped 2026-07-22 while personas were hidden). For temporary folders the raw
  // path never shows — "Temporary folder" + the Save as project… affordance instead.
  const subtitleParts = [fullPersonaName(personaOf(agent)?.name, agent), modelDisplay];
  if (isProjectScoped(personaOf(agent)) && workspace)
    subtitleParts.push(tempWorkspace ? t("root.temporary_space") : baseName(workspace));
  const showSaveAsProject = hasHistory && tempWorkspace && isProjectScoped(personaOf(agent));
  const activeInfo = sessions.find((s) => s.session_id === sessionId);
  const activeTitle = activeInfo?.title || t("sidebar.new_session");

  const desktop = isTauri();
  // Dev-only: `?overlay=1` simulates the desktop overlay layout in the browser (adds the
  // tauri-overlay class + draws fake traffic lights at the real position) so the top-left can be
  // tuned in the preview without a DMG build. Never active in the real app (isTauri() short-circuits).
  const simOverlay = !desktop && new URLSearchParams(window.location.search).has("overlay");
  // Overlay layout is macOS-ONLY: Windows/Linux keep the native title bar, so the mac
  // compensations (traffic-light insets, lowered top strips) must not apply there —
  // they rendered as misalignments under Windows' native bar (caught 2026-07-21).
  const overlay = (desktop && platformOS() === "macos") || simOverlay;
  const beginWindowDrag = (event: PointerEvent) => {
    if (!desktop || event.button !== 0) return;
    startWindowDrag();
  };

  if (booting || !uiReady) {
    return (
      <div className={"app boot-splash" + (overlay ? " tauri-overlay" : "")}>
        {/* overlay (not desktop): ?overlay=1 previews the splash's top-left in the browser
            too — the wordmark/traffic-light alignment is exactly what it exists to tune. */}
        {overlay && (
          <div className="titlebar-drag" data-tauri-drag-region>
            <span className="titlebar-brand brand-wordmark">
              <Icon name="logo" size={13} className="mark" /> OpenWorker<span className="beta-tag">BETA</span>
            </span>
          </div>
        )}
        {simOverlay && (
          <div className="sim-traffic-lights" aria-hidden="true">
            <span /><span /><span />
          </div>
        )}
        {/* The real OpenWorker mark (6-point star, same as the app/tray icon) — the old
            ✦ text glyph was a 4-point sparkle that read as another product's logo. */}
        <div className="boot-mark">
          <Icon name="logo" size={38} />
        </div>
        <div className="boot-text">
          {resumedExisting ? t("boot.restoring") : t("boot.starting")}
          <span className="beta-tag">BETA</span>
        </div>
      </div>
    );
  }

  return (
    <div
      className={
        "app" +
        (overlay ? " tauri-overlay" : "") +
        (navCollapsed ? " nav-collapsed" : "") +
        (navCollapsed && navPeek ? " nav-peek" : "")
      }
    >
      {/* Dev-only fake traffic lights so ?overlay=1 previews the real desktop top-left. */}
      {simOverlay && (
        <div className="sim-traffic-lights" aria-hidden="true">
          <span /><span /><span />
        </div>
      )}
      {/* Desktop-only auto-update prompt (15s after boot, then every 30 min; inert in browser). */}
      <UpdateBanner />
      {/* UX-026: automation-start toast — quiet panel, neutral dot/drain, accent only
          on the action (rev 2); auto-dismisses with the 5s drain bar. */}
      {runToast && (
        <div
          className="fixed top-3 right-3 z-[45] w-[290px] bg-panel border border-line rounded-xl shadow-lg px-3.5 pt-3 pb-2.5"
          data-testid="automation-toast"
        >
          <div className="flex items-center gap-2 text-[13px] font-semibold">
            <span className="w-[7px] h-[7px] rounded-full bg-faint toast-pulse" />
            {t("toast.automation_started")}
          </div>
          <div className="text-[13px] text-muted mt-0.5 ml-[15px] truncate">
            {runToast.title} · {runToast.time} {t("toast.run_count")}
          </div>
          <div className="flex items-center justify-between ml-[15px] mt-1.5">
            <button
              className="text-[13px] text-accent font-medium"
              data-testid="toast-view-run"
              onClick={() => {
                selectSession(runToast.sessionId, runToast.workspace, runToast.agent);
                setRunToast(null);
              }}
            >
              {t("toast.view_run")} ›
            </button>
            <button
              className="text-[12px] text-faint px-0.5"
              data-testid="toast-dismiss"
              title={t("common.dismiss")}
              onClick={() => setRunToast(null)}
            >
              ✕
            </button>
          </div>
          <div className="absolute left-3 right-3 bottom-1 h-[2px] rounded bg-line overflow-hidden">
            <span className="block h-full bg-faint toast-drain" />
          </div>
        </div>
      )}
      {/* When collapsed, a thin left-edge zone peeks the nav back as a floating overlay. */}
      {navCollapsed && (
        <div
          className="nav-hover-zone"
          onMouseEnter={() => setNavPeek(true)}
          aria-hidden="true"
        />
      )}
      {/* Explicit reveal affordance while collapsed (alongside hover-peek + ⌘B) — on every
          surface EXCEPT the session view, whose topbar carries the [sidebar][+][search] cluster
          instead (§22; no duplicate reveal buttons). */}
      {navCollapsed && !navPeek && surface !== "session" && (
        <button
          className="nav-reveal-btn"
          onClick={toggleNav}
          onMouseEnter={() => setNavPeek(true)}
          title={t("topbar.show_sidebar")}
          aria-label={t("topbar.show_sidebar_short")}
        >
          <Icon name="sidebar" size={16} />
        </button>
      )}
      {onboarding && (
        <Onboarding
          onDone={(next) => {
            setOnboarding(false);
            getHealth().then((h) => setModel(h.model)).catch(() => {});
            loadSettings(); // pick up a model connected during setup (clears the composer chip)
            if (next === "gallery") {
              // The specialists tip: land on Settings ▸ Personas, where the Gallery link lives.
              openSettings("personas");
            } else if (next === "automations") {
              // "Create your first automation" (§29) lands on the Automations quickstart.
              setSurface("scheduled");
            } else if (next === "work") {
              // "Start working" teaches by landing (§24, §32): a fresh session with the rail's
              // Access section expanded. Bump after the session switch settles.
              startNewSession();
              setTimeout(openAccess, 80);
            }
          }}
        />
      )}
      <Sidebar
        agent={agent}
        workspace={workspace || ""}
        surfaces={surfaces}
        sessions={sessions}
        projects={projects}
        activeSession={sessionId}
        onSwitchAgent={switchAgent}
        onNewSession={startNewSession}
        onSelectSession={selectSession}
        onNewProject={newProject}
        onRenameSession={renameConversation}
        onDeleteSession={deleteConversation}
        onArchiveSession={toggleArchived}
        onTogglePin={togglePinned}
        onManage={() => openSettings("appearance")}
        onOpenPersona={(id) => {
          openPersona(id, "session");
        }}
        onOpenScheduled={() => setSurface("scheduled")}
        onOpenAutomation={(id) => {
          setScheduledOpenId(id);
          setSurface("scheduled");
        }}
        onOpenIntegrations={() => setSurface("integrations")}
        onOpenAudit={() => setSurface("audit")}
        onOpenInbox={() => setSurface("inbox")}
        scheduledActive={surface === "scheduled"}
        integrationsActive={surface === "integrations"}
        auditActive={surface === "audit"}
        inboxActive={surface === "inbox"}
        collapsed={navCollapsed}
        onCollapse={toggleNav}
        onPeekLeave={() => setNavPeek(false)}
      />
      {surface === "scheduled" ? (
        <ScheduledView
          onOpenRun={openRunSession}
          onRunNow={runTaskNow}
          initialOpenId={scheduledOpenId}
        />
      ) : surface === "integrations" ? (
        <IntegrationsView />
      ) : surface === "settings" ? (
        <SettingsView
          key={settingsTab}
          initialTab={settingsTab}
          onOpenPersona={(id) => openPersona(id, "settings")}
          onCreateSkill={(description) => {
            // The Skills doorway (SKILLS-SPEC §5.2): creation is a conversation. Fresh
            // session, description in the composer — the user reads and hits send. With
            // no description, the prefill invites them to finish the sentence there.
            startNewSession();
            prefillComposer(
              description
                ? t("app.build_skill_prefill", { description })
                : t("app.build_skill_prefill_empty"),
            );
          }}
        />
      ) : surface === "audit" ? (
        <AuditView />
      ) : surface === "inbox" ? (
        <InboxView onOpenSession={openSessionFromInbox} />
      ) : surface === "persona" ? (
        <PersonaView
          personaId={personaViewId || agent}
          onBack={() =>
            personaViewReturn === "settings" ? openSettings("personas") : setSurface("session")
          }
          onOpenIntegrations={() => setSurface("integrations")}
        />
      ) : (
      <div className={"main" + (surface === "session" && agent !== "chat" && !railHidden ? " rail-open" : "")}>
        <div className="main-topbar">
          {/* Left: the contextual cluster — [sidebar] [+ new session] [search] — rendered ONLY
              while the sidebar is collapsed (§22; the expanded sidebar already owns those
              actions). Clicks must not start a window drag. */}
          <div className="main-topbar-side" onPointerDown={beginWindowDrag}>
            {navCollapsed && (
              <div
                className="flex items-center gap-1"
                data-testid="topbar-cluster"
                onPointerDown={(e) => e.stopPropagation()}
              >
                <button
                  className="topbar-icon-btn"
                  onClick={toggleNav}
                  aria-label={t("topbar.show_sidebar_short")}
                  title={t("topbar.show_sidebar")}
                >
                  <Icon name="sidebar" size={16} />
                </button>
                <button
                  className="topbar-icon-btn"
                  onClick={() => startNewSession()}
                  aria-label={t("sidebar.new_session")}
                  title={t("sidebar.new_session")}
                >
                  <Icon name="plus" size={16} />
                </button>
                <button
                  className="topbar-icon-btn"
                  onClick={() => setSearchOpen(true)}
                  aria-label={t("topbar.search")}
                  title={t("topbar.search")}
                >
                  <Icon name="search" size={16} />
                </button>
              </div>
            )}
            {/* §32: no session-settings row up here anymore — the §23 rest/hover/click glance
                machinery retired with the drawer. "What can this touch" lives permanently on
                the rail's Access section header; the panel toggle is the one entry. */}
          </div>
          {/* Center: title + facts subtitle (§22, amended: the ⋯ menu removed — the nav row's
              hover cluster owns pin/rename/archive/delete). The title stays: with the sidebar
              collapsed it is the only session identifier, and it anchors the subtitle. */}
          <div className="main-title" onPointerDown={beginWindowDrag}>
            <span
              className={"main-title-text" + (activeInfo ? "" : " title-ghost")}
              title={activeTitle}
            >
              {activeTitle}
            </span>
            {/* Plain facts, no affordance: the persona page it used to open is hidden for
                this release (owner ask 2026-07-22). */}
            {hasHistory && (
              <span className="title-sub" data-testid="session-subtitle">
                {subtitleParts.join(" · ")}
                {showSaveAsProject && (
                  <>
                    {" · "}
                    <button
                      className="text-accent hover:underline"
                      data-testid="save-as-project"
                      onMouseDown={(e) => e.stopPropagation()}
                      onClick={() => void saveAsProject()}
                    >
                      {t("app.save_as_project")}
                    </button>
                  </>
                )}
              </span>
            )}
          </div>
          {/* Right: session-settings icon (§23) + panel toggle. Model/mode/persona chrome is
              gone — the facts live in the subtitle, the controls in the composer (§22). */}
          <div className="main-topbar-side main-topbar-actions" onPointerDown={beginWindowDrag}>
            {railHidden && artifactCount > 0 && (
              <button
                className="topbar-artifacts-btn"
                onMouseDown={(e) => e.stopPropagation()}
                onClick={() => setRailHidden(false)}
                title={t("topbar.show_artifacts")}
              >
                <Icon name="file" size={14} />
                <span>{t("topbar.artifacts")}</span>
                <span className="topbar-artifacts-count">{artifactCount}</span>
              </button>
            )}
            {/* §32: the panel toggle is the ONE session-panel entry, for every non-chat persona
                (the rail now carries Access, so code-family gets it too). */}
            {agent !== "chat" && (
              <button
                className="topbar-icon-btn"
                onMouseDown={(e) => e.stopPropagation()}
                onClick={() => setRailHiddenPersist(!railHidden)}
                aria-label={railHidden ? t("topbar.show_side_panel") : t("topbar.hide_side_panel")}
                title={railHidden ? t("topbar.show_side_panel") : t("topbar.hide_side_panel")}
              >
                <Icon name="sidebarRight" size={16} />
              </button>
            )}
          </div>
        </div>
        {/* # team chat replaces the session view in place (owner ask 2026-08-16 —
            not a modal): the sidebar stays live, Esc/back returns to the session. */}
        {chatTeam && surface === "session" && (
          <TeamChatView teamId={chatTeam} onClose={() => setChatTeam(null)} />
        )}
        <div className={"main-workspace" + (railHidden ? " rail-hidden" : "")}>
          <div className="main-chat">
            {/* Automation-run context (owner ask 2026-07-04): a __run__ session looked like any
                other chat with no way back to the runs list. Lives INSIDE the chat column (which
                is padded to clear the absolute glass topbar — rendering above .main-workspace put
                it underneath the topbar; owner-reported CSS bug). */}
            {sessionId.startsWith("__run__") && (
              <div
                className="flex items-center gap-2 px-4 py-2 mb-1 rounded-lg text-[13px] border border-line bg-accentSoft/40"
                data-testid="run-banner"
              >
                <Icon name="clock" size={14} className="text-accent shrink-0" />
                <span className="truncate text-muted">
                  {t("run_banner.scheduled_run")}
                  {runContext?.title ? (
                    <>
                      {" — "}
                      <span className="text-ink font-medium">{runContext.title}</span>
                    </>
                  ) : null}{" "}
                  {t("run_banner.started_by_automation")}
                </span>
                <button
                  className="ml-auto shrink-0 text-accent font-medium hover:underline"
                  onClick={() => {
                    if (runContext) setScheduledOpenId(runContext.id);
                    setSurface("scheduled");
                  }}
                >
                  {t("run_banner.back_to_runs")}
                </button>
              </div>
            )}
            <div className="main-scroll" ref={scrollRef} onScroll={handleScroll}>
              {idle ? (
                agent === "cowork" ? (
                  <SessionIntro
                    sessionId={sessionId}
                    onOpenSessionSettings={openAccess}
                    onPrefill={prefillComposer}
                  />
                ) : (
                  <div className="hero">
                    <h1 className="greeting">
                      <span className="mark">✦</span>
                      {agent === "chat" ? t("hero.chat_greeting") : t("hero.build_greeting")}
                    </h1>
                    {(
                      <div className="suggestions">
                        <div className="suggest-head">{t("hero.try_a_task")}</div>
                        {SUGGESTION_KEYS.map((s, i) => (
                          <div className="suggest" key={i} onClick={() => workspace && send(t(s.key))}>
                            <span className="ico">{s.ico}</span>
                            {t(s.key)}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )
              ) : (
                <>
                  <Transcript
                    items={items}
                    onApprove={approve}
                    running={running}
                    onRetry={retry}
                    onOpenConnectors={() => setSurface("integrations")}
                    onAllowAnyway={allowAnyway}
                    onUndoMemory={(id, previous) => void undoMemorySave(id, previous)}
                    // §33 ref #3: sub-threshold streamed text renders INSIDE the live turn
                    // group (header when collapsed, quiet line when expanded) — never as a
                    // floating paragraph.
                    streamingText={streamMode(streaming, items, running) === "quiet" ? streaming : undefined}
                  />
                  {/* Live thinking (reasoning models): a quiet collapsed block that streams the
                      trace for anyone who expands it; folds into the answer's disclosure when
                      the message finalizes. */}
                  {running && reasoningStream && !streaming && (
                    <div className="transcript">
                      <ThinkingBlock text={reasoningStream} live />
                    </div>
                  )}
                  {/* Compaction runs between provider turns (nothing streams during it), so
                      the transient takes over the waiting slot with a specific label. */}
                  {running && compacting && <WaitingForAgent label={t("app.compacting_context")} />}
                  {running &&
                    !compacting &&
                    !reasoningStream &&
                    (!streaming || streamMode(streaming, items, running) === "hold") &&
                    !lastItemIsAssistant(items) && <WaitingForAgent />}
                  {streaming && streamMode(streaming, items, running) === "answer" && (
                    <div className="transcript">
                      <div className="bubble-assistant">
                        <div className="who">{t("transcript.who_assistant")}</div>
                        <Markdown text={streaming} />
                        <span className="stream-cursor">▍</span>
                      </div>
                    </div>
                  )}
                </>
              )}
            </div>

            {/* Scrolled up while the transcript is still growing → offer the way back down.
                Zero-height strip keeps the pill floating over the scroll area, above the
                composer, without reserving layout space. */}
            {!following && (running || !!streaming) && (
              <div className="relative h-0 z-10">
                <button
                  className="absolute bottom-3 left-1/2 -translate-x-1/2 flex items-center gap-1.5 px-3 py-1.5 rounded-full border border-line bg-panel shadow-md text-[12px] text-muted hover:text-ink cursor-pointer whitespace-nowrap"
                  data-testid="jump-to-latest"
                  onClick={followLatest}
                >
                  <Icon name="chevronDown" size={13} />
                  {t("app.jump_to_latest")}
                </button>
              </div>
            )}

            {/* UX-029: per-session setup (coworker + folder) lives in its own quiet row
                above the composer — never inside the per-message control row. One-time
                pick: the whole row leaves after the first message; its facts move to the
                session header. */}
            {idle && !sessionId.startsWith("__run__") && (
              <SessionSetupRow
                personas={personas}
                agent={agent}
                showFolder
                folderName={workspace && !tempWorkspace ? baseName(workspace) : null}
                onPickCoworker={pickCoworker}
                onPickFolder={pickDraftFolder}
                onManage={() => openSettings("personas")}
                onImport={() => {
                  openSettings("personas");
                  // Give the Settings page a beat to mount, then spotlight the Add section.
                  window.setTimeout(
                    () => window.dispatchEvent(new CustomEvent("ocw-focus-import")),
                    250,
                  );
                }}
              />
            )}
            {/* A scheduled agent must never read as a dead one: while a self-wake is
                pending and no turn is running, say so and offer the obvious action. */}
            {activeInfo?.liveness === "sleeping" && !running && (
              <div className="sleep-strip" data-testid="sleep-strip">
                <span className="sleep-dot" />
                <span className="sleep-text">
                  {t("app.sleep.label")}
                  {activeInfo.sleeping_until
                    ? t("app.sleep.until", {
                        time: new Date(activeInfo.sleeping_until).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }),
                      })
                    : ""}
                  {activeInfo.team?.role === "lead"
                    ? t("app.sleep.team_clause")
                    : t("app.sleep.trigger_clause")}{" "}
                  {t("app.sleep.talk_anytime")}
                </span>
                <button
                  className="btn sm"
                  data-testid="sleep-status-btn"
                  onClick={() => send(t("app.sleep.status_prompt"))}
                >
                  {t("app.sleep.ask_status")}
                </button>
              </div>
            )}
            <Composer
              mode={mode}
              model={model}
              models={models}
              modelLabels={modelLabels}
              running={running}
              gateOpen={!unattended && (!!pendingTeam || !!pendingItemsReq)}
              connected={connected}
              modelReady={modelReady}
              onConnectModel={openModelSetup}
              onOpenMemory={() => openSettings("memory")}
              onConfigureVoiceInput={() => openSettings("voice")}
              onSend={send}
              onInterrupt={interrupt}
              onModeChange={changeMode}
              onModelChange={changeModel}
              sessionId={sessionId}
              workspace={workspace || ""}
              unattended={unattended}
              onUnattendedChange={agent !== "chat" ? toggleUnattended : undefined}
              prefill={composerPrefill}
              resetKey={sessionId}
              usage={usage}
              contextWindow={modelContextWindows[model]}
              contextBar={contextBar}
              reviewerPaused={reviewerPaused}
              placeholder={
                agent === "code"
                  ? t("composer.placeholder_code")
                  : agent === "chat"
                    ? t("composer.placeholder_chat")
                    : t("composer.placeholder_cowork")
              }
              approvalSlot={
                // Live inline cards are for ATTENDED sessions only; when Unattended the prompt is
                // parked in the Inbox and surfaced via the answer-in-context card below.
                !unattended && pendingPlan?.kind === "planreq" ? (
                  <PlanCard item={pendingPlan} onRespond={respondPlan} />
                ) : !unattended && pendingItemsReq?.kind === "itemsreq" ? (
                  <WorkItemsCard item={pendingItemsReq} onRespond={respondItemsReq} />
                ) : !unattended && pendingTeam?.kind === "teamreq" ? (
                  <TeamRequestCard item={pendingTeam} onRespond={respondTeam} />
                ) : !unattended && pendingToolReq?.kind === "toolreq" ? (
                  <ToolRequestCard item={pendingToolReq} onRespond={respondTool} />
                ) : !unattended && pendingDirReq?.kind === "dirreq" ? (
                  <DirectoryRequestCard item={pendingDirReq} onRespond={respondDirectory} />
                ) : !unattended && pendingApproval?.kind === "approval" ? (
                  <ApprovalCard
                    item={pendingApproval}
                    onApprove={approve}
                    runTask={runContext}
                    autoApprove={mode === "auto-approve"}
                    compact
                  />
                ) : !unattended && pendingQuestion?.kind === "question" ? (
                  // Live ask_user in an attended session — answer inline (reuses the Inbox card UI).
                  <InboxItemCard
                    item={{
                      id: "live-question",
                      session_id: sessionId,
                      kind: "question",
                      title: pendingQuestion.question,
                      body: "",
                      state: "pending",
                      resolution: null,
                      inbox: "default",
                      created_at: "",
                      resolved_at: null,
                      options: pendingQuestion.options,
                      allow_text: pendingQuestion.allow_text,
                      multi: pendingQuestion.multi,
                      header: pendingQuestion.header,
                      questions: pendingQuestion.questions,
                    }}
                    onResolve={(_id, answer) => answerQuestion(answer)}
                    compact
                  />
                ) : sessionInbox[0] ? (
                  // Unattended session blocked on an Inbox item — answer it in context.
                  <InboxItemCard item={sessionInbox[0]} onResolve={resolveSessionInbox} compact />
                ) : undefined
              }
            />
                  </div>
          <RightRail
            active={surface === "session" && agent !== "chat" && !railHidden}
            sessionId={sessionId}
            refreshKey={browserRefreshKey}
            toolNames={items.filter((i) => i.kind === "tool").map((i: any) => i.name)}
            todo={todo}
            running={running}
            onPreviewChange={onArtifactPreview}
            // Universal scratch (UX-036): every session has a scratch surface, so the
            // Artifacts section always shows — the server lists the scratch root only.
            showArtifacts
            personaId={agent}
            projectScoped={isProjectScoped(personaOf(agent))}
            workspace={workspace || undefined}
            branch={branch}
            scratchPrimary={tempWorkspace || !isProjectScoped(personaOf(agent))}
            openAccessKey={accessKey}
            onOpenIntegrations={() => setSurface("integrations")}
            board={board}
            onExpandBoard={() => setBoardOpen(true)}
            onOpenBoardItem={(id) => {
              setBoardDetailId(id);
              setBoardOpen(true);
            }}
            /* team serializes as {} for plain sessions — lead-ness needs an actual
               role, else every solo session loses its Progress panel (owner-hit
               2026-08-21: the rail showed nothing but "More"). */
            isLead={
              teamMembers.length > 0 ||
              (curSession?.team?.role != null && curSession.team.role !== "worker")
            }
            teamMembers={teamMembers}
            teamChatEnabled={!!curSession?.team?.chat_enabled}
            teamChatUnread={curSession?.team?.chat_unread || 0}
            onOpenTeamChat={() => setChatTeam(curSession?.team?.team_id || "")}
            onOpenWorker={(w) => void selectSession(w.session_id, w.workspace, w.agent)}
            openBoardKey={boardRailKey}
          />
          {boardOpen && board && board.space && (
            <BoardOverlay
              board={board}
              onClose={() => {
                setBoardOpen(false);
                setBoardDetailId(null);
              }}
              onTransition={moveBoardItem}
              onComment={(item, body) => boardComment(sessionId, item, body)}
              loadItem={(id) => getBoardItem(sessionId, id)}
              loadAttachment={(stored) => fetchBoardAttachment(sessionId, stored)}
              onOpenWorker={(actor) => {
                // The assignee is a team actor whose worker session the sidebar
                // already knows — jump straight into its transcript.
                const match =
                  sessions.find(
                    (s) =>
                      s.team?.role === "worker" &&
                      s.team?.actor === actor &&
                      s.workspace === board.space
                  ) ||
                  sessions.find(
                    (s) => s.team?.role === "worker" && s.team?.actor === actor
                  );
                if (!match) return;
                setBoardOpen(false);
                setBoardDetailId(null);
                void selectSession(match.session_id, match.workspace, match.agent);
              }}
              initialItem={boardDetailId}
            />
          )}
        </div>
      </div>
      )}

      {/* Search from the collapsed-sidebar topbar cluster (the sidebar's own instance is
          unreachable while it's collapsed). */}
      {searchOpen && (
        <SearchModal
          sessions={sessions}
          personas={personas ?? undefined}
          onSelect={(id, ws, ag) => {
            setSearchOpen(false);
            selectSession(id, ws, ag);
          }}
          onClose={() => setSearchOpen(false)}
        />
      )}

      {/* UX-029: the send-time folder dialog — the stashed message flies as soon as a
          choice lands; Escape/backdrop restores the draft to the composer. */}
      {sendGate && surface === "session" && (
        <SendFolderDialog
          coworkerName={fullPersonaName(personaOf(agent)?.name, agent)}
          onPick={resolveSendFolder}
          onTemp={() => void startTempAndSend()}
          onCancel={cancelSendGate}
        />
      )}
      {showGate && surface === "session" && gatesWorkspace(agent) && (
        <FolderGate
          create={gateCreate}
          onChoose={chooseWorkspace}
          onCancel={
            workspace
              ? () => {
                  setShowGate(false);
                  setGateCreate(false);
                }
              : undefined
          }
        />
      )}
      {workspaceTrustRequest && (
        <WorkspaceTrustPrompt
          request={workspaceTrustRequest}
          onClose={() => setWorkspaceTrustRequest(null)}
        />
      )}
    </div>
  );
}

function lastItemIsAssistant(items: Item[]): boolean {
  for (let i = items.length - 1; i >= 0; i--) {
    const item = items[i];
    if (item.kind === "notice") continue;
    return item.kind === "assistant";
  }
  return false;
}

function WaitingForAgent({ label }: { label?: string }) {
  const { t } = useTranslation();
  return (
    <div className="waiting-transcript">
      <div className="waiting-row" aria-live="polite">
        <span className="waiting-spinner" />
        <span>{label || t("app.waiting_for_agent")}</span>
      </div>
    </div>
  );
}

function updateLastTool(
  items: Item[],
  name: string,
  status: string,
  preview?: string,
  hidden?: number,
  standingRule?: string,
  reviewerReason?: string,
  allowAnyway?: boolean,
  approvalOrigin?: string,
  approvalNote?: string,
): Item[] {
  const copy = [...items];
  for (let i = copy.length - 1; i >= 0; i--) {
    const it = copy[i];
    if (it.kind === "tool" && it.name === name && it.status === "…") {
      copy[i] = {
        ...it,
        status,
        preview,
        ...(hidden ? { hidden } : {}),
        ...(standingRule ? { standingRule } : {}),
        ...(reviewerReason ? { reviewerReason } : {}),
        ...(allowAnyway ? { allowAnyway } : {}),
        ...(approvalOrigin ? { approvalOrigin } : {}),
        ...(approvalNote ? { approvalNote } : {}),
      };
      break;
    }
  }
  return copy;
}

function resolveLastApproval(items: Item[], decision: ApprovalDecision): Item[] {
  const copy = [...items];
  for (let i = copy.length - 1; i >= 0; i--) {
    const it = copy[i];
    if (it.kind === "approval" && !it.resolved) {
      copy[i] = { ...it, resolved: decision };
      break;
    }
  }
  return copy;
}

function resolveLastDirReq(items: Item[], resolved: "granted" | "denied"): Item[] {
  const copy = [...items];
  for (let i = copy.length - 1; i >= 0; i--) {
    const it = copy[i];
    if (it.kind === "dirreq" && !it.resolved) {
      copy[i] = { ...it, resolved };
      break;
    }
  }
  return copy;
}

function resolveLastToolReq(items: Item[], resolved: "installed" | "skipped"): Item[] {
  const copy = [...items];
  for (let i = copy.length - 1; i >= 0; i--) {
    const it = copy[i];
    if (it.kind === "toolreq" && !it.resolved) {
      copy[i] = { ...it, resolved };
      break;
    }
  }
  return copy;
}

function resolveLastPlan(items: Item[], resolved: "approved" | "rejected"): Item[] {
  const copy = [...items];
  for (let i = copy.length - 1; i >= 0; i--) {
    const it = copy[i];
    if (it.kind === "planreq" && !it.resolved) {
      copy[i] = { ...it, resolved };
      break;
    }
  }
  return copy;
}

function resolveLastTeam(items: Item[], resolved: "approved" | "rejected"): Item[] {
  const copy = [...items];
  for (let i = copy.length - 1; i >= 0; i--) {
    const it = copy[i];
    if (it.kind === "teamreq" && !it.resolved) {
      copy[i] = { ...it, resolved };
      break;
    }
  }
  return copy;
}

function resolveLastItemsReq(items: Item[], resolved: "approved" | "rejected"): Item[] {
  const copy = [...items];
  for (let i = copy.length - 1; i >= 0; i--) {
    const it = copy[i];
    if (it.kind === "itemsreq" && !it.resolved) {
      copy[i] = { ...it, resolved };
      break;
    }
  }
  return copy;
}

function resolveLastQuestion(items: Item[], answer: string): Item[] {
  const copy = [...items];
  for (let i = copy.length - 1; i >= 0; i--) {
    const it = copy[i];
    if (it.kind === "question" && !it.resolved) {
      copy[i] = { ...it, resolved: answer };
      break;
    }
  }
  return copy;
}
