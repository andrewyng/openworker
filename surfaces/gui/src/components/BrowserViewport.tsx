import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
  type WheelEvent as ReactWheelEvent,
} from "react";
import {
  browserInputModifiers,
  createBrowserViewportConnection,
  type BrowserExtensionStatus,
  type BrowserFrame,
  type BrowserInput,
  type BrowserSurface,
  type BrowserViewportConnection,
  type BrowserViewportState,
  type BrowserVisualAction,
} from "../browser/BrowserViewportClient";
import { Icon } from "./Icon";

export interface ContentRect {
  x: number;
  y: number;
  width: number;
  height: number;
}

export function aspectFitRect(
  hostWidth: number,
  hostHeight: number,
  contentWidth: number,
  contentHeight: number,
): ContentRect {
  if (
    hostWidth <= 0 ||
    hostHeight <= 0 ||
    contentWidth <= 0 ||
    contentHeight <= 0
  ) {
    return { x: 0, y: 0, width: 0, height: 0 };
  }
  const scale = Math.min(hostWidth / contentWidth, hostHeight / contentHeight);
  const width = contentWidth * scale;
  const height = contentHeight * scale;
  return {
    x: (hostWidth - width) / 2,
    y: (hostHeight - height) / 2,
    width,
    height,
  };
}

export function browserPointFromClient(
  clientX: number,
  clientY: number,
  hostBounds: Pick<DOMRect, "left" | "top" | "width" | "height">,
  viewportWidth: number,
  viewportHeight: number,
  contentWidth = viewportWidth,
  contentHeight = viewportHeight,
): { x: number; y: number } | null {
  const fitted = aspectFitRect(
    hostBounds.width,
    hostBounds.height,
    contentWidth,
    contentHeight,
  );
  const localX = clientX - hostBounds.left - fitted.x;
  const localY = clientY - hostBounds.top - fitted.y;
  if (
    fitted.width <= 0 ||
    fitted.height <= 0 ||
    localX < 0 ||
    localY < 0 ||
    localX > fitted.width ||
    localY > fitted.height
  ) {
    return null;
  }
  return {
    x: Math.max(0, Math.min(viewportWidth, (localX / fitted.width) * viewportWidth)),
    y: Math.max(0, Math.min(viewportHeight, (localY / fitted.height) * viewportHeight)),
  };
}

export function panelPointFromBrowser(
  x: number,
  y: number,
  hostWidth: number,
  hostHeight: number,
  viewportWidth: number,
  viewportHeight: number,
  contentWidth = viewportWidth,
  contentHeight = viewportHeight,
): { x: number; y: number } {
  const fitted = aspectFitRect(hostWidth, hostHeight, contentWidth, contentHeight);
  return {
    x: fitted.x + (x / Math.max(1, viewportWidth)) * fitted.width,
    y: fitted.y + (y / Math.max(1, viewportHeight)) * fitted.height,
  };
}

interface Props {
  sessionId: string;
  refreshKey?: number;
  openRequestKey?: number;
  closeRequestKey?: number;
  workspaceActive?: boolean;
  embedded?: boolean;
  connection?: BrowserViewportConnection;
  onOpenChange?: (open: boolean) => void;
}

interface CursorState {
  actionId: string;
  frameId?: string;
  tabId: string;
  x: number;
  y: number;
  phase: BrowserVisualAction["phase"];
  sequence: number;
  visible: boolean;
}

const INITIAL_STATE: BrowserViewportState = {
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
};

const VIEWPORT_RESIZE_DEBOUNCE_MS = 120;
const MIN_BROWSER_VIEWPORT_WIDTH = 320;
const MIN_BROWSER_VIEWPORT_HEIGHT = 240;
const MAX_BROWSER_DEVICE_PIXEL_RATIO = 2;

function browserDevicePixelRatio(): number {
  const value =
    typeof window === "undefined" ? 1 : Number(window.devicePixelRatio) || 1;
  return Math.min(MAX_BROWSER_DEVICE_PIXEL_RATIO, Math.max(1, value));
}

function useBrowserFrameUrl(frame: BrowserFrame | null): string {
  const [url, setUrl] = useState("");
  useEffect(() => {
    if (!frame) {
      setUrl("");
      return;
    }
    if (typeof frame.source === "string") {
      setUrl(frame.source);
      return;
    }
    const objectUrl = URL.createObjectURL(frame.source);
    setUrl(objectUrl);
    return () => URL.revokeObjectURL(objectUrl);
  }, [frame]);
  return url;
}

function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(() =>
    typeof matchMedia === "function"
      ? matchMedia("(prefers-reduced-motion: reduce)").matches
      : false,
  );
  useEffect(() => {
    if (typeof matchMedia !== "function") return;
    const query = matchMedia("(prefers-reduced-motion: reduce)");
    const update = () => setReduced(query.matches);
    query.addEventListener?.("change", update);
    return () => query.removeEventListener?.("change", update);
  }, []);
  return reduced;
}

export function BrowserViewport({
  sessionId,
  refreshKey = 0,
  openRequestKey = 0,
  closeRequestKey = 0,
  workspaceActive = true,
  embedded = false,
  connection: suppliedConnection,
  onOpenChange,
}: Props) {
  const ownedConnection = useMemo(
    () => (suppliedConnection ? null : createBrowserViewportConnection(sessionId)),
    [sessionId, suppliedConnection],
  );
  const connection = suppliedConnection || ownedConnection!;
  const [state, setState] = useState<BrowserViewportState>(
    () => connection?.getState() || INITIAL_STATE,
  );
  const [address, setAddress] = useState("");
  const [commandError, setCommandError] = useState("");
  const [closing, setClosing] = useState(false);
  const [dialogBusy, setDialogBusy] = useState(false);
  const [dialogPrompt, setDialogPrompt] = useState("");
  const [extensionStatus, setExtensionStatus] =
    useState<BrowserExtensionStatus | null>(null);
  const [sourceBusy, setSourceBusy] = useState(false);
  const [hostSize, setHostSize] = useState({ width: 0, height: 0, dpr: 1 });
  const [cursor, setCursor] = useState<CursorState | null>(null);
  const stageRef = useRef<HTMLDivElement | null>(null);
  const resizeTimerRef = useRef<number | null>(null);
  const lastResizeSignatureRef = useRef("");
  const cursorTimerRef = useRef<number | null>(null);
  const cursorAckTimerRef = useRef<number | null>(null);
  const pointerFrameRef = useRef<number | null>(null);
  const queuedPointerRef = useRef<BrowserInput | null>(null);
  const lastVisualSequence = useRef(-1);
  const lastFrameSequence = useRef(-1);
  const lastOpenRequest = useRef(0);
  const lastCloseRequest = useRef(0);
  const reducedMotion = useReducedMotion();
  const frameUrl = useBrowserFrameUrl(state.frame);
  const chromeSurface = extensionStatus?.surfaces.find(
    (surface) => surface.surface === "chrome",
  );
  const selectedSource = extensionStatus?.selectedSurface || "iab";

  useEffect(() => {
    const unsubscribe = connection.subscribe(setState);
    connection.start();
    return () => {
      unsubscribe();
      ownedConnection?.destroy();
    };
  }, [connection, ownedConnection]);

  useEffect(() => {
    void connection.refresh();
  }, [connection, refreshKey]);

  useEffect(() => {
    if (!openRequestKey || lastOpenRequest.current === openRequestKey) return;
    lastOpenRequest.current = openRequestKey;
    setCommandError("");
    void connection.open().catch((openError) => {
      setCommandError(
        openError instanceof Error
          ? openError.message
          : "Could not open the browser.",
      );
    });
  }, [connection, openRequestKey]);

  useEffect(() => {
    if (!closeRequestKey || lastCloseRequest.current === closeRequestKey) return;
    lastCloseRequest.current = closeRequestKey;
    setClosing(true);
    setCommandError("");
    void connection
      .close()
      .catch((closeError) => {
        setCommandError(
          closeError instanceof Error
            ? closeError.message
            : "Could not close the browser.",
        );
      })
      .finally(() => setClosing(false));
  }, [closeRequestKey, connection]);

  useEffect(() => {
    onOpenChange?.(state.open && state.visible);
  }, [onOpenChange, state.open, state.visible]);

  const activeTab =
    state.tabs.find((tab) => tab.tabId === state.activeTabId) ||
    state.tabs.find((tab) => tab.active) ||
    state.tabs[0];

  useEffect(() => {
    setAddress(activeTab?.url || "");
  }, [activeTab?.tabId, activeTab?.url]);

  useEffect(() => {
    lastFrameSequence.current = -1;
    lastVisualSequence.current = -1;
    setCursor(null);
  }, [state.activeTabId]);

  useEffect(() => {
    setDialogPrompt(state.dialog?.defaultValue || "");
  }, [state.dialog?.defaultValue, state.dialog?.tabId]);

  useEffect(() => {
    let active = true;
    connection
      .getBrowserExtensionStatus()
      .then((status) => {
        if (active) setExtensionStatus(status);
      })
      .catch(() => {
        // Chrome is optional. The built-in browser remains usable when this fails.
      });
    return () => {
      active = false;
    };
  }, [connection, refreshKey]);

  useLayoutEffect(() => {
    if (!workspaceActive) return;
    const stage = stageRef.current;
    if (!stage) return;
    const update = (entry?: ResizeObserverEntry) => {
      const bounds = stage.getBoundingClientRect();
      const width = entry?.contentRect.width ?? bounds.width;
      const height = entry?.contentRect.height ?? bounds.height;
      const dpr = browserDevicePixelRatio();
      setHostSize((current) =>
        Math.abs(current.width - width) < 0.5 &&
        Math.abs(current.height - height) < 0.5 &&
        current.dpr === dpr
          ? current
          : { width, height, dpr },
      );
    };
    update();
    const observer =
      typeof ResizeObserver === "function"
        ? new ResizeObserver((entries) => update(entries[0]))
        : null;
    observer?.observe(stage);
    const updateFromWindow = () => update();
    window.addEventListener("resize", updateFromWindow);
    window.visualViewport?.addEventListener("resize", updateFromWindow);
    return () => {
      observer?.disconnect();
      window.removeEventListener("resize", updateFromWindow);
      window.visualViewport?.removeEventListener("resize", updateFromWindow);
    };
  }, [selectedSource, state.open, workspaceActive]);

  useEffect(() => {
    lastResizeSignatureRef.current = "";
  }, [connection]);

  useEffect(() => {
    if (
      selectedSource !== "iab" ||
      !workspaceActive ||
      !state.open ||
      !hostSize.width ||
      !hostSize.height
    ) {
      return;
    }
    const input: BrowserInput = {
      type: "resize",
      width: Math.max(MIN_BROWSER_VIEWPORT_WIDTH, Math.round(hostSize.width)),
      height: Math.max(MIN_BROWSER_VIEWPORT_HEIGHT, Math.round(hostSize.height)),
      dpr: hostSize.dpr,
    };
    const signature = `${input.width}x${input.height}@${input.dpr}`;
    if (signature === lastResizeSignatureRef.current) return;
    if (resizeTimerRef.current !== null) {
      window.clearTimeout(resizeTimerRef.current);
    }
    resizeTimerRef.current = window.setTimeout(() => {
      resizeTimerRef.current = null;
      lastResizeSignatureRef.current = signature;
      connection.sendInput(input);
    }, VIEWPORT_RESIZE_DEBOUNCE_MS);
    return () => {
      if (resizeTimerRef.current !== null) {
        window.clearTimeout(resizeTimerRef.current);
        resizeTimerRef.current = null;
      }
    };
  }, [
    connection,
    hostSize.dpr,
    hostSize.height,
    hostSize.width,
    selectedSource,
    state.open,
    workspaceActive,
  ]);

  useEffect(() => {
    if (selectedSource !== "iab") {
      setCursor(null);
      return;
    }
    const visual = state.visualAction;
    const frame = state.frame;
    if (!visual || !frame) return;
    if (visual.sequence < lastVisualSequence.current) return;
    if (frame.sequence < lastFrameSequence.current) return;
    lastFrameSequence.current = frame.sequence;
    if (visual.tabId && visual.tabId !== state.activeTabId) return;
    if (visual.frameId && visual.frameId !== frame.frameId) {
      setCursor((current) =>
        current?.frameId && current.frameId !== frame.frameId ? null : current,
      );
      return;
    }
    lastVisualSequence.current = visual.sequence;
    if (visual.phase === "cancelled") {
      setCursor(null);
      return;
    }
    const point = panelPointFromBrowser(
      visual.target.x,
      visual.target.y,
      hostSize.width,
      hostSize.height,
      visual.viewport.width,
      visual.viewport.height,
      frame.pixelWidth,
      frame.pixelHeight,
    );
    setCursor({
      actionId: visual.actionId,
      frameId: visual.frameId,
      tabId: visual.tabId,
      x: point.x,
      y: point.y,
      phase: visual.phase,
      sequence: visual.sequence,
      visible: true,
    });

    if (cursorTimerRef.current !== null) {
      window.clearTimeout(cursorTimerRef.current);
    }
    if (visual.phase === "completed" || visual.phase === "failed") {
      cursorTimerRef.current = window.setTimeout(
        () =>
          setCursor((current) =>
            current?.actionId === visual.actionId ? null : current,
          ),
        visual.phase === "failed" ? 900 : 620,
      );
    }

    if (visual.phase === "move") {
      if (cursorAckTimerRef.current !== null) {
        window.clearTimeout(cursorAckTimerRef.current);
      }
      cursorAckTimerRef.current = window.setTimeout(
        () => connection.acknowledgeCursor(visual.actionId, visual.frameId),
        reducedMotion ? 0 : 240,
      );
    }
  }, [
    connection,
    hostSize.height,
    hostSize.width,
    reducedMotion,
    selectedSource,
    state.activeTabId,
    state.frame,
    state.visualAction,
  ]);

  useEffect(
    () => () => {
      if (cursorTimerRef.current !== null) {
        window.clearTimeout(cursorTimerRef.current);
      }
      if (cursorAckTimerRef.current !== null) {
        window.clearTimeout(cursorAckTimerRef.current);
      }
      if (pointerFrameRef.current !== null) {
        window.cancelAnimationFrame(pointerFrameRef.current);
      }
      if (resizeTimerRef.current !== null) {
        window.clearTimeout(resizeTimerRef.current);
      }
    },
    [],
  );

  const viewport = state.frame
    ? {
        width: state.frame.viewportWidth,
        height: state.frame.viewportHeight,
      }
    : state.visualAction?.viewport || { width: 1280, height: 900 };

  const pointFor = useCallback(
    (clientX: number, clientY: number) => {
      const stage = stageRef.current;
      if (!stage) return null;
      return browserPointFromClient(
        clientX,
        clientY,
        stage.getBoundingClientRect(),
        viewport.width,
        viewport.height,
        state.frame?.pixelWidth || viewport.width,
        state.frame?.pixelHeight || viewport.height,
      );
    },
    [
      state.frame?.pixelHeight,
      state.frame?.pixelWidth,
      viewport.height,
      viewport.width,
    ],
  );

  const sendQueuedPointer = useCallback(() => {
    pointerFrameRef.current = null;
    const input = queuedPointerRef.current;
    queuedPointerRef.current = null;
    if (input) connection.sendInput(input);
  }, [connection]);

  const pointerInput = (
    event: ReactPointerEvent<HTMLDivElement>,
    phase: "move" | "down" | "up",
  ) => {
    const point = pointFor(event.clientX, event.clientY);
    if (!point) return;
    event.preventDefault();
    event.stopPropagation();
    if (phase === "down") {
      event.currentTarget.focus({ preventScroll: true });
      event.currentTarget.setPointerCapture?.(event.pointerId);
    } else if (
      phase === "up" &&
      event.currentTarget.hasPointerCapture?.(event.pointerId)
    ) {
      event.currentTarget.releasePointerCapture?.(event.pointerId);
    }
    const input: BrowserInput = {
      type: "pointer",
      phase,
      x: point.x,
      y: point.y,
      button: event.button,
      buttons: event.buttons,
      click_count: event.detail || 1,
      pointer_type: event.pointerType || "mouse",
      modifiers: browserInputModifiers(event),
    };
    if (phase !== "move") {
      connection.sendInput(input);
      return;
    }
    queuedPointerRef.current = input;
    if (pointerFrameRef.current === null) {
      pointerFrameRef.current = window.requestAnimationFrame(sendQueuedPointer);
    }
  };

  const wheelInput = (event: ReactWheelEvent<HTMLDivElement>) => {
    const point = pointFor(event.clientX, event.clientY);
    if (!point) return;
    event.preventDefault();
    event.stopPropagation();
    connection.sendInput({
      type: "wheel",
      x: point.x,
      y: point.y,
      delta_x: event.deltaX,
      delta_y: event.deltaY,
      modifiers: browserInputModifiers(event),
    });
  };

  const keyInput = (
    event: ReactKeyboardEvent<HTMLDivElement>,
    phase: "down" | "up",
  ) => {
    event.preventDefault();
    event.stopPropagation();
    connection.sendInput({
      type: "key",
      phase,
      key: event.key,
      code: event.code,
      repeat: event.repeat,
      modifiers: browserInputModifiers(event),
    });
  };

  const navigate = (event: FormEvent) => {
    event.preventDefault();
    let url = address.trim();
    if (!url) return;
    if (!/^[a-z][a-z0-9+.-]*:/i.test(url)) url = `https://${url}`;
    setAddress(url);
    connection.navigate(url);
  };

  const runHistory = async (action: "back" | "forward" | "reload") => {
    setCommandError("");
    try {
      await connection.history(action);
    } catch (historyError) {
      setCommandError(
        historyError instanceof Error
          ? historyError.message
          : "Browser navigation failed.",
      );
    }
  };

  const close = async () => {
    setClosing(true);
    setCommandError("");
    try {
      await connection.close();
    } catch (closeError) {
      setCommandError(
        closeError instanceof Error
          ? closeError.message
          : "Could not close the browser.",
      );
    } finally {
      setClosing(false);
    }
  };

  const resolveDialog = async (action: "accept" | "dismiss") => {
    setDialogBusy(true);
    setCommandError("");
    try {
      await connection.resolveDialog(
        action,
        action === "accept" && state.dialog?.dialogType === "prompt"
          ? dialogPrompt
          : undefined,
      );
    } catch (dialogError) {
      setCommandError(
        dialogError instanceof Error
          ? dialogError.message
          : "Could not resolve the page dialog.",
      );
    } finally {
      setDialogBusy(false);
    }
  };

  const chooseSource = async (surface: BrowserSurface) => {
    const selectedSource = extensionStatus?.selectedSurface || "iab";
    if (surface === selectedSource) return;
    setSourceBusy(true);
    setCommandError("");
    try {
      const selected = await connection.selectBrowserSurface(surface);
      if (!selected.available) {
        throw new Error(
          surface === "chrome"
            ? "Chrome is not connected."
            : "OpenWorker Browser is unavailable.",
        );
      }
      setExtensionStatus((current) =>
        current ? { ...current, selectedSurface: selected.surface } : current,
      );
    } catch (sourceError) {
      setCommandError(
        sourceError instanceof Error
          ? sourceError.message
          : "Could not change the browser for this task.",
      );
    } finally {
      setSourceBusy(false);
    }
  };

  if (!state.open) return null;

  const loading =
    selectedSource === "iab" &&
    (state.status === "loading" || activeTab?.loading);
  const error = commandError || (selectedSource === "iab" ? state.error : "");
  const tabCount = state.tabs.length;
  const frameRect = state.frame
    ? aspectFitRect(
        hostSize.width,
        hostSize.height,
        state.frame.pixelWidth,
        state.frame.pixelHeight,
      )
    : null;
  return (
    <section className="browser-viewport" aria-label="Browser" data-testid="browser-viewport">
      {(!embedded || chromeSurface?.connected || selectedSource === "chrome") && (
      <header className={"browser-view-head" + (embedded ? " is-embedded" : "")}>
        {!embedded && (
          <div
            className="browser-tab-title"
            title={
              selectedSource === "chrome"
                ? "Shared Chrome tab"
                : activeTab?.title || "Browser"
            }
          >
            <BrowserGlobe />
            <span>
              {selectedSource === "chrome"
                ? "Shared Chrome tab"
                : activeTab?.title || "Browser"}
            </span>
            {selectedSource === "iab" && tabCount > 1 && (
              <span className="browser-tab-count">+{tabCount - 1}</span>
            )}
          </div>
        )}
        {(chromeSurface?.connected || selectedSource === "chrome") && (
          <label className="browser-source-picker">
            <span className="sr-only">Browser source for this task</span>
            <select
              aria-label="Browser source for this task"
              value={selectedSource}
              disabled={sourceBusy}
              onChange={(event) =>
                void chooseSource(event.target.value as BrowserSurface)
              }
            >
              <option value="iab">OpenWorker</option>
              <option value="chrome">Chrome</option>
            </select>
          </label>
        )}
        {!embedded && <button
          className="browser-icon-btn"
          type="button"
          onClick={close}
          disabled={closing}
          aria-label="Close browser"
          title="Close browser"
        >
          <Icon name="x" size={15} />
        </button>}
      </header>
      )}

      {selectedSource === "iab" && (
        <div className="browser-toolbar" aria-label="Browser toolbar">
          <div className="browser-nav-buttons">
            <button
              className="browser-icon-btn"
              type="button"
              aria-label="Go back"
              title="Back"
              disabled={!activeTab?.canGoBack}
              onClick={() => void runHistory("back")}
            >
              <Icon name="arrowLeft" size={15} />
            </button>
            <button
              className="browser-icon-btn browser-forward"
              type="button"
              aria-label="Go forward"
              title="Forward"
              disabled={!activeTab?.canGoForward}
              onClick={() => void runHistory("forward")}
            >
              <Icon name="arrowLeft" size={15} />
            </button>
            <button
              className="browser-icon-btn"
              type="button"
              aria-label="Reload page"
              title="Reload"
              onClick={() => void runHistory("reload")}
            >
              <Icon name="refresh" size={15} />
            </button>
          </div>

          <form className="browser-address" onSubmit={navigate}>
            <BrowserLock secure={/^https:/i.test(address)} />
            <input
              aria-label="Address"
              value={address}
              spellCheck={false}
              autoCapitalize="none"
              onChange={(event) => setAddress(event.target.value)}
              onFocus={(event) => event.currentTarget.select()}
            />
          </form>
        </div>
      )}

      {loading && <div className="browser-load-bar" aria-label="Page loading" />}
      {error && (
        <div className="browser-inline-error" role="status">
          <span>{error}</span>
          <button type="button" onClick={() => void connection.refresh()}>
            Retry
          </button>
        </div>
      )}

      {selectedSource === "chrome" ? (
        <ChromeSharedTabState surface={chromeSurface} />
      ) : (
        <div
          ref={stageRef}
          className="browser-frame-stage"
          tabIndex={0}
          aria-label="Browser content"
          onPointerMove={(event) => pointerInput(event, "move")}
          onPointerDown={(event) => pointerInput(event, "down")}
          onPointerUp={(event) => pointerInput(event, "up")}
          onPointerCancel={(event) => pointerInput(event, "up")}
          onWheel={wheelInput}
          onKeyDown={(event) => keyInput(event, "down")}
          onKeyUp={(event) => keyInput(event, "up")}
          onPaste={(event) => {
            event.preventDefault();
            event.stopPropagation();
            const pasted = event.clipboardData.getData("text/plain");
            if (pasted) connection.sendInput({ type: "text", text: pasted });
          }}
          onContextMenu={(event) => event.preventDefault()}
        >
          {frameUrl ? (
            <img
              className="browser-frame"
              src={frameUrl}
              alt=""
              width={state.frame?.pixelWidth}
              height={state.frame?.pixelHeight}
              style={
                frameRect?.width && frameRect.height
                  ? {
                      width: `${frameRect.width}px`,
                      height: `${frameRect.height}px`,
                    }
                  : undefined
              }
              draggable={false}
              data-frame-id={state.frame?.frameId}
            />
          ) : state.status === "error" ? (
            <BrowserErrorState onRetry={() => void connection.refresh()} />
          ) : state.status === "ready" && !activeTab ? (
            <BrowserEmptyState />
          ) : (
            <BrowserLoadingState />
          )}

          {cursor?.visible && (
            <GhostCursor cursor={cursor} reducedMotion={reducedMotion} />
          )}

          {state.dialog && (
            <form
              className="browser-dialog-card"
              aria-label="Page dialog"
              onSubmit={(event) => {
                event.preventDefault();
                void resolveDialog("accept");
              }}
              onPointerDown={(event) => event.stopPropagation()}
              onPointerMove={(event) => event.stopPropagation()}
              onPointerUp={(event) => event.stopPropagation()}
              onKeyDown={(event) => event.stopPropagation()}
              onKeyUp={(event) => event.stopPropagation()}
            >
              <span className="browser-dialog-eyebrow">This page says</span>
              <strong>{state.dialog.message || "The page needs your response."}</strong>
              {state.dialog.dialogType === "prompt" && (
                <input
                  aria-label="Page dialog response"
                  value={dialogPrompt}
                  disabled={dialogBusy}
                  autoFocus
                  onChange={(event) => setDialogPrompt(event.target.value)}
                />
              )}
              <div className="browser-dialog-actions">
                <button
                  type="button"
                  disabled={dialogBusy}
                  onClick={() => void resolveDialog("dismiss")}
                >
                  Dismiss
                </button>
                <button className="primary" type="submit" disabled={dialogBusy}>
                  {state.dialog.dialogType === "alert" ? "OK" : "Accept"}
                </button>
              </div>
            </form>
          )}
        </div>
      )}
    </section>
  );
}

function ChromeSharedTabState({
  surface,
}: {
  surface: BrowserExtensionStatus["surfaces"][number] | undefined;
}) {
  const sharedTabs = surface?.claimedTabs || 0;
  const connected = Boolean(surface?.connected);
  return (
    <div
      className="browser-chrome-shared-state"
      role="status"
      aria-label="Chrome shared tab status"
    >
      <div className="browser-chrome-mark" aria-hidden="true">
        <BrowserGlobe />
      </div>
      <strong>{connected ? "Working in Chrome" : "Chrome disconnected"}</strong>
      <span>
        {!connected
          ? "Reconnect the OpenWorker extension to continue with this task."
          : sharedTabs > 0
            ? `${sharedTabs} shared ${sharedTabs === 1 ? "tab is" : "tabs are"} available. You can keep using Chrome while your coworker works.`
            : "No tabs are shared. Share a tab from the OpenWorker extension in Chrome."}
      </span>
      <div className="browser-chrome-status-line">
        <i className={connected ? "connected" : ""} aria-hidden="true" />
        {connected
          ? `${sharedTabs} ${sharedTabs === 1 ? "tab" : "tabs"} shared`
          : "Extension offline"}
      </div>
    </div>
  );
}

function GhostCursor({
  cursor,
  reducedMotion,
}: {
  cursor: CursorState;
  reducedMotion: boolean;
}) {
  const showRipple = ["down", "up", "completed", "failed"].includes(cursor.phase);
  return (
    <div
      className={`browser-ghost-cursor ${cursor.phase}${reducedMotion ? " reduced" : ""}`}
      data-phase={cursor.phase}
      data-action-id={cursor.actionId}
      style={{ transform: `translate3d(${cursor.x}px, ${cursor.y}px, 0)` }}
      aria-hidden="true"
    >
      <span className="browser-cursor-activity" />
      {showRipple && <span className="browser-click-ripple" key={cursor.phase} />}
      <svg width="18" height="22" viewBox="0 0 18 22" fill="none">
        <path
          d="M2.2 1.7 2.5 17.4l4-3.4 3.4 6.4 3-1.6-3.4-6.3 5.7-.7-13-10.1Z"
          fill="currentColor"
          stroke="white"
          strokeWidth="1.5"
          strokeLinejoin="round"
        />
      </svg>
    </div>
  );
}

function BrowserLoadingState() {
  return (
    <div className="browser-frame-empty" aria-label="Loading browser">
      <span className="browser-skeleton browser-skeleton-address" />
      <span className="browser-skeleton browser-skeleton-title" />
      <span className="browser-skeleton browser-skeleton-line" />
      <span className="browser-skeleton browser-skeleton-line short" />
    </div>
  );
}

function BrowserEmptyState() {
  return (
    <div className="browser-frame-error browser-frame-ready" role="status">
      <BrowserGlobe />
      <strong>Browser ready</strong>
      <span>The page will appear when the agent opens it.</span>
    </div>
  );
}

function BrowserErrorState({ onRetry }: { onRetry: () => void }) {
  return (
    <div className="browser-frame-error" role="status">
      <BrowserDisconnectedIcon />
      <strong>Browser view unavailable</strong>
      <span>The live view will reconnect automatically.</span>
      <button type="button" onClick={onRetry}>
        Reconnect
      </button>
    </div>
  );
}

function BrowserGlobe() {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      aria-hidden="true"
    >
      <circle cx="12" cy="12" r="8.5" />
      <path d="M3.8 12h16.4M12 3.5c2.3 2.4 3.5 5.2 3.5 8.5S14.3 18.1 12 20.5C9.7 18.1 8.5 15.3 8.5 12S9.7 5.9 12 3.5Z" />
    </svg>
  );
}

function BrowserLock({ secure }: { secure: boolean }) {
  return (
    <svg
      width="13"
      height="13"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      aria-hidden="true"
    >
      {secure ? (
        <>
          <rect x="5.5" y="10" width="13" height="10" rx="2.3" />
          <path d="M8.5 10V7.5a3.5 3.5 0 0 1 7 0V10" />
        </>
      ) : (
        <>
          <circle cx="12" cy="12" r="8.5" />
          <path d="M12 8.2v4.3M12 16h.01" />
        </>
      )}
    </svg>
  );
}

function BrowserDisconnectedIcon() {
  return (
    <svg
      width="28"
      height="28"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      aria-hidden="true"
    >
      <rect x="3.5" y="5" width="17" height="12.5" rx="3" />
      <path d="m7 21 3-3.5h4L17 21M8.5 11.2h7" />
    </svg>
  );
}
