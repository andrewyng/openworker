import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
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
const DAYS: Record<string, { labelKey: string; dow: string }> = {
  mon: { labelKey: "scheduled.monday", dow: "1" },
  tue: { labelKey: "scheduled.tuesday", dow: "2" },
  wed: { labelKey: "scheduled.wednesday", dow: "3" },
  thu: { labelKey: "scheduled.thursday", dow: "4" },
  fri: { labelKey: "scheduled.friday", dow: "5" },
  sat: { labelKey: "scheduled.saturday", dow: "6" },
  sun: { labelKey: "scheduled.sunday", dow: "0" },
  weekdays: { labelKey: "scheduled.weekdays", dow: "1-5" },
  daily: { labelKey: "scheduled.everyDay", dow: "*" },
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
  titleKey: string;
  blurbKey: string;
  cadenceKey: string; // the card's footer label
  conns: { name: string; whyKey: string }[]; // [] = no connections needed
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
    titleKey: "quickstart.github.title",
    blurbKey: "quickstart.github.blurb",
    cadenceKey: "quickstart.github.cadence",
    conns: [
      { name: "slack", whyKey: "quickstart.why.slack.digest" },
      { name: "github", whyKey: "quickstart.why.github.digest" },
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
    titleKey: "quickstart.pipeline.title",
    blurbKey: "quickstart.pipeline.blurb",
    cadenceKey: "quickstart.pipeline.cadence",
    conns: [
      { name: "slack", whyKey: "quickstart.why.slack.pipeline" },
      { name: "hubspot", whyKey: "quickstart.why.hubspot.pipeline" },
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
    titleKey: "quickstart.brief.title",
    blurbKey: "quickstart.brief.blurb",
    cadenceKey: "quickstart.brief.cadence",
    conns: [
      { name: "google_calendar", whyKey: "quickstart.why.google_calendar" },
      { name: "gmail", whyKey: "quickstart.why.gmail.brief" },
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
    titleKey: "quickstart.news.title",
    blurbKey: "quickstart.news.blurb",
    cadenceKey: "quickstart.news.cadence",
    conns: [],
    day: "daily",
    time: "08:00",
    instructions: () =>
      "Search the web for the most important technology and world news from the last 24 hours " +
      "and write a concise 5-bullet briefing, saved as a markdown file.",
  },
  {
    key: "inboxdigest",
    titleKey: "quickstart.inboxdigest.title",
    blurbKey: "quickstart.inboxdigest.blurb",
    cadenceKey: "quickstart.inboxdigest.cadence",
    conns: [{ name: "gmail", whyKey: "quickstart.why.gmail.inbox" }],
    day: "weekdays",
    time: "09:00",
    instructions: () => "Summarize my unread email into one short digest note.",
  },
  {
    key: "cleanup",
    titleKey: "quickstart.cleanup.title",
    blurbKey: "quickstart.cleanup.blurb",
    cadenceKey: "quickstart.cleanup.cadence",
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
  const picked = TEMPLATES.find((tpl) => tpl.key === pickedKey) || null;
  const { t } = useTranslation();

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
      title: t(picked.titleKey),
      instructions: picked.instructions({ repo, channel, deliver }),
      cron: cronFor(day, time),
      permissions:
        picked.consent && consent && channel
          ? [{ tool: "send_message", target: channel, access: "write" }]
          : [],
    });
  };

  const gateHint = !allConnected
    ? t("quickstart.connectToContinue", {
        names: picked?.conns
          .filter((c) => !connState(c.name)?.connected)
          .map((c) => connState(c.name)?.title || c.name)
          .join(", "),
      })
    : picked?.needsChannel && !channel
      ? t("quickstart.pickChannelFirst")
      : "";

  const label = "block text-[12px] text-muted mt-3 mb-1";
  const input =
    "w-full px-3 py-2 rounded-lg border border-line bg-panel text-[13.5px] outline-none focus:border-accent";

  return (
    <div className="mb-4">
      <div className="text-[11px] uppercase tracking-[0.05em] text-faint mb-2.5">
        {t("quickstart.startFromTemplate")}
      </div>
      {/* Equal-height cards (owner ask 2026-07-12): 1fr rows + h-full — <button> grid items
          don't stretch like divs. */}
      <div className="grid grid-cols-3 auto-rows-fr gap-3">
        {TEMPLATES.map((tpl) => (
          <button
            key={tpl.key}
            data-testid={`qs-template-${tpl.key}`}
            className={
              "h-full text-left rounded-xl2 border bg-panel p-4 flex flex-col gap-1.5 " +
              (pickedKey === tpl.key
                ? "border-accent ring-2 ring-accentSoft"
                : "border-line hover:border-lineStrong")
            }
            onClick={() => pick(tpl)}
          >
            <span className="text-[13.5px] font-semibold">{t(tpl.titleKey)}</span>
            <span className="text-[12px] text-muted leading-relaxed flex-1">{t(tpl.blurbKey)}</span>
            <span className="flex items-center gap-1.5 mt-1">
              {tpl.conns.map((c) => {
                const cs = connState(c.name);
                const on = !!cs?.connected;
                return (
                  <span
                    key={c.name}
                    title={`${cs?.title || c.name} — ${on ? t("quickstart.connectedState") : t("quickstart.notConnectedYet")}`}
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
                {tpl.conns.length === 0
                  ? `${t("quickstart.noConnectionsNeeded")} · ${t(tpl.cadenceKey)}`
                  : t(tpl.cadenceKey)}
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
              {t("quickstart.setUp")}
            </span>
            <span className="text-[14px] font-semibold">{t(picked.titleKey)}</span>
            <span className="ml-auto text-[12px] text-faint max-sm:hidden">
              {picked.conns.length
                ? t("quickstart.connectionsDeliverySchedule")
                : t("quickstart.deliverySchedule")} ·{" "}
              {t(picked.cadenceKey)}
            </span>
          </div>
          {picked.conns.map(({ name, whyKey }) => {
            const c = connState(name);
            const flow = connFlow?.name === name ? connFlow : null;
            return (
              <div key={name} className="border-b border-line last:border-b-0">
                <div className="flex items-center gap-3 py-2.5">
                  {c && <ConnectorBadge connector={c} size={26} title={c.title} />}
                  <span className="min-w-0 flex-1">
                    <span className="block text-[13.5px] font-medium">{c?.title || name}</span>
                    <span className="block text-[11.5px] text-faint">{t(whyKey)}</span>
                  </span>
                  {c?.connected ? (
                    <span className="text-[12.5px] text-ok">{t("quickstart.connected")}</span>
                  ) : flow ? (
                    <span className="inline-flex items-center gap-2 text-[12px] text-muted">
                      <Spinner />
                      {flow.phase === "opening"
                        ? t("quickstart.openingBrowser")
                        : t("quickstart.waitingFor", { name: c?.title || name })}
                    </span>
                  ) : (
                    <button
                      className="px-3.5 py-1 rounded-full border border-line text-[12.5px] hover:bg-paper"
                      onClick={() => startConnect(name)}
                      data-testid={`ob-connect-${name}`}
                    >
                      {t("quickstart.connect")}
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
                        {t("quickstart.finishConnecting", { name: c?.title || name })}
                      </b>{" "}
                      {t("quickstart.approveAndComeBack")}
                    </span>
                    <button
                      className="text-faint underline hover:text-muted shrink-0"
                      onClick={() => setConnFlow(null)}
                      data-testid="ob-connect-cancel"
                    >
                      {t("quickstart.cancel")}
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
                {t("quickstart.oneSigninUnlocksAll")}
              </span>
              {t("quickstart.brokeredByCloud")}
              <div className="flex items-center gap-3 mt-2">
                {signinPhase ? (
                  <>
                    <span className="inline-flex items-center gap-2 text-[12px]">
                      <Spinner />
                      {signinPhase === "opening"
                        ? t("quickstart.openingBrowser")
                        : t("quickstart.waitingForSignin")}
                    </span>
                    {signinPhase === "waiting" && (
                      <span className="text-[11.5px] text-faint">
                        {t("quickstart.finishSigningIn")}{" "}
                        <button
                          className="underline hover:text-muted"
                          onClick={cancelSignin}
                          data-testid="ob-signin-cancel"
                        >
                          {t("quickstart.cancel")}
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
                    {t("cloud.signInCloud")}
                  </button>
                )}
              </div>
            </div>
          )}

          {allConnected && (
            <div className={picked.conns.length ? "bg-paper rounded-xl px-4 py-3.5 mt-3" : ""} data-testid="ob-recipe">
              {picked.needsRepo && (
                <>
                  <label className={label}>{t("quickstart.repository")}</label>
                  <input
                    className={input}
                    placeholder={t("quickstart.repoPlaceholder")}
                    value={repo}
                    onChange={(e) => setRepo(e.target.value)}
                    data-testid="ob-repo"
                  />
                </>
              )}
              {picked.needsChannel && (
                <>
                  <label className={label}>{t("quickstart.postToChannel")}</label>
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
                  <p className="text-[11px] text-warnInk mt-1">{t("quickstart.botMustBeMember")}</p>
                </>
              )}
              <label className={label}>{t("quickstart.when")}</label>
              <div className="flex gap-2">
                <div className="flex-1 min-w-0">
                  <SelectMenu
                    ariaLabel={t("quickstart.day")}
                    value={day}
                    options={Object.entries(DAYS).map(([k, v]) => ({ value: k, label: t(v.labelKey) }))}
                    onChange={setDay}
                  />
                </div>
                <input
                  className="w-28 px-3 py-2 rounded-lg border border-line bg-panel text-[13.5px] outline-none focus:border-accent"
                  type="time"
                  aria-label={t("quickstart.time")}
                  value={time}
                  onChange={(e) => setTime(e.target.value)}
                />
              </div>
              {picked.deliver && (
                <>
                  <label className={label}>{t("quickstart.deliverTo")}</label>
                  <SelectMenu
                    ariaLabel={t("quickstart.deliverTo")}
                    value={deliver}
                    options={[
                      { value: "app", label: t("quickstart.deliverApp") },
                      { value: "slack", label: t("quickstart.deliverSlack") },
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
                    {t("quickstart.consentBefore")}
                    <b className="text-ink" title={channel || undefined}>
                      {channelLabel || t("quickstart.theChannel")}
                      {channelWorkspace ? ` (${channelWorkspace})` : ""}
                    </b>
                    {t("quickstart.consentAfter")}
                  </span>
                </label>
              ) : picked.conns.length > 0 ? (
                <p className="text-[12.5px] text-muted mt-3">{t("quickstart.readsOnly")}</p>
              ) : null}
            </div>
          )}

          <div className="flex items-center gap-3 mt-4">
            <button
              className="text-[12.5px] text-faint hover:text-muted"
              onClick={() => setPickedKey(null)}
            >
              {t("quickstart.cancel")}
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
              {busy ? t("quickstart.creating") : t("quickstart.createAutomation")}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
