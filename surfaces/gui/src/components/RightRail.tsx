import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
// Emits the asset URL only; the worker itself loads lazily with the pdfjs chunk.
import pdfWorkerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import {
  getArtifacts,
  readArtifact,
  revealArtifact,
  type ArtifactContent,
  type ArtifactInfo,
} from "../api";
import type { TodoItem } from "../types";
import { AccessSection } from "./AccessSection";
import { BrowserViewport } from "./BrowserViewport";
import { Icon } from "./Icon";
import { Markdown, OPEN_ARTIFACT_EVENT } from "./Markdown";

type Panel = "progress" | "artifacts";

type WorkspaceTab =
  | { id: "overview"; kind: "overview"; title: "Overview" }
  | { id: "browser"; kind: "browser"; title: "Browser" }
  | { id: "files"; kind: "files"; title: "Files" }
  | { id: string; kind: "artifact"; title: string; artifact: ArtifactInfo };

const OVERVIEW_TAB: WorkspaceTab = {
  id: "overview",
  kind: "overview",
  title: "Overview",
};
const BROWSER_TAB: WorkspaceTab = {
  id: "browser",
  kind: "browser",
  title: "Browser",
};
const FILES_TAB: WorkspaceTab = { id: "files", kind: "files", title: "Files" };

function artifactTab(artifact: ArtifactInfo): WorkspaceTab {
  return {
    id: `artifact:${artifact.path}`,
    kind: "artifact",
    title: artifact.name,
    artifact,
  };
}

// Quiet file-type icons for the artifact list (the colored kind pills read as noisy).
function kindIcon(kind: string): "file" | "fileCode" | "image" | "table" {
  if (kind === "image") return "image";
  if (kind === "html" || kind === "code") return "fileCode";
  if (kind === "csv" || kind === "sheet") return "table";
  return "file"; // markdown, text, pdf, everything else
}

// Fallback kind for an artifact: link whose path isn't in the list (yet) — mirrors the
// server's extension mapping closely enough for the viewer to pick a renderer.
function kindFromPath(path: string): string {
  const ext = (path.split(".").pop() || "").toLowerCase();
  if (["png", "jpg", "jpeg", "gif", "svg", "webp"].includes(ext)) return "image";
  if (["html", "htm"].includes(ext)) return "html";
  if (ext === "md") return "markdown";
  if (ext === "csv") return "csv";
  if (ext === "pdf") return "pdf";
  if (["py", "js", "ts", "tsx", "jsx", "json", "sh", "css"].includes(ext)) return "code";
  return "text";
}

interface Props {
  active: boolean;
  sessionId: string;
  refreshKey: number;
  openBrowserKey?: number;
  browserActivityKey?: number;
  toolNames: string[];
  todo: TodoItem[];
  running: boolean;
  // Fires when a full artifact preview opens/closes, so the app can auto-collapse the left nav
  // to give the preview (PDF/webpage/sheet) more room (#3).
  onPreviewChange?: (open: boolean) => void;
  onBrowserOpenChange?: (open: boolean) => void;
  // §32: the rail is the ONE session panel for every non-chat persona. Artifacts stays
  // cowork-only (deliverables; code-family gets "Files" later — slot reserved); the Access
  // section (the former Session-settings drawer) renders for all.
  showArtifacts?: boolean;
  personaId?: string;
  projectScoped?: boolean;
  workspace?: string;
  branch?: string | null;
  scratchPrimary?: boolean;
  openAccessKey?: number;
  onOpenIntegrations?: () => void;
}

export function RightRail({
  active,
  sessionId,
  refreshKey,
  openBrowserKey = 0,
  browserActivityKey = 0,
  toolNames,
  todo,
  running,
  onPreviewChange,
  onBrowserOpenChange,
  showArtifacts = true,
  personaId,
  projectScoped,
  workspace,
  branch,
  scratchPrimary,
  openAccessKey = 0,
  onOpenIntegrations,
}: Props) {
  const [open, setOpen] = useState<Record<Panel, boolean>>({
    progress: true,
    artifacts: true,
  });
  const [artifacts, setArtifacts] = useState<ArtifactInfo[]>([]);
  const [tabs, setTabs] = useState<WorkspaceTab[]>([OVERVIEW_TAB, BROWSER_TAB]);
  const [activeTabId, setActiveTabId] = useState("overview");
  const [content, setContent] = useState<ArtifactContent | null>(null);
  const [browserOpen, setBrowserOpen] = useState(false);
  const [browserRequestKey, setBrowserRequestKey] = useState(0);
  const [browserCloseKey, setBrowserCloseKey] = useState(0);
  const [browserAttention, setBrowserAttention] = useState(false);
  const [addMenuOpen, setAddMenuOpen] = useState(false);
  const [addMenuView, setAddMenuView] = useState<"main" | "artifacts">("main");
  const addMenuRef = useRef<HTMLDivElement | null>(null);
  const activeTab = tabs.find((tab) => tab.id === activeTabId) ?? OVERVIEW_TAB;
  const selected = activeTab.kind === "artifact" ? activeTab.artifact : null;

  const ensureTab = useCallback((tab: WorkspaceTab) => {
    setTabs((current) =>
      current.some((candidate) => candidate.id === tab.id)
        ? current
        : [...current, tab],
    );
  }, []);

  const activateTab = useCallback((tabId: string) => {
    setActiveTabId(tabId);
    if (tabId === "browser") setBrowserAttention(false);
  }, []);

  const openArtifact = useCallback((artifact: ArtifactInfo) => {
    const tab = artifactTab(artifact);
    ensureTab(tab);
    activateTab(tab.id);
  }, [activateTab, ensureTab]);

  const openBrowserTab = useCallback(() => {
    ensureTab(BROWSER_TAB);
    activateTab(BROWSER_TAB.id);
    setBrowserRequestKey((key) => key + 1);
    setAddMenuOpen(false);
    setAddMenuView("main");
  }, [activateTab, ensureTab]);

  const openFilesTab = useCallback(() => {
    ensureTab(FILES_TAB);
    activateTab(FILES_TAB.id);
    setAddMenuOpen(false);
    setAddMenuView("main");
  }, [activateTab, ensureTab]);

  const closeTab = useCallback((tabId: string) => {
    if (tabId === "overview") return;
    setTabs((current) => {
      const index = current.findIndex((tab) => tab.id === tabId);
      const next = current.filter((tab) => tab.id !== tabId);
      setActiveTabId((active) => {
        if (active !== tabId) return active;
        return next[Math.max(0, Math.min(index - 1, next.length - 1))]?.id || "overview";
      });
      return next;
    });
    if (tabId === "browser") {
      setBrowserAttention(false);
      setBrowserCloseKey((key) => key + 1);
    }
  }, []);

  const handleBrowserOpenChange = useCallback((open: boolean) => {
    setBrowserOpen(open);
    onBrowserOpenChange?.(open);
    // Agent-created browser sessions become a background tab. They never steal the
    // artifact or Overview tab the human is currently reading.
    if (open) ensureTab(BROWSER_TAB);
  }, [ensureTab, onBrowserOpenChange]);

  const refreshArtifacts = useCallback(
    () => getArtifacts(sessionId).then(setArtifacts).catch(() => setArtifacts([])),
    [sessionId],
  );

  useEffect(() => {
    if (!active) return;
    if (showArtifacts) refreshArtifacts();
  }, [active, sessionId, refreshKey, showArtifacts]);

  // Tabs are deliberately conversation-scoped: never let a file path or live browser surface
  // bleed into another task when the user switches sessions.
  useEffect(() => {
    setTabs([OVERVIEW_TAB, BROWSER_TAB]);
    setActiveTabId("overview");
    setContent(null);
    setBrowserOpen(false);
    setBrowserAttention(false);
    setAddMenuOpen(false);
    setAddMenuView("main");
  }, [sessionId]);

  useEffect(() => {
    if (!openBrowserKey) return;
    openBrowserTab();
  }, [openBrowserKey, openBrowserTab]);

  // A browser tool may open or modify the shared browser while the human is looking at an
  // artifact. Materialize/update its tab, but leave activeTabId alone and show a quiet dot.
  const lastBrowserActivity = useRef(0);
  useEffect(() => {
    if (!browserActivityKey || browserActivityKey === lastBrowserActivity.current) return;
    lastBrowserActivity.current = browserActivityKey;
    ensureTab(BROWSER_TAB);
    if (activeTabId !== "browser") setBrowserAttention(true);
  }, [activeTabId, browserActivityKey, ensureTab]);

  useEffect(() => {
    setContent(null);
    if (!selected) return;
    readArtifact(sessionId, selected.path).then(setContent).catch(() => setContent(null));
  }, [selected?.path, sessionId]);

  // Notify the app when a preview opens/closes (drives the left-nav auto-collapse).
  useEffect(() => {
    onPreviewChange?.(activeTab.kind !== "overview");
  }, [activeTab.kind, onPreviewChange]);

  const reloadSelected = () => {
    if (!selected) return Promise.resolve();
    setContent(null);
    return readArtifact(sessionId, selected.path).then(setContent).catch(() => setContent(null));
  };

  // §34 (UX-016): [Title](artifact:path) chips in the transcript open the viewer directly.
  // Resolve against the loaded list first; on a miss, refresh once (the file may be
  // seconds old), then fall back to a minimal record — readArtifact validates the path.
  useEffect(() => {
    if (!active) return;
    const minimal = (path: string): ArtifactInfo => ({
      path,
      name: path.split("/").pop() || path,
      kind: kindFromPath(path),
      size: 0,
      modified_at: 0,
    });
    const match = (list: ArtifactInfo[], path: string) =>
      list.find((a) => a.path === path || a.path.endsWith("/" + path) || a.name === path);
    const onOpen = (e: Event) => {
      const path = String((e as CustomEvent).detail?.path || "");
      if (!path) return;
      const found = match(artifacts, path);
      if (found) {
        openArtifact(found);
        return;
      }
      getArtifacts(sessionId)
        .then((list) => {
          setArtifacts(list);
          openArtifact(match(list, path) ?? minimal(path));
        })
        .catch(() => openArtifact(minimal(path)));
    };
    window.addEventListener(OPEN_ARTIFACT_EVENT, onOpen);
    return () => window.removeEventListener(OPEN_ARTIFACT_EVENT, onOpen);
  }, [active, sessionId, artifacts, openArtifact]);

  useEffect(() => {
    if (!addMenuOpen) return;
    const dismiss = (event: PointerEvent) => {
      if (!addMenuRef.current?.contains(event.target as Node)) {
        setAddMenuOpen(false);
        setAddMenuView("main");
      }
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setAddMenuOpen(false);
        setAddMenuView("main");
      }
    };
    window.addEventListener("pointerdown", dismiss);
    window.addEventListener("keydown", escape);
    return () => {
      window.removeEventListener("pointerdown", dismiss);
      window.removeEventListener("keydown", escape);
    };
  }, [addMenuOpen]);

  if (!active) return null;

  return (
    <aside
      className={
        "right-rail" +
        (activeTab.kind === "browser"
          ? " browser-mode"
          : activeTab.kind === "artifact" || activeTab.kind === "files"
            ? " artifact-mode"
            : "")
      }
    >
      <div className="workspace-tab-strip">
        <div className="workspace-tabs" role="tablist" aria-label="Workspace tabs">
          {tabs.map((tab) => (
            <div
              key={tab.id}
              className={"workspace-tab-item" + (activeTab.id === tab.id ? " is-active" : "")}
            >
              <button
                type="button"
                role="tab"
                aria-selected={activeTab.id === tab.id}
                className="workspace-tab"
                title={tab.title}
                onClick={() => {
                  activateTab(tab.id);
                  if (tab.kind === "browser" && !browserOpen) {
                    setBrowserRequestKey((key) => key + 1);
                  }
                }}
              >
                <Icon
                  name={
                    tab.kind === "browser"
                      ? "globe"
                      : tab.kind === "overview"
                        ? "sliders"
                        : tab.kind === "files"
                          ? "folder"
                          : kindIcon(tab.artifact.kind)
                  }
                  size={13}
                />
                <span>{tab.title}</span>
                {tab.kind === "browser" && browserAttention && (
                  <span className="workspace-tab-attention" aria-label="Browser updated" />
                )}
              </button>
              {tab.kind !== "overview" && (
                <button
                  type="button"
                  className="workspace-tab-close"
                  aria-label={`Close ${tab.title}`}
                  onClick={(event) => {
                    event.stopPropagation();
                    closeTab(tab.id);
                  }}
                >
                  <Icon name="x" size={11} />
                </button>
              )}
            </div>
          ))}
        </div>
        <div className="workspace-add" ref={addMenuRef}>
            <button
              type="button"
              className={"workspace-add-button" + (addMenuOpen ? " is-active" : "")}
              aria-label="Add workspace tab"
              aria-expanded={addMenuOpen}
              onClick={() => {
                setAddMenuOpen((value) => !value);
                setAddMenuView("main");
              }}
            >
              <Icon name="plus" size={14} />
            </button>
            {addMenuOpen && (
              <div className="workspace-add-menu" role="menu">
                {addMenuView === "main" ? (
                  <>
                    <button type="button" role="menuitem" onClick={openBrowserTab}>
                      <Icon name="globe" size={15} />
                      <span>Browser</span>
                    </button>
                    <button
                      type="button"
                      role="menuitem"
                      onClick={() => setAddMenuView("artifacts")}
                    >
                      <Icon name="file" size={15} />
                      <span>Open artifact…</span>
                      <Icon name="chevronRight" size={13} />
                    </button>
                    <button type="button" role="menuitem" onClick={openFilesTab}>
                      <Icon name="folder" size={15} />
                      <span>Files</span>
                    </button>
                  </>
                ) : (
                  <>
                    <button
                      type="button"
                      role="menuitem"
                      className="workspace-add-menu-back"
                      onClick={() => setAddMenuView("main")}
                    >
                      <Icon name="arrowLeft" size={14} />
                      <span>Artifacts</span>
                    </button>
                    <div className="workspace-add-menu-list">
                      {artifacts.length ? artifacts.slice(0, 12).map((artifact) => (
                        <button
                          type="button"
                          role="menuitem"
                          key={artifact.path}
                          title={artifact.path}
                          onClick={() => {
                            openArtifact(artifact);
                            setAddMenuOpen(false);
                            setAddMenuView("main");
                          }}
                        >
                          <Icon name={kindIcon(artifact.kind)} size={14} />
                          <span>{artifact.name}</span>
                        </button>
                      )) : (
                        <div className="workspace-add-empty">No artifacts yet.</div>
                      )}
                    </div>
                  </>
                )}
              </div>
            )}
        </div>
      </div>

      <div className="workspace-tab-content">
        <div className={"workspace-browser-panel" + (activeTab.kind === "browser" ? "" : " is-background")}>
          <BrowserViewport
            sessionId={sessionId}
            refreshKey={refreshKey}
            openRequestKey={browserRequestKey}
            closeRequestKey={browserCloseKey}
            workspaceActive={activeTab.kind === "browser"}
            embedded
            onOpenChange={handleBrowserOpenChange}
          />
        </div>

        {activeTab.kind === "artifact" && selected ? (
          <ArtifactViewer
            sessionId={sessionId}
            artifact={selected}
          content={content}
          onReload={reloadSelected}
          onBack={() => activateTab("overview")}
          onOpenEntry={(path) =>
            openArtifact({
              path,
              name: path.split("/").pop() || path,
              kind: kindFromPath(path),
              size: 0,
              modified_at: 0,
            })
          }
        />
        ) : activeTab.kind === "files" ? (
          <FilesPanel
            artifacts={artifacts}
            onOpen={openArtifact}
            onRefresh={refreshArtifacts}
          />
        ) : activeTab.kind === "overview" ? (
          <div className="workspace-overview">
          <RailSection title="Progress" open={open.progress} onToggle={() => setOpen({ ...open, progress: !open.progress })}>
            <ProgressSummary running={running} toolNames={toolNames} todo={todo} />
          </RailSection>

          {showArtifacts && (
          <RailSection
            title={`Artifacts${artifacts.length ? ` (${artifacts.length})` : ""}`}
            open={open.artifacts}
            onToggle={() => setOpen({ ...open, artifacts: !open.artifacts })}
            action={
              <>
                {artifacts.length > 0 && (
                  <button
                    className="rail-mini-btn"
                    onClick={(e) => { e.stopPropagation(); revealArtifact(sessionId, artifacts[0].path, "reveal"); }}
                    title="Show the folder where these files are saved"
                  >
                    <Icon name="folder" size={13} />
                  </button>
                )}
                <button className="rail-mini-btn" onClick={(e) => { e.stopPropagation(); refreshArtifacts(); }} title="Refresh artifacts"><Icon name="refresh" size={13} /></button>
              </>
            }
          >
            {artifacts.length === 0 ? (
              <div className="rail-muted">No previewable files yet.</div>
            ) : (
              <div className="artifact-list">
                {artifacts.slice(0, 16).map((a) => (
                  <button className="artifact-row" key={a.path} onClick={() => openArtifact(a)}>
                    <span className="artifact-ico" title={a.kind}>
                      <Icon name={kindIcon(a.kind)} size={17} />
                    </span>
                    <span className="artifact-name">
                      {a.name}
                      <span className="artifact-row-meta">{formatBytes(a.size)} · {formatTime(a.modified_at)}</span>
                    </span>
                    <span className="artifact-open">Open</span>
                  </button>
                ))}
              </div>
            )}
          </RailSection>
          )}

          {/* §32: Access — the former Session-settings drawer, one section among peers.
              key: its data ownership resets with the conversation, like the old row did. */}
          <AccessSection
            key={sessionId}
            sessionId={sessionId}
            personaId={personaId}
            projectScoped={projectScoped}
            workspace={workspace}
            branch={branch}
            scratchPrimary={scratchPrimary}
            openKey={openAccessKey}
            onOpenIntegrations={onOpenIntegrations}
          />
          </div>
        ) : null}
      </div>
    </aside>
  );
}

function ProgressSummary({ running, toolNames, todo }: { running: boolean; toolNames: string[]; todo: TodoItem[] }) {
  if (todo.length) {
    return (
      <div className="rail-todo-list">
        {todo.map((item, index) => (
          <div className={"rail-todo " + item.status} key={index}>
            <span className="rail-todo-mark" />
            <span>{item.content}</span>
          </div>
        ))}
        {running && (
          <div className="rail-muted">
            {toolNames.length ? `${toolNames.length} tool call${toolNames.length === 1 ? "" : "s"} so far.` : "Working..."}
          </div>
        )}
      </div>
    );
  }
  if (running) {
    return (
      <div className="rail-muted">
        Working on this task{toolNames.length ? ` with ${toolNames.length} tool call${toolNames.length === 1 ? "" : "s"} so far.` : "."}
      </div>
    );
  }
  return (
    <div className="rail-muted">
      For longer multi-step tasks, progress will appear here while OpenWorker plans, uses tools, waits for approval, and produces artifacts.
    </div>
  );
}

function RailSection({
  title,
  open,
  onToggle,
  children,
  action,
}: {
  title: string;
  open: boolean;
  onToggle: () => void;
  children: ReactNode;
  action?: ReactNode;
}) {
  return (
    <section className="rail-section">
      <div className="rail-section-head">
        <button className="rail-section-toggle" onClick={onToggle}>
          <Icon name={open ? "chevronDown" : "chevronRight"} size={14} className="rail-chev" />
          <span>{title}</span>
        </button>
        {action}
      </div>
      {open && <div className="rail-section-body">{children}</div>}
    </section>
  );
}

function FilesPanel({
  artifacts,
  onOpen,
  onRefresh,
}: {
  artifacts: ArtifactInfo[];
  onOpen: (artifact: ArtifactInfo) => void;
  onRefresh: () => Promise<void>;
}) {
  const [query, setQuery] = useState("");
  const normalizedQuery = query.trim().toLowerCase();
  const filtered = artifacts.filter((artifact) =>
    `${artifact.name} ${artifact.path}`.toLowerCase().includes(normalizedQuery),
  );

  return (
    <div className="workspace-files">
      <div className="workspace-files-head">
        <div>
          <strong>Files</strong>
          <span>{artifacts.length} artifact{artifacts.length === 1 ? "" : "s"}</span>
        </div>
        <button
          type="button"
          className="artifact-icon-btn"
          onClick={() => void onRefresh()}
          aria-label="Refresh files"
          title="Refresh"
        >
          <Icon name="refresh" size={15} />
        </button>
      </div>
      <label className="workspace-file-search">
        <Icon name="search" size={15} />
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Filter files…"
          aria-label="Filter files"
        />
      </label>
      <div className="workspace-file-list">
        {filtered.length ? filtered.map((artifact) => (
          <button
            type="button"
            className="workspace-file-row"
            key={artifact.path}
            onClick={() => onOpen(artifact)}
          >
            <span className="workspace-file-icon">
              <Icon name={kindIcon(artifact.kind)} size={17} />
            </span>
            <span>
              <strong>{artifact.name}</strong>
              <small>{artifact.path}</small>
            </span>
            <small>{formatBytes(artifact.size)}</small>
          </button>
        )) : (
          <div className="workspace-files-empty">
            <Icon name="file" size={24} />
            <strong>{artifacts.length ? "No matching files" : "No artifacts yet"}</strong>
            <span>
              {artifacts.length
                ? "Try a different file name."
                : "Files created in this conversation will appear here."}
            </span>
          </div>
        )}
      </div>
    </div>
  );
}

function ArtifactViewer({
  sessionId,
  artifact,
  content,
  onReload,
  onBack,
  onOpenEntry,
}: {
  sessionId: string;
  artifact: ArtifactInfo;
  content: ArtifactContent | null;
  onReload: () => Promise<void>;
  onBack: () => void;
  // Folder listings: open a child entry in the viewer (files and subfolders alike).
  onOpenEntry?: (path: string) => void;
}) {
  const [reloadKey, setReloadKey] = useState(0);
  const isHtml = content?.kind === "html" && !content.error;
  // Best viewed in a real app: spreadsheets, PDFs, and Office docs (pptx/docx can't preview inline)
  const isApp = content?.kind === "sheet" || content?.kind === "pdf" || content?.kind === "office";

  return (
    <div className="artifact-viewer">
      <div className="artifact-head">
        <button className="artifact-icon-btn" onClick={onBack} aria-label="Back to artifacts" title="Back">
          <Icon name="arrowLeft" size={16} />
        </button>
        <div className="artifact-heading">
          <div className="artifact-title"><span>Artifacts</span><span className="artifact-sep">/</span><span>{artifact.name}</span></div>
          <div className="artifact-path">{artifact.path}</div>
        </div>
        <div className="rail-actions">
          {isHtml && (
            <button
              className="artifact-icon-btn"
              onClick={async () => {
                await onReload();
                setReloadKey((k) => k + 1);
              }}
              aria-label="Reload preview"
              title="Reload"
            >
              <Icon name="refresh" size={16} />
            </button>
          )}
          {isApp && (
            <button
              className="artifact-icon-btn"
              onClick={() => revealArtifact(sessionId, artifact.path, "open")}
              aria-label="Open in default app"
              title="Open in default app"
            >
              <Icon name="panelOpen" size={16} />
            </button>
          )}
          {/* Copy the ABSOLUTE path — the workspace-relative one is useless outside the app
              (tester catch 2026-07-12: it copied just "slack-connector-debug.md"). */}
          <button
            className="artifact-icon-btn"
            onClick={() => navigator.clipboard?.writeText(artifact.abs_path || artifact.path)}
            aria-label="Copy path"
            title="Copy full path"
          >
            <Icon name="copy" size={16} />
          </button>
          <button
            className="artifact-icon-btn"
            onClick={() => revealArtifact(sessionId, artifact.path, "reveal")}
            aria-label="Show in folder"
            title="Show in folder"
          >
            <Icon name="folder" size={16} />
          </button>
        </div>
      </div>
      <div className="artifact-preview">
        {!content ? (
          <div className="rail-muted">Loading...</div>
        ) : content.error ? (
          <div className="rail-error">{content.error}</div>
        ) : content.kind === "html" ? (
          <iframe
            key={`${artifact.path}-${reloadKey}`}
            sandbox="allow-scripts allow-same-origin"
            className="artifact-frame"
            srcDoc={content.content || ""}
          />
        ) : content.kind === "markdown" ? (
          <div className="artifact-md">
            <Markdown text={content.content || ""} />
          </div>
        ) : content.kind === "image" ? (
          <img className="artifact-image" src={content.data_url} />
        ) : content.kind === "pdf" ? (
          <PdfViewer dataUrl={content.data_url || ""} />
        ) : content.kind === "csv" ? (
          <CsvTable text={content.content || ""} />
        ) : content.kind === "sheet" ? (
          <SheetViewer dataUrl={content.data_url || ""} />
        ) : content.kind === "folder" ? (
          // A linked directory (e.g. a skill package): render the listing, click through.
          <div className="artifact-folderlist" data-testid="artifact-folder">
            {(content.entries || []).map((e) => (
              <button
                key={e.name}
                className="artifact-folder-row"
                onClick={() => onOpenEntry?.(`${artifact.path.replace(/\/+$/, "")}/${e.name}`)}
              >
                <Icon name={e.dir ? "folder" : "file"} size={14} />
                <span className="artifact-folder-name">{e.name}</span>
                {!e.dir && <span className="artifact-folder-size">{formatBytes(e.size)}</span>}
              </button>
            ))}
            {!content.entries?.length && <div className="rail-muted">This folder is empty.</div>}
          </div>
        ) : content.kind === "office" ? (
          <div className="artifact-open-prompt">
            <Icon name="panelOpen" size={28} />
            <p>This {/\.pptx?$/i.test(artifact.name) ? "PowerPoint" : "Word"} file can’t be previewed here.</p>
            <button className="btn sm" onClick={() => revealArtifact(sessionId, artifact.path, "open")}>
              Open in default app
            </button>
          </div>
        ) : (
          <pre className="artifact-code">{content.content}</pre>
        )}
      </div>
    </div>
  );
}

const MAX_TABLE_ROWS = 500;

function GridTable({ rows, note }: { rows: unknown[][]; note?: string }) {
  const [head, ...body] = rows;
  return (
    <div className="artifact-tablewrap">
      <table className="artifact-table">
        {head && (
          <thead>
            <tr>{head.map((c, i) => <th key={i}>{String(c ?? "")}</th>)}</tr>
          </thead>
        )}
        <tbody>
          {body.slice(0, MAX_TABLE_ROWS).map((r, i) => (
            <tr key={i}>{r.map((c, j) => <td key={j}>{String(c ?? "")}</td>)}</tr>
          ))}
        </tbody>
      </table>
      {(note || body.length > MAX_TABLE_ROWS) && (
        <div className="rail-muted artifact-table-note">
          {note}
          {body.length > MAX_TABLE_ROWS ? ` Showing first ${MAX_TABLE_ROWS} of ${body.length} rows.` : ""}
        </div>
      )}
    </div>
  );
}

// Minimal RFC-4180-ish CSV parsing: quoted fields, escaped quotes, CRLF. TSV via tab sniffing.
function parseCsv(text: string): string[][] {
  const delim = text.includes("\t") && !text.split("\n")[0]?.includes(",") ? "\t" : ",";
  const rows: string[][] = [];
  let row: string[] = [];
  let cell = "";
  let quoted = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (quoted) {
      if (ch === '"') {
        if (text[i + 1] === '"') {
          cell += '"';
          i++;
        } else quoted = false;
      } else cell += ch;
    } else if (ch === '"') quoted = true;
    else if (ch === delim) {
      row.push(cell);
      cell = "";
    } else if (ch === "\n" || ch === "\r") {
      if (ch === "\r" && text[i + 1] === "\n") i++;
      row.push(cell);
      cell = "";
      rows.push(row);
      row = [];
    } else cell += ch;
  }
  if (cell !== "" || row.length) {
    row.push(cell);
    rows.push(row);
  }
  return rows.filter((r) => r.some((c) => c !== ""));
}

function CsvTable({ text }: { text: string }) {
  const rows = parseCsv(text);
  if (!rows.length) return <div className="rail-muted artifact-table-note">Empty file.</div>;
  return <GridTable rows={rows} />;
}

// xlsx/xls preview via SheetJS (loaded on demand — it's a heavy module): sheet tabs + a capped
// grid. Real spreadsheet work belongs in Numbers/Excel via "Open in default app".
// WKWebView has no inline PDF plugin (<embed> shows a gray pane in the Tauri shell), so we
// rasterize pages with pdf.js onto stacked canvases — same lazy-chunk pattern as SheetViewer.
function PdfViewer({ dataUrl }: { dataUrl: string }) {
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const holder = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let cancelled = false;
    setError("");
    setLoading(true);
    const base64 = dataUrl.split(",")[1] || "";
    import("pdfjs-dist")
      .then(async (pdfjs) => {
        pdfjs.GlobalWorkerOptions.workerSrc = pdfWorkerUrl;
        const bytes = Uint8Array.from(atob(base64), (c) => c.charCodeAt(0));
        const doc = await pdfjs.getDocument({ data: bytes }).promise;
        const el = holder.current;
        if (cancelled || !el) return;
        el.innerHTML = "";
        const width = el.clientWidth || 640;
        const dpr = window.devicePixelRatio || 1;
        for (let i = 1; i <= doc.numPages; i++) {
          const page = await doc.getPage(i);
          const base = page.getViewport({ scale: 1 });
          const viewport = page.getViewport({ scale: (width / base.width) * dpr });
          const canvas = document.createElement("canvas");
          canvas.width = viewport.width;
          canvas.height = viewport.height;
          canvas.className = "artifact-pdf-page";
          await page.render({ canvasContext: canvas.getContext("2d")!, viewport }).promise;
          if (cancelled) return;
          el.appendChild(canvas);
        }
        setLoading(false);
      })
      .catch((e) => !cancelled && setError(String(e?.message || e)));
    return () => {
      cancelled = true;
    };
  }, [dataUrl]);

  if (error) return <div className="rail-error artifact-table-note">Could not render PDF: {error}</div>;
  return (
    <div className="artifact-pdfjs">
      {loading && <div className="rail-muted artifact-table-note">Rendering PDF…</div>}
      <div ref={holder} />
    </div>
  );
}

function SheetViewer({ dataUrl }: { dataUrl: string }) {
  const [sheets, setSheets] = useState<{ name: string; rows: unknown[][] }[] | null>(null);
  const [error, setError] = useState("");
  const [active, setActive] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setSheets(null);
    setError("");
    setActive(0);
    const base64 = dataUrl.split(",")[1] || "";
    import("xlsx")
      .then((XLSX) => {
        if (cancelled) return;
        const wb = XLSX.read(base64, { type: "base64" });
        setSheets(
          wb.SheetNames.map((name) => ({
            name,
            rows: XLSX.utils.sheet_to_json(wb.Sheets[name], { header: 1, defval: "" }) as unknown[][],
          })),
        );
      })
      .catch((e) => !cancelled && setError(String(e?.message || e)));
    return () => {
      cancelled = true;
    };
  }, [dataUrl]);

  if (error) return <div className="rail-error artifact-table-note">Could not parse spreadsheet: {error}</div>;
  if (!sheets) return <div className="rail-muted artifact-table-note">Parsing spreadsheet…</div>;
  const sheet = sheets[active];
  return (
    <div className="sheet-viewer">
      {sheets.length > 1 && (
        <div className="sheet-tabs">
          {sheets.map((s, i) => (
            <button key={s.name} className={"sheet-tab" + (i === active ? " active" : "")} onClick={() => setActive(i)}>
              {s.name}
            </button>
          ))}
        </div>
      )}
      {sheet.rows.length ? <GridTable rows={sheet.rows} /> : <div className="rail-muted artifact-table-note">Empty sheet.</div>}
    </div>
  );
}

function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes)) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatTime(epochSeconds: number): string {
  if (!epochSeconds) return "";
  return new Date(epochSeconds * 1000).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
}
