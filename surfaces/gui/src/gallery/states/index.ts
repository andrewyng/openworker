// Every card's states by card id — data only, no React, so the Playwright fixture and the
// JSON export for the server-side contract check can import it.
import type { CardState } from "../types";
import { approvalStates } from "./approval";
import { teamViewStates } from "./team-view";
import { boardWakeStates } from "./board-wake";
import { connectorMessageStates } from "./connector-message";
import { connectorRequestStates } from "./connector-request";
import { directoryRequestStates } from "./directory-request";
import { planStates } from "./plan";
import { questionStates } from "./question";
import { teamRequestStates } from "./team-request";
import { toolRequestStates } from "./tool-request";
import { workItemsStates } from "./work-items";
import { workerDecisionStates } from "./worker-decision";

export const STATES = {
  approval: approvalStates,
  plan: planStates,
  "team-request": teamRequestStates,
  "work-items": workItemsStates,
  "connector-request": connectorRequestStates,
  "worker-decision": workerDecisionStates,
  question: questionStates,
  "directory-request": directoryRequestStates,
  "tool-request": toolRequestStates,
  "board-wake": boardWakeStates,
  "team-view": teamViewStates,
  "connector-message": connectorMessageStates,
} satisfies Record<string, CardState[]>;

export type CardId = keyof typeof STATES;

/** The payload of one named state. Throws on a typo, so a renamed state fails loudly. */
export function statePayload(card: CardId, id: string): Record<string, unknown> {
  const state = STATES[card].find((s) => s.id === id);
  if (!state) throw new Error(`no gallery state ${card}/${id}`);
  return state.payload;
}
