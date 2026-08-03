import React from "react";
import ReactDOM from "react-dom/client";
import { App } from "./App";
import { I18nProvider } from "./i18n";
import { initTheme } from "./theme";
import { isTauri, openExternal, platformOS } from "./tauri";
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

// Desktop shell: any web link that slips past a component-level handler (connector cards,
// dangerouslySetInnerHTML, future surfaces) must open in the system browser — the webview
// silently drops target="_blank" popups, which read as "clicking does nothing" (issue #270).
// Capture phase so it still runs when a component stopPropagation()s in bubble phase.
if (isTauri()) {
  window.addEventListener(
    "click",
    (e) => {
      if (e.defaultPrevented || e.button !== 0) return;
      const anchor = (e.target as HTMLElement | null)?.closest?.("a[href]");
      if (!(anchor instanceof HTMLAnchorElement)) return;
      const href = anchor.href;
      if (!/^https?:/i.test(href)) return; // in-app routes, artifact:, mailto: — not ours
      e.preventDefault();
      openExternal(href);
    },
    true,
  );
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <I18nProvider>
      <App />
    </I18nProvider>
  </React.StrictMode>,
);
