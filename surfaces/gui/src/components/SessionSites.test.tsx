// The Access section's Sites group (OPE-219).
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { SessionSandbox } from "./SandboxChip";

const api = vi.hoisted(() => ({ allowSessionSite: vi.fn(), removeSessionSite: vi.fn() }));
vi.mock("../api", () => api);

import { SessionSites } from "./SessionSites";

afterEach(cleanup);
beforeEach(() => {
  api.allowSessionSite.mockReset();
  api.removeSessionSite.mockReset();
});

const sandboxed: SessionSandbox = {
  state: "sandboxed", provider: "openshell", network: "allowlist",
  sites: ["github.com:443", "pypi.org:443"], session_sites: ["registry.npmjs.org:443"],
  folders: [], logins: [], started: true,
};

describe("SessionSites", () => {
  it("lists the machine's sites plain and the session's with a tag", () => {
    render(<SessionSites sessionId="s1" sandbox={sandboxed} onOpenSettings={vi.fn()} />);
    const rows = screen.getAllByTestId("session-site");
    expect(rows.map((r) => [r.getAttribute("data-source"), r.textContent])).toEqual([
      ["settings", "github.com"],
      ["settings", "pypi.org"],
      ["session", "registry.npmjs.orgSession only×"],
    ]);
    // A site from Settings says where it can be removed; it has no remove control here.
    expect(rows[0].getAttribute("title")).toBe("This can be removed from Settings ▸ Sandbox");
    expect(screen.getAllByTestId("session-site-remove")).toHaveLength(1);
    expect(screen.getByTestId("session-sites-settings").textContent).toBe("Open Settings ▸ Sandbox");
  });

  it("allows one more site for the session and hands the new list up", async () => {
    const next = { ...sandboxed, session_sites: ["registry.npmjs.org:443", "db.acme.dev:5432"] };
    api.allowSessionSite.mockResolvedValue({ ok: true, sandbox: next });
    const onSandbox = vi.fn();
    render(<SessionSites sessionId="s1" sandbox={sandboxed} onSandbox={onSandbox} />);
    fireEvent.click(screen.getByTestId("session-site-add"));
    fireEvent.change(screen.getByTestId("session-site-input"), { target: { value: " db.acme.dev:5432 " } });
    fireEvent.click(screen.getByTestId("session-site-allow"));
    await waitFor(() => expect(onSandbox).toHaveBeenCalledWith(next));
    expect(api.allowSessionSite).toHaveBeenCalledWith("s1", "db.acme.dev:5432");
    await waitFor(() => expect(screen.queryByTestId("session-site-input")).toBeNull());
  });

  it("says why a site was not taken and keeps the box open", async () => {
    api.allowSessionSite.mockResolvedValue({ ok: false, error: "Give an exact host name, such as registry.npmjs.org (add a port only when it is not 443)." });
    render(<SessionSites sessionId="s1" sandbox={sandboxed} />);
    fireEvent.click(screen.getByTestId("session-site-add"));
    fireEvent.change(screen.getByTestId("session-site-input"), { target: { value: "*.npmjs.org" } });
    fireEvent.click(screen.getByTestId("session-site-allow"));
    await waitFor(() => expect(screen.getByTestId("session-sites-error").textContent).toContain("exact host name"));
    expect(screen.getByTestId("session-site-input")).toBeTruthy();
  });

  it("takes a session site back", async () => {
    const next = { ...sandboxed, session_sites: [] };
    api.removeSessionSite.mockResolvedValue({ ok: true, sandbox: next });
    const onSandbox = vi.fn();
    render(<SessionSites sessionId="s1" sandbox={sandboxed} onSandbox={onSandbox} />);
    fireEvent.click(screen.getByTestId("session-site-remove"));
    await waitFor(() => expect(onSandbox).toHaveBeenCalledWith(next));
    expect(api.removeSessionSite).toHaveBeenCalledWith("s1", "registry.npmjs.org:443");
  });

  it("any site, no sites yet, and no group without a sandbox", () => {
    const { rerender, container } = render(<SessionSites sessionId="s1" sandbox={{ ...sandboxed, network: "open", sites: [], session_sites: [] }} />);
    expect(screen.getByTestId("session-sites").textContent).toContain("Any site");
    expect(screen.queryByTestId("session-site-add")).toBeNull();
    rerender(<SessionSites sessionId="s1" sandbox={{ ...sandboxed, sites: [], session_sites: [] }} />);
    expect(screen.getByTestId("session-sites").textContent).toContain("Commands and web tools reach no sites yet.");
    expect(screen.getByTestId("session-site-add")).toBeTruthy();
    for (const info of [null, { state: "off" } as SessionSandbox, { state: "not_sandboxed", provider: "openshell" } as SessionSandbox]) {
      rerender(<SessionSites sessionId="s1" sandbox={info} />);
      expect(container.textContent).toBe("");
    }
  });
});
