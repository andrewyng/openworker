// Phase 3 wiring: each answer that ends a turn carries that turn's footer (OB-5), and the
// Steps turn picker says why a turn started (OB-17).
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import i18n from "i18next";
import { Transcript } from "./Transcript";
import { wakeLine } from "./StepsPanel";
import type { Item } from "../types";

afterEach(cleanup);
const t = i18n.t.bind(i18n) as any;

const footer = (id: string, mid: string, total: number) => ({
  turn_id: id, seq: 1, status: "completed", last_message_id: mid, duration_ms: 2000, total, at_least: false,
  warn: 0, auto_approved: 0, asked: 0, feedback: null,
});

describe("Transcript footers", () => {
  it("puts each turn's footer under its own answer, and opens Steps at that turn", () => {
    const items: Item[] = [
      { kind: "user", text: "one" },
      { kind: "assistant", text: "first answer", mid: "m1" },
      { kind: "user", text: "two" },
      { kind: "assistant", text: "second answer", mid: "m2" },
    ];
    const open = vi.fn();
    render(
      <Transcript items={items} onApprove={() => {}} onOpenSteps={open} footerSessionId="s1"
        footers={{ turns: [footer("t1", "m1", 1200), footer("t2", "m2", 3400)], recording: true }} />,
    );
    const tokens = screen.getAllByTestId("turn-footer-tokens").map((e) => e.textContent);
    expect(tokens).toEqual(["1.2k tokens this turn", "3.4k tokens this turn"]);
    fireEvent.click(screen.getAllByTestId("turn-footer-steps")[1]);
    expect(open).toHaveBeenCalledWith(undefined, "t2");
    // only the last turn's footer is always shown; older ones on hover (Q9)
    const wraps = screen.getAllByTestId("turn-footer");
    expect(wraps[0].className).not.toContain("last");
    expect(wraps[1].className).toContain("last");
  });

  it("without footers nothing changes", () => {
    render(<Transcript items={[{ kind: "assistant", text: "hi" }]} onApprove={() => {}} />);
    expect(screen.queryByTestId("turn-footer")).toBeNull();
  });
});

describe("wakeLine", () => {
  it("says why a team turn started, and nothing for your own turns", () => {
    const turn = (wake: any) => ({ wake } as any);
    expect(wakeLine(turn({ reason: "review", batched: 4 }), t)).toBe("woke: Review batch");
    expect(wakeLine(turn({ reason: "timer" }), t)).toBe("woke: Timer (sleep_for)");
    expect(wakeLine(turn({ reason: "user" }), t)).toBeNull();
    expect(wakeLine(turn(undefined), t)).toBeNull();
  });
});
