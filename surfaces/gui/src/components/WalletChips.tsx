import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  deployMachineSecrets,
  getMachines,
  getMachineSecrets,
  revokeMachineSecrets,
  type Machine,
} from "../api";

// Keys wallet (remote-home-design.md §Keys wallet W3): the "Available on" row a
// provider/connector card grows once ≥1 machine is enrolled. This Mac is fixed
// (the wallet lives here); each ⌂ chip toggles deploy/revoke for ONE profile on
// ONE machine. Deploy = copy — the machine owns its copy; a wallet update after
// deploy shows as a stale chip whose click re-deploys (rotate = deploy again).

interface ChipState {
  machine: Machine;
  deployed: boolean;
  stale: boolean;
}

export function WalletChips({
  profiles,
  caption,
}: {
  // Deployed/revoked as ONE unit; the first profile is the presence marker in
  // the ledger (e.g. a connector account + its default pointer travel together).
  profiles: string[];
  caption?: string;
}) {
  const { t } = useTranslation();
  const marker = profiles[0];
  const [chips, setChips] = useState<ChipState[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null); // machine id in flight
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const { machines } = await getMachines();
      if (!machines.length) {
        setChips([]);
        return;
      }
      const states = await Promise.all(
        machines.map(async (m) => {
          try {
            const rows = await getMachineSecrets(m.id);
            const row = rows.find((r) => r.profile === marker);
            return { machine: m, deployed: !!row, stale: !!row?.stale };
          } catch {
            return { machine: m, deployed: false, stale: false };
          }
        }),
      );
      setChips(states);
    } catch {
      setChips([]);
    }
  }, [marker]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const toggle = async (chip: ChipState) => {
    setBusy(chip.machine.id);
    setError(null);
    try {
      // Stale chip re-deploys (rotation); deployed+fresh revokes; absent deploys.
      const r =
        chip.deployed && !chip.stale
          ? await revokeMachineSecrets(chip.machine.id, profiles)
          : await deployMachineSecrets(chip.machine.id, profiles);
      if (r.error) setError(r.error);
    } finally {
      setBusy(null);
      void refresh();
    }
  };

  if (!chips || chips.length === 0) return null; // zero machines ⇒ the row doesn't exist

  return (
    <div className="mt-4" data-testid="wallet-chips">
      <div className="text-meta font-medium text-ink mb-1.5">{t("machines.wallet.available_on")}</div>
      <div className="flex items-center gap-1.5 flex-wrap">
        <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full border border-line bg-paper text-meta text-muted">
          {t("machines.this_mac")} <span className="text-ok">✓</span>
        </span>
        {chips.map((c) => (
          <button
            key={c.machine.id}
            className={
              "inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full border text-meta " +
              (c.deployed
                ? "border-okLine bg-okSoft text-ink"
                : "border-line bg-paper text-muted hover:text-ink hover:border-lineStrong") +
              (!c.machine.connected ? " opacity-45 cursor-not-allowed" : "")
            }
            disabled={!c.machine.connected || busy === c.machine.id}
            title={
              !c.machine.connected
                ? t("machines.wallet.offline")
                : c.stale
                  ? t("machines.wallet.stale_click")
                  : c.deployed
                    ? t("machines.wallet.deployed_click")
                    : t("machines.wallet.deploy_click")
            }
            data-testid={`wallet-chip-${c.machine.name}`}
            onClick={() => void toggle(c)}
          >
            ⌂ {c.machine.name}
            {busy === c.machine.id ? (
              <span className="w-[9px] h-[9px] rounded-full border-[1.5px] border-faint border-t-transparent animate-spin" />
            ) : c.stale ? (
              <span className="text-warnInk" title={t("machines.wallet.stale")}>↻</span>
            ) : c.deployed ? (
              <span className="text-ok">✓</span>
            ) : null}
          </button>
        ))}
      </div>
      <p className="text-label text-faint mt-1.5 leading-relaxed">
        {caption || t("machines.wallet.caption")}
      </p>
      {error && <div className="text-meta text-warnInk mt-1">{error}</div>}
    </div>
  );
}
