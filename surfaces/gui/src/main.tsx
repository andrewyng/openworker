import React from "react";
import ReactDOM from "react-dom/client";
import { App } from "./App";
import { initCloudAuth, shouldGate } from "./cloudAuth";
import { initTheme } from "./theme";
import { initTextSize } from "./textSize";
import { platformOS } from "./tauri";
import { initI18n } from "./i18n";
import "./tailwind.css";
import "./styles.css";

initTheme();
initTextSize();
// Platform hook for CSS (html[data-platform="windows"] scrollbar styling etc.).
document.documentElement.dataset.platform = platformOS();

// A file dropped OUTSIDE a drop target (the composer) must never navigate the webview to the
// file itself — the browser/WKWebView default. Drop targets stopPropagation-free preventDefault
// in their own handlers; these guards only catch the misses. (The desktop shell disables Tauri's
// native drag-drop interception so HTML5 drag events reach the DOM at all — see lib.rs.)
window.addEventListener("dragover", (e) => e.preventDefault());
window.addEventListener("drop", (e) => e.preventDefault());

// The hosted dashboard signs in before first render; desktop and dev render
// straight away. A sign-in redirect never resolves — the navigation replaces this page.
const root = ReactDOM.createRoot(document.getElementById("root")!);
const render = () =>
  root.render(
    <React.StrictMode>
      <App />
    </React.StrictMode>,
  );

// Dev only: /#/gallery renders the card gallery instead of the app (no server needed).
// The DEV guard is a build-time constant, so the gallery and its payload files are
// dropped from the desktop bundle. The app rewrites the hash with replaceState (which
// fires no hashchange), so this listener only sees a hash a person typed.
const onGallery = () => import.meta.env.DEV && window.location.hash.startsWith("#/gallery");
if (import.meta.env.DEV) {
  window.addEventListener("hashchange", (e) => {
    if (new URL(e.oldURL).hash.startsWith("#/gallery") !== onGallery()) window.location.reload();
  });
}

// Dev only: /?scenario=<id>#/s/<session> boots the REAL app on a saved moment of a session,
// answered from memory (gallery/scenarioApi.ts). A query, not a hash, because the app reads
// its session from the hash at start-up.
const scenarioId = import.meta.env.DEV ? new URLSearchParams(window.location.search).get("scenario") : null;

// Initialize i18n before the first render so t() resolves everywhere (the gallery and
// scenario sessions render the same translated components).
initI18n().finally(() => {
  if (scenarioId) {
    import("./gallery/scenarioBoot").then(({ bootScenario }) => {
      if (!bootScenario(scenarioId, root, App)) render();
    });
  } else if (onGallery()) {
    import("./gallery/Gallery").then(({ Gallery }) =>
      root.render(
        <React.StrictMode>
          <Gallery />
        </React.StrictMode>,
      ),
    );
  } else if (shouldGate()) {
    initCloudAuth().then(render, render);
  } else {
    render();
  }
});
