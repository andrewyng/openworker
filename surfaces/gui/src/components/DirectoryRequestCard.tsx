import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { Item } from "../types";
import { chooseFolder } from "../tauri";
import { Icon } from "./Icon";

type DirReqItem = Extract<Item, { kind: "dirreq" }>;

// The agent asked (via request_directory) for access to a folder. The user picks/confirms a path
// and access level, or declines — mirroring the approval card, shown in the composer head.
export function DirectoryRequestCard({
  item,
  onRespond,
}: {
  item: DirReqItem;
  onRespond: (granted: boolean, path?: string, writable?: boolean) => void;
}) {
  const { t } = useTranslation();
  const [path, setPath] = useState(item.path || "");
  const [writable, setWritable] = useState(!!item.writable);

  const browse = async () => {
    const picked = await chooseFolder();
    if (picked) setPath(picked);
  };

  return (
    <div className="dirreq-card">
      <div className="dirreq-head">
        <Icon name="folderPlus" size={16} className="ico" />
        <span>{t("dirReq.requestingAccess")}</span>
      </div>
      {item.reason && <div className="dirreq-reason">“{item.reason}”</div>}
      <div className="dirreq-pathrow">
        <input
          className="dirreq-path"
          placeholder={t("dirReq.chooseOrPaste")}
          value={path}
          onChange={(e) => setPath(e.target.value)}
        />
        <button className="btn icon-only" onClick={browse} title={t("dirReq.chooseLocation")} aria-label={t("dirReq.chooseLocation")}>
          <Icon name="folder" size={15} />
        </button>
      </div>
      <div className="dirreq-actions">
        <label className="dirreq-access">
          <input type="checkbox" checked={writable} onChange={(e) => setWritable(e.target.checked)} />
          {t("dirReq.allowWriting")}
        </label>
        <span className="spacer" />
        <button className="btn" onClick={() => onRespond(false)}>
          {t("dirReq.decline")}
        </button>
        <button className="btn primary" disabled={!path.trim()} onClick={() => onRespond(true, path.trim(), writable)}>
          {t("dirReq.grantAccess")}
        </button>
      </div>
    </div>
  );
}
