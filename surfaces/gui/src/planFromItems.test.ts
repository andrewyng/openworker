import { describe, expect, it } from "vitest";
import { planFromItems } from "./planFromItems";
import type { Item } from "./types";

// The plan and its age. The age is the whole reason this function exists: a list is only a
// reading of the run for as long as the model keeps rewriting it, and nothing makes it.
const write = (todos: unknown) => ({ kind: "tool", name: "todo_write", args: { todos } }) as any;
const call = (name: string) => ({ kind: "tool", name, args: {} }) as any;
const plan = (items: any[]) => planFromItems(items as Item[]);

describe("planFromItems", () => {
  it("takes the last list written, not the first", () => {
    const out = plan([
      write([{ content: "a", status: "in_progress" }]),
      write([
        { content: "a", status: "done" },
        { content: "b", status: "in_progress" },
      ]),
    ]);
    expect(out.items.map((t) => t.status)).toEqual(["done", "in_progress"]);
    expect(out.stepsSince).toBe(0);
  });

  it("counts the calls that have run since the list was written", () => {
    // The measured failure, in miniature: the plan is written, the run keeps going, and the
    // model never says another word about it.
    const out = plan([
      write([
        { content: "read the spec", status: "done" },
        { content: "extend preflight", status: "in_progress" },
        { content: "add the tests", status: "pending" },
      ]),
      ...Array.from({ length: 76 }, () => call("write_file")),
    ]);
    expect(out.items).toHaveLength(3);
    expect(out.stepsSince).toBe(76);
  });

  it("ages the plan across turns, because a plan outlives the turn that wrote it", () => {
    const out = plan([
      write([{ content: "a", status: "in_progress" }]),
      { kind: "assistant", text: "done for now" } as any,
      { kind: "user", text: "and now this" } as any,
      call("read_file"),
      call("grep"),
    ]);
    expect(out.stepsSince).toBe(2); // assistant/user turns are not steps; calls are
  });

  it("skips a todo_write whose arguments never parsed — but still counts it as a step", () => {
    // A truncated stream or a local model's malformed JSON reaches the transcript as a call
    // with empty args. Falling back to the previous list is right; pretending that list is
    // fresh would be the same lie this function exists to stop telling.
    const out = plan([
      write([{ content: "a", status: "in_progress" }]),
      { kind: "tool", name: "todo_write", args: {} } as any,
      call("read_file"),
    ]);
    expect(out.items.map((t) => t.content)).toEqual(["a"]);
    expect(out.stepsSince).toBe(2);
  });

  it("reads the pre-rename `items` key so old histories still render", () => {
    const out = planFromItems([
      { kind: "tool", name: "todo_write", args: { items: [{ content: "old", status: "done" }] } },
    ] as Item[]);
    expect(out.items).toEqual([{ content: "old", status: "done" }]);
  });

  it("normalizes what models actually send: bare strings and the `completed` alias", () => {
    const out = plan([write(["just a string", { content: "b", status: "completed" }])]);
    expect(out.items).toEqual([
      { content: "just a string", status: "pending" },
      { content: "b", status: "done" },
    ]);
  });

  it("is empty, and ageless, when no plan was ever written", () => {
    expect(plan([call("read_file"), call("grep")])).toEqual({ items: [], stepsSince: 0 });
  });
});
