// The staffing gate (agent teams, UX-030): a lead proposes its worker roster.
// Visible layer = the decisions (who — by callname — on what model, why); approving
// grants the lead create/assign/steer for this board — standing, revocable — and
// PRE-SPAWNS the worker sessions. The chat checkbox is the USER's call (default
// OFF, ⓘ per mock); no in-card reply surface: editing happens by replying.
import { useState } from "react";
import { Trans, useTranslation } from "react-i18next";
import type { Item } from "../types";
import { Icon } from "./Icon";

export function TeamRequestCard({
  item,
  onRespond,
}: {
  item: Extract<Item, { kind: "teamreq" }>;
  onRespond: (approved: boolean, feedback?: string, enableChat?: boolean) => void;
}) {
  const { t } = useTranslation();
  const [chat, setChat] = useState(!!item.enable_chat);
  return (
    <div className="dirreq-card teamreq-card" data-testid="teamreq-card">
      <div className="teamreq-head">
        <Icon name="diamond" size={15} />
        <span className="teamreq-title">
          {t("team.proposed", { count: item.members.length })}
        </span>
      </div>
      {item.note && <div className="teamreq-note">{item.note}</div>}
      {item.members.map((m, i) => (
        <div className="teamreq-row" key={i}>
          <span className="teamreq-diamond">◆</span>
          <span className="teamreq-body">
            {m.name && <b className="teamreq-name">{m.name}</b>}
            {m.name ? " — " : ""}
            <code>{m.persona}</code>
            {m.model && <span className="teamreq-model"> · {m.model}</span>}
            {m.reason && <span className="teamreq-reason"> — {m.reason}</span>}
          </span>
        </div>
      ))}
      <label className="teamreq-chat">
        <input
          type="checkbox"
          data-testid="teamreq-chat-toggle"
          checked={chat}
          onChange={(e) => setChat(e.target.checked)}
        />
        <span>
          <Trans i18nKey="team.enable_chat" components={{ b: <b /> }} />
        </span>
        <span className="teamreq-info" title={t("team.chat_info")}>
          i
        </span>
      </label>
      <div className="dirreq-actions">
        <span className="teamreq-grant">{t("team.grant_note")}</span>
        <span className="spacer" />
        <button className="btn" onClick={() => onRespond(false)}>
          {t("team.not_now")}
        </button>
        <button
          className="btn primary"
          data-testid="teamreq-approve"
          onClick={() => onRespond(true, undefined, chat)}
        >
          {t("team.create_start")}
        </button>
      </div>
    </div>
  );
}
