import { Trans, useTranslation } from "react-i18next";
import type { Item } from "../types";
import { Icon } from "./Icon";

type ConnReqItem = Extract<Item, { kind: "connreq" }>;

const LABELS: Record<string, string> = {
  github: "GitHub",
  slack: "Slack",
  telegram: "Telegram",
  linear: "Linear",
  jira: "Jira",
  gmail: "Gmail",
  notion: "Notion",
  hubspot: "HubSpot",
};

export function connectorLabel(id: string): string {
  return LABELS[id] || id.charAt(0).toUpperCase() + id.slice(1);
}

// Connector access is a human decision (spec §11.6). Two asks share this card:
// `request_connector` — the coworker would like a service connected ("Connect" opens the
// Connectors page; "I've connected it" resolves once the user has); `grant_connector` —
// a lead asks to give one of its workers a connector it does not have. Declining is a
// normal outcome: the coworker carries on and says what it could not do.
export function ConnectorRequestCard({
  item,
  onRespond,
  onOpenConnectors,
}: {
  item: ConnReqItem;
  onRespond: (approved: boolean) => void;
  onOpenConnectors?: () => void;
}) {
  const { t } = useTranslation();
  const label = connectorLabel(item.connector);
  const grant = item.request === "grant";
  return (
    <div className="dirreq-card" data-testid="connreq-card">
      <div className="dirreq-head">
        <Icon name="plug" size={16} className="ico" />
        <span>
          {grant ? (
            <Trans
              i18nKey="connreq.head_grant"
              values={{ worker: item.worker, label }}
              components={{ b: <b /> }}
            />
          ) : (
            <Trans i18nKey="connreq.head_connect" values={{ label }} components={{ b: <b /> }} />
          )}
        </span>
      </div>
      {item.reason && (
        <div className="dirreq-reason">
          <Trans
            i18nKey="toolreq.reason_line"
            values={{ reason: item.reason }}
            components={{ label: <span className="toolreq-label" /> }}
          />
        </div>
      )}
      <div className="toolreq-facts">
        <div className="toolreq-explain">
          {grant
            ? t("connreq.explain_grant", { worker: item.worker, label })
            : t("connreq.explain_connect")}
        </div>
      </div>
      <div className="dirreq-actions">
        <span className="spacer" />
        <button className="btn" data-testid="connreq-decline" onClick={() => onRespond(false)}>
          {t("approval.btn.not_now")}
        </button>
        {grant ? (
          <button className="btn primary" data-testid="connreq-grant" onClick={() => onRespond(true)}>
            {t("connreq.grant", { label })}
          </button>
        ) : (
          <>
            {onOpenConnectors && (
              <button className="btn" data-testid="connreq-open" onClick={onOpenConnectors}>
                {t("transcript.mcp_open_connectors")}
              </button>
            )}
            <button className="btn primary" data-testid="connreq-connected" onClick={() => onRespond(true)}>
              {t("connreq.connected")}
            </button>
          </>
        )}
      </div>
    </div>
  );
}
