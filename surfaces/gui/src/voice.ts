import {
  cancelDictation,
  getDictationLevel,
  getDictationStatus,
  isTauri,
  startDictation,
  stopDictation,
  type DictationStatus,
} from "./tauri";

export type VoiceRuntime = "native" | "browser";
export type VoiceStatus = DictationStatus;

type BrowserSpeechAlternative = { transcript: string };
type BrowserSpeechResult = {
  readonly isFinal: boolean;
  readonly length: number;
  [index: number]: BrowserSpeechAlternative;
};
type BrowserSpeechResultList = {
  readonly length: number;
  [index: number]: BrowserSpeechResult;
};
type BrowserSpeechEvent = {
  readonly resultIndex: number;
  readonly results: BrowserSpeechResultList;
};
type BrowserSpeechErrorEvent = {
  readonly error: string;
  readonly message?: string;
};
type BrowserSpeechRecognition = {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  onstart: (() => void) | null;
  onresult: ((event: BrowserSpeechEvent) => void) | null;
  onerror: ((event: BrowserSpeechErrorEvent) => void) | null;
  onend: (() => void) | null;
  start(): void;
  stop(): void;
  abort(): void;
};
type BrowserSpeechRecognitionConstructor = new () => BrowserSpeechRecognition;

type BrowserAudioContext = AudioContext & {
  close(): Promise<void>;
};

type BrowserVoiceSession = {
  recognition: BrowserSpeechRecognition;
  desired: boolean;
  recognitionActive: boolean;
  stopRequested: boolean;
  cancelRequested: boolean;
  finalTranscript: string;
  interimTranscript: string;
  stream: MediaStream;
  audioContext: BrowserAudioContext | null;
  analyser: AnalyserNode | null;
  levelData: Uint8Array<ArrayBuffer> | null;
  restartTimer: number | null;
  startSettled: boolean;
  resolveStart: (status: VoiceStatus) => void;
  rejectStart: (error: Error) => void;
  resolveStop: ((transcript: string) => void) | null;
  rejectStop: ((error: Error) => void) | null;
  terminalError: Error | null;
};

let browserSession: BrowserVoiceSession | null = null;

export const getVoiceRuntime = (): VoiceRuntime => (isTauri() ? "native" : "browser");

const speechRecognitionConstructor = (): BrowserSpeechRecognitionConstructor | null => {
  const root = globalThis as typeof globalThis & {
    SpeechRecognition?: BrowserSpeechRecognitionConstructor;
    webkitSpeechRecognition?: BrowserSpeechRecognitionConstructor;
  };
  return root.SpeechRecognition || root.webkitSpeechRecognition || null;
};

const browserAudioContextConstructor = () => {
  const root = globalThis as typeof globalThis & {
    webkitAudioContext?: typeof AudioContext;
  };
  return globalThis.AudioContext || root.webkitAudioContext || null;
};

const browserCompatibility = () => {
  if (!speechRecognitionConstructor()) {
    return "This browser does not provide speech recognition. Try the latest Chrome, Edge, or Safari.";
  }
  if (!navigator.mediaDevices?.getUserMedia) {
    return "Microphone access is unavailable. Open OpenWorker from a secure local or HTTPS address.";
  }
  return null;
};

const browserStatus = (): VoiceStatus => {
  const compatibilityReason = browserCompatibility();
  const supported = compatibilityReason === null;
  return {
    recording: !!browserSession?.desired,
    model_installed: supported,
    model_verified: supported,
    test_passed: supported,
    download_in_progress: false,
    model_name: "Browser speech recognition",
    model_bytes: 0,
    supported,
    device_summary: supported
      ? "Browser speech recognition · microphone permission required"
      : "Browser voice input unavailable",
    compatibility_reason: compatibilityReason,
  };
};

const publishBrowserStatus = () => {
  if (typeof window === "undefined") return;
  window.dispatchEvent(
    new CustomEvent<VoiceStatus>("coworker:voice-input-changed", {
      detail: browserStatus(),
    }),
  );
};

const speechError = (event: BrowserSpeechErrorEvent): Error => {
  switch (event.error) {
    case "not-allowed":
    case "service-not-allowed":
      return new Error("Microphone access was blocked. Allow it in your browser’s site settings and try again.");
    case "audio-capture":
      return new Error("No working microphone is available to this browser.");
    case "network":
      return new Error("The browser speech recognition service could not be reached.");
    case "language-not-supported":
      return new Error("The browser speech recognition service does not support this language.");
    case "no-speech":
      return new Error("No speech was detected. Try again and speak for a little longer.");
    case "aborted":
      return new Error("Voice input was cancelled.");
    default:
      return new Error(event.message || "Browser voice input stopped unexpectedly.");
  }
};

const cleanupBrowserMedia = (session: BrowserVoiceSession) => {
  if (session.restartTimer !== null) window.clearTimeout(session.restartTimer);
  session.restartTimer = null;
  session.stream.getTracks().forEach((track) => track.stop());
  void session.audioContext?.close().catch(() => undefined);
};

const settleBrowserSession = (session: BrowserVoiceSession) => {
  if (browserSession !== session) return;
  session.desired = false;
  session.recognitionActive = false;
  cleanupBrowserMedia(session);
  browserSession = null;
  publishBrowserStatus();

  if (!session.startSettled) {
    session.startSettled = true;
    session.rejectStart(session.terminalError || new Error("Could not start browser voice input."));
  }
  if (session.resolveStop) {
    const transcript = `${session.finalTranscript} ${session.interimTranscript}`.trim();
    const reject = session.rejectStop;
    const resolve = session.resolveStop;
    session.resolveStop = null;
    session.rejectStop = null;
    if (session.terminalError && !session.cancelRequested) reject?.(session.terminalError);
    else resolve(session.cancelRequested ? "" : transcript);
  }
};

const restartBrowserRecognition = (session: BrowserVoiceSession) => {
  if (browserSession !== session || !session.desired) return;
  session.restartTimer = window.setTimeout(() => {
    session.restartTimer = null;
    if (browserSession !== session || !session.desired) return;
    try {
      session.recognition.start();
    } catch {
      session.terminalError = new Error("Browser voice input stopped unexpectedly.");
      settleBrowserSession(session);
    }
  }, 150);
};

const configureBrowserRecognition = (session: BrowserVoiceSession) => {
  const recognition = session.recognition;
  recognition.continuous = true;
  recognition.interimResults = true;
  recognition.lang = navigator.language || "en-US";

  recognition.onstart = () => {
    if (browserSession !== session) return;
    session.recognitionActive = true;
    if (!session.startSettled) {
      session.startSettled = true;
      session.resolveStart(browserStatus());
    }
    publishBrowserStatus();
  };

  recognition.onresult = (event) => {
    if (browserSession !== session) return;
    let interim = "";
    for (let index = event.resultIndex; index < event.results.length; index += 1) {
      const result = event.results[index];
      const transcript = result[0]?.transcript || "";
      if (result.isFinal) session.finalTranscript = `${session.finalTranscript} ${transcript}`.trim();
      else interim += transcript;
    }
    session.interimTranscript = interim.trim();
  };

  recognition.onerror = (event) => {
    if (browserSession !== session) return;
    if (event.error === "aborted" && session.cancelRequested) return;
    if (event.error === "no-speech" && session.desired && !session.stopRequested) return;
    session.terminalError = speechError(event);
  };

  recognition.onend = () => {
    if (browserSession !== session) return;
    session.recognitionActive = false;
    if (session.stopRequested || session.cancelRequested || session.terminalError) {
      settleBrowserSession(session);
      return;
    }
    restartBrowserRecognition(session);
  };
};

const startBrowserVoice = async (): Promise<VoiceStatus> => {
  if (browserSession?.desired) return browserStatus();
  const Recognition = speechRecognitionConstructor();
  const compatibilityReason = browserCompatibility();
  if (!Recognition || compatibilityReason) throw new Error(compatibilityReason || "Browser voice input is unavailable.");

  let stream: MediaStream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (error) {
    if (error instanceof DOMException && (error.name === "NotAllowedError" || error.name === "SecurityError")) {
      throw new Error("Microphone access was blocked. Allow it in your browser’s site settings and try again.");
    }
    if (error instanceof DOMException && error.name === "NotFoundError") {
      throw new Error("No working microphone is available to this browser.");
    }
    throw new Error("The browser could not start the microphone.");
  }

  const AudioContextConstructor = browserAudioContextConstructor();
  let audioContext: BrowserAudioContext | null = null;
  let analyser: AnalyserNode | null = null;
  let levelData: Uint8Array<ArrayBuffer> | null = null;
  if (AudioContextConstructor) {
    try {
      audioContext = new AudioContextConstructor() as BrowserAudioContext;
      analyser = audioContext.createAnalyser();
      analyser.fftSize = 256;
      levelData = new Uint8Array(analyser.fftSize);
      audioContext.createMediaStreamSource(stream).connect(analyser);
      void audioContext.resume().catch(() => undefined);
    } catch {
      void audioContext?.close().catch(() => undefined);
      audioContext = null;
      analyser = null;
      levelData = null;
    }
  }

  return new Promise<VoiceStatus>((resolve, reject) => {
    const session: BrowserVoiceSession = {
      recognition: new Recognition(),
      desired: true,
      recognitionActive: false,
      stopRequested: false,
      cancelRequested: false,
      finalTranscript: "",
      interimTranscript: "",
      stream,
      audioContext,
      analyser,
      levelData,
      restartTimer: null,
      startSettled: false,
      resolveStart: resolve,
      rejectStart: reject,
      resolveStop: null,
      rejectStop: null,
      terminalError: null,
    };
    browserSession = session;
    configureBrowserRecognition(session);
    try {
      session.recognition.start();
    } catch {
      session.terminalError = new Error("Could not start browser voice input.");
      settleBrowserSession(session);
    }
  });
};

const stopBrowserVoice = async (): Promise<string> => {
  const session = browserSession;
  if (!session?.desired) return "";
  session.desired = false;
  session.stopRequested = true;
  return new Promise<string>((resolve, reject) => {
    session.resolveStop = resolve;
    session.rejectStop = reject;
    if (session.restartTimer !== null) {
      window.clearTimeout(session.restartTimer);
      session.restartTimer = null;
    }
    if (session.recognitionActive) {
      session.restartTimer = window.setTimeout(() => settleBrowserSession(session), 5000);
      try {
        session.recognition.stop();
      } catch {
        settleBrowserSession(session);
      }
    } else {
      settleBrowserSession(session);
    }
  });
};

const cancelBrowserVoice = async (): Promise<void> => {
  const session = browserSession;
  if (!session) return;
  session.desired = false;
  session.cancelRequested = true;
  if (session.restartTimer !== null) {
    window.clearTimeout(session.restartTimer);
    session.restartTimer = null;
  }
  if (session.recognitionActive) {
    try {
      session.recognition.abort();
    } catch {
      // The deterministic settlement below still releases the microphone.
    }
  }
  settleBrowserSession(session);
};

const browserVoiceLevel = (): number | null => {
  const session = browserSession;
  if (!session?.analyser || !session.levelData) return null;
  session.analyser.getByteTimeDomainData(session.levelData);
  let sumSquares = 0;
  for (const sample of session.levelData) {
    const normalized = (sample - 128) / 128;
    sumSquares += normalized * normalized;
  }
  return Math.min(1, Math.sqrt(sumSquares / session.levelData.length) * 4);
};

export const getVoiceStatus = () =>
  getVoiceRuntime() === "native" ? getDictationStatus() : Promise.resolve(browserStatus());

export const getVoiceLevel = () =>
  getVoiceRuntime() === "native" ? getDictationLevel() : Promise.resolve(browserVoiceLevel());

export const startVoiceCapture = () =>
  getVoiceRuntime() === "native" ? startDictation() : startBrowserVoice();

export const stopVoiceCapture = () =>
  getVoiceRuntime() === "native" ? stopDictation() : stopBrowserVoice();

export const cancelVoiceCapture = () =>
  getVoiceRuntime() === "native" ? cancelDictation() : cancelBrowserVoice();

export const resetBrowserVoiceCaptureForTests = () => {
  const session = browserSession;
  if (!session) return;
  session.desired = false;
  session.cancelRequested = true;
  try {
    session.recognition.abort();
  } catch {
    // Test doubles do not always implement a complete recognition lifecycle.
  }
  cleanupBrowserMedia(session);
  browserSession = null;
};
