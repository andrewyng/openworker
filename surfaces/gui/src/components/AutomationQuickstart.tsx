import { useEffect, useRef, useState } from "react";
import { useTranslation, getI18n } from "react-i18next";
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
// Labels are i18n keys (resolved in the component via t()).
const DAYS: Record<string, { labelKey: string; dow: string }> = {
  mon: { labelKey: "automations.day_mon", dow: "1" },
  tue: { labelKey: "automations.day_tue", dow: "2" },
  wed: { labelKey: "automations.day_wed", dow: "3" },
  thu: { labelKey: "automations.day_thu", dow: "4" },
  fri: { labelKey: "automations.day_fri", dow: "5" },
  sat: { labelKey: "automations.day_sat", dow: "6" },
  sun: { labelKey: "automations.day_sun", dow: "0" },
  weekdays: { labelKey: "automations.freq_weekdays", dow: "1-5" },
  daily: { labelKey: "automations.freq_daily", dow: "*" },
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
    titleKey: "automations.tmpl_github_title",
    blurbKey: "automations.tmpl_github_blurb",
    cadenceKey: "automations.cadence_weekly",
    conns: [
      { name: "slack", whyKey: "automations.why_digest_posts" },
      { name: "github", whyKey: "automations.why_digest_summarizes" },
    ],
    needsRepo: true,
    needsChannel: true,
    consent: true,
    day: "mon",
    time: "09:00",
    instructions: ({ repo, channel }) => {
      const gt = getI18n().t;
      return gt("automations.tmpl_github_instructions", { repo: repo || gt("automations.tmpl_github_repo_default"), channel });
    },
  },
  {
    key: "pipeline",
    titleKey: "automations.tmpl_pipeline_title",
    blurbKey: "automations.tmpl_pipeline_blurb",
    cadenceKey: "automations.cadence_weekly",
    conns: [
      { name: "slack", whyKey: "automations.why_digest_posts" },
      { name: "hubspot", whyKey: "automations.why_pipeline_activity" },
    ],
    needsChannel: true,
    consent: true,
    day: "mon",
    time: "09:00",
    instructions: ({ channel }) => {
      const gt = getI18n().t;
      return gt("automations.tmpl_pipeline_instructions", { channel });
    },
  },
  {
    key: "brief",
    titleKey: "automations.tmpl_brief_title",
    blurbKey: "automations.tmpl_brief_blurb",
    cadenceKey: "automations.cadence_daily",
    conns: [
      { name: "google_calendar", whyKey: "automations.why_meetings_gaps" },
      { name: "gmail", whyKey: "automations.why_overnight_email" },
    ],
    deliver: true,
    day: "daily",
    time: "08:00",
    instructions: ({ deliver }) => {
      const gt = getI18n().t;
      return gt("automations.tmpl_brief_instructions_prefix") +
        (deliver === "app" ? gt("automations.tmpl_brief_save") : gt("automations.tmpl_brief_slack"));
    },
  },
  {
    key: "news",
    titleKey: "automations.tmpl_news_title",
    blurbKey: "automations.tmpl_news_blurb",
    cadenceKey: "automations.cadence_daily",
    conns: [],
    day: "daily",
    time: "08:00",
    instructions: () => getI18n().t("automations.tmpl_news_instructions"),
  },
  {
    key: "inboxdigest",
    titleKey: "automations.tmpl_inbox_title",
    blurbKey: "automations.tmpl_inbox_blurb",
    cadenceKey: "automations.cadence_weekdays",
    conns: [{ name: "gmail", whyKey: "automations.why_unread_email" }],
    day: "weekdays",
    time: "09:00",
    instructions: () => getI18n().t("automations.tmpl_inbox_instructions"),
  },
  {
    key: "cleanup",
    titleKey: "automations.tmpl_cleanup_title",
    blurbKey: "automations.tmpl_cleanup_blurb",
    cadenceKey: "automations.cadence_weekly",
    conns: [],
    day: "fri",
    time: "17:30",
    instructions: () => getI18n().t("automations.tmpl_cleanup_instructions"),
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
  const { t } = useTranslation();
  const [pickedKey, setPickedKey] = useState<string | null>(null);
  const picked = TEMPLATES.find((tpl) => tpl.key === pickedKey) || null;

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

  const pick = (tpl: QuickTemplate) => {
    setPickedKey(tpl.key);
    setDay(tpl.day);
    setTime(tpl.time);
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
    ? t("automations.gate_connect", {
        names: picked?.conns
          .filter((c) => !connState(c.name)?.connected)
          .map((c) => connState(c.name)?.title || c.name)
          .join(t("automations.gate_join")),
      })
    : picked?.needsChannel && !channel
      ? t("automations.gate_pick_channel")
      : "";

  const label = "block text-[12px] text-muted mt-3 mb-1";
  const input =
    "w-full px-3 py-2 rounded-lg border border-line bg-panel text-[13px] outline-none focus:border-accent";

  return (
    <div className="mb-4">
      <div className="text-[11px] uppercase tracking-[0.05em] text-faint mb-2.5">
        {t("automations.start_from_template")}
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
            <span className="text-[13px] font-semibold">{t(tpl.titleKey)}</span>
            <span className="text-[12px] text-muted leading-relaxed flex-1">{t(tpl.blurbKey)}</span>
            <span className="flex items-center gap-1.5 mt-1">
              {tpl.conns.map((c) => {
                const cs = connState(c.name);
                const on = !!cs?.connected;
                return (
                  <span
                    key={c.name}
                    title={`${cs?.title || c.name} — ${on ? t("automations.conn_connected") : t("automations.conn_not_connected")}`}
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
                {tpl.conns.length === 0 ? t("automations.no_conns_with_cadence", { cadence: t(tpl.cadenceKey) }) : t(tpl.cadenceKey)}
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
              {t("automations.set_up")}
            </span>
            <span className="text-[14px] font-semibold">{t(picked.titleKey)}</span>
            <span className="ml-auto text-[12px] text-faint max-sm:hidden">
              {picked.conns.length ? t("automations.conns_delivery_sched") : t("automations.delivery_sched")} ·{" "}
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
                    <span className="block text-[13px] font-medium">{c?.title || name}</span>
                    <span className="block text-[12px] text-faint">{t(whyKey)}</span>
                  </span>
                  {c?.connected ? (
                    <span className="text-[13px] text-ok">{t("automations.connected_ok")}</span>
                  ) : flow ? (
                    <span className="inline-flex items-center gap-2 text-[12px] text-muted">
                      <Spinner />
                      {flow.phase === "opening"
                        ? t("automations.opening_browser")
                        : t("automations.waiting_for", { name: c?.title || name })}
                    </span>
                  ) : (
                    <button
                      className="px-3.5 py-1 rounded-full border border-line text-[13px] hover:bg-paper"
                      onClick={() => startConnect(name)}
                      data-testid={`ob-connect-${name}`}
                    >
                      {t("automations.connect")}
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
                        {t("automations.finish_connecting", { name: c?.title || name })}
                      </b>{" "}
                      {t("automations.finish_connecting_desc")}
                    </span>
                    <button
                      className="text-faint underline hover:text-muted shrink-0"
                      onClick={() => setConnFlow(null)}
                      data-testid="ob-connect-cancel"
                    >
                      {t("automations.cancel")}
                    </button>
                  </div>
                )}
              </div>
            );
          })}

          {pendingConn && !cloud?.signed_in && (
            <div
              className="bg-accentSoft/50 rounded-xl px-4 py-3 mt-3 text-[13px] text-muted"
              data-testid="ob-cloudpane"
            >
              <span className="block text-[13px] text-ink font-medium">
                {t("automations.one_signin_unlocks")}
              </span>
              {t("automations.cloud_brokered")}
              <div className="flex items-center gap-3 mt-2">
                {signinPhase ? (
                  <>
                    <span className="inline-flex items-center gap-2 text-[12px]">
                      <Spinner />
                      {signinPhase === "opening" ? t("automations.opening_browser") : t("automations.waiting_signin")}
                    </span>
                    {signinPhase === "waiting" && (
                      <span className="text-[12px] text-faint">
                        {t("automations.finish_signin_desc")}{" "}
                        <button
                          className="underline hover:text-muted"
                          onClick={cancelSignin}
                          data-testid="ob-signin-cancel"
                        >
                          {t("automations.cancel")}
                        </button>
                      </span>
                    )}
                  </>
                ) : (
                  <button
                    className="px-3.5 py-1 rounded-full border border-line text-[13px] text-accent hover:bg-panel"
                    onClick={signInThenConnect}
                    data-testid="ob-cloud-signin"
                  >
                    {t("automations.sign_in_to_cloud")}
                  </button>
                )}
              </div>
            </div>
          )}

          {allConnected && (
            <div className={picked.conns.length ? "bg-paper rounded-xl px-4 py-3.5 mt-3" : ""} data-testid="ob-recipe">
              {picked.needsRepo && (
                <>
                  <label className={label}>{t("automations.repository")}</label>
                  <input
                    className={input}
                    placeholder={t("automations.repo_placeholder")}
                    value={repo}
                    onChange={(e) => setRepo(e.target.value)}
                    data-testid="ob-repo"
                  />
                </>
              )}
              {picked.needsChannel && (
                <>
                  <label className={label}>{t("automations.post_to_channel")}</label>
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
                    {t("automations.bot_member_hint")}
                  </p>
                </>
              )}
              <label className={label}>{t("automations.when")}</label>
              <div className="flex gap-2">
                <div className="flex-1 min-w-0">
                  <SelectMenu
                    ariaLabel={t("automations.day_aria")}
                    value={day}
                    options={Object.entries(DAYS).map(([k, v]) => ({ value: k, label: t(v.labelKey) }))}
                    onChange={setDay}
                  />
                </div>
                <input
                  className="w-28 px-3 py-2 rounded-lg border border-line bg-panel text-[13px] outline-none focus:border-accent"
                  type="time"
                  aria-label={t("automations.time_aria")}
                  value={time}
                  onChange={(e) => setTime(e.target.value)}
                />
              </div>
              {picked.deliver && (
                <>
                  <label className={label}>{t("automations.deliver_to")}</label>
                  <SelectMenu
                    ariaLabel={t("automations.deliver_to")}
                    value={deliver}
                    options={[
                      { value: "app", label: t("automations.deliver_app") },
                      { value: "slack", label: t("automations.deliver_slack") },
                    ]}
                    onChange={(v) => setDeliver(v as "app" | "slack")}
                  />
                </>
              )}
              {picked.consent ? (
                <label className="flex items-start gap-2.5 mt-3.5 text-[13px] text-muted select-none">
                  <input
                    type="checkbox"
                    className="mt-0.5"
                    checked={consent}
                    onChange={(e) => setConsent(e.target.checked)}
                    data-testid="ob-consent"
                  />
                  <span>
                    {t("automations.consent_prefix")}{" "}
                    <b className="text-ink" title={channel || undefined}>
                      {channelLabel || t("automations.the_channel")}
                      {channelWorkspace ? ` (${channelWorkspace})` : ""}
                    </b>{" "}
                    {t("automations.consent_suffix")}
                  </span>
                </label>
              ) : picked.conns.length > 0 ? (
                <p className="text-[13px] text-muted mt-3">
                  {t("automations.read_only_pref")} <b className="text-ink">{t("automations.reads")}</b> {t("automations.read_only_suff")}
                </p>
              ) : null}
            </div>
          )}

          <div className="flex items-center gap-3 mt-4">
            <button
              className="text-[13px] text-faint hover:text-muted"
              onClick={() => setPickedKey(null)}
            >
              {t("automations.cancel")}
            </button>
            {/* A silently-disabled primary reads as a bug — always name the missing piece. */}
            {gateHint && (
              <span className="ml-auto text-[12px] text-faint" data-testid="ob-create-hint">
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
              {busy ? t("automations.creating") : t("automations.create_btn")}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
