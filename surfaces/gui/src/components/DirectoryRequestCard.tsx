import { useState } from "react";
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
        <span>agent 正在请求访问某个文件夹</span>
      </div>
      {item.reason && <div className="dirreq-reason">“{item.reason}”</div>}
      <div className="dirreq-pathrow">
        <input
          className="dirreq-path"
          placeholder="选择或粘贴文件夹路径…"
          value={path}
          onChange={(e) => setPath(e.target.value)}
        />
        <button className="btn icon-only" onClick={browse} title="选择位置" aria-label="选择位置">
          <Icon name="folder" size={15} />
        </button>
      </div>
      <div className="dirreq-actions">
        <label className="dirreq-access">
          <input type="checkbox" checked={writable} onChange={(e) => setWritable(e.target.checked)} />
          允许写入（读写）
        </label>
        <span className="spacer" />
        <button className="btn" onClick={() => onRespond(false)}>
          拒绝
        </button>
        <button className="btn primary" disabled={!path.trim()} onClick={() => onRespond(true, path.trim(), writable)}>
          授予访问权限
        </button>
      </div>
    </div>
  );
}
