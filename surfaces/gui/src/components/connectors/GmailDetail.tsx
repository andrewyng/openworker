import { useState } from "react";
import {
  connectManaged,
  disconnectGmailAccount,
  setGmailDefaultAccount,
  setGmailFilters,
  type GmailAccount,
} from "../../api";
import { ConnectorBadge } from "../../connectors/ConnectorIcon";
import type { DetailProps } from "./ConnectorsSection";
import { ToolsDisclosure } from "./ToolsDisclosure";
import { FOOT, GRP, GRP_H, PILL_ACCENT, ROW, TAG_ACCENT, TAG_WARN, XBTN } from "./ui";

// The Gmail detail page (UX-DECISIONS §21): connected mailboxes (multi-account,
// Default badge, per-account disconnect) + "Never show agents" privacy filters.
// Adding an account launches managed OAuth DIRECTLY — Gmail has one connect mode,
// so no modal (the pill-modal is only for ≥2-mode connectors like Slack).

const LABEL = "text-[12.5px] text-muted w-24 shrink-0";

export function GmailDetail({ c, cloud, slack: _slack, onChanged }: DetailProps) {
  const [busy, setBusy] = useState(false);
  const accounts = (c.accounts ?? []) as GmailAccount[]; // email-keyed (pre-generic-layer shape)

  const addAccount = async () => {
    setBusy(true);
    await connectManaged("gmail"); // completes in the system browser; the poll picks it up
    setTimeout(() => setBusy(false), 2500);
  };

  return (
    <div data-testid="gmail-detail">
      <div className="flex items-center gap-3.5 mb-5">
        <ConnectorBadge connector={c} size={44} title="Gmail" />
        <div className="min-w-0 flex-1">
          <h2 className="text-[20px] font-semibold tracking-tight leading-tight">Gmail</h2>
          <div className="text-[12.5px] text-muted flex items-center gap-1.5">
            {c.connected ? (
              <>
                <span className="w-2 h-2 rounded-full bg-ok" />
                <span data-testid="gmail-status">
                  {accounts.length} 个账户
                </span>
              </>
            ) : (
              <span>未连接</span>
            )}
          </div>
        </div>
        <button
          className={PILL_ACCENT + (c.managed_paused ? " opacity-50" : "")}
          data-testid="add-account-btn"
          onClick={addAccount}
          disabled={busy || !cloud?.signed_in || c.managed_paused}
          title={
            c.managed_paused
              ? "一键 Google 登录即将推出"
              : cloud?.signed_in
                ? ""
                : "请先登录 OpenWorker Cloud"
          }
        >
          {c.managed_paused ? "＋ 添加账户 · 即将推出" : busy ? "请查看浏览器…" : "＋ 添加账户"}
        </button>
      </div>

      {!c.connected && (
        <div className={GRP}>
          <div className={ROW + " text-[12.5px] text-muted"}>
            使用 Google 登录 —— 每个邮箱彼此独立，agent 会注明使用的是哪一个。
            {cloud?.signed_in ? "" : " 需要登录 Cloud。"}
          </div>
        </div>
      )}

      {accounts.length > 0 && (
        <>
          <div className={GRP_H + " !mt-0"}>账户</div>
          <div className={GRP} data-testid="gmail-accounts">
            {accounts.map((a) => (
              <AccountRow key={a.email} a={a} onChanged={onChanged} />
            ))}
          </div>
        </>
      )}

      <FiltersGroup c={c} onChanged={onChanged} />

      <ToolsDisclosure c={c} onChanged={onChanged} />
      <div className={FOOT + " mt-2"}>
        过滤规则在这台电脑上执行，agent 看到结果之前就已生效。被隐藏的数量会显示在工具卡片和「活动」里，但内容不会。
      </div>
    </div>
  );
}

function AccountRow({ a, onChanged }: { a: GmailAccount; onChanged: () => void }) {
  const [busy, setBusy] = useState(false);
  return (
    <div className={ROW} data-testid={`gmail-account-${a.email}`}>
      <span className="min-w-0 flex-1 flex items-center gap-2">
        <span className="text-[13px] font-medium truncate">{a.email}</span>
        {a.default && <span className={TAG_ACCENT}>默认</span>}
        {a.needs_reauth && <span className={TAG_WARN}>⚠ 需重新登录</span>}
      </span>
      {!a.default && (
        <button
          className="text-[12px] text-muted hover:text-ink shrink-0"
          data-testid={`gmail-make-default-${a.email}`}
          onClick={async () => {
            await setGmailDefaultAccount(a.email);
            onChanged();
          }}
        >
          设为默认
        </button>
      )}
      <button
        className={XBTN}
        title="断开此邮箱连接"
        data-testid={`gmail-disconnect-${a.email}`}
        disabled={busy}
        onClick={async () => {
          setBusy(true);
          await disconnectGmailAccount(a.email);
          setBusy(false);
          onChanged();
        }}
      >
        ×
      </button>
    </div>
  );
}

function FiltersGroup({ c, onChanged }: Pick<DetailProps, "c" | "onChanged">) {
  const filters = c.filters ?? { senders: [], labels: [] };
  return (
    <>
      <div className={GRP_H}>始终对 agent 隐藏</div>
      <div className={GRP} data-testid="gmail-filters">
        <ChipListRow
          label="发件人"
          testid="gmail-filter-senders"
          placeholder="name@example.com 或 @domain.com"
          values={filters.senders}
          onSave={async (senders) => {
            await setGmailFilters({ senders });
            onChanged();
          }}
        />
        <ChipListRow
          label="标签"
          testid="gmail-filter-labels"
          placeholder="标签名，例如「个人」"
          values={filters.labels}
          onSave={async (labels) => {
            await setGmailFilters({ labels });
            onChanged();
          }}
        />
      </div>
      <div className={FOOT}>
        匹配的邮件会被静默排除在 agent 可读取的内容之外 —— 不留任何可被探知的痕迹。
      </div>
    </>
  );
}

function ChipListRow({
  label,
  testid,
  placeholder,
  values,
  onSave,
}: {
  label: string;
  testid: string;
  placeholder: string;
  values: string[];
  onSave: (next: string[]) => Promise<void>;
}) {
  const [draft, setDraft] = useState("");
  const add = async () => {
    const v = draft.trim();
    if (!v) return;
    setDraft("");
    await onSave([...values, v]);
  };
  return (
    <div className={ROW} data-testid={testid}>
      <span className={LABEL}>{label}</span>
      <span className="min-w-0 flex-1 flex flex-wrap items-center gap-1.5">
        {values.map((v) => (
          <span
            key={v}
            className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-paper border border-line text-[12.5px]"
          >
            {v}
            <button
              className={XBTN}
              title="移除"
              onClick={() => onSave(values.filter((x) => x !== v))}
            >
              ×
            </button>
          </span>
        ))}
        <input
          className="flex-1 min-w-[140px] bg-transparent text-[12.5px] outline-none placeholder:text-faint"
          placeholder={placeholder}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") add();
          }}
          onBlur={() => draft.trim() && add()}
        />
      </span>
    </div>
  );
}
