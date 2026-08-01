import { render, screen, cleanup, fireEvent } from "@testing-library/react";
import { describe, it, expect, afterEach } from "vitest";
import { RightRail } from "./RightRail";

describe("RightRail", () => {
  afterEach(cleanup);

  const defaultProps = {
    active: true,
    sessionId: "session-1",
    refreshKey: 0,
    toolNames: [],
    tools: [],
    skills: [],
    todo: [],
    running: false,
    showArtifacts: false,
  };

  it("does not render Tools or Skills sections when empty", () => {
    render(<RightRail {...defaultProps} />);
    
    // Current Task (under Telemetry tab) is always there
    expect(screen.getByText("Current Task")).toBeDefined();
    
    // Tools/Skills headings should not be in Telemetry tab
    expect(screen.queryByText("Tools (0)")).toBeNull();
    expect(screen.queryByText("Skills (0)")).toBeNull();

    // Switch to Tools tab
    const toolsTab = screen.getByRole("button", { name: "Tools" });
    fireEvent.click(toolsTab);

    // Now they are rendered, but empty
    expect(screen.getByText("Tools (0)")).toBeDefined();
    expect(screen.getByText("No tools loaded.")).toBeDefined();
    expect(screen.getByText("Skills (0)")).toBeDefined();
    expect(screen.getByText("No skills loaded.")).toBeDefined();
  });

  it("renders Tools section when tools are loaded", () => {
    render(<RightRail {...defaultProps} tools={["grep", "run_shell"]} />);
    
    // Switch to Tools tab
    const toolsTab = screen.getByRole("button", { name: "Tools" });
    fireEvent.click(toolsTab);

    expect(screen.getByText("Tools (2)")).toBeDefined();
    expect(screen.getByText("grep")).toBeDefined();
    expect(screen.getByText("run_shell")).toBeDefined();
  });

  it("renders Skills section when skills are loaded", () => {
    render(<RightRail {...defaultProps} skills={["tdd-workflow", "search-first"]} />);
    
    // Switch to Tools tab
    const toolsTab = screen.getByRole("button", { name: "Tools" });
    fireEvent.click(toolsTab);

    expect(screen.getByText("Skills (2)")).toBeDefined();
    expect(screen.getByText("tdd-workflow")).toBeDefined();
    expect(screen.getByText("search-first")).toBeDefined();
  });
});
