import { useState } from "react";
import { useTranslation } from "react-i18next";
import {
  connectManaged,
  disconnectGcalAccount,
  setGcalDefaultAccount,
  type GmailAccount,
} from "../../api";
import { ConnectorBadge } from "../../connectors/ConnectorIcon";
import type { DetailProps } from "./ConnectorsSection";
import { ToolsDisclosure } from "./ToolsDisclosure";
import { FOOT, GRP, GRP_H, PILL_ACCENT, ROW, TAG_ACCENT, TAG_WARN, XBTN } from "./ui";

// The Google Calendar detail page: connected accounts (multi-account, Default
// badge, per-account disconnect) — Gmail's page minus the privacy filters.
// Adding an account launches managed OAuth DIRECTLY (one connect mode, no modal).

export function CalendarDetail({ c, cloud, slack: _slack, onChanged }: DetailProps) {
  const { t } = useTranslation();
  const [busy, setBusy] = useState(false);
  const accounts = (c.accounts ?? []) as GmailAccount[]; // email-keyed (pre-generic-layer shape)

  const addAccount = async () => {
    setBusy(true);
    await connectManaged("google_calendar"); // completes in the system browser; the poll picks it up
    setTimeout(() => setBusy(false), 2500);
  };

  return (
    <div data-testid="gcal-detail">
      <div className="flex items-center gap-3.5 mb-5">
        <ConnectorBadge connector={c} size={44} title="Google Calendar" />
        <div className="min-w-0 flex-1">
          <h2 className="text-[20px] font-semibold tracking-tight leading-tight">
            Google Calendar
          </h2>
          <div className="text-[12.5px] text-muted flex items-center gap-1.5">
            {c.connected ? (
              <>
                <span className="w-2 h-2 rounded-full bg-ok" />
                <span data-testid="gcal-status">
                  {t(accounts.length === 1 ? "conn.accountsCount_one" : "conn.accountsCount_other", { count: accounts.length })}
                </span>
              </>
            ) : (
              <span>{t("conn.notConnected")}</span>
            )}
          </div>
        </div>
        <button
          className={PILL_ACCENT + (c.managed_paused ? " opacity-50" : "")}
          data-testid="add-account-btn"
          onClick={addAccount}
          disabled={busy || !cloud?.signed_in || c.managed_paused}
          title={
            c.managed_paused
              ? t("conn.gcalOneClickSoon")
              : cloud?.signed_in
                ? ""
                : t("conn.signInCloudFirst")
          }
        >
          {c.managed_paused ? t("conn.addAccountComingSoon") : busy ? t("cloud.checkBrowser") : t("conn.addAccount")}
        </button>
      </div>

      {!c.connected && (
        <div className={GRP}>
          <div className={ROW + " text-[12.5px] text-muted"}>
            {t("conn.signInWithGoogle")}
            {cloud?.signed_in ? "" : t("conn.requiresCloudSignIn")}
          </div>
        </div>
      )}

      {accounts.length > 0 && (
        <>
          <div className={GRP_H + " !mt-0"}>{t("conn.accounts")}</div>
          <div className={GRP} data-testid="gcal-accounts">
            {accounts.map((a) => (
              <AccountRow key={a.email} a={a} onChanged={onChanged} />
            ))}
          </div>
        </>
      )}

      <ToolsDisclosure c={c} onChanged={onChanged} />
      <div className={FOOT + " mt-2"}>
        {t("conn.calendarFooter")}
      </div>
    </div>
  );
}

function AccountRow({ a, onChanged }: { a: GmailAccount; onChanged: () => void }) {
  const { t } = useTranslation();
  const [busy, setBusy] = useState(false);
  return (
    <div className={ROW} data-testid={`gcal-account-${a.email}`}>
      <span className="min-w-0 flex-1 flex items-center gap-2">
        <span className="text-[13px] font-medium truncate">{a.email}</span>
        {a.default && <span className={TAG_ACCENT}>{t("common.default")}</span>}
        {a.needs_reauth && <span className={TAG_WARN}>{t("conn.signInAgain")}</span>}
      </span>
      {!a.default && (
        <button
          className="text-[12px] text-muted hover:text-ink shrink-0"
          data-testid={`gcal-make-default-${a.email}`}
          onClick={async () => {
            await setGcalDefaultAccount(a.email);
            onChanged();
          }}
        >
          {t("conn.makeDefault")}
        </button>
      )}
      <button
        className={XBTN}
        title={t("conn.disconnectAccountTitle")}
        data-testid={`gcal-disconnect-${a.email}`}
        disabled={busy}
        onClick={async () => {
          setBusy(true);
          await disconnectGcalAccount(a.email);
          setBusy(false);
          onChanged();
        }}
      >
        ×
      </button>
    </div>
  );
}
