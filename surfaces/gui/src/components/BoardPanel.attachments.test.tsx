import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ItemDetail } from "./BoardPanel";
import type { BoardItemDetail } from "../api";

afterEach(cleanup);

it("renders report downloads, not broken image thumbnails or active HTML", async () => {
  URL.revokeObjectURL = vi.fn();
  const load = vi.fn().mockResolvedValue("blob:managed-report");
  const detail = {
    id: 1, title: "Verify billing", description: "", criteria: "Tests pass", state: "review",
    assignee: "maya", creator: "lead", refs: [], links: [], comments: [],
    timeline: [{ seq: 4, ts: new Date().toISOString(), actor: "maya", kind: "comment",
      body: "Acceptance report", refs: [`attachment://${"a".repeat(64)}.md#findings.md`] }],
  } as unknown as BoardItemDetail;
  render(<ItemDetail detail={detail} loadAttachment={load} />);
  const link = await screen.findByTestId("board-file-attachment");
  expect(link.getAttribute("download")).toBe("findings.md");
  expect(link.getAttribute("href")).toBe("blob:managed-report");
  expect(screen.queryByRole("img")).toBeNull();
});
