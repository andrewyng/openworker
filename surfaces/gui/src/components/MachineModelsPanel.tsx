import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  type AuditExportStatus,
  deployMachineSecrets,
  deploySealedSecrets,
  getMachineSecrets,
  getMachineSettings,
  getWalletProfiles,
  hasWallet,
  revokeMachineSecrets,
  type Machine,
  type MachineSecretRow,
} from "../api";
import { profileHash, sealProfiles } from "../seal";
import { Icon } from "./Icon";
import { PanelHead } from "./IntegrationsView";

// Settings ▸ Models & Keys, scoped to a REMOTE machine (UX-046). Three parts:
// what the machine itself reports as active (the same list the composer's
// picker offers — the ratified truth source), the deploy ledger from this
// controller, and the two ways to send a key — from this Mac's wallet
// (desktop only), or pasted and sealed in this window (all deployments).

const CARD = "rounded-xl2 border border-line bg-panel";
const ROW = "flex items-center gap-3 px-4 py-3 border-b border-line last:border-b-0 text-ui";
const ACT = "text-meta text-accent ml-auto shrink-0";

const KEY_PROVIDERS = [
  { id: "anthropic", label: "Anthropic" },
  { id: "openai", label: "OpenAI" },
  { id: "google", label: "Google" },
  { id: "openrouter", label: "OpenRouter" },
];

function groupModels(models: string[]): { provider: string; names: string[] }[] {
  const by = new Map<string, string[]>();
  for (const m of models) {
    const [provider, ...rest] = m.split(":");
    const name = rest.join(":") || provider;
    by.set(provider, [...(by.get(provider) ?? []), name]);
  }
  return [...by.entries()].map(([provider, names]) => ({ provider, names }));
}

export function MachineModelsPanel({ machine }: { machine: Machine }) {
  const { t } = useTranslation();
  // Governance must be visible to the governed: an org export policy shows here.
  const [auditExport, setAuditExport] = useState<AuditExportStatus | null>(null);
  const [models, setModels] = useState<string[] | null>(null);
  const [rows, setRows] = useState<MachineSecretRow[]>([]);
  const [walletProfiles, setWalletProfiles] = useState<string[]>([]);
  const [walletPick, setWalletPick] = useState("");
  const [provider, setProvider] = useState(KEY_PROVIDERS[0].id);
  const [custom, setCustom] = useState("");
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ tone: "ok" | "err"; text: string } | null>(null);

  const refresh = useCallback(() => {
    getMachineSettings(machine.id)
      .then((s) => {
        // The box keeps its default model selectable even before that
        // provider has a key (onboarding relies on it); `model_ready` says
        // whether it can actually run. A fresh sandbox has no keys at all —
        // showing its default as "active" would be a lie (owner catch,
        // 2026-09-02), so drop it until a key lands.
        const reported = s.models ?? [];
        setModels(s.model_ready === false ? reported.filter((m) => m !== s.model) : reported);
        setAuditExport(s.audit_export ?? null);
      })
      .catch(() => setModels(null));
    getMachineSecrets(machine.id).then(setRows).catch(() => setRows([]));
  }, [machine.id]);

  useEffect(() => {
    setModels(null);
    setNote(null);
    refresh();
    if (hasWallet()) {
      getWalletProfiles()
        .then((p) => setWalletProfiles(p.map((x) => x.profile).filter((n) => n.startsWith("provider:"))))
        .catch(() => setWalletProfiles([]));
    }
  }, [refresh]);

  const done = (r: { error?: string }, ok: string) => {
    setNote(r.error ? { tone: "err", text: r.error } : { tone: "ok", text: ok });
    refresh();
  };

  const sendFromWallet = async () => {
    if (!walletPick) return;
    setBusy(true);
    try {
      done(await deployMachineSecrets(machine.id, [walletPick]), t("machines.models.sent_note", { profile: walletPick, name: machine.name }));
    } finally {
      setBusy(false);
    }
  };

  const profileName = provider === "custom" ? custom.trim() : `provider:${provider}`;

  const deployPasted = async () => {
    const secret = value.trim();
    if (!secret || !profileName || !machine.seal_pubkey) return;
    setBusy(true);
    setNote(null);
    try {
      const data = { api_key: secret, key_set_at: new Date().toISOString().slice(0, 10) };
      const sealed = sealProfiles(machine.seal_pubkey, { [profileName]: data });
      const hash = await profileHash(data);
      const r = await deploySealedSecrets(machine.id, sealed, [profileName], { [profileName]: hash });
      if (!r.error) setValue("");
      done(r, t("machines.models.deployed_note", { profile: profileName, name: machine.name }));
    } catch (e) {
      setNote({ tone: "err", text: String((e as Error).message || e) });
    } finally {
      setBusy(false);
    }
  };

  const revoke = async (profile: string) => {
    done(await revokeMachineSecrets(machine.id, [profile]), t("machines.models.revoked_note", { profile, name: machine.name }));
  };

  return (
    <section data-testid="machine-models-panel">
      <PanelHead
        title={t("machines.models.title")}
        sub={
          machine.seal_fingerprint
            ? t("machines.models.sub_sealed", {
                name: machine.name,
                fingerprint: machine.seal_fingerprint,
              })
            : t("machines.models.sub", { name: machine.name })
        }
      />

      <div className="text-label text-faint font-medium mb-2">
        {t("machines.models.active_heading")}
      </div>
      <div className={CARD}>
        {models === null ? (
          <div className={ROW + " text-muted"}>
            {machine.connected ? t("machines.models.loading") : t("machines.models.offline")}
          </div>
        ) : models.length === 0 ? (
          <div className={ROW + " text-muted"}>{t("machines.models.none")}</div>
        ) : (
          groupModels(models).map((g) => (
            <div key={g.provider} className={ROW} data-testid={`active-${g.provider}`}>
              <span className="inline-block w-[7px] h-[7px] rounded-full bg-ok" />
              <span className="font-medium text-ink">{g.provider}</span>
              <span className="text-muted text-meta truncate">{g.names.join(" · ")}</span>
            </div>
          ))
        )}
      </div>
      <div className="text-label text-faint mt-1.5 mb-5">
        {t("machines.models.reported_note")}
      </div>

      {auditExport?.enabled && (
        <div className={CARD + " mb-5"} data-testid="audit-export-notice">
          <div className={ROW}>
            <span className="inline-block w-[7px] h-[7px] rounded-full bg-ok" />
            <span className="font-medium text-ink">{t("machines.models.audit_exported")}</span>
            <span className="text-muted text-meta truncate">
              {auditExport.sink === "http"
                ? t("machines.models.audit_to_url", { url: auditExport.url })
                : t("machines.models.audit_via_cloud")}
              {" · " + t("machines.models.audit_sent", { n: auditExport.exported })}
              {auditExport.pending
                ? " · " + t("machines.models.audit_pending", { n: auditExport.pending })
                : ""}
              {auditExport.last_error ? ` · ${auditExport.last_error}` : ""}
            </span>
          </div>
        </div>
      )}

      {rows.length > 0 && (
        <>
          <div className="text-label text-faint font-medium mb-2">
            {t("machines.models.ledger_heading")}
          </div>
          <div className={CARD + " mb-5"}>
            {rows.map((r) => (
              <div key={r.profile} className={ROW} data-testid={`ledger-${r.profile}`}>
                <span className="font-mono text-meta text-ink">{r.profile}</span>
                <span className="text-faint text-label">
                  {t("machines.keys.deployed_on", {
                    date: new Date(r.deployed_at * 1000).toLocaleDateString(),
                  })}
                </span>
                {r.stale && (
                  <span className="text-label text-amber-600">{t("machines.models.stale")}</span>
                )}
                <button className={ACT} onClick={() => void revoke(r.profile)}>
                  {t("settings.trust_revoke")}
                </button>
              </div>
            ))}
          </div>
        </>
      )}

      <div className="text-label text-faint font-medium mb-2">
        {t("machines.models.send_heading")}
      </div>
      {!machine.seal_pubkey ? (
        <div className="text-meta text-muted">
          {t("machines.models.no_seal_key")}
        </div>
      ) : (
        <>
          {hasWallet() && walletProfiles.length > 0 && (
            <div className="flex items-center gap-2.5 mb-2.5">
              <select
                className="px-2.5 py-2 rounded-lg border border-line bg-paper text-meta text-ink outline-none"
                value={walletPick}
                onChange={(e) => setWalletPick(e.target.value)}
                data-testid="wallet-pick"
              >
                <option value="">{t("machines.models.from_wallet")}</option>
                {walletProfiles.map((p) => (
                  <option key={p} value={p}>
                    {p}
                  </option>
                ))}
              </select>
              <button
                className="px-3.5 py-2 rounded-lg bg-accent text-white text-ui font-medium disabled:opacity-50"
                disabled={busy || !walletPick}
                onClick={() => void sendFromWallet()}
                data-testid="wallet-send"
              >
                {t("common.send")}
              </button>
            </div>
          )}
          <div className="flex items-center gap-2.5">
            <select
              className="px-2.5 py-2 rounded-lg border border-line bg-paper text-meta text-ink outline-none"
              value={provider}
              onChange={(e) => setProvider(e.target.value)}
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
              placeholder={t("machines.models.paste_placeholder")}
              value={value}
              onChange={(e) => setValue(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") void deployPasted();
              }}
              data-testid="paste-key"
            />
            <button
              className="px-3.5 py-2 rounded-lg bg-accent text-white text-ui font-medium disabled:opacity-50"
              disabled={busy || !value.trim() || !profileName}
              onClick={() => void deployPasted()}
              data-testid="paste-deploy"
            >
              {busy ? t("machines.keys.sealing") : t("machines.keys.deploy")}
            </button>
          </div>
        </>
      )}
      {note && (
        <div
          className={"mt-2.5 text-meta " + (note.tone === "ok" ? "text-ok" : "text-red-500")}
          data-testid="keys-panel-note"
        >
          {note.text}
        </div>
      )}
    </section>
  );
}

/** Placeholder for machine-scoped pages that only speak to the local engine so
 * far. Honest emptiness beats silently showing This-Mac data under a remote
 * machine's name. */
export function MachineScopePending({ machine, page }: { machine: Machine; page: string }) {
  const { t } = useTranslation();
  return (
    <section className="text-ui text-muted" data-testid="machine-scope-pending">
      <PanelHead title={page} sub={t("machines.models.sub", { name: machine.name })} />
      <div className="rounded-xl2 border border-line bg-panel px-4 py-6 flex items-center gap-3">
        <Icon name="clock" size={16} />
        <span>
          {hasWallet()
            ? t("machines.models.scope_pending_local", {
                page,
                name: machine.name,
                page_lower: page.toLowerCase(),
              })
            : t("machines.models.scope_pending", { page, name: machine.name })}
        </span>
      </div>
    </section>
  );
}
