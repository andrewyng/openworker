import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  beginBrowserManagedConnect,
  beginManagedConnectOnMachine,
  connectManagedGrantSealed,
  getCloudBase,
  isCloudMode,
  connectConnectorSealed,
  delegateConnection,
  delegateConnector,
  deployMachineSecrets,
  deploySealedSecrets,
  disconnectConnector,
  forgetConnectorLocal,
  forgetConnectorOnMachine,
  forgetHolder,
  getCloudConnections,
  getConnectorHandoffInfo,
  getConnectors,
  getMachineConnectorsStrict,
  getInboxRouting,
  getMachines,
  getRecentChannels,
  handoffSealedFromMachine,
  hasWallet,
  revokeCloudConnections,
  setInboxBinding,
  updateConnectorTools,
  type CloudConnection,
  type Connector,
  type Machine,
} from "../api";
import { sealTo } from "../seal";
import { ConnectorBadge } from "../connectors/ConnectorIcon";
import { useMachineData } from "../useMachineData";
import { CachedNote, LoadingRow, UnreachableRow } from "./ScopedStatus";
import { FOOT, GRP, GRP_H, PILL_ACCENT, PILL_LINE, TAG_QUIET } from "./connectors/ui";

// Settings ▸ Connectors under a MACHINE scope (union view): that machine's
// own connector list, with the flows a headless box can actually run.
// Manual token connect seals the fields IN THIS TAB to the machine's pinned
// key — the desktop's acceptor or our cloud only ever relays ciphertext, and
// the box validates exactly as if the token were typed locally. Anything
// needing a browser sign-in (managed OAuth, MCP OAuth) stays on the
// controller's own scope; those connectors show why instead of a dead button.

const ROW = "flex items-start gap-3 px-4 py-3";

// Inbound connectors get a Configure link to their glance page (UX-049).
const INBOUND = new Set(["slack", "github"]);

/** The broker's machine id for a GUI machine row: cloud rows carry the
 * `cloud:` prefix the broker never sees; local-registry machines are
 * unknown to the broker (their delegation is the anonymous holder). */
function brokerMachineId(m: Machine): string {
  if (m.origin === "cloud") return m.id.replace(/^cloud:/, "");
  // On the hosted dashboard every row is a cloud machine and ids carry no prefix.
  return isCloudMode() ? m.id : "";
}

export function RemoteConnectorsPanel({
  machine,
  onConfigure,
}: {
  machine: Machine;
  // Opens the connector's glance page (Settings ▸ Slack / GitHub).
  onConfigure?: (name: string) => void;
}) {
  const { t } = useTranslation();
  const { data, cachedAt, loading, error, refresh } = useMachineData<Connector[]>(
    `connectors:${machine.id}`,
    // Strict variant, and it must live in api.ts: a component-level fetch
    // bypasses the module's auth wrapper (no sidecar token — found live).
    () => getMachineConnectorsStrict(machine.id),
  );
  const [open, setOpen] = useState<string | null>(null);
  // Which connectors THIS controller holds a grant for — feeds the "Move
  // from This Mac" affordance on rows the machine doesn't have yet.
  const [localConnected, setLocalConnected] = useState<Set<string>>(new Set());
  // The cloud's view (UX-049): which of the user's machines hold each grant,
  // so a connector held elsewhere offers Enable here instead of a new sign-in.
  const [cloudConns, setCloudConns] = useState<Record<string, CloudConnection>>({});
  const [machines, setMachines] = useState<Machine[]>([]);
  const loadCloud = () => {
    getCloudConnections()
      .then((rows) => {
        const map: Record<string, CloudConnection> = {};
        for (const r of rows) if (r.status !== "disconnected") map[r.connector] = r;
        setCloudConns(map);
      })
      .catch(() => {});
    getMachines()
      .then((r) => setMachines(r.machines))
      .catch(() => {});
  };
  useEffect(() => {
    getConnectors()
      .then((cs) =>
        setLocalConnected(new Set(cs.filter((c) => c.connected).map((c) => c.name))),
      )
      .catch(() => {});
    loadCloud();
  }, []);

  if (loading && !data) return <LoadingRow what={t("machines.connectors.loading_what", { name: machine.name })} />;
  if (error && !data) return <UnreachableRow machineName={machine.name} />;

  const connectors = data ?? [];
  const connected = connectors.filter((c) => c.connected);
  const mine = brokerMachineId(machine);
  // Held on another machine: the cloud lists a holder that is not this machine,
  // or This Mac holds it. Those rows get Enable (copy) or Move here.
  const heldElsewhere = (c: Connector): boolean => {
    if (localConnected.has(c.name)) return true;
    const cc = cloudConns[c.name];
    return !!cc && cc.holders.some((h) => h.machine_id && h.machine_id !== mine);
  };
  const notHere = connectors.filter((c) => !c.connected && c.available);
  const held = notHere.filter(heldElsewhere);
  const available = notHere.filter((c) => !heldElsewhere(c));
  const refreshAll = () => {
    refresh();
    loadCloud();
  };

  return (
    <div>
      {loading && cachedAt ? <CachedNote at={cachedAt} /> : null}

      {connected.length > 0 && (
        <>
          <div className={GRP_H}>{t("machines.connectors.connected_here")}</div>
          <div className={GRP}>
            {connected.map((c) => (
              <ConnectedRow
                key={c.name}
                c={c}
                machine={machine}
                cloud={cloudConns[c.name]}
                machines={machines}
                open={open === c.name}
                onToggle={() => setOpen(open === c.name ? null : c.name)}
                onChanged={refreshAll}
                onConfigure={onConfigure}
              />
            ))}
          </div>
        </>
      )}

      {held.length > 0 && (
        <>
          <div className={GRP_H}>{t("machines.connectors.held_elsewhere")}</div>
          <div className={GRP}>
            {held.map((c) => (
              <HeldRow
                key={c.name}
                c={c}
                machine={machine}
                cloud={cloudConns[c.name]}
                machines={machines}
                localConnected={localConnected.has(c.name)}
                onConnected={refreshAll}
              />
            ))}
          </div>
          <div className={FOOT}>
            {t("machines.connectors.held_foot")}
          </div>
        </>
      )}

      <div className={GRP_H}>{t("machines.connectors.available")}</div>
      <div className={GRP}>
        {available.map((c) => (
          <AvailableRow
            key={c.name}
            c={c}
            machine={machine}
            localConnected={false}
            open={open === c.name}
            onToggle={() => setOpen(open === c.name ? null : c.name)}
            onConnected={refreshAll}
          />
        ))}
      </div>
      <div className={FOOT}>
        {t("machines.connectors.tokens_foot")}
      </div>
    </div>
  );
}

function ConnectedRow({
  c,
  machine,
  cloud,
  machines,
  open,
  onToggle,
  onChanged,
  onConfigure,
}: {
  c: Connector;
  machine: Machine;
  cloud?: CloudConnection;
  machines: Machine[];
  open: boolean;
  onToggle: () => void;
  onChanged: () => void;
  onConfigure?: (name: string) => void;
}) {
  const { t } = useTranslation();
  const [busy, setBusy] = useState(false);
  const tools = c.tools || [];
  const mine = brokerMachineId(machine);
  const nameOf = (id: string) =>
    id === "desktop" ? t("machines.this_mac") : machines.find((m) => brokerMachineId(m) === id)?.name || id;
  const others = (cloud?.holders || [])
    .map((h) => h.machine_id)
    .filter((id) => id && id !== mine)
    .map(nameOf);
  const defaultName = cloud?.default_machine_id
    ? nameOf(cloud.default_machine_id)
    : cloud?.holders?.[0]?.machine_id
      ? nameOf(cloud.holders[0].machine_id)
      : "";

  const disconnect = async () => {
    // Disconnect HERE (UX-049): this machine's copy goes and its holder note
    // at the broker with it; other holders keep working. Only the last copy
    // of a managed grant ends the connection at the broker.
    const last = !cloud || cloud.holders.filter((h) => h.machine_id && h.machine_id !== mine).length === 0;
    const what = last
      ? t("machines.connectors.confirm_disconnect_last", { title: c.title, name: machine.name })
      : t("machines.connectors.confirm_disconnect_here", { title: c.title, name: machine.name });
    if (!window.confirm(what)) return;
    setBusy(true);
    await disconnectConnector(c.name, machine.id);
    if (cloud && mine && !last) await forgetHolder(cloud.connection_id, mine).catch(() => {});
    else if (c.managed_profile || c.managed) await revokeCloudConnections(c.name).catch(() => {});
    setBusy(false);
    onChanged();
  };

  const flipTool = async (name: string, enabled: boolean) => {
    await updateConnectorTools(c.name, { [name]: enabled }, machine.id);
    onChanged();
  };

  return (
    <div data-testid={`remote-connector-${c.name}`}>
      <div className={ROW}>
        <ConnectorBadge connector={c} size={32} title={c.title} />
        <div className="min-w-0 flex-1">
          <div className="text-ui font-medium text-ink">{c.title}</div>
          <div className="text-meta text-muted truncate" data-testid={`remote-holders-${c.name}`}>
            {c.account ? c.account : t("machines.connectors.connected")}
            {others.length > 0 &&
              " · " + t("machines.connectors.also_on", { machines: others.join(", ") })}
            {INBOUND.has(c.name) &&
              defaultName &&
              " · " + t("machines.connectors.answers_default", { name: defaultName })}
          </div>
        </div>
        {tools.length > 0 && (
          <button className={PILL_LINE} onClick={onToggle}>
            {t("machines.connectors.tools", { count: tools.length })}
          </button>
        )}
        {c.name === "slack" && <ApprovalsRouting machine={machine} />}
        {INBOUND.has(c.name) && onConfigure && (
          <button
            className="text-ui text-accent px-2 py-1.5"
            onClick={() => onConfigure(c.name)}
            data-testid={`remote-configure-${c.name}`}
          >
            {t("inbox.configure_arrow")}
          </button>
        )}
        <button
          className="text-meta text-red-600 px-2 py-1.5"
          onClick={() => void disconnect()}
          disabled={busy}
          data-testid={`remote-disconnect-${c.name}`}
        >
          {others.length > 0 ? t("machines.connectors.disconnect_here") : t("connector.disconnect")}
        </button>
      </div>
      {open && tools.length > 0 && (
        <div className="px-4 pb-3 space-y-1.5">
          {tools.map((tool) => (
            <label key={tool.name} className="flex items-start gap-2.5 text-meta">
              <input
                type="checkbox"
                className="mt-0.5"
                checked={tool.enabled}
                onChange={(e) => void flipTool(tool.name, e.target.checked)}
              />
              <span>
                <span className="text-ink">{tool.label}</span>
                <span className="block text-muted">{tool.description}</span>
              </span>
            </label>
          ))}
        </div>
      )}
    </div>
  );
}

/** Approvals routing is PER MACHINE (UX-049 third review): where this
 * machine's sessions mirror their Inbox approvals — in-app, or a Slack
 * channel the machine has seen. Lives on the row, not the glance page. */
export function ApprovalsRouting({ machine }: { machine?: Machine | null }) {
  const { t } = useTranslation();
  const [target, setTarget] = useState<string>("");
  const [channels, setChannels] = useState<{ channel: string; name?: string | null }[]>([]);
  const mid = machine?.id ?? null;
  const load = () => {
    getInboxRouting(mid)
      .then((bs) => {
        const b = bs.find((x) => x.name === "slack");
        setTarget(b && b.channel ? `slack:${b.target}` : "");
      })
      .catch(() => {});
    getRecentChannels(mid).then(setChannels).catch(() => setChannels([]));
  };
  useEffect(load, [mid]);
  const change = async (v: string) => {
    setTarget(v);
    if (!v) await setInboxBinding("slack", null, "", mid);
    else await setInboxBinding("slack", "slack", v.replace(/^slack:/, ""), mid);
    load();
  };
  return (
    <span className="flex items-center gap-1.5 text-meta text-muted" title={t("machines.connectors.approvals_title")}>
      {t("machines.connectors.approvals_arrow")}
      <select
        className="text-meta px-1.5 py-0.5 rounded-md border border-line bg-paper text-ink outline-none max-w-[140px]"
        value={target}
        onChange={(e) => void change(e.target.value)}
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => e.stopPropagation()}
        data-testid="remote-approvals-slack"
      >
        <option value="">{t("machines.connectors.in_app")}</option>
        {channels
          .filter((ch) => ch.channel.startsWith("slack:"))
          .map((ch) => (
            <option key={ch.channel} value={ch.channel}>
              {ch.name ? `#${ch.name}` : ch.channel}
            </option>
          ))}
        {target && !channels.some((ch) => ch.channel === target) && <option value={target}>{target}</option>}
      </select>
    </span>
  );
}

/** A connector another of the user's machines already holds (UX-049):
 * Enable = copy it here, no sign-in; Move here = copy then the source
 * forgets. Rotating providers cannot be copied and keep the sign-in. */
function HeldRow({
  c,
  machine,
  cloud,
  machines,
  localConnected,
  onConnected,
}: {
  c: Connector;
  machine: Machine;
  cloud?: CloudConnection;
  machines: Machine[];
  localConnected: boolean;
  onConnected: () => void;
}) {
  const { t } = useTranslation();
  const [busy, setBusy] = useState<"" | "enable" | "move">("");
  const [error, setError] = useState<string | null>(null);
  const mine = brokerMachineId(machine);
  const copyable = cloud ? cloud.copyable : true;
  // Where the copy comes from: This Mac when it holds the grant (the wallet
  // path), else the first other holder the broker lists (box-to-box).
  const sourceHolder = (cloud?.holders || []).find((h) => h.machine_id && h.machine_id !== mine);
  const sourceMachine = sourceHolder
    ? machines.find((m) => brokerMachineId(m) === sourceHolder.machine_id)
    : undefined;
  const sourceName = localConnected
    ? t("machines.this_mac")
    : sourceMachine?.name || sourceHolder?.machine_id || t("machines.connectors.another_machine");
  const account = cloud?.provider_account ? `${cloud.provider_account} · ` : "";

  const enable = async (move: boolean) => {
    setError(null);
    const pub = machine.seal_pubkey || "";
    if (!pub) {
      setError(t("machines.connectors.no_seal_key"));
      return;
    }
    setBusy(move ? "move" : "enable");
    try {
      if (localConnected) {
        // Desktop → machine: the existing handoff, minus the forget for Enable.
        const info = await getConnectorHandoffInfo(c.name);
        if (!info.ok || !info.portable || !info.profiles?.length) {
          setError(
            info.reason === "refresh_binding"
              ? t("machines.connectors.cant_copy_refresh", { title: c.title })
              : t("machines.connectors.cant_copy", { title: c.title }),
          );
          return;
        }
        if (info.needs_delegation) {
          const grant = await delegateConnector(c.name, pub, mine || undefined);
          if (!grant.ok) {
            setError(grant.error || t("machines.connectors.delegate_failed"));
            return;
          }
        }
        const res = await deployMachineSecrets(machine.id, info.profiles);
        if (res.error) {
          setError(res.error);
          return;
        }
        if (move) await forgetConnectorLocal(c.name);
      } else {
        if (!cloud || !sourceHolder || !sourceMachine) {
          setError(t("machines.connectors.holder_unreachable", { title: c.title }));
          return;
        }
        // Machine → machine: a grant for this machine, then the source seals
        // its copy to this machine's key; the cloud relays ciphertext only.
        const grant = await delegateConnection(cloud.connection_id, pub, mine);
        if (!grant.ok) {
          setError(grant.error || t("machines.connectors.delegate_failed"));
          return;
        }
        const sealed = await handoffSealedFromMachine(sourceMachine.id, c.name, pub, {
          user_id: grant.user_id,
          machine_credential: grant.machine_credential,
        });
        if (!sealed.ok || !sealed.sealed_b64) {
          setError(sealed.error || t("machines.connectors.cant_copy_from", { title: c.title, source: sourceName }));
          return;
        }
        const res = await deploySealedSecrets(machine.id, sealed.sealed_b64, sealed.profiles || [], {});
        if (res.error) {
          setError(res.error);
          return;
        }
        if (move) {
          await forgetConnectorOnMachine(sourceMachine.id, c.name).catch(() => {});
          await forgetHolder(cloud.connection_id, sourceHolder.machine_id).catch(() => {});
        }
      }
      onConnected();
    } finally {
      setBusy("");
    }
  };

  return (
    <div data-testid={`remote-held-${c.name}`}>
      <div className={ROW}>
        <ConnectorBadge connector={c} size={32} title={c.title} />
        <div className="min-w-0 flex-1">
          <div className="text-ui font-medium text-ink">{c.title}</div>
          <div className="text-meta text-muted truncate">
            {account}
            {t("machines.connectors.held_on", { source: sourceName })}
            {!copyable && " · " + t("machines.connectors.separate_signin")}
          </div>
        </div>
        {copyable ? (
          <>
            <button
              className={PILL_ACCENT}
              onClick={() => void enable(false)}
              disabled={!!busy}
              title={t("machines.connectors.enable_title")}
              data-testid={`remote-enable-${c.name}`}
            >
              {busy === "enable" ? t("machines.connectors.enabling") : t("machines.connectors.enable")}
            </button>
            <button
              className="text-meta text-muted px-2 py-1.5"
              onClick={() => void enable(true)}
              disabled={!!busy}
              data-testid={`remote-move-${c.name}`}
            >
              {busy === "move" ? t("machines.connectors.moving") : t("machines.connectors.move_here")}
            </button>
          </>
        ) : (
          <RotatingConnect c={c} machine={machine} onConnected={onConnected} />
        )}
      </div>
      {error && <div className="px-4 pb-3 text-meta text-red-600">{error}</div>}
    </div>
  );
}

/** The sign-in for a provider that cannot be copied: the plain available
 * row's connect affordances, nothing else. */
function RotatingConnect({ c, machine, onConnected }: { c: Connector; machine: Machine; onConnected: () => void }) {
  const [open, setOpen] = useState(false);
  return (
    <AvailableRow
      c={c}
      machine={machine}
      localConnected={false}
      open={open}
      onToggle={() => setOpen(!open)}
      onConnected={onConnected}
      bare
    />
  );
}

function AvailableRow({
  c,
  machine,
  localConnected,
  open,
  onToggle,
  onConnected,
  bare = false,
}: {
  c: Connector;
  machine: Machine;
  localConnected: boolean;
  open: boolean;
  onToggle: () => void;
  onConnected: () => void;
  // Render only the connect affordances (hosted inside another row).
  bare?: boolean;
}) {
  const { t } = useTranslation();
  const [values, setValues] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [moving, setMoving] = useState(false);
  const [awaiting, setAwaiting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const manual = c.fields.length > 0;
  // Connect-direct-to-machine: managed OAuth completes in this browser, and
  // the sidecar ships the grant straight to the machine — so it's a desktop
  // affordance (the sidecar owns the OAuth loopback). GitHub included: its
  // callback stages every returned installation (the Move unit) — metadata
  // plus the machine credential, no secrets.
  // The hosted dashboard has no sidecar loopback; there the broker hands the
  // result back to this tab by postMessage and the tab seals it to the
  // machine (spec §Cloud-dashboard connect-direct).
  const browserDirect = c.managed && !c.managed_paused && !hasWallet() && isCloudMode() && !!getCloudBase();
  const directConnect = (c.managed && !c.managed_paused && hasWallet()) || browserDirect;

  // Browser connect-direct: the broker's popup posts the grant back here,
  // pinned to our origin. Seal it to the machine and relay ciphertext.
  useEffect(() => {
    if (!browserDirect || !awaiting) return;
    const onMessage = async (ev: MessageEvent) => {
      if (ev.origin !== getCloudBase()) return;
      const d = ev.data;
      if (!d || d.type !== "openworker-managed-grant" || d.connector !== c.name) return;
      if (d.error) {
        setAwaiting(false);
        setError(String(d.error));
        return;
      }
      const pub = machine.seal_pubkey || "";
      const { type: _t, machine_credential, broker_user_id, machine_id: _m, ...form } = d as Record<string, string>;
      const sealed = sealTo(
        pub,
        new TextEncoder().encode(JSON.stringify({ form, machine_credential, broker_user_id })),
      );
      const res = await connectManagedGrantSealed(machine.id, c.name, sealed);
      setAwaiting(false);
      if (!res.ok) setError(res.error || t("machines.connectors.store_failed"));
      else onConnected();
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [browserDirect, awaiting, c.name, machine.id, machine.seal_pubkey]);

  // While the browser flow is out, poll the machine's list so the row flips
  // to Connected on its own once the grant lands.
  useEffect(() => {
    if (!awaiting) return;
    let ticks = 0;
    const timer = window.setInterval(() => {
      onConnected();
      if (++ticks >= 45) setAwaiting(false); // ~3 min, then stop quietly
    }, 4000);
    return () => window.clearInterval(timer);
  }, [awaiting]);

  const connectInBrowser = async () => {
    setError(null);
    if (browserDirect) {
      const pub = machine.seal_pubkey || "";
      if (!pub) {
        setError(t("machines.connectors.no_seal_key"));
        return;
      }
      // Open the popup FIRST (same user gesture) so blockers let it through.
      const popup = window.open("about:blank", "openworker-connect", "popup,width=640,height=760");
      const res = await beginBrowserManagedConnect(c.name, machine.id, pub);
      if (!res.ok || !res.authorize_url) {
        popup?.close();
        setError(
          res.error === "not signed in"
            ? t("machines.connectors.sign_in_first")
            : res.error || t("machines.connectors.start_failed"),
        );
        return;
      }
      if (!popup) {
        setError(t("machines.connectors.popup_blocked"));
        return;
      }
      popup.location.href = res.authorize_url;
      setAwaiting(true);
      return;
    }
    const res = await beginManagedConnectOnMachine(machine.id, c.name, machine.name);
    if (!res.ok) {
      setError(
        res.error === "not signed in"
          ? t("machines.connectors.sign_in_cloud_first")
          : res.error || t("machines.connectors.start_failed"),
      );
      return;
    }
    setAwaiting(true);
  };

  // Grant handoff (one grant, one holder): deploy the connector's profile
  // keys sealed to this machine, then This Mac forgets its copy. Offered
  // only when This Mac holds the grant; portability is the server's call
  // (managed grants wait on the broker's machine-refresh ruling).
  const move = async () => {
    setError(null);
    const info = await getConnectorHandoffInfo(c.name);
    if (!info.ok || !info.portable || !info.profiles?.length) {
      setError(
        info.reason === "refresh_binding"
          ? t("machines.connectors.cant_move_refresh", { title: c.title })
          : t("machines.connectors.cant_move", { title: c.title }),
      );
      return;
    }
    if (
      !window.confirm(
        t("machines.connectors.confirm_move", { title: c.title, name: machine.name }),
      )
    )
      return;
    setMoving(true);
    // Managed grants first get their broker connections marked machine-held,
    // so the machine can renew them by possession after the move. The seal
    // key mints the machine credential too (managed events) — Slack's
    // inbound follows the grant onto the machine's sealed queue.
    if (info.needs_delegation) {
      const grant = await delegateConnector(
        c.name,
        machine.seal_pubkey || undefined,
        machine.origin === "cloud" ? machine.id.replace(/^cloud:/, "") : undefined,
      );
      if (!grant.ok) {
        setMoving(false);
        setError(grant.error || t("machines.connectors.delegate_failed"));
        return;
      }
    }
    const res = await deployMachineSecrets(machine.id, info.profiles);
    if (res.error) {
      setMoving(false);
      setError(res.error);
      return;
    }
    // This Mac forgets — LOCAL deletion only; a broker disconnect here would
    // revoke the delegation the machine now lives on.
    await forgetConnectorLocal(c.name);
    setMoving(false);
    onConnected();
  };

  const submit = async () => {
    const pub = machine.seal_pubkey || "";
    if (!pub) {
      setError(t("machines.connectors.no_seal_key"));
      return;
    }
    setBusy(true);
    setError(null);
    const sealed = sealTo(pub, new TextEncoder().encode(JSON.stringify({ fields: values })));
    const res = await connectConnectorSealed(machine.id, c.name, sealed);
    setBusy(false);
    if (res.ok) {
      setValues({});
      onToggle();
      onConnected();
    } else setError(res.error || t("machines.connectors.connect_failed"));
  };

  return (
    <div data-testid={bare ? undefined : `remote-available-${c.name}`} className={bare ? "contents" : undefined}>
      <div className={bare ? "contents" : ROW}>
        {!bare && <ConnectorBadge connector={c} size={32} title={c.title} />}
        {!bare && (
          <div className="min-w-0 flex-1">
            <div className="text-ui font-medium text-ink">{c.title}</div>
            <div className="text-meta text-muted truncate">{c.blurb}</div>
          </div>
        )}
        {localConnected && (
          <button
            className={PILL_LINE}
            onClick={() => void move()}
            disabled={moving}
            data-testid={`remote-move-${c.name}`}
          >
            {moving ? t("machines.connectors.moving") : t("machines.connectors.move_from_mac")}
          </button>
        )}
        {directConnect && (
          <button
            className={manual ? PILL_LINE : PILL_ACCENT}
            onClick={() => void connectInBrowser()}
            disabled={awaiting}
            data-testid={`remote-connect-browser-${c.name}`}
          >
            {awaiting ? t("machines.connectors.waiting_browser") : t("machines.connectors.connect_in_browser")}
          </button>
        )}
        {manual ? (
          <button
            className={PILL_ACCENT}
            onClick={onToggle}
            data-testid={`remote-connect-${c.name}`}
          >
            {t("connector.connect")}
          </button>
        ) : !localConnected && !directConnect ? (
          <span
            className={TAG_QUIET}
            title={t("machines.connectors.browser_signin_title", { title: c.title })}
          >
            {t("machines.connectors.browser_signin")}
          </span>
        ) : null}
      </div>
      {awaiting && (
        <div className="px-4 pb-3 text-meta text-muted">
          {t("machines.connectors.awaiting_note", { name: machine.name })}
        </div>
      )}
      {error && !open && <div className="px-4 pb-3 text-meta text-red-600">{error}</div>}
      {open && manual && (
        <div className="px-4 pb-3.5 space-y-2.5">
          {c.instructions.length > 0 && (
            <ol className="list-decimal pl-4 text-meta text-muted leading-relaxed space-y-1">
              {c.instructions.map((step, i) => (
                <li key={i}>{step}</li>
              ))}
            </ol>
          )}
          {c.fields.map((f) => (
            <label className="conn-field" key={f.key}>
              <span className="conn-field-label">
                {f.label}
                {!f.required && <em> {t("machines.connectors.optional")}</em>}
              </span>
              <input
                type={f.secret ? "password" : "text"}
                placeholder={f.placeholder}
                value={values[f.key] || ""}
                spellCheck={false}
                onChange={(e) => setValues({ ...values, [f.key]: e.target.value })}
              />
              {f.help && <span className="conn-field-help">{f.help}</span>}
            </label>
          ))}
          <div className="flex items-center gap-3">
            <button
              className={PILL_ACCENT}
              onClick={() => void submit()}
              disabled={busy}
              data-testid={`remote-connect-submit-${c.name}`}
            >
              {busy ? t("modal.validating") : t("machines.connectors.connect_on", { name: machine.name })}
            </button>
            <span className="text-meta text-faint">
              {t("machines.connectors.encrypted_note", { name: machine.name })}
            </span>
          </div>
          {error && <div className="text-meta text-red-600">{error}</div>}
        </div>
      )}
    </div>
  );
}
