import { describe, expect, it } from "vitest";
import { itemsFromMessages } from "./itemsFromMessages";
import { todosFromItems } from "./todosFromItems";
import type { Item } from "./types";

const plan = (args: unknown): Item => ({
  kind: "tool", id: "plan", name: "todo_write", args, status: "ok",
});

describe("session progress", () => {
  it("restores live progress from saved messages when returning to a session", () => {
    const args = { todos: [{ content: "Fetch", status: "completed" }, { content: "Review", status: "in_progress" }] };
    const live = [plan({ todos: ["Fetch", "Review"] }), plan(args)];
    const replay = itemsFromMessages([{
      role: "assistant", content: "", tool_calls: [{
        id: "saved", function: { name: "todo_write", arguments: JSON.stringify(args) },
      }],
    }]);
    expect(todosFromItems(live)).toEqual([
      { content: "Fetch", status: "done" }, { content: "Review", status: "in_progress" },
    ]);
    expect(todosFromItems([])).toEqual([]);
    expect(todosFromItems(replay)).toEqual(todosFromItems(live));
  });

  it("keeps sessions without plans empty and honors an explicit empty plan", () => {
    expect(todosFromItems([{ kind: "user", text: "Hello" }])).toEqual([]);
    expect(todosFromItems([plan({ todos: ["Old"] }), plan({ todos: [] })])).toEqual([]);
  });

  it("supports legacy items and skips malformed proposals", () => {
    expect(todosFromItems([plan({ items: ["Legacy"] }), plan({ todos: "invalid" })])).toEqual([
      { content: "Legacy", status: "pending" },
    ]);
  });
});
