import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { getRecentWorkspaces, openWorkspace, type Machine, type RecentWorkspace } from "../api";
import { chooseFolder } from "../tauri";
import { baseName } from "../paths";
import { Icon } from "./Icon";

// UX-029: folder enforcement AT SEND, not at session start. A code-family coworker with no
// folder picked gets this dialog when the user hits send; the message goes out the moment a
// choice lands (recents / native picker / temporary folder). Escape restores the draft.

interface Props {
  coworkerName: string;
  // Remote homes: set when the draft runs on a machine — recents and path
  // validation come from THAT machine, the native picker (which browses this
  // computer) is hidden, and a typed path replaces it.
  machine?: Machine | null;
  onPick: (path: string, branch?: string | null) => void;
  onTemp: () => void;
  onCancel: () => void;
}

export function SendFolderDialog({ coworkerName, machine, onPick, onTemp, onCancel }: Props) {
  const { t } = useTranslation();
  const [recents, setRecents] = useState<RecentWorkspace[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [typedPath, setTypedPath] = useState("");

  useEffect(() => {
    getRecentWorkspaces(machine?.id).then(setRecents).catch(() => {});
  }, [machine?.id]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCancel();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel]);

  const pick = async (path: string) => {
    setError("");
    const res = await openWorkspace(path, false, machine?.id);
    if (res.ok) onPick(res.path, res.git_branch);
    else setError(res.error || t("folder_gate.open_error"));
  };

  const browse = async () => {
    const picked = await chooseFolder();
    if (picked) await pick(picked);
  };

  return (
    <div className="gate-overlay" onClick={onCancel}>
      <div
        className="w-[410px] bg-panel border border-line rounded-xl2 shadow-2xl p-[18px]"
        data-testid="send-folder-dialog"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="text-body font-semibold text-ink mb-1">
          {t("folder_gate.where_work", { name: coworkerName })}
        </h3>
        <p className="text-ui text-muted mb-3">
          {machine
            ? t("onmachine.folder.send_sub", { machine: machine.name })
            : t("folder_gate.send_sub")}
        </p>
        {recents
          .filter((w) => w.exists)
          .slice(0, 4)
          .map((w) => (
            <button
              key={w.path}
              className="w-full flex items-center gap-2.5 px-2.5 py-2 mb-1.5 rounded-lg border border-line hover:border-lineStrong hover:bg-paper text-left"
              onClick={() => void pick(w.path)}
              title={w.path}
            >
              <Icon name="folder" size={13} className="shrink-0 text-muted" />
              <span className="text-ui text-ink truncate">{baseName(w.path)}</span>
              <span className="ml-auto text-meta text-faint truncate max-w-[45%]">{w.path}</span>
            </button>
          ))}
        {machine && (
          <form
            className="flex gap-2 mt-1"
            onSubmit={(e) => {
              e.preventDefault();
              if (typedPath.trim()) void pick(typedPath.trim());
            }}
          >
            <input
              className="flex-1 min-w-0 px-2.5 py-2 rounded-lg border border-line bg-paper font-mono text-meta text-ink outline-none focus:border-accent"
              placeholder={t("onmachine.folder.path_placeholder", { machine: machine.name })}
              value={typedPath}
              onChange={(e) => setTypedPath(e.target.value)}
              data-testid="remote-path-input"
              autoFocus
            />
            <button
              type="submit"
              className="text-ui px-3 py-2 rounded-lg border border-lineStrong text-ink hover:bg-paper disabled:opacity-40"
              disabled={!typedPath.trim() || busy}
              data-testid="remote-path-open"
            >
              {t("folder_gate.open")}
            </button>
          </form>
        )}
        <div className="flex gap-2 mt-3">
          {/* The native picker browses THIS computer — never shown for a remote draft. */}
          {!machine && (
            <button
              className="flex-1 text-center text-ui px-2.5 py-2 rounded-lg border border-lineStrong text-ink hover:bg-paper"
              onClick={() => void browse()}
              disabled={busy}
            >
              {t("folder_gate.choose_a_folder")}
            </button>
          )}
          <button
            className="flex-1 text-center text-ui px-2.5 py-2 rounded-lg bg-accent text-white font-semibold hover:opacity-95"
            data-testid="start-temp-folder"
            onClick={() => {
              if (busy) return;
              setBusy(true);
              onTemp();
            }}
            disabled={busy}
          >
            {machine ? t("onmachine.folder.use_temp", { machine: machine.name }) : t("folder_gate.use_temp")}
          </button>
        </div>
        {error && <div className="mt-2 text-meta text-warnInk">{error}</div>}
        <p className="text-label text-faint mt-2.5">
          {machine
            ? t("onmachine.folder.temp_note", { machine: machine.name })
            : t("folder_gate.temp_note")}
        </p>
      </div>
    </div>
  );
}
