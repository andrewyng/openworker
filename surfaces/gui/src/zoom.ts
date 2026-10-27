// Desktop page zoom, independent of the text-size preference. Browser builds keep
// the browser's native shortcuts; CSS zoom would not resize the layout viewport.
import { isTauri, setUIZoom } from "./tauri";

const KEY = "openwork-ui-zoom";
const MIN = 0.5;
const MAX = 2;

function savedZoom(): number {
  try {
    const value = Number(localStorage.getItem(KEY));
    if (Number.isFinite(value) && value >= MIN && value <= MAX) return value;
  } catch { /* storage is optional */ }
  return 1;
}

/** Install once before rendering; returns a teardown for tests and hot reload. */
export function initZoom(): () => void {
  if (!isTauri()) return () => {};
  let factor = 1;
  // Serialize native calls so quick/repeated key presses cannot apply out of order.
  let pending = Promise.resolve();
  const apply = (next: () => number) => {
    pending = pending.then(async () => {
      const value = Math.max(MIN, Math.min(MAX, Math.round(next() * 100) / 100));
      await setUIZoom(value);
      factor = value;
      try {
        if (factor === 1) localStorage.removeItem(KEY);
        else localStorage.setItem(KEY, String(factor));
      } catch { /* zoom still works without storage */ }
    }).catch((error) => { console.warn("Could not apply UI zoom", error); });
  };
  const initial = savedZoom();
  apply(() => initial);

  const onKey = (event: KeyboardEvent) => {
    if (!(event.metaKey || event.ctrlKey) || event.altKey || event.isComposing) return;
    const { key } = event;
    if (key !== "=" && key !== "+" && key !== "-" && key !== "0") return;
    event.preventDefault();
    apply(() => key === "0" ? 1 : factor + (key === "-" ? -0.1 : 0.1));
  };
  // Capture also covers focused editors that stop keydown propagation.
  window.addEventListener("keydown", onKey, true);
  return () => window.removeEventListener("keydown", onKey, true);
}
