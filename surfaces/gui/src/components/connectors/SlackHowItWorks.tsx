import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import type { SlackWorkspace } from "../../api";

// UX-027: the post-connect "how mentions reach you" card. A tabbed carousel of
// animated split-scenes — Slack on the left (pinned to light-Slack colors, so it
// reads as a screenshot of Slack), OpenWorker on the right (app tokens). Tabs
// auto-advance through one full tour, then idle on a loop of the current scene;
// clicking a tab takes over. The chevron collapses the carousel to the status
// line — collapsed IS the seen-state (stored locally, survives restarts).
// "Listen to a channel" is deliberately absent: owner wants that scene reworked
// before it ships (UX-027 rev 4).

const KEY = "ocw.slack.howitworks.collapsed";
const DUR = 8000; // per-scene loop, ms
const TABS = ["conn.hiwTabMention", "conn.hiwTabThreads", "conn.hiwTabTeammates"];
const CAPTIONS = [
  "conn.hiwCaptionMention",
  "conn.hiwCaptionThreads",
  "conn.hiwCaptionTeammates",
];

function readCollapsed(): boolean {
  try { return localStorage.getItem(KEY) === "1"; } catch { return false; }
}

export function SlackHowItWorks({ workspaces }: { workspaces: SlackWorkspace[] }) {
  const { t } = useTranslation();
  const [collapsed, setCollapsed] = useState(readCollapsed);
  const [tab, setTab] = useState(0);
  const [cycle, setCycle] = useState(0); // bump = remount the scene = restart its animations
  const tourRef = useRef(TABS.length); // auto-advances left in the one-time story tour

  useEffect(() => {
    if (collapsed) return;
    const t = window.setTimeout(() => {
      if (tourRef.current > 1) {
        tourRef.current -= 1;
        setTab((x) => (x + 1) % TABS.length);
      } else {
        tourRef.current = 0;
        setCycle((c) => c + 1); // keep looping the current scene quietly
      }
    }, DUR);
    return () => window.clearTimeout(t);
  }, [tab, cycle, collapsed]);

  const jump = (i: number) => {
    tourRef.current = 0; // a click takes over: no more auto-advance
    setTab(i);
    setCycle((c) => c + 1);
  };
  const toggle = () => {
    const v = !collapsed;
    setCollapsed(v);
    try { localStorage.setItem(KEY, v ? "1" : "0"); } catch { /* best effort */ }
  };

  // Personalize when the install pre-added the connecting user (setup.py):
  // their workspace names the status line; the scenes call them by first name.
  const mine = workspaces.find(
    (w) => w.installer_user_id && w.allowed_users.includes(w.installer_user_id)
  );
  const ws = mine ?? workspaces[0];
  const meName =
    (mine &&
      (mine.installer_name ||
        mine.allowed_user_names?.[mine.installer_user_id ?? ""])) ||
    t("conn.you");
  const meFirst = meName.split(/\s+/)[0];
  const meInitial = (meName[0] || "Y").toUpperCase();

  return (
    // Rev 7 (owner): BORDERLESS — a proper section title + quiet status line;
    // the only boxes on screen are the two mini-windows, which own their frames.
    <div className="mb-5" data-testid="slack-howitworks">
      <div className="flex items-baseline gap-2.5">
        <h3 className="text-[13.5px] font-semibold tracking-tight">
          {t("conn.hiwTitle")}
        </h3>
        <button
          className="ml-auto shrink-0 inline-flex items-center gap-1.5 text-[12px] text-muted hover:text-ink"
          data-testid="hiw-collapse"
          title={collapsed ? t("conn.hiwShowTitle") : t("conn.hiwCollapseTitle")}
          onClick={toggle}
        >
          {collapsed ? t("conn.hiwHowItWorks") : t("common.hide")}
          <span
            className="text-[9px] transition-transform"
            style={collapsed ? { transform: "rotate(-90deg)" } : undefined}
          >
            ▼
          </span>
        </button>
      </div>
      <div className="text-[12px] text-muted mt-0.5">
        <span className="text-ok font-bold">✓ </span>
        {ws?.account || t("conn.hiwWorkspace")} {t("conn.hiwConnected")}
        {mine
          ? t("conn.hiwOnPeople")
          : t("conn.hiwMentionsReach")}
      </div>

      {!collapsed && (
        <div className="mt-3">
          <div className="flex gap-1 border-b border-line mb-3">
            {TABS.map((tabKey, i) => (
              <button
                key={tabKey}
                className={"hiw-tab" + (i === tab ? " on" : "")}
                data-testid={`hiw-tab-${i}`}
                style={{ "--hiw-dur": `${DUR}ms` } as React.CSSProperties}
                onClick={() => jump(i)}
              >
                {t(tabKey)}
                <span className="hiw-prog"><i /></span>
              </button>
            ))}
          </div>

          <div className="hiw-scene hiw-play" key={`${tab}:${cycle}`} data-testid="hiw-scene">
            {tab === 0 && <SceneMention meFirst={meFirst} meInitial={meInitial} />}
            {tab === 1 && <SceneThread meFirst={meFirst} meInitial={meInitial} />}
            {tab === 2 && <SceneTeammates />}
          </div>
          <div className="mt-2.5 text-[12px] text-muted" data-testid="hiw-caption">
            {t(CAPTIONS[tab])}
          </div>
        </div>
      )}
    </div>
  );
}

/* ---- shared miniature furniture ---- */

// The scenes deliberately play in a FICTIONAL workspace ("Lumina Labs") — a real
// account name here (or anything resembling our own product) reads as confusing
// or fake in an educational animation; the card's status line above keeps the
// user's real workspace name.
const WS_NAME = "Lumina Labs";

/* Post-it notes (rev 7, owner-approved): the concept in five hand-written words,
   slapped onto the scene at its beat; they fade out near the end of each loop so
   they read as annotation, never as UI. */
function Sticky({
  d: delay, r, pos, children,
}: {
  d: string; r?: boolean; pos: React.CSSProperties; children: React.ReactNode;
}) {
  return (
    <div
      className={"hiw-sticky hiw-k" + (r ? " r" : "")}
      style={{ "--d": delay, ...pos } as React.CSSProperties}
    >
      {children}
    </div>
  );
}

const ThreadsIcon = () => (
  <svg className="hiw-ic" viewBox="0 0 24 24" aria-hidden="true">
    <path d="M21 11.5a8.5 8.5 0 1 1-4.7-7.6L21 3l-.9 4.7a8.5 8.5 0 0 1 .9 3.8z" />
    <path d="M8 10h8M8 14h5" />
  </svg>
);
const SendIcon = () => (
  <svg className="hiw-ic" viewBox="0 0 24 24" aria-hidden="true">
    <path d="M22 2 11 13" />
    <path d="M22 2l-7 20-4-9-9-4 20-7z" />
  </svg>
);

function SlackRail({ active }: { active: string }) {
  const { t } = useTranslation();
  return (
    <div className="hiw-slrail">
      <div className="hiw-ws">{WS_NAME} ▾</div>
      <div className="hiw-slnav"><ThreadsIcon /> {t("conn.hiwThreads")}</div>
      <div className="hiw-slnav"><SendIcon /> {t("conn.hiwDraftsSent")}</div>
      <div className="hiw-sect">{t("conn.hiwChannels")}</div>
      <div className={"hiw-ch" + (active === "general" ? " on" : "")}># general</div>
      <div className={"hiw-ch" + (active === "launch-room" ? " on" : "")}># launch-room</div>
      <div className="hiw-sect">{t("conn.hiwDirectMessages")}</div>
      <div className="hiw-slnav"><span className="hiw-pres" />Priya N</div>
      <div className="hiw-slnav"><span className="hiw-pres" />Emma W</div>
      <div className="hiw-sect">{t("conn.hiwAgentsApps")}</div>
      <div className="hiw-slnav"><span className="hiw-appav">OW</span>OpenWorker</div>
    </div>
  );
}

function SlackWin({ children }: { children: React.ReactNode }) {
  const { t } = useTranslation();
  return (
    <div className="hiw-win hiw-sl">
      <div className="hiw-sltop">
        <span className="hiw-dots"><i /><i /><i /></span>
        <span className="hiw-slsearch">{t("conn.hiwSlackSearch")}</span>
      </div>
      <div className="hiw-slbody">{children}</div>
    </div>
  );
}

/* Slack's two-row message box: formatting toolbar, then + / placeholder / send. */
function SlackComposer({ placeholder }: { placeholder: string }) {
  return (
    <div className="hiw-slcomposer">
      <div className="hiw-slctools">
        <b>B</b><i>I</i><u>U</u><s>S</s><span>⛓</span><span>≔</span><span>≡</span><span>{"</>"}</span>
      </div>
      <div className="hiw-slcrow">
        <span className="hiw-plus">＋</span> {placeholder}
        <span className="hiw-send">➤</span>
      </div>
    </div>
  );
}

function SlackDate({ label }: { label: string }) {
  return (
    <div className="hiw-sldate">
      <span>{label} ▾</span>
    </div>
  );
}

function OwWin({ children }: { children: React.ReactNode }) {
  return (
    <div className="hiw-win hiw-ow">
      <div className="hiw-owtop">
        <span className="hiw-dots"><i /><i /><i /></span> OpenWorker
      </div>
      <div className="hiw-owbody">{children}</div>
    </div>
  );
}

function OwRail({ hot, hotSub, glow }: { hot?: string; hotSub?: string; glow?: boolean }) {
  const { t } = useTranslation();
  return (
    <div className="hiw-owrail">
      <div className="hiw-brand">OpenWorker</div>
      <div className="hiw-newbtn">{t("conn.hiwNewSession")}</div>
      <div className="hiw-ownav">{t("conn.hiwSearch")}</div>
      <div className="hiw-ownav">{t("conn.hiwAutomations")}</div>
      <div className="hiw-sect">{t("conn.hiwRecent")}</div>
      {hot && (
        <div
          className={"hiw-sess hot" + (glow ? " hiw-glow hiw-k" : " hiw-stay")}
          style={glow ? ({ "--d": "2.5s", "--g": "2.9s" } as React.CSSProperties) : undefined}
        >
          <b>{hot}</b>
          {hotSub}
        </div>
      )}
      <div className="hiw-sess"><b>Jira vs Linear</b>Coworker</div>
    </div>
  );
}

const d = (delay: string, extra?: Record<string, string>) =>
  ({ "--d": delay, ...extra } as React.CSSProperties);

function Msg({
  av, avBg, name, ts, app, children, delay, extra,
}: {
  av: string; avBg: string; name: string; ts: string; app?: boolean;
  children: React.ReactNode; delay?: string; extra?: React.ReactNode;
}) {
  return (
    <div className={"hiw-slm" + (delay ? " hiw-k" : "")} style={delay ? d(delay) : undefined}>
      <span className="hiw-sav" style={{ background: avBg }}>{av}</span>
      <span className="min-w-0">
        <span className="hiw-nm">{name}{app && <span className="hiw-appb">APP</span>}</span>
        <span className="hiw-ts">{ts}</span>
        <br />
        <span>{children}</span>
        {extra}
      </span>
    </div>
  );
}

/* ---- scene 1: mention in a channel → new session, reply via thread panel ---- */
function SceneMention({ meFirst, meInitial }: { meFirst: string; meInitial: string }) {
  const { t } = useTranslation();
  return (
    <>
      <span className="hiw-spark" style={d("1.9s")} />
      <Sticky d="3.1s" pos={{ left: "51%", top: "8%" }}>{t("conn.hiwStickyMention")}</Sticky>
      <Sticky d="5.8s" r pos={{ left: "27%", bottom: "5%" }}>{t("conn.hiwStickyThread")}</Sticky>
      <SlackWin>
        <SlackRail active="launch-room" />
        <div className="hiw-slmain">
          <div className="hiw-slhead"># launch-room <span className="hiw-sub">{t("conn.hiwMembers", { count: 24 })}</span></div>
          <div className="hiw-slmsgs">
            <SlackDate label={t("conn.hiwToday")} />
            <Msg av="P" avBg="#7c6cd0" name="Priya N" ts="6:31 PM">
              {t("conn.hiwSignups")}
            </Msg>
            <Msg
              av={meInitial} avBg="#3b82c4" name={meFirst} ts="6:33 PM" delay=".8s"
              extra={
                <span className="hiw-replybar hiw-k" style={d("4.6s")}>
                  <span className="hiw-sav2">OW</span> {t("conn.hiwReplies", { count: 1 })}
                  <span className="hiw-later">{t("conn.hiwTodayAt", { time: "6:34 PM" })}</span>
                </span>
              }
            >
              <span className="hiw-men">@OpenWorker</span> {t("conn.hiwSummarizeThread")}
            </Msg>
          </div>
          <SlackComposer placeholder={t("conn.hiwMessageChannel", { channel: "launch-room" })} />
          <div className="hiw-slthread hiw-k" style={d("5.1s")}>
            <div className="hiw-th">{t("conn.hiwThread")} <span className="hiw-sub"># launch-room</span><span className="hiw-x">✕</span></div>
            <div className="hiw-tmsgs">
              <Msg av={meInitial} avBg="#3b82c4" name={meFirst} ts="6:33 PM">
                <span className="hiw-men">@OpenWorker</span> {t("conn.hiwSummarizeThread")}
              </Msg>
              <div className="hiw-cnt">{t("conn.hiwReplies", { count: 1 })}</div>
              <Msg av="OW" avBg="#4a154b" name="OpenWorker" app ts="6:34 PM">
                {t("conn.hiwLaunchTraction")}
              </Msg>
            </div>
            <div className="hiw-treply">{t("conn.hiwReply")}</div>
          </div>
        </div>
      </SlackWin>
      <OwWin>
        <OwRail hot={t("conn.hiwSummarizeChannel", { channel: "launch-room" })} hotSub={t("conn.hiwViaSlackNow")} glow />
        <div className="hiw-owmain">
          <div className="hiw-owtitle hiw-k" style={d("2.6s")}>
            {t("conn.hiwSummarizeChannel", { channel: "launch-room" })} <span className="hiw-via">{t("conn.hiwViaSlack")}</span>
          </div>
          <div className="hiw-owchat">
            <div className="hiw-bub user hiw-k" style={d("2.8s")}>{t("conn.hiwMentionSummarize")}</div>
            <div className="hiw-bub agent hiw-k" style={d("3.6s")}>
              {t("conn.hiwReadingThread")} <i>{t("conn.hiwReplyingInThread")}</i>
            </div>
          </div>
          <div className="hiw-owcomposer">{t("conn.hiwMessageOpenWorker")}</div>
        </div>
      </OwWin>
    </>
  );
}

/* ---- scene 2: mention INSIDE the open thread panel → the same session ---- */
function SceneThread({ meFirst, meInitial }: { meFirst: string; meInitial: string }) {
  const { t } = useTranslation();
  return (
    <>
      <span className="hiw-spark" style={d("1.9s")} />
      <Sticky d="3.2s" r pos={{ left: "52%", top: "10%" }}>{t("conn.hiwStickySameConv")}</Sticky>
      <SlackWin>
        <SlackRail active="launch-room" />
        <div className="hiw-slmain">
          <div className="hiw-slhead"># launch-room <span className="hiw-sub">{t("conn.hiwMembers", { count: 24 })}</span></div>
          <div className="hiw-slmsgs">
            <SlackDate label={t("conn.hiwToday")} />
            <Msg av="P" avBg="#7c6cd0" name="Priya N" ts="6:31 PM">
              {t("conn.hiwSignups")}
            </Msg>
            <Msg
              av={meInitial} avBg="#3b82c4" name={meFirst} ts="6:33 PM"
              extra={
                <span className="hiw-replybar">
                  <span className="hiw-sav2">OW</span> {t("conn.hiwReplies", { count: 2 })}
                  <span className="hiw-later">{t("conn.hiwTodayAt", { time: "6:36 PM" })}</span>
                </span>
              }
            >
              <span className="hiw-men">@OpenWorker</span> {t("conn.hiwSummarizeThread")}
            </Msg>
          </div>
          <SlackComposer placeholder={t("conn.hiwMessageChannel", { channel: "launch-room" })} />
          {/* thread panel open from the start — the new mentions play INSIDE it */}
          <div className="hiw-slthread">
            <div className="hiw-th">{t("conn.hiwThread")} <span className="hiw-sub"># launch-room</span><span className="hiw-x">✕</span></div>
            <div className="hiw-tmsgs">
              <Msg av={meInitial} avBg="#3b82c4" name={meFirst} ts="6:33 PM">
                <span className="hiw-men">@OpenWorker</span> {t("conn.hiwSummarizeThread")}
              </Msg>
              <div className="hiw-cnt">{t("conn.hiwReplies", { count: 2 })}</div>
              <Msg av="OW" avBg="#4a154b" name="OpenWorker" app ts="6:34 PM">
                {t("conn.hiwLaunchTractionShort")}
              </Msg>
              <Msg av="P" avBg="#7c6cd0" name="Priya N" ts="6:36 PM" delay=".8s">
                <span className="hiw-men">@OpenWorker</span> {t("conn.hiwBreakDown")}
              </Msg>
              <Msg av="OW" avBg="#4a154b" name="OpenWorker" app ts="6:36 PM" delay="4.8s">
                {t("conn.hiwTopCountries")}
              </Msg>
            </div>
            <div className="hiw-treply">{t("conn.hiwReply")}</div>
          </div>
        </div>
      </SlackWin>
      <OwWin>
        <div className="hiw-owrail">
          <div className="hiw-brand">OpenWorker</div>
          <div className="hiw-newbtn">{t("conn.hiwNewSession")}</div>
          <div className="hiw-ownav">{t("conn.hiwSearch")}</div>
          <div className="hiw-ownav">{t("conn.hiwAutomations")}</div>
          <div className="hiw-sect">{t("conn.hiwRecent")}</div>
          <div className="hiw-sess hot hiw-stay hiw-glow" style={{ "--g": "2.4s" } as React.CSSProperties}>
            <b>{t("conn.hiwSummarizeChannel", { channel: "launch-room" })}</b>{t("conn.hiwViaSlack")}
          </div>
          <div className="hiw-sess"><b>Jira vs Linear</b>Coworker</div>
        </div>
        <div className="hiw-owmain">
          <div className="hiw-owtitle">
            {t("conn.hiwSummarizeChannel", { channel: "launch-room" })} <span className="hiw-via">{t("conn.hiwViaSlackSameSession")}</span>
          </div>
          <div className="hiw-owchat">
            <div className="hiw-bub agent hiw-stay">{t("conn.hiwSignupsShort")}</div>
            <div className="hiw-bub user hiw-k" style={d("2.6s")}>{t("conn.hiwBreakDown")}</div>
            <div className="hiw-bub agent hiw-k" style={d("3.8s")}>
              {t("conn.hiwTopCountriesFull")}
            </div>
          </div>
          <div className="hiw-owcomposer">{t("conn.hiwMessageOpenWorker")}</div>
        </div>
      </OwWin>
    </>
  );
}

/* ---- scene 3: a teammate's first mention waits for your OK ---- */
function SceneTeammates() {
  const { t } = useTranslation();
  return (
    <>
      <span className="hiw-spark" style={d("1.9s")} />
      <Sticky d="3.4s" pos={{ left: "53%", bottom: "10%" }}>{t("conn.hiwStickyFirstTime")}</Sticky>
      <SlackWin>
        <SlackRail active="launch-room" />
        <div className="hiw-slmain">
          <div className="hiw-slhead"># launch-room <span className="hiw-sub">{t("conn.hiwMembers", { count: 24 })}</span></div>
          <div className="hiw-slmsgs">
            <SlackDate label={t("conn.hiwToday")} />
            <Msg
              av="P" avBg="#7c6cd0" name="Priya N" ts="6:41 PM" delay=".7s"
              extra={
                <span className="hiw-replybar hiw-k" style={d("5.6s")}>
                  <span className="hiw-sav2">OW</span> {t("conn.hiwReplies", { count: 1 })}
                  <span className="hiw-later">{t("conn.hiwAfterYouAllow")}</span>
                </span>
              }
            >
              <span className="hiw-men">@OpenWorker</span> {t("conn.hiwPullSignups")}
            </Msg>
          </div>
          <SlackComposer placeholder={t("conn.hiwMessageChannel", { channel: "launch-room" })} />
        </div>
      </SlackWin>
      <OwWin>
        <OwRail hot={t("conn.hiwSummarizeChannel", { channel: "launch-room" })} hotSub={t("conn.hiwViaSlack")} />
        <div className="hiw-owmain">
          <div className="hiw-owtitle">{t("conn.hiwSlackWorkspace", { name: WS_NAME })}</div>
          <div className="hiw-waitrow hiw-k hiw-glow" style={d("2s", { "--g": "2.5s" })}>
            <span className="min-w-0"><b>Priya N</b> {t("conn.hiwIsWaitingSuffix")}</span>
            <span className="hiw-allowbtn ml-auto">{t("manageTabs.allowAndDeliver")}</span>
          </div>
          <div className="hiw-waitcap hiw-k" style={d("3.4s")}>
            {t("conn.hiwWaitCaption")}
          </div>
        </div>
      </OwWin>
    </>
  );
}
