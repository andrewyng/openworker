import { useCallback, useEffect, useRef, useState } from "react";
import type { TFunction } from "i18next";
import { Trans, useTranslation } from "react-i18next";
import {
  approveDeviceRequest,
  armEnrollment,
  denyDeviceRequest,
  deployMachineSecrets,
  deploySealedSecrets,
  disarmEnrollment,
  getDeviceRequest,
  getMachines,
  MACHINES_CHANGED,
  getMachineSecrets,
  getWalletProfiles,
  removeMachine,
  createSandbox,
  deleteSandbox,
  getSandboxes,
  renameMachine,
  revokeMachineSecrets,
  type DeviceRequestDetail,
  type Machine,
  type MachineSecretRow,
} from "../api";
import {
  getCloudMachines,
  hasWallet,
  isCloudMode,
  type Sandbox,
  type SandboxesInfo,
  type CloudMachinesInfo,
} from "../api";
import { profileHash, sealProfiles } from "../seal";
import { consumeApprovalCode, peekApprovalCode } from "../routes";
import { keyPushAllowed } from "../cloudAuth";
import { Icon } from "./Icon";
import { PanelHead } from "./IntegrationsView";

// Settings ▸ Machines (UX-045 frame C/D). Rows model the connectors/accounts
// idiom: calm at rest, actions on hover. Opening "Add a machine" is what ARMS
// enrollment — it mints the single-use token and starts the 10-minute window;
// closing the card disarms. Already-joined machines reconnect anytime.

const ROW =
  "flex items-center gap-3 px-3.5 py-3 rounded-xl2 border border-line bg-panel mb-2.5 group";
const META = "text-label text-muted";
const ACT =
  "text-meta text-muted hover:text-ink opacity-0 group-hover:opacity-100 transition-opacity";

const POLL_MS = 2000;

function agoLabel(epochSeconds: number | null, t: TFunction): string {
  if (!epochSeconds) return t("machines.never_seen");
  const seconds = Math.max(0, Date.now() / 1000 - epochSeconds);
  if (seconds < 90) return t("inbox.rel_just_now");
  if (seconds < 3600) return t("machines.min_ago", { n: Math.round(seconds / 60) });
  if (seconds < 172800) return t("machines.hours_ago", { n: Math.round(seconds / 3600) });
  return t("machines.days_ago", { n: Math.round(seconds / 86400) });
}

function joinedLabel(epochSeconds: number): string {
  return new Date(epochSeconds * 1000).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
  });
}

export function MachinesSection() {
  const { t } = useTranslation();
  const [machines, setMachines] = useState<Machine[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [adding, setAdding] = useState<"arm" | "approve" | null>(null);
  const [keysFor, setKeysFor] = useState<string | null>(null);
  // #/approve/CODE deep link: open the approval card with the code filled
  // in. Consumed in the effect — reopening this page later must not replay it.
  const [deepLinkCode] = useState(() => peekApprovalCode());
  useEffect(() => {
    if (deepLinkCode) {
      consumeApprovalCode();
      setAdding("approve");
    }
  }, [deepLinkCode]);
  const [renaming, setRenaming] = useState<string | null>(null);
  const [renameText, setRenameText] = useState("");
  const [error, setError] = useState<string | null>(null);

  // Union view: the cloud registry's rows, shown in their own section on a
  // signed-in desktop. A cloud-session problem only ever degrades that
  // section — the local list above it is never blocked.
  const [cloud, setCloud] = useState<CloudMachinesInfo | null>(null);
  // Managed sandboxes (hosted only): the listing carries the caller's cap,
  // so nothing here depends on the sign-in gate having run.
  const [sandboxes, setSandboxes] = useState<SandboxesInfo>({ sandboxes: [], cap: 0, used: 0 });
  const [creating, setCreating] = useState(false);

  const knownIds = useRef<string>("");
  const refresh = useCallback(async () => {
    try {
      const d = await getMachines();
      const rows = d.machines ?? [];
      setMachines(rows);
      setLoaded(true);
      // Tell the Settings rail (and any other picker) when the fleet changed.
      const ids = rows.map((m) => m.id).sort().join(",");
      if (knownIds.current && ids !== knownIds.current) {
        window.dispatchEvent(new CustomEvent(MACHINES_CHANGED));
      }
      knownIds.current = ids || "-";
    } catch {
      /* sidecar unreachable — keep the last list */
    }
    if (!isCloudMode()) {
      try {
        setCloud(await getCloudMachines());
      } catch {
        /* keep the last cloud view */
      }
    } else {
      setSandboxes(await getSandboxes());
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Presence changes while the page is open (a box going down) should show
  // without a manual reload; slow poll only while the tab is visible — and a
  // quick one while a sandbox is still coming up, so the row flips on its own.
  const pending = sandboxes.sandboxes.some((s) => s.phase === "provisioning" || s.phase === "joining");
  useEffect(() => {
    const t = setInterval(() => void refresh(), pending ? 3000 : 15000);
    return () => clearInterval(t);
  }, [refresh, pending]);

  const newSandbox = async () => {
    setCreating(true);
    const r = await createSandbox();
    setCreating(false);
    if (r.error) setError(r.error);
    else setError(null);
    void refresh();
  };

  const removeSandbox = async (sb: Sandbox) => {
    if (
      !window.confirm(
        t("machines.confirm_delete_sandbox", { name: sb.name }),
      )
    )
      return;
    const r = await deleteSandbox(sb.id);
    if (r.error) setError(r.error);
    else setError(null);
    void refresh();
  };

  const submitRename = async (id: string) => {
    const name = renameText.trim();
    setRenaming(null);
    if (!name) return;
    const r = await renameMachine(id, name);
    if (r.error) setError(r.error);
    else setError(null);
    void refresh();
  };

  const doRemove = async (m: Machine) => {
    if (m.provenance === "fly" && m.provenance_ref) {
      const sb = sandboxes.sandboxes.find((s) => s.id === m.provenance_ref);
      if (sb) return removeSandbox(sb);
    }
    if (
      !window.confirm(
        t("machines.confirm_remove", { name: m.name }),
      )
    )
      return;
    const r = await removeMachine(m.id);
    if (r.error) setError(r.error);
    else setError(null);
    void refresh();
  };

  // One row renderer for both registries — a cloud machine is the same row
  // wearing a different origin; rename/remove/keys route by id prefix.
  const machineRow = (m: Machine) =>
    renaming === m.id ? (
      <div key={m.id} className={ROW}>
        <div className="w-8 h-8 rounded-lg bg-paper flex items-center justify-center text-body">
          ⌂
        </div>
        <input
          autoFocus
          className="flex-1 min-w-0 px-2.5 py-1.5 rounded-lg border border-line bg-paper text-ui text-ink outline-none focus:border-accent"
          value={renameText}
          onChange={(e) => setRenameText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") void submitRename(m.id);
            if (e.key === "Escape") setRenaming(null);
          }}
          onBlur={() => void submitRename(m.id)}
        />
      </div>
    ) : (
      <div key={m.id} className={ROW} data-testid={`machine-${m.name}`}>
        <div className="w-8 h-8 rounded-lg bg-paper flex items-center justify-center text-body">
          {m.provenance === "fly" ? "☁" : "⌂"}
        </div>
        <div className="min-w-0">
          <div className="text-ui font-medium text-ink truncate">
            {m.name}
            {m.provenance === "fly" && (
              <span
                className="ml-2 align-middle text-[10.5px] text-muted border border-line rounded px-1.5 py-[1px]"
                data-testid={`sandbox-tag-${m.name}`}
              >
                {t("machines.sandbox_tag")}
              </span>
            )}
          </div>
          <div className={META}>
            <span
              className={
                "inline-block w-[7px] h-[7px] rounded-full mr-1.5 align-middle " +
                (m.connected ? "bg-ok" : sandboxPhaseOf(m) === "asleep" ? "bg-accent" : "bg-faint")
              }
            />
            {m.connected
              ? t("machines.online")
              : sandboxPhaseOf(m) === "asleep"
                ? t("machines.asleep")
                : t("machines.last_seen", { ago: agoLabel(m.last_seen, t) })}
            {m.app_version ? ` · v${m.app_version}` : ""}
            {" · " + t("machines.joined", { date: joinedLabel(m.created_at) })}
            <span className="font-mono"> · {m.fingerprint.slice(0, 8)}…</span>
          </div>
        </div>
        <div className="ml-auto flex items-center gap-3.5">
          {/* Wallet-less backend (hosted): keys are sealed IN THE BROWSER
              and relayed — the per-machine Keys card is the deploy surface.
              Desktop keeps its wallet flows on the provider cards. */}
          {!hasWallet() && keyPushAllowed() && (
            <button
              className={ACT}
              onClick={() => setKeysFor(keysFor === m.id ? null : m.id)}
              data-testid={`keys-${m.name}`}
            >
              {t("machines.keys_action")}
            </button>
          )}
          <button
            className={ACT}
            onClick={() => {
              setRenaming(m.id);
              setRenameText(m.name);
            }}
          >
            {t("sidebar.rename")}
          </button>
          <button className={ACT} onClick={() => void doRemove(m)}>
            {t("common.remove")}
          </button>
        </div>
      </div>
    );

  const sandboxPhaseOf = (m: Machine) =>
    m.provenance === "fly"
      ? sandboxes.sandboxes.find((s) => s.id === m.provenance_ref)?.phase
      : undefined;

  // Sandboxes that have no machine row yet (still coming up, stuck, or
  // failed) get a row of their own so the click has visible consequences.
  const pendingSandboxRow = (sb: Sandbox) => (
    <div key={sb.id} className={ROW} data-testid={`sandbox-${sb.name}`}>
      <div className="w-8 h-8 rounded-lg bg-paper flex items-center justify-center text-body">
        ☁
      </div>
      <div className="min-w-0">
        <div className="text-ui font-medium text-ink truncate">
          {sb.name}
          <span className="ml-2 align-middle text-[10.5px] text-muted border border-line rounded px-1.5 py-[1px]">
            {t("machines.sandbox_tag")}
          </span>
        </div>
        <div className={META} data-testid={`sandbox-phase-${sb.name}`}>
          {sb.phase === "provisioning" && (
            <>
              <span className="inline-block w-[7px] h-[7px] rounded-full mr-1.5 align-middle bg-accent animate-pulse" />
              {t("machines.sandbox_provisioning")}
            </>
          )}
          {sb.phase === "joining" && (
            <>
              <span className="inline-block w-[7px] h-[7px] rounded-full mr-1.5 align-middle bg-accent animate-pulse" />
              {t("machines.sandbox_joining")}
            </>
          )}
          {sb.phase === "stuck" && (
            <>
              <span className="inline-block w-[7px] h-[7px] rounded-full mr-1.5 align-middle bg-faint" />
              {t("machines.sandbox_stuck")}
            </>
          )}
          {sb.phase === "failed" && (
            <span className="text-red-500">
              {t("machines.sandbox_failed", { error: sb.error || t("machines.unknown_error") })}
            </span>
          )}
          {!sb.mine && ` · ${sb.owner}`}
        </div>
      </div>
      {(sb.phase === "stuck" || sb.phase === "failed") && (
        <div className="ml-auto flex items-center gap-3.5">
          <button className={ACT} onClick={() => void removeSandbox(sb)}>
            {t("sidebar.delete")}
          </button>
        </div>
      )}
    </div>
  );
  const pendingSandboxes = sandboxes.sandboxes.filter((sb) => !sb.machine_id);
  const atCap = sandboxes.used >= sandboxes.cap;

  return (
    <section>
      <PanelHead
        title={t("machines.title")}
        sub={t("machines.sub")}
      />
      {error ? (
        <div className="text-meta text-red-500 mb-3">{error}</div>
      ) : null}

      {/* Cloud mode: the controller isn't a home — there is no This-Mac row. */}
      {!isCloudMode() && (
        <div className={ROW}>
          <div className="w-8 h-8 rounded-lg bg-paper flex items-center justify-center text-body">
            💻
          </div>
          <div>
            <div className="text-ui font-medium text-ink">{t("machines.this_mac")}</div>
            <div className={META}>{t("machines.always_available")}</div>
          </div>
        </div>
      )}

      {machines.map(machineRow)}
      {pendingSandboxes.map(pendingSandboxRow)}
      {keysFor && machines.some((m) => m.id === keysFor) && (
        <MachineKeysCard
          machine={machines.find((m) => m.id === keysFor)!}
          onClose={() => setKeysFor(null)}
        />
      )}

      {adding === "arm" ? (
        <AddMachineCard
          knownIds={machines.map((m) => m.id)}
          onJoined={() => void refresh()}
          onClose={() => {
            setAdding(null);
            void refresh();
          }}
        />
      ) : adding === "approve" ? (
        <ApproveMachineCard
          initialCode={deepLinkCode}
          onDecided={() => void refresh()}
          onClose={() => {
            setAdding(null);
            void refresh();
          }}
        />
      ) : (
        <div className="flex items-center gap-2.5 mt-1.5">
          {/* Managed sandboxes (spec §Fly sandboxes): offered only where the
              caller's cap is above zero; disabled once they are at it. */}
          {isCloudMode() && sandboxes.cap > 0 && (
            <button
              className="inline-flex items-center gap-2 px-3.5 py-2 rounded-lg border border-accent bg-panel text-ui text-ink hover:border-lineStrong disabled:opacity-50"
              onClick={() => void newSandbox()}
              disabled={!loaded || creating || atCap}
              title={
                atCap
                  ? t("machines.sandbox_cap_title", { used: sandboxes.used, cap: sandboxes.cap })
                  : t("machines.sandbox_create_title")
              }
              data-testid="new-sandbox-button"
            >
              <Icon name="plus" size={14} />
              {creating
                ? t("automations.creating")
                : t("machines.new_sandbox", { used: sandboxes.used, cap: sandboxes.cap })}
            </button>
          )}
          <button
            className="inline-flex items-center gap-2 px-3.5 py-2 rounded-lg border border-line bg-panel text-ui text-ink hover:border-lineStrong"
            onClick={() => setAdding("arm")}
            disabled={!loaded}
          >
            <Icon name="plus" size={14} /> {t("machines.add_machine")}
          </button>
          <button
            className="inline-flex items-center gap-2 px-3.5 py-2 rounded-lg border border-line bg-panel text-ui text-ink hover:border-lineStrong"
            onClick={() => setAdding("approve")}
            disabled={!loaded}
            data-testid="approve-machine-button"
          >
            <Icon name="shield" size={14} /> {t("machines.approve_machine")}
          </button>
        </div>
      )}

      {/* Union view (spec §"Union view on the signed-in desktop"): the cloud
          registry's rows, one pane down. Signed out ⇒ the section does not
          exist; an expired session degrades to a sign-in-again row; the
          local list above is never touched by any of it. */}
      {!isCloudMode() && cloud && cloud.session !== "signed_out" && (
        <div className="mt-7" data-testid="cloud-machines-section">
          <div className="mb-2.5 text-label text-faint font-medium">
            OpenWorker Cloud{cloud.org?.name ? ` · ${cloud.org.name}` : ""}
          </div>
          {cloud.session === "expired" ? (
            <div className={ROW} data-testid="cloud-session-expired">
              <div className="text-meta text-muted">
                {t("machines.cloud_expired")}
              </div>
            </div>
          ) : cloud.session === "unreachable" ? (
            <div className={ROW}>
              <div className="text-meta text-muted">
                {t("machines.cloud_unreachable")}
              </div>
            </div>
          ) : cloud.machines.length === 0 ? (
            <div className={ROW}>
              <div className="text-meta text-muted">
                {t("machines.cloud_empty")}
              </div>
            </div>
          ) : (
            cloud.machines.map(machineRow)
          )}
        </div>
      )}
    </section>
  );
}

// The signed-in identity + org switcher moved to Settings ▸ Account
// (UX-046 tail, 2026-08-30) — this page is the fleet list only.

// Typed-code approval for `openworker join` (GitHub device-flow style).
// The machine printed a code; the user types it here, checks the fingerprint
// against what the machine printed, and decides. Approval mints the one join
// token, bound to that machine's identity — a mistyped code shows a stranger's
// name and fingerprint at worst, never grants anything by itself.
function ApproveMachineCard({
  initialCode = "",
  onDecided,
  onClose,
}: {
  initialCode?: string;
  onDecided: () => void;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const [code, setCode] = useState(initialCode);
  const [detail, setDetail] = useState<DeviceRequestDetail | null>(null);
  const [status, setStatus] = useState<"idle" | "unknown" | "approved" | "denied">("idle");

  const lookup = useCallback(async (raw?: string) => {
    const normalized = (raw ?? code).trim().toUpperCase();
    if (!normalized) return;
    const d = await getDeviceRequest(normalized);
    setDetail(d);
    setStatus(d ? "idle" : "unknown");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [code]);

  // Deep-linked code: look it up immediately — the user clicked a URL the
  // joining machine printed; the card should already show its claim.
  useEffect(() => {
    if (initialCode) void lookup(initialCode);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialCode]);

  const decide = async (approve: boolean) => {
    if (!detail) return;
    const ok = approve
      ? await approveDeviceRequest(detail.user_code)
      : await denyDeviceRequest(detail.user_code);
    if (!ok) {
      setDetail(null);
      setStatus("unknown");
      return;
    }
    setStatus(approve ? "approved" : "denied");
    onDecided();
  };

  return (
    <div className="rounded-xl2 border border-line bg-panel px-5 py-4 mt-1.5 max-w-xl">
      <div className="flex items-center justify-between">
        <div className="text-body font-semibold text-ink">{t("machines.approve.title")}</div>
        <button className="text-muted hover:text-ink" onClick={onClose} aria-label={t("modal.close")}>
          <Icon name="x" size={14} />
        </button>
      </div>

      {status === "approved" || status === "denied" ? (
        <div className="mt-3 text-ui font-medium text-ink" data-testid="approval-result">
          {status === "approved" ? (
            <Trans
              i18nKey="machines.approve.approved"
              values={{ name: detail?.name ?? "" }}
              components={{ mono: <span className="font-mono" /> }}
            />
          ) : (
            <>{t("machines.approve.denied")}</>
          )}
        </div>
      ) : detail ? (
        <>
          <div className="mt-3 text-meta text-muted">
            {t("machines.approve.claim_intro")}
          </div>
          <div className="mt-2 rounded-lg border border-line bg-paper px-3 py-2.5 text-ui text-ink">
            <div className="font-medium">{detail.name}</div>
            <div className="font-mono text-label text-muted mt-0.5" data-testid="approval-fingerprint">
              {t("machines.key_fingerprint", { fingerprint: detail.fingerprint })}
            </div>
          </div>
          <div className="mt-2 text-label text-faint">
            {t("machines.approve.check_fingerprint")}
          </div>
          <div className="mt-3.5 flex items-center gap-2.5">
            <button
              className="px-3.5 py-2 rounded-lg bg-accent text-white text-ui font-medium"
              onClick={() => void decide(true)}
              data-testid="approve-button"
            >
              {t("inbox.approve")}
            </button>
            <button
              className="px-3.5 py-2 rounded-lg border border-line text-ui text-ink"
              onClick={() => void decide(false)}
            >
              {t("approval.deny")}
            </button>
          </div>
        </>
      ) : (
        <>
          <div className="mt-3 text-meta text-muted">
            <Trans
              i18nKey="machines.approve.instructions"
              components={{
                cmd: (
                  <span className="font-mono text-ink">
                    {`openworker join <${t("machines.approve.this_url")}>`}
                  </span>
                ),
              }}
            />
          </div>
          <div className="mt-2.5 flex items-center gap-2.5">
            <input
              autoFocus
              className="px-3 py-2 rounded-lg border border-line bg-paper font-mono text-body tracking-widest text-ink outline-none focus:border-accent uppercase"
              placeholder="XXXX-XXXX"
              value={code}
              onChange={(e) => setCode(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") void lookup();
              }}
              data-testid="approval-code-input"
            />
            <button
              className="px-3.5 py-2 rounded-lg border border-line text-ui text-ink hover:border-lineStrong"
              onClick={() => void lookup()}
            >
              {t("machines.approve.look_up")}
            </button>
          </div>
          {status === "unknown" && (
            <div className="mt-2 text-meta text-red-500" data-testid="approval-unknown">
              {t("machines.approve.unknown_code")}
            </div>
          )}
        </>
      )}
    </div>
  );
}

function AddMachineCard({
  knownIds,
  onJoined,
  onClose,
}: {
  knownIds: string[];
  onJoined: () => void;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const [joinUrl, setJoinUrl] = useState<string | null>(null);
  const [expiresAt, setExpiresAt] = useState(0);
  const [now, setNow] = useState(() => Date.now() / 1000);
  const [reach, setReach] = useState<"ssh" | "vpn">("ssh");
  const [joined, setJoined] = useState<Machine | null>(null);
  const [copied, setCopied] = useState(false);
  // "Provision with my defaults" (§Keys wallet): the wallet's provider keys,
  // deployed automatically the moment the machine joins. Names only here —
  // values are resolved and sealed sidecar-side.
  const [providerProfiles, setProviderProfiles] = useState<string[]>([]);
  const [provision, setProvision] = useState(true);
  const [provisioned, setProvisioned] = useState<string[] | null>(null);
  useEffect(() => {
    getWalletProfiles()
      .then((rows) =>
        setProviderProfiles(
          rows.map((r) => r.profile).filter((p) => p.startsWith("provider:")),
        ),
      )
      .catch(() => setProviderProfiles([]));
  }, []);
  // Machines enrolled before the card opened must not read as the new arrival.
  const known = useRef(new Set(knownIds));

  const arm = useCallback(async () => {
    try {
      const d = await armEnrollment();
      setJoinUrl(d.join_url);
      setExpiresAt(d.expires_at);
    } catch {
      setJoinUrl(null);
    }
  }, []);

  useEffect(() => {
    void arm();
    // Leaving the card closes the enrollment window — but never after a
    // successful join, and never revoke a window we no longer own.
    return () => {
      void disarmEnrollment();
    };
  }, [arm]);

  useEffect(() => {
    const t = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    if (joined) return;
    const t = setInterval(async () => {
      try {
        const d = await getMachines();
        const fresh = (d.machines ?? []).find((m) => !known.current.has(m.id));
        if (fresh) {
          setJoined(fresh);
          onJoined();
          if (provision && providerProfiles.length) {
            try {
              const r = await deployMachineSecrets(fresh.id, providerProfiles);
              setProvisioned(r.deployed ?? []);
            } catch {
              setProvisioned([]);
            }
          }
        }
      } catch {
        /* keep waiting */
      }
    }, POLL_MS);
    return () => clearInterval(t);
  }, [joined, onJoined, provision, providerProfiles]);

  const remaining = Math.max(0, Math.floor(expiresAt - now));
  const expired = joinUrl !== null && remaining === 0 && !joined;
  const countdown = `${Math.floor(remaining / 60)}:${String(remaining % 60).padStart(2, "0")}`;
  const port = (() => {
    try {
      return joinUrl ? new URL(joinUrl).port || "80" : "9787";
    } catch {
      return "9787";
    }
  })();
  const command = joinUrl ? `openworker join ${joinUrl} --name=my-box` : "…";

  const copy = async () => {
    if (!joinUrl) return;
    try {
      await navigator.clipboard.writeText(command);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard unavailable */
    }
  };

  return (
    <div className="rounded-xl2 border border-line bg-panel px-5 py-4 mt-1.5 max-w-xl">
      <div className="flex items-center justify-between">
        <div className="text-body font-semibold text-ink">{t("machines.add.title")}</div>
        <button className="text-muted hover:text-ink" onClick={onClose} aria-label={t("modal.close")}>
          <Icon name="x" size={14} />
        </button>
      </div>

      {joined ? (
        <>
          <div className="mt-3 text-ui text-ok font-medium" data-testid="join-success">
            {t("machines.add.joined", { name: joined.name })}
            <span className="text-faint font-normal font-mono text-label">
              {joined.app_version ? ` · v${joined.app_version}` : ""}
              {" · " + t("machines.key_fingerprint", { fingerprint: joined.fingerprint })}
            </span>
          </div>
          {provisioned !== null && (
            <div className="mt-1.5 text-meta text-muted" data-testid="provisioned-note">
              {provisioned.length
                ? t("machines.add.provisioned", { count: provisioned.length })
                : t("machines.add.provision_failed")}
            </div>
          )}
          <div className="mt-2.5 text-meta text-muted">
            <Trans
              i18nKey="machines.add.next"
              values={{ name: joined.name }}
              components={{ b: <b className="text-ink font-medium" /> }}
            />
          </div>
        </>
      ) : (
        <>
          {/* Cloud: machines dial the public URL directly — no reach step. */}
          {!isCloudMode() && (
            <>
              <div className="mt-3 text-meta text-muted">
                <Trans
                  i18nKey="machines.add.step_reach"
                  components={{ b: <b className="text-ink font-medium" /> }}
                />
              </div>
              <div className="inline-flex mt-2 border border-line rounded-lg overflow-hidden text-meta">
                <button
                  className={"px-3 py-1.5 " + (reach === "ssh" ? "bg-paper text-ink font-medium" : "text-muted")}
                  onClick={() => setReach("ssh")}
                >
                  {t("machines.add.reach_ssh")}
                </button>
                <button
                  className={"px-3 py-1.5 " + (reach === "vpn" ? "bg-paper text-ink font-medium" : "text-muted")}
                  onClick={() => setReach("vpn")}
                >
                  {t("machines.add.reach_vpn")}
                </button>
              </div>
              <div className="mt-2 text-label text-faint leading-relaxed">
                {reach === "ssh" ? (
                  <>
                    {t("machines.add.ssh_help")}
                    <div className="font-mono mt-1">{`ssh -N -R ${port}:localhost:${port} user@your-vm`}</div>
                  </>
                ) : (
                  <>
                    {t("machines.add.vpn_help")}
                  </>
                )}
              </div>
            </>
          )}

          <div className="mt-3.5 text-meta text-muted">
            {isCloudMode() ? (
              <>{t("machines.add.run_this")}</>
            ) : (
              <Trans
                i18nKey="machines.add.step_run"
                components={{ b: <b className="text-ink font-medium" /> }}
              />
            )}
          </div>
          <div className="mt-2 flex items-center justify-between gap-3 rounded-lg border border-line bg-paper px-3 py-2.5 font-mono text-label text-ink">
            <span className="truncate" data-testid="join-command">{command}</span>
            <button className="text-accent text-label font-sans shrink-0" onClick={copy}>
              {copied ? t("transcript.copied") : t("machines.add.copy")}
            </button>
          </div>

          {providerProfiles.length > 0 && (
            <label className="mt-3 flex items-start gap-2 text-meta text-muted cursor-pointer select-none">
              <input
                type="checkbox"
                className="mt-0.5"
                checked={provision}
                onChange={(e) => setProvision(e.target.checked)}
                data-testid="provision-defaults"
              />
              <span>
                {t("machines.add.provision_label", { n: providerProfiles.length })}
              </span>
            </label>
          )}

          <div className="mt-3.5 pt-3 border-t border-line flex items-center gap-2.5 text-meta text-muted">
            {expired ? (
              <>
                <span>{t("machines.add.expired")}</span>
                <button className="ml-auto text-accent" onClick={() => void arm()}>
                  {t("machines.add.rearm")}
                </button>
              </>
            ) : (
              <>
                <span className="w-[9px] h-[9px] rounded-full border-[1.5px] border-faint border-t-transparent animate-spin" />
                {t("machines.add.waiting")}
                <span className="ml-auto text-faint" data-testid="token-countdown">
                  {t("machines.add.token_expires", { countdown: joinUrl ? countdown : "…" })}
                </span>
              </>
            )}
          </div>
        </>
      )}
    </div>
  );
}

// Per-machine key deploy for wallet-less backends (OPE-149). The value is
// sealed IN THIS TAB to the machine's attested sealing key; the backend
// relays ciphertext and records only names. The fingerprint shown here is
// the user's verification anchor: `openworker machine status` on the box prints the
// same sixteen characters.
const KEY_PROVIDERS = [
  { id: "anthropic", label: "Anthropic" },
  { id: "openai", label: "OpenAI" },
  { id: "google", label: "Google" },
  { id: "openrouter", label: "OpenRouter" },
];

function MachineKeysCard({ machine, onClose }: { machine: Machine; onClose: () => void }) {
  const { t } = useTranslation();
  const [rows, setRows] = useState<MachineSecretRow[]>([]);
  const [provider, setProvider] = useState(KEY_PROVIDERS[0].id);
  const [custom, setCustom] = useState("");
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ tone: "ok" | "err"; text: string } | null>(null);

  const refresh = useCallback(() => {
    getMachineSecrets(machine.id).then(setRows).catch(() => setRows([]));
  }, [machine.id]);
  useEffect(() => {
    refresh();
  }, [refresh]);

  const profileName =
    provider === "custom" ? custom.trim() : `provider:${provider}`;

  const deploy = async () => {
    const secret = value.trim();
    if (!secret || !profileName || !machine.seal_pubkey) return;
    setBusy(true);
    setNote(null);
    try {
      const data = {
        api_key: secret,
        key_set_at: new Date().toISOString().slice(0, 10),
      };
      const sealed = sealProfiles(machine.seal_pubkey, { [profileName]: data });
      const hash = await profileHash(data);
      const r = await deploySealedSecrets(machine.id, sealed, [profileName], {
        [profileName]: hash,
      });
      if (r.error) setNote({ tone: "err", text: r.error });
      else {
        setValue("");
        setNote({ tone: "ok", text: t("machines.keys.deployed_note", { profile: profileName }) });
        refresh();
      }
    } catch (e) {
      setNote({ tone: "err", text: String((e as Error).message || e) });
    } finally {
      setBusy(false);
    }
  };

  const revoke = async (profile: string) => {
    const r = await revokeMachineSecrets(machine.id, [profile]);
    if (r.error) setNote({ tone: "err", text: r.error });
    else setNote({ tone: "ok", text: t("machines.keys.revoked_note", { profile }) });
    refresh();
  };

  return (
    <div className="rounded-xl2 border border-line bg-panel px-5 py-4 mt-1.5 max-w-xl" data-testid="machine-keys-card">
      <div className="flex items-center justify-between">
        <div className="text-body font-semibold text-ink">
          {t("machines.keys.title", { name: machine.name })}
        </div>
        <button className="text-muted hover:text-ink" onClick={onClose} aria-label={t("modal.close")}>
          <Icon name="x" size={14} />
        </button>
      </div>

      {!machine.seal_pubkey ? (
        <div className="mt-3 text-meta text-muted">
          {t("machines.keys.no_seal_key")}
        </div>
      ) : (
        <>
          <div className="mt-2.5 text-meta text-muted leading-relaxed">
            {t("machines.keys.sealed_note")}
          </div>
          <div className="mt-1.5 text-label text-faint">
            <Trans
              i18nKey="machines.keys.fingerprint_line"
              components={{
                fp: (
                  <span className="font-mono text-ink" data-testid="seal-fingerprint">
                    {machine.seal_fingerprint}
                  </span>
                ),
                cmd: <span className="font-mono" />,
              }}
            />
          </div>

          {rows.length > 0 && (
            <div className="mt-3.5">
              {rows.map((r) => (
                <div
                  key={r.profile}
                  className="flex items-center gap-3 py-1.5 text-meta text-ink group"
                  data-testid={`deployed-${r.profile}`}
                >
                  <span className="font-mono">{r.profile}</span>
                  <span className="text-faint text-label">
                    {t("machines.keys.deployed_on", {
                      date: new Date(r.deployed_at * 1000).toLocaleDateString(),
                    })}
                  </span>
                  <button className={"ml-auto " + ACT} onClick={() => void revoke(r.profile)}>
                    {t("settings.trust_revoke")}
                  </button>
                </div>
              ))}
            </div>
          )}

          <div className="mt-3.5 flex items-center gap-2.5">
            <select
              className="px-2 py-2 rounded-lg border border-line bg-paper text-meta text-ink outline-none"
              value={provider}
              onChange={(e) => setProvider(e.target.value)}
              data-testid="keys-provider"
            >
              {KEY_PROVIDERS.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.label}
                </option>
              ))}
              <option value="custom">{t("machines.keys.custom_profile")}</option>
            </select>
            {provider === "custom" && (
              <input
                className="px-2.5 py-2 rounded-lg border border-line bg-paper font-mono text-meta text-ink outline-none w-40"
                placeholder={t("machines.keys.profile_placeholder")}
                value={custom}
                onChange={(e) => setCustom(e.target.value)}
              />
            )}
            <input
              type="password"
              className="flex-1 min-w-0 px-2.5 py-2 rounded-lg border border-line bg-paper font-mono text-meta text-ink outline-none focus:border-accent"
              placeholder={t("machines.keys.api_key_placeholder")}
              value={value}
              onChange={(e) => setValue(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") void deploy();
              }}
              data-testid="keys-value"
            />
            <button
              className="px-3.5 py-2 rounded-lg bg-accent text-white text-ui font-medium disabled:opacity-50"
              disabled={busy || !value.trim() || !profileName}
              onClick={() => void deploy()}
              data-testid="keys-deploy"
            >
              {busy ? t("machines.keys.sealing") : t("machines.keys.deploy")}
            </button>
          </div>
          {note && (
            <div
              className={
                "mt-2 text-meta " + (note.tone === "ok" ? "text-ok" : "text-red-500")
              }
              data-testid="keys-note"
            >
              {note.text}
            </div>
          )}
        </>
      )}
    </div>
  );
}
