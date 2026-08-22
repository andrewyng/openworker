import { Fragment, useEffect, useState } from "react";
import { getConnectors, getSessionConnections, type Persona } from "../api";
import type { Attachment } from "../types";
import { ConnectorIcon } from "../connectors/ConnectorIcon";
import { indexConnectors, visualFor, type ConnectorMap } from "../connectors/visuals";
import { useRoots } from "../useRoots";
import { AddFolderForm } from "./AddFolderForm";
import { PersonaGlyph } from "./personaIcon";
import { introFor } from "../personaStyle";

// Empty-state for a fresh session (§27): a greeting, a few concrete template tasks, and the
// composer — nothing else. Each task carries its own setup: no icon tiles (the title is the row),
// connector dots on the sub-line (brand color = connected and enabled for this session, grayscale
// = not — §23's vocabulary), and sub-line copy that is always the task's OUTCOME, never connection
// state. Sources ready → "Start →" on hover, click prefills the composer. Not ready →
// "Configure ›" always visible (for a gated row the setup action IS the row's meaning), opening
// the §23 Session settings drawer — no second setup surface here.
//
// The rows belong to the PERSONA, not to the screen (personaStyle.ts): a manifest's `intro:`
// block owns them, otherwise the persona's family supplies them. Before that, every persona got
// the default coworker's three tasks — folder / HubSpot / GitHub→Slack — which made no sense for
// a code Builder or a research briefer.

// The pseudo-source meaning "a shared directory": the only requirement not satisfiable from the
// connections drawer, so it gets the inline add-folder flow instead of "Configure ›".
const FOLDER = "folder";

export function SessionIntro({
  sessionId,
  persona,
  personaId,
  onOpenSessionSettings,
  onPrefill,
}: {
  sessionId: string;
  // The active persona — its manifest supplies the greeting, lede and tasks. Undefined while the
  // persona list is still loading; `personaId` keeps the built-in defaults right in the meantime.
  persona?: Persona;
  personaId: string;
  // Opens the §23 Session settings drawer (sources section) — the gated rows' Configure target.
  onOpenSessionSettings: () => void;
  onPrefill: (text: string, attachments?: Attachment[]) => void;
}) {
  const { roots, busy, error, addRoot } = useRoots(sessionId);
  const [live, setLive] = useState<Set<string>>(new Set());
  const [byName, setByName] = useState<ConnectorMap>({});
  const [addingFolder, setAddingFolder] = useState<string | null>(null);
  const intro = introFor(persona, personaId);

  useEffect(() => {
    // Live = what this session can touch right now (connected AND not muted here) — the same
    // truth the §23 glance renders, so the dots here can never disagree with the row above.
    getSessionConnections(sessionId)
      .then((c) => setLive(new Set(c.connected.filter((x) => x.enabled).map((x) => x.connector))))
      .catch(() => {});
    getConnectors()
      .then((list) => setByName(indexConnectors(list)))
      .catch(() => {});
  }, [sessionId]);

  const shared = roots.filter((r) => !r.primary);

  const dot = (name: string, on: boolean) => (
    <span className={"task-dot" + (on ? "" : " off")} key={name}>
      <ConnectorIcon connector={visualFor(name, "connector", byName)} size={12} />
    </span>
  );

  return (
    <div className="intro">
      <h1 className="greeting">
        <span className="mark">
          {/* The persona's own glyph, in the persona's accent — the greeting is the first place
              a session says who is answering. */}
          <PersonaGlyph icon={persona?.icon} family={persona?.family} size={22} />
        </span>
        {intro.greeting}
      </h1>
      {intro.lede && <p className="intro-lede">{intro.lede}</p>}

      <div className="intro-tasks">
        {intro.starters.map((task) => {
          // A folder requirement is met by any shared root; everything else is a connector that
          // has to be live for THIS session.
          const needsFolder = task.requires.includes(FOLDER);
          const connectors = task.requires.filter((r) => r !== FOLDER);
          const ready =
            (!needsFolder || shared.length > 0) && connectors.every((c) => live.has(c));
          const openForm = addingFolder === task.key;
          return (
            // A Fragment, not a wrapper element: .task-card's first-child border and
            // nth-of-type stagger are scoped to .intro-tasks' own children.
            <Fragment key={task.key}>
              <button
                className={"task-card" + (ready ? "" : " gated")}
                data-testid={`intro-task-${task.key}`}
                onClick={() => {
                  if (ready) return onPrefill(task.prompt);
                  // Missing folder → share one right here; missing connector → the drawer owns it.
                  if (needsFolder && shared.length === 0 && connectors.every((c) => live.has(c))) {
                    setAddingFolder((v) => (v === task.key ? null : task.key));
                  } else {
                    onOpenSessionSettings();
                  }
                }}
              >
                <span className="task-card-body">
                  <span className="task-card-title">{task.title}</span>
                  {(task.sub || connectors.length > 0) && (
                    <span className="task-card-sub">
                      {connectors.map((c) => dot(c, live.has(c)))}
                      {task.sub}
                    </span>
                  )}
                </span>
                <span className="task-card-act">
                  {ready ? "Start →" : needsFolder && shared.length === 0 ? "Pick a folder →" : "Configure ›"}
                </span>
              </button>
              {openForm && (
                <div className="intro-addfolder">
                  <AddFolderForm
                    startOpen
                    busy={busy}
                    onAdd={async (path, writable) => {
                      const ok = await addRoot(path, writable);
                      if (ok !== false) onPrefill(task.prompt);
                      return ok;
                    }}
                    onDismiss={() => setAddingFolder(null)}
                  />
                  {error && <div className="roots-err">{error}</div>}
                </div>
              )}
            </Fragment>
          );
        })}
      </div>
    </div>
  );
}
