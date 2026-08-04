import { useEffect, useRef, useState } from "react";
import {
  cloudLogin,
  connectManaged,
  getCloudStatus,
  getConnectors,
  getRecentChannels,
  waitForCloudSignIn,
  type CloudStatus,
  type Connector,
  type RecentChannel,
} from "../api";
import { ConnectorBadge } from "../connectors/ConnectorIcon";
import { ChannelPicker } from "./SubscriptionsChip";
import { SelectMenu } from "./SelectMenu";

// The Automations quickstart (UX-DECISIONS §29): ONE template system. The former onboarding
// recipe step (§24's role recipes) merged into the page's "Start from a template" grid — every
// card carries §27's connector-dot vocabulary (brand = connected, grayscale = needs connecting);
// picking a card expands the configure card below the grid: connect rows (with the lazy cloud
// sign-in pane), channel-by-name, day × time, and the §25 consent line for write recipes.
// The `ob-*` testids moved here with the machinery.

// "When" = day choice × free time (owner call 2026-07-11); the cron assembles from the two.
const DAYS: Record<string, { label: string; dow: string }> = {
  mon: { label: "每周一", dow: "1" },
  tue: { label: "每周二", dow: "2" },
  wed: { label: "每周三", dow: "3" },
  thu: { label: "每周四", dow: "4" },
  fri: { label: "每周五", dow: "5" },
  sat: { label: "每周六", dow: "6" },
  sun: { label: "每周日", dow: "0" },
  weekdays: { label: "工作日", dow: "1-5" },
  daily: { label: "每天", dow: "*" },
};
// §30 connect-state spinner (the app has no other spinner — waits elsewhere are label swaps).
// Exported for Onboarding page 2's sign-in button (same states, same look).
export const Spinner = () => (
  <span className="inline-block w-3 h-3 rounded-full border-[1.5px] border-line2 border-t-accent animate-spin" />
);

const cronFor = (dayKey: string, hhmm: string) => {
  const [h, m] = hhmm.split(":");
  return `${Number(m) || 0} ${Number(h) || 9} * * ${DAYS[dayKey]?.dow ?? "*"}`;
};

interface QuickTemplate {
  key: string;
  title: string;
  blurb: string;
  cadence: string; // the card's footer label
  conns: { name: string; why: string }[]; // [] = no connections needed
  needsRepo?: boolean;
  needsChannel?: boolean;
  consent?: boolean; // write recipes carry the §25 consent line; reads carry disclosure
  deliver?: boolean; // Morning brief's deliver-to choice
  day: string;
  time: string;
  instructions: (ctx: { repo: string; channel: string; deliver: "app" | "slack" }) => string;
}

const TEMPLATES: QuickTemplate[] = [
  {
    key: "github",
    title: "GitHub 摘要",
    blurb: "合并的 PR 与提交，推送到你团队的 Slack。",
    cadence: "每周",
    conns: [
      { name: "slack", why: "摘要发布的位置" },
      { name: "github", why: "摘要汇总的内容" },
    ],
    needsRepo: true,
    needsChannel: true,
    consent: true,
    day: "mon",
    time: "09:00",
    instructions: ({ repo, channel }) =>
      `Summarize activity since the last digest in the GitHub repository ${repo || "(the connected repository)"}: ` +
      `merged pull requests, notable commits, and anything needing attention. ` +
      `Post the digest to the Slack channel ${channel} using send_message.`,
  },
  {
    key: "pipeline",
    title: "销售管道摘要",
    blurb: "有进展的商机，以及陷入沉寂的商机，推送到 Slack。",
    cadence: "每周",
    conns: [
      { name: "slack", why: "摘要发布的位置" },
      { name: "hubspot", why: "销售管道与商机动态" },
    ],
    needsChannel: true,
    consent: true,
    day: "mon",
    time: "09:00",
    instructions: ({ channel }) =>
      `Review HubSpot activity since the last digest: deals that changed stage, deals going ` +
      `quiet, and deals past their close date. Post a short pipeline digest to the Slack ` +
      `channel ${channel} using send_message.`,
  },
  {
    key: "brief",
    title: "晨间简报",
    blurb: "日程与未读邮件，在你一天开始前汇总好。",
    cadence: "每天",
    conns: [
      { name: "google_calendar", why: "今天的会议与空档" },
      { name: "gmail", why: "夜间新到的邮件" },
    ],
    deliver: true,
    day: "daily",
    time: "08:00",
    instructions: ({ deliver }) =>
      `Prepare a short morning brief: today's calendar events and gaps, plus email that ` +
      `arrived since yesterday evening. ` +
      (deliver === "app" ? "Save it as the session deliverable." : "Send it to me as a Slack DM."),
  },
  {
    key: "news",
    title: "晨间新闻简报",
    blurb: "5 条要点的科技与国际新闻摘要，保存为 markdown。",
    cadence: "每天",
    conns: [],
    day: "daily",
    time: "08:00",
    instructions: () =>
      "Search the web for the most important technology and world news from the last 24 hours " +
      "and write a concise 5-bullet briefing, saved as a markdown file.",
  },
  {
    key: "inboxdigest",
    title: "收件箱摘要",
    blurb: "一份简短的未读邮件摘要。",
    cadence: "工作日",
    conns: [{ name: "gmail", why: "你的未读邮件" }],
    day: "weekdays",
    time: "09:00",
    instructions: () => "Summarize my unread email into one short digest note.",
  },
  {
    key: "cleanup",
    title: "文件夹整理",
    blurb: "把最近的下载文件按类型归入整齐的文件夹。",
    cadence: "每周",
    conns: [],
    day: "fri",
    time: "17:30",
    instructions: () => "Sort my recent Downloads into tidy folders by file type.",
  },
];

export function AutomationQuickstart({
  busy,
  onCreate,
}: {
  busy: boolean;
  onCreate: (payload: {
    title: string;
    instructions: string;
    cron?: string;
    permissions?: { tool: string; target: string; access: "read" | "write" }[];
  }) => void;
}) {
  const [pickedKey, setPickedKey] = useState<string | null>(null);
  const picked = TEMPLATES.find((t) => t.key === pickedKey) || null;

  const [connectors, setConnectors] = useState<Connector[]>([]);
  const [cloud, setCloud] = useState<CloudStatus | null>(null);
  const [pendingConn, setPendingConn] = useState<string | null>(null);
  // §30 connect states: "opening" while the broker POST is in flight (the browser hasn't
  // appeared yet), "waiting" once it has — the handoff strip explains the out-of-band finish.
  const [connFlow, setConnFlow] = useState<{ name: string; phase: "opening" | "waiting" } | null>(
    null,
  );
  const [signinPhase, setSigninPhase] = useState<"opening" | "waiting" | null>(null);
  const [recent, setRecent] = useState<RecentChannel[]>([]);
  const [repo, setRepo] = useState("");
  const [channel, setChannel] = useState("");
  const [day, setDay] = useState("mon");
  const [time, setTime] = useState("09:00");
  const [deliver, setDeliver] = useState<"app" | "slack">("app");
  const [consent, setConsent] = useState(true);

  const refresh = () => {
    getConnectors().then(setConnectors).catch(() => {});
    getCloudStatus().then(setCloud).catch(() => {});
  };
  // Connector state drives the card dots, so load once up front; poll only while a template
  // is being configured (connects and the cloud sign-in land out-of-band).
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  useEffect(() => {
    refresh();
  }, []);
  useEffect(() => {
    if (!picked) return;
    refresh();
    getRecentChannels().then(setRecent).catch(() => {});
    pollRef.current = setInterval(refresh, 3000);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pickedKey]);

  const connState = (name: string) => connectors.find((c) => c.name === name);
  const allConnected = !picked || picked.conns.every((c) => connState(c.name)?.connected);
  // §25 consent line shows the HUMAN name (owner catch 2026-07-14: it echoed the raw
  // slack:T…/C… target). Names come from a picker pick (remembered per address) or the
  // recent list; a hand-typed raw address stays raw — we never guess.
  const [picked_names, setPickedNames] = useState<Record<string, { name: string; workspace?: string }>>({});
  const pickedInfo = picked_names[channel];
  const channelName = pickedInfo?.name || recent.find((c) => c.channel === channel)?.name;
  const channelLabel = channelName ? `#${channelName}` : channel;
  const channelWorkspace = pickedInfo?.workspace;

  // The poll flipping a row to ✓ is what ends its waiting state.
  useEffect(() => {
    if (connFlow && connState(connFlow.name)?.connected) setConnFlow(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connectors]);

  // §30: the configure card scrolls into view on pick — it expands below the fold on
  // three-row grids and otherwise appears "nowhere".
  const cfgRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (pickedKey) cfgRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [pickedKey]);

  const pick = (t: QuickTemplate) => {
    setPickedKey(t.key);
    setDay(t.day);
    setTime(t.time);
    setConsent(true);
    setConnFlow(null);
  };

  const startConnect = async (name: string) => {
    if (!cloud?.signed_in) {
      setPendingConn(name); // the pane appears; sign-in completes it
      return;
    }
    // §30: the broker round-trip takes seconds — narrate it on the row itself.
    setConnFlow({ name, phase: "opening" });
    // GitHub is authorize-first at the BROKER: one connect links an existing
    // installation or lands on the install page — no flow choice here anymore.
    await connectManaged(name).catch(() => {});
    // The POST resolves once the system browser is off; the poll ends the waiting state.
    setConnFlow((f) => (f?.name === name ? { name, phase: "waiting" } : f));
    refresh();
  };

  const signinPollRef = useRef<(() => void) | null>(null);
  const cancelSignin = () => {
    signinPollRef.current?.();
    signinPollRef.current = null;
    setSigninPhase(null);
  };
  useEffect(() => cancelSignin, []); // never leave the poll running after unmount

  const signInThenConnect = async () => {
    setSigninPhase("opening");
    await cloudLogin().catch(() => {});
    setSigninPhase("waiting");
    // Poll until the browser flow lands, then finish the pending connect (bounded).
    signinPollRef.current = waitForCloudSignIn(async (s) => {
      signinPollRef.current = null;
      setSigninPhase(null);
      if (!s?.signed_in) return;
      setCloud(s);
      if (pendingConn) {
        const name = pendingConn;
        setConnFlow({ name, phase: "opening" });
        await connectManaged(name).catch(() => {});
        setConnFlow((f) => (f?.name === name ? { name, phase: "waiting" } : f));
        setPendingConn(null);
        refresh();
      }
    });
  };

  const create = () => {
    if (!picked) return;
    onCreate({
      title: picked.title,
      instructions: picked.instructions({ repo, channel, deliver }),
      cron: cronFor(day, time),
      permissions:
        picked.consent && consent && channel
          ? [{ tool: "send_message", target: channel, access: "write" }]
          : [],
    });
  };

  const gateHint = !allConnected
    ? `请先连接 ${picked?.conns
        .filter((c) => !connState(c.name)?.connected)
        .map((c) => connState(c.name)?.title || c.name)
        .join("、")} 以继续`
    : picked?.needsChannel && !channel
      ? "请先选择要发布到的频道"
      : "";

  const label = "block text-[12px] text-muted mt-3 mb-1";
  const input =
    "w-full px-3 py-2 rounded-lg border border-line bg-panel text-[13.5px] outline-none focus:border-accent";

  return (
    <div className="mb-4">
      <div className="text-[11px] uppercase tracking-[0.05em] text-faint mb-2.5">
        从模板开始
      </div>
      {/* Equal-height cards (owner ask 2026-07-12): 1fr rows + h-full — <button> grid items
          don't stretch like divs. */}
      <div className="grid grid-cols-3 auto-rows-fr gap-3">
        {TEMPLATES.map((t) => (
          <button
            key={t.key}
            data-testid={`qs-template-${t.key}`}
            className={
              "h-full text-left rounded-xl2 border bg-panel p-4 flex flex-col gap-1.5 " +
              (pickedKey === t.key
                ? "border-accent ring-2 ring-accentSoft"
                : "border-line hover:border-lineStrong")
            }
            onClick={() => pick(t)}
          >
            <span className="text-[13.5px] font-semibold">{t.title}</span>
            <span className="text-[12px] text-muted leading-relaxed flex-1">{t.blurb}</span>
            <span className="flex items-center gap-1.5 mt-1">
              {t.conns.map((c) => {
                const cs = connState(c.name);
                const on = !!cs?.connected;
                return (
                  <span
                    key={c.name}
                    title={`${cs?.title || c.name} — ${on ? "已连接" : "尚未连接"}`}
                    style={on ? undefined : { filter: "grayscale(1)", opacity: 0.55 }}
                  >
                    {cs ? (
                      <ConnectorBadge connector={cs} size={16} title={cs.title} />
                    ) : (
                      <span className="inline-block w-4 h-4 rounded-full border border-line2" />
                    )}
                  </span>
                );
              })}
              <span className="text-[11px] text-faint ml-0.5">
                {t.conns.length === 0 ? `无需连接 · ${t.cadence}` : t.cadence}
              </span>
            </span>
          </button>
        ))}
      </div>

      {picked && (
        <div
          ref={cfgRef}
          className="mt-3 rounded-xl2 border border-line bg-panel p-4"
          data-testid="qs-configure"
        >
          {/* §30: the card names its template — without this it starts abruptly after the grid. */}
          <div className="flex items-baseline gap-2 pb-2.5 mb-1 border-b border-line">
            <span className="text-[11px] uppercase tracking-[0.05em] text-accent font-semibold">
              设置
            </span>
            <span className="text-[14px] font-semibold">{picked.title}</span>
            <span className="ml-auto text-[12px] text-faint max-sm:hidden">
              {picked.conns.length ? "连接、发送与排程" : "发送与排程"} ·{" "}
              {picked.cadence}
            </span>
          </div>
          {picked.conns.map(({ name, why }) => {
            const c = connState(name);
            const flow = connFlow?.name === name ? connFlow : null;
            return (
              <div key={name} className="border-b border-line last:border-b-0">
                <div className="flex items-center gap-3 py-2.5">
                  {c && <ConnectorBadge connector={c} size={26} title={c.title} />}
                  <span className="min-w-0 flex-1">
                    <span className="block text-[13.5px] font-medium">{c?.title || name}</span>
                    <span className="block text-[11.5px] text-faint">{why}</span>
                  </span>
                  {c?.connected ? (
                    <span className="text-[12.5px] text-ok">✓ 已连接</span>
                  ) : flow ? (
                    <span className="inline-flex items-center gap-2 text-[12px] text-muted">
                      <Spinner />
                      {flow.phase === "opening"
                        ? "正在打开浏览器…"
                        : `正在等待 ${c?.title || name}…`}
                    </span>
                  ) : (
                    <button
                      className="px-3.5 py-1 rounded-full border border-line text-[12.5px] hover:bg-paper"
                      onClick={() => startConnect(name)}
                      data-testid={`ob-connect-${name}`}
                    >
                      连接
                    </button>
                  )}
                </div>
                {/* §30 handoff strip: the flow finishes out-of-band in the browser — say so,
                    and let Cancel clear the LOCAL state (the browser tab is the user's). */}
                {flow?.phase === "waiting" && (
                  <div
                    className="flex items-start gap-2 bg-accentSoft/50 rounded-lg px-3 py-2 mb-2.5 text-[12px] text-muted"
                    data-testid="ob-connect-wait"
                  >
                    <span>↗</span>
                    <span className="flex-1 min-w-0">
                      <b className="text-ink font-medium">
                        请在浏览器中完成 {c?.title || name} 的连接。
                      </b>{" "}
                      在那里确认后返回 — 本页面会自动更新。
                    </span>
                    <button
                      className="text-faint underline hover:text-muted shrink-0"
                      onClick={() => setConnFlow(null)}
                      data-testid="ob-connect-cancel"
                    >
                      取消
                    </button>
                  </div>
                )}
              </div>
            );
          })}

          {pendingConn && !cloud?.signed_in && (
            <div
              className="bg-accentSoft/50 rounded-xl px-4 py-3 mt-3 text-[12.5px] text-muted"
              data-testid="ob-cloudpane"
            >
              <span className="block text-[13px] text-ink font-medium">
                一次登录，解锁所有一键连接
              </span>
              连接由 OpenWorker Cloud 代理 — 你的 Token 始终保留在这台电脑上。
              <div className="flex items-center gap-3 mt-2">
                {signinPhase ? (
                  <>
                    <span className="inline-flex items-center gap-2 text-[12px]">
                      <Spinner />
                      {signinPhase === "opening" ? "正在打开浏览器…" : "正在等待登录…"}
                    </span>
                    {signinPhase === "waiting" && (
                      <span className="text-[11.5px] text-faint">
                        请在浏览器中完成登录 — 本页面会自动更新。{" "}
                        <button
                          className="underline hover:text-muted"
                          onClick={cancelSignin}
                          data-testid="ob-signin-cancel"
                        >
                          取消
                        </button>
                      </span>
                    )}
                  </>
                ) : (
                  <button
                    className="px-3.5 py-1 rounded-full border border-line text-[12.5px] text-accent hover:bg-panel"
                    onClick={signInThenConnect}
                    data-testid="ob-cloud-signin"
                  >
                    登录 OpenWorker Cloud
                  </button>
                )}
              </div>
            </div>
          )}

          {allConnected && (
            <div className={picked.conns.length ? "bg-paper rounded-xl px-4 py-3.5 mt-3" : ""} data-testid="ob-recipe">
              {picked.needsRepo && (
                <>
                  <label className={label}>代码仓库</label>
                  <input
                    className={input}
                    placeholder="owner/repo"
                    value={repo}
                    onChange={(e) => setRepo(e.target.value)}
                    data-testid="ob-repo"
                  />
                </>
              )}
              {picked.needsChannel && (
                <>
                  <label className={label}>发布到频道</label>
                  <div data-testid="ob-channel">
                    <ChannelPicker
                      value={channel}
                      onChange={setChannel}
                      recent={recent}
                      onPickName={(address, name, workspace) =>
                        setPickedNames((m) => ({ ...m, [address]: { name, workspace } }))
                      }
                    />
                  </div>
                  <p className="text-[11px] text-warnInk mt-1">
                    机器人必须是该频道的成员 — 若未加入，请在 Slack 中邀请 @OpenWorker。
                  </p>
                </>
              )}
              <label className={label}>时间</label>
              <div className="flex gap-2">
                <div className="flex-1 min-w-0">
                  <SelectMenu
                    ariaLabel="日期"
                    value={day}
                    options={Object.entries(DAYS).map(([k, v]) => ({ value: k, label: v.label }))}
                    onChange={setDay}
                  />
                </div>
                <input
                  className="w-28 px-3 py-2 rounded-lg border border-line bg-panel text-[13.5px] outline-none focus:border-accent"
                  type="time"
                  aria-label="时间"
                  value={time}
                  onChange={(e) => setTime(e.target.value)}
                />
              </div>
              {picked.deliver && (
                <>
                  <label className={label}>发送至</label>
                  <SelectMenu
                    ariaLabel="发送至"
                    value={deliver}
                    options={[
                      { value: "app", label: "应用内" },
                      { value: "slack", label: "Slack 私信（稍后连接 Slack）" },
                    ]}
                    onChange={(v) => setDeliver(v as "app" | "slack")}
                  />
                </>
              )}
              {picked.consent ? (
                <label className="flex items-start gap-2.5 mt-3.5 text-[12.5px] text-muted select-none">
                  <input
                    type="checkbox"
                    className="mt-0.5"
                    checked={consent}
                    onChange={(e) => setConsent(e.target.checked)}
                    data-testid="ob-consent"
                  />
                  <span>
                    允许该自动化任务将摘要发布到{" "}
                    <b className="text-ink" title={channel || undefined}>
                      {channelLabel || "该频道"}
                      {channelWorkspace ? ` (${channelWorkspace})` : ""}
                    </b>{" "}
                    而无需每次询问。其他操作仍会先询问。
                  </span>
                </label>
              ) : picked.conns.length > 0 ? (
                <p className="text-[12.5px] text-muted mt-3">
                  该自动化任务按计划仅<b className="text-ink">读取</b> — 读取操作无需审批。
                </p>
              ) : null}
            </div>
          )}

          <div className="flex items-center gap-3 mt-4">
            <button
              className="text-[12.5px] text-faint hover:text-muted"
              onClick={() => setPickedKey(null)}
            >
              取消
            </button>
            {/* A silently-disabled primary reads as a bug — always name the missing piece. */}
            {gateHint && (
              <span className="ml-auto text-[11.5px] text-faint" data-testid="ob-create-hint">
                {gateHint}
              </span>
            )}
            <button
              className={
                (gateHint ? "" : "ml-auto ") +
                "px-5 py-2 rounded-full bg-ink text-panel text-[13px] disabled:opacity-40"
              }
              disabled={busy || !allConnected || (picked.needsChannel && !channel)}
              onClick={create}
              data-testid="ob-create"
            >
              {busy ? "正在创建…" : "创建自动化任务"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
