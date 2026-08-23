// The plan the Progress panel renders, derived from the transcript — and how old it is.
//
// Two numbers, because the list alone was not enough. The panel's headline content is a
// SNAPSHOT of what the model last SAID it was doing, and the model is under no obligation to
// keep saying it: one measured run wrote a five-item plan at its first call, revised it once at
// call 24, then ran 76 more calls finishing items 2-5 — and ended the turn without touching the
// list again. Every one of those 76 steps rendered "Now: item 2 · 1/5 done", and so did the
// finished run. The rows were faithful to the last call; what the panel could not say was that
// the call was 76 steps behind. `stepsSince` is what lets it say so.
//
// The engine works the other end of the same defect (coworker/tools/todo.py `stale_plan_notice`,
// which asks the model to rewrite a list it has left behind); this is what the panel shows when
// that nudge has not landed yet, or the model ignored it.

import type { Item, TodoItem } from "./types";

export interface Plan {
  items: TodoItem[];
  /** Tool calls recorded after the todo_write that produced `items` (0 = just written). */
  stepsSince: number;
}

// Models sometimes pass todo items as bare strings instead of {content, status} objects (the
// backend tool normalizes them the same way; the GUI reads the raw proposal args, so mirror it).
export function normalizeTodos(raw: unknown): TodoItem[] {
  if (!Array.isArray(raw)) return [];
  const statuses = new Set(["pending", "in_progress", "done"]);
  return raw.map((entry: any) => {
    if (entry && typeof entry === "object") {
      const status = entry.status === "completed" ? "done" : entry.status; // common model alias
      return {
        content: String(entry.content ?? ""),
        status: statuses.has(status) ? status : "pending",
      };
    }
    return { content: String(entry ?? ""), status: "pending" as const };
  });
}

/** The last todo_write's list, and the number of tool calls that have run since it. */
export function planFromItems(items: Item[]): Plan {
  let stepsSince = 0;
  for (let i = items.length - 1; i >= 0; i--) {
    const it = items[i] as any;
    if (it?.kind !== "tool") continue;
    if (it.name === "todo_write") {
      const raw = it.args?.todos ?? it.args?.items;
      // `todos` is current; `items` is the pre-rename key (see coworker/tools/todo.py). A call
      // carrying NEITHER never parsed its arguments — a truncated stream, or a local model's
      // malformed JSON — so the search continues past it to the last list that exists. It is
      // still a step: the plan is that much older, and silently rewinding to an earlier list
      // without ageing it would be the same lie in a different place.
      if (Array.isArray(raw)) return { items: normalizeTodos(raw), stepsSince };
    }
    stepsSince++;
  }
  return { items: [], stepsSince: 0 };
}
