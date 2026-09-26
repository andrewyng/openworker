import { useEffect, useState } from "react";
import type { TFunction } from "i18next";
import { useTranslation } from "react-i18next";
import {
  addPerson,
  allowUser,
  disallowUser,
  getCloudConnections,
  getCloudStatus,
  getCloudSubscriptions,
  getConnectors,
  getMachines,
  getPeople,
  getPersonasIndex,
  getSlackStatus,
  getSubscriptions,
  isCloudMode,
  removeCloudSubscription,
  removePerson,
  resolveUnauthorized,
  setScopeRouting,
  unsubscribeChannel,
  type CloudConnection,
  type CloudStatus,
  type CloudSubscription,
  type Connector,
  type Machine,
  type ParkedMessage,
  type Person,
  type Persona,
  type SlackStatus,
  type Subscription,
} from "../../api";
import { ConnectorBadge } from "../../connectors/ConnectorIcon";
import { AddConnectionModal } from "./AddConnectionModal";
import { ConfigurationsSection, type MachineRef } from "./ConfigurationsSection";
import { SlackHowItWorks } from "./SlackHowItWorks";
import { FOOT, GRP, GRP_H, PILL_ACCENT, PILL_LINE, ROW, XBTN } from "./ui";

// UX-049 (owner, 2026-09-03/04): the per-connector glance page under Settings ▸ App.
// Everything on it is true for all of the user's machines. Per workspace (or
// GitHub installation): ONE routing line — mentions nobody subscribed to go to
// this machine and start a session with this coworker — and ONE People list:
// everyone on it may post and approve; the user is pinned. Below, who answers
// each channel. Moves are pulls (from a session's Access panel), so the table
// lists and unsubscribes only. Approvals routing is per machine and lives on
// that machine's Connectors row.

const TITLES: Record<string, string> = { slack: "Slack", github: "GitHub" };
const LABEL = "text-ui text-muted w-28 shrink-0";

function brokerId(m: Machine): string {
  if (m.origin === "cloud") return m.id.replace(/^cloud:/, "");
  return isCloudMode() ? m.id : "";
}

function glyph(m: Machine | undefined, id: string): string {
  if (id === "desktop") return "💻";
  return m?.provenance === "fly" ? "☁" : "🖥";
}

function relayLine(slack: SlackStatus | null, t: TFunction): string {
  if (!slack) return t("slack.relay_live");
  if (!slack.signed_in) return t("slack.relay_signin_needed");
  if (slack.relay.state === "offline") return t("slack.relay_offline");
  if (slack.relay.state === "reconnecting") return t("slack.relay_reconnecting");
  return t("slack.relay_live");
}

export function ConnectorGlance({
  connector,
  onManage,
}: {
  connector: "slack" | "github";
  // Opens this Mac's management page for the connector (add / disconnect a
  // workspace or installation, token health, the people picker).
  onManage?: (name: string) => void;
}) {
  const { t } = useTranslation();
  const [conn, setConn] = useState<CloudConnection | null>(null);
  const [machines, setMachines] = useState<Machine[]>([]);
  const [cloudSubs, setCloudSubs] = useState<CloudSubscription[]>([]);
  const [localSubs, setLocalSubs] = useState<Subscription[]>([]);
  const [people, setPeople] = useState<Person[]>([]);
  const [local, setLocal] = useState<Connector | null>(null);
  const [cloud, setCloud] = useState<CloudStatus | null>(null);
  const [slack, setSlack] = useState<SlackStatus | null>(null);
  const [personas, setPersonas] = useState<Record<string, Persona[]>>({});
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [adding, setAdding] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  // The dashboard has no engine of its own: a holder's box supplies the
  // workspace / installation list and the channel names (through the proxy).
  const [remote, setRemote] = useState<Connector | null>(null);
  const [remoteSubs, setRemoteSubs] = useState<Subscription[]>([]);
  // The page renders in one go once the holder read has settled — the
  // workspace section arrives after the cloud rows, and showing the table
  // first made it jump (owner report 2026-09-04).
  const [ready, setReady] = useState(false);
  const [settled, setSettled] = useState({ cloud: false, local: false, machines: false });
  const desktop = !isCloudMode();

  const load = () => {
    getCloudConnections()
      .then((rows) => setConn(rows.find((r) => r.connector === connector && r.status !== "disconnected") ?? null))
      .catch(() => setConn(null))
      .finally(() => setSettled((x) => ({ ...x, cloud: true })));
    getMachines()
      .then((r) => setMachines(r.machines))
      .catch(() => setMachines([]))
      .finally(() => setSettled((x) => ({ ...x, machines: true })));
    getCloudSubscriptions(connector).then(setCloudSubs).catch(() => setCloudSubs([]));
    getPeople(connector).then(setPeople).catch(() => setPeople([]));
    getConnectors()
      .then((cs) => setLocal(cs.find((c) => c.name === connector) ?? null))
      .catch(() => setLocal(null))
      .finally(() => setSettled((x) => ({ ...x, local: true })));
    if (desktop) {
      getSubscriptions().then(setLocalSubs).catch(() => setLocalSubs([]));
      getCloudStatus().then(setCloud).catch(() => setCloud(null));
      if (connector === "slack") getSlackStatus().then(setSlack).catch(() => setSlack(null));
    }
  };
  useEffect(load, [connector]);

  const title = TITLES[connector] ?? connector;
  const nameOf = (id: string): string => {
    if (id === "desktop" || !id) return desktop ? t("connglance.this_mac") : t("connglance.the_desktop");
    return machines.find((m) => brokerId(m) === id)?.name || id;
  };
  const machineOf = (id: string) => machines.find((m) => brokerId(m) === id);
  const guiIdOf = (id: string): string | null => (id === "desktop" ? null : machineOf(id)?.id ?? null);
  const holders = conn?.holders.map((h) => h.machine_id).filter(Boolean) ?? [];
  const heldHere = !!local?.connected && desktop;
  const holderIds = [...(heldHere ? ["desktop"] : []), ...holders.filter((h) => h !== "desktop")];
  // Where the workspace list comes from: This Mac when it holds the grant,
  // else the first holder's box.
  const source: Connector | null = heldHere ? local : remote;
  useEffect(() => {
    if (!(settled.cloud && settled.local && settled.machines)) return; // first paint waits for the three reads
    const boxes = holders.filter((h) => h !== "desktop").map((h) => machineOf(h)?.id).filter((x): x is string => !!x);
    if (heldHere || boxes.length === 0) {
      setReady(true);
      return;
    }
    let live = true;
    Promise.allSettled([
      getConnectors(boxes[0]).then((cs) => live && setRemote(cs.find((c) => c.name === connector) ?? null)),
      // Channel names live on whichever box holds the subscription: read them all.
      Promise.all(boxes.map((b) => getSubscriptions(b).catch(() => [] as Subscription[]))).then(
        (lists) => live && setRemoteSubs(lists.flat()),
      ),
    ]).then(() => live && setReady(true));
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conn, machines, local, settled]);
  const channelName = (source_: string): string | null =>
    [...localSubs, ...remoteSubs].find((s) => s.channel === source_)?.channel_name ?? null;

  // Scopes: the workspaces / installations the local engine knows, then any
  // the cloud rows mention.
  const scopes: { id: string; label: string; installer?: string; localAllowed: string[]; names: Record<string, string | null> }[] =
    connector === "slack"
      ? (source?.workspaces ?? []).map((w) => ({
          id: w.team_id,
          label: w.account || w.team_id,
          installer: w.installer_user_id,
          localAllowed: w.allowed_users,
          names: w.allowed_user_names ?? {},
        }))
      : (source?.installations ?? []).map((i) => ({
          id: i.installation_id,
          label: i.account_login || i.installation_id,
          installer: i.github_login,
          localAllowed: i.allowed_users,
          names: {},
        }));
  for (const p of people) if (!scopes.some((s) => s.id === p.scope)) scopes.push({ id: p.scope, label: p.scope, localAllowed: [], names: {} });
  for (const k of Object.keys(conn?.routing ?? {})) if (!scopes.some((s) => s.id === k)) scopes.push({ id: k, label: k, localAllowed: [], names: {} });

  // The coworker picker lists what the routed machine has installed.
  const routingFor = (scope: string) => {
    const r = conn?.routing?.[scope];
    return { machine_id: r?.machine_id || conn?.default_machine_id || holderIds[0] || "", persona: r?.persona || "" };
  };
  useEffect(() => {
    const wanted = new Set(scopes.map((s) => routingFor(s.id).machine_id).filter(Boolean));
    for (const id of wanted) {
      if (personas[id] !== undefined) continue;
      getPersonasIndex(guiIdOf(id))
        .then((r) => setPersonas((p) => ({ ...p, [id]: r.personas })))
        .catch(() => setPersonas((p) => ({ ...p, [id]: [] })));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conn, machines, local]);

  const setRouting = async (scope: string, machineId: string, persona?: string) => {
    if (!conn) return;
    setErr(null);
    const r = await setScopeRouting(conn.connection_id, scope, machineId, persona);
    if (!r.ok) setErr(r.error || t("connglance.err_routing"));
    load();
  };
  const add = async (scope: string) => {
    const member = (draft[scope] || "").trim();
    if (!member) return;
    setErr(null);
    const r = await addPerson(connector, scope, member);
    if (!r.ok && !desktop) setErr(r.error || t("connglance.err_add_person"));
    if (desktop && local?.connected) await allowUser(connector, member, scope).catch(() => {});
    setDraft((d) => ({ ...d, [scope]: "" }));
    load();
  };
  const drop = async (scope: string, member: string) => {
    await removePerson(connector, scope, member).catch(() => {});
    if (desktop && local?.connected) await disallowUser(connector, member, scope).catch(() => {});
    load();
  };
  const unsubscribeCloud = async (s: CloudSubscription) => {
    await removeCloudSubscription(s.source);
    load();
  };
  const unsubscribeLocal = async (s: Subscription) => {
    await unsubscribeChannel(s.session_id, s.channel);
    load();
  };

  const channelLabel = (source_: string): string => {
    const [, rest = ""] = source_.split(":", 2);
    if (connector === "slack") {
      const chan = rest.includes("/") ? rest.split("/")[1] : rest;
      if (chan.startsWith("T") || chan.startsWith("E")) return t("connglance.whole_workspace", { id: chan });
      const name = channelName(source_);
      return name ? `#${name}` : `#${chan}`;
    }
    return rest.includes("/") ? rest : t("connglance.org", { name: rest });
  };
  // Local-only subscriptions (Socket Mode, offline) that the cloud does not list.
  const localOnly = localSubs.filter(
    (s) => s.channel.startsWith(`${connector}:`) && !cloudSubs.some((c) => c.source === s.channel),
  );

  return (
    <div data-testid={`glance-${connector}`}>
      <div className="flex items-center gap-3.5 mb-5">
        {local ? <ConnectorBadge connector={local} size={44} title={title} /> : null}
        <div className="min-w-0 flex-1">
          <h2 className="text-title font-semibold tracking-tight leading-tight">{title}</h2>
          <div className="text-ui text-muted" data-testid="glance-holders">
            {connector === "slack" && heldHere && local?.mode === "relay" ? (
              <span data-testid="slack-mode-badge">{relayLine(slack, t)} · </span>
            ) : null}
            {holderIds.length === 0
              ? t("connglance.not_connected_anywhere")
              : t("connglance.on_machines", { machines: holderIds.map((id) => `${glyph(machineOf(id), id)} ${nameOf(id)}`).join(" · ") })}
          </div>
        </div>
        {desktop && local && onManage && (
          <button className={PILL_LINE} data-testid={`manage-local-${connector}`} onClick={() => onManage(connector)}>
            {t("connglance.manage_on_this_mac")}
          </button>
        )}
        {desktop && local && connector === "slack" && (
          <button className={PILL_ACCENT} data-testid="add-workspace-btn" onClick={() => setAdding(true)}>
            {t("slack.add_workspace")}
          </button>
        )}
      </div>

      {!ready && <div className={FOOT} data-testid="glance-loading">{t("connector.loading")}</div>}
      {ready && connector === "slack" && source?.workspaces?.length ? <SlackHowItWorks workspaces={source.workspaces} /> : null}

      {ready && scopes.length === 0 && holderIds.length === 0 ? (
        <div className={FOOT}>{t("connglance.connect_first", { title })}</div>
      ) : null}

      {ready && connector === "github" && (
        // One mechanism (owner, 2026-09-04): the GitHub page IS its
        // configurations; the installation default is a row like any other.
        <ConfigurationsSection
          holders={holderIds.map((id): MachineRef => ({ id, name: nameOf(id), glyph: glyph(machineOf(id), id), guiId: guiIdOf(id) }))}
          installationId={scopes[0]?.id ?? ""}
          owner={scopes[0]?.label ?? ""}
          cloudSubs={cloudSubs}
          onChanged={load}
        />
      )}

      {ready && connector === "slack" && scopes.map((scope) => {
        const routing = routingFor(scope.id);
        const list = personas[routing.machine_id] ?? [];
        const missing = !!routing.persona && list.length > 0 && !list.some((p) => p.id === routing.persona);
        // One list: the cloud's rows, plus anything only this Mac knows (Socket Mode).
        const members = new Map<string, { name: string; pinned: boolean; cloud: boolean }>();
        for (const p of people.filter((x) => x.scope === scope.id)) members.set(p.member, { name: p.name, pinned: !!p.pinned, cloud: true });
        if (desktop) for (const u of scope.localAllowed) if (!members.has(u)) members.set(u, { name: scope.names[u] || "", pinned: u === scope.installer, cloud: false });
        if (scope.installer && !members.has(scope.installer)) members.set(scope.installer, { name: "you", pinned: true, cloud: false });
        return (
          <div key={scope.id} data-testid={connector === "slack" ? `slack-workspace-${scope.id}` : `github-install-${scope.id}`}>
            <div className={GRP_H}>{scope.label}</div>
            <div className={GRP}>
              {conn && holderIds.length > 0 && (
                <div className={ROW} data-testid={`glance-routing-${scope.id}`}>
                  <span className={LABEL}>{t("connglance.mentions_go_to")}</span>
                  <select
                    className="text-ui px-2 py-1 rounded-lg border border-line bg-paper text-ink outline-none"
                    value={routing.machine_id}
                    onChange={(e) => void setRouting(scope.id, e.target.value)}
                    data-testid={`glance-machine-${scope.id}`}
                    aria-label={t("connconfig.label_machine")}
                  >
                    {holderIds.map((id) => (
                      <option key={id} value={id}>
                        {glyph(machineOf(id), id)} {nameOf(id)}
                      </option>
                    ))}
                  </select>
                  <span className="text-meta text-muted">·</span>
                  <select
                    className="text-ui px-2 py-1 rounded-lg border border-line bg-paper text-ink outline-none max-w-[200px]"
                    value={missing ? "" : routing.persona}
                    onChange={(e) => void setRouting(scope.id, "", e.target.value)}
                    data-testid={`glance-coworker-${scope.id}`}
                    aria-label={t("connconfig.label_coworker")}
                  >
                    <option value="">{t("connconfig.default_coworker")}</option>
                    {list.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.icon ? `${p.icon} ` : ""}{p.name}
                      </option>
                    ))}
                  </select>
                  <span className="text-meta text-muted">
                    {missing
                      ? t("connglance.coworker_missing", { coworker: routing.persona, machine: nameOf(routing.machine_id) })
                      : t("connglance.one_session_per_thread")}
                  </span>
                </div>
              )}
              <div className={ROW}>
                <span className={LABEL}>{t("connector.people")}</span>
                <div className="flex flex-wrap items-center gap-1.5 min-w-0 flex-1" data-testid={`glance-people-${scope.id}`}>
                  {[...members.entries()].map(([member, m]) => (
                    <span key={member} className="inline-flex items-center gap-1 text-ui border border-line rounded-full pl-2.5 pr-1 py-0.5" title={member}>
                      {m.pinned ? t("slack.you") : m.name || member}
                      {m.pinned ? (
                        <span className="pr-1.5" />
                      ) : (
                        <button className={XBTN} title={t("common.remove")} onClick={() => void drop(scope.id, member)} data-testid={`glance-person-x-${member}`}>
                          ×
                        </button>
                      )}
                    </span>
                  ))}
                  <input
                    className="text-ui px-2 py-0.5 rounded-full border border-dashed border-lineStrong bg-transparent outline-none w-44"
                    placeholder={connector === "slack" ? t("connglance.add_person_placeholder") : t("connglance.add_login_placeholder")}
                    value={draft[scope.id] || ""}
                    onChange={(e) => setDraft((d) => ({ ...d, [scope.id]: e.target.value }))}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") void add(scope.id);
                    }}
                    data-testid={`glance-person-input-${scope.id}`}
                  />
                </div>
              </div>
              {/* Parked senders and local-only listening rows are per machine (this Mac). */}
              {desktop && local &&
                (local.unauthorized ?? [])
                  .filter((m) => m.team_id === scope.id)
                  .map((m) => <WaitingRow key={m.id} connector={connector} m={m} onChanged={load} />)}
              {desktop && (() => {
                const here = localOnly.filter((s) => s.channel.startsWith(`${connector}:${scope.id}/`));
                return here.length ? <LocalListening subs={here} onChanged={load} /> : null;
              })()}
            </div>
          </div>
        );
      })}
      {ready && connector === "slack" && scopes.length > 0 && (
        <div className={FOOT}>
          {connector === "slack" ? t("connglance.people_foot_workspace") : t("connglance.people_foot_installation")}
        </div>
      )}

      {ready && connector === "slack" && (
      <>
      <div className={GRP_H}>
        {t(connector === "slack" ? "connglance.subscribed_channels" : "connglance.subscribed_repos", { n: cloudSubs.length + localOnly.length })}
      </div>
      <div className={GRP} data-testid="glance-subscriptions">
        {cloudSubs.length + localOnly.length === 0 ? (
          <div className={ROW}>
            <span className="text-meta text-muted">{connector === "slack" ? t("connglance.none_claimed_channel") : t("connglance.none_claimed_repo")}</span>
          </div>
        ) : null}
        {cloudSubs.map((s) => (
          <div className={ROW} key={s.source} data-testid={`glance-sub-${s.source}`}>
            <div className="min-w-0 flex-1">
              <div className="text-ui text-ink truncate">{channelLabel(s.source)}</div>
              <div className="text-meta text-muted truncate">
                {glyph(machineOf(s.machine_id), s.machine_id)} {nameOf(s.machine_id)} ·{" "}
                {s.state === "orphan" ? <span className="text-warnInk">{t("connconfig.session_gone")}</span> : s.title || s.session_id}
              </div>
            </div>
            <button className={PILL_LINE} onClick={() => void unsubscribeCloud(s)} data-testid={`glance-unsub-${s.source}`}>
              {s.state === "orphan" ? t("common.remove") : t("connconfig.unsubscribe")}
            </button>
          </div>
        ))}
        {localOnly.filter((s) => !scopes.some((sc) => s.channel.startsWith(`${connector}:${sc.id}/`))).map((s) => (
          <div className={ROW} key={`local-${s.session_id}-${s.channel}`} data-testid="listening-local">
            <div className="min-w-0 flex-1">
              <div className="text-ui text-ink truncate">{s.channel_name ? `#${s.channel_name}` : channelLabel(s.channel)}</div>
              <div className="text-meta text-muted truncate">💻 {t("connglance.this_mac")} · {s.session_title || s.session_id} · {t("connglance.local_only")}</div>
            </div>
            <button className={XBTN} title={t("connector.unsubscribe_title")} onClick={() => void unsubscribeLocal(s)}>
              ×
            </button>
          </div>
        ))}
      </div>
      <div className={FOOT}>
        {connector === "slack" ? t("connglance.move_foot_channel") : t("connglance.move_foot_repo")}
      </div>
      </>
      )}
      {err && <div className="text-meta text-red-600 mt-2">{err}</div>}
      {adding && local && (
        <AddConnectionModal c={local} cloud={cloud} title={t("slack.add_workspace_title")} onClose={() => setAdding(false)} onChanged={load} />
      )}
    </div>
  );
}


function WaitingRow({ connector, m, onChanged }: { connector: string; m: ParkedMessage; onChanged: () => void }) {
  const { t } = useTranslation();
  const act = async (action: "dismiss" | "allow" | "allow_deliver") => {
    await resolveUnauthorized(connector, m.id, action);
    onChanged();
  };
  return (
    <div className={ROW + " bg-warnSoft/25"} data-testid={`waiting-${m.id}`}>
      <span className={LABEL}>{t("connector.waiting")}</span>
      <span className="min-w-0 flex-1">
        <span className="font-medium text-ui">{m.user_name || m.user_id}</span>{" "}
        <span className="text-ui text-muted">{t("connector.in_channel", { name: m.chat_name || m.chat_id })}</span>
        <span className="block text-ui text-muted truncate">“{m.text}”</span>
      </span>
      <button className={PILL_ACCENT + " !py-1"} data-testid={`parked-allow-deliver-${m.id}`} title={t("slack.allow_deliver_title")} onClick={() => act("allow_deliver")}>
        {t("connector.allow_deliver")}
      </button>
      <button className={PILL_LINE + " !py-1"} data-testid={`parked-allow-${m.id}`} title={t("slack.allow_discard_title")} onClick={() => act("allow")}>
        {t("connector.allow")}
      </button>
      <button className={XBTN + " px-1"} data-testid={`parked-dismiss-${m.id}`} title={t("connector.dismiss")} onClick={() => act("dismiss")}>
        ×
      </button>
    </div>
  );
}

/** Subscriptions only this Mac knows (Socket Mode, or not yet registered). */
function LocalListening({ subs, onChanged }: { subs: Subscription[]; onChanged: () => void }) {
  const { t } = useTranslation();
  return (
    <div className={ROW} data-testid="listening-slack">
      <span className={LABEL}>{t("connector.listening")}</span>
      <span className="min-w-0 flex-1 space-y-1">
        {subs.map((s) => (
          <span key={s.session_id + s.channel} className="flex items-center gap-2 text-ui">
            <span className="font-medium truncate" title={s.session_id}>{s.session_title || s.session_id}</span>
            <span className="text-faint">←</span>
            <span className="text-muted truncate" title={s.channel}>{s.channel_name ? `#${s.channel_name}` : s.channel}</span>
            <span className="text-label text-faint">{t("connglance.this_mac_only")}</span>
            <button
              className={XBTN + " ml-auto"}
              title={t("connector.unsubscribe_title")}
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
