import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { getRecentWorkspaces, openWorkspace, type RecentWorkspace } from "../api";
import { chooseFolder } from "../tauri";
import { baseName } from "../paths";
import { Icon } from "./Icon";

// UX-029: folder enforcement AT SEND, not at session start. A code-family coworker with no
// folder picked gets this dialog when the user hits send; the message goes out the moment a
// choice lands (recents / native picker / temporary folder). Escape restores the draft.

interface Props {
  coworkerName: string;
  onPick: (path: string, branch?: string | null) => void;
  onTemp: () => void;
  onCancel: () => void;
}

export function SendFolderDialog({ coworkerName, onPick, onTemp, onCancel }: Props) {
  const { t } = useTranslation();
  const [recents, setRecents] = useState<RecentWorkspace[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    getRecentWorkspaces().then(setRecents).catch(() => {});
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCancel();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel]);

  const pick = async (path: string) => {
    setError("");
    const res = await openWorkspace(path);
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
        <h3 className="text-[14px] font-semibold text-ink mb-1">
          {t("folder_gate.where_work", { name: coworkerName })}
        </h3>
        <p className="text-[13px] text-muted mb-3">{t("folder_gate.send_sub")}</p>
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
              <span className="text-[13px] text-ink truncate">{baseName(w.path)}</span>
              <span className="ml-auto text-[12px] text-faint truncate max-w-[45%]">{w.path}</span>
            </button>
          ))}
        <div className="flex gap-2 mt-3">
          <button
            className="flex-1 text-center text-[13px] px-2.5 py-2 rounded-lg border border-lineStrong text-ink hover:bg-paper"
            onClick={() => void browse()}
            disabled={busy}
          >
            {t("folder_gate.choose_a_folder")}
          </button>
          <button
            className="flex-1 text-center text-[13px] px-2.5 py-2 rounded-lg bg-accent text-white font-semibold hover:opacity-95"
            data-testid="start-temp-folder"
            onClick={() => {
              if (busy) return;
              setBusy(true);
              onTemp();
            }}
            disabled={busy}
          >
            {t("folder_gate.use_temp")}
          </button>
        </div>
        {error && <div className="mt-2 text-[12px] text-warnInk">{error}</div>}
        <p className="text-[11px] text-faint mt-2.5">{t("folder_gate.temp_note")}</p>
      </div>
    </div>
  );
}
