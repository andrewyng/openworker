import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { getI18n, useTranslation } from "react-i18next";
import type { Attachment, SessionUsage } from "../types";
import { isPdfFile, readFile } from "../attach";
import { ProjectBindMenu } from "./ProjectBindMenu";
import { getSettings, inspectPdf, sessionSkills, type SessionSkillRow } from "../api";
import { formatTokens, totalTokens } from "../usage";
import { Dropdown, type Option } from "./Dropdown";
import { Icon } from "./Icon";
import { Toggle } from "./Toggle";
import {
  cancelDictation,
  getDictationLevel,
  getDictationStatus,
  isTauri,
  startDictation,
  stopDictation,
  type DictationStatus,
} from "../tauri";

// Plan + Custom hidden for this release (owner ask 2026-07-22): Plan's approval flow isn't
// polished enough to ship, and Custom (config.toml auto-allow rules) is a power-user mode
// with no in-app explanation. The server still honors both — a session already in one of
// those modes keeps working; the picker just doesn't offer them.
// "auto" is the legacy wire value for Bypass approvals (server: Mode.BYPASS_APPROVALS) —
// kept so saved sessions and configs keep working. Auto-Approve ("auto-approve") is the
// reviewer mode (spec: reviewed-auto-mode.md); it appears only when the server says the
// feature flag is on, wired in the settings pass — until then the picker omits it.
// `caution` prefixes the label with a warning triangle; `gated` hides the entry unless the
// server's auto_approve flag is on. Picker-local extensions of Dropdown's Option.
type ModeOption = Option & { caution?: boolean; gated?: boolean };

// "auto" is the legacy wire value for Bypass approvals (server: Mode.BYPASS_APPROVALS).
// Auto-approve is `gated`: shown only when getSettings().auto_approve is true (the feature
// flag, off by default).
// Labels/descriptions are i18n keys (resolved at render via t()); kept as keys here so the
// module-level constant stays outside the component without losing translation.
const PERMISSION_OPTIONS: ModeOption[] = [
  { value: "discuss", label: "composer.mode.discuss", description: "composer.mode.discuss_desc" },
  { value: "interactive", label: "composer.mode.interactive", description: "composer.mode.interactive_desc" },
  {
    value: "auto-approve",
    label: "composer.mode.auto_approve",
    description: "composer.mode.auto_approve_desc",
    gated: true,
  },
  {
    value: "auto",
    label: "composer.mode.auto",
    description: "composer.mode.auto_desc",
    caution: true,
  },
];

/** The picker's label for a mode value ("auto-approve" -> "Auto-approve"). Exported so the
 * transcript's mode markers read the same names the user just chose from. */
export function modeLabel(value: string): string {
  const option = PERMISSION_OPTIONS.find((o) => o.value === value);
  return option ? getI18n().t(option.label) : value;
}

// No hardcoded model fallback: until the server supplies the list (a few seconds after a
// cold app boot), the picker renders a disabled "Loading models…" chip. A baked-in list
// goes stale and silently offers ids the backend never confirmed (caught 2026-07-21).

// Drop the provider prefix for display (anthropic:claude-opus-4-8 → claude-opus-4-8); full id on hover.
const shortModel = (m: string) => (m.includes(":") ? m.split(":").slice(1).join(":") : m);

// Identify an attachment by name + payload size so duplicates (e.g. the same file picked twice,
// or a prefill applied twice) collapse to one chip.
const attKey = (a: Attachment) =>
  a.kind === "text"
    ? `t:${a.name}:${a.text?.length ?? 0}`
    : `${a.kind[0]}:${a.name}:${a.data_url?.length ?? 0}`;
const mergeAttachments = (cur: Attachment[], add: Attachment[]): Attachment[] => {
  const seen = new Set(cur.map(attKey));
  return [...cur, ...add.filter((a) => !seen.has(attKey(a)))].slice(0, 8);
};

interface Props {
  mode: string;
  model: string;
  models?: string[];
  modelLabels?: Record<string, string>; // curated display names (raw id when absent)
  // The model is FIXED once the session has history (§17): the picker renders ONLY on a fresh
  // session; after the first turn the fact lives in the topbar subtitle (§22) — no
  // interactive-then-disabled control.
  running: boolean;
  // A proposal gate (team/items) is awaiting the user: the engine is suspended,
  // so `running` is true — but typing must stay possible, because a typed reply
  // IS an answer (decline-with-feedback). Unblocks Send while the gate is up.
  gateOpen?: boolean;
  connected: boolean;
  // False when the default model's provider has no key — the composer shows a "connect a model"
  // banner and routes sends to setup (preserving the draft) instead of dropping them.
  modelReady?: boolean;
  onConnectModel?: () => void;
  onConfigureVoiceInput?: () => void;
  onSend: (text: string, attachments?: Attachment[], skill?: string) => void;
  // Feeds the "/" force-run popup (SKILLS-SPEC §4.1 #3): the popup lists this session's
  // effective skill menu. Absent (e.g. tests without sessions) → the popup never opens.
  sessionId?: string;
  onInterrupt: () => void;
  onModeChange: (mode: string) => void;
  onModelChange: (model: string) => void;
  // When set (Code/Cowork), the Mode menu is shown. The folder/roots + branch controls left the
  // composer for the Session settings drawer (§22) — folder access is standing session config.
  workspace?: string;
  // Unattended / send-approvals-to-Inbox — folded into the Mode menu (§22): "who approves, and
  // when" is one mental model. Absent handler = no toggle (e.g. Chat).
  unattended?: boolean;
  onUnattendedChange?: (on: boolean) => void;
  // The pending-approval card rendered above the input (plan / work-items / team / tool /
  // folder requests). Attended sessions only — Unattended parks the prompt in the Inbox.
  approvalSlot?: ReactNode;
  // UX-044: "View & edit…" in the Project memory submenu routes to the memory panel.
  onOpenMemory?: () => void;
  // Push text + attachments into the composer (e.g. a start-panel task card). The `nonce` makes
  // repeated identical prefills re-apply; the user can still edit before sending.
  prefill?: { text: string; attachments?: Attachment[]; nonce: number };
  // Changes when the active conversation changes; clears any unsent draft.
  resetKey?: string;
  // Surface-specific hint shown in the empty textarea.
  placeholder?: string;
  // Per-session token usage (OPE-42) — absent/empty hides the usage chip entirely
  // (older servers, backends that don't report usage, fresh sessions).
  usage?: SessionUsage;
  // Context-window size (tokens) of the ACTIVE model, from the curated matrix;
  // undefined hides the fill meter (unverified/custom models) but keeps the counts.
  contextWindow?: number;
  // Settings toggle (default off): true shows the fill bar instead of the session total.
  contextBar?: boolean;
  // §8.4 breaker tripped this turn: the mode chip says so quietly until the turn ends
  // or an ask_user answer resets the streak.
  reviewerPaused?: boolean;
}

export function Composer(props: Props) {
  const { t } = useTranslation();
  const [text, setText] = useState("");
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  // "/" force-run (SKILLS-SPEC §4.1 #3). The popup derives from the draft: it is open while
  // the text is a bare "/query" (no whitespace yet) and no skill is picked. Selecting a row
  // inserts "/name " INLINE in the box (Claude-Code style — the slash text IS the state);
  // the user keeps typing after it, and on send the prefix is stripped while the skill name
  // rides the user_message as its own field. Editing the prefix away un-picks the skill.
  const [pendingSkill, setPendingSkill] = useState<SessionSkillRow | null>(null);
  const [slashSkills, setSlashSkills] = useState<SessionSkillRow[] | null>(null);
  const [slashIndex, setSlashIndex] = useState(0);
  const prefixIntact =
    pendingSkill !== null &&
    (text === `/${pendingSkill.name}` || text.startsWith(`/${pendingSkill.name} `));
  useEffect(() => {
    if (pendingSkill && !prefixIntact) setPendingSkill(null);
  }, [pendingSkill, prefixIntact]);
  const slashQuery =
    !prefixIntact && props.sessionId && text.startsWith("/") && !/\s/.test(text.slice(1))
      ? text.slice(1).toLowerCase()
      : null;
  const slashMatches = (slashSkills ?? []).filter((s) =>
    s.name.toLowerCase().includes(slashQuery ?? ""),
  );
  useEffect(() => {
    // Fetch on each popup open (fresh menu); drop when closed.
    if (slashQuery === null) {
      setSlashSkills(null);
      setSlashIndex(0);
      return;
    }
    if (slashSkills === null && props.sessionId) {
      sessionSkills(props.sessionId, props.workspace)
        .then((all) => setSlashSkills(all.filter((s) => s.enabled)))
        .catch(() => setSlashSkills([]));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [slashQuery === null]);
  const pickSkill = (s: SessionSkillRow) => {
    setPendingSkill(s);
    setText(`/${s.name} `);
    textareaRef.current?.focus();
  };
  const [dragging, setDragging] = useState(false);
  const [attachMenuOpen, setAttachMenuOpen] = useState(false);
  // UX-044: which "This session" submenu is open (bindings live server-side).
  const [bindMenu, setBindMenu] = useState<"memory" | "board" | null>(null);
  // Bindings need a session and a workspace surface (Chat has neither).
  const sessionRows = Boolean(props.sessionId && props.workspace !== undefined);
  const bindRow = (icon: "book" | "table", label: string, kind: "memory" | "board") => (
    <button
      className={
        "w-full flex items-center gap-2.5 px-3 py-1.5 text-[13px] text-left hover:bg-paper" +
        (bindMenu === kind ? " bg-paper" : "")
      }
      onClick={() => setBindMenu(bindMenu === kind ? null : kind)}
    >
      <Icon name={icon} size={15} className="shrink-0 text-muted" />
      <span className="flex-1">{label}</span>
      <Icon name="chevronRight" size={12} className="shrink-0 text-faint" />
    </button>
  );
  const [dictation, setDictation] = useState<DictationStatus | null>(null);
  const [dictationBusy, setDictationBusy] = useState<string | null>(null);
  const [dictationError, setDictationError] = useState<string | null>(null);
  const [recordingSeconds, setRecordingSeconds] = useState(0);
  const [attachNotice, setAttachNotice] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const noticeTimer = useRef<number | null>(null);

  // Rejected-attachment notice: visible ~8s, then clears (or on ✕).
  const showAttachNotice = (message: string) => {
    setAttachNotice(message);
    if (noticeTimer.current) window.clearTimeout(noticeTimer.current);
    noticeTimer.current = window.setTimeout(() => setAttachNotice(null), 8000);
  };

  useLayoutEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    // The cap must include the vertical PADDING: scrollHeight does, so a
    // padding-blind cap left the box ~20px short and scrolled the top padding
    // (plus the first line) out of the clip while typing (OPE-106). Six lines —
    // team briefs outgrew four.
    const cs = getComputedStyle(el);
    const pad =
      (parseFloat(cs.paddingTop) || 0) + (parseFloat(cs.paddingBottom) || 0);
    const max = (parseFloat(cs.lineHeight) || 22) * 6 + pad;
    const next = Math.min(el.scrollHeight, max);
    el.style.height = `${Math.max(next, 24)}px`;
    el.style.overflowY = el.scrollHeight > max ? "auto" : "hidden";
  }, [text]);

  // Clear the draft when the conversation changes, so a half-typed message / picked file doesn't
  // bleed from one session into another. Declared BEFORE the prefill effect: when both fire in
  // the same render (the Skills doorway starts a new session AND prefills it), effects run in
  // declaration order — clear first, then the prefill lands on the fresh session.
  useEffect(() => {
    setText("");
    setAttachments([]);
    setPendingSkill(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.resetKey]);

  // Apply a prefill (text + attachments) pushed from outside, then focus the composer. Applied at
  // most once per nonce (a ref guards against StrictMode/re-render double-fires), and attachments
  // are de-duplicated so the same file never lands twice.
  const appliedNonce = useRef<number>(-1);
  useEffect(() => {
    const p = props.prefill;
    if (!p || p.nonce === appliedNonce.current) return;
    appliedNonce.current = p.nonce;
    setText(p.text);
    if (p.attachments?.length) setAttachments((cur) => mergeAttachments(cur, p.attachments!));
    textareaRef.current?.focus();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.prefill?.nonce]);

  // Dictation is intentionally native-only: the browser/dev build remains a local server client
  // and never turns on the browser microphone or ships audio anywhere.
  useEffect(() => {
    if (!isTauri()) return;
    const refresh = (event?: Event) => {
      const supplied = (event as CustomEvent<DictationStatus> | undefined)?.detail;
      if (supplied) {
        setDictation(supplied);
        return;
      }
      void getDictationStatus().then((status) => status && setDictation(status));
    };
    refresh();
    window.addEventListener("coworker:voice-input-changed", refresh);
    return () => window.removeEventListener("coworker:voice-input-changed", refresh);
  }, []);

  useEffect(() => {
    if (!dictation?.recording) {
      setRecordingSeconds(0);
      return;
    }
    const started = Date.now();
    const timer = window.setInterval(() => {
      setRecordingSeconds(Math.floor((Date.now() - started) / 1000));
    }, 250);
    return () => window.clearInterval(timer);
  }, [dictation?.recording]);

  // Live waveform: poll mic loudness at ~10Hz while recording; the bars scroll left so the
  // trace reads as a real input meter (owner catch on DMG #28 — the first cut's bars were
  // decorative constants and read as fake).
  const [levels, setLevels] = useState<number[]>([]);
  useEffect(() => {
    if (!dictation?.recording) {
      setLevels([]);
      return;
    }
    const timer = window.setInterval(() => {
      getDictationLevel().then((level) => {
        if (typeof level === "number") setLevels((cur) => [...cur.slice(-13), level]);
      });
    }, 100);
    return () => window.clearInterval(timer);
  }, [dictation?.recording]);

  useEffect(() => {
    if (!dictation?.recording) return;
    const cancelOnEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      void cancelDictation()
        .catch(() => undefined)
        .finally(() => {
          void getDictationStatus().then((status) => status && setDictation(status));
        });
    };
    window.addEventListener("keydown", cancelOnEscape);
    return () => window.removeEventListener("keydown", cancelOnEscape);
  }, [dictation?.recording]);

  const voiceReady = !!dictation?.supported && !!dictation?.model_verified && !!dictation?.test_passed;
  const recordingTime = `${Math.floor(recordingSeconds / 60)}:${String(recordingSeconds % 60).padStart(2, "0")}`;

  // Attach-time PDF thresholds (Settings → Token savings): a PDF over the user's page or
  // size limit is REJECTED with a visible notice — never attached, never silently dropped.
  // The rationale is token cost: a big PDF re-rides every turn of the conversation.
  const addFiles = async (files: FileList | File[]) => {
    const list = Array.from(files);
    let maxPages = 20;
    let maxMb = 10;
    if (list.some(isPdfFile)) {
      try {
        const s = await getSettings();
        if (s.pdf_max_pages) maxPages = s.pdf_max_pages;
        if (s.pdf_max_mb) maxMb = s.pdf_max_mb;
      } catch {
        /* offline settings fetch — fall back to defaults */
      }
    }
    const accepted: File[] = [];
    for (const file of list) {
      if (isPdfFile(file) && file.size > maxMb * 1024 * 1024) {
        showAttachNotice(
          t("composer.pdf_too_big", { name: file.name, mb: (file.size / 1024 / 1024).toFixed(1), limit: maxMb }),
        );
        continue;
      }
      accepted.push(file);
    }
    const read = (await Promise.all(accepted.map(readFile))).filter(Boolean) as Attachment[];
    const next: Attachment[] = [];
    for (const a of read) {
      if (a.kind === "pdf" && a.data_url) {
        const info = await inspectPdf(a.data_url).catch(() => null);
        if (info?.ok && (info.pages ?? 0) > maxPages) {
          showAttachNotice(
            t("composer.pdf_too_many_pages", { name: a.name, pages: info.pages, limit: maxPages }),
          );
          continue;
        }
        if (info && !info.ok) {
          showAttachNotice(t("composer.pdf_unreadable", { name: a.name, error: info.error || t("composer.pdf_could_not_read") }));
          continue;
        }
      }
      next.push(a);
    }
    if (next.length) setAttachments((a) => mergeAttachments(a, next));
  };

  // The "+" menu offers typed shortcuts; each just narrows the OS picker's filter.
  const pickFiles = (accept: string) => {
    setAttachMenuOpen(false);
    if (fileInput.current) {
      fileInput.current.accept = accept;
      fileInput.current.click();
    }
  };

  const needsModel = props.modelReady === false;

  const submit = () => {
    // While the "/" popup is open the draft is a query, not a message — never send it.
    if (slashQuery !== null) return;
    // The visible "/name " prefix is UI state, not message text — strip it for the send;
    // the skill rides as its own field. (Named `body`, not `t`, so it can't shadow i18n's t.)
    const skill = prefixIntact ? pendingSkill!.name : undefined;
    const body = (skill ? text.slice(skill.length + 1) : text).trim();
    if (
      (!body && attachments.length === 0 && !skill) ||
      (props.running && !props.gateOpen) ||
      dictation?.recording ||
      dictationBusy
    )
      return;
    // No model connected: keep the draft (don't drop it) and send the user to setup instead.
    if (needsModel) {
      props.onConnectModel?.();
      return;
    }
    props.onSend(body, attachments, skill);
    setText("");
    setAttachments([]);
    setPendingSkill(null);
  };

  const onKey = (e: React.KeyboardEvent) => {
    if (slashQuery !== null) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setSlashIndex((i) => Math.min(i + 1, Math.max(slashMatches.length - 1, 0)));
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setSlashIndex((i) => Math.max(i - 1, 0));
        return;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        setText("");
        return;
      }
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        const chosen = slashMatches[slashIndex];
        if (chosen) pickSkill(chosen);
        return;
      }
    }
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  };

  const onPaste = (e: React.ClipboardEvent) => {
    const imgs = Array.from(e.clipboardData.items)
      .filter((it) => it.kind === "file" && it.type.startsWith("image/"))
      .map((it) => it.getAsFile())
      .filter(Boolean) as File[];
    if (imgs.length) {
      e.preventDefault();
      addFiles(imgs);
    }
  };

  const toggleDictation = async () => {
    if (!isTauri() || dictationBusy) return;
    setDictationError(null);
    try {
      if (dictation?.recording) {
        setDictationBusy(t("composer.starting_transcribe"));
        const transcript = await stopDictation();
        if (transcript === null) throw new Error(t("composer.err_transcribe"));
        if (transcript.trim()) {
          setText((draft) => (draft.trim() ? `${draft.trimEnd()} ${transcript.trim()}` : transcript.trim()));
        }
        setDictation(await getDictationStatus());
        textareaRef.current?.focus();
        return;
      }

      const status = dictation || (await getDictationStatus());
      if (!status) throw new Error(t("composer.err_dictation_unavailable"));
      if (!status.supported || !status.model_verified || !status.test_passed) {
        props.onConfigureVoiceInput?.();
        return;
      }
      setDictationBusy(t("composer.starting_mic"));
      const recording = await startDictation();
      if (!recording?.recording) throw new Error(t("composer.err_mic_start"));
      setDictation(recording);
    } catch (error) {
      setDictationError(error instanceof Error ? error.message : t("composer.err_dictation_unavailable"));
      const status = await getDictationStatus();
      if (status) setDictation(status);
    } finally {
      setDictationBusy(null);
    }
  };

  const modelsLoaded = !!(props.models && props.models.length);
  const modelOptions: Option[] = Array.from(
    new Set([props.model, ...(props.models || [])]),
  ).map((m) => ({
    value: m,
    label: props.modelLabels?.[m] || shortModel(m),
  }));

  const iconBtn =
    "w-7 h-7 grid place-items-center rounded-md text-muted hover:text-ink hover:bg-paper shrink-0";

  // The send button is accent only when there's something to send — subtle grey otherwise, so the
  // composer isn't carrying a constant blue dot.
  // A pinned /skill is sendable content on its own (tester catch 2026-07-26: the arrow
  // stayed grey after picking a skill, reading as "stuck").
  const hasContent = text.trim().length > 0 || attachments.length > 0 || !!pendingSkill;

  return (
    <div className="composer-wrap px-6 pb-5 pt-4">
      {props.approvalSlot}

      {dictationError && (
        <div className="max-w-3xl mx-auto mb-2 px-1 text-[12px] text-red-600" role="alert">
          {dictationError}
        </div>
      )}

      {/* Rejected-attachment notice (PDF over the user's Token-savings thresholds). */}
      {attachNotice && (
        <div
          data-testid="attach-notice"
          className="max-w-3xl mx-auto mb-1.5 flex items-center gap-2 rounded-lg border border-warnInk/30 bg-warnSoft px-3 py-1.5 text-[13px] text-warnInk"
        >
          <span className="flex-1">{attachNotice}</span>
          <button
            className="shrink-0 opacity-60 hover:opacity-100"
            onClick={() => setAttachNotice(null)}
            title={t("common.dismiss")}
          >
            ✕
          </button>
        </div>
      )}

      {/* Attachments preview — a strip ABOVE the input box (mock/Claude-style). */}
      {attachments.length > 0 && (
        <div className="max-w-3xl mx-auto mb-1.5 flex flex-wrap gap-2">
          {attachments.map((a, i) => (
            <AttachChip key={i} a={a} onRemove={() => setAttachments((all) => all.filter((_, j) => j !== i))} />
          ))}
        </div>
      )}

      <div
        className={
          "composer max-w-3xl mx-auto rounded-2xl border border-line bg-panel shadow-sm" +
          (dragging ? " dragging" : "")
        }
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          if (e.dataTransfer.files.length) addFiles(e.dataTransfer.files);
        }}
      >
        {/* "/" force-run popup — in-flow above the textarea; rows are the session's
            effective menu only (muted/disabled skills never appear). */}
        {slashQuery !== null && (
          <div className="px-2 pt-2" data-testid="skill-popup" role="listbox" aria-label="Skills">
            {slashSkills === null ? (
              <div className="px-2 py-1.5 text-[12px] text-faint">Loading skills…</div>
            ) : slashMatches.length === 0 ? (
              <div className="px-2 py-1.5 text-[12px] text-faint">No matching skills.</div>
            ) : (
              slashMatches.map((s, i) => (
                <button
                  key={s.name}
                  role="option"
                  aria-selected={i === slashIndex}
                  className={
                    "w-full text-left flex items-center gap-2 px-2 py-1.5 rounded-lg " +
                    (i === slashIndex ? "bg-paper" : "hover:bg-paper")
                  }
                  onMouseEnter={() => setSlashIndex(i)}
                  onClick={() => pickSkill(s)}
                >
                  <span className="text-[13px] font-medium text-accent shrink-0">/{s.name}</span>
                  <span className="text-[12px] text-faint truncate flex-1">{s.description}</span>
                  <span className="text-[11px] px-1.5 py-0.5 rounded-full border border-line text-faint shrink-0">
                    {s.scope}
                  </span>
                </button>
              ))
            )}
          </div>
        )}
        <textarea
          ref={textareaRef}
          className="w-full block px-3.5 pt-3.5 pb-1.5 text-[14px]"
          placeholder={
            props.gateOpen
              ? t("composer.placeholder_gate")
              : props.placeholder || t("composer.placeholder")
          }
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKey}
          onPaste={onPaste}
          rows={1}
        />

        {/* Three-control row (§22): + attach · Mode ⌄ …(right)… model (fresh only) · send */}
        <div className="px-2.5 pb-2.5 pt-1 flex items-center gap-1.5">
          {/* + attach menu */}
          <div className="relative">
            <button
              className={iconBtn + (attachMenuOpen ? " bg-paper text-ink" : "")}
              title={t("composer.attach")}
              aria-label={t("composer.attach")}
              onClick={() => setAttachMenuOpen((v) => !v)}
            >
              <Icon name="plus" size={17} />
            </button>
            {attachMenuOpen && (
              <>
                <div
                  className="fixed inset-0 z-30"
                  onClick={() => {
                    setAttachMenuOpen(false);
                    setBindMenu(null);
                  }}
                />
                <div className="absolute z-40 bottom-full mb-1 left-0 min-w-[200px] rounded-xl border border-line bg-panel shadow-2xl py-1.5">
                  {sessionRows && (
                    <div className="px-3 pt-1 pb-0.5 text-[10.5px] font-semibold tracking-wide uppercase text-faint">
                      {t("composer.attach_this_message")}
                    </div>
                  )}
                  {attachItem("image", t("composer.attach_image"), () => pickFiles("image/*"))}
                  {attachItem("file", "PDF", () => pickFiles("application/pdf,.pdf"))}
                  {attachItem(
                    "fileCode",
                    t("composer.attach_other"),
                    () => pickFiles("text/*,.md,.csv,.json,.yaml,.yml,.log,.py,.ts,.tsx,.js,.rs,.go,.toml"),
                  )}
                  {sessionRows && (
                    <>
                      <div className="my-1 border-t border-line" />
                      <div className="px-3 pt-0.5 pb-0.5 text-[10.5px] font-semibold tracking-wide uppercase text-faint">
                        {t("composer.attach_this_session")}
                      </div>
                      {bindRow("book", t("composer.bind_memory"), "memory")}
                      {bindRow("table", t("composer.bind_board"), "board")}
                    </>
                  )}
                </div>
                {bindMenu && props.sessionId && (
                  <ProjectBindMenu
                    sessionId={props.sessionId}
                    kind={bindMenu}
                    onClose={() => {
                      setBindMenu(null);
                      setAttachMenuOpen(false);
                    }}
                    onOpenMemory={props.onOpenMemory}
                  />
                )}
              </>
            )}
          </div>
          <input
            ref={fileInput}
            type="file"
            multiple
            style={{ display: "none" }}
            onChange={(e) => {
              if (e.target.files) addFiles(e.target.files);
              e.target.value = "";
            }}
          />

          {/* Listening replaces the quiet middle controls with a LIVE waveform (mic RMS,
              polled ~10Hz, scrolling left) + elapsed time (§37). */}
          {dictation?.recording ? (
            <div className="voice-wave-row flex-1 flex items-center gap-2 ml-1" aria-hidden="true">
              <span className="voice-wave-line" />
              <span className="voice-wave-bars">
                {Array.from({ length: 14 }, (_, index) => {
                  const level = levels[levels.length - 14 + index] ?? 0;
                  return <i key={index} style={{ height: Math.round(4 + level * 24) }} />;
                })}
              </span>
              <span className="text-[12px] text-muted tabular-nums">{recordingTime}</span>
            </div>
          ) : props.workspace !== undefined ? (
            <ModeMenu
              reviewerPaused={props.reviewerPaused}
              mode={props.mode}
              onModeChange={props.onModeChange}
              unattended={props.unattended}
              onUnattendedChange={props.onUnattendedChange}
            />
          ) : null}

          {dictationBusy === t("composer.starting_transcribe") && <span className="text-[12px] text-accent">{dictationBusy}</span>}

          <span className="ml-auto" />

          {/* token usage (OPE-42) — a quiet chip; hidden until the server reports usage.
              Shows the context-window fill bar alone (the session total lives in the
              popover), or the session total when there's no window / the bar is off. */}
          {!dictation?.recording && props.usage && totalTokens(props.usage) > 0 && (
            <UsageChip
              usage={props.usage}
              contextWindow={props.contextWindow}
              contextBar={props.contextBar}
              model={props.model}
              modelLabels={props.modelLabels}
            />
          )}

          {/* model — a quiet chip, now for the session's whole life (§17 rev 2026-07-22:
              mid-session switching shipped, so the picker stays actionable; the topbar
              subtitle still states the current model). */}
          {!dictation?.recording && (needsModel ? (
            <button
              className="pill model-warn chip"
              onClick={() => props.onConnectModel?.()}
              title={t("composer.model.connect")}
              aria-label={t("composer.model.none_aria")}
            >
              <span className="pill-label">{t("composer.model.none")}</span>
              <span className="model-warn-ico" aria-hidden>⚠</span>
            </button>
          ) : modelsLoaded ? (
            <Dropdown value={props.model} options={modelOptions} onChange={props.onModelChange} align="right" />
          ) : (
            <button
              className="pill chip text-faint cursor-default"
              disabled
              data-testid="models-loading"
              title={t("composer.model.loading_title")}
            >
              <span className="pill-label">{t("composer.model.loading")}</span>
            </button>
          ))}

          {/* mic — immediately before send (owner call, DMG #28 walkthrough) */}
          {isTauri() && (
            <button
              className={
                iconBtn +
                (dictation?.recording ? " bg-red-50 text-red-600 hover:bg-red-100" : "") +
                (dictationBusy ? " opacity-60" : "") +
                (!voiceReady && !dictation?.recording ? " opacity-40" : "")
              }
              onClick={() => void toggleDictation()}
              disabled={!!dictationBusy}
              title={
                dictationBusy ||
                (dictation?.recording
                  ? t("composer.voice.stop_transcribe")
                  : voiceReady
                    ? t("composer.voice.start_dictation")
                    : t("composer.voice.configure"))
              }
              aria-label={dictation?.recording ? t("composer.voice.stop_dictation") : voiceReady ? t("composer.voice.start_dictation_btn") : t("composer.voice.configure")}
              aria-disabled={!voiceReady && !dictation?.recording}
            >
              <Icon name={dictation?.recording ? "stop" : "mic"} size={16} />
            </button>
          )}

          {/* send / stop — a pending gate re-opens Send: the reply resolves it */}
          {props.running && !props.gateOpen ? (
            <button className="btn danger" onClick={props.onInterrupt}>
              {t("composer.stop")}
            </button>
          ) : (
            <button
              className={
                "w-7 h-7 rounded-full grid place-items-center shrink-0 transition-colors " +
                (hasContent && props.connected && !dictation?.recording && !dictationBusy
                  ? "bg-accent text-white hover:brightness-105"
                  : "bg-paper border border-line text-faint")
              }
              onClick={submit}
              disabled={!props.connected || !!dictation?.recording || !!dictationBusy}
              title={needsModel ? t("composer.connect_to_send") : undefined}
              aria-label={t("common.send")}
            >
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M12 19V5M5 12l7-7 7 7" />
              </svg>
            </button>
          )}
        </div>
      </div>
      <span className="sr-only" role="status" aria-live="polite">
        {dictation?.recording ? t("composer.listening_sr", { time: recordingTime }) : dictationBusy || ""}
      </span>
    </div>
  );
}

// Token-usage chip + popover (OPE-42). Trigger: a tiny context-fill meter (only when the
// active model's window is known) + the session's total token count. Click → per-model
// breakdown. Tokens only, never dollars (true cost is unknowable client-side — discounted
// pricing, per-provider cache billing).
function UsageChip({
  usage,
  contextWindow,
  contextBar,
  model,
  modelLabels,
}: {
  usage: SessionUsage;
  contextWindow?: number;
  contextBar?: boolean;
  model: string;
  modelLabels?: Record<string, string>;
}) {
  const [open, setOpen] = useState(false);
  const total = totalTokens(usage);
  const pct = contextWindow
    ? Math.min(100, Math.round((usage.context / contextWindow) * 100))
    : null;
  // Settings can hide the bar; without a known window there is nothing to fill either.
  const showBar = pct !== null && contextBar === true;
  // Release hold (owner call 2026-08-24): cumulative session totals need more vetting
  // before they ship — cache-read sums across turns read like a bill. Until then the
  // chip and popover speak context-window only. Flip this to restore the breakdown.
  const SHOW_SESSION_TOTALS = false;
  const labelFor = (id: string) =>
    id === "unknown" ? "Unknown model" : modelLabels?.[id] || shortModel(id);
  // One field per line, session-summed (owner ask 2026-07-28). Values are cumulative
  // across the whole session, never just the last turn; "Input" is the fresh
  // (uncached) share — the cached share sits in the cache rows at its own price.
  const stat = (label: string, value: number) => (
    <div className="flex items-baseline justify-between text-[12px] leading-snug">
      <span className="text-faint">{label}</span>
      <span className="text-ink tabular-nums">{formatTokens(value)}</span>
    </div>
  );
  return (
    <div className="relative">
      <button
        className="inline-flex items-center gap-1.5 px-2 py-1 rounded-lg text-[12px] text-muted hover:text-ink hover:bg-paper shrink-0"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="Token usage"
        title={
          pct !== null
            ? `Context window ${pct}% full — ${formatTokens(usage.context)} of ${formatTokens(contextWindow as number)}`
            : `In context now: ${formatTokens(usage.context)} tokens`
        }
        data-testid="usage-chip"
      >
        {/* The bar is the context-window fill. With totals on release hold, the numeric
            fallback is the in-context size — the one figure we trust — never the
            cumulative session total. */}
        {showBar ? (
          <span className="w-12 h-1.5 rounded-full bg-line overflow-hidden" aria-hidden="true">
            <span
              className="block h-full bg-accent transition-all"
              style={{ width: `${Math.max(pct as number, 4)}%` }}
            />
          </span>
        ) : (
          <span className="tabular-nums">
            {SHOW_SESSION_TOTALS ? formatTokens(total) : formatTokens(usage.context)}
          </span>
        )}
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-30" onClick={() => setOpen(false)} />
          <div
            className="absolute z-40 bottom-full mb-1 right-0 w-[280px] rounded-xl border border-line bg-panel shadow-2xl p-3"
            role="menu"
            data-testid="usage-popover"
          >
            {contextWindow ? (
              <div className="mb-2.5">
                <div className="text-[11px] uppercase tracking-[0.06em] text-faint font-semibold mb-1">
                  Context window
                </div>
                <div className="h-1.5 rounded-full bg-line overflow-hidden">
                  <div
                    className="h-full bg-accent transition-all"
                    style={{ width: `${pct}%` }}
                  />
                </div>
                <div className="mt-1 text-[12px] text-muted tabular-nums">
                  {formatTokens(usage.context)} of {formatTokens(contextWindow)} · {pct}%
                </div>
              </div>
            ) : usage.context > 0 ? (
              <div className="mb-2.5 text-[12px] text-muted tabular-nums">
                In context now: {formatTokens(usage.context)} tokens
              </div>
            ) : null}
            {SHOW_SESSION_TOTALS && (<>
            <div className="text-[11px] uppercase tracking-[0.06em] text-faint font-semibold mb-1">
              Session totals
            </div>
            <div className="flex flex-col gap-1.5">
              {Object.entries(usage.byModel).map(([id, t]) => (
                <div key={id}>
                  <div className="text-[12px] text-ink font-medium truncate" title={id}>
                    {labelFor(id)}
                  </div>
                  {/* Every row is a session sum. With a cache split, the input rows are
                      the three BILLING CLASSES of input (each priced differently) and
                      read as components: uncached + cache reads + cache writes = total.
                      Without one (Ollama, compat vendors), plain "Input" says it all. */}
                  <div className="mt-0.5 flex flex-col gap-0.5">
                    {t.cache_read + t.cache_write > 0 ? (
                      <>
                        {stat("Uncached input", t.input)}
                        {stat("Cache reads", t.cache_read)}
                        {stat("Cache writes", t.cache_write)}
                        {stat("Total input", t.input + t.cache_read + t.cache_write)}
                      </>
                    ) : (
                      stat("Input", t.input)
                    )}
                    {stat("Output", t.output)}
                  </div>
                </div>
              ))}
            </div>
            <div className="mt-2 pt-2 border-t border-line flex items-baseline justify-between text-[12px]">
              <span className="text-faint">Total</span>
              <span className="text-ink tabular-nums">{formatTokens(total)} tokens</span>
            </div>
            </>)}
            {model && !modelLabels?.[model] && contextWindow === undefined && (
              <div className="mt-1 text-[11px] text-faint leading-snug">
                Context meter unavailable for custom models.
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}

// The composer's Mode menu (§22): a quiet "Mode ⌄" chip opening the five permission options with
// the current one marked, plus — when the session supports it — the "Send approvals to Inbox"
// toggle at the bottom (the old standalone InboxControl, folded in).
function ModeMenu({
  mode,
  onModeChange,
  unattended,
  onUnattendedChange,
  reviewerPaused,
}: {
  mode: string;
  onModeChange: (mode: string) => void;
  unattended?: boolean;
  onUnattendedChange?: (on: boolean) => void;
  reviewerPaused?: boolean;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  // The Auto-Approve entry is gated on the server flag. Fetch once on first open; a session
  // already IN auto-approve mode always shows its own entry so the current mode is legible
  // even if the flag was later turned off.
  const [autoApproveEnabled, setAutoApproveEnabled] = useState(false);
  useEffect(() => {
    if (!open) return;
    getSettings()
      .then((s) => setAutoApproveEnabled(s.auto_approve === true))
      .catch(() => {});
  }, [open]);
  const options = PERMISSION_OPTIONS.filter(
    (o) => !o.gated || autoApproveEnabled || o.value === mode,
  );
  const current = PERMISSION_OPTIONS.find((o) => o.value === mode);
  return (
    <div className="relative">
      {/* Borderless, and it names the CHOSEN mode (owner ask 2026-07-11, competitor composer
          comparison): "Ask for approval ⌄" not a generic "Mode ⌄" pill. aria-label stays
          "Mode" so the accessible name is stable across mode changes. */}
      <button
        className="inline-flex items-center gap-1 px-2 py-1 rounded-lg text-[12px] text-muted hover:text-ink hover:bg-paper shrink-0"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={t("composer.mode_label")}
        title={
          `${t("composer.mode_label")}: ${current ? t(current.label) : mode}` +
          (reviewerPaused && mode === "auto-approve" ? " · " + t("composer.reviewer_paused_tip") : "") +
          (unattended ? " · " + t("composer.approvals_to_inbox") : "")
        }
      >
        {current ? t(current.label) : mode}
        {reviewerPaused && mode === "auto-approve" && (
          <span className="text-[11px] text-warnInk" data-testid="mode-paused">· {t("composer.paused")}</span>
        )}
        <Icon name="chevronDown" size={11} className="text-faint" />
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-30" onClick={() => setOpen(false)} />
          <div
            className="absolute z-40 bottom-full mb-1 left-0 w-[260px] rounded-xl border border-line bg-panel shadow-2xl p-1.5"
            role="menu"
            data-testid="mode-menu"
          >
            {options.map((o) => (
              <button
                key={o.value}
                className="w-full flex flex-col items-start px-2.5 py-1.5 rounded-lg text-left hover:bg-paper"
                onClick={() => {
                  onModeChange(o.value);
                  setOpen(false);
                }}
              >
                <span
                  className={
                    "flex items-center text-[13px] " +
                    (o.value === mode ? "font-medium text-accent" : "text-ink")
                  }
                >
                  {o.caution && (
                    <Icon name="warning" size={13} className="mr-1.5 shrink-0 text-warnInk" />
                  )}
                  {t(o.label)}
                  {o.value === mode && <span className="ml-1.5">✓</span>}
                </span>
                <span className="text-[11px] text-faint leading-snug">{t(o.description ?? "")}</span>
              </button>
            ))}
            {onUnattendedChange && (
              <>
                <div className="my-1 border-t border-line" />
                <div className="flex items-center gap-2 px-2.5 py-1.5">
                  <span className="flex-1 min-w-0">
                    <span className="block text-[13px] text-ink">{t("composer.approvals_to_inbox")}</span>
                    <span className="block text-[11px] text-faint leading-snug">
                      {t("composer.approvals_to_inbox_help")}
                    </span>
                  </span>
                  <Toggle
                    checked={!!unattended}
                    onChange={onUnattendedChange}
                    title={t("composer.send_approvals_to_inbox")}
                  />
                </div>
              </>
            )}
          </div>
        </>
      )}
    </div>
  );
}

// A row in the "+" attach menu.
function attachItem(icon: "image" | "file" | "fileCode", label: string, onClick: () => void) {
  return (
    <button
      className="w-full flex items-center gap-2.5 px-3 py-1.5 text-[13px] text-left hover:bg-paper"
      onClick={onClick}
    >
      <Icon name={icon} size={15} className="shrink-0 text-muted" /> {label}
    </button>
  );
}

function AttachChip({ a, onRemove }: { a: Attachment; onRemove: () => void }) {
  const { t } = useTranslation();
  return (
    <div className={"attach-chip" + (a.kind === "image" ? " img" : "")}>
      {a.kind === "image" ? (
        <img src={a.data_url} alt={a.name} />
      ) : (
        <>
          <Icon name="file" size={13} />
          <span className="attach-name">{a.name}</span>
        </>
      )}
      <button className="attach-x" onClick={onRemove} title={t("common.remove")}>
        ✕
      </button>
    </div>
  );
}
