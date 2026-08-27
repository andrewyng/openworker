// The staffing gate (agent teams, UX-030): a lead proposes its worker roster.
// Visible layer = the decisions (who — by callname — on what model, why); approving
// grants the lead create/assign/steer for this board — standing, revocable — and
// PRE-SPAWNS the worker sessions. The chat checkbox is the USER's call (default
// OFF, ⓘ per mock); no in-card reply surface: editing happens by replying.
import { useState } from "react";
import type { Item } from "../types";
import { useLanguage } from "../i18n";
import { Icon } from "./Icon";

export function TeamRequestCard({
  item,
  onRespond,
}: {
  item: Extract<Item, { kind: "teamreq" }>;
  onRespond: (approved: boolean, feedback?: string, enableChat?: boolean) => void;
}) {
  const [language] = useLanguage();
  const text = (english: string, portuguese: string, spanish: string) =>
    language === "pt-BR" ? portuguese : language === "es-ES" ? spanish : english;
  const [chat, setChat] = useState(!!item.enable_chat);
  const operation = item.operation || "create";
  const isRelease = operation === "release";
  const actionLabel: Record<string, string> = {
    create: text("Create team & start", "Criar equipe e iniciar", "Crear equipo e iniciar"),
    add: text("Approve roster addition", "Aprovar adição à equipe", "Aprobar incorporación al equipo"),
    replace: text("Approve roster change", "Aprovar mudança da equipe", "Aprobar cambio del equipo"),
    release: text("Release workers", "Liberar workers", "Liberar workers"),
  };
  return (
    <div className="dirreq-card teamreq-card" data-testid="teamreq-card">
      <div className="teamreq-head">
        <Icon name="diamond" size={15} />
        <span className="teamreq-title">
          {text("Proposed", "Proposta", "Propuesta")} {operation === "create" ? text("team", "de equipe", "de equipo") : text("roster change", "de alteração da equipe", "de cambio del equipo")} — {item.members.length} {text(item.members.length === 1 ? "worker" : "workers", item.members.length === 1 ? "worker" : "workers", item.members.length === 1 ? "worker" : "workers")}
        </span>
      </div>
      <div className="teamreq-note">
        {text("Strategy", "Estratégia", "Estrategia")}: <b>{item.strategy || "parallel"}</b>
        {operation !== "create" && <> · {text("Operation", "Operação", "Operación")}: <b>{operation}</b></>}
      </div>
      {item.note && <div className="teamreq-note">{item.note}</div>}
      {!isRelease && item.members.map((m, i) => (
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
      {!!item.release?.length && (
        <div className="teamreq-note">{text("Release idle workers", "Liberar workers ociosos", "Liberar workers inactivos")}: <b>{item.release.join(", ")}</b></div>
      )}
      {!!item.reasons?.length && (
        <div className="teamreq-note">{text("Why now", "Por que agora", "Por qué ahora")}: {item.reasons.join(" · ")}</div>
      )}
      {!!item.integrations?.length && (
        <div className="teamreq-note">{text("Integrations", "Integrações", "Integraciones")}: {item.integrations.join(" · ")}</div>
      )}
      {operation === "create" && <label className="teamreq-chat">
        <input
          type="checkbox"
          data-testid="teamreq-chat-toggle"
          checked={chat}
          onChange={(e) => setChat(e.target.checked)}
        />
        <span>
          {text("Enable", "Ativar", "Activar")} <b># team chat</b>
        </span>
        <span
          className="teamreq-info"
          title={text("A group channel for questions and consensus — @mentions wake the mentioned coworker. Status stays on the board either way.", "Canal para perguntas e consenso — @menções despertam o coworker citado. O status permanece no quadro.", "Canal para preguntas y consenso: las @menciones despiertan al coworker mencionado. El estado permanece en el tablero.")}
        >
          i
        </span>
      </label>}
      <div className="dirreq-actions">
        <span className="teamreq-grant">
          {operation === "create"
            ? text("Approving grants the lead create, assign & steer — this team only, revocable.", "A aprovação concede ao líder criar, atribuir e orientar — apenas nesta equipe e revogável.", "La aprobación permite al líder crear, asignar y orientar: solo este equipo y revocable.")
            : text("Approving applies this roster change only. Active work can never be released.", "A aprovação aplica somente esta mudança. Trabalho ativo nunca pode ser liberado.", "La aprobación aplica solo este cambio. El trabajo activo nunca puede liberarse.")}
        </span>
        <span className="spacer" />
        <button className="btn" onClick={() => onRespond(false)}>
          {text("Not now", "Agora não", "Ahora no")}
        </button>
        <button
          className="btn primary"
          data-testid="teamreq-approve"
          onClick={() => onRespond(true, undefined, chat)}
        >
          {actionLabel[operation]}
        </button>
      </div>
    </div>
  );
}
