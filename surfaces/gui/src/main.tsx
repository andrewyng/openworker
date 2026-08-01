import React from "react";
import ReactDOM from "react-dom/client";
import { App } from "./App";
import { initTheme } from "./theme";
import { platformOS } from "./tauri";
import "./tailwind.css";
import "./styles.css";

initTheme();
// Platform hook for CSS (html[data-platform="windows"] scrollbar styling etc.).
document.documentElement.dataset.platform = platformOS();

// A file dropped OUTSIDE a drop target (the composer) must never navigate the webview to the
// file itself — the browser/WKWebView default. Drop targets stopPropagation-free preventDefault
// in their own handlers; these guards only catch the misses. (The desktop shell disables Tauri's
// native drag-drop interception so HTML5 drag events reach the DOM at all — see lib.rs.)
window.addEventListener("dragover", (e) => e.preventDefault());
window.addEventListener("drop", (e) => e.preventDefault());

// React 18 unmounts the entire tree on an uncaught render throw, so without a boundary the window
// simply goes blank — which is how one malformed ask_user option (an object where a string was
// promised) turned into an app that wouldn't start, with nothing on screen to say why. The boundary
// can't repair the state that threw; it exists so the failure is legible and the error text is
// something the user can report.
class RootBoundary extends React.Component<{ children: React.ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    // A standalone shell, deliberately not `.app` — that class is the sidebar/content grid.
    return (
      <div className="h-screen grid place-items-center p-8 bg-paper text-ink">
        <div className="max-w-lg rounded-xl2 border border-line bg-panel px-5 py-4">
          <div className="text-[15px] font-semibold">OpenWorker hit an error and stopped.</div>
          <div className="text-[13px] text-muted mt-1">
            The conversation is saved — reloading usually gets you back in.
          </div>
          <pre className="mt-3 px-2.5 py-1.5 rounded-lg border border-line bg-paper font-mono text-[11.5px] leading-relaxed text-muted whitespace-pre-wrap break-words max-h-56 overflow-auto">
            {error.message || String(error)}
          </pre>
          <button
            className="mt-3 px-3 py-1.5 rounded-lg bg-accent text-white text-[12.5px] font-medium hover:brightness-105"
            onClick={() => window.location.reload()}
          >
            Reload
          </button>
        </div>
      </div>
    );
  }
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <RootBoundary>
      <App />
    </RootBoundary>
  </React.StrictMode>,
);
