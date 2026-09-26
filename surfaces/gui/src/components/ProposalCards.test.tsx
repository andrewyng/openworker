import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { WorkItemsCard } from "./WorkItemsCard";
import { TeamRequestCard } from "./TeamRequestCard";
import { workItemsItemFromPayload, teamItemFromPayload } from "../cardPayloads";
import {
  launchProposal,
  securityProposal,
  marketingProposal,
  launchTeam,
} from "../gallery/states/proposal-examples";

afterEach(cleanup);
describe("structured proposal cards", () => {
  it("renders declared activities and references without inferring sequence", () => {
    render(
      <WorkItemsCard
        item={workItemsItemFromPayload(launchProposal)}
        onRespond={vi.fn()}
      />,
    );
    expect(screen.getByTestId("itemsreq-card").textContent).toContain(
      "(7 Build · 3 Verify · 1 Accept)",
    );
    expect(screen.getAllByText("Acceptance criteria")).toHaveLength(11);
    expect(
      screen.getByTestId("proposal-external-actions").textContent,
    ).toContain("None planned");
    expect(screen.getByText("Owner: Lead")).toBeTruthy();
    expect(screen.queryByText(/can start now/i)).toBeNull();
  });

  it("renders security targets, scans, exclusions and actual declaration", () => {
    render(
      <WorkItemsCard
        item={workItemsItemFromPayload(securityProposal)}
        onRespond={vi.fn()}
      />,
    );
    expect(
      screen.getByTestId("proposal-external-actions").textContent,
    ).toContain("Run authenticated checks against sandbox-1");
    expect(screen.getByText(/\(2 Assess ·/)).toBeTruthy();
    expect(screen.queryByText("None planned")).toBeNull();
  });

  it("does not mistake missing decisions for no external actions", () => {
    render(
      <WorkItemsCard
        item={workItemsItemFromPayload(marketingProposal)}
        onRespond={vi.fn()}
      />,
    );
    expect(screen.getByText("Not yet determined")).toBeTruthy();
    expect(screen.queryByText("None planned")).toBeNull();
  });

  it("preserves feedback without granting approval", () => {
    const respond = vi.fn();
    render(
      <WorkItemsCard
        item={workItemsItemFromPayload(securityProposal)}
        onRespond={respond}
      />,
    );
    fireEvent.click(screen.getByText("Request changes"));
    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: "No active scans" },
    });
    fireEvent.click(screen.getByText("Send feedback"));
    expect(respond).toHaveBeenCalledWith(false, "No active scans");
  });

  it("uses declared worker groups and sends the edited name and chosen grants", () => {
    const respond = vi.fn();
    render(
      <TeamRequestCard
        item={teamItemFromPayload(launchTeam)}
        onRespond={respond}
      />,
    );
    expect(screen.getByText("Builders")).toBeTruthy();
    expect(screen.getByText("Verifiers")).toBeTruthy();
    expect(screen.getByText("Owner: Lead")).toBeTruthy();
    expect(screen.getAllByText("Planned responsibilities")).toHaveLength(10);
    fireEvent.change(screen.getAllByLabelText("Worker name")[0], {
      target: { value: "sam-custom" },
    });
    fireEvent.click(screen.getByTestId("teamreq-approve"));
    expect(respond.mock.calls[0][3][0]).toMatchObject({
      name: "sam-custom",
      connectors: [],
    });
  });

  it("prevents duplicate or reserved worker names", () => {
    const respond = vi.fn();
    render(
      <TeamRequestCard
        item={teamItemFromPayload(launchTeam)}
        onRespond={respond}
      />,
    );
    fireEvent.change(screen.getAllByLabelText("Worker name")[0], {
      target: { value: "lead" },
    });
    expect(
      (screen.getByTestId("teamreq-approve") as HTMLButtonElement).disabled,
    ).toBe(true);
    expect(screen.getByRole("alert")).toBeTruthy();
    fireEvent.click(screen.getByTestId("teamreq-approve"));
    expect(respond).not.toHaveBeenCalled();
  });

  it("manages per-worker connectors even when the lead suggests none", () => {
    const respond = vi.fn();
    Element.prototype.scrollIntoView = vi.fn();
    render(<TeamRequestCard item={teamItemFromPayload({ ...launchTeam, other_connected: ["github"] })} onRespond={respond} />);
    fireEvent.click(screen.getByTestId("proposal-manage-connectors"));
    expect(screen.getByTestId("teamreq-row-0").closest("details")?.open).toBe(true);
    fireEvent.click(screen.getByTestId("teamreq-connector-0-github"));
    fireEvent.click(screen.getByTestId("teamreq-approve"));
    expect(respond.mock.calls[0][3][0].connectors).toEqual(["github"]);
    expect(respond.mock.calls[0][3][1].connectors).toEqual([]);
  });
});
