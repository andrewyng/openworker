// OB-5: the footer under an answer and the rating card (technical-design.md §8).
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

vi.mock("../api", () => ({
  getSessionFooters: vi.fn(),
  putTurnFeedback: vi.fn(async (_s: string, turnId: string, body: any) => ({
    feedback: body.rating ? { turn_id: turnId, session_id: "s1", rating: body.rating, reasons: body.reasons || [], rated_at: 1, changed_at: null, content: { note: body.note || null } } : null,
    where: { local: true, export: "langfuse", managed: false, with_note: !!body.note },
  })),
}));

import { putTurnFeedback } from "../api";
import { TurnFooterLine } from "./TurnFooter";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const footer = { turn_id: "t1", seq: 5, status: "completed", duration_ms: 14_000, total: 41_000, at_least: false,
  warn: 1, auto_approved: 1, asked: 0, feedback: null };

describe("TurnFooterLine", () => {
  it("says this turn's tokens and hides parts that are 0 (M3-R6, M3-R7)", () => {
    render(<TurnFooterLine sessionId="s1" footer={footer} last onOpenSteps={() => {}} />);
    const text = screen.getByTestId("turn-footer").textContent || "";
    expect(text).toContain("14s");
    expect(text).toContain("41k tokens this turn");
    expect(text).toContain("1 issue");
    expect(text).toContain("1 auto-approved");
    expect(text).not.toContain("asked you");
    expect(screen.getByTestId("turn-footer-steps").textContent).toBe("Trace"); // T3-13
  });

  it("a 👎 opens the card; Send stores the reasons and the note and says where it went", async () => {
    const changed = vi.fn();
    render(<TurnFooterLine sessionId="s1" footer={footer} last onChange={changed} />);
    fireEvent.click(screen.getByTestId("turn-footer-down"));
    fireEvent.click(screen.getByTestId("rating-reason-did_unasked"));
    fireEvent.change(screen.getByTestId("rating-note"), { target: { value: "ran a command I didn't want" } });
    fireEvent.click(screen.getByTestId("rating-send"));
    await waitFor(() => expect(putTurnFeedback).toHaveBeenCalled());
    expect((putTurnFeedback as any).mock.calls[0]).toEqual(["s1", "t1", { rating: "down", reasons: ["did_unasked"], note: "ran a command I didn't want" }]);
    await waitFor(() => expect(screen.getByTestId("turn-footer-where").textContent).toContain("Also sent to Langfuse"));
    expect(screen.getByTestId("turn-footer-where").textContent).toContain("With your note.");
    expect(changed).toHaveBeenCalled();
  });

  it("a 👍 is stored at once; a second 👍 clears it", async () => {
    const { rerender } = render(<TurnFooterLine sessionId="s1" footer={footer} last />);
    fireEvent.click(screen.getByTestId("turn-footer-up"));
    await waitFor(() => expect((putTurnFeedback as any).mock.calls[0][2]).toEqual({ rating: "up", reasons: [], note: undefined }));
    rerender(<TurnFooterLine sessionId="s1" footer={{ ...footer, feedback: { turn_id: "t1", session_id: "s1", rating: "up", reasons: [], rated_at: 1, changed_at: null } }} last />);
    fireEvent.click(screen.getByTestId("turn-footer-up"));
    await waitFor(() => expect((putTurnFeedback as any).mock.calls[1][2].rating).toBeNull());
  });

  it("the open 👎 card says reasons can be several and where the rating will go; 👎 again closes it unrated (T3-8)", () => {
    const up = { ...footer, feedback: { turn_id: "t1", session_id: "s1", rating: "up" as const, reasons: [], rated_at: 1, changed_at: null } };
    render(<TurnFooterLine sessionId="s1" footer={up} last defaultWhere={{ local: true, export: null, managed: false, with_note: false }} />);
    expect(screen.getByTestId("turn-footer-up").getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(screen.getByTestId("turn-footer-down"));
    const card = screen.getByTestId("rating-card").textContent || "";
    expect(card).toContain("Pick any that apply.");
    expect(card).toContain("Will be saved on this machine.");
    expect(card).not.toContain("Saved on this machine.");
    // (d) 👎 is the one shown chosen while its card is open
    expect(screen.getByTestId("turn-footer-up").getAttribute("aria-pressed")).toBe("false");
    expect(screen.getByTestId("turn-footer-down").getAttribute("aria-pressed")).toBe("true");
    // (a) several reasons stay picked
    fireEvent.click(screen.getByTestId("rating-reason-wrong_answer"));
    fireEvent.click(screen.getByTestId("rating-reason-too_slow"));
    expect(screen.getByTestId("rating-reason-wrong_answer").getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByTestId("rating-reason-too_slow").getAttribute("aria-pressed")).toBe("true");
    // (c) 👎 again closes the card without rating; the 👍 is back
    fireEvent.click(screen.getByTestId("turn-footer-down"));
    expect(screen.queryByTestId("rating-card")).toBeNull();
    expect(putTurnFeedback).not.toHaveBeenCalled();
    expect(screen.getByTestId("turn-footer-up").getAttribute("aria-pressed")).toBe("true");
  });

  it("with recording off, says so and still offers a rating", () => {
    render(<TurnFooterLine sessionId="s1" footer={footer} recording={false} last />);
    expect(screen.getByTestId("turn-footer").textContent).toContain("Recording is off on this machine");
    expect(screen.getByTestId("turn-footer-down")).toBeTruthy();
  });
});
