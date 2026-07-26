// SKILLS-SPEC §4.6 GUI — the composer's "/" force-run popup: opens only for a leading
// slash, lists only the session's effective (enabled) menu, filters while typing, and the
// picked skill rides onSend as its own field — never as message text.
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Composer } from "./Composer";

const MENU = {
  skills: [
    { name: "weekly-report", description: "Monday status report", scope: "global", enabled: true },
    { name: "greet", description: "says hello", scope: "project", enabled: true },
    { name: "muted-one", description: "muted here", scope: "global", enabled: false },
  ],
};

function stubFetch() {
  const calls: { url: string; method: string }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, method: (init?.method || "GET").toUpperCase() });
      if (url.includes("/skills")) return { ok: true, json: async () => MENU } as Response;
      return { ok: true, json: async () => ({}) } as Response;
    }),
  );
  return calls;
}

const props = (extra: Partial<Parameters<typeof Composer>[0]> = {}) => ({
  mode: "interactive",
  model: "gpt-5.6-sol",
  running: false,
  connected: true,
  sessionId: "s1",
  onSend: vi.fn(),
  onInterrupt: vi.fn(),
  onModeChange: vi.fn(),
  onModelChange: vi.fn(),
  ...extra,
});

const box = () => screen.getByPlaceholderText(/Ask the coworker/);

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("Composer / skills popup", () => {
  it("opens on a leading '/' and lists only enabled skills from the effective menu", async () => {
    stubFetch();
    render(<Composer {...props()} />);
    fireEvent.change(box(), { target: { value: "/" } });
    await screen.findByTestId("skill-popup");
    expect(await screen.findByText("/weekly-report")).toBeTruthy();
    expect(screen.getByText("/greet")).toBeTruthy();
    expect(screen.queryByText("/muted-one")).toBeNull(); // muted → not offered
    expect(screen.getByText("project")).toBeTruthy(); // scope badge
  });

  it("filters as you type", async () => {
    stubFetch();
    render(<Composer {...props()} />);
    fireEvent.change(box(), { target: { value: "/" } });
    await screen.findByText("/weekly-report");
    fireEvent.change(box(), { target: { value: "/wee" } });
    expect(screen.getByText("/weekly-report")).toBeTruthy();
    expect(screen.queryByText("/greet")).toBeNull();
  });

  it("does NOT open for a mid-text slash", async () => {
    stubFetch();
    render(<Composer {...props()} />);
    fireEvent.change(box(), { target: { value: "rate 5/10 please" } });
    expect(screen.queryByTestId("skill-popup")).toBeNull();
  });

  it("selecting pins a chip and the send carries the skill as its own field", async () => {
    stubFetch();
    const p = props();
    render(<Composer {...p} />);
    fireEvent.change(box(), { target: { value: "/gr" } });
    fireEvent.click(await screen.findByRole("option", { name: /greet/ }));
    await screen.findByTestId("skill-chip");
    fireEvent.change(box(), { target: { value: "say hi to the team" } });
    fireEvent.keyDown(box(), { key: "Enter" });
    await waitFor(() => expect(p.onSend).toHaveBeenCalled());
    expect(p.onSend).toHaveBeenCalledWith("say hi to the team", [], "greet");
  });

  it("a skill-only send works and Enter inside the popup never sends the query text", async () => {
    stubFetch();
    const p = props();
    render(<Composer {...p} />);
    fireEvent.change(box(), { target: { value: "/wee" } });
    await screen.findByText("/weekly-report");
    fireEvent.keyDown(box(), { key: "Enter" }); // selects, does not send
    expect(p.onSend).not.toHaveBeenCalled();
    await screen.findByTestId("skill-chip");
    fireEvent.keyDown(box(), { key: "Enter" }); // now sends, skill-only
    await waitFor(() => expect(p.onSend).toHaveBeenCalledWith("", [], "weekly-report"));
  });

  it("Escape closes the popup and no popup ever opens without a sessionId", async () => {
    stubFetch();
    render(<Composer {...props()} />);
    fireEvent.change(box(), { target: { value: "/gr" } });
    await screen.findByTestId("skill-popup");
    fireEvent.keyDown(box(), { key: "Escape" });
    expect(screen.queryByTestId("skill-popup")).toBeNull();
    cleanup();
    stubFetch();
    render(<Composer {...props({ sessionId: undefined })} />);
    fireEvent.change(box(), { target: { value: "/" } });
    expect(screen.queryByTestId("skill-popup")).toBeNull();
  });
});
