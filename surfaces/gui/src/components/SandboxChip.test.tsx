// The session header's sandbox chip (OPE-218).
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { SandboxChip, type SessionSandbox } from "./SandboxChip";

afterEach(cleanup);

const sandboxed: SessionSandbox = {
  state: "sandboxed", provider: "openshell", network: "allowlist",
  sites: ["github.com:443", "api.github.com:443", "pypi.org:443"],
  folders: [{ path: "/home/sam/code/billing", writable: true }], logins: ["GitHub CLI"], started: true,
};

describe("SandboxChip", () => {
  it("says the sandbox and how many sites, and lists the walls on click", () => {
    const openSettings = vi.fn();
    render(<SandboxChip info={sandboxed} onOpenSettings={openSettings} />);
    const chip = screen.getByTestId("sandbox-chip");
    expect(chip.textContent).toBe("OpenShell · 3 sites");
    expect(chip.getAttribute("data-state")).toBe("sandboxed");
    expect(screen.queryByTestId("sandbox-chip-panel")).toBeNull();
    fireEvent.click(chip);
    const panel = screen.getByTestId("sandbox-chip-panel").textContent || "";
    expect(panel).toContain("This session is sandboxed");
    expect(panel).toContain("OpenShell. Commands and file tools run inside it.");
    expect(panel).toContain("/home/sam/code/billing");
    expect(panel).toContain("github.com, api.github.com, pypi.org"); // the web port is not shown
    expect(panel).toContain("GitHub CLI");
    fireEvent.click(screen.getByTestId("sandbox-chip-settings"));
    expect(openSettings).toHaveBeenCalled();
  });

  it("counts and lists the sites allowed for this session apart from the machine's", () => {
    render(<SandboxChip info={{ ...sandboxed, session_sites: ["registry.npmjs.org:443", "db.acme.dev:5432"] }} />);
    const chip = screen.getByTestId("sandbox-chip");
    expect(chip.textContent).toBe("OpenShell · 5 sites");
    fireEvent.click(chip);
    expect(screen.getByTestId("sandbox-chip-session-sites").textContent).toBe("registry.npmjs.org, db.acme.dev:5432");
    expect(screen.getByTestId("sandbox-chip-panel").textContent).toContain("Sites allowed for this session only");
  });

  it("any site, and one site", () => {
    const { rerender } = render(<SandboxChip info={{ ...sandboxed, provider: "seatbelt", network: "open", sites: [] }} />);
    expect(screen.getByTestId("sandbox-chip").textContent).toBe("macOS sandbox · any site");
    rerender(<SandboxChip info={{ ...sandboxed, sites: ["github.com:443"] }} />);
    expect(screen.getByTestId("sandbox-chip").textContent).toBe("OpenShell · 1 site");
  });

  it("warns only for a session opened before the sandbox was switched on", () => {
    render(<SandboxChip info={{ state: "not_sandboxed", provider: "openshell" }} />);
    const chip = screen.getByTestId("sandbox-chip");
    expect(chip.textContent).toBe("Not sandboxed");
    expect(chip.getAttribute("data-state")).toBe("not_sandboxed");
    fireEvent.click(chip);
    expect(screen.getByTestId("sandbox-chip-panel").textContent).toContain("opened before the sandbox was switched on");
  });

  it("shows nothing when the machine has no sandbox, or the server says nothing", () => {
    const { container, rerender } = render(<SandboxChip info={{ state: "off" }} />);
    expect(container.textContent).toBe("");
    rerender(<SandboxChip info={null} />);
    expect(container.textContent).toBe("");
  });
});
