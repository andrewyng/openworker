/**
 * True while an IME (Chinese pinyin, Japanese kana, etc.) is composing text.
 *
 * During composition, Enter / ArrowUp / ArrowDown / Escape belong to the IME
 * candidate list — e.g. Enter confirms the highlighted candidate. Those key
 * events still fire as keydown, so any handler that treats Enter as "send /
 * submit / choose" must first check this and bail out, or the user's candidate
 * confirmation gets swallowed as a message send (caught 2026-08-03, zh IME).
 *
 * Two signals cover modern WebKit/Chromium (`isComposing`, reliable in the
 * Tauri WKWebView) and legacy engines (`keyCode === 229`).
 */
export function imeComposing(e: {
  nativeEvent: { isComposing?: boolean };
  keyCode?: number;
}): boolean {
  return Boolean(e.nativeEvent?.isComposing) || e.keyCode === 229;
}
