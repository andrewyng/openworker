import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { McpServerDetail } from "./CustomMcp";
import type { McpServer } from "../../api";
import { getMcpServers, replaceMcpServer } from "../../api";

vi.mock("../../api", async (original) => ({
  ...await original<typeof import("../../api")>(),
  getMcpServers: vi.fn(),
  getMcpTools: vi.fn(async () => ({ ok: true, tools: [] })),
  getMcpTrust: vi.fn(async () => ({ ok: true, tools: [], legacy_dont_ask: false })),
  replaceMcpServer: vi.fn(),
}));

const row: McpServer = {
  name: "example", enabled: true, transport: "http", requires_approval: true,
  status: "connected", tool_count: 2,
  config: { url: "https://old.test/mcp", headers: { Authorization: "***" } },
};

beforeEach(() => {
  vi.mocked(getMcpServers).mockResolvedValue([structuredClone(row)]);
  vi.mocked(replaceMcpServer).mockReset();
});
afterEach(cleanup);

function Detail() {
  const [server, setServer] = useState(structuredClone(row));
  return <McpServerDetail server={server} onGone={vi.fn()} onChanged={async () => {
    const servers = await getMcpServers();
    setServer(servers[0]);
  }} />;
}

async function edit() {
  render(<Detail />);
  fireEvent.click(await screen.findByRole("button", { name: "Edit example" }));
  return screen.getByLabelText("Configuration JSON") as HTMLTextAreaElement;
}

it("edits the masked existing config and saves a full replacement with reload feedback", async () => {
  const input = await edit();
  expect(JSON.parse(input.value)).toEqual(row.config);
  expect(document.activeElement).toBe(input);
  const next = { url: "https://new.test/mcp", headers: { Authorization: "***" } };
  fireEvent.change(input, { target: { value: JSON.stringify(next) } });
  vi.mocked(replaceMcpServer).mockResolvedValue({ ok: true, status: "connected", tool_count: 45 });
  fireEvent.click(screen.getByRole("button", { name: "Save & reload" }));
  await screen.findByText("Saved and reloaded. 45 tools available.");
  expect(replaceMcpServer).toHaveBeenCalledTimes(1);
  expect(replaceMcpServer).toHaveBeenCalledWith("example", next);
  expect(screen.queryByLabelText("Configuration JSON")).toBeNull();
});

it("rejects invalid JSON and wrapper objects without discarding the draft", async () => {
  const input = await edit();
  for (const value of ["{ invalid", "[]", '{"example":{"url":"https://new.test/mcp"}}']) {
    fireEvent.change(input, { target: { value } });
    fireEvent.click(screen.getByRole("button", { name: "Save & reload" }));
    expect(screen.getByRole("alert")).toBeTruthy();
    expect(input.value).toBe(value);
  }
  expect(replaceMcpServer).not.toHaveBeenCalled();
});

it("cancel leaves the original config untouched", async () => {
  const input = await edit();
  fireEvent.change(input, { target: { value: '{"command":"different"}' } });
  fireEvent.click(screen.getByRole("button", { name: /cancel/i }));
  expect(replaceMcpServer).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Edit example" }));
  expect(JSON.parse((screen.getByLabelText("Configuration JSON") as HTMLTextAreaElement).value)).toEqual(row.config);
});

it("blocks duplicate saves and retains the draft after a network failure", async () => {
  const input = await edit();
  let reject!: (reason: Error) => void;
  vi.mocked(replaceMcpServer).mockReturnValue(new Promise((_, fail) => { reject = fail; }));
  fireEvent.click(screen.getByRole("button", { name: "Save & reload" }));
  const pending = screen.getByRole("button", { name: "Saving & reloading…" }) as HTMLButtonElement;
  expect(pending.disabled).toBe(true);
  expect(input.disabled).toBe(true);
  fireEvent.click(pending);
  expect(replaceMcpServer).toHaveBeenCalledTimes(1);
  reject(new Error("Connection lost. Try again."));
  await screen.findByText("Connection lost. Try again.");
  expect(input.value).toContain("https://old.test/mcp");
  await waitFor(() => expect(input.disabled).toBe(false));
});

it("distinguishes saved configuration from a failed reconnect", async () => {
  await edit();
  vi.mocked(replaceMcpServer).mockResolvedValue({ ok: true, status: "error", error: "timed out during initialize" });
  fireEvent.click(screen.getByRole("button", { name: "Save & reload" }));
  expect((await screen.findByRole("alert")).textContent).toContain("Saved, but reconnect failed: timed out during initialize");
  expect(screen.queryByLabelText("Configuration JSON")).toBeNull();
});

it("shows Sign in after saving a configuration that needs OAuth", async () => {
  await edit();
  vi.mocked(replaceMcpServer).mockResolvedValue({ ok: true, status: "needs_auth" });
  vi.mocked(getMcpServers).mockResolvedValue([{ ...row, auth: "oauth", status: "needs_auth" }]);
  fireEvent.click(screen.getByRole("button", { name: "Save & reload" }));
  await screen.findByText("Saved. Sign in to finish connecting.");
  expect(await screen.findByRole("button", { name: "Sign in" })).toBeTruthy();
});
