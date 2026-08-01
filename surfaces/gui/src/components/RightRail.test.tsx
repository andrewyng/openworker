import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { getArtifacts, readArtifact, type ArtifactInfo } from "../api";
import { RightRail } from "./RightRail";

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  return {
    ...actual,
    getArtifacts: vi.fn(),
    readArtifact: vi.fn(),
    revealArtifact: vi.fn(),
  };
});

vi.mock("./AccessSection", () => ({
  AccessSection: () => <div data-testid="access-section" />,
}));

vi.mock("./BrowserViewport", () => ({
  BrowserViewport: ({ workspaceActive }: { workspaceActive?: boolean }) => (
    <div
      data-testid="browser-viewport-mock"
      data-workspace-active={workspaceActive ? "true" : "false"}
    />
  ),
}));

const artifact: ArtifactInfo = {
  path: "output/draft.md",
  abs_path: "/tmp/output/draft.md",
  name: "draft.md",
  kind: "markdown",
  size: 128,
  modified_at: 1,
};

const renderRail = (browserActivityKey = 0) =>
  render(
    <RightRail
      active
      sessionId="session-1"
      refreshKey={0}
      browserActivityKey={browserActivityKey}
      toolNames={[]}
      todo={[]}
      running={false}
    />,
  );

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("RightRail shared workspace tabs", () => {
  it("keeps the add control after the last tab and opens Browser from its menu", async () => {
    vi.mocked(getArtifacts).mockResolvedValue([artifact]);
    vi.mocked(readArtifact).mockResolvedValue({
      ok: true,
      path: artifact.path,
      kind: "markdown",
      content: "# Draft",
    });

    renderRail();
    const tablist = screen.getByRole("tablist", { name: "Workspace tabs" });
    const add = screen.getByRole("button", { name: "Add workspace tab" });
    const strip = tablist.parentElement;
    expect(strip?.lastElementChild?.contains(add)).toBe(true);
    expect(tablist.contains(add)).toBe(false);
    expect(screen.getByRole("tab", { name: "Overview" }).getAttribute("aria-selected")).toBe("true");

    fireEvent.click(add);
    fireEvent.click(screen.getByRole("menuitem", { name: "Browser" }));

    expect(screen.getByRole("tab", { name: /Browser/ }).getAttribute("aria-selected")).toBe("true");
    expect(screen.getByTestId("browser-viewport-mock").dataset.workspaceActive).toBe("true");
    expect(strip?.lastElementChild?.contains(add)).toBe(true);
  });

  it("opens artifacts as tabs and leaves them focused when the agent updates Browser", async () => {
    vi.mocked(getArtifacts).mockResolvedValue([artifact]);
    vi.mocked(readArtifact).mockResolvedValue({
      ok: true,
      path: artifact.path,
      kind: "markdown",
      content: "# Draft",
    });

    const view = renderRail();
    await waitFor(() => expect(screen.getByText("draft.md")).toBeTruthy());
    fireEvent.click(screen.getByText("draft.md"));
    expect(screen.getByRole("tab", { name: /draft\.md/ }).getAttribute("aria-selected")).toBe("true");

    view.rerender(
      <RightRail
        active
        sessionId="session-1"
        refreshKey={1}
        browserActivityKey={1}
        toolNames={["browser_click"]}
        todo={[]}
        running
      />,
    );

    expect(screen.getByRole("tab", { name: /draft\.md/ }).getAttribute("aria-selected")).toBe("true");
    expect(screen.getByRole("tab", { name: /Browser/ }).getAttribute("aria-selected")).toBe("false");
    expect(screen.getByLabelText("Browser updated")).toBeTruthy();
    expect(screen.getByTestId("browser-viewport-mock").dataset.workspaceActive).toBe("false");
  });

  it("opens a selected artifact from the trailing add menu", async () => {
    vi.mocked(getArtifacts).mockResolvedValue([artifact]);
    vi.mocked(readArtifact).mockResolvedValue({
      ok: true,
      path: artifact.path,
      kind: "markdown",
      content: "# Draft",
    });

    renderRail();
    await waitFor(() => expect(vi.mocked(getArtifacts)).toHaveBeenCalled());
    fireEvent.click(screen.getByRole("button", { name: "Add workspace tab" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Open artifact…" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "draft.md" }));

    expect(screen.getByRole("tab", { name: /draft\.md/ }).getAttribute("aria-selected")).toBe("true");
  });
});
