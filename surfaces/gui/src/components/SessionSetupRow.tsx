import { useState } from "react";
import { useTranslation } from "react-i18next";
import { getRecentWorkspaces, openWorkspace, type Persona, type RecentWorkspace } from "../api";
import { chooseFolder } from "../tauri";
import { fullPersonaName } from "../personaScope";
import { baseName } from "../paths";
import { Icon } from "./Icon";

// UX-029: the session-setup row — per-SESSION choices (coworker + folder) in their own
// quiet chip row above the composer, a different species from the per-MESSAGE controls
// inside it. Rendered only before the first message; after that the whole row leaves and
// its facts move to the session header. Chips are borderless (position, not a border,
// marks them as different) and the coworker chip carries no icon — both owner calls.

interface Props {
  personas: Persona[] | null;
  agent: string;
  // The folder chip renders only for personas that work in a folder (Chat hides it).
  showFolder: boolean;
  // The user's explicit folder pick for this draft, if any (never a temporary dir's path).
  folderName: string | null;
  onPickCoworker: (id: string) => void;
  onPickFolder: (path: string, branch?: string | null) => void;
  onManage: () => void;
  // Sharing v1 (OPE-7): the quick door to the import/browse screen — one row, so the
  // picker itself never grows beyond the user's own coworkers.
  onImport: () => void;
}

export function SessionSetupRow(props: Props) {
  const { t } = useTranslation();
  const [openMenu, setOpenMenu] = useState<"coworker" | "folder" | null>(null);
  const [recents, setRecents] = useState<RecentWorkspace[] | null>(null);
  const [error, setError] = useState("");
  const personas = (props.personas || []).filter((p) => p.enabled);
  const current = personas.find((p) => p.id === props.agent);

  const toggle = (menu: "coworker" | "folder") => {
    setError("");
    if (menu === "folder" && openMenu !== "folder") {
      getRecentWorkspaces().then(setRecents).catch(() => setRecents([]));
    }
    setOpenMenu((cur) => (cur === menu ? null : menu));
  };

  const pickFolder = async (path: string) => {
    const res = await openWorkspace(path);
    if (!res.ok) {
      setError(res.error || t("folder_gate.open_error"));
      return;
    }
    setOpenMenu(null);
    props.onPickFolder(res.path, res.git_branch);
  };

  const browse = async () => {
    const picked = await chooseFolder();
    if (picked) await pickFolder(picked);
  };

  const chip =
    "relative inline-flex items-center gap-1.5 px-2 py-1.5 rounded-lg text-[13px] text-muted hover:text-ink hover:bg-paper cursor-pointer select-none whitespace-nowrap";

  return (
    <div className="max-w-3xl mx-auto mb-1.5 px-1 flex items-center gap-1.5" data-testid="setup-row">
      {openMenu && <div className="fixed inset-0 z-20" onClick={() => setOpenMenu(null)} />}

      {/* Coworker chip — name only, no icon (owner call). */}
      <div className="relative">
        <button className={chip} data-testid="coworker-chip" onClick={() => toggle("coworker")}>
          {fullPersonaName(current?.name, props.agent)}
          <Icon name="chevronDown" size={12} className="text-faint" />
        </button>
        {openMenu === "coworker" && (
          <div className="setup-menu absolute bottom-full mb-1.5 left-0 z-30 w-[320px] bg-panel border border-line rounded-xl2 shadow-xl p-1">
            {personas.map((p) => (
              <button
                key={p.id}
                className={
                  "w-full text-left px-2.5 py-2 rounded-lg hover:bg-paper " +
                  (p.id === props.agent ? "bg-accentSoft/50" : "")
                }
                onClick={() => {
                  setOpenMenu(null);
                  props.onPickCoworker(p.id);
                }}
              >
                <span className="block text-[13px] font-medium text-ink">
                  {fullPersonaName(p.name, p.id)}
                </span>
                {p.tagline && (
                  <span className="block text-[12px] text-muted truncate">{p.tagline}</span>
                )}
              </button>
            ))}
            <div className="border-t border-line mt-1 pt-1">
              <button
                className="w-full text-left px-2.5 py-1.5 rounded-lg hover:bg-paper text-[12px] text-accent"
                data-testid="import-coworker"
                onClick={() => {
                  setOpenMenu(null);
                  props.onImport();
                }}
              >
                {t("setup.import_coworker")}
              </button>
              <button
                className="w-full text-left px-2.5 py-1.5 rounded-lg hover:bg-paper text-[12px] text-accent"
                onClick={() => {
                  setOpenMenu(null);
                  props.onManage();
                }}
              >
                {t("setup.manage_coworkers")}
              </button>
            </div>
          </div>
        )}
      </div>

      {/* Folder chip — only for personas that work in a folder. */}
      {props.showFolder && (
        <div className="relative">
          <button className={chip} data-testid="folder-chip" onClick={() => toggle("folder")}>
            <Icon name="folder" size={13} />
            <span className="max-w-[220px] truncate">
              {props.folderName || t("setup.choose_folder")}
            </span>
            <Icon name="chevronDown" size={12} className="text-faint" />
          </button>
          {openMenu === "folder" && (
            <div className="setup-menu absolute bottom-full mb-1.5 left-0 z-30 w-[280px] bg-panel border border-line rounded-xl2 shadow-xl p-1">
              {(recents || [])
                .filter((w) => w.exists)
                .slice(0, 5)
                .map((w) => (
                  <button
                    key={w.path}
                    className="w-full text-left flex items-start gap-2.5 px-2.5 py-2 rounded-lg hover:bg-paper"
                    onClick={() => void pickFolder(w.path)}
                    title={w.path}
                  >
                    <Icon name="folder" size={13} className="mt-0.5 shrink-0 text-muted" />
                    <span className="min-w-0">
                      <span className="block text-[13px] font-medium text-ink truncate">{baseName(w.path)}</span>
                      <span className="block text-[12px] text-faint truncate">{w.path}</span>
                    </span>
                  </button>
                ))}
              <div className={(recents || []).some((w) => w.exists) ? "border-t border-line mt-1 pt-1" : ""}>
                <button
                  className="w-full text-left px-2.5 py-1.5 rounded-lg hover:bg-paper text-[12px] text-accent"
                  onClick={() => void browse()}
                >
                  {props.folderName ? t("setup.choose_another_folder") : t("setup.choose_a_folder")}
                </button>
              </div>
              {error && <div className="px-2.5 py-1 text-[12px] text-warnInk">{error}</div>}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
