// A folder on a machine, for the setup row's "Choose folder" when the session runs on one
// (UX ruling 2026-09-29, mock ux-improvements/mocks/coworker-picker-groups.html): the Mac's
// file picker cannot see the machine, so the user types a path, Check asks the machine
// whether it is a folder (and its git branch), and "Use this folder" is enabled only after a
// check passes. Recent folders on that machine are one click. No browsing.
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { getRecentWorkspaces, openWorkspace, type Machine, type RecentWorkspace } from "../api";
import { Icon } from "./Icon";

interface Props {
  coworkerName: string;
  machine: Machine;
  onPick: (path: string, branch?: string | null) => void;
  onCancel: () => void;
}

type Check =
  | { state: "idle" }
  | { state: "checking" }
  | { state: "ok"; path: string; branch: string | null }
  | { state: "bad"; path: string; error: string };

function baseName(p: string): string {
  const parts = p.replace(/[\\/]+$/, "").split(/[\\/]/);
  return parts[parts.length - 1] || p;
}

export function MachineFolderDialog({ coworkerName, machine, onPick, onCancel }: Props) {
  const { t } = useTranslation();
  const [recents, setRecents] = useState<RecentWorkspace[]>([]);
  const [typed, setTyped] = useState("");
  const [check, setCheck] = useState<Check>({ state: "idle" });

  useEffect(() => {
    getRecentWorkspaces(machine.id).then(setRecents).catch(() => {});
  }, [machine.id]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCancel();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel]);

  // The machine answers: openWorkspace with its id validates the path there.
  const runCheck = async (path: string): Promise<Check> => {
    setCheck({ state: "checking" });
    const res = await openWorkspace(path, false, machine.id);
    const next: Check = res.ok
      ? { state: "ok", path: res.path, branch: res.git_branch ?? null }
      : { state: "bad", path, error: res.error || t("onmachine.folder.not_found", { path, machine: machine.name }) };
    setCheck(next);
    return next;
  };

  const useChecked = () => {
    if (check.state === "ok") onPick(check.path, check.branch);
  };

  // A recent folder is checked and used in one step.
  const pickRecent = async (path: string) => {
    const next = await runCheck(path);
    if (next.state === "ok") onPick(next.path, next.branch);
  };

  const fieldTone =
    check.state === "ok" ? " border-ok" : check.state === "bad" ? " border-danger" : " border-line focus:border-accent";

  return (
    <div className="gate-overlay" onClick={onCancel}>
      <div
        className="w-[410px] bg-panel border border-line rounded-xl2 shadow-2xl p-[18px]"
        data-testid="machine-folder-dialog"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="text-body font-semibold text-ink mb-1">
          {t("onmachine.folder.draft_title", { name: coworkerName, machine: machine.name })}
        </h3>
        <p className="text-ui text-muted mb-3">{t("onmachine.folder.draft_sub", { machine: machine.name })}</p>
        {recents
          .filter((w) => w.exists)
          .slice(0, 4)
          .map((w) => (
            <button
              key={w.path}
              className="w-full flex items-center gap-2.5 px-2.5 py-2 mb-1.5 rounded-lg border border-line hover:border-lineStrong hover:bg-paper text-left"
              onClick={() => void pickRecent(w.path)}
              title={w.path}
              data-testid="machine-folder-recent"
            >
              <Icon name="folder" size={13} className="shrink-0 text-muted" />
              <span className="text-ui text-ink truncate">{baseName(w.path)}</span>
              <span className="ml-auto text-meta text-faint truncate max-w-[45%]">{w.path}</span>
            </button>
          ))}
        <form
          className="flex gap-2 mt-1"
          onSubmit={(e) => {
            e.preventDefault();
            if (typed.trim()) void runCheck(typed.trim());
          }}
        >
          <input
            className={"flex-1 min-w-0 px-2.5 py-2 rounded-lg border bg-paper font-mono text-meta text-ink outline-none" + fieldTone}
            placeholder={t("onmachine.folder.path_placeholder", { machine: machine.name })}
            value={typed}
            onChange={(e) => {
              setTyped(e.target.value);
              if (check.state !== "idle") setCheck({ state: "idle" }); // a new path needs a new check
            }}
            data-testid="machine-folder-input"
            autoFocus
          />
          <button
            type="submit"
            className="text-ui px-3 py-2 rounded-lg border border-lineStrong text-ink hover:bg-paper disabled:opacity-40"
            disabled={!typed.trim() || check.state === "checking"}
            data-testid="machine-folder-check"
          >
            {check.state === "checking" ? t("onmachine.folder.checking") : t("onmachine.folder.check")}
          </button>
        </form>
        <p
          className={"text-meta mt-2 min-h-[18px] " + (check.state === "ok" ? "text-ok" : check.state === "bad" ? "text-danger" : "text-muted")}
          data-testid="machine-folder-status"
        >
          {check.state === "ok"
            ? check.branch
              ? t("onmachine.folder.found_branch", { machine: machine.name, branch: check.branch })
              : t("onmachine.folder.found", { machine: machine.name })
            : check.state === "bad"
              ? check.error
              : ""}
        </p>
        <div className="flex gap-2 mt-3 justify-end">
          <button className="text-ui px-3 py-2 rounded-lg border border-lineStrong text-ink hover:bg-paper" onClick={onCancel}>
            {t("folder_gate.cancel")}
          </button>
          <button
            className="text-ui px-3 py-2 rounded-lg bg-accent text-white hover:opacity-90 disabled:opacity-40"
            disabled={check.state !== "ok"}
            onClick={useChecked}
            data-testid="machine-folder-use"
          >
            {t("onmachine.folder.use")}
          </button>
        </div>
      </div>
    </div>
  );
}
