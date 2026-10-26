// The gallery's payloads as one JSON-able object, for the server-side contract check
// (tests/test_gallery_contract.py builds the same payloads from the real server code and
// compares field names and types). Committed as states.contract.json; regenerate with
//   npm run gallery:contract
import { CARDS } from "./cards";
import { inboxItemBuilders } from "./inboxItems";

export function buildContract() {
  const cards: Record<string, unknown> = {};
  for (const card of CARDS) {
    const toInbox = inboxItemBuilders[card.id];
    cards[card.id] = {
      event: card.event,
      // The first state is the card's common case: the fields the card relies on.
      canonical: card.states[0].id,
      states: Object.fromEntries(card.states.map((s) => [s.id, s.payload])),
      inbox: toInbox ? Object.fromEntries(card.states.map((s) => [s.id, toInbox(s)])) : {},
    };
  }
  // Through JSON once, so `undefined` fields drop out exactly as they do on disk.
  return JSON.parse(JSON.stringify({ note: "GENERATED — run `npm run gallery:contract`; do not edit", cards }));
}
