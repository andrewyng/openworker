// Large teams (owner-ruled 2026-09-17): workers that share a role collapse into one line
// with one model and one set of connector ticks; "Show workers" opens the per-worker rows.
// Grouping is a view — the answer stays one decision per worker. Payloads come from the
// card gallery's state files.
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { teamItemFromPayload } from "../cardPayloads";
import { statePayload } from "../gallery/states";
import { TeamRequestCard } from "./TeamRequestCard";

const OPUS = "anthropic:claude-opus-4-8";
const SONNET = "anthropic:claude-sonnet-4-8";

function show(state: string) {
  const onRespond = vi.fn();
  render(<TeamRequestCard item={teamItemFromPayload(statePayload("team-request", state))} onRespond={onRespond} />);
  const approve = () => {
    fireEvent.click(screen.getByTestId("teamreq-approve"));
    return onRespond.mock.calls[0][3] as { persona: string; name: string; connectors: string[]; model?: string }[];
  };
  return { approve };
}

describe("TeamRequestCard — roles with several workers", () => {
  afterEach(cleanup);

  it("shows twenty-four workers as four role lines and still answers per worker", () => {
    const { approve } = show("twenty-four-workers");
    expect(screen.getByTestId("teamreq-card").textContent).toContain("24 workers · 4 roles");
    for (const role of ["swe-worker", "test-worker", "review-worker", "docs-worker"])
      expect(screen.getByTestId(`teamreq-group-${role}`)).toBeTruthy();
    expect(screen.getByTestId("teamreq-group-swe-worker").textContent).toContain("× 12");
    // No per-worker row until a role is opened.
    expect(screen.queryByTestId("teamreq-row-0")).toBeNull();
    // A fallback shared by the whole role sits on the role line.
    expect(screen.getByTestId("teamreq-group-warn-test-worker")).toBeTruthy();
    const members = approve();
    expect(members).toHaveLength(24);
    expect(members.filter((m) => m.persona === "swe-worker").every((m) => m.connectors.includes("github") && m.model === OPUS)).toBe(true);
    expect(members.filter((m) => m.persona === "test-worker").every((m) => m.connectors.length === 0)).toBe(true);
  });

  it("keeps the ordinary row for a role with one worker", () => {
    show("six-workers");
    expect(screen.getByTestId("teamreq-group-swe-worker")).toBeTruthy();
    expect(screen.getByTestId("teamreq-row-3").textContent).toContain("checks");
    expect(screen.queryByTestId("teamreq-group-test-worker")).toBeNull();
  });

  it("sets one model for the whole role from the role line", () => {
    const { approve } = show("twelve-workers");
    fireEvent.change(screen.getByTestId("teamreq-group-model-swe-worker"), { target: { value: SONNET } });
    const members = approve();
    expect(members.filter((m) => m.persona === "swe-worker").map((m) => m.model)).toEqual(Array(6).fill(SONNET));
    expect(members.filter((m) => m.persona === "review-worker").every((m) => m.model === OPUS)).toBe(true);
  });

  it("says when a role is mixed, and one click makes it uniform", () => {
    const { approve } = show("mixed-within-a-role");
    expect((screen.getByTestId("teamreq-group-model-swe-worker") as HTMLSelectElement).value).toBe("__mixed__");
    expect(screen.getByTestId("teamreq-group-tally-swe-worker").textContent).toBe(
      "4 on Claude Opus 4.8, 2 on Claude Sonnet 4.8",
    );
    const github = screen.getByTestId("teamreq-group-connector-swe-worker-github") as HTMLInputElement;
    expect(github.indeterminate).toBe(true);
    expect(screen.getByTestId("teamreq-group-partial-swe-worker-github").textContent).toBe("5 of 6");
    fireEvent.click(github); // some → all
    expect(screen.queryByTestId("teamreq-group-partial-swe-worker-github")).toBeNull();
    expect(github.checked).toBe(true);
    const members = approve();
    expect(members.filter((m) => m.persona === "swe-worker").every((m) => m.connectors.includes("github"))).toBe(true);
    // The lead's per-worker models were left alone.
    expect(members.filter((m) => m.model === SONNET && m.persona === "swe-worker")).toHaveLength(2);
  });

  it("unticks the whole role when every worker already holds the connector", () => {
    const { approve } = show("twelve-workers");
    fireEvent.click(screen.getByTestId("teamreq-group-connector-swe-worker-github"));
    expect(approve().filter((m) => m.persona === "swe-worker").every((m) => m.connectors.length === 0)).toBe(true);
  });

  it("opens a role to change one worker; the role line then reports the split", () => {
    const { approve } = show("twelve-workers");
    fireEvent.click(screen.getByTestId("teamreq-group-toggle-swe-worker"));
    expect(screen.getByTestId("teamreq-group-members-swe-worker").children).toHaveLength(6);
    fireEvent.change(screen.getByTestId("teamreq-model-2"), { target: { value: SONNET } });
    fireEvent.click(screen.getByTestId("teamreq-connector-2-github"));
    expect(screen.getByTestId("teamreq-group-tally-swe-worker").textContent).toBe(
      "5 on Claude Opus 4.8, 1 on Claude Sonnet 4.8",
    );
    expect(screen.getByTestId("teamreq-group-partial-swe-worker-github").textContent).toBe("5 of 6");
    const members = approve();
    expect(members[2]).toEqual({ persona: "swe-worker", name: "lena", connectors: [], model: SONNET });
    expect(members[1].model).toBe(OPUS);
    fireEvent.click(screen.getByTestId("teamreq-group-toggle-swe-worker"));
    expect(screen.queryByTestId("teamreq-group-members-swe-worker")).toBeNull();
  });

  it("adds a connector beyond the usual set for the whole role", () => {
    const { approve } = show("twelve-workers");
    fireEvent.click(screen.getByTestId("teamreq-group-add-test-worker"));
    fireEvent.click(screen.getByTestId("teamreq-group-connector-test-worker-linear"));
    expect(screen.getByTestId("teamreq-group-test-worker").textContent).toContain("Beyond this role's usual set");
    expect((screen.getByTestId("teamreq-group-connector-test-worker-linear") as HTMLInputElement).checked).toBe(true);
    const members = approve();
    expect(members.filter((m) => m.persona === "test-worker").every((m) => m.connectors.includes("linear"))).toBe(true);
    expect(members.filter((m) => m.persona === "swe-worker").some((m) => m.connectors.includes("linear"))).toBe(false);
  });
});
