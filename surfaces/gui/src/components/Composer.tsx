import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import type { Attachment, UserMessageMeta } from "../types";
import { isPdfFile, readFile } from "../attach";
import { getSettings, inspectPdf } from "../api";
import { Dropdown, type Option } from "./Dropdown";
import { Icon } from "./Icon";
import { Toggle } from "./Toggle";
import {
  cancelVoiceCapture,
  getVoiceLevel,
  getVoiceRuntime,
  getVoiceStatus,
  startVoiceCapture,
  stopVoiceCapture,
  VOICE_INPUT_ERROR_EVENT,
  type VoiceRuntime,
  type VoiceStatus,
} from "../voice";

// Plan + Custom hidden for this release (owner ask 2026-07-22): Plan's approval flow isn't
// polished enough to ship, and Custom (config.toml auto-allow rules) is a power-user mode
// with no in-app explanation. The server still honors both — a session already in one of
// those modes keeps working; the picker just doesn't offer them.
const PERMISSION_OPTIONS: Option[] = [
  { value: "discuss", label: "Discuss", description: "Chat and explore — no edits or commands" },
  { value: "interactive", label: "Ask for approval", description: "Ask before edits and commands" },
  { value: "auto", label: "Full access", description: "Run everything without asking" },
];

type VoiceCaptureMode = "dictation" | "discussion";
const VOICE_MODE_KEY = "openwork-voice-capture-mode";
const getVoiceCaptureMode = (): VoiceCaptureMode => {
  try {
    return window.localStorage.getItem(VOICE_MODE_KEY) === "discussion" ? "discussion" : "dictation";
  } catch {
    return "dictation";
  }
};
const voiceModeName = (mode: VoiceCaptureMode) =>
  mode === "discussion" ? "Voice discussion" : "Dictation";

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
  connected: boolean;
  // False when the default model's provider has no key — the composer shows a "connect a model"
  // banner and routes sends to setup (preserving the draft) instead of dropping them.
  modelReady?: boolean;
  onConnectModel?: () => void;
  onConfigureVoiceInput?: () => void;
  onSend: (text: string, attachments?: Attachment[], meta?: UserMessageMeta) => void;
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
  approvalSlot?: ReactNode;
  // Push text + attachments into the composer (e.g. a start-panel task card). The `nonce` makes
  // repeated identical prefills re-apply; the user can still edit before sending.
  prefill?: { text: string; attachments?: Attachment[]; nonce: number };
  // Changes when the active conversation changes; clears any unsent draft.
  resetKey?: string;
  // Surface-specific hint shown in the empty textarea.
  placeholder?: string;
}

export function Composer(props: Props) {
  const [text, setText] = useState("");
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [dragging, setDragging] = useState(false);
  const [attachMenuOpen, setAttachMenuOpen] = useState(false);
  const [dictation, setDictation] = useState<VoiceStatus | null>(null);
  const [dictationBusy, setDictationBusy] = useState<string | null>(null);
  const [dictationError, setDictationError] = useState<string | null>(null);
  const [dictationNotice, setDictationNotice] = useState<string | null>(null);
  const [voiceMode, setVoiceMode] = useState<VoiceCaptureMode>(getVoiceCaptureMode);
  const [recordingSeconds, setRecordingSeconds] = useState(0);
  const [attachNotice, setAttachNotice] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const noticeTimer = useRef<number | null>(null);
  const activeVoiceMode = useRef<VoiceCaptureMode>("dictation");
  const autoSendVoiceTurn = useRef(false);
  const captureEpoch = useRef(0);
  const captureOriginKey = useRef<string | undefined>(undefined);
  const previousResetKey = useRef(props.resetKey);
  const voiceSendContext = useRef({
    connected: props.connected,
    running: props.running,
    modelReady: props.modelReady,
    resetKey: props.resetKey,
    onSend: props.onSend,
  });
  voiceSendContext.current = {
    connected: props.connected,
    running: props.running,
    modelReady: props.modelReady,
    resetKey: props.resetKey,
    onSend: props.onSend,
  };

  const changeVoiceMode = (mode: VoiceCaptureMode) => {
    setVoiceMode(mode);
    try {
      window.localStorage.setItem(VOICE_MODE_KEY, mode);
    } catch {
      /* per-device preference is best effort */
    }
  };

  // Voice discussion is auto-submit by default. Touching the editable composer while recording
  // is the deliberate escape hatch from issue #71: keep the transcript as a draft instead.
  const keepVoiceTurnAsDraft = () => {
    if (activeVoiceMode.current === "discussion" && autoSendVoiceTurn.current) {
      autoSendVoiceTurn.current = false;
      setDictationNotice("Auto-send cancelled. This voice turn will stay in the composer.");
    }
  };

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
    const max = parseFloat(getComputedStyle(el).lineHeight || "22") * 4;
    const next = Math.min(el.scrollHeight, max);
    el.style.height = `${Math.max(next, 24)}px`;
    el.style.overflowY = el.scrollHeight > max ? "auto" : "hidden";
  }, [text]);

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

  // Clear the draft when the conversation changes, so a half-typed message / picked file doesn't
  // bleed from one session into another. Invalidate and cancel any capture owned by the previous
  // conversation so a late transcript can never cross session boundaries.
  useEffect(() => {
    const conversationChanged = previousResetKey.current !== props.resetKey;
    previousResetKey.current = props.resetKey;
    setText("");
    setAttachments([]);
    setDictationNotice(null);
    setDictationError(null);
    if (conversationChanged) {
      captureEpoch.current += 1;
      captureOriginKey.current = undefined;
      autoSendVoiceTurn.current = false;
      setDictationBusy(null);
      setDictation((current) =>
        current?.recording ? { ...current, recording: false } : current,
      );
      void cancelVoiceCapture().catch(() => undefined);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.resetKey]);

  // Keep one composer contract for the local Tauri recorder and the browser's speech-recognition
  // service. The adapter owns runtime-specific permission, capture, and transcription behavior.
  useEffect(() => {
    const refresh = (event?: Event) => {
      const supplied = (event as CustomEvent<VoiceStatus> | undefined)?.detail;
      if (supplied) {
        setDictation(supplied);
        return;
      }
      void getVoiceStatus().then((status) => status && setDictation(status));
    };
    refresh();
    const reportError = (event: Event) => {
      const message = (event as CustomEvent<string>).detail;
      captureEpoch.current += 1;
      captureOriginKey.current = undefined;
      autoSendVoiceTurn.current = false;
      setDictationBusy(null);
      setDictationNotice(null);
      setDictationError(message || "Browser voice input stopped unexpectedly.");
    };
    window.addEventListener("coworker:voice-input-changed", refresh);
    window.addEventListener(VOICE_INPUT_ERROR_EVENT, reportError);
    return () => {
      window.removeEventListener("coworker:voice-input-changed", refresh);
      window.removeEventListener(VOICE_INPUT_ERROR_EVENT, reportError);
      captureEpoch.current += 1;
      captureOriginKey.current = undefined;
      autoSendVoiceTurn.current = false;
      void cancelVoiceCapture().catch(() => undefined);
    };
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
      getVoiceLevel().then((level) => {
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
      void cancelVoiceCapture()
        .catch(() => undefined)
        .finally(() => {
          void getVoiceStatus().then((status) => status && setDictation(status));
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
    keepVoiceTurnAsDraft();
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
          `${file.name} skipped — ${(file.size / 1024 / 1024).toFixed(1)} MB is over your ${maxMb} MB limit (Settings → Token savings)`,
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
            `${a.name} skipped — ${info.pages} pages is over your ${maxPages}-page limit (Settings → Token savings)`,
          );
          continue;
        }
        if (info && !info.ok) {
          showAttachNotice(`${a.name} skipped — ${info.error || "could not read PDF"}`);
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
    const t = text.trim();
    if ((!t && attachments.length === 0) || props.running || dictation?.recording || dictationBusy) return;
    // No model connected: keep the draft (don't drop it) and send the user to setup instead.
    if (needsModel) {
      props.onConnectModel?.();
      return;
    }
    props.onSend(t, attachments);
    setText("");
    setAttachments([]);
  };

  const onKey = (e: React.KeyboardEvent) => {
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
    if (dictationBusy) return;
    let operationEpoch = captureEpoch.current;
    setDictationError(null);
    setDictationNotice(null);
    try {
      if (dictation?.recording) {
        const completedMode = activeVoiceMode.current;
        const shouldAutoSend =
          completedMode === "discussion" && autoSendVoiceTurn.current;
        operationEpoch = captureEpoch.current;
        setDictationBusy("Transcribing…");
        const transcript = await stopVoiceCapture();
        const latest = voiceSendContext.current;
        const captureIsCurrent =
          operationEpoch === captureEpoch.current &&
          captureOriginKey.current === latest.resetKey;
        if (!captureIsCurrent) return;
        const canAutoSend =
          shouldAutoSend &&
          latest.connected &&
          !latest.running &&
          latest.modelReady !== false;
        autoSendVoiceTurn.current = false;
        captureOriginKey.current = undefined;
        if (transcript === null) throw new Error("Could not transcribe your recording.");
        if (transcript.trim()) {
          if (canAutoSend) {
            latest.onSend(transcript.trim(), undefined, { inputMode: "voice_discussion" });
            setText("");
            setAttachments([]);
          } else {
            setText((draft) => (draft.trim() ? `${draft.trimEnd()} ${transcript.trim()}` : transcript.trim()));
            if (completedMode === "discussion") {
              setDictationNotice("Voice transcript kept as an editable draft.");
            }
          }
        } else if (completedMode === "discussion") {
          throw new Error("No speech was detected. Try again and speak for a little longer.");
        }
        const latestStatus = await getVoiceStatus();
        if (operationEpoch === captureEpoch.current) setDictation(latestStatus);
        if (!canAutoSend) textareaRef.current?.focus();
        return;
      }

      const status = dictation || (await getVoiceStatus());
      if (!status) throw new Error("Voice dictation is unavailable.");
      if (!status.supported || !status.model_verified || !status.test_passed) {
        if (getVoiceRuntime() === "native") props.onConfigureVoiceInput?.();
        else throw new Error(status.compatibility_reason || "Voice input is unavailable in this browser.");
        return;
      }
      if (voiceMode === "discussion" && props.modelReady === false) {
        props.onConnectModel?.();
        return;
      }
      if (voiceMode === "discussion" && (!props.connected || props.running)) return;
      setDictationBusy("Starting microphone…");
      operationEpoch = captureEpoch.current + 1;
      captureEpoch.current = operationEpoch;
      captureOriginKey.current = voiceSendContext.current.resetKey;
      activeVoiceMode.current = voiceMode;
      autoSendVoiceTurn.current =
        voiceMode === "discussion" && !text.trim() && attachments.length === 0;
      const recording = await startVoiceCapture();
      if (
        operationEpoch !== captureEpoch.current ||
        captureOriginKey.current !== voiceSendContext.current.resetKey
      ) {
        await cancelVoiceCapture().catch(() => undefined);
        return;
      }
      if (!recording?.recording) throw new Error("Could not start the microphone.");
      setDictation(recording);
    } catch (error) {
      if (operationEpoch !== captureEpoch.current) return;
      autoSendVoiceTurn.current = false;
      captureOriginKey.current = undefined;
      setDictationNotice(null);
      setDictationError(error instanceof Error ? error.message : "Voice dictation is unavailable.");
      const status = await getVoiceStatus();
      if (status && operationEpoch === captureEpoch.current) setDictation(status);
    } finally {
      if (operationEpoch === captureEpoch.current) setDictationBusy(null);
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
  const hasContent = text.trim().length > 0 || attachments.length > 0;
  const activeVoiceModeName = voiceModeName(activeVoiceMode.current);
  const voiceRuntime = getVoiceRuntime();
  const voiceUnavailable = voiceRuntime === "browser" && !!dictation && !dictation.supported;
  const voiceUnavailableReason =
    dictation?.compatibility_reason || "Voice input is unavailable in this browser.";
  const voiceSetupLabel =
    voiceRuntime === "native"
      ? "Configure Voice Input in Settings"
      : "Voice input unavailable in this browser";
  const voiceActionDisabled =
    !!dictationBusy ||
    voiceUnavailable ||
    (!dictation?.recording &&
      voiceMode === "discussion" &&
      (!props.connected || props.running));

  return (
    <div className="composer-wrap px-6 pb-5 pt-4">
      {props.approvalSlot}

      {dictationError && (
        <div className="max-w-3xl mx-auto mb-2 px-1 text-[12px] text-red-600" role="alert">
          {dictationError}
        </div>
      )}
      {dictationNotice && (
        <div className="max-w-3xl mx-auto mb-2 px-1 text-[12px] text-muted" role="status">
          {dictationNotice}
        </div>
      )}

      {/* Rejected-attachment notice (PDF over the user's Token-savings thresholds). */}
      {attachNotice && (
        <div
          data-testid="attach-notice"
          className="max-w-3xl mx-auto mb-1.5 flex items-center gap-2 rounded-lg border border-warnInk/30 bg-warnSoft px-3 py-1.5 text-[12.5px] text-warnInk"
        >
          <span className="flex-1">{attachNotice}</span>
          <button
            className="shrink-0 opacity-60 hover:opacity-100"
            onClick={() => setAttachNotice(null)}
            title="Dismiss"
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
        <textarea
          ref={textareaRef}
          className="w-full block px-3.5 pt-3.5 pb-1.5 text-[14.5px]"
          placeholder={props.placeholder || "Ask the coworker…  (drop or paste files)"}
          value={text}
          onPointerDown={keepVoiceTurnAsDraft}
          onChange={(e) => {
            keepVoiceTurnAsDraft();
            setDictationNotice(null);
            setText(e.target.value);
          }}
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
              title="Attach"
              aria-label="Attach"
              onClick={() => setAttachMenuOpen((v) => !v)}
            >
              <Icon name="plus" size={17} />
            </button>
            {attachMenuOpen && (
              <>
                <div className="fixed inset-0 z-30" onClick={() => setAttachMenuOpen(false)} />
                <div className="absolute z-40 bottom-full mb-1 left-0 min-w-[180px] rounded-xl border border-line bg-panel shadow-2xl py-1.5">
                  {attachItem("image", "Photo or image", () => pickFiles("image/*"))}
                  {attachItem("file", "PDF", () => pickFiles("application/pdf,.pdf"))}
                  {attachItem(
                    "fileCode",
                    "Other files",
                    () => pickFiles("text/*,.md,.csv,.json,.yaml,.yml,.log,.py,.ts,.tsx,.js,.rs,.go,.toml"),
                  )}
                </div>
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
              {activeVoiceMode.current === "discussion" && (
                <span className="shrink-0 rounded-full bg-accentSoft px-2 py-0.5 text-[10.5px] font-medium text-accent">
                  Voice discussion
                </span>
              )}
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
              mode={props.mode}
              onModeChange={props.onModeChange}
              unattended={props.unattended}
              onUnattendedChange={props.onUnattendedChange}
            />
          ) : null}

          {dictationBusy === "Transcribing…" && <span className="text-[11.5px] text-accent">Transcribing…</span>}

          <span className="ml-auto" />

          {/* model — a quiet chip, now for the session's whole life (§17 rev 2026-07-22:
              mid-session switching shipped, so the picker stays actionable; the topbar
              subtitle still states the current model). */}
          {!dictation?.recording && (needsModel ? (
            <button
              className="pill model-warn chip"
              onClick={() => props.onConnectModel?.()}
              title="Connect a model"
              aria-label="No model connected — connect a model"
            >
              <span className="pill-label">No model</span>
              <span className="model-warn-ico" aria-hidden>⚠</span>
            </button>
          ) : modelsLoaded ? (
            <Dropdown value={props.model} options={modelOptions} onChange={props.onModelChange} align="right" />
          ) : (
            <button
              className="pill chip text-faint cursor-default"
              disabled
              data-testid="models-loading"
              title="Fetching the model list from the server"
            >
              <span className="pill-label">Loading models…</span>
            </button>
          ))}

          {/* Voice split control: the primary action records in the selected mode; the quiet
              chevron keeps editable Dictation and auto-submit Voice discussion one click apart. */}
          {dictation && (
            <div className="relative flex items-center">
              <button
                className={
                  iconBtn +
                  (dictation?.recording ? " bg-red-50 text-red-600 hover:bg-red-100" : "") +
                  (!dictation?.recording && voiceMode === "discussion" ? " bg-accentSoft text-accent" : "") +
                  (voiceActionDisabled ? " opacity-60" : "") +
                  (!voiceReady && !dictation?.recording ? " opacity-40" : "")
                }
                onClick={() => void toggleDictation()}
                disabled={voiceActionDisabled}
                title={
                  dictationBusy ||
                  (dictation?.recording
                    ? `Stop ${activeVoiceModeName.toLowerCase()} and transcribe`
                    : voiceReady
                      ? voiceMode === "discussion"
                        ? "Start voice discussion. The transcript sends when you stop."
                        : voiceRuntime === "native"
                          ? "Start local voice dictation"
                          : "Start browser voice dictation"
                      : voiceRuntime === "browser"
                        ? voiceUnavailableReason
                        : voiceSetupLabel)
                }
                aria-label={
                  dictation?.recording
                    ? `Stop ${activeVoiceModeName.toLowerCase()}`
                    : voiceReady
                      ? voiceMode === "discussion"
                        ? "Start voice discussion"
                        : "Start dictation"
                      : voiceSetupLabel
                }
                aria-disabled={
                  (!voiceReady && !dictation?.recording) ||
                  (voiceMode === "discussion" && props.modelReady === false)
                }
              >
                <Icon name={dictation?.recording ? "stop" : "mic"} size={16} />
              </button>
              {!dictation?.recording && (
                <VoiceModePicker
                  mode={voiceMode}
                  onChange={changeVoiceMode}
                  disabled={!!dictationBusy || !dictation.supported}
                  runtime={voiceRuntime}
                />
              )}
            </div>
          )}

          {/* send / stop */}
          {props.running ? (
            <button className="btn danger" onClick={props.onInterrupt}>
              ⏹ Stop
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
              title={needsModel ? "Connect a model to send" : undefined}
              aria-label="Send"
            >
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M12 19V5M5 12l7-7 7 7" />
              </svg>
            </button>
          )}
        </div>
      </div>
      <span className="sr-only" role="status" aria-live="polite">
        {dictation?.recording
          ? `${activeVoiceModeName} listening, ${recordingTime}${
              activeVoiceMode.current === "discussion" && autoSendVoiceTurn.current
                ? ". Transcript will send when stopped."
                : ""
            }`
          : dictationBusy || ""}
      </span>
    </div>
  );
}

function VoiceModePicker({
  mode,
  onChange,
  disabled,
  runtime,
}: {
  mode: VoiceCaptureMode;
  onChange: (mode: VoiceCaptureMode) => void;
  disabled: boolean;
  runtime: VoiceRuntime;
}) {
  const [open, setOpen] = useState(false);
  const choose = (next: VoiceCaptureMode) => {
    onChange(next);
    setOpen(false);
  };
  return (
    <>
      <button
        className="h-7 w-4 -ml-1 grid place-items-center rounded-r-md text-faint hover:bg-paper hover:text-ink disabled:opacity-50"
        aria-label="Voice input mode"
        aria-haspopup="menu"
        aria-expanded={open}
        title={`Voice input mode: ${voiceModeName(mode)}`}
        disabled={disabled}
        onClick={() => setOpen((value) => !value)}
      >
        <Icon name="chevronDown" size={10} />
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-30" onClick={() => setOpen(false)} />
          <div
            className="voice-mode-menu absolute z-40 bottom-full mb-1.5 right-0 w-[304px] rounded-xl border border-lineStrong bg-panel p-2"
            role="menu"
            aria-label="Voice input mode"
          >
            <VoiceModeOption
              checked={mode === "dictation"}
              label="Dictation"
              description="Transcribe into the composer so you can edit before sending."
              onClick={() => choose("dictation")}
            />
            <VoiceModeOption
              checked={mode === "discussion"}
              label="Voice discussion"
              description="Send the transcript automatically when you stop speaking."
              onClick={() => choose("discussion")}
            />
            <div className="mx-2.5 mt-1.5 border-t border-line px-0 pt-2.5 pb-1.5 text-[10.5px] leading-[1.45] text-faint">
              Clicking or typing in the composer keeps a discussion turn as an editable draft.{" "}
              {runtime === "browser"
                ? "Transcription is managed by your browser."
                : "Desktop transcription stays on this device."}
            </div>
          </div>
        </>
      )}
    </>
  );
}

function VoiceModeOption({
  checked,
  label,
  description,
  onClick,
}: {
  checked: boolean;
  label: string;
  description: string;
  onClick: () => void;
}) {
  return (
    <button
      className={
        "voice-mode-option w-full rounded-[10px] px-3 py-2.5 text-left " +
        (checked ? "is-selected" : "")
      }
      role="menuitemradio"
      aria-checked={checked}
      onClick={onClick}
    >
      <span className="flex items-start gap-3">
        <span
          className="voice-mode-indicator mt-px grid h-6 w-6 shrink-0 place-items-center rounded-full border"
          data-testid={checked ? "voice-mode-selected-indicator" : undefined}
        >
          {checked ? <Icon name="check" size={14} /> : <Icon name="mic" size={12} />}
        </span>
        <span className="min-w-0">
          <span className="voice-mode-label block text-[13.5px] leading-[1.25]">
            {label}
          </span>
          <span className="voice-mode-description mt-1 block text-[11.5px] leading-[1.4]">
            {description}
          </span>
        </span>
      </span>
    </button>
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
}: {
  mode: string;
  onModeChange: (mode: string) => void;
  unattended?: boolean;
  onUnattendedChange?: (on: boolean) => void;
}) {
  const [open, setOpen] = useState(false);
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
        aria-label="Mode"
        title={
          `Mode: ${current?.label || mode}` +
          (unattended ? " · approvals go to the Inbox" : "")
        }
      >
        {current?.label || mode}
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
            {PERMISSION_OPTIONS.map((o) => (
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
                    "text-[13px] " + (o.value === mode ? "font-medium text-accent" : "text-ink")
                  }
                >
                  {o.label}
                  {o.value === mode && <span className="ml-1.5">✓</span>}
                </span>
                <span className="text-[11px] text-faint leading-snug">{o.description}</span>
              </button>
            ))}
            {onUnattendedChange && (
              <>
                <div className="my-1 border-t border-line" />
                <div className="flex items-center gap-2 px-2.5 py-1.5">
                  <span className="flex-1 min-w-0">
                    <span className="block text-[13px] text-ink">Send approvals to Inbox</span>
                    <span className="block text-[11px] text-faint leading-snug">
                      Approvals &amp; questions go to the Inbox; the agent keeps working.
                    </span>
                  </span>
                  <Toggle
                    checked={!!unattended}
                    onChange={onUnattendedChange}
                    title="Send approvals to the Inbox"
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
      <button className="attach-x" onClick={onRemove} title="Remove">
        ✕
      </button>
    </div>
  );
}
