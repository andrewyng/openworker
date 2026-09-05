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

describe("itemsFromMessages notices", () => {
  it("replays persisted error/interrupted markers; only errors are retriable", () => {
    const items = itemsFromMessages([
      { role: "user", content: "hi" },
      { role: "assistant", content: "partial ans" },
      { role: "notice", kind: "interrupted", ts: 1752969720 },
      { role: "user", content: "again" },
      { role: "notice", kind: "error", text: "model down", ts: 1752969724 },
    ] as any);

    expect(items).toEqual([
      { kind: "user", text: "hi" },
      { kind: "assistant", text: "partial ans" },
      { kind: "notice", tone: "warn", text: "Interrupted.", notice: "interrupted" },
      { kind: "user", text: "again" },
      { kind: "notice", tone: "warn", text: "Error: model down", retriable: true, notice: "error" },
    ]);
  });
});

describe("itemsFromMessages server restart", () => {
  it("replays the restart marker as a resumable warning, and folds the orphan tool result", () => {
    const items = itemsFromMessages([
      { role: "user", content: "go" },
      {
        role: "assistant",
        tool_calls: [{ id: "t1", function: { name: "shell", arguments: "{}" } }],
      },
      {
        role: "tool",
        tool_call_id: "t1",
        content: '{"error":"tool call not executed","reason":"the server restarted"}',
      },
      { role: "notice", kind: "server_restart", text: "The agent server restarted mid-run." },
    ] as any);

    // "Resume", not "Retry": the run was cut off, not wrong.
    expect(items[items.length - 1]).toEqual({
      kind: "notice",
      tone: "warn",
      text: "The agent server restarted mid-run.",
      retriable: true,
      retryLabel: "Resume",
      notice: "server_restart",
    });
  });

  it("falls back to its own wording when the marker carries no text", () => {
    const items = itemsFromMessages([
      { role: "user", content: "go" },
      { role: "notice", kind: "server_restart" },
    ] as any);
    expect((items[1] as any).text).toMatch(/restarted/);
    expect((items[1] as any).retriable).toBe(true);
  });
});

describe("itemsFromMessages model switch", () => {
  it("replays the persisted model_switch marker as an info notice", () => {
    const items = itemsFromMessages([
      { role: "user", content: "hi" },
      { role: "notice", kind: "model_switch", text: "Model switched to Kimi K2.6 · Moonshot" },
    ] as any);
    expect(items[1]).toEqual({
      kind: "notice",
      tone: "info",
      text: "Model switched to Kimi K2.6 · Moonshot",
      notice: "model_switch",
    });
  });
});

describe("itemsFromMessages compaction", () => {
  it("replays the persisted compacted marker as an info notice (the divider)", () => {
    const items = itemsFromMessages([
      { role: "user", content: "hi" },
      { role: "notice", kind: "compacted", text: "Context compacted — earlier turns were summarized" },
    ] as any);
    expect(items[1]).toEqual({
      kind: "notice",
      tone: "info",
      text: "Context compacted — earlier turns were summarized",
      // The engine's marker kind, carried through so counting compactions never means matching
      // their prose — the rail's "compacted 3x" reads this, not the text.
      notice: "compacted",
    });
  });
});

describe("itemsFromMessages reasoning", () => {
  it("attaches the reasoning sidecar to assistant items; thinking-only messages still render", () => {
    const items = itemsFromMessages([
      { role: "user", content: "hi" },
      { role: "assistant", content: "answer", reasoning: "let me think" },
      { role: "assistant", content: "", reasoning: "stopped mid-thought" },
    ] as any);
    expect(items[1]).toEqual({ kind: "assistant", text: "answer", reasoning: "let me think" });
    expect(items[2]).toEqual({ kind: "assistant", text: "", reasoning: "stopped mid-thought" });
  });
});

describe("itemsFromMessages mcp failure", () => {
  it("replays the persisted mcp_error marker as a collapsed notice WITHOUT retry", () => {
    // Legacy format (no `server` field): the name is recovered from the text and the
    // old plain-text Settings pointer is dropped — the Open Connectors button owns it.
    const items = itemsFromMessages([
      { role: "user", content: "hi" },
      { role: "notice", kind: "mcp_error", text: "MCP server “sales-db” failed to start — see Settings ▸ Connectors" },
    ] as any);
    expect(items[1]).toEqual({
      kind: "notice",
      tone: "warn",
      text: "MCP server “sales-db” didn’t start — its tools are unavailable here",
      server: "sales-db",
      detail: "MCP server “sales-db” failed to start",
    });
  });

  it("keeps an unparseable mcp_error text as a plain warn notice", () => {
    const items = itemsFromMessages([
      { role: "notice", kind: "mcp_error", text: "something opaque went wrong" },
    ] as any);
    expect(items[0]).toEqual({ kind: "notice", tone: "warn", text: "something opaque went wrong" });
  });

  it("renders a project_presence notice as one quiet info line, never an error", () => {
    const items = itemsFromMessages([
      {
        role: "notice",
        kind: "project_presence",
        text: "“notes” already has project memory (3 entries) — bind it by name or start a session there to use it.",
      },
    ] as any);
    expect(items[0]).toEqual({
      kind: "notice",
      tone: "info",
      text: "“notes” already has project memory (3 entries) — bind it by name or start a session there to use it.",
    });
  });
});
