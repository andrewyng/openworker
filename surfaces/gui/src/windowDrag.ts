// Native window drags can swallow the mouse-up that an interactive control needs
// for its click. Filter pointerdown at the title bar, including nested icons.
const INTERACTIVE =
  "button, a, input, textarea, select, label, [role='button'], [role='menuitem'], [role='link'], [contenteditable='true']";

export function shouldStartWindowDrag(event: { button: number; target: EventTarget | null }): boolean {
  if (event.button !== 0) return false;
  const el = event.target as Element | null;
  return !(el && typeof el.closest === "function" && el.closest(INTERACTIVE));
}
