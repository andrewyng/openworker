import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  connectConnector,
  connectManaged,
  connectMcpBacked,
  getConnectors,
  type CloudStatus,
  type Connector,
} from "../../api";
import { ConnectorBadge } from "../../connectors/ConnectorIcon";
import { ConnectSetup } from "../ManageTabs";
import { CloudSignInInline, CloudStatusPending } from "./CloudSignIn";
import { PILL_ACCENT, PILL_LINE, TAG_ACCENT } from "./ui";

// The ONE place a connection gets added (UX-DECISIONS §21): the detail page's header
// button (or the list's Connect pill) opens this sheet. Connectors with two connect
// modes get a One click | Manual pill switcher; single-mode connectors render their
// existing ConnectSetup directly (Gmail's managed flow skips the modal entirely).

const INPUT =
  "w-full px-3 py-2 rounded-lg border border-line bg-paper text-[13px] text-ink outline-none focus:border-accent";

export function AddConnectionModal({
  c,
  cloud,
  title,
  onClose,
  onChanged,
}: {
  c: Connector;
  cloud: CloudStatus | null;
  title?: string; // e.g. "Add a workspace" — defaults to "Connect {title}"
  onClose: () => void;
  onChanged: () => void;
}) {
  const { t } = useTranslation();
  // MCP-backed one-click (§42): local OAuth against the vendor's hosted MCP server —
  // with manual fields alongside (jira, asana) it's a second mode; alone (monday)
  // it IS the connect flow.
  const mcpBacked = !!c.mcp;
  const twoModes =
    c.name === "slack" ||
    c.name === "hubspot" ||
    c.name === "github" ||
    c.name === "notion" ||
    c.name === "attio" ||
    (mcpBacked && c.fields.length > 0);
  const [pane, setPane] = useState<"one" | "manual">("one");

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="fixed inset-0 z-40" data-testid="add-connection-modal">
      <div className="absolute inset-0 bg-black/30" onClick={onClose} />
      <div
        className="absolute left-1/2 top-[14%] -translate-x-1/2 w-[480px] max-w-[calc(100vw-2rem)] bg-panel rounded-2xl border border-line shadow-2xl"
        role="dialog"
        aria-label={title || t("access.connectTitle", { title: c.title })}
      >
        <div className="flex items-center gap-3 px-5 pt-5">
          <ConnectorBadge connector={c} size={34} title={c.title} />
          <div className="flex-1 font-semibold text-[16px] tracking-tight">
            {title || t("access.connectTitle", { title: c.title })}
          </div>
          <button className="text-faint hover:text-ink text-[18px] leading-none" onClick={onClose} title={t("common.close")}>
            ×
          </button>
        </div>

        {twoModes ? (
          <>
            <div className="px-5 pt-4">
              <div className="inline-flex rounded-full p-0.5 bg-paper text-[12.5px] font-medium">
                {(["one", "manual"] as const).map((p) => (
                  <button
                    key={p}
                    data-testid={`modal-pane-${p}`}
                    className={
                      "px-3.5 py-1 rounded-full " +
                      (pane === p ? "bg-panel shadow-sm text-ink border border-line" : "text-muted")
                    }
                    onClick={() => setPane(p)}
                  >
                    {p === "one" ? t("conn.oneClick") : t("common.manual")}
                  </button>
                ))}
              </div>
            </div>
            {pane === "one" ? (
              mcpBacked ? (
                <McpOneClick c={c} onConnected={() => { onChanged(); onClose(); }} />
              ) : c.name === "hubspot" ? (
                <HubSpotOneClick c={c} cloud={cloud} />
              ) : c.name === "github" ? (
                <GithubOneClick c={c} cloud={cloud} />
              ) : c.name === "slack" ? (
                <SlackOneClick c={c} cloud={cloud} />
              ) : (
                <GenericOneClick c={c} cloud={cloud} />
              )
            ) : c.name === "slack" ? (
              <SlackManual onConnected={() => { onChanged(); onClose(); }} />
            ) : (
              <div className="px-1.5 pb-2">
                <ConnectSetup c={c} cloud={cloud} onConnected={() => { onChanged(); onClose(); }} manualOnly />
              </div>
            )}
          </>
        ) : mcpBacked ? (
          /* MCP-backed with no manual fields (monday): one-click IS the flow. */
          <McpOneClick c={c} onConnected={() => { onChanged(); onClose(); }} />
        ) : (
          <div className="px-1.5 pb-2">
            {/* Existing combined setup (managed button + manual fields) for everything else. */}
            <ConnectSetup c={c} cloud={cloud} onConnected={() => { onChanged(); onClose(); }} />
          </div>
        )}
      </div>
    </div>
  );
}

// One-click pane for MCP-BACKED connectors (monday, asana, jira — §42): the sidecar
// runs a fully LOCAL OAuth flow against the vendor's hosted MCP server (DCR — no
// client secret, no broker, no OpenWorker sign-in required). Poll until the card
// flips to connected, then close.
function McpOneClick({ c, onConnected }: { c: Connector; onConnected: () => void }) {
  const { t } = useTranslation();
  const [waiting, setWaiting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!waiting) return;
    const interval = setInterval(async () => {
      try {
        const list = await getConnectors();
        if (list.find((x) => x.name === c.name)?.connected) onConnected();
      } catch {
        /* keep polling */
      }
    }, 2000);
    return () => clearInterval(interval);
  }, [waiting, c.name, onConnected]);
  const go = async () => {
    setError(null);
    const res = await connectMcpBacked(c.name);
    if (res.ok) setWaiting(true);
    else setError(res.error || t("manageTabs.couldNotStartConnect"));
  };
  return (
    <div className="px-5 py-4 space-y-3">
      <p className="text-[13px] text-muted">
        {t("conn.mcpOpens", { title: c.title })}
      </p>
      <button
        className={PILL_ACCENT + " w-full !py-2"}
        data-testid="modal-mcp-one-click"
        onClick={go}
        disabled={waiting}
      >
        {waiting ? t("cloud.checkBrowser") : t("access.connectTitle", { title: c.title })}
      </button>
      {error && <div className="text-[12.5px] text-danger">{error}</div>}
      <p className="text-[12px] text-faint text-center flex items-center justify-center gap-1.5">
        <span className={TAG_ACCENT}>{t("access.recommended")}</span> {t("conn.mcpToolsNote", { title: c.title })}
      </p>
    </div>
  );
}

// One-click pane for generic managed connectors (Notion, Attio, …): sign in
// with the service in the browser; each consent lands as its own account.
function GenericOneClick({ c, cloud }: { c: Connector; cloud: CloudStatus | null }) {
  const { t } = useTranslation();
  const [waiting, setWaiting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const go = async () => {
    setError(null);
    const res = await connectManaged(c.name);
    if (res.ok) setWaiting(true);
    else setError(res.error || t("manageTabs.couldNotStartConnect"));
  };
  return (
    <div className="px-5 py-4 space-y-3">
      <p className="text-[13px] text-muted">
        {t("conn.opensApprove", { title: c.title })}
      </p>
      {cloud?.signed_in ? (
        <button
          className={PILL_ACCENT + " w-full !py-2"}
          data-testid="modal-generic-one-click"
          onClick={go}
          disabled={waiting}
        >
          {waiting ? t("cloud.checkBrowser") : t("access.connectTitle", { title: c.title })}
        </button>
      ) : cloud ? (
        <CloudSignInInline />
      ) : (
        <CloudStatusPending />
      )}
      {error && <div className="text-[12.5px] text-danger">{error}</div>}
      <p className="text-[12px] text-faint text-center flex items-center justify-center gap-1.5">
        <span className={TAG_ACCENT}>{t("access.recommended")}</span> {t("conn.tokensStay")}
      </p>
    </div>
  );
}

function SlackOneClick({ c, cloud }: { c: Connector; cloud: CloudStatus | null }) {
  const { t } = useTranslation();
  const [waiting, setWaiting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const go = async () => {
    setError(null);
    const res = await connectManaged(c.name);
    if (res.ok) setWaiting(true);
    else setError(res.error || t("conn.couldNotStartInstall"));
  };
  return (
    <div className="px-5 py-4 space-y-3">
      <p className="text-[13px] text-muted">
        {t("conn.slackOpens")}
      </p>
      {cloud?.signed_in ? (
        <button className={PILL_ACCENT + " w-full !py-2"} data-testid="modal-add-to-slack" onClick={go} disabled={waiting}>
          {waiting ? t("cloud.checkBrowser") : t("conn.addToSlack")}
        </button>
      ) : cloud ? (
        <CloudSignInInline />
      ) : (
        <CloudStatusPending />
      )}
      {error && <div className="text-[12.5px] text-danger">{error}</div>}
      <p className="text-[12px] text-faint text-center flex items-center justify-center gap-1.5">
        <span className={TAG_ACCENT}>{t("access.recommended")}</span> {t("conn.relayTokensStay")}
      </p>
    </div>
  );
}

function GithubOneClick({ c, cloud }: { c: Connector; cloud: CloudStatus | null }) {
  const { t } = useTranslation();
  const [waiting, setWaiting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const go = async () => {
    setError(null);
    const res = await connectManaged(c.name);
    if (res.ok) setWaiting(true);
    else setError(res.error || t("conn.couldNotStartInstall"));
  };
  return (
    <div className="px-5 py-4 space-y-3">
      <p className="text-[13px] text-muted">
        {t("conn.githubOpens")}
      </p>
      {cloud?.signed_in ? (
        /* One button: the broker is authorize-first — it links an existing installation or
           redirects the same tab on to the install page (the old "Already installed? Link
           it" question and the Configure dead-end are gone). */
        <button className={PILL_ACCENT + " w-full !py-2"} data-testid="modal-install-github-app" onClick={() => go()} disabled={waiting}>
          {waiting ? t("cloud.checkBrowser") : t("conn.connectGithub")}
        </button>
      ) : cloud ? (
        <CloudSignInInline />
      ) : (
        <CloudStatusPending />
      )}
      {error && <div className="text-[12.5px] text-danger">{error}</div>}
      <p className="text-[12px] text-faint text-center flex items-center justify-center gap-1.5">
        <span className={TAG_ACCENT}>{t("access.recommended")}</span> {t("conn.relayShortLived")}
      </p>
    </div>
  );
}

function HubSpotOneClick({ c, cloud }: { c: Connector; cloud: CloudStatus | null }) {
  const { t } = useTranslation();
  const [access, setAccess] = useState<"read" | "write">("read");
  const [waiting, setWaiting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const go = async () => {
    setError(null);
    const res = await connectManaged(c.name, { access });
    if (res.ok) setWaiting(true);
    else setError(res.error || t("manageTabs.couldNotStartConnect"));
  };
  return (
    <div className="px-5 py-4 space-y-3">
      <p className="text-[13px] text-muted">
        {t("conn.hubspotOpens")}
      </p>
      <div className="space-y-1.5" data-testid="hubspot-access">
        {(
          [
            ["read", "conn.hubspotReadOnly", "conn.hubspotReadOnlyDesc"],
            ["write", "conn.hubspotReadWrite", "conn.hubspotReadWriteDesc"],
          ] as const
        ).map(([value, labelKey, blurbKey]) => (
          <label key={value} className="flex items-start gap-2 text-[13px] cursor-pointer">
            <input
              type="radio"
              name="hubspot-access"
              className="mt-0.5"
              checked={access === value}
              data-testid={`hubspot-access-${value}`}
              onChange={() => setAccess(value)}
            />
            <span>
              <span className="font-medium">{t(labelKey)}</span>
              <span className="block text-[12px] text-muted">{t(blurbKey)}</span>
            </span>
          </label>
        ))}
      </div>
      {cloud?.signed_in ? (
        <button className={PILL_ACCENT + " w-full !py-2"} data-testid="modal-connect-hubspot" onClick={go} disabled={waiting}>
          {waiting ? t("cloud.checkBrowser") : t("conn.connectHubspot")}
        </button>
      ) : cloud ? (
        <CloudSignInInline />
      ) : (
        <CloudStatusPending />
      )}
      {error && <div className="text-[12.5px] text-danger">{error}</div>}
      <p className="text-[12px] text-faint text-center">
        {t("conn.anyPortals")}
      </p>
    </div>
  );
}

function SlackManual({ onConnected }: { onConnected: () => void }) {
  const { t } = useTranslation();
  const [bot, setBot] = useState("");
  const [app, setApp] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const submit = async () => {
    setBusy(true);
    setError(null);
    const res = await connectConnector("slack", { bot_token: bot.trim(), app_token: app.trim() });
    setBusy(false);
    if (res.ok) onConnected();
    else setError(res.error || t("manageTabs.couldNotConnect"));
  };
  return (
    <div className="px-5 py-4 space-y-3">
      <ol className="list-decimal pl-4 text-[13px] text-muted space-y-1">
        <li>{t("conn.slackManualStep1")}</li>
        <li>{t("conn.slackManualStep2")}</li>
        <li>{t("conn.slackManualStep3")}</li>
      </ol>
      <input className={INPUT} type="password" placeholder={t("conn.botTokenPlaceholder")} value={bot} spellCheck={false} onChange={(e) => setBot(e.target.value)} />
      <input className={INPUT} type="password" placeholder={t("conn.appTokenPlaceholder")} value={app} spellCheck={false} onChange={(e) => setApp(e.target.value)} />
      <button className={PILL_LINE + " w-full !py-2"} onClick={submit} disabled={busy || !bot.trim() || !app.trim()}>
        {busy ? t("manageTabs.validating") : t("conn.connect")}
      </button>
      {error && <div className="text-[12.5px] text-danger">{error}</div>}
      <p className="text-[12px] text-warnInk text-center">
        {t("conn.oneModeAtATime")}
      </p>
    </div>
  );
}
