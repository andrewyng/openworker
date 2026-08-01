import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  aspectFitRect,
  browserPointFromClient,
  BrowserViewport,
} from "./BrowserViewport";
import type {
  BrowserExtensionStatus,
  BrowserHistoryAction,
  BrowserInput,
  BrowserSettings,
  BrowserSettingsUpdate,
  BrowserSurface,
  BrowserViewportConnection,
  BrowserViewportState,
} from "../browser/BrowserViewportClient";
import { LocalBrowserViewportConnection } from "../browser/BrowserViewportClient";

const DATA_URL =
  "data:image/svg+xml;charset=utf-8," +
  encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720"/>');

const nativeResizeObserver = globalThis.ResizeObserver;
const nativeDevicePixelRatio = window.devicePixelRatio;
let resizeObservers: MockResizeObserver[] = [];

class MockResizeObserver implements ResizeObserver {
  private readonly callback: ResizeObserverCallback;
  private readonly targets = new Set<Element>();

  constructor(callback: ResizeObserverCallback) {
    this.callback = callback;
    resizeObservers.push(this);
  }

  observe(target: Element) {
    this.targets.add(target);
  }

  unobserve(target: Element) {
    this.targets.delete(target);
  }

  disconnect() {
    this.targets.clear();
  }

  takeRecords(): ResizeObserverEntry[] {
    return [];
  }

  emit(target: Element, width: number, height: number) {
    if (!this.targets.has(target)) return;
    this.callback(
      [
        {
          target,
          contentRect: {
            x: 0,
            y: 0,
            top: 0,
            right: width,
            bottom: height,
            left: 0,
            width,
            height,
            toJSON: () => ({}),
          },
        } as ResizeObserverEntry,
      ],
      this,
    );
  }
}

beforeEach(() => {
  resizeObservers = [];
  Object.defineProperty(window, "PointerEvent", {
    configurable: true,
    value: MouseEvent,
  });
  Object.defineProperty(globalThis, "ResizeObserver", {
    configurable: true,
    value: MockResizeObserver,
  });
  Object.defineProperty(window, "devicePixelRatio", {
    configurable: true,
    value: 1,
  });
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  Object.defineProperty(globalThis, "ResizeObserver", {
    configurable: true,
    value: nativeResizeObserver,
  });
  Object.defineProperty(window, "devicePixelRatio", {
    configurable: true,
    value: nativeDevicePixelRatio,
  });
});

const baseState = (): BrowserViewportState => ({
  open: true,
  visible: true,
  status: "ready",
  activeTabId: "tab_1",
  tabs: [
    {
      tabId: "tab_1",
      title: "OpenWorker browser fixture",
      url: "https://example.test/dashboard",
      active: true,
      loading: false,
      canGoBack: true,
      canGoForward: false,
    },
  ],
  frame: {
    tabId: "tab_1",
    frameId: "frame_12",
    sequence: 12,
    mimeType: "image/svg+xml",
    source: DATA_URL,
    pixelWidth: 1280,
    pixelHeight: 720,
    viewportWidth: 1280,
    viewportHeight: 720,
    dpr: 1,
  },
  visualAction: null,
  dialog: null,
  error: "",
  connected: true,
});

class FakeConnection implements BrowserViewportConnection {
  state = baseState();
  listeners = new Set<(state: BrowserViewportState) => void>();
  inputs: BrowserInput[] = [];
  histories: BrowserHistoryAction[] = [];
  navigations: string[] = [];
  acknowledgements: { actionId: string; frameId?: string }[] = [];
  openCalls = 0;
  closeCalls = 0;
  refreshCalls = 0;
  profile = { rememberSignins: false, hasSavedData: false };
  browserSettings: BrowserSettings = {
    siteAccessMode: "ask",
    allowedSites: [],
    blockedSites: ["blocked.example"],
    rememberSignins: false,
    downloadDirectory: "/Users/test/Downloads",
    askDownloadLocation: false,
    developerMode: false,
  };
  settingsUpdates: BrowserSettingsUpdate[] = [];
  browserExtensionStatus: BrowserExtensionStatus = {
    selectedSurface: "iab",
    surfaces: [
      {
        surface: "iab",
        label: "OpenWorker isolated browser",
        connected: true,
        available: true,
        claimedTabs: 0,
        client: {},
        disconnectReason: "",
        nativeHostInstalled: true,
        extensionId: "",
      },
      {
        surface: "chrome",
        label: "Google Chrome",
        connected: false,
        available: false,
        claimedTabs: 0,
        client: {},
        disconnectReason: "",
        nativeHostInstalled: true,
        extensionId: "chrome-extension-id",
      },
    ],
  };
  selectedBrowserSurfaces: BrowserSurface[] = [];

  start() {}

  getState() {
    return this.state;
  }

  subscribe(listener: (state: BrowserViewportState) => void) {
    this.listeners.add(listener);
    listener(this.state);
    return () => this.listeners.delete(listener);
  }

  emit(changes: Partial<BrowserViewportState>) {
    this.state = { ...this.state, ...changes };
    for (const listener of this.listeners) listener(this.state);
  }

  async refresh() {
    this.refreshCalls += 1;
  }

  async open() {
    this.openCalls += 1;
    this.emit({ open: true, visible: true, status: "ready" });
  }

  async history(action: BrowserHistoryAction) {
    this.histories.push(action);
  }

  navigate(url: string) {
    this.navigations.push(url);
  }

  sendInput(input: BrowserInput) {
    this.inputs.push(input);
  }

  acknowledgeCursor(actionId: string, frameId?: string) {
    this.acknowledgements.push({ actionId, frameId });
  }

  async resolveDialog(action: "accept" | "dismiss", promptText?: string) {
    this.emit({ dialog: null });
    this.inputs.push({
      type: "text",
      text: `${action}:${promptText || ""}`,
    });
  }

  async getProfile() {
    return this.profile;
  }

  async setRememberSignins(rememberSignins: boolean) {
    this.profile = { rememberSignins, hasSavedData: rememberSignins };
    return this.profile;
  }

  async clearBrowserData() {
    this.profile = { rememberSignins: false, hasSavedData: false };
    return this.profile;
  }

  async getBrowserSettings() {
    return this.browserSettings;
  }

  async updateBrowserSettings(update: BrowserSettingsUpdate) {
    this.settingsUpdates.push(update);
    this.browserSettings = { ...this.browserSettings, ...update };
    if (update.rememberSignins !== undefined) {
      this.profile = {
        rememberSignins: update.rememberSignins,
        hasSavedData: update.rememberSignins,
      };
    }
    return this.browserSettings;
  }

  async getBrowserExtensionStatus() {
    return this.browserExtensionStatus;
  }

  async selectBrowserSurface(surface: BrowserSurface) {
    this.selectedBrowserSurfaces.push(surface);
    this.browserExtensionStatus = {
      ...this.browserExtensionStatus,
      selectedSurface: surface,
    };
    return { surface, available: true };
  }

  async close() {
    this.closeCalls += 1;
    this.emit({ open: false, status: "closed" });
  }

  destroy() {}
}

function setStageBounds(stage: HTMLElement, width = 1000, height = 500) {
  Object.defineProperty(stage, "getBoundingClientRect", {
    configurable: true,
    value: () => ({
      x: 20,
      y: 30,
      left: 20,
      top: 30,
      right: 20 + width,
      bottom: 30 + height,
      width,
      height,
      toJSON: () => ({}),
    }),
  });
  fireEvent(window, new Event("resize"));
}

function emitStageResize(stage: HTMLElement, width: number, height: number) {
  for (const observer of resizeObservers) observer.emit(stage, width, height);
}

describe("BrowserViewport coordinate transforms", () => {
  it("aspect-fits without counting letterboxing as browser pixels", () => {
    expect(aspectFitRect(1000, 500, 800, 600)).toEqual({
      x: 166.66666666666663,
      y: 0,
      width: 666.6666666666667,
      height: 500,
    });

    const center = browserPointFromClient(
      520,
      280,
      { left: 20, top: 30, width: 1000, height: 500 },
      800,
      600,
    );
    expect(center?.x).toBeCloseTo(400);
    expect(center?.y).toBeCloseTo(300);

    expect(
      browserPointFromClient(
        40,
        280,
        { left: 20, top: 30, width: 1000, height: 500 },
        800,
        600,
      ),
    ).toBeNull();
  });
});

describe("LocalBrowserViewportConnection tab streams", () => {
  it("maps the Browser settings contract between the API and the UI model", async () => {
    const responses = [
      {
        site_access_mode: "auto",
        allowed_sites: ["docs.example.com"],
        blocked_sites: ["ads.example.com"],
        remember_signins: true,
        download_directory: "/Users/test/Downloads",
        ask_download_location: false,
        developer_mode: false,
      },
      {
        site_access_mode: "allow",
        allowed_sites: ["docs.example.com"],
        blocked_sites: ["ads.example.com"],
        remember_signins: true,
        download_directory: "/Users/test/Downloads",
        ask_download_location: true,
        developer_mode: false,
      },
    ];
    const fetchMock = vi.fn(async () =>
      new Response(JSON.stringify(responses.shift()), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const connection = new LocalBrowserViewportConnection("session-settings");

    await expect(connection.getBrowserSettings()).resolves.toEqual({
      siteAccessMode: "auto",
      allowedSites: ["docs.example.com"],
      blockedSites: ["ads.example.com"],
      rememberSignins: true,
      downloadDirectory: "/Users/test/Downloads",
      askDownloadLocation: false,
      developerMode: false,
    });
    await expect(
      connection.updateBrowserSettings({
        siteAccessMode: "allow",
        askDownloadLocation: true,
      }),
    ).resolves.toEqual(
      expect.objectContaining({
        siteAccessMode: "allow",
        askDownloadLocation: true,
      }),
    );

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      "http://127.0.0.1:8765/v1/browser/settings",
      expect.objectContaining({ headers: expect.any(Headers) }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "http://127.0.0.1:8765/v1/browser/settings",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          site_access_mode: "allow",
          ask_download_location: true,
        }),
      }),
    );
  });

  it("maps Chrome connection and task-source contracts", async () => {
    const responses = [
      {
        ok: true,
        chrome: {
          connected: true,
          available: true,
          shared_tab_count: 2,
          selected_for_task: false,
          native_host_installed: true,
          extension_id: "chrome-extension-id",
        },
      },
      {
        ok: true,
        surface: "chrome",
        available: true,
      },
    ];
    const fetchMock = vi.fn(async () =>
      new Response(JSON.stringify(responses.shift()), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const connection = new LocalBrowserViewportConnection("session-extension");

    await expect(connection.getBrowserExtensionStatus()).resolves.toEqual({
      selectedSurface: "iab",
      surfaces: [
        expect.objectContaining({
          surface: "chrome",
          connected: true,
          claimedTabs: 2,
          nativeHostInstalled: true,
          extensionId: "chrome-extension-id",
        }),
      ],
    });
    await expect(connection.selectBrowserSurface("chrome")).resolves.toEqual({
      surface: "chrome",
      available: true,
    });

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      "http://127.0.0.1:8765/v1/browser-extension/status?session_id=session-extension",
      expect.objectContaining({ headers: expect.any(Headers) }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "http://127.0.0.1:8765/v1/browser-extension/select",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          session_id: "session-extension",
          surface: "chrome",
        }),
      }),
    );
  });

  it("sends browser input without a control-mode handshake", () => {
    const connection = new LocalBrowserViewportConnection("session-resize");
    const sent: Record<string, unknown>[] = [];
    (
      connection as unknown as {
        sendJson: (message: Record<string, unknown>) => void;
      }
    ).sendJson = (message) => sent.push(message);

    connection.sendInput({ type: "resize", width: 960, height: 640, dpr: 2 });
    connection.sendInput({
      type: "pointer",
      phase: "down",
      x: 100,
      y: 80,
      button: 0,
      buttons: 1,
    });

    expect(sent).toEqual([
      {
        type: "resize",
        width: 960,
        height: 640,
        dpr: 2,
        session_id: "session-resize",
      },
      {
        type: "pointer",
        phase: "down",
        x: 100,
        y: 80,
        button: 0,
        buttons: 1,
        session_id: "session-resize",
      },
    ]);
  });

  it("preserves tabs on partial state and accepts a new tab's lower frame sequence", () => {
    const connection = new LocalBrowserViewportConnection("session-tabs");
    const applyMessage = (
      connection as unknown as { applyMessage: (message: Record<string, unknown>) => void }
    ).applyMessage.bind(connection);

    applyMessage({
      type: "browser_state",
      open: true,
      status: "open",
      active_tab_id: "tab_1",
      tabs: [{ tab_id: "tab_1", title: "One", url: "https://one.test" }],
    });
    applyMessage({
      type: "browser_frame",
      tab_id: "tab_1",
      frame_id: "frame_100",
      sequence: 100,
      data_url: DATA_URL,
      width: 1280,
      height: 720,
    });
    applyMessage({ type: "browser_state", status: "open" });

    expect(connection.getState().tabs).toHaveLength(1);

    applyMessage({
      type: "browser_state",
      open: true,
      status: "open",
      active_tab_id: "tab_2",
      tabs: [
        { tab_id: "tab_1", title: "One", url: "https://one.test" },
        {
          tab_id: "tab_2",
          title: "Two",
          url: "https://two.test",
          active: true,
        },
      ],
    });
    applyMessage({
      type: "browser_frame",
      tab_id: "tab_1",
      frame_id: "frame_101",
      sequence: 101,
      data_url: DATA_URL,
      width: 1280,
      height: 720,
    });
    expect(connection.getState().frame).toBeNull();

    applyMessage({
      type: "browser_frame",
      tab_id: "tab_2",
      frame_id: "frame_1",
      sequence: 1,
      data_url: DATA_URL,
      width: 1280,
      height: 720,
    });

    expect(connection.getState().activeTabId).toBe("tab_2");
    expect(connection.getState().frame).toEqual(
      expect.objectContaining({ tabId: "tab_2", sequence: 1 }),
    );
  });
});

describe("BrowserViewport", () => {
  it("opens a blank browser when the human launcher is pressed", async () => {
    const connection = new FakeConnection();
    connection.state = {
      ...connection.state,
      open: false,
      status: "closed",
      activeTabId: "",
      tabs: [],
      frame: null,
    };

    render(
      <BrowserViewport
        sessionId="session_a"
        connection={connection}
        openRequestKey={1}
      />,
    );

    await waitFor(() => expect(connection.openCalls).toBe(1));
    expect(screen.getByTestId("browser-viewport")).toBeTruthy();
  });

  it("distinguishes a ready empty browser from a recoverable connection error", async () => {
    const connection = new FakeConnection();
    connection.state = {
      ...connection.state,
      activeTabId: "",
      tabs: [],
      frame: null,
    };
    render(<BrowserViewport sessionId="session_a" connection={connection} />);

    expect(screen.getByText("Browser ready")).toBeTruthy();
    expect(screen.getByText(/page will appear when the agent opens it/i)).toBeTruthy();

    act(() => {
      connection.emit({
        status: "error",
        error: "Browser process stopped.",
      });
    });
    expect(screen.getByText("Browser view unavailable")).toBeTruthy();
    const retryButtons = screen.getAllByRole("button", { name: "Retry" });
    fireEvent.click(retryButtons[retryButtons.length - 1]!);
    await waitFor(() => expect(connection.refreshCalls).toBeGreaterThan(1));
  });

  it("keeps browser navigation and the address bar directly interactive", async () => {
    const connection = new FakeConnection();
    render(<BrowserViewport sessionId="session_a" connection={connection} />);

    expect(screen.getByTestId("browser-viewport")).toBeTruthy();
    expect(screen.getByText("OpenWorker browser fixture")).toBeTruthy();
    expect(screen.getByLabelText("Address")).toHaveProperty(
      "value",
      "https://example.test/dashboard",
    );
    expect(screen.getByRole("button", { name: "Go forward" })).toHaveProperty(
      "disabled",
      true,
    );
    expect(screen.queryByRole("button", { name: /control/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /browser settings/i })).toBeNull();

    const address = screen.getByLabelText("Address");
    fireEvent.change(address, { target: { value: "docs.example.test" } });
    fireEvent.submit(address.closest("form")!);
    expect(connection.navigations).toEqual(["https://docs.example.test"]);

    fireEvent.click(screen.getByRole("button", { name: "Go back" }));
    await waitFor(() => expect(connection.histories).toEqual(["back"]));
  });

  it("shows shared Chrome status without leaving isolated-browser controls active", async () => {
    const connection = new FakeConnection();
    connection.browserExtensionStatus = {
      ...connection.browserExtensionStatus,
      surfaces: connection.browserExtensionStatus.surfaces.map((surface) =>
        surface.surface === "chrome"
          ? { ...surface, connected: true, available: true, claimedTabs: 1 }
          : surface,
      ),
    };
    render(<BrowserViewport sessionId="session_a" connection={connection} />);

    const source = await screen.findByLabelText("Browser source for this task");
    fireEvent.change(source, { target: { value: "chrome" } });
    await waitFor(() =>
      expect(connection.selectedBrowserSurfaces).toEqual(["chrome"]),
    );
    expect(screen.getByText("Working in Chrome")).toBeTruthy();
    expect(screen.getByText(/1 shared tab is available/i)).toBeTruthy();
    expect(screen.queryByLabelText("Address")).toBeNull();
    expect(screen.queryByLabelText("Browser content")).toBeNull();
    expect(screen.queryByRole("button", { name: "Go back" })).toBeNull();
    expect(screen.queryByRole("button", { name: /control/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /browser settings/i })).toBeNull();

    fireEvent.change(source, { target: { value: "iab" } });
    await waitFor(() =>
      expect(connection.selectedBrowserSurfaces).toEqual(["chrome", "iab"]),
    );
    expect(screen.getByLabelText("Address")).toBeTruthy();
    expect(screen.getByLabelText("Browser content")).toBeTruthy();
  });

  it("keeps the task source selector available if selected Chrome disconnects", async () => {
    const connection = new FakeConnection();
    connection.browserExtensionStatus = {
      ...connection.browserExtensionStatus,
      selectedSurface: "chrome",
    };
    render(<BrowserViewport sessionId="session_a" connection={connection} />);

    expect(await screen.findByText("Chrome disconnected")).toBeTruthy();
    const source = screen.getByLabelText("Browser source for this task");
    expect(source).toHaveProperty("value", "chrome");
    fireEvent.change(source, { target: { value: "iab" } });
    await waitFor(() =>
      expect(connection.selectedBrowserSurfaces).toEqual(["iab"]),
    );
    expect(screen.getByLabelText("Address")).toBeTruthy();
  });

  it("recovers alert, confirm, and prompt dialogs inside the shared viewport", async () => {
    const connection = new FakeConnection();
    render(<BrowserViewport sessionId="session_a" connection={connection} />);

    act(() => {
      connection.emit({
        dialog: {
          tabId: "tab_1",
          dialogType: "prompt",
          message: "What should we call you?",
          defaultValue: "V",
        },
      });
    });

    expect(screen.getByText("What should we call you?")).toBeTruthy();
    const prompt = screen.getByRole("textbox", {
      name: "Page dialog response",
    });
    fireEvent.change(prompt, { target: { value: "Vignesh" } });
    fireEvent.click(screen.getByRole("button", { name: "Accept" }));

    await waitFor(() =>
      expect(connection.inputs).toContainEqual({
        type: "text",
        text: "accept:Vignesh",
      }),
    );
    expect(screen.queryByLabelText("Page dialog")).toBeNull();
  });

  it("maps shared pointer and wheel input through the exact frame content rect", async () => {
    const connection = new FakeConnection();
    render(<BrowserViewport sessionId="session_a" connection={connection} />);
    const stage = screen.getByLabelText("Browser content");
    setStageBounds(stage);

    fireEvent.pointerDown(stage, {
      clientX: 520,
      clientY: 280,
      button: 0,
      buttons: 1,
      pointerId: 4,
      pointerType: "mouse",
    });
    fireEvent.wheel(stage, {
      clientX: 520,
      clientY: 280,
      deltaX: 2,
      deltaY: 70,
    });

    const pointer = connection.inputs.find(
      (input): input is Extract<BrowserInput, { type: "pointer" }> =>
        input.type === "pointer" && input.phase === "down",
    );
    expect(pointer?.x).toBeCloseTo(640);
    expect(pointer?.y).toBeCloseTo(360);
    expect(connection.inputs).toContainEqual(
      expect.objectContaining({ type: "wheel", delta_x: 2, delta_y: 70 }),
    );
  });

  it("debounces responsive high-DPI viewport sync even while the agent has control", () => {
    vi.useFakeTimers();
    Object.defineProperty(window, "devicePixelRatio", {
      configurable: true,
      value: 2,
    });
    const connection = new FakeConnection();
    render(<BrowserViewport sessionId="session_a" connection={connection} />);
    const stage = screen.getByLabelText("Browser content");

    act(() => emitStageResize(stage, 900, 580));
    act(() => emitStageResize(stage, 944, 612));
    act(() => vi.advanceTimersByTime(119));
    expect(connection.inputs.filter((input) => input.type === "resize")).toEqual([]);

    act(() => vi.advanceTimersByTime(1));
    expect(connection.inputs.filter((input) => input.type === "resize")).toEqual([
      {
        type: "resize",
        width: 944,
        height: 612,
        dpr: 2,
      },
    ]);

    act(() => emitStageResize(stage, 944, 612));
    act(() => vi.advanceTimersByTime(121));
    expect(connection.inputs.filter((input) => input.type === "resize")).toHaveLength(1);
  });

  it("renders high-DPI frames in their fitted content rect without stretching", () => {
    const connection = new FakeConnection();
    connection.state = {
      ...connection.state,
      frame: {
        ...connection.state.frame!,
        pixelWidth: 2560,
        pixelHeight: 1440,
        dpr: 2,
      },
    };
    render(<BrowserViewport sessionId="session_a" connection={connection} />);
    const stage = screen.getByLabelText("Browser content");
    act(() => emitStageResize(stage, 1000, 500));

    const frame = document.querySelector(".browser-frame") as HTMLImageElement;
    expect(frame.getAttribute("width")).toBe("2560");
    expect(frame.getAttribute("height")).toBe("1440");
    expect(Number.parseFloat(frame.style.width)).toBeCloseTo(888.889, 2);
    expect(Number.parseFloat(frame.style.height)).toBeCloseTo(500, 2);
    expect(frame.style.width).not.toBe("100%");
    expect(frame.style.height).not.toBe("100%");
  });

  it("draws only frame-matched agent cursor actions and acknowledges arrival", async () => {
    vi.useFakeTimers();
    const connection = new FakeConnection();
    render(<BrowserViewport sessionId="session_a" connection={connection} />);
    const stage = screen.getByLabelText("Browser content");
    setStageBounds(stage);

    act(() => {
      connection.emit({
        visualAction: {
          actionId: "act_wrong",
          tabId: "tab_1",
          snapshotId: "snap_3",
          frameId: "frame_old",
          sequence: 18,
          phase: "move",
          kind: "click",
          target: { ref: "e4", x: 640, y: 360 },
          viewport: { width: 1280, height: 720, dpr: 1 },
        },
      });
    });
    expect(document.querySelector(".browser-ghost-cursor")).toBeNull();

    act(() => {
      connection.emit({
        visualAction: {
          actionId: "act_19",
          tabId: "tab_1",
          snapshotId: "snap_4",
          frameId: "frame_12",
          sequence: 19,
          phase: "move",
          kind: "click",
          target: { ref: "e5", x: 640, y: 360 },
          viewport: { width: 1280, height: 720, dpr: 1 },
        },
      });
    });
    const cursor = document.querySelector(".browser-ghost-cursor") as HTMLElement;
    expect(cursor.dataset.phase).toBe("move");
    expect(cursor.style.transform).toBe("translate3d(500px, 250px, 0)");
    expect(cursor.querySelector(".browser-cursor-activity")).toBeTruthy();
    expect(cursor.querySelector("svg")?.getAttribute("width")).toBe("18");
    expect(cursor.querySelector(".browser-click-ripple")).toBeNull();

    act(() => vi.advanceTimersByTime(241));
    expect(connection.acknowledgements).toEqual([
      { actionId: "act_19", frameId: "frame_12" },
    ]);

    act(() => {
      connection.emit({
        visualAction: {
          ...connection.state.visualAction!,
          sequence: 20,
          phase: "failed",
          error: "target moved",
        },
      });
    });
    expect(
      (document.querySelector(".browser-ghost-cursor") as HTMLElement).dataset.phase,
    ).toBe("failed");
    expect(document.querySelector(".browser-click-ripple")).toBeTruthy();
    act(() => vi.advanceTimersByTime(901));
    expect(document.querySelector(".browser-ghost-cursor")).toBeNull();
    vi.useRealTimers();
  });

  it("normalizes Blob frames to a disposable object URL", () => {
    const create = vi.fn(() => "blob:openworker-frame");
    const revoke = vi.fn();
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: create,
    });
    Object.defineProperty(URL, "revokeObjectURL", {
      configurable: true,
      value: revoke,
    });
    const connection = new FakeConnection();
    connection.state = {
      ...connection.state,
      frame: {
        ...connection.state.frame!,
        source: new Blob(["jpeg"], { type: "image/jpeg" }),
      },
    };
    const { unmount } = render(
      <BrowserViewport sessionId="session_a" connection={connection} />,
    );
    expect((document.querySelector(".browser-frame") as HTMLImageElement).src).toContain(
      "blob:openworker-frame",
    );
    unmount();
    expect(revoke).toHaveBeenCalledWith("blob:openworker-frame");
  });
});
