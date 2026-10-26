// Card gallery (dev only) — spec: ocw-context/docs/card-gallery-spec.md.
//
// A state is a NAME plus the exact payload the server sends for that card: the websocket
// event's `data` for an inline card. The files under states/ hold data only (no React), so
// the gallery, the unit tests, the Playwright fixture and the server-side contract check
// can all read the same payloads.

/** What the card reads from the app around it, not from the payload. */
export interface StateContext {
  /** The session's approval mode (composer state) — "auto-approve" hides session grants. */
  mode?: string;
  /** The automation run this approval was raised in — unlocks "Allow every time". */
  runTask?: { id: string; title: string };
}

export interface CardState<P = Record<string, unknown>> {
  /** URL slug: /#/gallery/<card>/<id>. Never rename once linked. */
  id: string;
  title: string;
  /** One line on what to look at in this state. */
  note?: string;
  payload: P;
  context?: StateContext;
}
