import { render, screen, cleanup } from "@testing-library/react";
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
    
    // Progress section is always there
    expect(screen.getByText("Progress")).toBeDefined();
    
    // Tools and Skills sections should be hidden
    expect(screen.queryByText(/Tools/)).toBeNull();
    expect(screen.queryByText(/Skills/)).toBeNull();
  });

  it("renders Tools section when tools are loaded", () => {
    render(<RightRail {...defaultProps} tools={["grep", "run_shell"]} />);
    
    expect(screen.getByText("Tools (2)")).toBeDefined();
    expect(screen.getByText("grep")).toBeDefined();
    expect(screen.getByText("run_shell")).toBeDefined();
  });

  it("renders Skills section when skills are loaded", () => {
    render(<RightRail {...defaultProps} skills={["tdd-workflow", "search-first"]} />);
    
    expect(screen.getByText("Skills (2)")).toBeDefined();
    expect(screen.getByText("tdd-workflow")).toBeDefined();
    expect(screen.getByText("search-first")).toBeDefined();
  });
});
