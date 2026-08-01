import {
  LocalBrowserSettingsClient,
  authenticatedBrowserFetch as authenticatedFetch,
  browserBool as bool,
  browserHttpBase as httpBase,
  browserNumber as number,
  browserRecord as asRecord,
  browserText as text,
  postBrowserJson as post,
  type BrowserExtensionStatus,
  type BrowserProfileState,
  type BrowserSettings,
  type BrowserSettingsClient,
  type BrowserSettingsUpdate,
  type BrowserSurface,
  type BrowserSurfaceSelection,
} from "./BrowserSettingsClient";

export type {
  BrowserExtensionStatus,
  BrowserProfileState,
  BrowserSettings,
  BrowserSettingsClient,
  BrowserSettingsUpdate,
  BrowserSiteAccessMode,
  BrowserSurface,
  BrowserSurfaceSelection,
  ExternalBrowserKind,
} from "./BrowserSettingsClient";

export type BrowserConnectionStatus =
  | "connecting"
  | "ready"
  | "loading"
  | "error"
  | "closed";

export interface BrowserTab {
  tabId: string;
  title: string;
  url: string;
  active: boolean;
  loading: boolean;
  canGoBack: boolean;
  canGoForward: boolean;
}

export interface BrowserFrame {
  tabId: string;
  frameId: string;
  sequence: number;
  mimeType: string;
  source: string | Blob;
  pixelWidth: number;
  pixelHeight: number;
  viewportWidth: number;
  viewportHeight: number;
  dpr: number;
}

export type BrowserVisualPhase =
  | "move"
  | "down"
  | "up"
  | "completed"
  | "failed"
  | "cancelled";

export interface BrowserVisualAction {
  actionId: string;
  tabId: string;
  snapshotId: string;
  frameId?: string;
  sequence: number;
  phase: BrowserVisualPhase;
  kind: string;
  target: {
    ref: string;
    x: number;
    y: number;
    box?: { x: number; y: number; width: number; height: number };
  };
  viewport: { width: number; height: number; dpr: number };
  error?: string;
}

export interface BrowserDialog {
  tabId: string;
  dialogType: "alert" | "confirm" | "prompt" | "beforeunload" | string;
  message: string;
  defaultValue: string;
}

export interface BrowserViewportState {
  open: boolean;
  visible: boolean;
  status: BrowserConnectionStatus;
  activeTabId: string;
  tabs: BrowserTab[];
  frame: BrowserFrame | null;
  visualAction: BrowserVisualAction | null;
  dialog: BrowserDialog | null;
  error: string;
  connected: boolean;
}

export type BrowserHistoryAction = "back" | "forward" | "reload";

export type BrowserInput =
  | {
      type: "pointer";
      phase: "move" | "down" | "up";
      x: number;
      y: number;
      button: number;
      buttons: number;
      click_count?: number;
      pointer_type?: string;
      modifiers?: string[];
    }
  | {
      type: "wheel";
      x: number;
      y: number;
      delta_x: number;
      delta_y: number;
      modifiers?: string[];
    }
  | {
      type: "key";
      phase: "down" | "up";
      key: string;
      code: string;
      repeat?: boolean;
      modifiers?: string[];
    }
  | { type: "text"; text: string }
  | { type: "resize"; width: number; height: number; dpr: number };

export interface BrowserViewportConnection extends BrowserSettingsClient {
  start(): void;
  getState(): BrowserViewportState;
  subscribe(listener: (state: BrowserViewportState) => void): () => void;
  open(): Promise<void>;
  refresh(): Promise<void>;
  history(action: BrowserHistoryAction): Promise<void>;
  navigate(url: string): void;
  sendInput(input: BrowserInput): void;
  acknowledgeCursor(actionId: string, frameId?: string): void;
  resolveDialog(action: "accept" | "dismiss", promptText?: string): Promise<void>;
  selectBrowserSurface(surface: BrowserSurface): Promise<BrowserSurfaceSelection>;
  close(): Promise<void>;
  destroy(): void;
}

interface JsonRecord {
  [key: string]: unknown;
}

const emptyState = (): BrowserViewportState => ({
  open: false,
  visible: true,
  status: "connecting",
  activeTabId: "",
  tabs: [],
  frame: null,
  visualAction: null,
  dialog: null,
  error: "",
  connected: false,
});

const wsBase = (): string =>
  (globalThis as any).__COWORKER_WS__ ||
  (import.meta as any).env?.VITE_COWORKER_WS ||
  "ws://127.0.0.1:8765";

const apiToken = (): string =>
  (globalThis as any).__COWORKER_API_TOKEN__ ||
  (import.meta as any).env?.VITE_COWORKER_API_TOKEN ||
  "";

function normalizeTab(raw: unknown, index: number, activeTabId: string): BrowserTab {
  const tab = asRecord(raw);
  const tabId = text(tab.tab_id ?? tab.tabId, `tab_${index + 1}`);
  const status = text(tab.status);
  return {
    tabId,
    title: text(tab.title, "New tab"),
    url: text(tab.url),
    active: bool(tab.active, tabId === activeTabId || (!activeTabId && index === 0)),
    loading: bool(tab.loading, status === "loading"),
    canGoBack: bool(tab.can_go_back ?? tab.canGoBack),
    canGoForward: bool(tab.can_go_forward ?? tab.canGoForward),
  };
}

function normalizeFrame(raw: unknown, binary?: Blob): BrowserFrame | null {
  const frame = asRecord(raw);
  const dataUrl = text(frame.data_url ?? frame.dataUrl);
  const source = binary || dataUrl;
  if (!source) return null;
  const metadata = asRecord(frame.metadata);
  const dpr = Math.max(
    0.1,
    number(metadata.device_scale_factor ?? metadata.dpr ?? frame.dpr, 1),
  );
  const pixelWidth = Math.max(1, number(frame.width ?? metadata.pixel_width, 1));
  const pixelHeight = Math.max(1, number(frame.height ?? metadata.pixel_height, 1));
  const viewportWidth = Math.max(
    1,
    number(
      metadata.viewport_width ?? metadata.css_width ?? frame.viewport_width,
      pixelWidth / dpr,
    ),
  );
  const viewportHeight = Math.max(
    1,
    number(
      metadata.viewport_height ?? metadata.css_height ?? frame.viewport_height,
      pixelHeight / dpr,
    ),
  );
  return {
    tabId: text(frame.tab_id ?? frame.tabId),
    frameId: text(frame.frame_id ?? frame.frameId, `frame_${number(frame.sequence)}`),
    sequence: number(frame.sequence),
    mimeType: text(frame.mime_type ?? frame.mimeType, binary?.type || "image/jpeg"),
    source,
    pixelWidth,
    pixelHeight,
    viewportWidth,
    viewportHeight,
    dpr,
  };
}

function normalizeVisual(raw: unknown): BrowserVisualAction | null {
  const event = asRecord(raw);
  const target = asRecord(event.target);
  const viewport = asRecord(event.viewport);
  const phase = text(event.phase) as BrowserVisualPhase;
  if (
    !["move", "down", "up", "completed", "failed", "cancelled"].includes(phase) ||
    !text(event.action_id ?? event.actionId) ||
    !Number.isFinite(Number(target.x)) ||
    !Number.isFinite(Number(target.y))
  ) {
    return null;
  }
  const box = asRecord(target.box);
  return {
    actionId: text(event.action_id ?? event.actionId),
    tabId: text(event.tab_id ?? event.tabId),
    snapshotId: text(event.snapshot_id ?? event.snapshotId),
    frameId: text(event.frame_id ?? event.frameId) || undefined,
    sequence: number(event.sequence),
    phase,
    kind: text(event.kind, "action"),
    target: {
      ref: text(target.ref),
      x: number(target.x),
      y: number(target.y),
      ...(Object.keys(box).length
        ? {
            box: {
              x: number(box.x),
              y: number(box.y),
              width: number(box.width),
              height: number(box.height),
            },
          }
        : {}),
    },
    viewport: {
      width: Math.max(1, number(viewport.width, 1280)),
      height: Math.max(1, number(viewport.height, 900)),
      dpr: Math.max(0.1, number(viewport.dpr, 1)),
    },
    error: text(event.error) || undefined,
  };
}

function normalizeDialog(raw: unknown): BrowserDialog | null {
  const dialog = asRecord(raw);
  const dialogType = text(dialog.dialog_type ?? dialog.dialogType);
  const tabId = text(dialog.tab_id ?? dialog.tabId);
  if (!dialogType || !tabId) return null;
  return {
    tabId,
    dialogType,
    message: text(dialog.message),
    defaultValue: text(dialog.default_value ?? dialog.defaultValue),
  };
}

function modifiers(event: {
  altKey: boolean;
  ctrlKey: boolean;
  metaKey: boolean;
  shiftKey: boolean;
}): string[] {
  const result: string[] = [];
  if (event.altKey) result.push("Alt");
  if (event.ctrlKey) result.push("Control");
  if (event.metaKey) result.push("Meta");
  if (event.shiftKey) result.push("Shift");
  return result;
}

export { modifiers as browserInputModifiers };

/**
 * Browser transport adapter.
 *
 * The UI only understands the typed state above. This adapter accepts both the final
 * versioned protocol names (`browser_state`, `browser_frame`,
 * `browser_action_visual`) and the MVP aliases (`state`, `frame`,
 * `visual_action`). That keeps frame transport replaceable: MVP data URLs and the
 * later metadata+binary JPEG pair produce the same BrowserFrame.
 */
export class LocalBrowserViewportConnection implements BrowserViewportConnection {
  private state = emptyState();
  private listeners = new Set<(state: BrowserViewportState) => void>();
  private socket: WebSocket | null = null;
  private retryTimer: number | null = null;
  private pollTimer: number | null = null;
  private pendingFrame: JsonRecord | null = null;
  private selectedBrowserSurface: BrowserSurface = "iab";
  private readonly settingsClient: LocalBrowserSettingsClient;
  private destroyed = false;
  private started = false;

  constructor(private readonly sessionId: string) {
    this.settingsClient = new LocalBrowserSettingsClient(sessionId);
  }

  start(): void {
    if (this.started) return;
    this.started = true;
    this.destroyed = false;
    this.connect();
    void this.refresh();
    this.pollTimer = globalThis.setInterval(() => void this.refresh(), 1200);
  }

  getState(): BrowserViewportState {
    return this.state;
  }

  subscribe(listener: (state: BrowserViewportState) => void): () => void {
    this.listeners.add(listener);
    listener(this.state);
    return () => this.listeners.delete(listener);
  }

  private update(changes: Partial<BrowserViewportState>) {
    this.state = { ...this.state, ...changes };
    for (const listener of this.listeners) listener(this.state);
  }

  private connect() {
    if (this.destroyed || typeof WebSocket === "undefined") return;
    const token = apiToken();
    const socketUrl = `${wsBase()}/ws/browser/${encodeURIComponent(this.sessionId)}`;
    const socket = token
      ? new WebSocket(socketUrl, ["openworker", token])
      : new WebSocket(socketUrl);
    socket.binaryType = "blob";
    this.socket = socket;
    socket.onopen = () => {
      if (this.socket === socket) this.update({ connected: true });
    };
    socket.onmessage = (event) => {
      if (this.socket === socket) this.receive(event.data);
    };
    socket.onerror = () => {
      // A missing stream endpoint is not a page error: the state poll still supports
      // the data-url MVP and older sidecars.
      if (this.socket === socket) this.update({ connected: false });
    };
    socket.onclose = () => {
      if (this.socket !== socket) return;
      this.socket = null;
      this.update({ connected: false });
      if (!this.destroyed) {
        this.retryTimer = globalThis.setTimeout(() => this.connect(), 1200);
      }
    };
  }

  private receive(data: unknown) {
    if (data instanceof Blob) {
      const frame = normalizeFrame(this.pendingFrame, data);
      this.pendingFrame = null;
      if (
        frame &&
        (!this.state.activeTabId || frame.tabId === this.state.activeTabId) &&
        (!this.state.frame ||
          frame.tabId !== this.state.frame.tabId ||
          frame.sequence >= this.state.frame.sequence)
      ) {
        this.update({ frame });
      }
      return;
    }
    if (data instanceof ArrayBuffer) {
      const mime = text(this.pendingFrame?.mime_type, "image/jpeg");
      this.receive(new Blob([data], { type: mime }));
      return;
    }
    if (typeof data !== "string") return;
    let message: JsonRecord;
    try {
      message = asRecord(JSON.parse(data));
    } catch {
      return;
    }
    this.applyMessage(message);
  }

  private applyMessage(message: JsonRecord) {
    const type = text(message.type);
    const payload = asRecord(message.data);
    const event = Object.keys(payload).length ? { ...message, ...payload } : message;
    if (type === "browser_state" || type === "state") {
      this.applyState(event);
      return;
    }
    if (type === "browser_frame" || type === "frame") {
      const frame = normalizeFrame(event);
      if (frame) {
        const activeTabMatches =
          !this.state.activeTabId ||
          frame.tabId === this.state.activeTabId;
        if (
          activeTabMatches &&
          (!this.state.frame ||
            frame.tabId !== this.state.frame.tabId ||
            frame.sequence >= this.state.frame.sequence)
        ) {
          this.update({ frame });
        }
      } else {
        this.pendingFrame = event;
      }
      return;
    }
    if (type === "browser_action_visual" || type === "visual_action") {
      const visualAction = normalizeVisual(event);
      if (
        visualAction &&
        (!this.state.visualAction ||
          visualAction.sequence >= this.state.visualAction.sequence)
      ) {
        this.update({ visualAction });
      }
      return;
    }
    if (type === "browser_dialog" || type === "dialog") {
      const dialog = normalizeDialog(event);
      if (
        dialog &&
        (!this.state.activeTabId ||
          dialog.tabId === this.state.activeTabId)
      ) {
        this.update({ dialog });
      }
      return;
    }
    if (type === "error") {
      this.update({
        status: "error",
        error: text(event.error, "The browser connection failed."),
      });
    }
  }

  private applyState(raw: JsonRecord) {
    const previousActiveTabId = this.state.activeTabId;
    const activeTabId = text(
      raw.active_tab_id ?? raw.activeTabId,
      previousActiveTabId,
    );
    const hasTabs = Array.isArray(raw.tabs);
    const tabValues = hasTabs ? raw.tabs as unknown[] : [];
    let tabs = hasTabs
      ? tabValues.map((tab, index) => normalizeTab(tab, index, activeTabId))
      : this.state.tabs;
    // Legacy /v1/browser/state shape: one implicit tab.
    if (hasTabs && !tabs.length && (raw.open || raw.url || raw.title)) {
      tabs = [
        normalizeTab(
          {
            tab_id: activeTabId || "tab_1",
            title: raw.title,
            url: raw.url,
            active: true,
            loading: raw.status === "loading",
            can_go_back: raw.can_go_back,
            can_go_forward: raw.can_go_forward,
          },
          0,
          activeTabId,
        ),
      ];
    }
    const rawStatus = text(raw.status, bool(raw.open) ? "ready" : "closed");
    const status: BrowserConnectionStatus =
      rawStatus === "open"
        ? "ready"
        : ["connecting", "ready", "loading", "error", "closed"].includes(rawStatus)
          ? (rawStatus as BrowserConnectionStatus)
          : bool(raw.open)
            ? "ready"
            : "closed";
    const legacyFrame = normalizeFrame({
      tab_id: activeTabId || tabs[0]?.tabId,
      frame_id: text(raw.frame_id, "legacy"),
      sequence: number(raw.frame_sequence),
      mime_type: "image/png",
      data_url: raw.screenshot_data_url,
      width: number(raw.viewport_width, 1280),
      height: number(raw.viewport_height, 900),
      metadata: {
        viewport_width: number(raw.viewport_width, 1280),
        viewport_height: number(raw.viewport_height, 900),
        dpr: number(raw.dpr, 1),
      },
    });
    const open = bool(raw.open, status !== "closed");
    const selectedTabId =
      activeTabId ||
      tabs.find((tab) => tab.active)?.tabId ||
      tabs[0]?.tabId ||
      "";
    const activeTabChanged =
      Boolean(previousActiveTabId) &&
      Boolean(selectedTabId) &&
      previousActiveTabId !== selectedTabId;
    if (activeTabChanged) this.pendingFrame = null;
    const hasDialog = Object.prototype.hasOwnProperty.call(raw, "dialog");
    const dialog = hasDialog
      ? normalizeDialog(raw.dialog)
      : activeTabChanged
        ? null
        : this.state.dialog;
    this.update({
      open,
      visible: bool(raw.visible, this.state.visible),
      status,
      activeTabId: selectedTabId,
      tabs: !open ? [] : tabs,
      error: text(raw.last_error ?? raw.error),
      ...(!open || activeTabChanged
        ? { frame: null, visualAction: null, dialog: null }
        : {}),
      ...(open ? { dialog } : {}),
      ...(legacyFrame ? { frame: legacyFrame } : {}),
    });
  }

  async refresh(): Promise<void> {
    if (this.destroyed) return;
    try {
      const query = new URLSearchParams({ session_id: this.sessionId });
      const response = await authenticatedFetch(`${httpBase()}/v1/browser/state?${query}`);
      if (!response.ok) {
        if (response.status === 404) this.update({ open: false, status: "closed" });
        return;
      }
      this.applyState(asRecord(await response.json()));
    } catch {
      // The sidecar may still be starting. Keep the last good frame/state; the app's
      // normal health UI owns global connectivity.
    }
  }

  async open(): Promise<void> {
    // Open optimistically so the side panel immediately shows its matching
    // loading/error states instead of leaving the human's click unanswered.
    this.update({
      open: true,
      visible: true,
      status: "connecting",
      error: "",
    });
    const response = await post("/v1/browser/open", {
      session_id: this.sessionId,
    });
    const data = asRecord(await response.json().catch(() => ({})));
    if (!response.ok || data.ok === false) {
      const message = text(
        data.message ?? data.error,
        "Could not open the browser.",
      );
      this.update({ status: "error", error: message });
      throw new Error(message);
    }
    this.applyState(data);
  }

  async history(action: BrowserHistoryAction): Promise<void> {
    const response = await post("/v1/browser/history", {
      session_id: this.sessionId,
      action,
    });
    const data = asRecord(await response.json().catch(() => ({})));
    if (!response.ok || data.ok === false) {
      throw new Error(text(data.error, `Could not ${action} in the browser.`));
    }
    if (Object.keys(data).length > 1) this.applyState(data);
  }

  navigate(url: string): void {
    this.sendJson({ type: "navigate", session_id: this.sessionId, url });
  }

  sendInput(input: BrowserInput): void {
    // The browser is a shared surface. Human input and agent actions are both sent
    // as they occur; the runtime resolves ordering and refreshes stale snapshots.
    this.sendJson({ ...input, session_id: this.sessionId });
  }

  acknowledgeCursor(actionId: string, frameId?: string): void {
    this.sendJson({
      type: "browser_cursor_arrived",
      session_id: this.sessionId,
      action_id: actionId,
      ...(frameId ? { frame_id: frameId } : {}),
    });
  }

  async resolveDialog(
    action: "accept" | "dismiss",
    promptText?: string,
  ): Promise<void> {
    const response = await post("/v1/browser/dialog", {
      session_id: this.sessionId,
      action,
      ...(promptText !== undefined ? { prompt_text: promptText } : {}),
    });
    const data = asRecord(await response.json().catch(() => ({})));
    if (!response.ok || data.ok === false) {
      throw new Error(
        text(data.message ?? data.error, "Could not resolve the browser dialog."),
      );
    }
    this.update({ dialog: null });
  }

  async getProfile(): Promise<BrowserProfileState> {
    return this.settingsClient.getProfile();
  }

  async setRememberSignins(remember: boolean): Promise<BrowserProfileState> {
    return this.settingsClient.setRememberSignins(remember);
  }

  async clearBrowserData(): Promise<BrowserProfileState> {
    return this.settingsClient.clearBrowserData();
  }

  async getBrowserSettings(): Promise<BrowserSettings> {
    return this.settingsClient.getBrowserSettings();
  }

  async updateBrowserSettings(
    settings: BrowserSettingsUpdate,
  ): Promise<BrowserSettings> {
    return this.settingsClient.updateBrowserSettings(settings);
  }

  async getBrowserExtensionStatus(): Promise<BrowserExtensionStatus> {
    const status = await this.settingsClient.getBrowserExtensionStatus();
    return {
      ...status,
      selectedSurface: status.selectedSurface || this.selectedBrowserSurface,
    };
  }

  async selectBrowserSurface(
    surface: BrowserSurface,
  ): Promise<BrowserSurfaceSelection> {
    const response = await post("/v1/browser-extension/select", {
      session_id: this.sessionId,
      surface,
    });
    const data = asRecord(await response.json().catch(() => ({})));
    if (!response.ok || data.ok === false) {
      throw new Error(
        text(
          data.message ?? data.error,
          `Could not use ${surface} for this task.`,
        ),
      );
    }
    const selected = text(data.surface, surface).toLowerCase();
    const value: BrowserSurfaceSelection = {
      surface: selected === "chrome" ? "chrome" : "iab",
      available: bool(data.available, true),
    };
    if (value.available) this.selectedBrowserSurface = value.surface;
    return value;
  }

  async close(): Promise<void> {
    const response = await post("/v1/browser/close", { session_id: this.sessionId });
    const data = asRecord(await response.json().catch(() => ({})));
    if (!response.ok || data.ok === false) {
      throw new Error(text(data.error, "Could not close the browser."));
    }
    this.update({
      open: false,
      status: "closed",
      frame: null,
      visualAction: null,
      dialog: null,
      tabs: [],
      activeTabId: "",
    });
  }

  private sendJson(value: JsonRecord) {
    if (this.socket?.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify(value));
    }
  }

  destroy(): void {
    if (!this.started) return;
    this.destroyed = true;
    this.started = false;
    if (this.retryTimer !== null) globalThis.clearTimeout(this.retryTimer);
    if (this.pollTimer !== null) globalThis.clearInterval(this.pollTimer);
    this.retryTimer = null;
    this.pollTimer = null;
    this.socket?.close();
    this.socket = null;
    this.listeners.clear();
  }
}

export function createBrowserViewportConnection(
  sessionId: string,
): BrowserViewportConnection {
  return new LocalBrowserViewportConnection(sessionId);
}
