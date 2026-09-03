import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const updateConnectorTools = vi.fn<
  (name: string, changes: Record<string, boolean>) => Promise<{ ok: boolean }>
>(async () => ({ ok: true }));
vi.mock("../../api", () => ({
  updateConnectorTools: (name: string, changes: Record<string, boolean>) =>
    updateConnectorTools(name, changes),
}));

import { ToolsDisclosure } from "./ToolsDisclosure";
import type { Connector } from "../../api";

// The connector tools list renders through the SAME primitives as the MCP review
// (ToolReview.tsx, owner ask 2026-08-30) — these tests pin the shared dialect:
// the chips explain themselves, the count line explains unchecking, saves leave
// a receipt.
const connector = (): Connector =>
  ({
    name: "attio",
    title: "Attio",
    connected: true,
    tools: [
      { name: "attio_list_objects", label: "List objects", description: "List Attio object types.", kind: "read", enabled: true },
      { name: "attio_log_note", label: "Log note", description: "Write a note onto a record.", kind: "write", enabled: true },
    ],
  }) as unknown as Connector;

afterEach(() => {
  cleanup();
  updateConnectorTools.mockClear();
});

describe("ToolsDisclosure — the shared tool-review dialect", () => {
  it("chips answer 'what happens when called?' and carry explanatory tooltips", () => {
    render(<ToolsDisclosure c={connector()} onChanged={vi.fn()} />);
    const read = screen.getByText("read");
    expect(read.getAttribute("title")).toContain("runs without an approval card");
    const asks = screen.getByText("asks first");
    expect(asks.getAttribute("title")).toContain("approval card before running");
  });

  it("the count line explains what unchecking means — same words as the MCP page", () => {
    render(<ToolsDisclosure c={connector()} onChanged={vi.fn()} />);
    // Exactly once — in the summary; the fine print carries only the explanations.
    expect(screen.getAllByText(/2 of 2 enabled/).length).toBe(1);
    expect(screen.getByText(/unchecked tools never reach a session/)).toBeTruthy();
  });

  it("a toggle saves immediately and leaves the Saved receipt", async () => {
    render(<ToolsDisclosure c={connector()} onChanged={vi.fn()} />);
    fireEvent.click(screen.getByTestId("connector-tool-check-attio-attio_log_note"));
    await waitFor(() =>
      expect(updateConnectorTools).toHaveBeenCalledWith("attio", { attio_log_note: false }),
    );
    await waitFor(() =>
      expect(screen.getByTestId("connector-tools-saved-attio")).toBeTruthy(),
    );
  });

  it("row tooltips disclose the real tool name behind the friendly label", () => {
    render(<ToolsDisclosure c={connector()} onChanged={vi.fn()} />);
    const row = screen.getByText("List objects").closest("label")!;
    expect(row.getAttribute("title")).toContain("attio_list_objects");
  });
});
