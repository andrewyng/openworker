import type { RunEvent, Json } from "@openworker/harness";
import type { Activity, Message } from "../shared/contracts.js";
export class EventProjection {
  private seen = new Set<number>();
  private attempt: string | null = null;
  partial = "";
  stage = "Ready to build";
  activity: Activity[] = [];
  messages: Message[] = [];
  consume(wrapper: RunEvent): boolean {
    const e = wrapper.event;
    if (this.seen.has(e.event_id)) return false;
    this.seen.add(e.event_id);
    if (this.seen.size > 8000)
      this.seen.delete(this.seen.values().next().value!);
    const d = e.data as Record<string, Json>;
    const add = (text: string, kind: Activity["kind"] = "status") =>
      this.add(String(e.event_id), text, kind);
    switch (e.type) {
      case "model_attempt_started":
        this.attempt = String(d.attempt_id);
        this.partial = "";
        this.stage = "Thinking through your game…";
        add("Model request started");
        break;
      case "assistant_delta":
        if (typeof d.text === "string" && d.attempt_id === this.attempt) {
          this.partial = (this.partial + d.text).slice(0, 64000);
          this.stage = "Writing your game…";
        }
        break;
      case "model_attempt_finished":
        if (d.attempt_id === this.attempt && d.status !== "completed") {
          this.partial = "";
          this.stage = "Checking the connection…";
          add("Model attempt did not complete", "error");
        }
        break;
      case "model_retrying":
        this.partial = "";
        this.stage = "Retrying the model request…";
        add("Retrying model request");
        break;
      case "tool_started":
        this.stage = "Updating your game…";
        add(
          wrapper.summary && "tool" in wrapper.summary
            ? `Running ${wrapper.summary.tool.replaceAll("__", ".")}`
            : "Tool started",
          "tool",
        );
        break;
      case "tool_finished":
        this.stage = "Checking the result…";
        add("Tool finished", "tool");
        break;
      case "tool_progress":
        if (d.message === "Playtesting the game draft")
          this.stage = "Playtesting your game…";
        if (typeof d.message === "string") add(d.message.slice(0, 160), "tool");
        break;
      case "interaction_pending":
        this.stage = "Waiting for your input";
        add("Your input is needed");
        break;
      case "turn_cancelled":
        this.partial = "";
        this.stage = "Build cancelled";
        add("Build cancelled");
        break;
    }
    return true;
  }
  add(id: string, text: string, kind: Activity["kind"] = "status"): void {
    if (!this.activity.some((a) => a.id === id))
      this.activity = [
        ...this.activity,
        { id, text, kind, time: new Date().toISOString() },
      ].slice(-80);
  }
  finish(status: string): void {
    this.partial = "";
    this.attempt = null;
    this.stage =
      status === "completed"
        ? "Ready for your next idea"
        : status === "interrupted"
          ? "Waiting for your input"
          : status === "cancelled"
            ? "Build cancelled"
            : "Build needs attention";
  }
  hydrate(
    messages: Message[],
    attempts: { id: string; status: string; text: string }[] = [],
    running = false,
  ): void {
    this.messages = messages;
    const current = attempts.find((a) => a.status === "running");
    if (running && current) {
      this.attempt = current.id;
      this.partial = current.text.slice(0, 64000);
    } else this.partial = "";
  }
}
export function projectMessages(
  items: { index: number; item: Record<string, unknown> }[],
): Message[] {
  const result: Message[] = [];
  for (const { index, item } of items) {
    if (
      item.runtime_notice ||
      !["user", "assistant"].includes(String(item.role))
    )
      continue;
    let text =
      typeof item.content === "string"
        ? item.content
        : Array.isArray(item.content)
          ? item.content
              .filter((p) =>
                ["text", "input_text", "output_text"].includes(p.type),
              )
              .map((p) => p.text ?? "")
              .join("")
          : "";
    if (item.role === "user") {
      try {
        const value = JSON.parse(text);
        if (
          value.arcade_context?.version === 1 &&
          typeof value.user_message === "string"
        )
          text = value.user_message;
      } catch {}
    }
    if (text)
      result.push({
        id: `history-${index}`,
        role: item.role as Message["role"],
        text,
      });
  }
  return result;
}
