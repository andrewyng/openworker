import { useState } from "react";
import {
  disconnectHubSpotPortal,
  setHubSpotDefaultPortal,
  setHubSpotHiddenFields,
  type HubSpotPortal,
} from "../../api";
import { ConnectorBadge } from "../../connectors/ConnectorIcon";
import { AddConnectionModal } from "./AddConnectionModal";
import type { DetailProps } from "./ConnectorsSection";
import { ToolsDisclosure } from "./ToolsDisclosure";
import { FOOT, GRP, GRP_H, PILL_ACCENT, ROW, TAG_ACCENT, TAG_QUIET, TAG_WARN, XBTN } from "./ui";

// The HubSpot detail page (UX-DECISIONS §21): connected portals (multi-portal,
// Default/Sandbox tags, the consent tier granted at connect) + Access & privacy
// (hidden-fields denylist — hides data from the MODEL; HubSpot permission sets
// are the ACL against humans) + collapsed Tools. Adding a portal goes through
// the ONE entry point: header button → modal (One click w/ access radios | Manual).

const LABEL = "text-[12.5px] text-muted w-24 shrink-0";

export function HubSpotDetail({ c, cloud, slack: _slack, onChanged }: DetailProps) {
  const [adding, setAdding] = useState(false);
  const portals = c.portals ?? [];

  return (
    <div data-testid="hubspot-detail">
      <div className="flex items-center gap-3.5 mb-5">
        <ConnectorBadge connector={c} size={44} title="HubSpot" />
        <div className="min-w-0 flex-1">
          <h2 className="text-[20px] font-semibold tracking-tight leading-tight">HubSpot</h2>
          <div className="text-[12.5px] text-muted flex items-center gap-1.5">
            {c.connected ? (
              <>
                <span className="w-2 h-2 rounded-full bg-ok" />
                <span data-testid="hubspot-status">
                  {portals.length} 个门户
                </span>
              </>
            ) : (
              <span>未连接</span>
            )}
          </div>
        </div>
        <button className={PILL_ACCENT} data-testid="add-portal-btn" onClick={() => setAdding(true)}>
          ＋ 添加门户
        </button>
      </div>

      {!c.connected && (
        <div className={GRP}>
          <div className={ROW + " text-[12.5px] text-muted"}>
            连接一个门户 —— 只读还是读写，在授权时选择；无论哪种都不含删除工具。
          </div>
        </div>
      )}

      {portals.length > 0 && (
        <>
          <div className={GRP_H + " !mt-0"}>门户</div>
          <div className={GRP} data-testid="hubspot-portals">
            {portals.map((p) => (
              <PortalRow key={p.hub_id} p={p} onChanged={onChanged} />
            ))}
          </div>
        </>
      )}

      <PrivacyGroup c={c} onChanged={onChanged} />

      <ToolsDisclosure c={c} onChanged={onChanged} />
      <div className={FOOT + " mt-2"}>
        隐藏字段永远不会传给 agent；被剔除的数量会记录在「活动」里。若要限制真人同事能查看的范围，请在已连接的用户上使用 HubSpot 权限集。
      </div>

      {adding && (
        <AddConnectionModal
          c={c}
          cloud={cloud}
          title="添加门户"
          onClose={() => setAdding(false)}
          onChanged={onChanged}
        />
      )}
    </div>
  );
}

function PortalRow({ p, onChanged }: { p: HubSpotPortal; onChanged: () => void }) {
  const [busy, setBusy] = useState(false);
  return (
    <div className={ROW} data-testid={`hubspot-portal-${p.hub_id}`}>
      <span className="min-w-0 flex-1 flex items-center gap-2">
        <span className="text-[13px] font-medium truncate" title={`hub ${p.hub_id}`}>
          {p.name}
        </span>
        {p.default && <span className={TAG_ACCENT}>默认</span>}
        {p.sandbox && <span className={TAG_WARN}>沙盒</span>}
        {p.access && (
          <span className={TAG_QUIET} data-testid={`hubspot-access-tag-${p.hub_id}`}>
            {p.access === "write" ? "读写" : "只读"}
          </span>
        )}
        {!p.managed && <span className={TAG_QUIET}>私有应用</span>}
      </span>
      {!p.default && (
        <button
          className="text-[12px] text-muted hover:text-ink shrink-0"
          data-testid={`hubspot-make-default-${p.hub_id}`}
          onClick={async () => {
            await setHubSpotDefaultPortal(p.hub_id);
            onChanged();
          }}
        >
          设为默认
        </button>
      )}
      <button
        className={XBTN}
        title="断开此门户连接"
        data-testid={`hubspot-disconnect-${p.hub_id}`}
        disabled={busy}
        onClick={async () => {
          setBusy(true);
          await disconnectHubSpotPortal(p.hub_id);
          setBusy(false);
          onChanged();
        }}
      >
        ×
      </button>
    </div>
  );
}

function PrivacyGroup({ c, onChanged }: Pick<DetailProps, "c" | "onChanged">) {
  const fields = c.hidden_fields ?? [];
  const [draft, setDraft] = useState("");
  const save = async (next: string[]) => {
    await setHubSpotHiddenFields(next);
    onChanged();
  };
  const add = async () => {
    const v = draft.trim();
    if (!v) return;
    setDraft("");
    await save([...fields, v]);
  };
  return (
    <>
      <div className={GRP_H}>访问权限与隐私</div>
      <div className={GRP}>
        <div className={ROW} data-testid="hubspot-hidden-fields">
          <span className={LABEL}>隐藏字段</span>
          <span className="min-w-0 flex-1 flex flex-wrap items-center gap-1.5">
            {fields.map((f) => (
              <span
                key={f}
                className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-paper border border-line text-[12.5px] font-mono"
              >
                {f}
                <button className={XBTN} title="移除" onClick={() => save(fields.filter((x) => x !== f))}>
                  ×
                </button>
              </span>
            ))}
            <input
              className="flex-1 min-w-[140px] bg-transparent text-[12.5px] outline-none placeholder:text-faint"
              placeholder="属性名，例如 salary"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") add();
              }}
              onBlur={() => draft.trim() && add()}
            />
          </span>
        </div>
      </div>
      <div className={FOOT}>在所有门户中，从 agent 读取的每一条记录里剔除。</div>
    </>
  );
}
