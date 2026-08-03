import { useEffect, useState } from "react";
import { imeComposing } from "../ime";
import {
  getRecentWorkspaces,
  openWorkspace,
  type ProjectInfo,
  type RecentWorkspace,
} from "../api";
import { chooseFolder } from "../tauri";
import { useI18n } from "../i18n";

// The mandatory workspace picker for project-scoped personas. Deliberately no
// "switch persona" escape hatch: if a persona needs a folder, the choice here is
// pick one or cancel — offering Chat as an exit undermined the persona the user
// just chose (owner call, 2026-07-03).
//
// The new-session flow also asks whether the session belongs to a project (owner
// ask 2026-08-03): plain = regular session (no registration), existing = bind to a
// registered project (the folder locks to the project's path), new = register a new
// project for this folder. The backend auto-binds sessions to a registered project
// by workspace path, so "existing" only needs to pick the right folder.
interface Props {
  onChoose: (path: string, branch?: string | null, name?: string) => void;
  onCancel?: () => void; // present when changing folder mid-session
  projectIndex: ProjectInfo[]; // registered projects, for the assignment dropdown
}

type AssignMode = "plain" | "existing" | "new";

export function FolderGate({ onChoose, onCancel, projectIndex }: Props) {
  const { t } = useI18n();
  const [recents, setRecents] = useState<RecentWorkspace[]>([]);
  const [path, setPath] = useState("");
  const [name, setName] = useState("");
  const [mode, setMode] = useState<AssignMode>("plain");
  const [selected, setSelected] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    getRecentWorkspaces().then(setRecents).catch(() => {});
  }, []);

  const open = async (p: string, doCreate = false) => {
    setError("");
    const res = await openWorkspace(p.trim(), doCreate);
    if (res.ok) onChoose(res.path, res.git_branch, mode === "new" ? name.trim() : undefined);
    else setError(res.error || t("could not open that folder"));
  };

  const browse = async () => {
    const picked = await chooseFolder();
    if (picked) {
      setPath(picked);
      open(picked, mode === "new"); // "new" may point at a not-yet-created folder
    }
  };

  const project = projectIndex.find((p) => p.project_id === selected);
  const lockedPath = mode === "existing" && project ? project.path : "";

  return (
    <div className="gate-overlay">
      <div className="gate">
        <div className="gate-mark">✦</div>
        <h2>{t("Choose a workspace")}</h2>
        <p className="gate-sub">{t("This coworker needs a workspace to read, edit, and run in.")}</p>

        <div className="gate-label">{t("Assign to a project (optional)")}</div>
        <div className="gate-modes">
          <label className="gate-mode">
            <input
              type="radio"
              name="assign"
              checked={mode === "plain"}
              onChange={() => setMode("plain")}
            />
            <span>{t("Regular session (no project)")}</span>
          </label>
          <label className="gate-mode">
            <input
              type="radio"
              name="assign"
              checked={mode === "existing"}
              onChange={() => {
                setMode("existing");
                setSelected("");
              }}
            />
            <span>{t("Belongs to an existing project")}</span>
          </label>
          <label className="gate-mode">
            <input
              type="radio"
              name="assign"
              checked={mode === "new"}
              onChange={() => setMode("new")}
            />
            <span>{t("New project")}</span>
          </label>
        </div>

        {mode === "existing" && (
          <div className="gate-input">
            <select
              className="gate-select"
              data-testid="gate-project-select"
              value={selected}
              onChange={(e) => {
                setSelected(e.target.value);
                const p = projectIndex.find((pp) => pp.project_id === e.target.value);
                if (p) setPath(p.path);
              }}
            >
              <option value="">{t("Select a project…")}</option>
              {projectIndex.map((p) => (
                <option key={p.project_id} value={p.project_id}>
                  {p.name} — {p.path}
                </option>
              ))}
            </select>
          </div>
        )}

        {mode === "new" && (
          <div className="gate-input">
            <input
              placeholder={t("Project name")}
              value={name}
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => !imeComposing(e) && e.key === "Enter" && open(path, true)}
              autoFocus
            />
          </div>
        )}

        <div className="gate-input">
          <input
            placeholder="/path/to/your/project"
            value={lockedPath || path}
            readOnly={mode === "existing"}
            onChange={(e) => setPath(e.target.value)}
            onKeyDown={(e) => !imeComposing(e) && e.key === "Enter" && open(path, mode === "new")}
          />
          <button className="btn" onClick={browse} title={t("Pick a folder")}>
            Browse…
          </button>
          <button
            className="btn primary"
            onClick={() => open(path, mode === "new")}
            disabled={!path.trim() || (mode === "new" && !name.trim())}
          >
            {mode === "new" ? t("Create") : t("Open")}
          </button>
        </div>
        {error && <div className="gate-error">{error}</div>}

        {recents.length > 0 && (
          <>
            <div className="gate-label">{t("Recent")}</div>
            <div className="gate-recents">
              {recents.map((w) => (
                <div className="gate-recent" key={w.path} onClick={() => open(w.path)} title={w.path}>
                  <span className="folder">📁 {w.name}</span>
                  <span className="dim">{w.path}</span>
                </div>
              ))}
            </div>
          </>
        )}

        {onCancel && (
          <div className="gate-foot">
            <button className="btn gate-cancel" onClick={onCancel}>
              {t("Cancel")}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
