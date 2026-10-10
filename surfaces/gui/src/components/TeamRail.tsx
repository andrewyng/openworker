import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { SessionInfo, SessionUsage } from "../types";
import type { TeamSummary } from "../teamView";
import {
  filterWorkers,
  teamRoster,
  type RosterEntry,
  type WorkerFilter,
} from "../teamRoster";
import { formatTokens, totalTokens } from "../usage";
import { Icon } from "./Icon";

export function WorkerSearch({
  value,
  onChange,
}: {
  value: string;
  onChange: (value: string) => void;
}) {
  const { t } = useTranslation();
  return (
    <label className="team-worker-search">
      <Icon name="search" size={15} />
      <input
        type="search"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        aria-label={t("teamview.search_workers_label")}
        placeholder={t("teamview.search_workers")}
      />
    </label>
  );
}

export function TeamRail({
  members,
  summary,
  usage,
  machine,
  chatEnabled,
  unread = 0,
  onChat,
  onWorker,
  onTeam,
  onWorkers,
  open,
  onToggle,
}: {
  members: SessionInfo[];
  summary?: TeamSummary | null;
  usage?: SessionUsage;
  machine?: string;
  chatEnabled: boolean;
  unread?: number;
  onChat?: () => void;
  onWorker?: (s: SessionInfo) => void;
  onTeam?: () => void;
  onWorkers?: (filter: WorkerFilter) => void;
  open: boolean;
  onToggle: () => void;
}) {
  const { t } = useTranslation();
  const [chatNotice, setChatNotice] = useState(false),
    [query, setQuery] = useState("");
  const entries = teamRoster(
    members,
    summary,
    machine || t("teamview.this_machine"),
  );
  const waiting = entries.filter((w) => w.attention),
    large = entries.length > 8;
  const matched = filterWorkers(entries, { query });
  const row = (w: RosterEntry, detail = false) => (
    <button
      className="rail-team-row team-roster-row"
      key={w.id}
      data-testid={`team-row-${w.name}`}
      disabled={!w.session}
      onClick={() => w.session && onWorker?.(w.session)}
      title={t("teamview.open_worker_pane", { name: w.name })}
    >
      <span
        className={`team-dot ${w.attention ? "attention" : w.working ? "working" : "idle"}`}
      />
      <span className="team-roster-main">
        <span className="rail-team-name">{w.name}</span>
        {detail && (
          <span className="team-roster-sub">
            {w.role} · {w.machine}
          </span>
        )}
      </span>
      <span
        className={"team-roster-status" + (w.attention ? " text-warnInk" : "")}
      >
        {w.attention
          ? t("teamview.needs_attention")
          : w.working
            ? t("teamview.state_working")
            : t("teamview.idle")}
      </span>
    </button>
  );
  const more = (filter: WorkerFilter, count: number, key = "view_workers") => (
    <button className="team-roster-link" onClick={() => onWorkers?.(filter)}>
      {t("teamview." + key, { count })} <Icon name="chevronRight" size={12} />
    </button>
  );
  return (
    <section className="rail-section team-rail" data-testid="team-rail">
      <div className="rail-section-head">
        <button
          className="rail-section-toggle"
          data-testid="rail-toggle-team"
          aria-expanded={open}
          onClick={onToggle}
        >
          <Icon
            name={open ? "chevronDown" : "chevronRight"}
            size={14}
            className="rail-chev"
          />
          <span>{t("rail.team_title")}</span>
          <span className="team-roster-count">{entries.length}</span>
        </button>
        <div className="team-rail-actions">
          <button
            className="rail-mini-btn"
            data-testid="team-chat-action"
            title={chatEnabled ? t("rail.team_chat") : t("teamview.chat_off")}
            aria-label={
              chatEnabled ? t("rail.team_chat") : t("teamview.chat_off")
            }
            aria-expanded={!chatEnabled && chatNotice}
            onClick={() =>
              chatEnabled ? onChat?.() : setChatNotice(!chatNotice)
            }
          >
            <Icon name="chat" size={17} />
            {chatEnabled && unread > 0 && (
              <span className="team-chat-badge">{unread}</span>
            )}
          </button>
          <button
            className="rail-mini-btn"
            data-testid="rail-open-team-view"
            title={t("teamview.open_view")}
            aria-label={t("teamview.open_view")}
            onClick={onTeam}
          >
            <Icon name="workList" size={17} />
          </button>
        </div>
      </div>
      {!chatEnabled && chatNotice && (
        <div
          className="team-chat-notice"
          role="status"
          data-testid="team-chat-off"
        >
          <span>{t("teamview.chat_off")}</span>
          <button
            className="rail-mini-btn"
            aria-label={t("teamview.dismiss")}
            onClick={() => setChatNotice(false)}
          >
            <Icon name="x" size={13} />
          </button>
        </div>
      )}
      {open && (
        <div className="rail-section-body rail-team" data-testid="team-panel">
          {large && <WorkerSearch value={query} onChange={setQuery} />}
          {large && query.trim() ? (
            <>
              <p className="team-roster-caption" role="status">
                {t("teamview.worker_matches", { count: matched.length })}
              </p>
              {matched.slice(0, 6).map((w) => row(w, true))}
              {matched.length > 6 &&
                more({ query }, matched.length, "view_matches")}
            </>
          ) : !large ? (
            entries.map((w) => row(w))
          ) : (
            <>
              <p className="team-roster-caption">
                {t("teamview.roster_summary", {
                  count: waiting.length,
                  working: entries.filter((w) => w.working && !w.attention)
                    .length,
                })}
              </p>
              {!!waiting.length && (
                <>
                  <p className="team-roster-caption">
                    {t("teamview.needs_attention")}
                  </p>
                  {waiting.slice(0, 2).map((w) => row(w, true))}
                  {waiting.length > 2 &&
                    more({ attention: true }, waiting.length, "view_attention")}
                </>
              )}
              <p className="team-roster-caption">
                {t("teamview.workers_by_role")}
              </p>
              {[...new Set(entries.map((w) => w.role))].map((role) => {
                const group = entries.filter((w) => w.role === role);
                return (
                  <details className="team-roster-group" key={role}>
                    <summary>
                      <Icon name="chevronRight" size={13} />
                      <span>{role}</span>
                      <span className="team-roster-count">{group.length}</span>
                    </summary>
                    <div className="team-roster-sublist">
                      {group.slice(0, 4).map((w) => row(w))}
                      {group.length > 4 && more({ role }, group.length)}
                    </div>
                  </details>
                );
              })}
              {more({}, entries.length)}
            </>
          )}
          {usage && totalTokens(usage) > 0 && (
            <details className="team-roster-usage" data-testid="team-usage">
              <summary>
                <span>{t("misc.rail.tokens")}</span>
                <span>{formatTokens(totalTokens(usage))}</span>
              </summary>
              {Object.entries(usage.byModel).map(([model, u]) => (
                <div className="rail-team-usage-row" key={model}>
                  <span className="rail-team-usage-model">
                    {model.split(":").pop()}
                  </span>
                  <span>
                    {formatTokens(
                      u.input + u.output + u.cache_read + u.cache_write,
                    )}
                  </span>
                </div>
              ))}
            </details>
          )}
        </div>
      )}
    </section>
  );
}
