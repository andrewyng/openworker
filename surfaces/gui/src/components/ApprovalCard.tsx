import { useState } from "react";
import type { ApprovalDecision, Item } from "../types";
import { humanizeApprovalTitle, type HumanLine } from "../humanize";
import { useLanguage, type Language } from "../i18n";
import { Icon } from "./Icon";
import { approvalCopy, approvalToolVerb, localizedApprovalDecision, localizedApprovalLine, standingGrantTitle } from "./approvalI18n";

export function shortArgs(args: any): string {
  if (!args || typeof args !== "object") return "";
  return Object.entries(args)
    .map(([k, v]) => {
      let s = typeof v === "string" ? v : JSON.stringify(v);
      if (s.length > 96) s = s.slice(0, 95) + "...";
      return `${k}=${s.replace(/\n/g, " ")}`;
    })
    .join("  ");
}

// §35: routine workspace writes render as a compact ROW; everything else is a full card.
const FILE_WRITES = new Set(["write_file", "replace_in_file", "apply_patch", "apply_unified_diff"]);
// Actions that leave the Mac get the warm border + explicit destination note.
const EXTERNAL = new Set(["send_message", "send_file"]);

type ApprovalItem = Extract<Item, { kind: "approval" }>;

// Per-tool button copy (§7): a skill proposal is an "add", not an "allow". Shared with the
// parked Inbox card so both dialects match.
export function approvalActionLabels(name?: string, language: Language = "en-US"): { allow: string; deny: string } {
  const copy = approvalCopy(language);
  return name === "save_skill"
    ? { allow: copy.addSkills, deny: copy.notNow }
    : { allow: copy.allowOnce, deny: copy.deny };
}

// save_skill's review surface (SKILLS-SPEC §5.2): description, the full instructions
// (clamped, expandable, scrollable), every bundled file, and the guaranteed footer that
// answers "added WHERE, available WHEN". Shared verbatim with the parked Inbox card —
// one decision, one dialect.
export function SaveSkillPreview({ args, language = "en-US" }: { args: any; language?: Language }) {
  const copy = approvalCopy(language);
  return (
    <>
      {args?.description && <div className="approval-with">{String(args.description)}</div>}
      {args?.instructions && <PreviewBlock text={String(args.instructions)} mono={false} language={language} />}
      {Array.isArray(args?.files) && args.files.length > 0 && (
        <div data-testid="skill-bundle-files">
          {args.files.map((f: unknown, i: number) => (
            <span className="approval-filechip" key={i}>
              <span className="ico">
                <Icon name="file" size={13} />
              </span>
              {String(f).split(/[\\/]/).pop() || String(f)}
            </span>
          ))}
        </div>
      )}
      <div className="approval-with">
        {copy.skillFooter}
      </div>
    </>
  );
}

// A `permissions` proposal on the create_scheduled_task consent card (§25): reads are
// disclosure lines, writes are the standing grants the approval mints.
interface PermissionLine {
  tool: string;
  target: string;
  access: string;
}

function permissionLines(args: any): PermissionLine[] {
  const raw = args?.permissions;
  if (!Array.isArray(raw)) return [];
  return raw
    .filter((p) => p && typeof p === "object" && p.tool && p.target)
    .map((p) => ({ tool: String(p.tool), target: String(p.target), access: String(p.access || "read") }));
}

export function TitleText({ line, language = "en-US" }: { line: HumanLine; language?: Language }) {
  const localized = localizedApprovalLine(line, language);
  return (
    <span className="approval-title">
      {localized.pre}
      {line.obj && <b>{line.obj}</b>}
      {localized.post}
    </span>
  );
}

// The host a fetch-card domain grant would cover (§1.9): lowercased, `www.` stripped —
// pure spelling only, mirroring the server's minting in `allow_domain_for_session`. The
// button must name exactly what the grant covers. "" when the URL doesn't parse.
export function grantHost(url: any): string {
  try {
    const h = new URL(String(url ?? "")).hostname.toLowerCase();
    return h.startsWith("www.") ? h.slice(4) : h;
  } catch {
    return "";
  }
}

// Plain-words scope note (replaces the "local action" badge): where does this act?
// Shared with the parked-approval card (InboxItemCard) so both dialects match (§35).
export function scopeNote(
  name: string,
  args: any,
  category?: string,
  language: Language = "en-US",
): { text: string; external: boolean } {
  const copy = approvalCopy(language);
  // save_skill's corner answers WHERE (SKILLS-SPEC §5.2): the exact place to find, edit,
  // or turn off the skill afterwards.
  if (name === "save_skill") return { text: copy.skillDestination, external: false };
  if (category === "connector") return { text: copy.connectedService, external: true };
  // Egress (§1.9): the request itself reaches the network — never "stays on this computer".
  if (name === "web_fetch")
    return { text: copy.leavesFor(grantHost(args?.url) || (language === "pt-BR" ? "a web" : language === "es-ES" ? "la web" : "the web")), external: true };
  if (name === "web_search")
    return { text: copy.leavesFor(language === "pt-BR" ? "seu provedor de pesquisa" : language === "es-ES" ? "tu proveedor de búsqueda" : "your search provider"), external: true };
  if (EXTERNAL.has(name)) {
    const platform = String(args?.target ?? "").split(":")[0];
    const names: Record<string, string> = { slack: "Slack", telegram: "Telegram" };
    return { text: copy.leavesFor(names[platform] || platform || (language === "pt-BR" ? "uma conversa conectada" : language === "es-ES" ? "un chat conectado" : "a connected chat")), external: true };
  }
  const overwrite = name === "write_file" && args?.overwrite;
  return { text: copy.staysHere + (overwrite ? ` · ${copy.overwrites}` : ""), external: false };
}

// The proposed content/command, straight from the tool call's ARGS — the file/action
// doesn't exist yet, so no viewer could show it (§35; see UX-018 mock note).
// Clamps by CHARACTERS as well as lines: a one-paragraph Slack digest has no
// newlines at all and once ballooned the card to full-transcript height.
const PREVIEW_LINES = 5;
const PREVIEW_CHARS = 420;

export function PreviewBlock({ text, mono = true, language = "en-US" }: { text: string; mono?: boolean; language?: Language }) {
  const copy = approvalCopy(language);
  const [all, setAll] = useState(false);
  const lines = text.split("\n");
  const clipped = lines.length > PREVIEW_LINES || text.length > PREVIEW_CHARS;
  let shown = text;
  if (!all && clipped) {
    shown = lines.slice(0, PREVIEW_LINES).join("\n");
    if (shown.length > PREVIEW_CHARS) shown = shown.slice(0, PREVIEW_CHARS).trimEnd() + "…";
  }
  return (
    <div className={"approval-prev" + (mono ? "" : " prose")}>
      {shown}
      {clipped && (
        <button className="approval-prev-more" onClick={() => setAll((v) => !v)}>
          {all
            ? copy.showLess
            : lines.length > PREVIEW_LINES
              ? copy.showAllLines(lines.length)
              : copy.showFullMessage}
        </button>
      )}
    </div>
  );
}

// Outbound message text: short one-liners keep the cozy inline quote; anything
// long (or multi-line) gets the clamped preview so the card stays card-sized.
function MessagePreview({ text, label, language = "en-US" }: { text: string; label?: string; language?: Language }) {
  if (text.length <= 220 && !text.includes("\n")) {
    return (
      <div className="approval-with">
        {label ? `${label}: ` : ""}“{text}”
      </div>
    );
  }
  return <PreviewBlock text={text} mono={false} language={language} />;
}

function Buttons({
  item,
  onApprove,
  runTask,
  primaryLabel,
  denyLabel = "Deny",
  language = "en-US",
  autoApprove = false,
}: {
  item: ApprovalItem;
  onApprove: (decision: ApprovalDecision) => void;
  runTask?: { id: string; title: string } | null;
  primaryLabel: string;
  denyLabel?: string;
  language?: Language;
  // Session is in Auto-Approve mode: session grants don't skip the reviewer there (§1.5),
  // so no session-scoped "always" button is shown at all — a button that lies is worse
  // than none. Allow once / Deny only.
  autoApprove?: boolean;
}) {
  const copy = approvalCopy(language);
  const connector = item.category === "connector";
  const mcp = item.category === "mcp";
  const offerStanding = !!(runTask && item.standingTarget);
  // §1.9: egress grants are destination-shaped. web_fetch offers the DOMAIN — tool-wide
  // would cover every future destination, so it's withheld (and server-refused). web_search
  // has a fixed destination (the configured provider), so tool-wide IS provider-wide and
  // the button is labelled by what it actually grants: searches.
  const fetchHost = item.name === "web_fetch" ? grantHost(item.args?.url) : "";
  const noSessionGrant =
    autoApprove ||
    offerStanding ||
    connector ||
    mcp ||
    item.name === "run_shell" ||
    item.name === "save_skill" ||
    item.name === "web_fetch" ||
    item.name === "web_search";
  return (
    <div className="approval-btns">
      <button className="btn approval-primary" onClick={() => onApprove("once")}>
        {primaryLabel}
      </button>
      {offerStanding && (
        <button
          className="btn"
          title={standingGrantTitle(language, item.name, item.standingTarget || "", runTask?.title || (language === "pt-BR" ? "esta automação" : language === "es-ES" ? "esta automatización" : "this automation"))}
          onClick={() => onApprove("always_task")}
        >
          {copy.allowEveryTime}
        </button>
      )}
      {/* In a run context the task-persistent grant replaces the session-scoped one —
          a run session is ephemeral, and two adjacent "always" buttons would blur
          exactly the scope distinction §25 exists to draw. Same rule for run_shell:
          the command-scoped button below is the specific (safer) grant, so the
          tool-wide one stays out of the card. */}
      {/* save_skill: no session-wide "always" — every skill proposal gets its own review
          (SKILLS-SPEC §5: one gate, always). */}
      {!noSessionGrant && (
        <button
          className="btn"
          title={language === "pt-BR" ? `Sempre permitir ${approvalToolVerb(item.name, language).toLowerCase()} nesta conversa` : language === "es-ES" ? `Permitir siempre ${approvalToolVerb(item.name, language).toLowerCase()} en esta conversación` : `Always allow ${approvalToolVerb(item.name, language).toLowerCase()} for this session`}
          onClick={() => onApprove("always_tool")}
        >
          {copy.alwaysAllow}
        </button>
      )}
      {!autoApprove && !offerStanding && item.name === "web_fetch" && fetchHost && (
        <button
          className="btn"
          title={language === "pt-BR" ? `Toda consulta a ${fetchHost} e seus subdomínios será executada sem perguntar durante o restante desta conversa` : language === "es-ES" ? `Cada consulta a ${fetchHost} y sus subdominios se ejecutará sin preguntar durante el resto de esta conversación` : `Every fetch to ${fetchHost} (and its subdomains) runs without asking for the rest of this session`}
          onClick={() => onApprove("always_domain")}
        >
          {language === "pt-BR" ? `Sempre permitir ${fetchHost} nesta conversa` : language === "es-ES" ? `Permitir siempre ${fetchHost} en esta conversación` : `Always allow ${fetchHost} this session`}
        </button>
      )}
      {!autoApprove && !offerStanding && item.name === "web_search" && (
        <button
          className="btn"
          title={language === "pt-BR" ? "Toda pesquisa na web será executada sem perguntar durante o restante desta conversa; a permissão termina se você alterar o provedor de pesquisa" : language === "es-ES" ? "Cada búsqueda web se ejecutará sin preguntar durante el resto de esta conversación; el permiso termina si cambias el proveedor de búsqueda" : "Every web search runs without asking for the rest of this session — the grant ends if you change the search provider"}
          onClick={() => onApprove("always_tool")}
        >
          {language === "pt-BR" ? "Sempre permitir pesquisas nesta conversa" : language === "es-ES" ? "Permitir siempre las búsquedas en esta conversación" : "Always allow searches this session"}
        </button>
      )}
      {!autoApprove && item.name === "run_shell" && (
        <button className="btn" onClick={() => onApprove("always_command")}>
          {copy.alwaysCommand}
        </button>
      )}
      {/* Session-wide read-only grant (owner ask 2026-08-11): offered only when the
          server's conservative classifier accepted THIS command — one click, then every
          local-read command in the session runs without a card. Network, writes, and
          anything doubtful keep asking. */}
      {item.name === "run_shell" && item.readonlyOk && !item.resolved && (
        <button
          className="btn"
          data-testid="allow-readonly-session"
          title={language === "pt-BR" ? "Permitir automaticamente comandos somente leitura durante o restante desta conversa" : language === "es-ES" ? "Permitir automáticamente comandos de solo lectura durante el resto de esta conversación" : "Auto-allow read-only commands (local reads and pipelines only — no network, writes, or interpreters) for the rest of this session"}
          onClick={() => onApprove("readonly_session")}
        >
          {copy.readonlyCommands}
        </button>
      )}
      <span className="spacer" />
      <button className="btn quiet-deny" onClick={() => onApprove("deny")}>
        {denyLabel}
      </button>
    </div>
  );
}

export function ApprovalCard({
  item,
  onApprove,
  runTask,
  compact = false,
  autoApprove = false,
}: {
  item: ApprovalItem;
  onApprove: (decision: ApprovalDecision) => void;
  // Present when this approval was raised inside an automation run — unlocks the
  // task-persistent "Allow every time" (in-app only, §25).
  runTask?: { id: string; title: string } | null;
  compact?: boolean;
  // Session is in Auto-Approve mode — this card is a reviewer fall-through, and session
  // grants wouldn't skip the reviewer anyway (§1.5), so the "always" buttons are hidden.
  autoApprove?: boolean;
}) {
  const [language] = useLanguage();
  const copy = approvalCopy(language);
  const [peek, setPeek] = useState(false);
  const title = humanizeApprovalTitle(item.name, item.args);
  const scope = scopeNote(item.name, item.args, item.category, language);
  const grants = item.name === "create_scheduled_task" ? permissionLines(item.args) : [];
  // "requires approval" is the engine's default boilerplate — only surface a real reason.
  const reason = item.reason && item.reason !== "requires approval" ? item.reason : "";
  const offerStanding = !!(runTask && item.standingTarget);
  const dock = compact ? " approval-dock" : "";
  // OPE-114 §1: the command text cannot tell you the agent wrote this file a moment ago.
  const provenance = item.provenance ? (
    <div className="approval-provenance">
      <Icon name="warning" size={13} />
      <span>{item.provenance}</span>
    </div>
  ) : null;

  // §35 compact row: routine workspace writes — one line, preview expands inline from the
  // tool args. Standing/grant flows keep the full card (they carry §25 consent weight).
  const content = typeof item.args?.content === "string" ? item.args.content : "";
  if (FILE_WRITES.has(item.name) && !offerStanding && !grants.length && !item.resolved) {
    return (
      <div className={"approval approval-row" + dock} data-testid="approval-row">
        <div className="approval-row-line">
          <TitleText line={title} language={language} />
          {content && (
            <button className="approval-peek" onClick={() => setPeek((v) => !v)}>
              {copy.preview} {peek ? "▴" : "▾"}
            </button>
          )}
          <span className="spacer" />
          <Buttons
            item={item}
            onApprove={onApprove}
            runTask={runTask}
            primaryLabel={copy.allow}
            denyLabel={approvalActionLabels(item.name, language).deny}
            language={language}
            autoApprove={autoApprove}
          />
        </div>
        {peek && content && <PreviewBlock text={content} language={language} />}
        {provenance}
        {reason && <div className="approval-reason">{reason}</div>}
      </div>
    );
  }

  return (
    <div className={"approval" + (scope.external ? " approval-external" : "") + dock}>
      <div className="approval-top">
        <div className="approval-heading">
          <span className="approval-ico" title={`${copy.tool}: ${item.name}`}>
            <Icon name="shield" size={15} />
          </span>
          <TitleText line={title} language={language} />
        </div>
        <span className={"approval-scope" + (scope.external ? " out" : "")}>{scope.text}</span>
      </div>

      {/* Tool-shaped previews — the proposal, not an args dump. */}
      {item.name === "run_shell" && item.args?.command && (
        <PreviewBlock text={String(item.args.command)} language={language} />
      )}
      {FILE_WRITES.has(item.name) && content && <PreviewBlock text={content} language={language} />}
      {item.name === "send_file" && (
        <>
          <span className="approval-filechip">
            <span className="ico">
              <Icon name="file" size={13} />
            </span>
            {String(item.args?.path ?? "").split("/").pop() || copy.file}
            {item.args?.as_screenshot ? ` · ${copy.pngScreenshot}` : ""}
          </span>
          {item.args?.comment && (
            <MessagePreview text={String(item.args.comment)} label={copy.withMessage} language={language} />
          )}
        </>
      )}
      {item.name === "send_message" && item.args?.text && (
        <MessagePreview text={String(item.args.text)} language={language} />
      )}
      {/* save_skill (SKILLS-SPEC §5.2): the arguments ARE the review surface. */}
      {item.name === "save_skill" && <SaveSkillPreview args={item.args} language={language} />}
      {/* web_search (§1.9): name the LIVE destination — "currently", never "default",
          because the card must show the setting as it stands right now. */}
      {item.name === "web_search" && (
        <div className="approval-with">
          {copy.searchProvider}
          {item.searchProvider ? ` ${copy.currentProvider(item.searchProvider)}` : ""}.
        </div>
      )}

      {grants.length > 0 && (
        <div className="approval-grants" data-testid="approval-grants">
          {grants.map((g, i) => (
            <div className="approval-grant" key={i} data-access={g.access}>
              <span className={"grant-mark" + (g.access === "write" ? " write" : "")}>
                {g.access === "write" ? "✓" : "·"}
              </span>
              <span className="grant-line">
                {approvalToolVerb(g.tool, language)} <code className="approval-tool">{g.target}</code>
                <span className="grant-note">
                  {g.access === "write" ? ` — ${copy.alwaysAfterApproval}` : ` — ${copy.readOnly}`}
                </span>
              </span>
            </div>
          ))}
        </div>
      )}
      {/* Long-tail tools: no bespoke preview — fall back to the compact args line. */}
      {!FILE_WRITES.has(item.name) &&
        !["run_shell", "send_message", "send_file", "save_skill"].includes(item.name) &&
        !grants.length &&
        shortArgs(item.args) && <div className="approval-rest">{shortArgs(item.args)}</div>}
      {provenance}
      {reason && <div className="approval-reason">{reason}</div>}

      {item.resolved ? (
        <div className="resolved">{copy.approved}: {localizedApprovalDecision(item.resolved, language)}</div>
      ) : (
        <Buttons
          item={item}
          onApprove={onApprove}
          runTask={runTask}
          primaryLabel={approvalActionLabels(item.name, language).allow}
          denyLabel={approvalActionLabels(item.name, language).deny}
          language={language}
          autoApprove={autoApprove}
        />
      )}
    </div>
  );
}
