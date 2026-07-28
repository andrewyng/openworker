import { useState } from "react";
import { getI18n, useTranslation } from "react-i18next";
import type { ApprovalDecision, Item } from "../types";
import { humanizeApprovalTitle, type HumanLine } from "../humanize";
import { Icon } from "./Icon";

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

// Human verbs kept for the §25 grant lines (the card title now comes from humanize.ts).
// Values are i18n keys resolved at render time.
const TOOL_VERBS: Record<string, string> = {
  write_file: "approval.verbs.write_file",
  replace_in_file: "approval.verbs.edit_file",
  apply_patch: "approval.verbs.apply_patch",
  apply_unified_diff: "approval.verbs.apply_patch",
  run_shell: "approval.verbs.run_command",
  send_message: "approval.verbs.send_message",
  send_file: "approval.verbs.send_file",
};

// §35: routine workspace writes render as a compact ROW; everything else is a full card.
const FILE_WRITES = new Set(["write_file", "replace_in_file", "apply_patch", "apply_unified_diff"]);
// Actions that leave the Mac get the warm border + explicit destination note.
const EXTERNAL = new Set(["send_message", "send_file"]);

type ApprovalItem = Extract<Item, { kind: "approval" }>;

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

export function TitleText({ line }: { line: HumanLine }) {
  return (
    <span className="approval-title">
      {line.pre}
      {line.obj && <b>{line.obj}</b>}
      {line.post}
    </span>
  );
}

// Plain-words scope note (replaces the "local action" badge): where does this act?
// Shared with the parked-approval card (InboxItemCard) so both dialects match (§35).
// Uses the fixed-T form because this helper is also called from non-component modules.
export function scopeNote(
  name: string,
  args: any,
  category?: string,
): { text: string; external: boolean } {
  const tt = getI18n().getFixedT(null, "translation");
  if (category === "connector") return { text: tt("approval.scope.connector"), external: true };
  if (EXTERNAL.has(name)) {
    const platform = String(args?.target ?? "").split(":")[0];
    const names: Record<string, string> = { slack: "Slack", telegram: "Telegram" };
    const dest = names[platform] || platform || tt("approval.scope.connected_chat_fallback");
    return { text: tt("approval.scope.leaves_mac", { dest }), external: true };
  }
  const overwrite = name === "write_file" && args?.overwrite;
  return {
    text: tt("approval.scope.stays_mac") + (overwrite ? tt("approval.scope.overwrite_suffix") : ""),
    external: false,
  };
}

// The proposed content/command, straight from the tool call's ARGS — the file/action
// doesn't exist yet, so no viewer could show it (§35; see UX-018 mock note).
// Clamps by CHARACTERS as well as lines: a one-paragraph Slack digest has no
// newlines at all and once ballooned the card to full-transcript height.
const PREVIEW_LINES = 5;
const PREVIEW_CHARS = 420;

export function PreviewBlock({ text, mono = true }: { text: string; mono?: boolean }) {
  const { t } = useTranslation();
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
            ? t("approval.preview_less")
            : lines.length > PREVIEW_LINES
              ? t("approval.preview_all_lines", { n: lines.length })
              : t("approval.preview_full")}
        </button>
      )}
    </div>
  );
}

// Outbound message text: short one-liners keep the cozy inline quote; anything
// long (or multi-line) gets the clamped preview so the card stays card-sized.
function MessagePreview({ text, label }: { text: string; label?: string }) {
  if (text.length <= 220 && !text.includes("\n")) {
    return (
      <div className="approval-with">
        {label ? `${label}: ` : ""}“{text}”
      </div>
    );
  }
  return <PreviewBlock text={text} mono={false} />;
}

function Buttons({
  item,
  onApprove,
  runTask,
  primaryLabel,
}: {
  item: ApprovalItem;
  onApprove: (decision: ApprovalDecision) => void;
  runTask?: { id: string; title: string } | null;
  primaryLabel: string;
}) {
  const { t } = useTranslation();
  const connector = item.category === "connector";
  const offerStanding = !!(runTask && item.standingTarget);
  const verbKey = TOOL_VERBS[item.name];
  const verbName = verbKey ? t(verbKey).toLowerCase() : item.name;
  return (
    <div className="approval-btns">
      <button className="btn approval-primary" onClick={() => onApprove("once")}>
        {primaryLabel}
      </button>
      {offerStanding && (
        <button
          className="btn"
          title={t("approval.btn.always_task_title", { name: item.name, target: item.standingTarget, task: runTask?.title || t("approval.btn.this_automation") })}
          onClick={() => onApprove("always_task")}
        >
          {t("approval.btn.allow_every_time")}
        </button>
      )}
      {/* In a run context the task-persistent grant replaces the session-scoped one —
          a run session is ephemeral, and two adjacent "always" buttons would blur
          exactly the scope distinction §25 exists to draw. Same rule for run_shell:
          the command-scoped button below is the specific (safer) grant, so the
          tool-wide one stays out of the card. */}
      {!connector && !offerStanding && item.name !== "run_shell" && (
        <button
          className="btn"
          title={t("approval.btn.always_tool_title", { name: verbName })}
          onClick={() => onApprove("always_tool")}
        >
          {t("approval.btn.always_allow")}
        </button>
      )}
      {item.name === "run_shell" && (
        <button className="btn" onClick={() => onApprove("always_command")}>
          {t("approval.btn.always_command")}
        </button>
      )}
      <span className="spacer" />
      <button className="btn quiet-deny" onClick={() => onApprove("deny")}>
        {t("approval.btn.deny")}
      </button>
    </div>
  );
}

export function ApprovalCard({
  item,
  onApprove,
  runTask,
  compact = false,
}: {
  item: ApprovalItem;
  onApprove: (decision: ApprovalDecision) => void;
  // Present when this approval was raised inside an automation run — unlocks the
  // task-persistent "Allow every time" (in-app only, §25).
  runTask?: { id: string; title: string } | null;
  compact?: boolean;
}) {
  const { t } = useTranslation();
  const [peek, setPeek] = useState(false);
  const title = humanizeApprovalTitle(item.name, item.args);
  const scope = scopeNote(item.name, item.args, item.category);
  const grants = item.name === "create_scheduled_task" ? permissionLines(item.args) : [];
  // "requires approval" is the engine's default boilerplate — only surface a real reason.
  const reason = item.reason && item.reason !== "requires approval" ? item.reason : "";
  const offerStanding = !!(runTask && item.standingTarget);
  const dock = compact ? " approval-dock" : "";

  // §35 compact row: routine workspace writes — one line, preview expands inline from the
  // tool args. Standing/grant flows keep the full card (they carry §25 consent weight).
  const content = typeof item.args?.content === "string" ? item.args.content : "";
  if (FILE_WRITES.has(item.name) && !offerStanding && !grants.length && !item.resolved) {
    return (
      <div className={"approval approval-row" + dock} data-testid="approval-row">
        <div className="approval-row-line">
          <TitleText line={title} />
          {content && (
            <button className="approval-peek" onClick={() => setPeek((v) => !v)}>
              {t("approval.preview_label")} {peek ? "▴" : "▾"}
            </button>
          )}
          <span className="spacer" />
          <Buttons item={item} onApprove={onApprove} runTask={runTask} primaryLabel={t("approval.allow")} />
        </div>
        {peek && content && <PreviewBlock text={content} />}
        {reason && <div className="approval-reason">{reason}</div>}
      </div>
    );
  }

  return (
    <div className={"approval" + (scope.external ? " approval-external" : "") + dock}>
      <div className="approval-top">
        <div className="approval-heading">
          <span className="approval-ico" title={t("approval.tool_title", { name: item.name })}>
            <Icon name="shield" size={15} />
          </span>
          <TitleText line={title} />
        </div>
        <span className={"approval-scope" + (scope.external ? " out" : "")}>{scope.text}</span>
      </div>

      {/* Tool-shaped previews — the proposal, not an args dump. */}
      {item.name === "run_shell" && item.args?.command && (
        <PreviewBlock text={String(item.args.command)} />
      )}
      {FILE_WRITES.has(item.name) && content && <PreviewBlock text={content} />}
      {item.name === "send_file" && (
        <>
          <span className="approval-filechip">
            <span className="ico">
              <Icon name="file" size={13} />
            </span>
            {String(item.args?.path ?? "").split("/").pop() || t("approval.file_fallback")}
            {item.args?.as_screenshot ? t("approval.as_png_screenshot") : ""}
          </span>
          {item.args?.comment && (
            <MessagePreview text={String(item.args.comment)} label={t("approval.with_message")} />
          )}
        </>
      )}
      {item.name === "send_message" && item.args?.text && (
        <MessagePreview text={String(item.args.text)} />
      )}

      {grants.length > 0 && (
        <div className="approval-grants" data-testid="approval-grants">
          {grants.map((g, i) => {
            const verbKey = TOOL_VERBS[g.tool];
            return (
              <div className="approval-grant" key={i} data-access={g.access}>
                <span className={"grant-mark" + (g.access === "write" ? " write" : "")}>
                  {g.access === "write" ? "✓" : "·"}
                </span>
                <span className="grant-line">
                  {verbKey ? t(verbKey) : g.tool} <code className="approval-tool">{g.target}</code>
                  <span className="grant-note">
                    {g.access === "write" ? t("approval.grant.always_after_approve") : t("approval.grant.read_only")}
                  </span>
                </span>
              </div>
            );
          })}
        </div>
      )}
      {/* Long-tail tools: no bespoke preview — fall back to the compact args line. */}
      {!FILE_WRITES.has(item.name) &&
        !["run_shell", "send_message", "send_file"].includes(item.name) &&
        !grants.length &&
        shortArgs(item.args) && <div className="approval-rest">{shortArgs(item.args)}</div>}
      {reason && <div className="approval-reason">{reason}</div>}

      {item.resolved ? (
        <div className="resolved">{t("approval.resolved_prefix", { state: item.resolved.replace(/_/g, " ") })}</div>
      ) : (
        <Buttons item={item} onApprove={onApprove} runTask={runTask} primaryLabel={t("approval.allow_once")} />
      )}
    </div>
  );
}
