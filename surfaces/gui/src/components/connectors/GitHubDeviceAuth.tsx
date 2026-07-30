import { useEffect, useRef, useState } from "react";
import {
  cancelGithubDeviceAuth,
  pollGithubDeviceAuth,
  startGithubDeviceAuth,
} from "../../api";
import { openExternal } from "../../tauri";
import { PILL_ACCENT, PILL_LINE, TAG_ACCENT } from "./ui";

type Flow = {
  id: string;
  code: string;
  url: string;
  interval: number;
  expiresIn: number;
};

export function GitHubDeviceAuth({ onConnected }: { onConnected: () => void }) {
  const [starting, setStarting] = useState(false);
  const [flow, setFlow] = useState<Flow | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const onConnectedRef = useRef(onConnected);
  onConnectedRef.current = onConnected;

  useEffect(() => {
    if (!flow) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const tick = async () => {
      try {
        const result = await pollGithubDeviceAuth(flow.id);
        if (stopped) return;
        if (result.state === "complete") {
          setFlow(null);
          onConnectedRef.current();
          return;
        }
        if (result.state !== "pending") {
          setFlow(null);
          setError(result.error || "GitHub sign-in did not complete.");
          return;
        }
        setError(result.error || null);
        timer = setTimeout(tick, Math.max(1, result.retry_after || flow.interval) * 1000);
      } catch {
        if (!stopped) {
          setError("Could not check GitHub yet; retrying.");
          timer = setTimeout(tick, Math.max(2, flow.interval) * 1000);
        }
      }
    };

    timer = setTimeout(tick, 0);
    return () => {
      stopped = true;
      if (timer) clearTimeout(timer);
      // Closing the modal, switching panes, or navigating away must also
      // forget the server-side device code rather than merely stopping UI polls.
      void Promise.resolve(cancelGithubDeviceAuth(flow.id)).catch(() => undefined);
    };
  }, [flow]);

  const start = async () => {
    setStarting(true);
    setError(null);
    setCopied(false);
    try {
      const result = await startGithubDeviceAuth();
      if (
        !result.ok ||
        !result.flow_id ||
        !result.user_code ||
        !result.verification_uri
      ) {
        setError(result.error || "Could not start GitHub sign-in.");
        return;
      }
      setFlow({
        id: result.flow_id,
        code: result.user_code,
        url: result.verification_uri,
        interval: Math.max(1, result.interval || 5),
        expiresIn: Math.max(1, result.expires_in || 900),
      });
    } catch {
      setError("Could not reach the local OpenWorker service.");
    } finally {
      setStarting(false);
    }
  };

  const copyAndOpen = async () => {
    if (!flow) return;
    try {
      await navigator.clipboard?.writeText(flow.code);
      setCopied(true);
    } catch {
      setCopied(false);
    }
    openExternal(flow.url);
  };

  const cancel = async () => {
    const id = flow?.id;
    setFlow(null);
    setError(null);
    setCopied(false);
    if (id) await cancelGithubDeviceAuth(id).catch(() => undefined);
  };

  if (!flow) {
    return (
      <div className="px-5 pt-4 pb-3 space-y-3" data-testid="github-device-auth">
        <p className="text-[13px] text-muted">
          Sign in directly with GitHub. No OpenWorker Cloud account, callback server, or
          manually-created token is needed.
        </p>
        <button
          className={PILL_ACCENT + " w-full !py-2"}
          data-testid="github-device-start"
          onClick={start}
          disabled={starting}
        >
          {starting ? "Contacting GitHub…" : "Sign in with GitHub"}
        </button>
        {error && <div className="text-[12.5px] text-danger">{error}</div>}
        <p className="text-[12px] text-faint text-center">
          <span className={TAG_ACCENT}>Local</span> tools only · credentials stay on this
          computer
        </p>
      </div>
    );
  }

  return (
    <div className="px-5 pt-4 pb-3 space-y-3" data-testid="github-device-auth">
      <p className="text-[13px] text-muted">
        Copy this one-time code, then approve OpenWorker on GitHub.
      </p>
      <div
        className="rounded-xl border border-line bg-paper px-4 py-3 text-center"
        data-testid="github-device-code-card"
      >
        <div
          className="font-mono text-[24px] font-semibold tracking-[0.12em] text-ink"
          data-testid="github-user-code"
        >
          {flow.code}
        </div>
        <div className="text-[11.5px] text-faint mt-1">
          Expires in about {Math.max(1, Math.round(flow.expiresIn / 60))} minutes
        </div>
      </div>
      <button
        className={PILL_ACCENT + " w-full !py-2"}
        data-testid="github-device-copy-open"
        onClick={copyAndOpen}
      >
        {copied ? "Copied — open GitHub again" : "Copy code & open GitHub"}
      </button>
      <div className="flex items-center justify-between gap-3 text-[12px]">
        <span className="text-muted" data-testid="github-device-waiting">
          <span className="inline-block w-1.5 h-1.5 rounded-full bg-accent animate-pulse mr-1.5" />
          Waiting for approval on GitHub…
        </span>
        <button className={PILL_LINE + " !px-2.5 !py-1"} onClick={cancel}>
          Cancel
        </button>
      </div>
      {error && <div className="text-[12px] text-danger">{error}</div>}
      <a
        className="block text-[11.5px] text-faint hover:text-muted break-all text-center"
        href={flow.url}
        target="_blank"
        rel="noreferrer"
      >
        {flow.url}
      </a>
    </div>
  );
}
