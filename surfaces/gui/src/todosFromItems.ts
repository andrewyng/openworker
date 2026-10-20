import type { Item, TodoItem } from "./types";

// Models sometimes pass todo items as bare strings instead of {content, status} objects (the
// backend tool normalizes them the same way; the GUI reads the raw proposal args, so mirror it).
function normalizeTodos(raw: unknown): TodoItem[] {
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

// The transcript is the source of truth for both live proposals and session replay.
// Deriving progress here prevents navigation resets from discarding the saved plan.
export function todosFromItems(items: Item[]): TodoItem[] {
  for (let i = items.length - 1; i >= 0; i--) {
    const item = items[i];
    if (item.kind !== "tool" || item.name !== "todo_write") continue;
    const raw = item.args?.todos ?? item.args?.items;
    if (Array.isArray(raw)) return normalizeTodos(raw);
  }
  return [];
}
