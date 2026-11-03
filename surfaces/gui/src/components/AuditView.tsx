import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { getAudit, type AuditEvent } from "../api";
import { PanelHead } from "./IntegrationsView";

// Activity — connector/browser tool history, restructured onto the IntegrationsView page shell
// (centered panel + PanelHead + cards), replacing the legacy `page-view` layout. Read-only:
// filterable, with sanitized arguments.
const CARD = "rounded-xl2 border border-line bg-panel";
const INPUT = "px-3 py-1.5 rounded-lg border border-line bg-paper text-ui text-ink outline-none focus:border-accent";
const BTN_ACCENT = "text-ui px-3 py-1.5 rounded-lg bg-accent text-white shrink-0";

// `callId` (OPE-238): opened from the "changes made without asking" summary — show that one
// tool call's rows, with the filter visible and clearable.
export function AuditView({ callId }: { callId?: string } = {}) {
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [sessionFilter, setSessionFilter] = useState("");
  const [connectorFilter, setConnectorFilter] = useState("");
  const [toolFilter, setToolFilter] = useState("");
  const [callFilter, setCallFilter] = useState(callId || "");
  const { t } = useTranslation();

  const refresh = (call = callFilter) =>
    getAudit({
      limit: 150,
      session_id: sessionFilter.trim() || undefined,
      connector: connectorFilter.trim() || undefined,
      tool: toolFilter.trim() || undefined,
      call_id: call || undefined,
    })
      .then(setEvents)
      .catch(() => setEvents([]));

  useEffect(() => {
    setCallFilter(callId || "");
    refresh(callId || "");
  }, [callId]);

  return (
    <main className="flex-1 min-w-0 flex bg-paper">
      <div className="flex-1 min-w-0 overflow-y-auto hairline-scroll">
        <div className="max-w-4xl mx-auto px-7 py-6">
          <PanelHead
            title={t("audit.title")}
            sub={t("audit.sub")}
          />

          <div className="flex items-center gap-2 flex-wrap mb-4">
            <input className={INPUT} placeholder={t("audit.placeholder_session")} value={sessionFilter} onChange={(e) => setSessionFilter(e.target.value)} />
            <input className={INPUT} placeholder={t("audit.placeholder_connector")} value={connectorFilter} onChange={(e) => setConnectorFilter(e.target.value)} />
            <input className={INPUT} placeholder={t("audit.placeholder_tool")} value={toolFilter} onChange={(e) => setToolFilter(e.target.value)} />
            <button className={BTN_ACCENT} onClick={() => refresh()}>
              {t("audit.filter")}
            </button>
          </div>
          {callFilter && (
            <div className="flex items-center gap-2 mb-4 text-meta text-muted" data-testid="audit-call-filter">
              <span>{t("audit.call_filter", { id: callFilter })}</span>
              <button
                className="text-label text-faint hover:text-muted cursor-pointer"
                data-testid="audit-call-filter-clear"
                onClick={() => {
                  setCallFilter("");
                  refresh("");
                }}
              >
                {t("audit.call_filter_clear")}
              </button>
            </div>
          )}

          {events.length === 0 ? (
            <div className={CARD + " p-4 text-ui text-muted"}>{t("audit.no_events")}</div>
          ) : (
            <div className="space-y-2">
              {events.map((ev) => (
                <AuditRow ev={ev} key={ev.id} />
              ))}
            </div>
          )}
        </div>
      </div>
    </main>
  );
}

function AuditRow({ ev }: { ev: AuditEvent }) {
  const { t } = useTranslation();
  return (
    <div className={CARD + " p-3.5"}>
      <div className="flex items-center gap-2 flex-wrap">
        <span className="font-mono text-ui font-medium text-ink">{ev.tool}</span>
        <span className="text-meta text-faint">
          {ev.connector || t("audit.fallback_tool")} · {ev.stage || ev.status || t("audit.fallback_event")} · {ev.timestamp}
        </span>
      </div>
      <div className="text-meta text-muted mt-0.5">
        {t("audit.session")} {ev.session_id || "-"} {ev.approval ? `· ${ev.approval}` : ""} {ev.status ? `· ${ev.status}` : ""}
      </div>
      {ev.resource && <div className="text-meta text-faint mt-0.5">{t("audit.resource", { value: ev.resource })}</div>}
      {ev.args && Object.keys(ev.args).length > 0 && (
        <div className="font-mono text-meta text-muted mt-1.5 break-words">{formatAuditArgs(ev.args)}</div>
      )}
      {(ev.reason || ev.result_preview) && (
        <div className="text-meta text-faint mt-1">{ev.reason || ev.result_preview}</div>
      )}
    </div>
  );
}

function formatAuditArgs(args: Record<string, any>) {
  return Object.entries(args)
    .map(([k, v]) => `${k}=${typeof v === "string" ? v : JSON.stringify(v)}`)
    .join("  ");
}
