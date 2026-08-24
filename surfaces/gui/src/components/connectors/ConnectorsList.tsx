import { useState } from "react";
import { type CloudStatus, type Connector, type McpServer, type SlackStatus } from "../../api";
import { useLanguage, type Language } from "../../i18n";
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

const connectorBlurbs: Record<string, [string, string, string]> = {
  telegram: ["Two-way messaging with a Telegram bot.", "Mensagens em duas vias com um bot do Telegram.", "Mensajería bidireccional con un bot de Telegram."],
  slack: ["Two-way messaging — one-click via Worker Cloud, or a manual Slack app (Socket Mode).", "Mensagens em duas vias — com um clique pelo Worker Cloud ou por um app manual do Slack (modo Socket).", "Mensajería bidireccional: con un clic mediante Worker Cloud o con una aplicación manual de Slack (modo Socket)."],
  email: ["Read, search, and send mail from any IMAP account — Gmail, iCloud, Fastmail, or custom.", "Leia, pesquise e envie e-mails de qualquer conta IMAP — Gmail, iCloud, Fastmail ou personalizada.", "Lee, busca y envía correos desde cualquier cuenta IMAP: Gmail, iCloud, Fastmail o personalizada."],
  gmail: ["Search, summarize, draft, and send email.", "Pesquise, resuma, redija e envie e-mails.", "Busca, resume, redacta y envía correos."],
  google_calendar: ["Read availability, summarize schedules, and create events.", "Consulte disponibilidade, resuma agendas e crie eventos.", "Consulta disponibilidad, resume agendas y crea eventos."],
  github: ["Work with issues, pull requests, repository files, and CI status.", "Trabalhe com issues, pull requests, arquivos de repositório e status de CI.", "Trabaja con issues, pull requests, archivos de repositorio y estado de CI."],
  outlook: ["Search, summarize, and send Microsoft 365 mail, and run your calendar.", "Pesquise, resuma e envie e-mails do Microsoft 365, além de gerenciar seu calendário.", "Busca, resume y envía correos de Microsoft 365, además de gestionar tu calendario."],
};

function connectorBlurb(connector: Connector, language: Language) {
  const copy = connectorBlurbs[connector.name];
  return copy ? copy[language === "pt-BR" ? 1 : language === "es-ES" ? 2 : 0] : connector.blurb;
}

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
  const [language] = useLanguage();
  const text = (english: string, portuguese: string, spanish: string) =>
    language === "pt-BR" ? portuguese : language === "es-ES" ? spanish : english;
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
          {text("+ Add custom MCP server", "+ Adicionar servidor MCP personalizado", "+ Agregar servidor MCP personalizado")}
        </button>
        <input
          placeholder={text("Search", "Pesquisar", "Buscar")}
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          className="w-44 px-3.5 py-1.5 rounded-full border border-line bg-panel text-[13px] outline-none focus:border-accent"
        />
      </div>

      {/* No cloud strip here anymore (§26): the sidebar's account row is the permanent
          sign-in home, and the connect modals keep their inline sign-in panes. */}
      {connected.length > 0 && (
        <>
          <div className={GRP_H + " !mt-0"}>{text("Connected", "Conectados", "Conectados")} · {connected.length}</div>
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
                  <span className="block text-[12px] text-muted">{statusLine(c)}</span>
                </span>
                {healthChip(c, slack)}
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

      <div className={GRP_H}>{text("Available", "Disponíveis", "Disponibles")}</div>
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
              <span className="block text-[12px] text-muted truncate">{connectorBlurb(c, language)}</span>
            </span>
            <span
              className={PILL_QUIET + " cursor-pointer"}
              role="button"
              onClick={(e) => {
                e.stopPropagation();
                setConnecting(c.name);
              }}
            >
              {text("Connect", "Conectar", "Conectar")}
            </span>
          </button>
        ))}
        {shown.length === 0 && (
          <div className={ROW + " text-[13px] text-muted"}>{text("Nothing matches.", "Nenhum resultado encontrado.", "No hay coincidencias.")}</div>
        )}
      </div>
      {!showAll && !q && available.length > AVAILABLE_FOLD && (
        <div className={FOOT}>
          {available.length - AVAILABLE_FOLD} more ·{" "}
          <button className="text-muted hover:text-ink" onClick={() => setShowAll(true)}>
            {text("show all", "mostrar todos", "mostrar todos")}
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

function statusLine(c: Connector): string {
  if (c.name === "slack" && c.mode === "relay") {
    const n = c.workspaces?.length ?? 0;
    return `${n} workspace${n === 1 ? "" : "s"} · relay`;
  }
  if ((c.accounts?.length ?? 0) > 1) return `${c.accounts!.length} accounts`;
  if ((c.portals?.length ?? 0) > 1) return `${c.portals!.length} portals`;
  if (c.auth === "none") return "Built in";
  return c.account || "Connected";
}

function healthChip(c: Connector, slack: SlackStatus | null) {
  // Slack relay gets a LIVE chip from /v1/connectors/slack/status — problems
  // surface in the list, never one click deep. Named honestly per layer; we
  // never claim "Slack↔cloud down" (the desktop can't see that leg).
  if (c.name === "slack" && c.mode === "relay" && slack) {
    if (!slack.signed_in) return <span className={CHIP_WARN}>● Sign-in needed</span>;
    if (slack.relay.state === "offline") return <span className={CHIP_OFF}>● Offline</span>;
    if (slack.relay.state === "reconnecting")
      return <span className={CHIP_WARN}>● Reconnecting</span>;
    if (Object.values(slack.teams).some((t) => !t.token_ok))
      return <span className={CHIP_WARN}>⚠ Token</span>;
    return <span className={CHIP_OK}>● Live</span>;
  }
  if (c.two_way && c.connected) return <span className={CHIP_OK}>● Live</span>;
  return <span className={CHIP_OK}>● Ready</span>;
}

