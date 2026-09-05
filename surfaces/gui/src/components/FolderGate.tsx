import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { getRecentWorkspaces, openWorkspace, type RecentWorkspace } from "../api";
import { chooseFolder } from "../tauri";

// The mandatory workspace picker for project-scoped personas. Deliberately no
// "switch persona" escape hatch: if a persona needs a folder, the choice here is
// pick one or cancel — offering Chat as an exit undermined the persona the user
// just chose (owner call, 2026-07-03).
interface Props {
  onChoose: (path: string, branch?: string | null) => void;
  onCancel?: () => void; // present when changing folder mid-session
  create?: boolean; // "New project" mode: create the folder if missing
}

export function FolderGate({ onChoose, onCancel, create }: Props) {
  const [recents, setRecents] = useState<RecentWorkspace[]>([]);
  const [path, setPath] = useState("");
  const [error, setError] = useState("");
  const { t } = useTranslation();

  useEffect(() => {
    getRecentWorkspaces().then(setRecents).catch(() => {});
  }, []);

  const open = async (p: string, doCreate = false) => {
    setError("");
    const res = await openWorkspace(p.trim(), doCreate);
    if (res.ok) onChoose(res.path, res.git_branch);
    else setError(res.error || t("folder_gate.open_error"));
  };

  const browse = async () => {
    const picked = await chooseFolder();
    if (picked) {
      setPath(picked);
      open(picked, create); // a picked folder already exists; create flag is harmless
    }
  };

  return (
    <div className="gate-overlay">
      <div className="gate">
        <div className="gate-mark">✦</div>
        <h2>{create ? t("sidebar.new_project") : t("folder_gate.choose_folder")}</h2>
        <p className="gate-sub">
          {create
            ? t("folder_gate.create_sub")
            : t("folder_gate.choose_sub")}
        </p>

        <div className="gate-input">
          <input
            placeholder={t("folder_gate.path_placeholder")}
            value={path}
            onChange={(e) => setPath(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && open(path, create)}
            autoFocus
          />
          <button className="btn" onClick={browse} title={t("folder_gate.pick_folder")}>
            {t("folder_gate.browse")}
          </button>
          <button className="btn primary" onClick={() => open(path, create)} disabled={!path.trim()}>
            {create ? t("folder_gate.create") : t("folder_gate.open")}
          </button>
        </div>
        {error && <div className="gate-error">{error}</div>}

        {recents.length > 0 && (
          <>
            <div className="gate-label">{t("sidebar.recent")}</div>
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
              {t("folder_gate.cancel")}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
