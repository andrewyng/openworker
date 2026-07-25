import { useEffect, useState, type Dispatch, type SetStateAction } from "react";
import {
  cancelDictationModelDownload,
  deleteDictationModel,
  downloadDictationModel,
  getDictationStatus,
  isTauri,
  listenDictationDownloadProgress,
  markDictationTestPassed,
  startDictation,
  stopDictation,
  verifyDictationModel,
  type DictationDownloadProgress,
  type DictationStatus,
} from "../tauri";
import { Icon } from "./Icon";
import { PanelHead } from "./IntegrationsView";

const CARD = "rounded-xl2 border border-line bg-panel";
const BTN_ACCENT =
  "text-[12.5px] px-3 py-2 rounded-lg bg-accent text-white shrink-0 disabled:opacity-40";
const BTN_BORDERED =
  "text-[12.5px] px-3 py-2 rounded-lg border border-line bg-paper hover:border-lineStrong shrink-0";

type Phase = "idle" | "downloading" | "verifying" | "testing" | "transcribing";

type VoiceController = {
  status: DictationStatus | null;
  progress: DictationDownloadProgress | null;
  phase: Phase;
  error: string | null;
  testTranscript: string;
  publish: (status: DictationStatus) => void;
  setProgress: Dispatch<SetStateAction<DictationDownloadProgress | null>>;
  setPhase: Dispatch<SetStateAction<Phase>>;
  setError: Dispatch<SetStateAction<string | null>>;
  setTestTranscript: Dispatch<SetStateAction<string>>;
};

const voiceError = (error: unknown) =>
  error instanceof Error
    ? error.message
    : typeof error === "string"
      ? error
      : "Voice Input could not complete that action.";

const formatBytes = (bytes: number) => {
  if (!bytes) return "0 MiB";
  return `${Math.round(bytes / 1024 / 1024)} MiB`;
};

async function verifyLegacyModel(
  initial: DictationStatus,
  active: () => boolean,
  publish: (status: DictationStatus) => void,
  setPhase: Dispatch<SetStateAction<Phase>>,
  setError: Dispatch<SetStateAction<string | null>>,
) {
  if (!initial.model_installed || initial.model_verified) return;
  setPhase("verifying");
  try {
    const verified = await verifyDictationModel();
    if (active()) publish(verified);
  } catch (error) {
    if (active()) setError(voiceError(error));
  } finally {
    if (active()) setPhase("idle");
  }
}

function useVoiceController(desktop: boolean): VoiceController {
  const [status, setStatus] = useState<DictationStatus | null>(null);
  const [progress, setProgress] = useState<DictationDownloadProgress | null>(
    null,
  );
  const [phase, setPhase] = useState<Phase>("idle");
  const [error, setError] = useState<string | null>(null);
  const [testTranscript, setTestTranscript] = useState("");

  const publish = (next: DictationStatus) => {
    setStatus(next);
    window.dispatchEvent(
      new CustomEvent("coworker:voice-input-changed", { detail: next }),
    );
  };

  useEffect(() => {
    if (!desktop) return;
    let active = true;
    let unlisten = () => {};
    void listenDictationDownloadProgress((next) => {
      if (active) setProgress(next);
    }).then((stop) => {
      if (active) unlisten = stop;
      else stop();
    });
    void getDictationStatus().then((initial) => {
      if (!active || !initial) return;
      publish(initial);
      void verifyLegacyModel(
        initial,
        () => active,
        publish,
        setPhase,
        setError,
      );
    });
    return () => {
      active = false;
      unlisten();
    };
  }, [desktop]);

  return {
    status,
    progress,
    phase,
    error,
    testTranscript,
    publish,
    setProgress,
    setPhase,
    setError,
    setTestTranscript,
  };
}

function useModelActions(controller: VoiceController) {
  const download = async () => {
    controller.setError(null);
    controller.setProgress({
      downloaded_bytes: 0,
      total_bytes: controller.status?.model_bytes || 0,
    });
    controller.setPhase("downloading");
    try {
      controller.publish(await downloadDictationModel());
    } catch (error) {
      controller.setError(voiceError(error));
      const latest = await getDictationStatus();
      if (latest) controller.publish(latest);
    } finally {
      controller.setPhase("idle");
    }
  };

  const cancel = async () => {
    await cancelDictationModelDownload().catch(() => undefined);
  };

  const repair = async () => {
    controller.setError(null);
    try {
      controller.publish(await deleteDictationModel());
      await download();
    } catch (error) {
      controller.setError(voiceError(error));
    }
  };

  const remove = async () => {
    const confirmed = window.confirm(
      "Delete the local Whisper model and disable Voice Input?",
    );
    if (!confirmed) return;
    controller.setError(null);
    try {
      controller.publish(await deleteDictationModel());
      controller.setTestTranscript("");
      controller.setProgress(null);
    } catch (error) {
      controller.setError(voiceError(error));
    }
  };

  return { download, cancel, repair, remove };
}

function useMicrophoneTest(controller: VoiceController) {
  return async () => {
    const status = controller.status;
    if (!status?.supported || !status.model_verified) return;
    controller.setError(null);
    try {
      if (status.recording) {
        controller.setPhase("transcribing");
        const transcript = (await stopDictation()).trim();
        controller.setTestTranscript(transcript);
        if (!transcript) {
          throw new Error(
            "No speech was detected. Try again and speak for a little longer.",
          );
        }
        controller.publish(await markDictationTestPassed());
      } else {
        controller.setTestTranscript("");
        controller.setPhase("testing");
        controller.publish(await startDictation());
      }
    } catch (error) {
      controller.setError(voiceError(error));
      const latest = await getDictationStatus();
      if (latest) controller.publish(latest);
    } finally {
      controller.setPhase("idle");
    }
  };
}

function DeviceCompatibilityCard({
  status,
}: {
  status: DictationStatus | null;
}) {
  return (
    <div className={CARD}>
      <div className="p-4 flex items-start gap-3">
        <Icon name="code" size={18} className="text-accent mt-0.5" />
        <div className="min-w-0 flex-1">
          <div className="text-[13.5px] font-medium">This device</div>
          <div className="text-[12px] text-muted mt-1">
            {status?.device_summary || "Checking compatibility…"}
          </div>
          {status?.compatibility_reason && (
            <div className="text-[12px] text-red-600 mt-1.5">
              {status.compatibility_reason}
            </div>
          )}
        </div>
        {status && (
          <span
            className={
              "text-[11.5px] px-2 py-1 rounded-full " +
              (status.supported
                ? "bg-green-50 text-green-700"
                : "bg-red-50 text-red-600")
            }
          >
            {status.supported ? "● Compatible" : "Unsupported"}
          </span>
        )}
      </div>
      <div className="border-t border-line bg-paper/50 px-4 py-3 grid grid-cols-2 gap-3 text-[12px] text-muted">
        <div>
          <span className="block text-ink font-medium">Mac</span>
          macOS 12+ · Apple Silicon M1+
        </div>
        <div>
          <span className="block text-ink font-medium">Windows</span>
          Windows 10 22H2/11 · x64
        </div>
        <div>
          <span className="block text-ink font-medium">Memory</span>8 GB
          recommended
        </div>
        <div>
          <span className="block text-ink font-medium">Processor</span>4 CPU
          cores recommended
        </div>
      </div>
    </div>
  );
}

function ModelActions({
  status,
  phase,
  downloading,
  onDownload,
  onCancel,
  onRepair,
  onRemove,
}: {
  status: DictationStatus | null;
  phase: Phase;
  downloading: boolean;
  onDownload: () => void;
  onCancel: () => void;
  onRepair: () => void;
  onRemove: () => void;
}) {
  if (status?.model_verified) {
    return (
      <>
        <span className="text-[11.5px] px-2 py-1 rounded-full bg-green-50 text-green-700">
          Verified
        </span>
        <button className={BTN_BORDERED} onClick={onRepair}>
          Repair
        </button>
        <button
          className="text-[12px] text-red-600 px-2 py-2"
          onClick={onRemove}
        >
          Delete
        </button>
      </>
    );
  }
  if (downloading) {
    return (
      <button className={BTN_BORDERED} onClick={onCancel}>
        Cancel
      </button>
    );
  }
  if (phase === "verifying") {
    return <span className="text-[12px] text-muted">Verifying…</span>;
  }
  return (
    <button
      className={BTN_ACCENT}
      disabled={!status?.supported}
      onClick={onDownload}
    >
      Download model
    </button>
  );
}

function DownloadProgress({
  progress,
  total,
}: {
  progress: DictationDownloadProgress | null;
  total: number;
}) {
  const downloaded = progress?.downloaded_bytes || 0;
  const percent = Math.min(100, Math.round((downloaded / total) * 100));
  return (
    <div className="border-t border-line px-4 py-3">
      <div className="h-1.5 rounded-full bg-line overflow-hidden">
        <div
          className="h-full bg-accent transition-all"
          style={{ width: `${percent}%` }}
        />
      </div>
      <div className="mt-1.5 text-[11.5px] text-muted flex">
        <span>
          {formatBytes(downloaded)} of {formatBytes(total)}
        </span>
        <span className="ml-auto">{percent}%</span>
      </div>
    </div>
  );
}

function VoiceModelCard({
  controller,
  actions,
}: {
  controller: VoiceController;
  actions: ReturnType<typeof useModelActions>;
}) {
  const { status, progress, phase } = controller;
  const downloading = phase === "downloading" || !!status?.download_in_progress;
  const progressTotal = progress?.total_bytes || status?.model_bytes || 1;
  return (
    <div className={CARD}>
      <div className="p-4 flex items-center gap-3">
        <div className="w-9 h-9 rounded-lg bg-accentSoft text-accent grid place-items-center font-semibold">
          W
        </div>
        <div className="min-w-0 flex-1">
          <div className="text-[13.5px] font-medium">
            Whisper Base · English
          </div>
          <div className="text-[12px] text-muted mt-0.5">
            {status?.model_verified
              ? `Installed and verified · ${formatBytes(status.model_bytes)}`
              : `Local voice model · ${formatBytes(status?.model_bytes || 147_964_211)}`}
          </div>
        </div>
        <ModelActions
          status={status}
          phase={phase}
          downloading={downloading}
          onDownload={() => void actions.download()}
          onCancel={() => void actions.cancel()}
          onRepair={() => void actions.repair()}
          onRemove={() => void actions.remove()}
        />
      </div>
      {downloading && (
        <DownloadProgress progress={progress} total={progressTotal} />
      )}
    </div>
  );
}

function MicrophoneTestCard({
  controller,
  onToggle,
}: {
  controller: VoiceController;
  onToggle: () => void;
}) {
  const { status, phase, testTranscript } = controller;
  const ready =
    !!status?.supported && !!status.model_verified && !!status.test_passed;
  const buttonLabel = status?.recording
    ? "Stop and check"
    : phase === "transcribing"
      ? "Transcribing…"
      : ready
        ? "Test again"
        : "Test microphone";
  return (
    <div className={CARD}>
      <div className="p-4 flex items-center gap-3">
        <Icon
          name="mic"
          size={18}
          className={ready ? "text-green-600" : "text-muted"}
        />
        <div className="min-w-0 flex-1">
          <div className="text-[13.5px] font-medium">Microphone test</div>
          <div className="text-[12px] text-muted mt-0.5">
            {ready
              ? "Your microphone and local transcription engine are working."
              : "Record a short phrase to enable the composer microphone."}
          </div>
        </div>
        {ready && (
          <span className="text-[11.5px] px-2 py-1 rounded-full bg-green-50 text-green-700">
            ● Ready
          </span>
        )}
        <button
          className={BTN_BORDERED}
          disabled={
            !status?.supported ||
            !status.model_verified ||
            phase === "transcribing"
          }
          onClick={onToggle}
        >
          {buttonLabel}
        </button>
      </div>
      {status?.recording && (
        <div
          className="border-t border-line px-4 py-3 text-[12px] text-accent"
          role="status"
        >
          ● Listening… speak a short phrase, then stop.
        </div>
      )}
      {testTranscript && (
        <div className="border-t border-line bg-paper/50 px-4 py-3 text-[13px]">
          “{testTranscript}”
        </div>
      )}
    </div>
  );
}

export function VoiceInputSection() {
  const desktop = isTauri();
  const controller = useVoiceController(desktop);
  const modelActions = useModelActions(controller);
  const toggleTest = useMicrophoneTest(controller);

  return (
    <section>
      <PanelHead
        title="Voice input"
        sub="Speak naturally in the composer. Recordings and transcripts stay on this device."
      />
      {!desktop ? (
        <div className={CARD + " p-4 text-[13px] text-muted"}>
          Voice Input setup is available in the OpenWorker desktop app.
        </div>
      ) : (
        <div className="space-y-4">
          <div className="rounded-xl border border-green-200 bg-green-50/70 px-4 py-3 text-[12.5px] text-green-800">
            <span className="font-medium">Private by design.</span> Audio is
            held in memory only while you record and is transcribed locally.
          </div>
          <DeviceCompatibilityCard status={controller.status} />
          <VoiceModelCard controller={controller} actions={modelActions} />
          <MicrophoneTestCard
            controller={controller}
            onToggle={() => void toggleTest()}
          />
          {controller.error && (
            <div
              role="alert"
              className="rounded-lg border border-red-200 bg-red-50 px-3 py-2.5 text-[12px] text-red-700"
            >
              {controller.error}
            </div>
          )}
        </div>
      )}
    </section>
  );
}
