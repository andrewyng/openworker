import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { ScheduledView } from "./ScheduledView";

vi.mock("../api", () => ({
  announceAutomationsChanged: vi.fn(),
  createAutomation: vi.fn(),
  deleteAutomation: vi.fn(),
  getAutomation: vi.fn(),
  getAutomations: vi.fn().mockResolvedValue([]),
  markAutomationSeen: vi.fn(),
  updateAutomation: vi.fn(),
}));

vi.mock("./AutomationQuickstart", () => ({
  AutomationQuickstart: () => <div data-testid="automation-quickstart" />,
}));

vi.mock("./IntegrationsView", () => ({
  PanelHead: ({ title, sub }: { title: string; sub: string }) => (
    <header>
      <h1>{title}</h1>
      <p>{sub}</p>
    </header>
  ),
}));

afterEach(cleanup);

describe("ScheduledView empty state", () => {
  it("renders translated emphasis as a strong element, not literal markup", () => {
    const { container } = render(
      <ScheduledView onOpenRun={vi.fn()} onRunNow={vi.fn()} />,
    );

    expect(
      screen.getByText("+ New automation", { selector: "strong" }),
    ).toBeTruthy();
    expect(container.textContent).not.toContain("<strong>");
  });
});
