import { useState } from "react";
import { useTranslation } from "react-i18next";
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
const TOOL_VERBS: Record<string, string> = {
  write_file: "approval.writeFile",
  replace_in_file: "approval.editFile",
  apply_patch: "approval.applyPatch",
  apply_unified_diff: "approval.applyPatch",
  run_shell: "approval.runCommand",
  send_message: "approval.sendMessage",
  send_file: "approval.sendFile",
};

// §35: routine workspace writes render as a compact ROW; everything else is a full card.
const FILE_WRITES = new Set(["write_file", "replace_in_file", "apply_patch", "apply_unified_diff"]);
// Actions that leave the Mac get the warm border + explicit destination note.
const EXTERNAL = new Set(["send_message", "send_file"]);

type ApprovalItem = Extract<Item, { kind: "approval" }>;

// Per-tool button copy (§7): a skill proposal is an "add", not an "allow". Shared with the
// parked Inbox card so both dialects match.
export function approvalActionLabels(
  t: (key: string, opts?: Record<string, unknown>) => string,
  name?: string,
): { allow: string; deny: string } {
  return name === "save_skill"
    ? { allow: t("approval.addToMySkills"), deny: t("approval.notNow") }
    : { allow: t("approval.allowOnce"), deny: t("approval.deny") };
}

// save_skill's review surface (SKILLS-SPEC §5.2): description, the full instructions
// (clamped, expandable, scrollable), every bundled file, and the guaranteed footer that
// answers "added WHERE, available WHEN". Shared verbatim with the parked Inbox card —
// one decision, one dialect.
export function SaveSkillPreview({ args }: { args: any }) {
  const { t } = useTranslation();
  return (
    <>
      {args?.description && <div className="approval-with">{String(args.description)}</div>}
      {args?.instructions && <PreviewBlock text={String(args.instructions)} mono={false} />}
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
      <div className="approval-with">{t("approval.addToSkillsNote")}</div>
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
export function scopeNote(
  t: (key: string, opts?: Record<string, unknown>) => string,
  name: string,
  args: any,
  category?: string,
): { text: string; external: boolean } {
  // save_skill's corner answers WHERE (SKILLS-SPEC §5.2): the exact place to find, edit,
  // or turn off the skill afterwards.
  if (name === "save_skill") return { text: t("approval.savesToSkills"), external: false };
  if (category === "connector") return { text: t("approval.actsOnConnectedService"), external: true };
  if (EXTERNAL.has(name)) {
    const platform = String(args?.target ?? "").split(":")[0];
    const names: Record<string, string> = { slack: "Slack", telegram: "Telegram" };
    return {
      text: t("approval.leavesMac", {
        platform: names[platform] || platform || t("approval.connectedChat"),
      }),
      external: true,
    };
  }
  const overwrite = name === "write_file" && args?.overwrite;
  return {
    text: t("approval.staysOnMac") + (overwrite ? t("approval.overwriteExisting") : ""),
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
            ? t("approval.showLess")
            : lines.length > PREVIEW_LINES
              ? t("approval.showAllLines", { count: lines.length })
              : t("approval.showFullMessage")}
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
  denyLabel,
}: {
  item: ApprovalItem;
  onApprove: (decision: ApprovalDecision) => void;
  runTask?: { id: string; title: string } | null;
  primaryLabel: string;
  denyLabel?: string;
}) {
  const { t } = useTranslation();
  const connector = item.category === "connector";
  const offerStanding = !!(runTask && item.standingTarget);
  return (
    <div className="approval-btns">
      <button className="btn approval-primary" onClick={() => onApprove("once")}>
        {primaryLabel}
      </button>
      {offerStanding && (
        <button
          className="btn"
          title={t("approval.alwaysAllowTitle", {
            name: item.name,
            target: item.standingTarget,
            title: runTask?.title || t("approval.thisAutomation"),
          })}
          onClick={() => onApprove("always_task")}
        >
          {t("approval.allowEveryTime")}
        </button>
      )}
      {/* In a run context the task-persistent grant replaces the session-scoped one —
          a run session is ephemeral, and two adjacent "always" buttons would blur
          exactly the scope distinction §25 exists to draw. Same rule for run_shell:
          the command-scoped button below is the specific (safer) grant, so the
          tool-wide one stays out of the card. */}
      {/* save_skill: no session-wide "always" — every skill proposal gets its own review
          (SKILLS-SPEC §5: one gate, always). */}
      {!connector && !offerStanding && item.name !== "run_shell" && item.name !== "save_skill" && (
        <button
          className="btn"
          title={t("approval.alwaysAllowToolTitle", {
            verb: TOOL_VERBS[item.name] ? t(TOOL_VERBS[item.name]).toLowerCase() : item.name,
          })}
          onClick={() => onApprove("always_tool")}
        >
          {t("approval.alwaysAllow")}
        </button>
      )}
      {item.name === "run_shell" && (
        <button className="btn" onClick={() => onApprove("always_command")}>
          {t("approval.alwaysAllowThisCommand")}
        </button>
      )}
      <span className="spacer" />
      <button className="btn quiet-deny" onClick={() => onApprove("deny")}>
        {denyLabel ?? t("approval.deny")}
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
  const [peek, setPeek] = useState(false);
  const { t } = useTranslation();
  const title = humanizeApprovalTitle(item.name, item.args);
  const scope = scopeNote(t, item.name, item.args, item.category);
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
              {t("approval.preview")} {peek ? "▴" : "▾"}
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
          <span className="approval-ico" title={t("approval.toolLabel", { name: item.name })}>
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
            {String(item.args?.path ?? "").split("/").pop() || t("approval.fileLabel")}
            {item.args?.as_screenshot ? t("approval.asPngScreenshot") : ""}
          </span>
          {item.args?.comment && (
            <MessagePreview text={String(item.args.comment)} label={t("approval.withMessage")} />
          )}
        </>
      )}
      {item.name === "send_message" && item.args?.text && (
        <MessagePreview text={String(item.args.text)} />
      )}
      {/* save_skill (SKILLS-SPEC §5.2): the arguments ARE the review surface. */}
      {item.name === "save_skill" && <SaveSkillPreview args={item.args} />}

      {grants.length > 0 && (
        <div className="approval-grants" data-testid="approval-grants">
          {grants.map((g, i) => (
            <div className="approval-grant" key={i} data-access={g.access}>
              <span className={"grant-mark" + (g.access === "write" ? " write" : "")}>
                {g.access === "write" ? "✓" : "·"}
              </span>
              <span className="grant-line">
                {TOOL_VERBS[g.tool] ? t(TOOL_VERBS[g.tool]) : g.tool} <code className="approval-tool">{g.target}</code>
                <span className="grant-note">
                  {g.access === "write" ? t("approval.grantNoteWrite") : t("approval.grantNoteRead")}
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
      {reason && <div className="approval-reason">{reason}</div>}

      {item.resolved ? (
        <div className="resolved">
          {t("approval.approvedWith", { resolution: item.resolved.replace("_", " ") })}
        </div>
      ) : (
        <Buttons
          item={item}
          onApprove={onApprove}
          runTask={runTask}
          primaryLabel={approvalActionLabels(t, item.name).allow}
          denyLabel={approvalActionLabels(t, item.name).deny}
        />
      )}
    </div>
  );
}
