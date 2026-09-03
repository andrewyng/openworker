import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor, fireEvent } from "@testing-library/react";

// OPE-136 §3 — the connect-time tool review: checkboxes decide EXISTENCE (an
// unchecked tool is never registered), saved as `include_tools`; server growth is
// fail-closed (new tools arrive unchecked, badged).

const getMcpTools =
  vi.fn<(name: string) => Promise<{ ok: boolean; error?: string; tools: { name: string; description: string }[] }>>();
const patchMcpServer = vi.fn<(name: string, changes: unknown) => Promise<{ ok: boolean }>>(
  async () => ({ ok: true }),
);
const getMcpTrust = vi.fn<
  (name: string) => Promise<{ ok: boolean; tools: string[]; legacy_dont_ask: boolean }>
>(async () => ({ ok: true, tools: [], legacy_dont_ask: false }));
const revokeMcpTrust = vi.fn<(name: string, tool: string) => Promise<{ ok: boolean }>>(
  async () => ({ ok: true }),
);
const convertMcpTrust = vi.fn<
  (name: string) => Promise<{ ok: boolean; trusted?: string[] }>
>(async () => ({ ok: true, trusted: [] }));
vi.mock("../../api", () => ({
  addMcpServer: vi.fn(),
  connectMcp: vi.fn(),
  deleteMcpServer: vi.fn(),
  getMcpTools: (name: string) => getMcpTools(name),
  patchMcpServer: (name: string, changes: unknown) => patchMcpServer(name, changes),
  getMcpTrust: (name: string) => getMcpTrust(name),
  revokeMcpTrust: (name: string, tool: string) => revokeMcpTrust(name, tool),
  convertMcpTrust: (name: string) => convertMcpTrust(name),
  signoutMcp: vi.fn(),
}));

import { McpToolReview } from "./CustomMcp";
import type { McpServer } from "../../api";

const OFFERED = [
  { name: "getIssue", description: "Read a Jira issue" },
  { name: "createIssue", description: "Create a Jira issue" },
  { name: "deleteIssue", description: "Delete an issue permanently" },
];

const server = (config: Record<string, unknown> = {}): McpServer => ({
  name: "jirax",
  enabled: true,
  transport: "http",
  requires_approval: true,
  status: "connected",
  tool_count: 3,
  config: { type: "http", url: "https://mcp.example.com/v1/mcp", ...config },
});

afterEach(() => {
  cleanup();
  getMcpTools.mockReset();
  patchMcpServer.mockClear();
  revokeMcpTrust.mockClear();
  convertMcpTrust.mockClear();
  getMcpTrust.mockReset();
  getMcpTrust.mockResolvedValue({ ok: true, tools: [], legacy_dont_ask: false });
});

describe("McpToolReview (OPE-136 §3)", () => {
  it("first review: auto-loads, everything checked, Keep writes the full include list", async () => {
    getMcpTools.mockResolvedValue({ ok: true, tools: OFFERED });
    render(<McpToolReview server={server()} onChanged={vi.fn()} />);
    await waitFor(() => expect(screen.getByTestId("mcp-tool-check-getIssue")).toBeTruthy());
    for (const t of OFFERED) {
      expect((screen.getByTestId(`mcp-tool-check-${t.name}`) as HTMLInputElement).checked).toBe(true);
    }
    // Saving with everything checked still writes the list — that is what locks in
    // fail-closed growth (a later server tool won't be on it).
    fireEvent.click(screen.getByTestId("mcp-tools-save-jirax"));
    await waitFor(() =>
      expect(patchMcpServer).toHaveBeenCalledWith("jirax", {
        include_tools: ["getIssue", "createIssue", "deleteIssue"],
        exclude_tools: [],
      }),
    );
  });

  it("unchecking a tool drops it from the saved list, preserving server order", async () => {
    getMcpTools.mockResolvedValue({ ok: true, tools: OFFERED });
    render(<McpToolReview server={server()} onChanged={vi.fn()} />);
    await waitFor(() => expect(screen.getByTestId("mcp-tool-check-deleteIssue")).toBeTruthy());
    fireEvent.click(screen.getByTestId("mcp-tool-check-deleteIssue"));
    fireEvent.click(screen.getByTestId("mcp-tools-save-jirax"));
    await waitFor(() =>
      expect(patchMcpServer).toHaveBeenCalledWith("jirax", {
        include_tools: ["getIssue", "createIssue"],
        // The first-review ceremony reviews the whole list: the uncheck is an
        // explicit decline, recorded so it never wears the "new" badge later.
        exclude_tools: ["deleteIssue"],
      }),
    );
  });

  it("a tool the server added after the review arrives UNCHECKED with a new badge", async () => {
    getMcpTools.mockResolvedValue({ ok: true, tools: OFFERED });
    render(
      <McpToolReview
        server={server({ include_tools: ["getIssue", "createIssue"] })}
        onChanged={vi.fn()}
      />,
    );
    await waitFor(() => expect(screen.getByTestId("mcp-tool-check-deleteIssue")).toBeTruthy());
    expect((screen.getByTestId("mcp-tool-check-deleteIssue") as HTMLInputElement).checked).toBe(false);
    expect(screen.getByTestId("mcp-tool-new-deleteIssue")).toBeTruthy();
    expect((screen.getByTestId("mcp-tool-check-getIssue") as HTMLInputElement).checked).toBe(true);
  });

  it("after the first review, toggles auto-save — no button, a Saved tick instead", async () => {
    getMcpTools.mockResolvedValue({ ok: true, tools: OFFERED });
    render(
      <McpToolReview
        server={server({ include_tools: ["getIssue", "createIssue", "deleteIssue"] })}
        onChanged={vi.fn()}
      />,
    );
    await waitFor(() => expect(screen.getByTestId("mcp-tool-check-deleteIssue")).toBeTruthy());
    fireEvent.click(screen.getByTestId("mcp-tool-check-deleteIssue"));
    // The write happens without any save button click…
    await waitFor(() =>
      expect(patchMcpServer).toHaveBeenCalledWith("jirax", {
        include_tools: ["getIssue", "createIssue"],
        exclude_tools: ["deleteIssue"],
      }),
    );
    expect(screen.queryByTestId("mcp-tools-save-jirax")).toBeNull();
    // …and leaves a visible receipt.
    await waitFor(() => expect(screen.getByTestId("mcp-tools-saved-jirax")).toBeTruthy());
  });

  it("a declined tool is remembered as DECLINED — absent, but never 'new'", async () => {
    // Owner catch 2026-08-30: unchecking a tool earned it the "new" badge, because
    // the include list alone can't tell "you said no" from "the server added it".
    getMcpTools.mockResolvedValue({ ok: true, tools: OFFERED });
    render(
      <McpToolReview
        server={server({ include_tools: ["getIssue", "createIssue"], exclude_tools: ["deleteIssue"] })}
        onChanged={vi.fn()}
      />,
    );
    await waitFor(() => expect(screen.getByTestId("mcp-tool-check-deleteIssue")).toBeTruthy());
    expect((screen.getByTestId("mcp-tool-check-deleteIssue") as HTMLInputElement).checked).toBe(false);
    expect(screen.queryByTestId("mcp-tool-new-deleteIssue")).toBeNull();
  });

  it("an autosaved uncheck moves ONLY the toggled tool — server-new tools keep their badge", async () => {
    // deleteIssue is server-new (on neither list). Unchecking createIssue must not
    // sweep deleteIssue into the declined list — a whole-list recompute would
    // silently convert "new, needs review" into "declined".
    getMcpTools.mockResolvedValue({ ok: true, tools: OFFERED });
    render(
      <McpToolReview
        server={server({ include_tools: ["getIssue", "createIssue"] })}
        onChanged={vi.fn()}
      />,
    );
    await waitFor(() => expect(screen.getByTestId("mcp-tool-check-createIssue")).toBeTruthy());
    fireEvent.click(screen.getByTestId("mcp-tool-check-createIssue"));
    await waitFor(() =>
      expect(patchMcpServer).toHaveBeenCalledWith("jirax", {
        include_tools: ["getIssue"],
        exclude_tools: ["createIssue"],
      }),
    );
    expect(screen.getByTestId("mcp-tool-new-deleteIssue")).toBeTruthy();
  });

  it("a reviewed list collapses to the connector-style summary row; first review stays open", async () => {
    // One dialect (owner ask 2026-08-30): after the ceremony the MCP list wears the
    // same quiet "› Tools · N of M enabled" summary the connector pages use.
    getMcpTools.mockResolvedValue({ ok: true, tools: OFFERED });
    render(
      <McpToolReview
        server={server({ include_tools: ["getIssue", "createIssue"] })}
        onChanged={vi.fn()}
      />,
    );
    await waitFor(() => expect(screen.getByText("› Tools")).toBeTruthy());
    // Exactly once — in the summary; the fine print carries only the explanations
    // (owner catch 2026-08-30: the count printed twice in adjacent lines).
    expect(screen.getAllByText(/2 of 3 enabled/).length).toBe(1);
    // The shared fine print is present in the expandable content.
    expect(screen.getByText(/unchecked tools never reach a session/)).toBeTruthy();
    expect(screen.getByText(/every tool asks before running unless always-allowed/)).toBeTruthy();
    cleanup();

    // First review: no summary row — the ceremony IS the open list.
    getMcpTools.mockResolvedValue({ ok: true, tools: OFFERED });
    render(<McpToolReview server={server()} onChanged={vi.fn()} />);
    await waitFor(() => expect(screen.getByTestId("mcp-tool-check-getIssue")).toBeTruthy());
    expect(screen.queryByText("› Tools")).toBeNull();
    expect(screen.getByTestId("mcp-tools-save-jirax")).toBeTruthy();
  });

  it("a saved review with no changes offers no save button", async () => {
    getMcpTools.mockResolvedValue({
      ok: true,
      tools: OFFERED.slice(0, 2),
    });
    render(
      <McpToolReview
        server={server({ include_tools: ["getIssue", "createIssue"] })}
        onChanged={vi.fn()}
      />,
    );
    await waitFor(() => expect(screen.getByTestId("mcp-tool-check-getIssue")).toBeTruthy());
    expect(screen.queryByTestId("mcp-tools-save-jirax")).toBeNull();
  });

  it("descriptions render as the server's own words, quoted", async () => {
    getMcpTools.mockResolvedValue({ ok: true, tools: OFFERED.slice(0, 1) });
    render(<McpToolReview server={server()} onChanged={vi.fn()} />);
    await waitFor(() => expect(screen.getByText(/“Read a Jira issue”/)).toBeTruthy());
  });
});

// OPE-136 §4/§5 — standing trust on the review list, and the legacy flag made loud.
describe("McpToolReview — trust markers and the legacy flag", () => {
  it("a trusted tool shows the Always-allowed chip; Revoke removes the rule", async () => {
    getMcpTools.mockResolvedValue({ ok: true, tools: OFFERED });
    getMcpTrust.mockResolvedValue({ ok: true, tools: ["getIssue"], legacy_dont_ask: false });
    render(<McpToolReview server={server({ include_tools: ["getIssue", "createIssue"] })} onChanged={vi.fn()} />);
    await waitFor(() => expect(screen.getByTestId("mcp-tool-trusted-getIssue")).toBeTruthy());
    expect(screen.queryByTestId("mcp-tool-trusted-createIssue")).toBeNull();
    // The chip echoes the card button that created it, and the tooltip closes the
    // loop. Lowercase, like its read/asks-first tag siblings (owner call 2026-08-30).
    expect(screen.getByText("always allowed")).toBeTruthy();
    expect(screen.getByText("always allowed").getAttribute("title")).toContain(
      "You chose 'Always allow this tool'",
    );
    // Granted authority surfaces in the summary line before anyone scrolls.
    expect(screen.getByText(/1 always allowed/)).toBeTruthy();
    fireEvent.click(screen.getByText("Revoke"));
    await waitFor(() => expect(revokeMcpTrust).toHaveBeenCalledWith("jirax", "getIssue"));
  });

  it("an unchecked-but-trusted tool keeps a dimmed idle chip — standing rules never vanish", async () => {
    getMcpTools.mockResolvedValue({ ok: true, tools: OFFERED });
    getMcpTrust.mockResolvedValue({ ok: true, tools: ["getIssue"], legacy_dont_ask: false });
    // getIssue is trusted but NOT in include_tools → it can never be called, yet the
    // rule still exists on disk and must stay visible on the screen that audits it.
    render(<McpToolReview server={server({ include_tools: ["createIssue"] })} onChanged={vi.fn()} />);
    await waitFor(() => expect(screen.getByTestId("mcp-tool-trusted-getIssue")).toBeTruthy());
    const marker = screen.getByTestId("mcp-tool-trusted-getIssue");
    expect(marker.className).toContain("opacity-50");
    expect(screen.getByText("always allowed").getAttribute("title")).toContain("Idle");
  });

  it("the legacy don't-ask flag shows the loud banner; convert calls the migration", async () => {
    getMcpTools.mockResolvedValue({ ok: true, tools: OFFERED });
    getMcpTrust.mockResolvedValue({ ok: true, tools: [], legacy_dont_ask: true });
    render(<McpToolReview server={server()} onChanged={vi.fn()} />);
    await waitFor(() => expect(screen.getByTestId("mcp-legacy-warn-jirax")).toBeTruthy());
    expect(screen.getByText("This server never asks")).toBeTruthy();
    fireEvent.click(screen.getByTestId("mcp-legacy-convert-jirax"));
    await waitFor(() => expect(convertMcpTrust).toHaveBeenCalledWith("jirax"));
  });

  it("no banner when the config is clean", async () => {
    getMcpTools.mockResolvedValue({ ok: true, tools: OFFERED });
    getMcpTrust.mockResolvedValue({ ok: true, tools: [], legacy_dont_ask: false });
    render(<McpToolReview server={server()} onChanged={vi.fn()} />);
    await waitFor(() => expect(screen.getByTestId("mcp-tool-check-getIssue")).toBeTruthy());
    expect(screen.queryByTestId("mcp-legacy-warn-jirax")).toBeNull();
  });
});
