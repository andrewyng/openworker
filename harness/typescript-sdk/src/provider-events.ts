import { createHash } from "node:crypto";
import { hook, type SafetyHook } from "./safety.js";
import { record } from "./types.js";

const states = [
  "session_created",
  "turn_started",
  "tool_queued",
  "tool_finished",
  "model_attempt_started",
  "model_attempt_finished",
  "turn_completed",
  "session_closed",
  "waiting_tool_completion",
  "waiting_user_response",
  "user_response_done",
  "waiting_user_approval",
  "user_approval_done",
] as const;
export type ProviderSessionState = (typeof states)[number];
export interface ProviderSessionEvent {
  state: ProviderSessionState;
  sessionId: string;
  turnId: string | null;
  sequence: number;
  interactionId?: string;
  callId?: string;
  decision?: "approved" | "denied";
}
export type ProviderListener = (
  event: ProviderSessionEvent,
  signal: AbortSignal,
) => void | Promise<void>;

/** Adapter callbacks see only lifecycle metadata, never a question, answer, or tool arguments. */
export function providerObserver(
  provider: string,
  listener: ProviderListener,
): SafetyHook {
  const name =
    "provider-" +
    createHash("sha256").update(provider).digest("hex").slice(0, 24);
  const observer = hook.onEvent({
    name,
    run: async (context) => {
      const envelope = record(context.context);
      const event = record(envelope.event);
      const facts = record(event.facts);
      const kind = event.kind;
      const state =
        kind === "tool_started"
          ? "waiting_tool_completion"
          : kind === "interaction_required" || kind === "interaction_resolved"
            ? facts.state_change
            : kind;
      if (!states.includes(state as ProviderSessionState)) return;
      const sequence = Number(String(envelope.eventId).split(":").at(-1));
      if (!Number.isSafeInteger(sequence) || sequence < 1) return;
      await listener(
        {
          state: state as ProviderSessionState,
          sessionId: context.sessionId,
          turnId: context.turnId,
          sequence,
          interactionId:
            typeof facts.interaction_id === "string"
              ? facts.interaction_id
              : undefined,
          callId: typeof facts.call_id === "string" ? facts.call_id : undefined,
          decision:
            facts.decision === "approved" || facts.decision === "denied"
              ? facts.decision
              : undefined,
        },
        context.signal,
      );
    },
  });
  return { ...observer, provider };
}
