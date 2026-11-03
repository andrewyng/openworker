import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { Item } from "../types";
import { humanizeTool } from "../humanize";
import { Icon } from "./Icon";

// OPE-238: "N changes auto-approved". After a turn in which auto-approve let actions run
// with no approval card (a gate tier, OPE-233, or a reviewer "allow"), the user who was
// away sees every change here, with diffs, without opening the audit log. Collapsed at
// rest; one click expands. Built from the turn's tool items, whose approval origin,
// effect and outcome are persisted on the tool message, so a reloaded session shows the
// same block.
//
// The count means "things that changed state and that nobody approved": a read-only
// action (a command the read-only tier classified, a web read) is listed apart and never
// counted; an attempt that failed is counted as failed, not as a change.

type ToolItem = Extract<Item, { kind: "tool" }>;

// Mirrors `_UNASKED_ORIGINS` in coworker/engine.py.
const UNASKED_ORIGINS = new Set(["gate_tier", "reviewer"]);
const FILE_TOOLS = new Set(["write_file", "replace_in_file", "apply_patch", "apply_unified_diff"]);
const COMMAND_TOOLS = new Set(["run_shell"]);
// A whole-file write can be long: show its head, and say how much is left.
const MAX_DIFF_LINES = 200;
// A long unattended turn can leave dozens of rows: show the first ones, offer the rest.
export const MAX_ROWS = 25;

export function unaskedTools(items: Item[]): ToolItem[] {
  return items.filter(
    (it): it is ToolItem =>
      it.kind === "tool" && UNASKED_ORIGINS.has(it.approvalOrigin ?? "") && it.status !== "denied" && it.status !== "…",
  );
}

// A shell result is `{"command", "cwd", "exit_code", "output", …}`; the preview may be cut
// short, so read the field rather than parse the whole thing. The engine now puts the exit
// code on the item itself (`exitCode`); this is the fallback for older transcripts.
export function exitCodeOf(preview: string | undefined): number | null {
  if (!preview) return null;
  const m = /"exit_code"\s*:\s*(-?\d+)/.exec(preview);
  return m ? Number(m[1]) : null;
}

// The error text of a failed call's result (`{"error": …}` or `{"reason": …}`), for the chip.
export function errorOf(preview: string | undefined): string {
  if (!preview) return "";
  try {
    const v = JSON.parse(preview);
    if (v && typeof v === "object") {
      const text = v.reason || v.error;
      if (typeof text === "string") return text;
    }
  } catch {
    const m = /"(?:reason|error)"\s*:\s*"((?:[^"\\]|\\.)*)"/.exec(preview);
    if (m) return m[1];
  }
  return "";
}

export function exitCodeFor(tool: ToolItem): number | null {
  if ("exitCode" in tool) return typeof tool.exitCode === "number" ? tool.exitCode : null;
  return exitCodeOf(tool.preview);
}

export function isRead(tool: ToolItem): boolean {
  return tool.effect === "read";
}

// Changed nothing: the call errored, timed out, or the command exited non-zero.
export function failed(tool: ToolItem): boolean {
  if (tool.status !== "ok") return true;
  if (tool.timedOut) return true;
  const exit = exitCodeFor(tool);
  return exit !== null && exit !== 0;
}

export type Summary = { changes: ToolItem[]; reads: ToolItem[]; count: number; failed: number };

export function summarize(tools: ToolItem[]): Summary {
  const changes = tools.filter((t) => !isRead(t));
  const reads = tools.filter(isRead);
  const failedCount = changes.filter(failed).length;
  return { changes, reads, count: changes.length - failedCount, failed: failedCount };
}

type DiffLine = { kind: "add" | "del" | "ctx" | "meta"; text: string };

function lines(text: unknown): string[] {
  const s = typeof text === "string" ? text : "";
  if (!s) return [];
  return s.replace(/\r\n/g, "\n").replace(/\n$/, "").split("\n");
}

export function diffLines(name: string, args: any): DiffLine[] {
  const a = args && typeof args === "object" ? args : {};
  switch (name) {
    case "write_file":
      return lines(a.content).map((text) => ({ kind: "add", text }));
    case "replace_in_file":
      return [
        ...lines(a.old).map((text) => ({ kind: "del" as const, text })),
        ...lines(a.new).map((text) => ({ kind: "add" as const, text })),
      ];
    case "apply_patch":
    case "apply_unified_diff":
      return lines(name === "apply_patch" ? a.patch : a.diff).map((raw) => {
        if (raw.startsWith("***") || raw.startsWith("+++") || raw.startsWith("---") || raw.startsWith("@@"))
          return { kind: "meta", text: raw };
        if (raw.startsWith("+")) return { kind: "add", text: raw.slice(1) };
        if (raw.startsWith("-")) return { kind: "del", text: raw.slice(1) };
        return { kind: "ctx", text: raw.startsWith(" ") ? raw.slice(1) : raw };
      });
    default:
      return [];
  }
}

function Diff({ tool }: { tool: ToolItem }) {
  const { t } = useTranslation();
  const all = diffLines(tool.name, tool.args);
  if (!all.length) return null;
  const shown = all.slice(0, MAX_DIFF_LINES);
  return (
    <pre
      className="ml-5 mr-2 my-1 px-2.5 py-1.5 rounded-lg border border-line bg-paper font-mono text-meta leading-relaxed whitespace-pre-wrap break-words max-h-72 overflow-auto"
      data-testid="unasked-diff"
    >
      {shown.map((l, i) =>
        l.kind === "add" ? (
          <div className="diff-add text-ok" key={i}>{"+" + l.text}</div>
        ) : l.kind === "del" ? (
          <div className="diff-del text-danger" key={i}>{"-" + l.text}</div>
        ) : l.kind === "meta" ? (
          <div className="diff-meta text-faint" key={i}>{l.text}</div>
        ) : (
          <div className="diff-ctx text-muted" key={i}>{" " + l.text}</div>
        ),
      )}
      {all.length > shown.length && (
        <div className="text-faint">{t("transcript.unasked.more_lines", { count: all.length - shown.length })}</div>
      )}
    </pre>
  );
}

export type PatchChange = { verb: "created" | "edited" | "deleted" | "moved"; path: string; to?: string };

// What a patch does to each file, from its headers, so the summary names the files instead
// of "Edited files". Codex-style patches (`*** Add/Update/Delete File:`, `*** Move to:`) and
// unified diffs (`--- a/x` / `+++ b/x`, with /dev/null for a created or deleted file).
export function patchSummary(name: string, args: any): PatchChange[] {
  const a = args && typeof args === "object" ? args : {};
  const changes: PatchChange[] = [];
  if (name === "apply_patch") {
    for (const raw of lines(a.patch)) {
      const m = /^\*\*\* (Add|Update|Delete) File: (.+)$/.exec(raw.trim());
      if (m) {
        const verb = m[1] === "Add" ? "created" : m[1] === "Delete" ? "deleted" : "edited";
        changes.push({ verb, path: m[2].trim() });
        continue;
      }
      const move = /^\*\*\* Move to: (.+)$/.exec(raw.trim());
      const last = changes[changes.length - 1];
      if (move && last && last.verb === "edited") {
        last.verb = "moved";
        last.to = move[1].trim();
      }
    }
  } else if (name === "apply_unified_diff") {
    const strip = (p: string) => p.trim().replace(/^[ab]\//, "");
    let from = "";
    for (const raw of lines(a.diff)) {
      if (raw.startsWith("--- ")) from = raw.slice(4).trim();
      else if (raw.startsWith("+++ ")) {
        const to = raw.slice(4).trim();
        if (to === "/dev/null") changes.push({ verb: "deleted", path: strip(from) });
        else if (from === "/dev/null") changes.push({ verb: "created", path: strip(to) });
        else changes.push({ verb: "edited", path: strip(to) });
        from = "";
      }
    }
  }
  return changes.filter((c) => c.path);
}

// Who let it through: the tier by name, else the reviewer.
function OriginChip({ tool }: { tool: ToolItem }) {
  const { t } = useTranslation();
  const tier = tool.approvalOrigin === "gate_tier";
  return (
    <span
      className="text-label text-faint shrink-0"
      data-testid="unasked-origin"
      title={tier ? t("transcript.approval.gate_tier_title") : tool.approvalNote || t("transcript.approval.reviewer_allowed_title")}
    >
      {tier ? t("transcript.unasked.by_tier") : t("transcript.unasked.by_reviewer")}
    </span>
  );
}

function AuditLink({ tool, onOpenAudit }: { tool: ToolItem; onOpenAudit?: (callId: string) => void }) {
  const { t } = useTranslation();
  if (!onOpenAudit || !tool.callId) return null;
  return (
    <button
      className="ml-auto shrink-0 text-label text-faint hover:text-muted cursor-pointer"
      data-testid="unasked-audit"
      onClick={() => onOpenAudit(tool.callId!)}
    >
      {t("transcript.unasked.audit")}
    </button>
  );
}

// A row: one tool call, or every edit of one file in this turn (stacked diffs).
function UnaskedRow({ tools, onOpenAudit }: { tools: ToolItem[]; onOpenAudit?: (callId: string) => void }) {
  const { t } = useTranslation();
  const tool = tools[0];
  const line = humanizeTool(tool.name, tool.args);
  const path = typeof tool.args?.path === "string" ? tool.args.path : "";
  const isCommand = COMMAND_TOOLS.has(tool.name);
  const exit = isCommand && !tool.background ? exitCodeFor(tool) : null;
  const anyFailed = tools.some(failed);
  const errorText = tool.status !== "ok" ? errorOf(tool.preview) : "";
  const patched = patchSummary(tool.name, tool.args);
  const dot = tool.background ? "text-faint" : anyFailed ? "text-danger" : "text-ok";
  return (
    <div data-testid="unasked-item" className="py-0.5">
      <div className="flex items-baseline gap-2 px-2">
        <span className={"w-3.5 text-center text-label shrink-0 " + dot}>●</span>
        <span className="min-w-0 text-ui leading-relaxed" data-testid="unasked-item-line">
          {isCommand ? (
            <code className="font-mono text-meta text-ink break-all">{String(tool.args?.command ?? "")}</code>
          ) : patched.length ? (
            patched.map((change, i) => (
              <span key={i}>
                {i > 0 && <span className="text-faint"> · </span>}
                <span className="text-muted">{t(`transcript.unasked.${change.verb}`)} </span>
                <span className="font-mono text-meta text-ink break-all">{change.path}</span>
                {change.to && (
                  <>
                    <span className="text-muted"> {t("transcript.unasked.moved_to")} </span>
                    <span className="font-mono text-meta text-ink break-all">{change.to}</span>
                  </>
                )}
              </span>
            ))
          ) : path ? (
            <>
              <span className="text-muted">{tools.length > 1 ? t("transcript.unasked.edited") + " " : line.pre}</span>
              <span className="font-mono text-meta text-ink break-all">{path}</span>
              {tools.length > 1 && (
                <span className="text-faint" data-testid="unasked-edit-count">
                  {" · " + t("transcript.unasked.edits", { count: tools.length })}
                </span>
              )}
            </>
          ) : (
            <>
              <span className="text-muted">{line.pre}</span>
              {line.obj && <span className="text-ink">{line.obj}</span>}
              {line.post && <span className="text-muted">{line.post}</span>}
            </>
          )}
        </span>
        {tool.background ? (
          <span className="text-label text-faint shrink-0" data-testid="unasked-background">
            {t("transcript.unasked.background")}
          </span>
        ) : tool.timedOut ? (
          <span className="text-label text-danger shrink-0" data-testid="unasked-exit">
            {t("transcript.unasked.timed_out")}
          </span>
        ) : exit !== null ? (
          <span className={"text-label shrink-0 " + (exit === 0 ? "text-ok" : "text-danger")} data-testid="unasked-exit">
            {t("transcript.unasked.exit", { code: exit })}
          </span>
        ) : (
          tool.status !== "ok" && (
            <span className="text-label text-danger shrink-0 truncate max-w-[40%]" data-testid="unasked-failed" title={errorText}>
              {t("transcript.unasked.failed")}
              {errorText ? " · " + errorText : ""}
            </span>
          )
        )}
        <OriginChip tool={tool} />
        {tools.length === 1 && <AuditLink tool={tool} onOpenAudit={onOpenAudit} />}
      </div>
      {tools.map((edit, i) => (
        <div key={edit.id || i}>
          {tools.length > 1 && (
            <div className="flex items-baseline gap-2 px-2 ml-5 text-label text-faint">
              <span>{i + 1}.</span>
              {failed(edit) && <span className="text-danger">{t("transcript.unasked.failed")}</span>}
              <AuditLink tool={edit} onOpenAudit={onOpenAudit} />
            </div>
          )}
          <Diff tool={edit} />
        </div>
      ))}
    </div>
  );
}

// Rows in display order: files (edits of one file together), commands, other actions.
export function rowsOf(changes: ToolItem[]): Array<{ group: "files" | "commands" | "other"; tools: ToolItem[] }> {
  const rows: Array<{ group: "files" | "commands" | "other"; tools: ToolItem[] }> = [];
  const byPath = new Map<string, ToolItem[]>();
  for (const tool of changes.filter((x) => FILE_TOOLS.has(x.name))) {
    const path = typeof tool.args?.path === "string" ? tool.args.path : "";
    if (!path) {
      rows.push({ group: "files", tools: [tool] });
      continue;
    }
    const bucket = byPath.get(path);
    if (bucket) bucket.push(tool);
    else {
      const fresh = [tool];
      byPath.set(path, fresh);
      rows.push({ group: "files", tools: fresh });
    }
  }
  for (const tool of changes.filter((x) => COMMAND_TOOLS.has(x.name))) rows.push({ group: "commands", tools: [tool] });
  for (const tool of changes.filter((x) => !FILE_TOOLS.has(x.name) && !COMMAND_TOOLS.has(x.name)))
    rows.push({ group: "other", tools: [tool] });
  return rows;
}

export function UnaskedSummary({ tools, onOpenAudit }: { tools: ToolItem[]; onOpenAudit?: (callId: string) => void }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [showAll, setShowAll] = useState(false);
  const [showReads, setShowReads] = useState(false);
  const summary = summarize(tools);
  // Only reads, and nothing attempted: nothing changed, so nothing to say.
  if (!summary.changes.length) return null;
  const rows = rowsOf(summary.changes);
  const shown = showAll ? rows : rows.slice(0, MAX_ROWS);
  const groups = (["files", "commands", "other"] as const)
    .map((key) => ({ key, rows: shown.filter((r) => r.group === key) }))
    .filter((g) => g.rows.length);
  const head =
    summary.count > 0
      ? t("transcript.unasked.summary", { count: summary.count })
      : t("transcript.unasked.summary_none");
  return (
    <details className="stepgroup unasked-summary" open={open} data-testid="unasked-summary">
      <summary
        className="stepgroup-head transcript-disclosure cursor-pointer select-none text-ui text-faint hover:text-muted"
        data-testid="unasked-summary-head"
        onClick={(e) => {
          e.preventDefault(); // drive open/closed from state, not the native toggle
          setOpen(!open);
        }}
      >
        <Icon name="chevronDown" size={12} />
        <span>{head}</span>
        {summary.failed > 0 && (
          <span className="text-faint" data-testid="unasked-failed-count">
            {" · " + t("transcript.unasked.failed_count", { count: summary.failed })}
          </span>
        )}
        {summary.reads.length > 0 && (
          <span className="text-faint" data-testid="unasked-read-count">
            {" · " + t("transcript.unasked.read_count", { count: summary.reads.length })}
          </span>
        )}
      </summary>
      {open && (
        <div className="ml-1.5 mt-1 pl-2 border-l-2 border-line flex flex-col gap-1.5" data-testid="unasked-summary-body">
          {groups.map((g) => (
            <div key={g.key} data-testid={`unasked-group-${g.key}`}>
              <div className="px-2 text-label text-faint uppercase tracking-wide">{t(`transcript.unasked.group_${g.key}`)}</div>
              {g.rows.map((row, i) => (
                <UnaskedRow tools={row.tools} onOpenAudit={onOpenAudit} key={row.tools[0].id || i} />
              ))}
            </div>
          ))}
          {rows.length > shown.length && (
            <button
              className="self-start px-2 text-label text-faint hover:text-muted cursor-pointer"
              data-testid="unasked-show-all"
              onClick={() => setShowAll(true)}
            >
              {t("transcript.unasked.show_all", { count: rows.length })}
            </button>
          )}
          {summary.reads.length > 0 && (
            <div data-testid="unasked-reads">
              <button
                className="px-2 text-label text-faint hover:text-muted cursor-pointer"
                data-testid="unasked-reads-toggle"
                onClick={() => setShowReads((v) => !v)}
              >
                {t("transcript.unasked.read_tail", { count: summary.reads.length })}
              </button>
              {showReads &&
                summary.reads.map((tool, i) => (
                  <div className="flex items-baseline gap-2 px-2 ml-3 text-meta" key={tool.id || i} data-testid="unasked-read-item">
                    {COMMAND_TOOLS.has(tool.name) ? (
                      <code className="font-mono text-meta text-muted break-all">{String(tool.args?.command ?? "")}</code>
                    ) : (
                      <span className="text-muted">
                        {humanizeTool(tool.name, tool.args).pre}
                        {humanizeTool(tool.name, tool.args).obj}
                      </span>
                    )}
                    <AuditLink tool={tool} onOpenAudit={onOpenAudit} />
                  </div>
                ))}
            </div>
          )}
        </div>
      )}
    </details>
  );
}
