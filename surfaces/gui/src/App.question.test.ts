import { describe, expect, it } from "vitest";
import { resolveLastQuestion } from "./App";
import type { Item } from "./types";

describe("question lifecycle", () => {
  it("closes the latest unresolved question when its tool call finishes", () => {
    const items: Item[] = [
      { kind: "question", question: "First?", resolved: "yes" },
      { kind: "assistant", text: "Continuing" },
      { kind: "question", question: "Second?" },
    ];

    const resolved = resolveLastQuestion(items, "interrupted by user");

    expect(resolved[0]).toEqual(items[0]);
    expect(resolved[2]).toEqual({
      kind: "question",
      question: "Second?",
      resolved: "interrupted by user",
    });
    expect(items[2]).toEqual({ kind: "question", question: "Second?" });
  });
});
