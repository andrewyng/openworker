import { Trans, useTranslation } from "react-i18next";
import type { Item } from "../types";
import { Icon } from "./Icon";

type ToolReqItem = Extract<Item, { kind: "toolreq" }>;

// The agent asked (via request_tool) for a CLI it couldn't find — a scanner, usually.
// Declining is a normal outcome, not a failure: the agent falls back and says which checks
// were degraded, so the copy here shouldn't push the user toward Install.
export function ToolRequestCard({
  item,
  onRespond,
}: {
  item: ToolReqItem;
  onRespond: (approved: boolean) => void;
}) {
  const { t } = useTranslation();
  return (
    <div className="dirreq-card">
      <div className="dirreq-head">
        <Icon name="wrench" size={16} className="ico" />
        <span>
          <Trans
            i18nKey="toolreq.needs"
            values={{ tool: item.tool }}
            components={{ code: <code /> }}
          />
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
      {/* The fact strip is the PRODUCT speaking (registry metadata), styled apart from the
          coworker's quoted ask above — mixing the two voices is what made the card confusing. */}
      {item.installable ? (
        <div className="toolreq-facts">
          <div className="toolreq-factrow">
            <code>
              {item.tool}
              {item.version ? ` ${item.version}` : ""}
            </code>
            {item.summary && <span className="toolreq-fact">{item.summary}</span>}
          </div>
          <div className="toolreq-explain">
            {item.source
              ? t("toolreq.installs_with_source", { source: item.source })
              : t("toolreq.installs")}
          </div>
        </div>
      ) : (
        <div className="toolreq-facts">
          <div className="toolreq-explain">{t("toolreq.no_build")}</div>
        </div>
      )}
      <div className="dirreq-actions">
        <span className="spacer" />
        <button className="btn" data-testid="toolreq-skip" onClick={() => onRespond(false)}>
          {t("toolreq.skip")}
        </button>
        <button
          className="btn primary"
          data-testid="toolreq-install"
          disabled={!item.installable}
          onClick={() => onRespond(true)}
        >
          {t("toolreq.install")}
        </button>
      </div>
    </div>
  );
}
