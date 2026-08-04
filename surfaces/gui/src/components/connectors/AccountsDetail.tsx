import { useState } from "react";
import {
  connectManaged,
  disconnectAccount,
  setDefaultAccount,
  type AccountRow,
} from "../../api";
import { ConnectorBadge } from "../../connectors/ConnectorIcon";
import { ConnectSetup } from "../ManageTabs";
import type { DetailProps } from "./ConnectorsSection";
import { ToolsDisclosure } from "./ToolsDisclosure";
import { FOOT, GRP, GRP_H, PILL_ACCENT, ROW, TAG_ACCENT, XBTN } from "./ui";

// The generic detail page for multi-account connectors on the accounts layer
// (Notion, Attio, PostHog, Mixpanel, Amplitude, Apollo, Hunter — batch 2).
// Same grammar as the Calendar page: an Accounts group with a Default badge,
// make-default, per-account ×. "＋ Add account" launches managed OAuth when
// the connector has it (and the user is signed in); the manual token form is
// always available underneath — signed out or in, local-only stays first-class.

export function AccountsDetail({ c, cloud, slack: _slack, onChanged }: DetailProps) {
  const [busy, setBusy] = useState(false);
  const [showManual, setShowManual] = useState(false);
  const accounts = (c.accounts ?? []) as AccountRow[];
  const canOneClick = c.managed && !!cloud?.signed_in;

  const addManaged = async () => {
    setBusy(true);
    await connectManaged(c.name); // completes in the system browser; the section poll picks it up
    setTimeout(() => setBusy(false), 2500);
  };

  return (
    <div data-testid="accounts-detail">
      <div className="flex items-center gap-3.5 mb-5">
        <ConnectorBadge connector={c} size={44} title={c.title} />
        <div className="min-w-0 flex-1">
          <h2 className="text-[20px] font-semibold tracking-tight leading-tight">
            {c.title}
          </h2>
          <div className="text-[12.5px] text-muted flex items-center gap-1.5">
            {c.connected ? (
              <>
                <span className="w-2 h-2 rounded-full bg-ok" />
                <span data-testid="accounts-status">
                  {accounts.length} 个账户
                </span>
              </>
            ) : (
              <span>未连接</span>
            )}
          </div>
        </div>
        <button
          className={PILL_ACCENT}
          data-testid="add-account-btn"
          onClick={() => (canOneClick ? addManaged() : setShowManual((v) => !v))}
          disabled={busy}
          title={
            c.managed && !cloud?.signed_in
              ? "登录 OpenWorker Cloud 即可一键连接 —— 或在下方添加 Token"
              : ""
          }
        >
          {busy ? "请查看浏览器…" : "＋ 添加账户"}
        </button>
      </div>

      {accounts.length > 0 && (
        <>
          <div className={GRP_H + " !mt-0"}>账户</div>
          <div className={GRP} data-testid="accounts-group">
            {accounts.map((a) => (
              <Row key={a.account_id} connector={c.name} a={a} onChanged={onChanged} />
            ))}
          </div>
        </>
      )}

      {(showManual || !c.connected) && (
        <>
          <div className={GRP_H + (accounts.length ? "" : " !mt-0")}>
            {c.managed ? "手动添加" : "添加账户"}
          </div>
          <div className={GRP} data-testid="accounts-manual-add">
            <div className="px-1.5 py-1">
              <ConnectSetup
                c={c}
                cloud={cloud}
                onConnected={() => {
                  setShowManual(false);
                  onChanged();
                }}
              />
            </div>
          </div>
        </>
      )}

      <ToolsDisclosure c={c} onChanged={onChanged} />
      <div className={FOOT + " mt-2"}>
        每个账户彼此独立 —— 工具结果和审批都会注明所用的账户。
      </div>
    </div>
  );
}

function Row({
  connector,
  a,
  onChanged,
}: {
  connector: string;
  a: AccountRow;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState(false);
  return (
    <div className={ROW} data-testid={`account-${a.account_id}`}>
      <span className="min-w-0 flex-1 flex items-center gap-2">
        <span className="text-[13px] font-medium truncate">{a.name}</span>
        {a.name !== a.account_id && (
          <span className="text-[11px] text-faint truncate" title={a.account_id}>
            {a.account_id}
          </span>
        )}
        {a.default && <span className={TAG_ACCENT}>默认</span>}
      </span>
      {!a.default && (
        <button
          className="text-[12px] text-muted hover:text-ink shrink-0"
          data-testid={`account-make-default-${a.account_id}`}
          onClick={async () => {
            await setDefaultAccount(connector, a.account_id);
            onChanged();
          }}
        >
          设为默认
        </button>
      )}
      <button
        className={XBTN}
        title="断开此账户连接"
        data-testid={`account-disconnect-${a.account_id}`}
        disabled={busy}
        onClick={async () => {
          setBusy(true);
          await disconnectAccount(connector, a.account_id);
          setBusy(false);
          onChanged();
        }}
      >
        ×
      </button>
    </div>
  );
}
