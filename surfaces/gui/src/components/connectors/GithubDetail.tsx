import { useEffect, useState } from "react";
import {
  disallowUser,
  disconnectGithubInstallation,
  getGithubStatus,
  getSubscriptions,
  resolveUnauthorized,
  unsubscribeChannel,
  type Connector,
  type GithubInstallation,
  type GithubStatus,
  type ParkedMessage,
  type Subscription,
} from "../../api";
import { ConnectorBadge } from "../../connectors/ConnectorIcon";
import { AddConnectionModal } from "./AddConnectionModal";
import type { DetailProps } from "./ConnectorsSection";
import { ToolsDisclosure } from "./ToolsDisclosure";
import { FOOT, GRP, GRP_H, PILL_ACCENT, PILL_LINE, ROW, TAG_WARN, XBTN } from "./ui";

// The GitHub detail page (github-relay-spec §8), the Slack page's shape: one
// group per App INSTALLATION (the allow-list scope) — People (sender logins
// allowed to trigger work) · Waiting (parked mentions) · per-installation
// disconnect — plus a page-level Listening group (a subscription names a repo
// thread, which the GUI can't map back to an installation). Adding an
// installation goes through the ONE entry point: header button → modal.

const LABEL = "text-[12.5px] text-muted w-24 shrink-0";

/** The relay status line, one honest layer at a time (the Slack rule). */
function relayHealth(gh: GithubStatus | null): { dot: string; text: string } {
  if (!gh) return { dot: "bg-ok", text: "在线 · 托管中继" };
  if (!gh.signed_in)
    return { dot: "bg-warnInk", text: "需登录 —— 中继已暂停" };
  if (gh.relay.state === "offline")
    return { dot: "bg-faint/60", text: "离线 —— 无法连接中继" };
  if (gh.relay.state === "reconnecting")
    return { dot: "bg-warnInk", text: "正在重连中继…" };
  return { dot: "bg-ok", text: "在线 · 托管中继" };
}

export function GithubDetail({ c, cloud, onChanged }: DetailProps) {
  const [adding, setAdding] = useState(false);
  const [subs, setSubs] = useState<Subscription[]>([]);
  const [status, setStatus] = useState<GithubStatus | null>(null);
  const load = () => {
    getSubscriptions().then(setSubs).catch(() => setSubs([]));
    getGithubStatus().then(setStatus).catch(() => setStatus(null));
  };
  useEffect(() => {
    load();
  }, [c.name]);

  const relay = c.mode === "relay";
  const installations = c.installations ?? [];
  const changed = () => {
    onChanged();
    load();
  };
  const listening = subs.filter((s) => s.channel.startsWith("github:"));

  return (
    <div data-testid="github-installations">
      <div className="flex items-center gap-3.5 mb-5">
        <ConnectorBadge connector={c} size={44} title="GitHub" />
        <div className="min-w-0 flex-1">
          <h2 className="text-[20px] font-semibold tracking-tight leading-tight">GitHub</h2>
          <div className="text-[12.5px] text-muted flex items-center gap-1.5">
            {c.connected ? (
              <>
                <span
                  className={
                    "w-2 h-2 rounded-full " + (relay ? relayHealth(status).dot : "bg-ok")
                  }
                />
                <span data-testid="github-mode-badge">
                  {relay
                    ? relayHealth(status).text
                    : "已连接 · 个人访问令牌"}
                </span>
              </>
            ) : (
              <span>未连接</span>
            )}
          </div>
        </div>
        {(relay || !c.connected) && (
          <button
            className={PILL_ACCENT}
            data-testid="add-installation-btn"
            onClick={() => setAdding(true)}
          >
            ＋ 添加安装
          </button>
        )}
      </div>

      {!c.connected && (
        <div className={GRP}>
          <div className={ROW + " text-[12.5px] text-muted"}>
            一个 @ocw-agent App，按账户或组织分别安装 —— 你在 GitHub 上选择仓库；每次安装都有各自独立的白名单。
            {cloud?.signed_in ? "" : " 一键连接需要登录 Cloud；用 PAT 则无需登录。"}
          </div>
        </div>
      )}

      {relay &&
        installations.map((inst) => (
          <InstallationGroup
            key={inst.installation_id}
            c={c}
            inst={inst}
            tokenOk={status?.installs?.[inst.installation_id]?.token_ok !== false}
            onChanged={changed}
          />
        ))}

      {/* Manual PAT: request/response tools only — no inbound triggers. */}
      {c.connected && !relay && (
        <div className={GRP} data-testid="github-manual-card">
          <div className={ROW + " text-[12.5px] text-muted"}>
            个人访问令牌 · 仅工具。安装 GitHub App，才能让 @提及 和 agent 标签送达这台电脑。
          </div>
        </div>
      )}

      {relay && listening.length > 0 && (
        <>
          <div className={GRP_H}>正在监听</div>
          <div className={GRP}>
            <ListeningRows subs={listening} onChanged={changed} />
          </div>
        </>
      )}

      <ToolsDisclosure c={c} onChanged={onChanged} />
      {c.connected && relay && (
        <div className={FOOT + " mt-2"}>
          触发方式：@ocw-agent 提及，以及「ocw-agent」标签。agent 会以 ocw-agent[bot] 身份回复。
        </div>
      )}

      {adding && (
        <AddConnectionModal
          c={c}
          cloud={cloud}
          title="添加安装"
          onClose={() => setAdding(false)}
          onChanged={changed}
        />
      )}
    </div>
  );
}

function InstallationGroup({
  c,
  inst,
  tokenOk,
  onChanged,
}: {
  c: Connector;
  inst: GithubInstallation;
  tokenOk: boolean;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const parked = (c.unauthorized ?? []).filter((m) => m.team_id === inst.installation_id);
  const empty = inst.allowed_users.length === 0 && parked.length === 0;

  const disconnect = async () => {
    setBusy(true);
    await disconnectGithubInstallation(inst.installation_id);
    setBusy(false);
    onChanged();
  };

  return (
    <div data-testid={`github-install-${inst.installation_id}`}>
      <div className={GRP_H + " flex items-center gap-2"}>
        <span>
          {inst.account_login}{" "}
          <span className="font-normal text-faint" title={`installation ${inst.installation_id}`}>
            · {inst.repo_selection === "all" ? "全部仓库" : "所选仓库"}
          </span>
        </span>
        {!tokenOk && (
          <span className={TAG_WARN} data-testid={`token-warn-${inst.installation_id}`}>
            ⚠ 安装已撤销 —— 请重新安装
          </span>
        )}
      </div>
      <div className={GRP}>
        {empty ? (
          <div className={ROW}>
            <span className="min-w-0 flex-1 text-[12.5px] text-muted">
              还没有允许任何人 —— @ocw-agent 的提及会显示在这里，等你确认。
            </span>
            <DisconnectBtn id={inst.installation_id} busy={busy} onClick={disconnect} />
          </div>
        ) : (
          <>
            <PeopleRow
              allowed={inst.allowed_users}
              installationId={inst.installation_id}
              onChanged={onChanged}
            />
            {parked.map((m) => (
              <WaitingRow key={m.id} m={m} onChanged={onChanged} />
            ))}
            <div className={ROW}>
              <span className="flex-1" />
              <DisconnectBtn id={inst.installation_id} busy={busy} onClick={disconnect} />
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function DisconnectBtn({ id, busy, onClick }: { id: string; busy: boolean; onClick: () => void }) {
  return (
    <button
      className="text-[12.5px] text-danger/80 hover:text-danger shrink-0"
      data-testid={`disconnect-install-${id}`}
      title="停止将此安装中继到这台电脑。该 App 仍保留在 GitHub 上。"
      onClick={onClick}
      disabled={busy}
    >
      {busy ? "正在断开…" : "断开此安装"}
    </button>
  );
}

function PeopleRow({
  allowed,
  installationId,
  onChanged,
}: {
  allowed: string[];
  installationId: string;
  onChanged: () => void;
}) {
  return (
    <div className={ROW}>
      <span className={LABEL}>成员</span>
      <span className="min-w-0 flex-1 flex flex-wrap items-center gap-1.5">
        {allowed.length === 0 && (
          <span className="text-[12px] text-faint">还没有人 —— 在下方批准一位等待中的发送者</span>
        )}
        {allowed.map((login) => (
          <span
            key={login}
            className="inline-flex items-center gap-1.5 pl-2 pr-2 py-0.5 rounded-full bg-paper border border-line text-[12.5px]"
          >
            {/* GitHub logins ARE the readable identity — no resolution needed. */}
            @{login}
            <button
              className={XBTN}
              title="移除"
              onClick={() => disallowUser("github", login, installationId).then(onChanged)}
            >
              ×
            </button>
          </span>
        ))}
      </span>
    </div>
  );
}

function WaitingRow({ m, onChanged }: { m: ParkedMessage; onChanged: () => void }) {
  const act = async (action: "dismiss" | "allow" | "allow_deliver") => {
    await resolveUnauthorized("github", m.id, action);
    onChanged();
  };
  return (
    <div className={ROW + " bg-warnSoft/25"} data-testid={`waiting-${m.id}`}>
      <span className={LABEL}>等待中</span>
      <span className="min-w-0 flex-1">
        <span className="font-medium text-[13px]">@{m.user_name || m.user_id}</span>{" "}
        <span className="text-[12.5px] text-muted">于 {m.chat_name || m.chat_id}</span>
        <span className="block text-[12.5px] text-muted truncate">“{m.text}”</span>
      </span>
      <button
        className={PILL_ACCENT + " !py-1"}
        data-testid={`parked-allow-deliver-${m.id}`}
        title="允许该发送者，并立即送达此提及"
        onClick={() => act("allow_deliver")}
      >
        允许并送达
      </button>
      <button
        className={PILL_LINE + " !py-1"}
        data-testid={`parked-allow-${m.id}`}
        title="允许该发送者；此提及将被丢弃"
        onClick={() => act("allow")}
      >
        允许
      </button>
      <button className={XBTN + " px-1"} data-testid={`parked-dismiss-${m.id}`} title="忽略" onClick={() => act("dismiss")}>
        ×
      </button>
    </div>
  );
}

function ListeningRows({ subs, onChanged }: { subs: Subscription[]; onChanged: () => void }) {
  return (
    <div className={ROW} data-testid="listening-github">
      <span className={LABEL}>正在监听</span>
      <span className="min-w-0 flex-1 space-y-1">
        {subs.map((s) => (
          <span key={s.session_id + s.channel} className="flex items-center gap-2 text-[12.5px]">
            <span className="font-medium truncate" title={s.session_id}>
              {s.session_title || s.session_id}
            </span>
            <span className="text-faint">←</span>
            <span className="text-muted truncate" title={s.channel}>
              {s.channel.replace(/^github:/, "")}
            </span>
            <button
              className={XBTN + " ml-auto"}
              title="退订此会话"
              onClick={async () => {
                await unsubscribeChannel(s.session_id, s.channel);
                onChanged();
              }}
            >
              ×
            </button>
          </span>
        ))}
      </span>
    </div>
  );
}
