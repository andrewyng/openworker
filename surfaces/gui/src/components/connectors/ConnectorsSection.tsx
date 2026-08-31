import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  disconnectConnector,
  getCloudStatus,
  getConnectors,
  getMcpServers,
  getSlackStatus,
  type CloudStatus,
  type Connector,
  type McpServer,
  type SlackStatus,
} from "../../api";
import { McpServerDetail } from "./CustomMcp";
import { ConnectorBadge } from "../../connectors/ConnectorIcon";
import { AllowlistBlock, ConnectorTools, ListeningSessionsBlock, UnauthorizedBlock } from "../ManageTabs";
import { AccountsDetail } from "./AccountsDetail";
import { AvailableDetail } from "./AvailableDetail";
import { CalendarDetail } from "./CalendarDetail";
import { ConnectorsList } from "./ConnectorsList";
import { GithubDetail } from "./GithubDetail";
import { GmailDetail } from "./GmailDetail";
import { HubSpotDetail } from "./HubSpotDetail";
import { SlackDetail } from "./SlackDetail";
import { GRP } from "./ui";

// Connectors surface = LIST ⇄ per-connector DETAIL SUBPAGE (UX-DECISIONS §21). The
// Integrations sub-nav never grows per-connector items; detail pages live behind a
// `‹ Connectors` breadcrumb. Connectors without a bespoke page get GenericDetail so
// every connected row navigates from day one.

export interface DetailProps {
  c: Connector;
  cloud: CloudStatus | null;
  slack: SlackStatus | null; // live Slack health (relay/sign-in/tokens); null elsewhere
  onChanged: () => void;
}

// Bespoke pages register here; everything else gets GenericDetail below.
const DETAIL_PAGES: Record<string, (p: DetailProps) => JSX.Element> = {
  slack: (p) => <SlackDetail {...p} />,
  gmail: (p) => <GmailDetail {...p} />,
  google_calendar: (p) => <CalendarDetail {...p} />,
  hubspot: (p) => <HubSpotDetail {...p} />,
  github: (p) => <GithubDetail {...p} />,
  // Generic multi-account connectors (accounts.py layer) share one page.
  notion: (p) => <AccountsDetail {...p} />,
  attio: (p) => <AccountsDetail {...p} />,
  posthog: (p) => <AccountsDetail {...p} />,
  mixpanel: (p) => <AccountsDetail {...p} />,
  amplitude: (p) => <AccountsDetail {...p} />,
  apollo: (p) => <AccountsDetail {...p} />,
  hunter: (p) => <AccountsDetail {...p} />,
};

export function ConnectorsSection() {
  const { t: tt } = useTranslation();
  const [detail, setDetail] = useState<string | null>(null);
  const [connectors, setConnectors] = useState<Connector[]>([]);
  const [mcpServers, setMcpServers] = useState<McpServer[]>([]);
  const [cloud, setCloud] = useState<CloudStatus | null>(null);
  const [slack, setSlack] = useState<SlackStatus | null>(null);

  const refresh = () => {
    getConnectors().then(setConnectors).catch(() => setConnectors([]));
    getMcpServers().then(setMcpServers).catch(() => setMcpServers([]));
    getCloudStatus().then(setCloud).catch(() => setCloud(null));
    getSlackStatus().then(setSlack).catch(() => setSlack(null));
  };
  useEffect(() => {
    refresh();
    // Poll: recent senders/parked arrive over time; sign-in + managed connects finish
    // in the system browser and surface on the next tick.
    const t = setInterval(refresh, 5000);
    return () => clearInterval(t);
  }, []);

  // While an MCP test/sign-in is in flight, poll fast so the chip flips to its
  // result (Live / Error / Needs sign-in) without the user touching anything.
  const mcpBusy = mcpServers.some((s) => s.status === "authorizing");
  useEffect(() => {
    if (!mcpBusy) return;
    const t = setInterval(refresh, 2000);
    return () => clearInterval(t);
  }, [mcpBusy]);

  // Custom MCP entries route as "mcp:<name>" so they can never collide with a
  // connector detail page of the same name.
  if (detail?.startsWith("mcp:")) {
    const s = mcpServers.find((x) => "mcp:" + x.name === detail);
    return (
      <div>
        <button
          className="text-[13px] text-accent mb-3"
          data-testid="connectors-breadcrumb"
          onClick={() => setDetail(null)}
        >
          ‹ Connectors
        </button>
        {!s ? (
          <div className="text-[13px] text-muted">Loading…</div>
        ) : (
          <McpServerDetail server={s} onChanged={refresh} onGone={() => setDetail(null)} />
        )}
      </div>
    );
  }

  if (detail) {
    const c = connectors.find((x) => x.name === detail);
    const Page = DETAIL_PAGES[detail];
    return (
      <div>
        <button
          className="text-[13px] text-accent mb-3"
          data-testid="connectors-breadcrumb"
          onClick={() => setDetail(null)}
        >
          {tt("connector.back_to_connectors")}
        </button>
        {!c ? (
          <div className="text-[13px] text-muted">{tt("connector.loading")}</div>
        ) : !c.connected ? (
          /* Pre-connect page (§38). When a connect completes, the poll flips
             c.connected and this same route re-renders as the connected page. */
          <AvailableDetail c={c} cloud={cloud} onChanged={refresh} />
        ) : Page ? (
          <Page c={c} cloud={cloud} slack={slack} onChanged={refresh} />
        ) : (
          <GenericDetail
            c={c}
            cloud={cloud}
            slack={slack}
            onChanged={refresh}
            onGone={() => setDetail(null)}
          />
        )}
      </div>
    );
  }

  return (
    <ConnectorsList
      connectors={connectors}
      mcpServers={mcpServers}
      cloud={cloud}
      slack={slack}
      onOpen={setDetail}
      onChanged={refresh}
    />
  );
}

// Fallback detail page: status header + the connector's existing config blocks
// (tools; allow-list/parked/listening for two-way) + Disconnect. Bespoke pages
// (Slack/Gmail/HubSpot) replace this one connector at a time.
function GenericDetail({
  c,
  cloud: _cloud,
  slack: _slack,
  onChanged,
  onGone,
}: DetailProps & { onGone: () => void }) {
  const { t } = useTranslation();
  return (
    <div>
      <div className="flex items-center gap-3.5 mb-5">
        <ConnectorBadge connector={c} size={44} title={c.title} />
        <div className="min-w-0 flex-1">
          <h2 className="text-[20px] font-semibold tracking-tight leading-tight">{c.title}</h2>
          <div className="text-[13px] text-muted flex items-center gap-1.5">
            <span className="w-2 h-2 rounded-full bg-ok" />
            {c.account || (c.auth === "none" ? t("connector.built_in") : t("connector.connected"))}
          </div>
        </div>
        {c.auth !== "none" && (
          <button
            className="text-[13px] text-danger/80 hover:text-danger shrink-0"
            onClick={async () => {
              await disconnectConnector(c.name);
              onChanged();
              onGone();
            }}
          >
            {t("connector.disconnect")}
          </button>
        )}
      </div>

      <div className={GRP}>
        <ConnectorTools c={c} onChanged={onChanged} />
      </div>

      {c.two_way && (
        <div className={GRP + " mt-4"}>
          <AllowlistBlock c={c} onChanged={onChanged} />
          <UnauthorizedBlock c={c} onChanged={onChanged} />
          {/* Channel subscriptions are a chat-platform concept — GitHub is two_way via the
              relay (inbound mentions) but has no channels. */}
          {c.channels && <ListeningSessionsBlock c={c} />}
        </div>
      )}
    </div>
  );
}
