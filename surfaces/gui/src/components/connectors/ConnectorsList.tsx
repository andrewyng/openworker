import { useState } from "react";
import { type CloudStatus, type Connector, type SlackStatus } from "../../api";
import { ConnectorBadge } from "../../connectors/ConnectorIcon";
import { AddConnectionModal } from "./AddConnectionModal";
import { CHIP_OK, CHIP_OFF, CHIP_WARN, GRP, GRP_H, FOOT, PILL_QUIET, ROW } from "./ui";
import { useLanguage } from "../../i18n";

// The Connectors LIST (UX-DECISIONS §21): connected first in their own inset group —
// rows navigate to the connector's detail subpage; problems surface as a chip in the
// list, never one click deep. Available connectors below with a Connect pill.

const AVAILABLE_FOLD = 8; // rows shown before "show all"

export function ConnectorsList({
  connectors,
  cloud,
  slack,
  onOpen,
  onChanged,
}: {
  connectors: Connector[];
  cloud: CloudStatus | null;
  slack: SlackStatus | null;
  onOpen: (name: string) => void;
  onChanged: () => void;
}) {
  const { t } = useLanguage();
  const [filter, setFilter] = useState("");
  const [showAll, setShowAll] = useState(false);
  const [connecting, setConnecting] = useState<string | null>(null);

  const q = filter.trim().toLowerCase();
  const match = (c: Connector) => !q || c.title.toLowerCase().includes(q) || c.name.includes(q);
  const connected = connectors.filter((c) => c.connected && match(c));
  const available = connectors.filter((c) => !c.connected && c.available && match(c));
  const shown = showAll || q ? available : available.slice(0, AVAILABLE_FOLD);
  const connectingC = connecting ? connectors.find((c) => c.name === connecting) : null;

  return (
    <div>
      <div className="flex items-center justify-end mb-4">
        <input
          placeholder={t("Search")}
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          className="w-44 px-3.5 py-1.5 rounded-full border border-line bg-panel text-[13px] outline-none focus:border-accent"
        />
      </div>

      {/* No cloud strip here anymore (§26): the sidebar's account row is the permanent
          sign-in home, and the connect modals keep their inline sign-in panes. */}
      {connected.length > 0 && (
        <>
          <div className={GRP_H + " !mt-0"}>{t("Connected")} · {connected.length}</div>
          <div className={GRP}>
            {connected.map((c) => (
              <button
                key={c.name}
                data-testid={`connector-${c.name}`}
                className={ROW + " w-full text-left hover:bg-paper/60"}
                onClick={() => onOpen(c.name)}
              >
                <ConnectorBadge connector={c} size={34} title={c.title} />
                <span className="min-w-0 flex-1">
                  <span className="font-medium text-[13.5px]">{c.title}</span>
                  <span className="block text-[12px] text-muted">{statusLine(c, t)}</span>
                </span>
                {healthChip(c, slack, t)}
                <span className="text-faint text-[15px] shrink-0">›</span>
              </button>
            ))}
          </div>
        </>
      )}

      <div className={GRP_H}>{t("Available")}</div>
      <div className={GRP}>
        {shown.map((c) => (
          /* The row navigates to the pre-connect detail page (§38); the pill
             stays the fast path straight into the modal. */
          <button
            key={c.name}
            data-testid={`connector-${c.name}`}
            className={ROW + " w-full text-left hover:bg-paper/60"}
            onClick={() => onOpen(c.name)}
          >
            <ConnectorBadge connector={c} size={34} title={c.title} />
            <span className="min-w-0 flex-1">
              <span className="font-medium text-[13.5px]">{c.title}</span>
              <span className="block text-[12px] text-muted truncate">{c.blurb}</span>
            </span>
            <span
              className={PILL_QUIET + " cursor-pointer"}
              role="button"
              onClick={(e) => {
                e.stopPropagation();
                setConnecting(c.name);
              }}
            >
              {t("Connect")}
            </span>
          </button>
        ))}
        {shown.length === 0 && (
          <div className={ROW + " text-[12.5px] text-muted"}>{t("Nothing matches.")}</div>
        )}
      </div>
      {!showAll && !q && available.length > AVAILABLE_FOLD && (
        <div className={FOOT}>
          {t("{{count}} more", { count: available.length - AVAILABLE_FOLD })} ·{" "}
          <button className="text-muted hover:text-ink" onClick={() => setShowAll(true)}>
            {t("show all")}
          </button>
        </div>
      )}

      {connectingC && (
        <AddConnectionModal
          c={connectingC}
          cloud={cloud}
          onClose={() => setConnecting(null)}
          onChanged={onChanged}
        />
      )}
    </div>
  );
}

function statusLine(c: Connector, t: (key: string, vars?: Record<string, string | number>) => string): string {
  if (c.name === "slack" && c.mode === "relay") {
    const n = c.workspaces?.length ?? 0;
    return `${t("{{count}} workspaces", { count: n })} · relay`;
  }
  if ((c.accounts?.length ?? 0) > 1) return t("{{count}} accounts", { count: c.accounts!.length });
  if ((c.portals?.length ?? 0) > 1) return t("{{count}} portals", { count: c.portals!.length });
  if (c.auth === "none") return t("Built in");
  return c.account || t("Connected");
}

function healthChip(c: Connector, slack: SlackStatus | null, t: (key: string) => string) {
  // Slack relay gets a LIVE chip from /v1/connectors/slack/status — problems
  // surface in the list, never one click deep. Named honestly per layer; we
  // never claim "Slack↔cloud down" (the desktop can't see that leg).
  if (c.name === "slack" && c.mode === "relay" && slack) {
    if (!slack.signed_in) return <span className={CHIP_WARN}>● {t("Sign-in needed")}</span>;
    if (slack.relay.state === "offline") return <span className={CHIP_OFF}>● {t("Offline")}</span>;
    if (slack.relay.state === "reconnecting")
      return <span className={CHIP_WARN}>● {t("Reconnecting")}</span>;
    if (Object.values(slack.teams).some((t) => !t.token_ok))
      return <span className={CHIP_WARN}>⚠ {t("Token")}</span>;
    return <span className={CHIP_OK}>● {t("Live")}</span>;
  }
  if (c.two_way && c.connected) return <span className={CHIP_OK}>● {t("Live")}</span>;
  return <span className={CHIP_OK}>● {t("Ready")}</span>;
}
