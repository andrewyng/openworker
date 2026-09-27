// One generated test per gallery state, in each place the card can appear: the payload
// renders through the real component without a console error, and ids stay unique
// (a state id is a URL). Behaviour checks stay in each card's own test file.
import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { InboxItemCard } from "../components/InboxItemCard";
import { CARDS } from "./cards";
import { inboxItemBuilders } from "./inboxItems";

describe("gallery states", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("has unique card ids and unique state ids per card", () => {
    expect(new Set(CARDS.map((c) => c.id)).size).toBe(CARDS.length);
    for (const card of CARDS) {
      expect(card.states.length).toBeGreaterThan(0);
      expect(new Set(card.states.map((s) => s.id)).size).toBe(card.states.length);
      for (const s of card.states) expect(s.id).toMatch(/^[a-z0-9-]+$/);
    }
  });

  it("keeps every payload plain JSON (the server contract check reads them as JSON)", () => {
    for (const card of CARDS)
      for (const s of card.states)
        expect(JSON.parse(JSON.stringify(s.payload))).toEqual(
          // undefined values are how a state says "the server did not send this field"
          Object.fromEntries(Object.entries(s.payload).filter(([, v]) => v !== undefined)),
        );
  });

  for (const card of CARDS) {
    for (const state of card.states) {
      it(`${card.id} / ${state.id} renders in the session`, () => {
        const errors = vi.spyOn(console, "error").mockImplementation(() => {});
        const { container } = render(<>{card.renderInline(state, () => {})}</>);
        expect(container.firstElementChild).toBeTruthy();
        expect(errors).not.toHaveBeenCalled();
      });

      const toInbox = inboxItemBuilders[card.id];
      if (!toInbox) continue;
      it(`${card.id} / ${state.id} renders in the Inbox`, () => {
        const errors = vi.spyOn(console, "error").mockImplementation(() => {});
        const { container } = render(<InboxItemCard item={toInbox(state)} onResolve={() => {}} />);
        expect(container.firstElementChild).toBeTruthy();
        expect(errors).not.toHaveBeenCalled();
      });
    }
  }
});
