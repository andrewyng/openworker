import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import i18n from "i18next";
import type { Item } from "../types";
import { Transcript } from "./Transcript";

const parses = vi.hoisted(() => ({ count: 0 }));
vi.mock("react-markdown", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-markdown")>();
  return {
    ...actual,
    default: (props: Parameters<typeof actual.default>[0]) => {
      parses.count++;
      return actual.default(props);
    },
  };
});

// Fresh objects model a session reload: stable text, not object identity, should
// determine whether the expensive Markdown parser needs to run again.
function history(): Item[] {
  return Array.from({ length: 20 }, (_, i) => ({
    kind: i % 2 ? "assistant" : "user",
    text: `Message **${i}**`,
    ts: 1_700_000_000 + i * 60,
  }));
}

beforeEach(() => { parses.count = 0; });
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  Reflect.deleteProperty(navigator, "clipboard");
});

describe("transcript rerenders", () => {
  it("keeps an out-of-range persisted timestamp from crashing the conversation", () => {
    render(<Transcript items={[{ kind: "assistant", text: "Still readable", ts: 1e14 }]} onApprove={() => {}} />);
    expect(screen.getByText("Still readable")).toBeTruthy();
    expect(screen.getByTestId("bubble-ts").textContent).toBe("Invalid Date");
    expect(screen.getByTestId("bubble-ts").title).toBe("Invalid Date");
  });

  it("does not reparse unchanged messages when parent props and item objects change", () => {
    const { rerender } = render(<Transcript items={history()} onApprove={() => {}} />);
    expect(parses.count).toBe(10);
    rerender(<Transcript items={history()} onApprove={() => {}} running />);
    expect(parses.count).toBe(10);
    expect(screen.getByText("19", { selector: "strong" })).toBeTruthy();
  });

  it("does not reformat unchanged bubble timestamps on a parent rerender", () => {
    const { rerender } = render(<Transcript items={history()} onApprove={() => {}} />);
    const stamps = screen.getAllByTestId("bubble-ts").map((el) => [el.textContent, el.title]);
    // Observe both Date's convenience API and reusable Intl formatters, keeping
    // their real implementations. This detects repeated work through either API.
    const date = vi.spyOn(Date.prototype, "toLocaleString");
    const time = vi.spyOn(Date.prototype, "toLocaleTimeString");
    // The runtime property is an accessor; TypeScript declares it as a method.
    const format = vi.spyOn(Intl.DateTimeFormat.prototype as { format: unknown }, "format", "get");
    rerender(<Transcript items={history()} onApprove={() => {}} />);
    expect(date.mock.calls.length + time.mock.calls.length + format.mock.calls.length).toBe(0);
    expect(screen.getAllByTestId("bubble-ts").map((el) => [el.textContent, el.title])).toEqual(stamps);
  });

  it("reparses only an edited answer and updates its timestamp and copied raw text", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    const { rerender } = render(<Transcript items={history()} onApprove={() => {}} />);
    const previousStamp = screen.getAllByTestId("bubble-ts")[19].title;
    const items = history();
    items[19] = { kind: "assistant", text: "Updated **answer**", ts: 1_700_086_400 };
    rerender(<Transcript items={items} onApprove={() => {}} />);
    expect(parses.count).toBe(11);
    expect(screen.getByText("answer", { selector: "strong" })).toBeTruthy();
    expect(screen.queryByText("19", { selector: "strong" })).toBeNull();
    expect(screen.getAllByTestId("bubble-ts")[19].title).not.toBe(previousStamp);
    fireEvent.click(screen.getAllByTestId("bubble-copy")[19]);
    await waitFor(() => expect(screen.getByText("Copied")).toBeTruthy());
    expect(writeText).toHaveBeenCalledWith("Updated **answer**");
  });

  it("still updates translated controls inside unchanged Markdown and bubble metadata", async () => {
    i18n.addResourceBundle("fr", "translation", {
      rail: { open: "Open translated" },
      transcript: { copy_message: "Copy translated" },
    });
    render(<Transcript items={[{ kind: "assistant", text: "[Report](artifact:report.md)" }]} onApprove={() => {}} />);
    try {
      await act(async () => { await i18n.changeLanguage("fr"); });
      expect(screen.getByText("Open translated ›")).toBeTruthy();
      expect(screen.getByTestId("bubble-copy").title).toBe("Copy translated");
      expect(parses.count).toBe(1);
    } finally {
      await act(async () => { await i18n.changeLanguage("en"); });
      i18n.removeResourceBundle("fr", "translation");
    }
  });
});
