import { Icon } from "./Icon";
import type { ServerStatus } from "../tauri";

// Full-stop screen for the desktop shell when its local agent server is not there:
// launch failed, exited during startup, or never answered. Every feature needs that
// backend, so a clear stop with the log path beats the previous behavior — a fully
// navigable UI where every request hung forever with no error (#382).
interface Props {
  fault: ServerStatus;
  onRetry: () => void;
}

const HEADLINES: Record<ServerStatus["status"], string> = {
  spawn_failed: "The local agent server couldn't be launched",
  exited: "The local agent server exited during startup",
  starting: "The local agent server isn't responding",
  listening: "The local agent server isn't responding",
};

export function ServerFault({ fault, onRetry }: Props) {
  return (
    <div className="gate-overlay">
      <div className="gate" data-testid="server-fault">
        <div className="gate-mark">
          <Icon name="logo" size={28} />
        </div>
        <h2>{HEADLINES[fault.status] ?? HEADLINES.starting}</h2>
        <p className="gate-sub">
          The window is fine, but the background server that does the actual work never
          came up — so connectors, sessions, and automations would all hang. The server
          log usually says why.
        </p>
        {fault.detail && (
          <div className="gate-error" data-testid="server-fault-detail">
            {fault.status === "exited" ? `Server process ${fault.detail}` : fault.detail}
          </div>
        )}
        {fault.log_path && (
          <>
            <div className="gate-label">Server log</div>
            <p className="gate-sub gate-path">
              <code>{fault.log_path}</code>
            </p>
          </>
        )}
        {fault.bin_path && (
          <>
            <div className="gate-label">Server binary</div>
            <p className="gate-sub gate-path">
              <code>{fault.bin_path}</code>
            </p>
          </>
        )}
        <p className="gate-sub">
          If reloading doesn't help, quit OpenWorker from the tray icon and open it again.
        </p>
        <div className="gate-foot">
          <button className="btn primary" data-testid="server-fault-retry" onClick={onRetry}>
            Reload
          </button>
        </div>
      </div>
    </div>
  );
}
