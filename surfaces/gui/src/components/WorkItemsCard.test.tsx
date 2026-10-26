// The card prints its own "Done when:" label; a lead that repeats the label inside the
// criteria text must not produce "Done when: Done when: …".
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { stripDoneWhen, WorkItemsCard } from "./WorkItemsCard";

describe("WorkItemsCard — criteria", () => {
  afterEach(cleanup);

  it("strips a leading Done when: (any case, spacing, repeated)", () => {
    expect(stripDoneWhen("Done when: the cap holds")).toBe("the cap holds");
    expect(stripDoneWhen("  done  WHEN :Done when:  the cap holds")).toBe("the cap holds");
    expect(stripDoneWhen("It is done when: the cap holds")).toBe("It is done when: the cap holds");
    expect(stripDoneWhen(undefined)).toBe("");
  });

  it("shows the label exactly once", () => {
    render(
      <WorkItemsCard
        item={{
          kind: "itemsreq",
          items: [{ title: "Cap the backoff", criteria: "Done when: Done when: no wait exceeds maxDelayMs" }],
        }}
        onRespond={vi.fn()}
      />,
    );
    const text = screen.getByTestId("itemsreq-card").textContent || "";
    expect(text.match(/Acceptance criteria/g)).toHaveLength(1);
    expect(text).not.toContain("Done when:");
    expect(text).toContain("no wait exceeds maxDelayMs");
  });

  it("measures the clamp on the stripped text", () => {
    // 150 chars of criteria + a repeated label: over the threshold raw, under it stripped.
    const criteria = "Done when: Done when: " + "x".repeat(150);
    render(
      <WorkItemsCard item={{ kind: "itemsreq", items: [{ title: "T", criteria }] }} onRespond={vi.fn()} />,
    );
    expect(screen.queryByTestId("itemsreq-ac-toggle-0")).toBeNull();
  });
});
