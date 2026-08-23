import { useEffect, useRef, useState, type ReactNode } from "react";
// Emits the asset URL only; the worker itself loads lazily with the pdfjs chunk.
import pdfWorkerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import {
  getArtifacts,
  readArtifact,
  revealArtifact,
  type ArtifactContent,
  type ArtifactInfo,
  type Persona,
} from "../api";
import type { TodoItem } from "../types";
import { budgetUse, checkpointProgress, checkpointsFor } from "../personaStyle";
import { AccessSection } from "./AccessSection";
import { Icon } from "./Icon";
import { Markdown, OPEN_ARTIFACT_EVENT } from "./Markdown";

type Panel = "progress" | "artifacts" | "memory";

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
  toolNames: string[];
  todo: TodoItem[];
  // Tool calls run since the model last rewrote `todo` — see planFromItems.ts. The plan is a
  // snapshot of what it last SAID it was doing, and this is how far the run has moved since.
  planStepsSince?: number;
  running: boolean;
  // Fires when a full artifact preview opens/closes, so the app can auto-collapse the left nav
  // to give the preview (PDF/webpage/sheet) more room (#3).
  onPreviewChange?: (open: boolean) => void;
  // §32: the rail is the ONE session panel for every non-chat persona. Artifacts stays
  // cowork-only (deliverables; code-family gets "Files" later — slot reserved); the Access
  // section (the former Session-settings drawer) renders for all.
  showArtifacts?: boolean;
  personaId?: string;
  // The persona's family shapes the Progress panel: a code persona's work is reads/edits/
  // commands, a knowledge persona's is pages/searches/deliverables. Counting the same way for
  // both made the panel a generic "3 tool calls" that said nothing about the work.
  personaFamily?: string;
  personaName?: string;
  // The persona record itself, for the checkpoint strip: its declared job shape.
  persona?: Persona;
  /** Prompt-side tokens in the CURRENT round-trip (usage.context) — not a cumulative total. */
  contextUsed?: number;
  /** The window the ENGINE resolved for this session, sent on `ready`. */
  contextWindow?: number | null;
  /** Compaction markers in the transcript — how many times history was summarized to fit. */
  compactions?: number;
  /** Brain threads this session read from / wrote to. */
  threadsTouched?: { id: string; read: boolean; written: boolean }[];
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
  toolNames,
  todo,
  planStepsSince = 0,
  running,
  onPreviewChange,
  showArtifacts = true,
  personaId,
  personaFamily,
  personaName,
  persona,
  contextUsed,
  contextWindow,
  compactions,
  threadsTouched,
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
    memory: false,
  });
  const [artifacts, setArtifacts] = useState<ArtifactInfo[]>([]);
  // A long deliverable list pushed Access sixteen rows down the scroll. The header's count now
  // carries the total, so the list can start short and open on request.
  const [allArtifacts, setAllArtifacts] = useState(false);
  const [selected, setSelected] = useState<ArtifactInfo | null>(null);
  const [content, setContent] = useState<ArtifactContent | null>(null);

  const refreshArtifacts = () => getArtifacts(sessionId).then(setArtifacts).catch(() => setArtifacts([]));

  // Counted once and split once, here, so that each section's header glance, the decision to
  // render it at all, and its body are all reading the same list. Tallying in two places is how
  // a section ends up summarising something it does not show.
  const activity = countBuckets(toolNames, personaFamily);
  const memoryActivity = activity.filter((a) => MEMORY_BUCKETS.has(a.key));
  const workActivity = activity.filter((a) => !MEMORY_BUCKETS.has(a.key));
  const showContextMeters =
    (!!contextWindow && contextWindow > 0 && !!contextUsed && contextUsed > 0) || !!compactions;

  useEffect(() => {
    if (!active) return;
    if (showArtifacts) refreshArtifacts();
  }, [active, sessionId, refreshKey, showArtifacts]);

  // Switching conversations closes any open artifact — it belongs to the previous session's
  // workspace, which the new session can't (and shouldn't) read.
  useEffect(() => {
    setSelected(null);
    setContent(null);
  }, [sessionId]);

  useEffect(() => {
    setContent(null);
    if (!selected) return;
    readArtifact(sessionId, selected.path).then(setContent).catch(() => setContent(null));
  }, [selected?.path, sessionId]);

  // Notify the app when a preview opens/closes (drives the left-nav auto-collapse).
  useEffect(() => {
    onPreviewChange?.(!!selected);
  }, [!!selected, onPreviewChange]);

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
        setSelected(found);
        return;
      }
      getArtifacts(sessionId)
        .then((list) => {
          setArtifacts(list);
          setSelected(match(list, path) ?? minimal(path));
        })
        .catch(() => setSelected(minimal(path)));
    };
    window.addEventListener(OPEN_ARTIFACT_EVENT, onOpen);
    return () => window.removeEventListener(OPEN_ARTIFACT_EVENT, onOpen);
  }, [active, sessionId, artifacts]);

  if (!active) return null;

  return (
    <aside className={"right-rail" + (selected ? " artifact-mode" : "")}>
      {selected ? (
        <ArtifactViewer
          sessionId={sessionId}
          artifact={selected}
          content={content}
          onReload={reloadSelected}
          onBack={() => setSelected(null)}
          onOpenEntry={(path) =>
            setSelected({
              path,
              name: path.split("/").pop() || path,
              kind: kindFromPath(path),
              size: 0,
              modified_at: 0,
            })
          }
        />
      ) : (
        <>
          <RailSection
            title="Progress"
            summary={progressGlance({ todo, running, activity: workActivity })}
            open={open.progress}
            onToggle={() => setOpen({ ...open, progress: !open.progress })}
          >
            <ProgressSummary
              running={running}
              toolNames={toolNames}
              activity={workActivity}
              todo={todo}
              planStepsSince={planStepsSince}
              personaName={personaName}
              persona={persona}
              personaId={personaId}
            />
          </RailSection>

          {/* Memory — what this session has taken in and kept: how full the window is, whether
              history had to be summarized to fit, the reads and searches that filled it, and the
              durable threads it pulled from or changed.

              Rendered whenever ANY of those holds, not just when a brain thread was touched.
              Gating the whole section on `threadsTouched` was right while it was only a thread
              list; it would now hide the context meter on every run that never calls brain_* —
              most of them, and exactly the runs where a filling window is the thing you need to
              see. Still collapsed by default, because the glance carries the percentage: the one
              number worth having at all times sits in the header, not the body. */}
          {(!!threadsTouched?.length || !!memoryActivity.length || showContextMeters) && (
            <RailSection
              title="Memory"
              summary={memoryGlance({
                threads: threadsTouched || [],
                activity: memoryActivity,
                contextUsed,
                contextWindow,
              })}
              open={open.memory}
              onToggle={() => setOpen({ ...open, memory: !open.memory })}
            >
              {/* Three groups, each a titled <section>. Before this the body ran the meter, a
                  row of pills and a thread list together with nothing between them: to anything
                  reading the page linearly it was one undifferentiated stretch of text, and the
                  headings mean it can also be browsed by heading rather than only in order.
                  The ids are static because the rail is a singleton — one per app. */}
              <div className="rail-memory">
                {showContextMeters && (
                  <section className="rail-memory-group" aria-labelledby="rail-mem-window">
                    <h3 className="rail-memory-h" id="rail-mem-window">
                      Window
                    </h3>
                    <ContextMeters
                      contextUsed={contextUsed}
                      contextWindow={contextWindow}
                      compactions={compactions}
                    />
                  </section>
                )}
                {!!memoryActivity.length && (
                  <section className="rail-memory-group" aria-labelledby="rail-mem-intake">
                    <h3 className="rail-memory-h" id="rail-mem-intake">
                      Taken in
                    </h3>
                    {/* A real list: as a row of bare spans this announced as one run-on line
                        ("3 files read 2 searches 1 recall") with no boundary between the counts
                        and no total. Same idiom as the plan list and the checkpoint strip. */}
                    <ul className="rail-activity" data-testid="rail-memory-activity" role="list">
                      {memoryActivity.map((a) => (
                        <li className="rail-activity-item" key={a.key} role="listitem">
                          {a.text}
                        </li>
                      ))}
                    </ul>
                  </section>
                )}
                {!!threadsTouched?.length && (
                  <section className="rail-memory-group" aria-labelledby="rail-mem-threads">
                    <h3 className="rail-memory-h" id="rail-mem-threads">
                      Threads
                    </h3>
                    <ul className="rail-threads" data-testid="rail-threads" role="list">
                      {threadsTouched.map((t) => (
                        <li className="rail-thread" key={t.id} role="listitem">
                          <span className="rail-thread-id">{t.id}</span>
                          {/* The pills are the sighted reading. Spoken, "openevolve-phase-2
                              updated read" is three nouns in a row with no relation between
                              them — so the pills are hidden and the row says what happened. */}
                          {t.written && (
                            <span className="rail-thread-tag written" aria-hidden>
                              updated
                            </span>
                          )}
                          {t.read && (
                            <span className="rail-thread-tag" aria-hidden>
                              read
                            </span>
                          )}
                          <span className="sr-only">
                            {t.written && t.read
                              ? " — read from and updated"
                              : t.written
                                ? " — updated"
                                : " — read from"}
                          </span>
                        </li>
                      ))}
                    </ul>
                  </section>
                )}
              </div>
            </RailSection>
          )}

          {showArtifacts && (
          <RailSection
            title="Artifacts"
            summary={artifacts.length ? `${artifacts.length} file${artifacts.length === 1 ? "" : "s"}` : "none yet"}
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
                {/* All of them, once asked: the control's own label is "Show all 60", and the
                    40-row cap that used to live here made that promise false for any workspace
                    with more previewable files than that (the server returns up to 80). */}
                {(allArtifacts ? artifacts : artifacts.slice(0, 8)).map((a) => (
                  <button className="artifact-row" key={a.path} onClick={() => setSelected(a)}>
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
                {artifacts.length > 8 && (
                  <button
                    className="artifact-more"
                    onClick={() => setAllArtifacts((v) => !v)}
                    aria-expanded={allArtifacts}
                  >
                    {allArtifacts ? "Show fewer" : `Show all ${artifacts.length}`}
                  </button>
                )}
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
        </>
      )}
    </aside>
  );
}

// What a session has actually DONE so far, counted in the vocabulary of the persona doing it.
// A code persona's work is reads, edits and commands; a research persona's is pages opened and
// searches run; every persona's is what it recalled and recorded. Counting them all as "N tool
// calls" was true and useless — it never told you whether the run was reading, writing, or stuck.
type Bucket = { key: string; label: (n: number) => string; tools: string[] };

const BUCKETS: Record<string, Bucket> = {
  edited: {
    key: "edited",
    label: (n) => `${n} file${n === 1 ? "" : "s"} edited`,
    tools: ["write_file", "apply_patch", "apply_unified_diff", "replace_in_file", "create_file", "edit_file"],
  },
  read: {
    key: "read",
    label: (n) => `${n} file${n === 1 ? "" : "s"} read`,
    tools: ["read_file", "read_file_lines", "list_files"],
  },
  searched: { key: "searched", label: (n) => `${n} search${n === 1 ? "" : "es"}`, tools: ["grep", "search_files"] },
  commands: { key: "commands", label: (n) => `${n} command${n === 1 ? "" : "s"}`, tools: ["run_shell", "shell_task_output", "shell_task_kill"] },
  git: { key: "git", label: (n) => `${n} git check${n === 1 ? "" : "s"}`, tools: ["git_status", "git_diff", "git_log"] },
  pages: { key: "pages", label: (n) => `${n} page${n === 1 ? "" : "s"} opened`, tools: ["web_fetch"] },
  web: { key: "web", label: (n) => `${n} web search${n === 1 ? "" : "es"}`, tools: ["web_search"] },
  recalled: { key: "recalled", label: (n) => `${n} recall${n === 1 ? "" : "s"}`, tools: ["brain_recall"] },
  noted: { key: "noted", label: (n) => `${n} note${n === 1 ? "" : "s"} to memory`, tools: ["brain_note"] },
  asked: { key: "asked", label: (n) => `${n} question${n === 1 ? "" : "s"} to you`, tools: ["ask_user"] },
};

// The buckets that belong to Memory rather than Progress. Progress answers "is this run moving"
// — what it changed: edits, commands, git checks, questions put to you. Memory answers "what has
// this session taken in and kept" — the reads and searches that filled the window, and the brain
// threads it recalled from or wrote to.
//
// `noted` sits here rather than with the other writes because the rows it produces (the "updated"
// thread tags) are already in this section: counting it in Progress would put the same fact in
// two places and make neither the whole story.
const MEMORY_BUCKETS = new Set(["read", "searched", "pages", "web", "recalled", "noted"]);

// Tools whose work the panel already renders in full, so counting them again would say the same
// thing twice: the plan IS the todo list two elements up.
const RENDERED_ELSEWHERE = new Set(["todo_write"]);

// After this many tool calls with no rewrite, the plan stops being read as live state and says
// how old it is instead. Matched to the engine's own threshold (coworker/tools/todo.py
// `_STALE_AFTER`), which is what asks the model to refresh the list: past this point the panel
// and the model have been told the same thing, so the note appearing means the nudge has not
// landed yet — which is exactly when the reader needs to know not to trust the rows.
const PLAN_STALE_AFTER = 8;

// Order matters: the first bucket is the headline, so each family leads with the thing that
// means "the work is happening" for it — edits for code, pages read for research.
const FAMILY_ORDER: Record<string, string[]> = {
  code: ["edited", "read", "commands", "searched", "git", "recalled", "noted", "asked"],
  knowledge: ["pages", "web", "edited", "read", "searched", "commands", "recalled", "noted", "asked"],
};

function countBuckets(toolNames: string[], family?: string): { key: string; text: string }[] {
  const counts: Record<string, number> = {};
  let mcp = 0;
  // Calls no bucket names, kept under the tool's own name in the order they first appeared.
  const other = new Map<string, number>();
  for (const name of toolNames) {
    if (name.startsWith("mcp__")) {
      mcp += 1;
      continue;
    }
    let matched = RENDERED_ELSEWHERE.has(name);
    for (const b of Object.values(BUCKETS)) {
      if (b.tools.includes(name)) {
        counts[b.key] = (counts[b.key] || 0) + 1;
        matched = true;
      }
    }
    if (!matched) other.set(name, (other.get(name) || 0) + 1);
  }
  const order = FAMILY_ORDER[family || "knowledge"] || FAMILY_ORDER.knowledge;
  const out = order
    .filter((k) => counts[k])
    .map((k) => ({ key: k, text: BUCKETS[k].label(counts[k]) }));
  // MCP calls are a persona's declared servers doing work; they belong in the count even
  // though no static bucket can name them.
  if (mcp) out.push({ key: "mcp", text: `${mcp} MCP call${mcp === 1 ? "" : "s"}` });
  // The ~130 native connector tools (gmail_*, hubspot_*, notion_*, github_*, …) and anything the
  // catalog gains after this file was written match no bucket. Dropping them silently meant a run
  // done entirely through connectors rendered NO activity line at all, while the budget meter one
  // element up counted the same calls as "8/12" — the panel contradicting itself. A tool's own
  // name is not as good as a bucket's phrasing, but it is true.
  for (const [name, n] of other) {
    const label = name.replace(/_/g, " ");
    // "×N" rather than a leading count: a bucket can pluralize its own phrase ("3 searches"),
    // an arbitrary tool name cannot, and "3 gmail search messages" reads worse than the tally.
    out.push({ key: name, text: n === 1 ? label : `${label} ×${n}` });
  }
  return out;
}

// The persona's job shape with the run's position in it. A todo list says what the model chose
// to do; this says how far through the work this persona is SUPPOSED to do the run has got —
// and, crucially, what comes next. Without it the panel could show five ticked items and still
// not answer "is this nearly finished?".
function CheckpointStrip({
  persona,
  personaId,
  toolNames,
}: {
  persona?: Persona;
  personaId?: string;
  toolNames: string[];
}) {
  const steps = checkpointProgress(checkpointsFor(persona, personaId), toolNames);
  return (
    <ol className="rail-steps" data-testid="rail-checkpoints" aria-label="Job steps" role="list">
      {steps.map(({ checkpoint, state }) => (
        <li
          className={"rail-step " + state}
          key={checkpoint.id}
          data-state={state}
          // Explicit: `list-style: none` removes list semantics in Safari/VoiceOver, and the
          // list's aria-label goes with them.
          role="listitem"
          // The shape and colour say this to sighted users; aria-current says it to everyone
          // else, and the visually-hidden word carries the other three states.
          aria-current={state === "current" ? "step" : undefined}
        >
          <span className="rail-step-mark" aria-hidden />
          <span className="rail-step-label">{checkpoint.label}</span>
          <span className="sr-only">
            {state === "current"
              ? " — current step"
              : state === "done"
                ? " — done"
                : state === "skipped"
                  ? " — skipped"
                  : " — not started"}
          </span>
        </li>
      ))}
    </ol>
  );
}

/** One meter: a label, an n/N value and a bar. Budgets and context share the form because they
 *  answer the same question — how close is this run to a wall it cannot see. */
function Meter({
  label,
  value,
  used,
  limit,
  state,
  title,
}: {
  label: string;
  value: string;
  used: number;
  limit: number;
  state: string;
  title?: string;
}) {
  const pct = limit > 0 ? Math.min(100, Math.round((used / limit) * 100)) : 0;
  return (
    <div className={"rail-meter " + state} title={title}>
      <div className="rail-meter-head">
        <span className="rail-meter-label">{label}</span>
        <span className="rail-meter-value">{value}</span>
      </div>
      <div
        className="rail-meter-track"
        role="meter"
        aria-valuenow={used}
        aria-valuemin={0}
        aria-valuemax={limit}
        // Without this a screen reader computes the announcement from valuenow/valuemax and
        // says "47000 of 100000" — true, and not the number anyone is reading this bar for.
        aria-valuetext={value}
        aria-label={`${label}: ${value}`}
      >
        <span className="rail-meter-fill" style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

/** Budget consumption for THIS run, counted from what actually happened.
 *
 *  `runStarted` gates it: a budget is a ceiling on one run, so there is nothing to meter before
 *  the run has called anything. Context fill and compactions are facts about the whole session
 *  rather than the run, which is why they are no longer here — see ContextMeters, in Memory. */
function Meters({
  persona,
  toolNames,
  runStarted,
}: {
  persona?: Persona;
  toolNames: string[];
  runStarted: boolean;
}) {
  const budgets = runStarted ? budgetUse(persona, toolNames) : [];
  if (!budgets.length) return null;
  return (
    <div className="rail-meters" data-testid="rail-meters">
      {budgets.map(({ budget, used, state }) => (
        <Meter
          key={budget.id}
          label={budget.label}
          value={`${used}/${budget.limit}`}
          used={used}
          limit={budget.limit}
          state={state}
          title={
            budget.tools[0] === "*"
              ? "Every tool call this run"
              : `Counts: ${budget.tools.join(", ")}`
          }
        />
      ))}
    </div>
  );
}

/** How full the window is, and whether history has already been summarized to fit.
 *
 *  Deliberately NOT gated on a run having started: a session can fill its window by conversation
 *  alone — no tool call, no plan — and that is exactly when the number matters. This is the pair
 *  that used to sit under Progress; it belongs with the reads that filled the window, not with
 *  the plan being worked through. */
function ContextMeters({
  contextUsed,
  contextWindow,
  compactions,
}: {
  contextUsed?: number;
  contextWindow?: number | null;
  compactions?: number;
}) {
  // Only when BOTH numbers are real: a percentage against a guessed window is worse than none,
  // and a provider that reports no usage would otherwise render a confident 0%.
  const showContext = !!contextWindow && contextWindow > 0 && !!contextUsed && contextUsed > 0;
  if (!showContext && !compactions) return null;

  const pct = showContext ? Math.min(100, Math.round((contextUsed! / contextWindow!) * 100)) : 0;
  return (
    <div className="rail-meters" data-testid="rail-context-meters">
      {showContext && (
        <Meter
          label="context"
          value={`${pct}%`}
          used={contextUsed!}
          limit={contextWindow!}
          state={pct >= 90 ? "at" : pct >= 75 ? "near" : "ok"}
          title={`${contextUsed!.toLocaleString()} of ${contextWindow!.toLocaleString()} tokens`}
        />
      )}
      {!!compactions && (
        <p className="rail-muted" data-testid="rail-compactions">
          {/* "1×" is a glyph, not a word. Screen readers vary on U+00D7 — some say "times",
              some say nothing at all — so the spoken form is written out rather than left to
              the reader's character table. */}
          <span aria-hidden>Compacted {compactions}×</span>
          <span className="sr-only">
            History compacted {compactions} {compactions === 1 ? "time" : "times"} —
          </span>{" "}
          <span className="rail-thread-tag">history summarized to fit</span>
        </p>
      )}
    </div>
  );
}

function ProgressSummary({
  running,
  toolNames,
  activity,
  todo,
  planStepsSince = 0,
  personaName,
  persona,
  personaId,
}: {
  running: boolean;
  toolNames: string[];
  /** The work half of the tally — what the run CHANGED. The intake half (reads, searches,
   *  recalls) is Memory's line now, so the two sections answer different questions instead of
   *  splitting one sentence across both. See MEMORY_BUCKETS. */
  activity: { key: string; text: string }[];
  todo: TodoItem[];
  planStepsSince?: number;
  personaName?: string;
  persona?: Persona;
  personaId?: string;
}) {
  const done = todo.filter((t) => t.status === "done").length;
  const current = todo.find((t) => t.status === "in_progress");
  const next = todo.find((t) => t.status === "pending" && t !== current);
  const started = toolNames.length > 0 || todo.length > 0;
  // A plan the run has moved well past. It is still the best thing the panel has — the model
  // never said what it did instead — but it is a snapshot with a date on it, not "Now".
  const stale = planStepsSince >= PLAN_STALE_AFTER;

  const activityLine = activity.length > 0 && (
    <div className="rail-activity" data-testid="rail-activity">
      {activity.map((a) => (
        <span className="rail-activity-item" key={a.key}>
          {a.text}
        </span>
      ))}
    </div>
  );

  // Budget only. The context meter moved to Memory, where it renders in every branch including
  // idle — a session can fill its window by conversation alone, and gating that number on a run
  // having started is what used to hide it exactly when it mattered.
  const meters = (
    <Meters persona={persona} toolNames={toolNames} runStarted={running || started} />
  );

  // "Now" and "Next" first: the two things the panel was never answering. A plan of five items
  // with one in_progress buried in the middle made you read the list to find your place.
  //
  // Both keys are claims about the present, and a plan the run has left behind cannot support
  // either: "Now" over an item the model finished forty calls ago is the panel's loudest wrong
  // sentence, and it is what made a working run read as a stuck one. Past the staleness line the
  // keys say when instead of what — the rows are unchanged, only the tense is honest.
  const nowNext = (current || next) && (
    <div className="rail-nownext">
      {current && (
        <div className="rail-now">
          <span className="rail-nownext-key">{stale ? "Last on" : "Now"}</span>
          <span>{current.content}</span>
        </div>
      )}
      {next && (
        <div className="rail-next">
          <span className="rail-nownext-key">{stale ? "Then" : "Next"}</span>
          <span>{next.content}</span>
        </div>
      )}
    </div>
  );

  if (todo.length) {
    return (
      <div className="rail-todo-list">
        {nowNext}
        {/* A real list with a per-row state, the same idiom as the checkpoint strip below.
            The rows previously carried "done" as a colour, a strike-through and a tick glyph
            drawn in CSS — none of which reaches a screen reader, so the panel's headline
            content announced as one run-on line of item text with no states in it. */}
        <ul className="rail-plan" data-testid="rail-plan" aria-label="Plan" role="list">
          {todo.map((item, index) => (
            <li
              className={"rail-todo " + item.status}
              key={index}
              role="listitem"
              aria-current={item.status === "in_progress" ? "step" : undefined}
            >
              <span className="rail-todo-mark" aria-hidden />
              <span>{item.content}</span>
              <span className="sr-only">
                {item.status === "done"
                  ? " — done"
                  : item.status === "in_progress"
                    ? " — current step"
                    : " — not started"}
              </span>
            </li>
          ))}
        </ul>
        <div className="rail-muted">
          {done}/{todo.length} done
          {running ? " · working" : ""}
          {/* The line that was missing. "1/5 done" is a true reading of the last list the model
              wrote and a false reading of the run: a five-item plan revised once, 76 calls from
              the end of a 101-call turn, said "1/5 · Now: item 2" while items 2-5 were finished
              and committed — and went on saying it after the turn ended. The count is the whole
              point: it is the difference between a plan being worked and a plan left behind. */}
          {stale && (
            <span className="rail-plan-stale" data-testid="rail-plan-age">
              {" · plan unchanged for "}
              {planStepsSince} {planStepsSince === 1 ? "call" : "calls"}
            </span>
          )}
        </div>
        <CheckpointStrip persona={persona} personaId={personaId} toolNames={toolNames} />
        {meters}
        {activityLine}
      </div>
    );
  }
  if (running || started) {
    return (
      <div>
        <div className="rail-muted">{running ? "Working on this task." : "Last turn:"}</div>
        <CheckpointStrip persona={persona} personaId={personaId} toolNames={toolNames} />
        {meters}
        {activityLine}
      </div>
    );
  }
  return (
    <div>
      <div className="rail-muted">
        {personaName
          ? `${personaName}'s progress appears here — the plan it is working through, and what it has read, changed or produced.`
          : "For longer multi-step tasks, progress will appear here while OpenWorker plans, uses tools, waits for approval, and produces artifacts."}
      </div>
      {meters}
    </div>
  );
}

/** The Progress header's glance: how far through the plan, and how full the context is. */
function progressGlance({
  todo,
  running,
  activity,
}: {
  todo: TodoItem[];
  running: boolean;
  activity: { key: string; text: string }[];
}): string {
  const parts: string[] = [];
  if (todo.length) parts.push(`${todo.filter((t) => t.status === "done").length}/${todo.length}`);
  else if (running) parts.push("working");
  // The headline work bucket. Context percentage used to be this section's fallback glance, and
  // it has moved to Memory — without something in its place a finished, plan-less run collapses
  // to two words and a chevron, which is the thing these glances exist to prevent.
  if (activity.length) parts.push(activity[0].text);
  return parts.join(" · ");
}

/** The collapsed Memory header.
 *
 *  Context percentage leads, and it is the reason this glance exists: the section is closed by
 *  default, so the header is the only place that number can live and still be seen at a glance.
 *  It is also the one figure here that changes what you do next — compact, or start fresh. */
function memoryGlance({
  threads,
  activity,
  contextUsed,
  contextWindow,
}: {
  threads: { read: boolean; written: boolean }[];
  activity: { key: string; text: string }[];
  contextUsed?: number;
  contextWindow?: number | null;
}): string {
  const parts: string[] = [];
  if (contextWindow && contextUsed) {
    parts.push(`${Math.min(100, Math.round((contextUsed / contextWindow) * 100))}% context`);
  }
  // Threads are the scarce, high-signal item, so they get the rest of the line when there are
  // any. The intake tally stands in when there are none — never alongside them, because "3 files
  // read · 2 read" is two different meanings of the same word in one glance.
  if (threads.length) {
    const written = threads.filter((t) => t.written).length;
    const read = threads.filter((t) => t.read && !t.written).length;
    if (read) parts.push(`${read} read`);
    if (written) parts.push(`${written} updated`);
  } else if (activity.length) {
    parts.push(activity[0].text);
  }
  return parts.join(" · ");
}

let railSectionSeq = 0;

/**
 * A collapsible rail section.
 *
 * `summary` is the one-line glance shown in the header — the thing that makes a COLLAPSED
 * section still worth having. Access had this and the others did not, so shutting Progress or
 * Artifacts turned them into two words and a chevron.
 *
 * Disclosure semantics are explicit (`aria-expanded` + `aria-controls`) and the heading is a
 * real `h2`, so the rail is navigable by heading rather than being one undifferentiated region.
 */
function RailSection({
  title,
  summary,
  open,
  onToggle,
  children,
  action,
}: {
  title: string;
  summary?: ReactNode;
  open: boolean;
  onToggle: () => void;
  children: ReactNode;
  action?: ReactNode;
}) {
  const id = useRef(`rail-sect-${++railSectionSeq}`).current;
  return (
    <section className="rail-section" aria-labelledby={`${id}-title`}>
      <div className="rail-section-head">
        <h2 id={`${id}-title`} className="contents">
          <button
            className="rail-section-toggle"
            onClick={onToggle}
            aria-expanded={open}
            aria-controls={`${id}-body`}
          >
            <Icon name={open ? "chevronDown" : "chevronRight"} size={14} className="rail-chev" />
            <span>{title}</span>
            {summary != null && <span className="rail-section-sum">{summary}</span>}
          </button>
        </h2>
        {action}
      </div>
      <div id={`${id}-body`} className="rail-section-body" hidden={!open}>
        {open && children}
      </div>
    </section>
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
