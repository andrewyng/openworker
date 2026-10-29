import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { Transcript } from "./Transcript";
import { MAX_ROWS, errorOf, exitCodeOf, patchSummary, summarize } from "./UnaskedSummary";
import type { Item } from "../types";

afterEach(cleanup);

// OPE-238: after a turn in which auto-approve let actions run with no card (a gate tier or
// a reviewer "allow"), the user sees every one of them, with diffs, without opening the
// audit log. Collapsed at rest; one click expands; each item links to its audit rows.
const TURN: Item[] = [
  { kind: "user", text: "tidy the notes and run the tests" },
  { kind: "tool", id: "t1", callId: "c1", name: "read_file", args: { path: "notes.md" }, status: "ok" },
  {
    kind: "tool",
    id: "t2",
    callId: "c2",
    name: "write_file",
    args: { path: "docs/summary.md", content: "# Summary\nThree tasks done." },
    status: "ok",
    approvalOrigin: "gate_tier",
  },
  {
    kind: "tool",
    id: "t3",
    callId: "c3",
    name: "replace_in_file",
    args: { path: "src/app.py", old: "DEBUG = True", new: "DEBUG = False" },
    status: "ok",
    approvalOrigin: "reviewer",
    approvalNote: "matches the request",
  },
  {
    kind: "tool",
    id: "t4",
    callId: "c4",
    name: "run_shell",
    args: { command: "pytest -q" },
    status: "ok",
    preview: '{"command": "pytest -q", "cwd": "/w", "exit_code": 0, "output": "5 passed"}',
    approvalOrigin: "reviewer",
  },
  // Approved by the user on a card: not "without asking".
  { kind: "approval", name: "run_shell", args: { command: "make deploy" }, reason: "", resolved: "once" },
  { kind: "tool", id: "t5", callId: "c5", name: "run_shell", args: { command: "make deploy" }, status: "ok", approvalOrigin: "user" },
  { kind: "assistant", text: "Done." },
];

function openSummary() {
  fireEvent.click(screen.getByTestId("unasked-summary-head"));
  return screen.getByTestId("unasked-summary-body");
}

describe("changes auto-approved (OPE-238)", () => {
  it("is collapsed at rest and counts only the unasked actions", () => {
    render(<Transcript items={TURN} onApprove={vi.fn()} />);
    expect(screen.getByTestId("unasked-summary-head").textContent).toContain("3 changes auto-approved");
    expect(screen.queryByTestId("unasked-summary-body")).toBeNull();
    expect(screen.queryByText("Three tasks done.")).toBeNull();
  });

  it("sits after the turn's answer", () => {
    const { container } = render(<Transcript items={TURN} onApprove={vi.fn()} />);
    const answer = screen.getByText("Done.");
    const summary = screen.getByTestId("unasked-summary");
    // DOCUMENT_POSITION_FOLLOWING: the summary comes after the answer bubble.
    expect(answer.compareDocumentPosition(summary) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(container.querySelectorAll("[data-testid=unasked-summary]").length).toBe(1);
  });

  it("expands to file changes with diffs and commands with their exit status, grouped by kind", () => {
    render(<Transcript items={TURN} onApprove={vi.fn()} />);
    const body = openSummary();

    const files = within(body).getByTestId("unasked-group-files");
    const commands = within(body).getByTestId("unasked-group-commands");
    expect(within(files).getAllByTestId("unasked-item").length).toBe(2);
    expect(within(commands).getAllByTestId("unasked-item").length).toBe(1);

    // write_file: the written content, as added lines.
    const added = Array.from(files.querySelectorAll(".diff-add")).map((n) => n.textContent);
    expect(added).toContain("+# Summary");
    expect(added).toContain("+Three tasks done.");
    // replace_in_file: old text removed, new text added.
    const removed = Array.from(files.querySelectorAll(".diff-del")).map((n) => n.textContent);
    expect(removed).toContain("-DEBUG = True");
    expect(added).toContain("+DEBUG = False");
    // The full path, not just the base name: the user is checking what changed where.
    expect(files.textContent).toContain("docs/summary.md");
    expect(files.textContent).toContain("src/app.py");

    // The command, with its exit status read from the result.
    expect(commands.textContent).toContain("pytest -q");
    expect(within(commands).getByTestId("unasked-exit").textContent).toBe("exit 0");

    // Who let each one through.
    expect(files.textContent).toContain("safe change");
    expect(body.textContent).toContain("reviewer");

    // The user-approved deploy is not listed.
    expect(body.textContent).not.toContain("make deploy");
  });

  it("collapses again on a second click", () => {
    render(<Transcript items={TURN} onApprove={vi.fn()} />);
    openSummary();
    fireEvent.click(screen.getByTestId("unasked-summary-head"));
    expect(screen.queryByTestId("unasked-summary-body")).toBeNull();
  });

  it("links each item to its audit rows by tool call", () => {
    const onOpenAudit = vi.fn();
    render(<Transcript items={TURN} onApprove={vi.fn()} onOpenAudit={onOpenAudit} />);
    const body = openSummary();
    const links = within(body).getAllByTestId("unasked-audit");
    expect(links.length).toBe(3);
    fireEvent.click(links[2]);
    expect(onOpenAudit).toHaveBeenCalledWith("c4");
  });

  it("shows no audit link where the app gives none", () => {
    render(<Transcript items={TURN} onApprove={vi.fn()} />);
    expect(within(openSummary()).queryByTestId("unasked-audit")).toBeNull();
  });

  it("a failed command shows its exit status as a failure", () => {
    const items: Item[] = [
      { kind: "user", text: "run the tests" },
      {
        kind: "tool",
        id: "t1",
        callId: "c1",
        name: "run_shell",
        args: { command: "pytest -q" },
        status: "ok",
        preview: '{"command": "pytest -q", "exit_code": 1, "output": "1 failed"}',
        approvalOrigin: "reviewer",
      },
      { kind: "assistant", text: "One test fails." },
    ];
    render(<Transcript items={items} onApprove={vi.fn()} />);
    const exit = within(openSummary()).getByTestId("unasked-exit");
    expect(exit.textContent).toBe("exit 1");
    expect(exit.className).toContain("text-danger");
  });

  it("a patch shows its own added and removed lines", () => {
    const patch = "*** Begin Patch\n*** Update File: a.txt\n@@\n-old line\n+new line\n*** End Patch";
    const items: Item[] = [
      { kind: "user", text: "fix a.txt" },
      { kind: "tool", id: "t1", callId: "c1", name: "apply_patch", args: { patch }, status: "ok", approvalOrigin: "reviewer" },
      { kind: "assistant", text: "Fixed." },
    ];
    const { container } = render(<Transcript items={items} onApprove={vi.fn()} />);
    expect(screen.getByTestId("unasked-summary-head").textContent).toContain("1 change auto-approved");
    openSummary();
    expect(Array.from(container.querySelectorAll(".diff-del")).map((n) => n.textContent)).toContain("-old line");
    expect(Array.from(container.querySelectorAll(".diff-add")).map((n) => n.textContent)).toContain("+new line");
  });

  it("other actions the reviewer allowed are listed under their own heading", () => {
    const items: Item[] = [
      { kind: "user", text: "post the digest" },
      {
        kind: "tool",
        id: "t1",
        callId: "c1",
        name: "send_message",
        args: { target: "slack:T1/C9", text: "hi" },
        status: "ok",
        approvalOrigin: "reviewer",
      },
      { kind: "assistant", text: "Posted." },
    ];
    render(<Transcript items={items} onApprove={vi.fn()} />);
    const other = within(openSummary()).getByTestId("unasked-group-other");
    expect(other.textContent).toContain("Slack");
  });

  it("is not shown while the turn is still running", () => {
    const live = TURN.slice(0, -1); // no answer yet
    render(<Transcript items={live} onApprove={vi.fn()} running />);
    expect(screen.queryByTestId("unasked-summary")).toBeNull();
  });

  it("is not shown when every action was asked or allowed by the mode", () => {
    const items: Item[] = [
      { kind: "user", text: "read the notes" },
      { kind: "tool", id: "t1", callId: "c1", name: "read_file", args: { path: "notes.md" }, status: "ok" },
      { kind: "tool", id: "t2", callId: "c2", name: "run_shell", args: { command: "ls" }, status: "ok", approvalOrigin: "bypass" },
      { kind: "assistant", text: "Read." },
    ];
    render(<Transcript items={items} onApprove={vi.fn()} />);
    expect(screen.queryByTestId("unasked-summary")).toBeNull();
  });

  it("a denied call is never listed as a change", () => {
    const items: Item[] = [
      { kind: "user", text: "clean up" },
      { kind: "tool", id: "t1", callId: "c1", name: "run_shell", args: { command: "rm -rf build" }, status: "denied", approvalOrigin: "reviewer_denied" },
      { kind: "assistant", text: "Not allowed." },
    ];
    render(<Transcript items={items} onApprove={vi.fn()} />);
    expect(screen.queryByTestId("unasked-summary")).toBeNull();
  });

  it("each turn gets its own summary", () => {
    const items: Item[] = [
      ...TURN,
      { kind: "user", text: "again" },
      { kind: "tool", id: "t9", callId: "c9", name: "run_shell", args: { command: "ruff check" }, status: "ok", approvalOrigin: "reviewer" },
      { kind: "assistant", text: "Clean." },
    ];
    render(<Transcript items={items} onApprove={vi.fn()} />);
    const heads = screen.getAllByTestId("unasked-summary-head").map((h) => h.textContent);
    expect(heads.length).toBe(2);
    expect(heads[0]).toContain("3 changes");
    expect(heads[1]).toContain("1 change auto-approved");
  });
});

// A patch names its files in its headers. The summary says what happened to each one,
// instead of "Edited files" (seen live: a patch that only deleted old-notes.txt read as
// "Edited files", 26 Oct 2026).
describe("patch rows name their files", () => {
  const patchTurn = (name: string, args: any): Item[] => [
    { kind: "user", text: "tidy up" },
    { kind: "tool", id: "t1", callId: "c1", name, args, status: "ok", approvalOrigin: "reviewer" },
    { kind: "assistant", text: "Done." },
  ];
  const rowText = (items: Item[]) => {
    render(<Transcript items={items} onApprove={vi.fn()} />);
    fireEvent.click(screen.getByTestId("unasked-summary-head"));
    return within(screen.getByTestId("unasked-group-files")).getByTestId("unasked-item-line").textContent;
  };

  it("a patch that only deletes a file says Deleted <file>", () => {
    const patch = "*** Begin Patch\n*** Delete File: old-notes.txt\n*** End Patch";
    const text = rowText(patchTurn("apply_patch", { patch }));
    expect(text).toBe("Deleted old-notes.txt");
    expect(text).not.toContain("Edited files");
  });

  it("a patch touching several files names each with what happened to it", () => {
    const patch =
      "*** Begin Patch\n*** Add File: summary.txt\n+done\n*** Update File: notes.md\n@@\n-a\n+b\n" +
      "*** Delete File: old-notes.txt\n*** Update File: draft.md\n*** Move to: final.md\n@@\n-x\n+y\n*** End Patch";
    expect(rowText(patchTurn("apply_patch", { patch }))).toBe(
      "Created summary.txt · Edited notes.md · Deleted old-notes.txt · Moved draft.md to final.md",
    );
  });

  it("a unified diff that removes a file says Deleted <file>", () => {
    const diff = "--- a/old-notes.txt\n+++ /dev/null\n@@ -1 +0,0 @@\n-old\n";
    expect(rowText(patchTurn("apply_unified_diff", { diff }))).toBe("Deleted old-notes.txt");
  });

  it("a unified diff that creates or edits files names them", () => {
    const diff =
      "--- /dev/null\n+++ b/summary.txt\n@@ -0,0 +1 @@\n+done\n" +
      "--- a/notes.md\n+++ b/notes.md\n@@ -1 +1 @@\n-a\n+b\n";
    expect(rowText(patchTurn("apply_unified_diff", { diff }))).toBe("Created summary.txt · Edited notes.md");
  });

  it("a write to one file still shows its path, as before", () => {
    const text = rowText(patchTurn("write_file", { path: "docs/summary.md", content: "x" }));
    expect(text).toBe("Wrote docs/summary.md");
  });
});

describe("patchSummary", () => {
  it("reads Codex-style patch headers", () => {
    expect(patchSummary("apply_patch", { patch: "*** Begin Patch\n*** Delete File: a.txt\n*** End Patch" })).toEqual([
      { verb: "deleted", path: "a.txt" },
    ]);
  });

  it("returns nothing for a patch with no headers it understands", () => {
    expect(patchSummary("apply_patch", { patch: "garbage" })).toEqual([]);
    expect(patchSummary("apply_patch", {})).toEqual([]);
    expect(patchSummary("write_file", { path: "a.txt" })).toEqual([]);
  });
});

describe("exitCodeOf", () => {
  it("reads the exit code from a JSON result, a truncated one, or not at all", () => {
    expect(exitCodeOf('{"exit_code": 0}')).toBe(0);
    expect(exitCodeOf('{"command": "x", "cwd": "/w", "exit_code": 2, "output": "unterminated')).toBe(2);
    expect(exitCodeOf("ran: ls")).toBeNull();
    expect(exitCodeOf(undefined)).toBeNull();
    expect(exitCodeOf('{"exit_code": null}')).toBeNull();
  });
});


// The count means "things that changed state and that nobody approved". A failed attempt
// changed nothing; a read-only action is listed apart; a background start is neither.
describe("the count is honest", () => {
  const turn = (...tools: Item[]): Item[] => [{ kind: "user", text: "work" }, ...tools, { kind: "assistant", text: "Done." }];
  const shell = (id: string, command: string, extra: Partial<Extract<Item, { kind: "tool" }>> = {}): Item => ({
    kind: "tool",
    id,
    callId: "c_" + id,
    name: "run_shell",
    args: { command },
    status: "ok",
    approvalOrigin: "reviewer",
    ...extra,
  });

  it("a command that exited non-zero is a failure, not a change, even when the preview hides its exit code", () => {
    const long = "python -c '" + "x = 1; ".repeat(80) + "'";
    render(
      <Transcript
        items={turn(shell("a", "pytest -q", { exitCode: 0 }), shell("b", long, { exitCode: 1, preview: '{"command": "' + long + '"...' }))}
        onApprove={vi.fn()}
      />,
    );
    const head = screen.getByTestId("unasked-summary-head");
    expect(head.textContent).toContain("1 change auto-approved");
    expect(screen.getByTestId("unasked-failed-count").textContent).toContain("1 failed");
    openSummary();
    const exits = screen.getAllByTestId("unasked-exit").map((e) => e.textContent);
    expect(exits).toEqual(["exit 0", "exit 1"]);
  });

  it("a timed-out command is a failure and says so", () => {
    render(<Transcript items={turn(shell("a", "sleep 999", { exitCode: null, timedOut: true }))} onApprove={vi.fn()} />);
    expect(screen.getByTestId("unasked-summary-head").textContent).toContain("No changes auto-approved");
    expect(screen.getByTestId("unasked-failed-count").textContent).toContain("1 failed");
    openSummary();
    const exit = screen.getByTestId("unasked-exit");
    expect(exit.textContent).toBe("timed out");
    expect(exit.className).toContain("text-danger");
  });

  it("a command started in the background is neither done nor failed", () => {
    render(<Transcript items={turn(shell("a", "npm run dev", { background: true }), shell("b", "pytest -q", { exitCode: 0 }))} onApprove={vi.fn()} />);
    expect(screen.getByTestId("unasked-summary-head").textContent).toContain("2 changes auto-approved");
    expect(screen.queryByTestId("unasked-failed-count")).toBeNull();
    openSummary();
    expect(screen.getByTestId("unasked-background").textContent).toBe("started in the background");
    expect(screen.queryAllByTestId("unasked-exit").length).toBe(1);
  });

  it("a write that failed is a failure with its error, not a green change", () => {
    const items = turn({
      kind: "tool",
      id: "w",
      callId: "c_w",
      name: "write_file",
      args: { path: "/tmp/gen_poc.py", content: "x" },
      status: "error",
      preview: '{"error": "Path escapes allowed roots: /tmp/gen_poc.py", "error_type": "ValueError"}',
      approvalOrigin: "reviewer",
    });
    render(<Transcript items={items} onApprove={vi.fn()} />);
    expect(screen.getByTestId("unasked-summary-head").textContent).toContain("No changes auto-approved · 1 failed");
    const body = openSummary();
    expect(within(body).getByTestId("unasked-failed").textContent).toContain("failed · Path escapes allowed roots");
  });

  it("read-only actions are listed apart and never counted", () => {
    const items = turn(
      shell("r1", "cat notes.md", { approvalOrigin: "gate_tier", tier: "readonly_command", effect: "read" }),
      shell("r2", "git status", { approvalOrigin: "gate_tier", tier: "readonly_command", effect: "read" }),
      shell("w", "pytest -q", { exitCode: 0, effect: "change" }),
    );
    render(<Transcript items={items} onApprove={vi.fn()} />);
    const head = screen.getByTestId("unasked-summary-head");
    expect(head.textContent).toContain("1 change auto-approved");
    expect(screen.getByTestId("unasked-read-count").textContent).toContain("2 read-only");
    const body = openSummary();
    expect(within(body).getAllByTestId("unasked-item").length).toBe(1);
    expect(body.textContent).not.toContain("cat notes.md");
    fireEvent.click(within(body).getByTestId("unasked-reads-toggle"));
    const reads = within(body).getAllByTestId("unasked-read-item").map((n) => n.textContent);
    expect(reads[0]).toContain("cat notes.md");
    expect(reads[1]).toContain("git status");
  });

  it("a turn that only read things shows nothing at all", () => {
    render(
      <Transcript
        items={turn(shell("r1", "cat notes.md", { approvalOrigin: "gate_tier", tier: "readonly_command", effect: "read" }))}
        onApprove={vi.fn()}
      />,
    );
    expect(screen.queryByTestId("unasked-summary")).toBeNull();
  });

  it("summarize() is the same rule the engine applies", () => {
    const s = summarize([
      shell("a", "x", { exitCode: 0 }),
      shell("b", "y", { exitCode: 2 }),
      shell("c", "z", { effect: "read" }),
      { ...shell("d", "w"), status: "error" },
    ] as any);
    expect([s.count, s.failed, s.reads.length, s.changes.length]).toEqual([1, 2, 1, 3]);
  });
});

describe("rows stay readable", () => {
  it("several edits of one file collapse into one row with stacked diffs and a link per edit", () => {
    const edit = (id: string, old: string, neu: string): Item => ({
      kind: "tool",
      id,
      callId: "c_" + id,
      name: "replace_in_file",
      args: { path: "src/app.py", old, new: neu },
      status: "ok",
      approvalOrigin: "reviewer",
    });
    const items: Item[] = [
      { kind: "user", text: "turn debug off" },
      edit("e1", "DEBUG = True", "DEBUG = False"),
      { kind: "tool", id: "o", callId: "c_o", name: "write_file", args: { path: "docs/x.md", content: "hi" }, status: "ok", approvalOrigin: "gate_tier" },
      edit("e2", 'LOG = "debug"', 'LOG = "info"'),
      { kind: "assistant", text: "Done." },
    ];
    const onOpenAudit = vi.fn();
    render(<Transcript items={items} onApprove={vi.fn()} onOpenAudit={onOpenAudit} />);
    // Three calls, three changes: the count is per change, the rows are per file.
    expect(screen.getByTestId("unasked-summary-head").textContent).toContain("3 changes auto-approved");
    const files = within(openSummary()).getByTestId("unasked-group-files");
    const rows = within(files).getAllByTestId("unasked-item");
    expect(rows.length).toBe(2);
    expect(within(rows[0]).getByTestId("unasked-item-line").textContent).toBe("Edited src/app.py · 2 edits");
    expect(within(rows[0]).getAllByTestId("unasked-diff").length).toBe(2);
    const links = within(rows[0]).getAllByTestId("unasked-audit");
    expect(links.length).toBe(2);
    fireEvent.click(links[1]);
    expect(onOpenAudit).toHaveBeenCalledWith("c_e2");
  });

  it("a long list shows the first rows and offers the rest", () => {
    const many: Item[] = Array.from({ length: MAX_ROWS + 5 }, (_, i) => ({
      kind: "tool" as const,
      id: "s" + i,
      callId: "c" + i,
      name: "run_shell",
      args: { command: "cmd " + i },
      status: "ok",
      exitCode: 0,
      approvalOrigin: "reviewer",
    }));
    render(<Transcript items={[{ kind: "user", text: "go" }, ...many, { kind: "assistant", text: "Done." }]} onApprove={vi.fn()} />);
    const body = openSummary();
    expect(within(body).getAllByTestId("unasked-item").length).toBe(MAX_ROWS);
    const more = within(body).getByTestId("unasked-show-all");
    expect(more.textContent).toContain(String(MAX_ROWS + 5));
    fireEvent.click(more);
    expect(within(body).getAllByTestId("unasked-item").length).toBe(MAX_ROWS + 5);
  });
});

// A notice inside a turn (compaction, continuation, a saved memory) breaks the step group,
// not the turn: one summary per turn, after its answer, and never while it is running.
describe("one summary per turn", () => {
  const first: Item = { kind: "tool", id: "t1", callId: "c1", name: "run_shell", args: { command: "pytest -q" }, status: "ok", exitCode: 0, approvalOrigin: "reviewer" };
  const notice: Item = { kind: "notice", tone: "info", text: "Context compacted" };
  const second: Item = { kind: "tool", id: "t2", callId: "c2", name: "run_shell", args: { command: "ruff check" }, status: "ok", exitCode: 0, approvalOrigin: "reviewer" };

  it("nothing shows while the turn is running, even after a mid-turn notice", () => {
    render(<Transcript items={[{ kind: "user", text: "go" }, first, notice, { ...second, status: "…" }]} running onApprove={vi.fn()} />);
    expect(screen.queryByTestId("unasked-summary")).toBeNull();
  });

  it("a finished turn with a mid-turn notice gets one summary, after the answer, counting both halves", () => {
    render(<Transcript items={[{ kind: "user", text: "go" }, first, notice, second, { kind: "assistant", text: "Done." }]} onApprove={vi.fn()} />);
    const heads = screen.getAllByTestId("unasked-summary-head");
    expect(heads.length).toBe(1);
    expect(heads[0].textContent).toContain("2 changes auto-approved");
    const answer = screen.getByText("Done.");
    expect(answer.compareDocumentPosition(screen.getByTestId("unasked-summary")) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("the previous turn's summary sits before the next user message while the new turn runs", () => {
    render(
      <Transcript
        items={[{ kind: "user", text: "go" }, first, { kind: "assistant", text: "Done." }, { kind: "user", text: "again" }, { ...second, status: "…" }]}
        running
        onApprove={vi.fn()}
      />,
    );
    const heads = screen.getAllByTestId("unasked-summary-head");
    expect(heads.length).toBe(1);
    expect(heads[0].textContent).toContain("1 change auto-approved");
    const next = screen.getByText("again");
    expect(screen.getByTestId("unasked-summary").compareDocumentPosition(next) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
});

describe("errorOf", () => {
  it("reads the error text of a failed result, whole or cut short", () => {
    expect(errorOf('{"error": "Path escapes allowed roots: /tmp/x", "error_type": "ValueError"}')).toBe("Path escapes allowed roots: /tmp/x");
    expect(errorOf('{"error": "tool call not executed", "reason": "denied"}')).toBe("denied");
    expect(errorOf('{"error": "boom", "error_type": "Runt')).toBe("boom");
    expect(errorOf("plain text")).toBe("");
    expect(errorOf(undefined)).toBe("");
  });
});
