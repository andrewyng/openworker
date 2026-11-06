import React, { useEffect, useState, useRef } from "react";
import { GameFrame } from "./GameFrame";
import { TestingPanel } from "./TestingPanel";
import type {
  AppSnapshot,
  ProjectView,
  Settings,
  ProviderSettings,
  Brief,
  StarterGame,
  ReviewView,
} from "../shared/contracts";
import { bridge } from "./bridge";
import { Icon } from "./icons";
import { LibraryScreen } from "./LibraryScreen";
import { ArcadeHome, StarterPlayer } from "./ArcadeHome";
const presets = [
  {
    id: "openai",
    name: "OpenAI",
    api: "responses",
    baseUrl: "https://api.openai.com/v1",
    local: false,
  },
  {
    id: "anthropic",
    name: "Anthropic",
    api: "anthropic",
    baseUrl: "https://api.anthropic.com/v1",
    local: false,
  },
  {
    id: "openrouter",
    name: "OpenRouter",
    api: "chat-completions",
    baseUrl: "https://openrouter.ai/api/v1",
    local: false,
  },
  {
    id: "ollama",
    name: "Ollama",
    api: "chat-completions",
    baseUrl: "http://127.0.0.1:11434/v1",
    local: true,
  },
  {
    id: "vllm",
    name: "vLLM",
    api: "chat-completions",
    baseUrl: "http://127.0.0.1:8000/v1",
    local: true,
  },
  {
    id: "custom",
    name: "Custom",
    api: "chat-completions",
    baseUrl: "",
    local: false,
  },
] as const;
const emptyBrief: Brief = {
  idea: "",
  kind: "You decide",
  vibe: "You decide",
  difficulty: "You decide",
};
export function App() {
  const [page, setPage] = useState<"home" | "library">("home");
  const [starter, setStarter] = useState<StarterGame | null>(null);
  const [remixing, setRemixing] = useState(false);
  const [mode, setMode] = useState<"studio" | "play">("studio");
  const [filesOpen, setFilesOpen] = useState(false),
    [source, setSource] = useState<{
      path: string;
      text: string;
      truncated: boolean;
    } | null>(null);
  const [snapshot, setSnapshot] = useState<AppSnapshot | null>(null),
    [project, setProject] = useState<ProjectView | null>(null);
  const [dialog, setDialog] = useState<"settings" | "create" | null>(null),
    [error, setError] = useState(""),
    [message, setMessage] = useState(""),
    [details, setDetails] = useState(false),
    [revisions, setRevisions] = useState(false);
  const [creating, setCreating] = useState(false),
    [brief, setBrief] = useState<Brief>(emptyBrief),
    [answer, setAnswer] = useState("");
  const [previewNonce, setPreviewNonce] = useState(0);
  const [comparison, setComparison] = useState<{
    gameId: string;
    interactionId: string;
    view: ReviewView;
    tab: "current" | "draft";
  } | null>(null);
  useEffect(() => {
    if (
      comparison &&
      (project?.game.id !== comparison.gameId ||
        !project?.pending.some((p) => p.id === comparison.interactionId))
    )
      setComparison(null);
  }, [project?.game.id, project?.pending, comparison]);
  const selected = useRef<string | null>(null);
  const captured = useRef(new Set<string>());
  const log = useRef<HTMLDivElement>(null);
  const refresh = async () =>
    setSnapshot(await bridge.action<AppSnapshot>({ type: "snapshot" }));
  const fail = (e: unknown) =>
    setError(e instanceof Error ? e.message : String(e));
  const open = async (id: string, play = false) => {
    try {
      selected.current = id;
      const view = await bridge.action<ProjectView>({
        type: play ? "game.play" : "game.open",
        id,
      });
      if (selected.current === id) {
        setStarter(null);
        setProject(view);
        setMode(play ? "play" : "studio");
        setFilesOpen(false);
        setSource(null);
        setError("");
        setAnswer("");
      }
    } catch (e) {
      fail(e);
    }
  };
  useEffect(() => {
    const off = bridge.onEvent((event) => {
      if (event.type === "changed") void refresh().catch(fail);
      if (event.type === "project" && selected.current === event.view.game.id)
        setProject(event.view);
      if (event.type === "runtime") void refresh().catch(fail);
    });
    void refresh().catch(fail);
    return off;
  }, []);
  useEffect(() => {
    log.current?.scrollTo({
      top: log.current.scrollHeight,
      behavior: "smooth",
    });
  }, [project?.messages.length, project?.partial]);
  const home = () => {
    setStarter(null);
    selected.current = null;
    setProject(null);
    setMode("studio");
    setFilesOpen(false);
    setSource(null);
    setRevisions(false);
    setError("");
    void refresh().catch(fail);
  };
  const startCreate = (idea = "") => {
    setBrief({ ...emptyBrief, idea });
    setDialog("create");
    setError("");
  };
  const create = async () => {
    if (!snapshot?.settings.provider) {
      setDialog("settings");
      setError("Connect a model before building your first game.");
      return;
    }
    setCreating(true);
    try {
      const view = await bridge.action<ProjectView>({
        type: "game.create",
        brief,
      });
      selected.current = view.game.id;
      setProject(view);
      setMode("studio");
      setDialog(null);
      await refresh();
    } catch (e) {
      fail(e);
    } finally {
      setCreating(false);
    }
  };
  const act = async (fn: () => Promise<unknown>) => {
    try {
      setError("");
      await fn();
    } catch (e) {
      fail(e);
    }
  };
  const send = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!project || !message.trim()) return;
    const text = message;
    setMessage("");
    await act(() =>
      bridge.action({ type: "game.send", id: project.game.id, text }),
    );
  };
  const remix = async (game: StarterGame) => {
    if (remixing) return;
    setRemixing(true);
    await act(async () => {
      const view = await bridge.action<ProjectView>({
        type: "starter.remix",
        id: game.id,
      });
      selected.current = view.game.id;
      setStarter(null);
      setProject(view);
      setMode("studio");
      setFilesOpen(false);
      setSource(null);
      setRevisions(false);
      setMessage("");
      await refresh();
    });
    setRemixing(false);
  };
  return (
    <div
      className={`app-shell ${!project ? "discovery" : ""} ${starter ? "starter-playing" : ""} ${project && mode === "play" ? "playing" : ""}`}
    >
      {!project && !starter && (
        <header className="arcade-topbar">
          <button
            className="brand"
            onClick={() => {
              home();
              setPage("home");
            }}
            aria-label="Arcade home"
          >
            <span className="brand-mark">
              <Icon name="sparkle" size={27} />
            </span>
            <span>
              arcade<span className="brand-dot">.</span>
            </span>
          </button>
          <nav aria-label="Main navigation">
            <button
              className={page === "home" ? "active" : ""}
              onClick={() => setPage("home")}
            >
              Home
            </button>
            <button
              className={page === "library" ? "active" : ""}
              onClick={() => setPage("library")}
            >
              My games
              {Boolean(snapshot?.games.length) && (
                <span>{snapshot?.games.length}</span>
              )}
            </button>
          </nav>
          <div className="topbar-actions">
            {snapshot?.activeGameId && (
              <button
                className="build-indicator"
                onClick={() => void open(snapshot.activeGameId!)}
              >
                <span className="busy-dot" />
                {snapshot.games.find((g) => g.id === snapshot.activeGameId)
                  ?.status === "waiting"
                  ? "Needs your input"
                  : "Building"}
              </button>
            )}
            <button className="top-create" onClick={() => startCreate()}>
              <Icon name="plus" size={17} />
              Create
            </button>
            <button
              aria-label="Settings"
              className="top-settings"
              onClick={() => {
                setError("");
                setDialog("settings");
              }}
            >
              <Icon name="settings" size={21} />
            </button>
          </div>
        </header>
      )}
      {project && (
        <>
          <aside className="sidebar">
            <button className="brand" onClick={home} aria-label="Arcade home">
              <span className="brand-mark">
                <Icon name="sparkle" size={26} />
              </span>
              <span>
                arcade<span className="brand-dot">.</span>
              </span>
            </button>
            <button
              className={`nav-item ${!project ? "selected" : ""}`}
              onClick={home}
            >
              <Icon name="grid" />
              Your games
              <span className="count">{snapshot?.games.length ?? 0}</span>
            </button>
            <button className="nav-item" onClick={() => startCreate()}>
              <Icon name="plus" />
              Create game
            </button>
            <div className="sidebar-rule" />
            {snapshot?.games.length ? (
              <>
                <p className="nav-caption">Recent</p>
                {snapshot.games.slice(0, 5).map((g) => (
                  <button
                    className={`recent-item ${project?.game.id === g.id ? "current" : ""}`}
                    onClick={() => void open(g.id)}
                    key={g.id}
                  >
                    <span className={`recent-dot tone-${g.title.length % 3}`} />
                    <span>{g.title}</span>
                    {snapshot.activeGameId === g.id && (
                      <span className="busy-dot" />
                    )}
                  </button>
                ))}
              </>
            ) : null}
            <div className="sidebar-bottom">
              {snapshot?.activeGameId && (
                <button
                  className="active-build"
                  onClick={() => void open(snapshot.activeGameId!)}
                >
                  <span className="busy-dot" />
                  <span>
                    {snapshot.games.find((g) => g.id === snapshot.activeGameId)
                      ?.status === "waiting"
                      ? "Needs your input · "
                      : "Building "}
                    {snapshot.games.find((g) => g.id === snapshot.activeGameId)
                      ?.title ?? "your game"}
                  </span>
                </button>
              )}
              <button
                className="nav-item"
                onClick={() => {
                  setError("");
                  setDialog("settings");
                }}
              >
                <Icon name="settings" />
                Settings
              </button>
              <div className="model-footnote">
                <span
                  className={
                    snapshot?.settings.provider ? "connected-dot" : "empty-dot"
                  }
                />
                <span>
                  {snapshot?.settings.provider?.name ?? "No model connected"}
                </span>
              </div>
            </div>
          </aside>
        </>
      )}
      <main
        className={
          project
            ? `workspace ${mode === "play" ? "play-workspace" : ""}`
            : "library-page"
        }
      >
        {error && (
          <div role="alert" className="error-banner">
            <span>
              {error.replace(
                /^Error invoking remote method '[^']+': Error: /,
                "",
              )}
            </span>
            <button aria-label="Dismiss error" onClick={() => setError("")}>
              <Icon name="close" size={16} />
            </button>
          </div>
        )}
        {starter ? (
          <StarterPlayer
            game={starter}
            onBack={home}
            onRemix={() => void remix(starter)}
            onFullscreen={() =>
              void act(() => bridge.action({ type: "window.fullscreen" }))
            }
          />
        ) : !project ? (
          <>
            {page === "home" && (
              <ArcadeHome
                starters={snapshot?.starters ?? []}
                onPlay={(game) => {
                  setStarter(game);
                  setError("");
                }}
                onRemix={(game) => void remix(game)}
              />
            )}
            {(page === "library" || Boolean(snapshot?.games.length)) && (
              <LibraryScreen
                compact={page === "home"}
                games={snapshot?.games ?? []}
                activeId={snapshot?.activeGameId ?? null}
                onPlay={(id) => void open(id, true)}
                onBuild={(id) => void open(id)}
                onCreate={() => startCreate()}
                onClone={(id) =>
                  void act(async () => {
                    const v = await bridge.action<ProjectView>({
                      type: "game.duplicate",
                      id,
                    });
                    await refresh();
                    await open(v.game.id);
                  })
                }
              />
            )}
            {page === "home" && (snapshot?.games.length ?? 0) > 3 && (
              <button
                className="all-games-link"
                onClick={() => setPage("library")}
              >
                View all games
                <Icon name="arrow" size={16} />
              </button>
            )}
            {page === "home" && (
              <section className="create-invitation">
                <div>
                  <Icon name="sparkle" size={22} />
                  <h2>What do you want to play?</h2>
                  <p>
                    Start with an idea. Make something only you would imagine.
                  </p>
                </div>
                <button onClick={() => startCreate()}>
                  Create a game
                  <Icon name="arrow" size={18} />
                </button>
              </section>
            )}
            {remixing && <p role="status">Creating your own copy…</p>}
          </>
        ) : (
          <>
            <header className="workspace-header">
              <div className="workspace-title">
                <button
                  className="icon-button"
                  onClick={home}
                  aria-label="Back to games"
                >
                  <Icon name="back" />
                </button>
                <h1>{project.game.title}</h1>
                <span className="model-chip">
                  {project.game.model?.name ?? "Choose a model"}
                </span>
              </div>
              <div className="workspace-tools">
                <div className="mode-switch">
                  <button
                    className={mode === "studio" ? "active" : ""}
                    onClick={() => void open(project.game.id)}
                  >
                    Build
                  </button>
                  <button
                    className={mode === "play" ? "active" : ""}
                    disabled={!project.previewUrl}
                    onClick={() => void open(project.game.id, true)}
                  >
                    Play
                  </button>
                </div>
                {mode === "play" ? (
                  <button
                    aria-label="Toggle fullscreen"
                    onClick={() =>
                      void act(() =>
                        bridge.action({ type: "window.fullscreen" }),
                      )
                    }
                  >
                    <Icon name="fullscreen" size={18} />
                    Fullscreen
                  </button>
                ) : (
                  <button
                    disabled={!project.files?.length}
                    onClick={() => setFilesOpen(!filesOpen)}
                  >
                    <Icon name="code" size={16} />
                    Files
                  </button>
                )}
                <button
                  className="studio-only"
                  disabled={project.active || Boolean(snapshot?.activeGameId)}
                  onClick={() =>
                    void act(async () => {
                      const result = await bridge.action<{ paths: string[] }>({
                        type: "game.import-assets",
                        id: project.game.id,
                      });
                      if (result.paths.length) {
                        setMessage(
                          `Use these imported assets in the game: ${result.paths.join(", ")}. Keep their filenames and inspect the current draft first.`,
                        );
                        setSource({
                          path: "Imported into working draft",
                          text: `${result.paths.join("\n")}\n\nThese files are staged, not published. Send an instruction to use them; the next publication still requires approval. Make sure you have permission to use these assets.`,
                          truncated: false,
                        });
                        setFilesOpen(true);
                      }
                    })
                  }
                >
                  <Icon name="folder" size={16} /> Import assets
                </button>
                <button
                  className="studio-only"
                  onClick={() => setRevisions(!revisions)}
                  disabled={!project.revisions.length}
                >
                  <Icon name="clock" size={16} />
                  Versions
                </button>
                <button
                  className="studio-only"
                  onClick={() =>
                    void act(() =>
                      bridge.action({
                        type: "game.export",
                        id: project.game.id,
                      }),
                    )
                  }
                  disabled={!project.game.revisionId}
                >
                  <Icon name="download" size={16} />
                  Export
                </button>
              </div>
            </header>
            <div className="workspace-body">
              <section className="chat-panel">
                <div className="conversation" ref={log}>
                  <div className="brief-card">
                    <span className="mini-mark">
                      <Icon name="sparkle" size={16} />
                    </span>
                    <div>
                      <strong>Your idea</strong>
                      <p>{project.game.brief.idea}</p>
                      <div className="brief-tags">
                        {[
                          project.game.brief.kind,
                          project.game.brief.vibe,
                          project.game.brief.difficulty,
                        ]
                          .filter((t) => t !== "You decide")
                          .map((t, i) => (
                            <span key={i}>{t}</span>
                          ))}
                      </div>
                    </div>
                  </div>
                  {project.messages.map((m) => (
                    <div className={`chat-message ${m.role}`} key={m.id}>
                      <span className="message-avatar">
                        {m.role === "assistant" ? (
                          <Icon name="sparkle" size={16} />
                        ) : (
                          "You"
                        )}
                      </span>
                      <div>{m.text}</div>
                    </div>
                  ))}
                  {project.partial && (
                    <div className="chat-message assistant streaming">
                      <span className="message-avatar">
                        <Icon name="sparkle" size={16} />
                      </span>
                      <div>
                        {project.partial}
                        <span className="typing-caret" />
                      </div>
                    </div>
                  )}
                  {project.pending.map((p) => (
                    <section className="approval-card" key={p.id}>
                      <strong>
                        {p.kind === "safety"
                          ? "Build paused by a safety check"
                          : p.kind === "approval"
                            ? "Save this game update?"
                            : p.question}
                      </strong>
                      <p>
                        {p.kind === "safety"
                          ? "The app must resolve this safety check before continuing. You can stop the build; your saved game is still available."
                          : p.kind === "approval"
                            ? "The agent is ready to publish a new playable version to this game’s folder."
                            : "Your agent needs a little direction."}
                      </p>
                      {p.kind === "safety" ? null : p.kind === "approval" ? (
                        <div className="button-row">
                          {project.reviewAvailable && (
                            <button
                              onClick={() =>
                                void act(async () => {
                                  const view = await bridge.action<ReviewView>({
                                    type: "game.review",
                                    id: project.game.id,
                                    interactionId: p.id,
                                  });
                                  setComparison({
                                    gameId: project.game.id,
                                    interactionId: p.id,
                                    view,
                                    tab: "draft",
                                  });
                                })
                              }
                            >
                              Compare update
                            </button>
                          )}
                          <button
                            className="primary small"
                            onClick={() =>
                              void act(() =>
                                bridge.action({
                                  type: "game.respond",
                                  id: project.game.id,
                                  interactionId: p.id,
                                  decision: "once",
                                }),
                              )
                            }
                          >
                            Allow once
                          </button>
                          <button
                            onClick={() =>
                              void act(() =>
                                bridge.action({
                                  type: "game.respond",
                                  id: project.game.id,
                                  interactionId: p.id,
                                  decision: "session",
                                }),
                              )
                            }
                          >
                            This session
                          </button>
                          <button
                            onClick={() =>
                              void act(() =>
                                bridge.action({
                                  type: "game.respond",
                                  id: project.game.id,
                                  interactionId: p.id,
                                  decision: "deny",
                                }),
                              )
                            }
                          >
                            Deny
                          </button>
                        </div>
                      ) : (
                        <form
                          onSubmit={(e) => {
                            e.preventDefault();
                            void act(() =>
                              bridge.action({
                                type: "game.respond",
                                id: project.game.id,
                                interactionId: p.id,
                                answer,
                              }),
                            );
                            setAnswer("");
                          }}
                        >
                          {!!p.options?.length && (
                            <div className="button-row">
                              {p.options.map((option) => (
                                <button
                                  key={option}
                                  type="button"
                                  onClick={() => setAnswer(option)}
                                >
                                  {option}
                                </button>
                              ))}
                            </div>
                          )}
                          <input
                            aria-label="Answer agent question"
                            value={answer}
                            onChange={(e) => setAnswer(e.target.value)}
                            placeholder="Your answer"
                          />
                          <button
                            className="primary small"
                            disabled={!answer.trim()}
                          >
                            Send answer
                          </button>
                        </form>
                      )}
                    </section>
                  ))}
                  {project.game.error && (
                    <p className="inline-error">{project.game.error}</p>
                  )}
                </div>
                <div className="activity-block">
                  <button
                    className="activity-toggle"
                    onClick={() => setDetails(!details)}
                    aria-expanded={details}
                  >
                    <span
                      className={project.active ? "busy-dot" : "status-dot"}
                    />
                    <span>{project.stage}</span>
                    <Icon name="down" size={15} />
                  </button>
                  {details && (
                    <div className="activity-detail">
                      {project.activity.length ? (
                        project.activity.map((a) => (
                          <p key={a.id} className={a.kind}>
                            <span>
                              {new Date(a.time).toLocaleTimeString([], {
                                hour: "2-digit",
                                minute: "2-digit",
                              })}
                            </span>
                            {a.text}
                          </p>
                        ))
                      ) : (
                        <p>No activity yet.</p>
                      )}
                    </div>
                  )}
                </div>
                <form className="composer" onSubmit={send}>
                  <textarea
                    aria-label="Change your game"
                    placeholder={
                      project.game.session
                        ? "What would make it even better?"
                        : "Describe what you’d like to build…"
                    }
                    value={message}
                    onChange={(e) => setMessage(e.target.value)}
                    disabled={project.active || !!project.pending.length}
                  />
                  <div className="composer-bottom">
                    <span>Make it yours.</span>
                    {project.active || project.pending.length > 0 ? (
                      <button
                        type="button"
                        className="send-button stop"
                        aria-label="Cancel build"
                        onClick={() =>
                          void act(() =>
                            bridge.action({
                              type: "game.cancel",
                              id: project.game.id,
                            }),
                          )
                        }
                      >
                        <Icon name="stop" size={16} />
                      </button>
                    ) : (
                      <button
                        className="send-button"
                        aria-label="Send message"
                        disabled={!message.trim() || !!project.pending.length}
                      >
                        <Icon name="send" size={19} />
                      </button>
                    )}
                  </div>
                </form>
              </section>
              <section className="preview-panel">
                <div className="preview-toolbar">
                  {comparison ? (
                    <div
                      className="comparison-tabs"
                      aria-label="Compare game versions"
                    >
                      <button
                        aria-pressed={comparison.tab === "current"}
                        disabled={!comparison.view.currentUrl}
                        onClick={() =>
                          setComparison({ ...comparison, tab: "current" })
                        }
                      >
                        Current
                      </button>
                      <button
                        aria-pressed={comparison.tab === "draft"}
                        onClick={() =>
                          setComparison({ ...comparison, tab: "draft" })
                        }
                      >
                        Proposed update
                      </button>
                    </div>
                  ) : (
                    <span>
                      <Icon name="play" size={14} />
                      Play
                    </span>
                  )}
                  {(project.previewUrl || comparison) && (
                    <button
                      className="icon-button"
                      aria-label="Restart game"
                      onClick={() => setPreviewNonce((n) => n + 1)}
                    >
                      <Icon name="rotate" size={15} />
                    </button>
                  )}
                  <span className="preview-hint">
                    {comparison
                      ? "Temporary play · saves unchanged"
                      : project.game.revisionId
                        ? "Your latest version"
                        : "Your game will appear here"}
                  </span>
                </div>
                {comparison && (
                  <details className="comparison-files">
                    <summary>
                      Changed files · {comparison.view.changes.length}
                    </summary>
                    <ul>
                      {comparison.view.changes.map((c) => (
                        <li key={c.path}>
                          <code>{c.path}</code>
                          <span>{c.kind}</span>
                        </li>
                      ))}
                    </ul>
                  </details>
                )}
                <TestingPanel value={project.testing} />
                <div className="preview-stage">
                  {comparison ? (
                    <iframe
                      id="comparison-preview"
                      title={
                        comparison.tab === "current"
                          ? "Current version comparison"
                          : "Proposed update comparison"
                      }
                      key={`${comparison.view.digest}:${comparison.tab}:${previewNonce}`}
                      src={
                        comparison.tab === "current"
                          ? comparison.view.currentUrl!
                          : comparison.view.draftUrl
                      }
                      sandbox="allow-scripts"
                      allow=""
                    />
                  ) : project.previewUrl ? (
                    <GameFrame
                      key={`${project.previewUrl}:${previewNonce}`}
                      id="game-preview"
                      title="Game preview"
                      src={project.previewUrl}
                      onLoad={() => {
                        const id = project.game.id,
                          revisionId = project.game.revisionId!;
                        if (captured.current.has(revisionId)) return;
                        captured.current.add(revisionId);
                        setTimeout(() => {
                          if (selected.current === id)
                            void bridge
                              .action({
                                type: "game.thumbnail",
                                id,
                                revisionId,
                              })
                              .catch(() => {});
                        }, 900);
                      }}
                      sandbox="allow-scripts"
                      allow=""
                    />
                  ) : (
                    <div className="preview-empty">
                      <div>
                        <Icon name="game" size={56} />
                      </div>
                      <h2>A little magic in the making.</h2>
                      <p>
                        {project.active
                          ? "Your agent is building. You can follow along on the left."
                          : "Start a build to bring your idea to life."}
                      </p>
                    </div>
                  )}
                </div>
              </section>
            </div>
            {filesOpen && mode === "studio" && (
              <section className="files-panel">
                <div className="files-heading">
                  <h3>Published files</h3>
                  <button
                    aria-label="Close files"
                    onClick={() => setFilesOpen(false)}
                  >
                    <Icon name="close" size={18} />
                  </button>
                </div>
                <div className="files-body">
                  <nav>
                    {project.files?.map((f) => (
                      <button
                        className={source?.path === f.path ? "selected" : ""}
                        key={f.path}
                        onClick={() =>
                          void act(async () => {
                            const s = await bridge.action<{
                              text: string;
                              truncated: boolean;
                            }>({
                              type: "game.file",
                              id: project.game.id,
                              path: f.path,
                            });
                            setSource({ path: f.path, ...s });
                          })
                        }
                      >
                        <Icon name="code" size={14} />
                        {f.path}
                        <small>{Math.ceil(f.bytes / 1024)} KB</small>
                      </button>
                    ))}
                  </nav>
                  <div className="source-view">
                    <header>
                      {source?.path ?? "Choose a file"}
                      {source?.truncated && " · first 50,000 characters"}
                    </header>
                    <pre>
                      {source?.text ??
                        "Every version contains the complete project, including its bundled libraries."}
                    </pre>
                  </div>
                </div>
              </section>
            )}
            {revisions && (
              <div className="versions-panel">
                <div>
                  <h3>Versions</h3>
                  <button
                    className="icon-button"
                    onClick={() => setRevisions(false)}
                    aria-label="Close versions"
                  >
                    <Icon name="close" size={16} />
                  </button>
                </div>
                {project.revisions.map((r, i) => (
                  <button
                    key={r.id}
                    disabled={project.active}
                    onClick={() =>
                      void act(async () => {
                        const v = await bridge.action<ProjectView>({
                          type: "game.restore",
                          id: project.game.id,
                          revisionId: r.id,
                        });
                        setProject(v);
                        await refresh();
                      })
                    }
                  >
                    <Icon name="clock" size={17} />
                    <span>
                      Version {project.revisions.length - i}
                      <small>{new Date(r.createdAt).toLocaleString()}</small>
                    </span>
                    {project.game.revisionId === r.id ? (
                      <span className="current-label">Current</span>
                    ) : (
                      <span>Restore</span>
                    )}
                  </button>
                ))}
              </div>
            )}
          </>
        )}
      </main>
      {dialog === "settings" && snapshot && (
        <SettingsDialog
          settings={snapshot.settings}
          onClose={() => setDialog(null)}
          onSaved={(s) => {
            setSnapshot(s);
            setDialog(null);
            setError("");
          }}
          onError={fail}
        />
      )}
      {dialog === "create" && (
        <div
          className="overlay"
          onClick={(e) => {
            if (e.target === e.currentTarget && !creating) setDialog(null);
          }}
        >
          <form
            className="modal create-modal"
            onSubmit={(e) => {
              e.preventDefault();
              void create();
            }}
          >
            <div className="modal-header">
              <span className="modal-icon">
                <Icon name="sparkle" size={24} />
              </span>
              <button
                type="button"
                className="icon-button"
                aria-label="Close creation"
                onClick={() => setDialog(null)}
              >
                <Icon name="close" />
              </button>
            </div>
            <h2>Let’s make your next favorite.</h2>
            <p>Start with an idea. The details are up to you.</p>
            <div className="engine-choice">
              <button
                type="button"
                className={brief.engine !== "three" ? "selected" : ""}
                onClick={() => setBrief({ ...brief, engine: "canvas" })}
              >
                <Icon name="game" />
                <span>
                  <strong>2D playground</strong>
                  <small>Canvas · arcade, puzzles & more</small>
                </span>
              </button>
              <button
                type="button"
                className={brief.engine === "three" ? "selected" : ""}
                onClick={() => setBrief({ ...brief, engine: "three" })}
              >
                <Icon name="cube" />
                <span>
                  <strong>Another dimension</strong>
                  <small>Three.js · a bundled 3D starter</small>
                </span>
              </button>
            </div>
            <div className="idea-suggestions">
              {["Neon Snake", "Cozy puzzle", "Orbit Runner"].map((name) => (
                <button
                  type="button"
                  key={name}
                  onClick={() =>
                    setBrief({
                      ...brief,
                      idea:
                        name === "Neon Snake"
                          ? "A neon Snake game with smooth controls and a high score."
                          : name === "Cozy puzzle"
                            ? "A cozy colorful matching puzzle."
                            : "A beautiful 3D runner with collectible rings and obstacles.",
                      engine: name === "Orbit Runner" ? "three" : "canvas",
                    })
                  }
                >
                  {name}
                  <Icon name="arrow" size={12} />
                </button>
              ))}
            </div>
            {error && (
              <p className="modal-error" role="alert">
                {error}
              </p>
            )}
            <label className="field-label">
              Your idea
              <textarea
                autoFocus
                aria-label="Game idea"
                required
                maxLength={8000}
                placeholder="A game where you catch falling stars…"
                value={brief.idea}
                onChange={(e) => setBrief({ ...brief, idea: e.target.value })}
              />
            </label>
            <div className="brief-options">
              {[
                {
                  key: "kind",
                  label: "Game experience",
                  values: [
                    "You decide",
                    "Arcade",
                    "Puzzle",
                    "Adventure",
                    "Catch & dodge",
                    "Quiz",
                  ],
                },
                {
                  key: "vibe",
                  label: "Look & feel",
                  values: [
                    "You decide",
                    "Neon",
                    "Cozy",
                    "Retro",
                    "Minimal",
                    "Playful",
                  ],
                },
                {
                  key: "difficulty",
                  label: "Pace",
                  values: [
                    "You decide",
                    "Relaxed",
                    "A little challenge",
                    "Fast & tricky",
                  ],
                },
              ].map((field) => (
                <label key={field.key} className="field-label">
                  {field.label}
                  <select
                    aria-label={field.label}
                    value={brief[field.key as keyof Brief]}
                    onChange={(e) =>
                      setBrief({ ...brief, [field.key]: e.target.value })
                    }
                  >
                    {field.values.map((v) => (
                      <option key={v}>{v}</option>
                    ))}
                  </select>
                </label>
              ))}
            </div>
            <div className="modal-footer">
              <button
                type="button"
                onClick={() =>
                  setBrief({
                    ...brief,
                    kind: "You decide",
                    vibe: "You decide",
                    difficulty: "You decide",
                    idea:
                      brief.idea ||
                      "Surprise me with a delightful tiny arcade game.",
                  })
                }
              >
                <Icon name="sparkle" size={16} />
                Surprise me
              </button>
              <button
                className="primary"
                disabled={creating || !brief.idea.trim()}
              >
                {creating ? "Starting…" : "Build my game"}
                <Icon name="arrow" size={17} />
              </button>
            </div>
            {!snapshot?.settings.provider && (
              <p className="modal-note">
                You’ll connect a model before your first build.
              </p>
            )}
            <button
              type="button"
              className="try-starter"
              onClick={() =>
                void act(async () => {
                  const v = await bridge.action<ProjectView>({
                    type: "game.template",
                  });
                  selected.current = v.game.id;
                  setProject(v);
                  setMode("play");
                  setDialog(null);
                  await refresh();
                })
              }
            >
              Try the playable 3D starter <Icon name="arrow" size={15} />
              <small>No model needed</small>
            </button>
          </form>
        </div>
      )}
    </div>
  );
}
function SettingsDialog({
  settings,
  onClose,
  onSaved,
  onError,
}: {
  settings: Settings;
  onClose: () => void;
  onSaved: (s: AppSnapshot) => void;
  onError: (e: unknown) => void;
}) {
  const [tab, setTab] = useState("model"),
    [provider, setProvider] = useState<ProviderSettings>(
      settings.provider ?? { ...presets[0], preset: "openai", model: "" },
    ),
    [key, setKey] = useState(""),
    [root, setRoot] = useState(settings.gamesRoot),
    [saving, setSaving] = useState(false),
    [error, setError] = useState(""),
    [testStatus, setTestStatus] = useState("");
  const local = presets.find((p) => p.id === provider.preset)?.local;
  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setError("");
    try {
      const s = await bridge.action<AppSnapshot>({
        type: "settings.save",
        provider,
        ...(key ? { apiKey: key } : {}),
      });
      setKey("");
      onSaved(s);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save settings.");
    } finally {
      setSaving(false);
    }
  };
  return (
    <div
      className="overlay"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <form className="modal settings-modal" onSubmit={save}>
        <div className="modal-header">
          <h2>Settings</h2>
          <button
            type="button"
            className="icon-button"
            aria-label="Close settings"
            onClick={onClose}
          >
            <Icon name="close" />
          </button>
        </div>
        <div className="settings-tabs">
          <button
            type="button"
            className={tab === "model" ? "active" : ""}
            onClick={() => setTab("model")}
          >
            <Icon name="computer" size={17} />
            Model
          </button>
          <button
            type="button"
            className={tab === "storage" ? "active" : ""}
            onClick={() => setTab("storage")}
          >
            <Icon name="folder" size={17} />
            Storage
          </button>
        </div>
        {error && (
          <p className="modal-error" role="alert">
            {error}
          </p>
        )}
        {tab === "model" ? (
          <>
            <p className="settings-intro">
              Choose the model that brings your ideas to life.
            </p>
            <p className="settings-intro">
              Use a vision-capable model for visual playtesting. The builder can
              play its draft and send game-only screenshots to this model. Your
              desktop and microphone are never captured. No video is recorded.
            </p>
            <div className="provider-presets">
              {presets.map((p) => (
                <button
                  type="button"
                  className={provider.preset === p.id ? "selected" : ""}
                  key={p.id}
                  onClick={() => {
                    setProvider({
                      name: p.name,
                      preset: p.id,
                      api: p.api,
                      baseUrl: p.baseUrl,
                      model: "",
                    });
                    setKey("");
                    setTestStatus("");
                  }}
                >
                  {p.name}
                  {p.local && <span>Local</span>}
                </button>
              ))}
            </div>
            <div className="settings-fields">
              <label className="field-label">
                Model ID
                <input
                  aria-label="Model ID"
                  value={provider.model}
                  onChange={(e) =>
                    setProvider({ ...provider, model: e.target.value })
                  }
                  placeholder={
                    local
                      ? "The model installed on your server"
                      : "Enter an exact model ID"
                  }
                  required
                />
              </label>
              {!local && (
                <label className="field-label">
                  API key
                  <input
                    type="password"
                    aria-label="API key"
                    autoComplete="off"
                    value={key}
                    onChange={(e) => setKey(e.target.value)}
                    placeholder="Leave blank to keep your saved key"
                  />
                </label>
              )}
              <details className="advanced-settings">
                <summary>Connection details</summary>
                <label className="field-label">
                  Provider name
                  <input
                    aria-label="Provider name"
                    value={provider.name}
                    onChange={(e) =>
                      setProvider({ ...provider, name: e.target.value })
                    }
                    required
                  />
                </label>
                <label className="field-label">
                  API
                  <select
                    aria-label="API"
                    value={provider.api}
                    onChange={(e) =>
                      setProvider({
                        ...provider,
                        api: e.target.value as ProviderSettings["api"],
                      })
                    }
                  >
                    <option value="responses">Responses</option>
                    <option value="chat-completions">Chat Completions</option>
                    <option value="anthropic">Anthropic Messages</option>
                  </select>
                </label>
                <label className="field-label">
                  Base URL
                  <input
                    aria-label="Base URL"
                    value={provider.baseUrl}
                    onChange={(e) =>
                      setProvider({ ...provider, baseUrl: e.target.value })
                    }
                    required
                  />
                </label>
                {local && (
                  <label className="field-label">
                    API key if required
                    <input
                      aria-label="API key"
                      type="password"
                      value={key}
                      onChange={(e) => setKey(e.target.value)}
                    />
                  </label>
                )}
              </details>
            </div>
            <p className="settings-note">
              {local
                ? "Start your local model server first. Arcade connects to it on your computer."
                : "Keys are saved by the harness in your platform’s secure store."}{" "}
              Model changes apply to new games. Testing sends a short request to
              your selected model.
            </p>
            <div className="modal-footer">
              <button
                type="button"
                disabled={saving || !provider.model}
                onClick={async () => {
                  setSaving(true);
                  setTestStatus("Connecting…");
                  try {
                    await bridge.action({
                      type: "settings.save",
                      provider,
                      ...(key ? { apiKey: key } : {}),
                    });
                    setKey("");
                    await bridge.action({ type: "provider.test" });
                    setTestStatus("Connection works");
                  } catch {
                    setTestStatus(
                      "Could not connect. Check the endpoint, model and credentials.",
                    );
                  } finally {
                    setSaving(false);
                  }
                }}
              >
                Test connection
              </button>
              <span className="test-status" role="status">
                {testStatus}
              </span>
              <button className="primary" disabled={saving}>
                {saving ? "Saving…" : "Save settings"}
              </button>
            </div>
          </>
        ) : (
          <>
            <div className="storage-copy">
              <span className="storage-icon">
                <Icon name="folder" size={32} />
              </span>
              <h3>A home for your games.</h3>
              <p>
                Each game gets its own folder. Change where new games are saved;
                existing games stay where they are.
              </p>
              <div className="directory-path">{root}</div>
              <button
                type="button"
                onClick={async () => {
                  try {
                    const s = await bridge.action<AppSnapshot>({
                      type: "settings.directory",
                    });
                    setRoot(s.settings.gamesRoot);
                  } catch (e) {
                    onError(e);
                  }
                }}
              >
                Choose games folder
              </button>
            </div>
            <div className="modal-footer">
              <span className="settings-note">
                Your library and agent history stay in app data.
              </span>
              <button
                type="button"
                className="primary"
                onClick={() => {
                  void bridge
                    .action<AppSnapshot>({ type: "snapshot" })
                    .then(onSaved)
                    .catch(onError);
                }}
              >
                Done
              </button>
            </div>
          </>
        )}
      </form>
    </div>
  );
}
