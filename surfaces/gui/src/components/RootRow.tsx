import { useTranslation } from "react-i18next";
import type { RootInfo } from "../api";
import { Icon } from "./Icon";
import { baseName } from "../paths";

// One directory row, shared by the composer popover and the session start panel. The primary is the
// session's bound workspace — the repo/folder for Code/Ops (shown by name), or a throwaway scratch
// for Cowork (shown as "Temporary folder"). It's always read-write and can't be removed.
export function RootRow({
  root,
  busy,
  scratchPrimary,
  branch,
  onToggle,
  onRemove,
}: {
  root: RootInfo;
  busy?: boolean;
  scratchPrimary?: boolean;
  // The workspace's git branch — shown on the primary row (drawer's Working directories, §23).
  branch?: string | null;
  onToggle: (r: RootInfo) => void;
  onRemove: (path: string) => void;
}) {
  const { t } = useTranslation();
  const label = root.primary
    ? scratchPrimary
      ? t("root.temporary_space")
      : baseName(root.path)
    : root.label;
  return (
    <div className={"root-row" + (root.exists ? "" : " missing")}>
      <Icon name="folder" size={14} className="root-ico" />
      <span className="root-text" title={root.path}>
        <span className="root-label">
          {label}
          {root.primary && !scratchPrimary && <span className="root-tag"> {t("root.main")}</span>}
          {branch && (
            <span className="root-tag root-branch">
              {" "}
              <Icon name="branch" size={11} /> {branch}
            </span>
          )}
        </span>
        <span className="root-path">{root.path}</span>
      </span>
      {!root.exists && <span className="root-tag warn">{t("root.missing")}</span>}
      <button
        className={"root-access" + (root.writable ? " rw" : " ro")}
        onClick={() => onToggle(root)}
        disabled={busy || root.primary}
        title={root.primary ? t("root.primary_always_rw") : t("root.toggle_rw")}
      >
        {root.writable ? t("root.read_write") : t("root.read_only")}
      </button>
      {!root.primary && (
        <button className="root-x" onClick={() => onRemove(root.path)} disabled={busy} title={t("common.remove")}>
          ×
        </button>
      )}
    </div>
  );
}
