import { useState } from "react";
import { useTranslation } from "react-i18next";
import { type CloudStatus, type Connector, type McpServer, type SlackStatus } from "../../api";
import { ConnectorBadge } from "../../connectors/ConnectorIcon";
import { AddConnectionModal } from "./AddConnectionModal";
import { AddMcpModal, CustomMcpGroup } from "./CustomMcp";
import { CHIP_OK, CHIP_OFF, CHIP_WARN, GRP, GRP_H, FOOT, PILL_QUIET, ROW } from "./ui";

// The Connectors LIST (UX-DECISIONS §21): connected first in their own inset group —
// rows navigate to the connector's detail subpage; problems surface as a chip in the
// list, never one click deep. Available connectors below with a Connect pill.
// Custom MCP servers (UX-034) render as their own group after Connected; the "Add
// custom server" affordance sits at the top of the page (owner ruling: top).

const AVAILABLE_FOLD = 8; // rows shown before "show all"

export function ConnectorsList({
  connectors,
  mcpServers,
  cloud,
  slack,
  onOpen,
  onChanged,
}: {
  connectors: Connector[];
  mcpServers: McpServer[];
  cloud: CloudStatus | null;
  slack: SlackStatus | null;
  onOpen: (name: string) => void;
  onChanged: () => void;
}) {
  const { t } = useTranslation();
  const [filter, setFilter] = useState("");
  const [showAll, setShowAll] = useState(false);
  const [connecting, setConnecting] = useState<string | null>(null);
  const [addingMcp, setAddingMcp] = useState(false);

  const q = filter.trim().toLowerCase();
  const match = (c: Connector) => !q || c.title.toLowerCase().includes(q) || c.name.includes(q);
  const connected = connectors.filter((c) => c.connected && match(c));
  const available = connectors.filter((c) => !c.connected && c.available && match(c));
  const customMcp = mcpServers.filter((s) => !q || s.name.toLowerCase().includes(q));
  const shown = showAll || q ? available : available.slice(0, AVAILABLE_FOLD);
  const connectingC = connecting ? connectors.find((c) => c.name === connecting) : null;

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <button
          className={PILL_QUIET}
          onClick={() => setAddingMcp(true)}
          data-testid="add-custom-server"
        >
          {t("connector.add_custom_mcp")}
        </button>
        <input
          placeholder={t("connector.search")}
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          className="w-44 px-3.5 py-1.5 rounded-full border border-line bg-panel text-[13px] outline-none focus:border-accent"
        />
      </div>

      {/* No cloud strip here anymore (§26): the sidebar's account row is the permanent
          sign-in home, and the connect modals keep their inline sign-in panes. */}
      {connected.length > 0 && (
        <>
          <div className={GRP_H + " !mt-0"}>{t("connector.connected_count", { count: connected.length })}</div>
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
                  <span className="font-medium text-[13px]">{c.title}</span>
                  <span className="block text-[12px] text-muted">{statusLine(c, t)}</span>
                </span>
                {healthChip(c, slack, t)}
                <span className="text-faint text-[14px] shrink-0">›</span>
              </button>
            ))}
          </div>
        </>
      )}

      <CustomMcpGroup
        servers={customMcp}
        onOpen={(name) => onOpen("mcp:" + name)}
        onChanged={onChanged}
      />

      <div className={GRP_H}>{t("connector.available")}</div>
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
              <span className="font-medium text-[13px]">{c.title}</span>
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
              {t("connector.connect")}
            </span>
          </button>
        ))}
        {shown.length === 0 && (
          <div className={ROW + " text-[13px] text-muted"}>{t("connector.nothing_matches")}</div>
        )}
      </div>
      {!showAll && !q && available.length > AVAILABLE_FOLD && (
        <div className={FOOT}>
          {t("connector.more_count", { count: available.length - AVAILABLE_FOLD })}{" "}
          <button className="text-muted hover:text-ink" onClick={() => setShowAll(true)}>
            {t("connector.show_all")}
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
      {addingMcp && <AddMcpModal onClose={() => setAddingMcp(false)} onChanged={onChanged} />}
    </div>
  );
}

function statusLine(c: Connector, t: (k: string, opts?: Record<string, unknown>) => string): string {
  if (c.name === "slack" && c.mode === "relay") {
    const n = c.workspaces?.length ?? 0;
    return t("connector.slack_status", { count: n });
  }
  if ((c.accounts?.length ?? 0) > 1) return t("connector.account_count", { count: c.accounts!.length });
  if ((c.portals?.length ?? 0) > 1) return t("connector.portal_count", { count: c.portals!.length });
  if (c.auth === "none") return t("connector.built_in");
  return c.account || t("connector.connected");
}

function healthChip(c: Connector, slack: SlackStatus | null, t: (k: string, opts?: Record<string, unknown>) => string) {
  // Slack relay gets a LIVE chip from /v1/connectors/slack/status — problems
  // surface in the list, never one click deep. Named honestly per layer; we
  // never claim "Slack↔cloud down" (the desktop can't see that leg).
  if (c.name === "slack" && c.mode === "relay" && slack) {
    if (!slack.signed_in) return <span className={CHIP_WARN}>{"● " + t("connector.sign_in_needed")}</span>;
    if (slack.relay.state === "offline") return <span className={CHIP_OFF}>{"● " + t("connector.offline")}</span>;
    if (slack.relay.state === "reconnecting")
      return <span className={CHIP_WARN}>{"● " + t("connector.reconnecting")}</span>;
    if (Object.values(slack.teams).some((tm) => !tm.token_ok))
      return <span className={CHIP_WARN}>{"⚠ " + t("connector.token")}</span>;
    return <span className={CHIP_OK}>{"● " + t("connector.live")}</span>;
  }
  if (c.two_way && c.connected) return <span className={CHIP_OK}>{"● " + t("connector.live")}</span>;
  return <span className={CHIP_OK}>{"● " + t("connector.ready")}</span>;
}

