import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { RightRail } from "./RightRail";

vi.mock("../api", async () => {
  const actual: any = await vi.importActual("../api");
  return {
    ...actual,
    getArtifacts: vi.fn().mockResolvedValue([]),
    getRoots: vi.fn().mockResolvedValue([]),
    getJournalCases: vi.fn().mockResolvedValue([]),
  };
});

describe("RightRail runtime tree", () => {
  it("shows the durable supervisor and delegated specialists", () => {
    render(
      <RightRail
        active
        sessionId="runtime-session"
        refreshKey={0}
        toolNames={[]}
        todo={[]}
        running={false}
        runtime="graph"
        runtimeRun={{
          session_id: "runtime-session",
          run_id: "run-1",
          runtime: "graph",
          status: "completed",
          node: "finalize",
          usage: { input: 12, output: 8 },
          delegations: [{
            id: "delegate-1",
            title: "Reviewer",
            status: "completed",
            tools: ["read_file", "git_diff"],
            result: { confidence: "high" },
          }],
        }}
      />,
    );

    const toggle = screen.getByTestId("rail-toggle-agent run");
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    const tree = screen.getByTestId("runtime-tree").textContent || "";
    expect(tree).toContain("Supervisor");
    expect(tree).toContain("Reviewer");
    expect(tree).toContain("Durable graph");
    expect(tree).toContain("20 tokens");
  });
});
