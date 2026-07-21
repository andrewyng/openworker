// The `_display` sidecar on a persisted tool message (privacy-filter hidden
// counts) must surface on the replayed tool item — and only there; the
// agent-visible content string carries no trace.
import { describe, expect, it } from "vitest";
import { itemsFromMessages } from "./itemsFromMessages";

describe("itemsFromMessages _display sidecar", () => {
  it("attaches hidden counts to the matching tool item", () => {
    const items = itemsFromMessages([
      { role: "user", content: "check my mail" },
      {
        role: "assistant",
        content: "",
        tool_calls: [
          { id: "t1", function: { name: "gmail_search_messages", arguments: "{}" } },
          { id: "t2", function: { name: "gmail_get_message", arguments: "{}" } },
        ],
      },
      {
        role: "tool",
        tool_call_id: "t1",
        content: '{"ok": true, "data": {"messages": []}}',
        _display: { hidden_by_filters: 2, connector: "gmail" },
      },
      { role: "tool", tool_call_id: "t2", content: '{"ok": true}' },
    ] as any);

    const tools = items.filter((i: any) => i.kind === "tool") as any[];
    expect(tools).toHaveLength(2);
    expect(tools[0].hidden).toBe(2);
    expect(tools[1].hidden).toBeUndefined();
    expect(tools[0].preview).not.toContain("hidden"); // content stays clean
  });
});

describe("itemsFromMessages timestamps", () => {
  it("carries the server ts through to user/assistant items; pre-stamp history gets none", () => {
    const items = itemsFromMessages([
      { role: "user", content: "hi", ts: 1752969720 },
      { role: "assistant", content: "hello", ts: 1752969724 },
      { role: "user", content: "old message" }, // saved before the server stamped ts
    ] as any);

    expect(items).toEqual([
      { kind: "user", text: "hi", ts: 1752969720 },
      { kind: "assistant", text: "hello", ts: 1752969724 },
      { kind: "user", text: "old message" },
    ]);
  });
});
