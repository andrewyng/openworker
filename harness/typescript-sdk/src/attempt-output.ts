import type { RunEvent } from "./types.js";

export interface AttemptView {
  sessionId: string;
  turnId: string | null;
  attemptId: string;
  status: "running" | "completed" | "failed" | "interrupted";
  text: string;
  reasoning: string;
}

/** UI projection for one run. Failed candidates disappear when their attempt
 * fails; replayed events are ignored. This never changes the durable transcript.
 * Render text with textContent/escaped text, never as HTML. */
export class AttemptOutput {
  private readonly rows = new Map<string, AttemptView>();
  private readonly cursors = new Map<string, number>();
  constructor(private readonly maxAttempts = 128, private readonly maxChars = 262144) {
    if (!Number.isSafeInteger(maxAttempts) || maxAttempts < 1 || maxAttempts > 1024 ||
        !Number.isSafeInteger(maxChars) || maxChars < 1 || maxChars > 1048576)
      throw new Error("Invalid attempt display limits");
  }
  apply(item: RunEvent): void {
    const { event, sessionId } = item;
    if (event.event_id <= (this.cursors.get(sessionId) ?? -1)) return;
    this.cursors.set(sessionId, event.event_id);
    const summary = item.summary;
    if (!summary || !("attemptId" in summary)) return;
    const key = JSON.stringify([sessionId, event.turn_id, summary.attemptId]);
    let row = this.rows.get(key);
    if (!row) {
      row = { sessionId, turnId:event.turn_id, attemptId:summary.attemptId,
        status:"running", text:"", reasoning:"" };
      this.rows.set(key, row);
      while (this.rows.size > this.maxAttempts) this.rows.delete(this.rows.keys().next().value!);
    }
    if (summary.type === "model_attempt_finished") {
      row.status = summary.status;
      if (row.status !== "completed") { row.text = ""; row.reasoning = ""; }
    } else if (row.status === "running") {
      if (summary.type === "assistant_delta") row.text = (row.text + summary.text).slice(0, this.maxChars);
      if (summary.type === "reasoning_delta") row.reasoning = (row.reasoning + summary.text).slice(0, this.maxChars);
    }
    // A cancelled/interrupted turn may end before an attempt-finished event.
    // Call interrupt() when the run terminates without completing.
  }
  interrupt(): void {
    for (const row of this.rows.values()) if (row.status === "running") {
      row.status = "interrupted"; row.text = ""; row.reasoning = "";
    }
  }
  get attempts(): readonly AttemptView[] { return [...this.rows.values()].map((row) => ({...row})); }
  get text(): string { return [...this.rows.values()].filter((row) => row.status === "running" || row.status === "completed").map((row) => row.text).join(""); }
  get reasoning(): string { return [...this.rows.values()].filter((row) => row.status === "running" || row.status === "completed").map((row) => row.reasoning).join(""); }
}
