// Composer draft persistence (UX: an unsent message survives switching conversations and
// navigating to another page/surface and back). See composerDraft.ts.
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { Composer } from "./Composer";
import { loadDraft, saveDraft } from "./composerDraft";

const props = (extra: Partial<Parameters<typeof Composer>[0]> = {}) => ({
  mode: "interactive",
  model: "gpt-5.6-sol",
  running: false,
  connected: true,
  sessionId: "s1",
  onSend: vi.fn(),
  onInterrupt: vi.fn(),
  onModeChange: vi.fn(),
  onModelChange: vi.fn(),
  ...extra,
});

const box = () => screen.getByPlaceholderText(/Ask the coworker/) as HTMLTextAreaElement;
const type = (text: string) => fireEvent.change(box(), { target: { value: text } });

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  localStorage.clear();
});

describe("Composer draft persistence", () => {
  it("keeps an unsent message when switching to another conversation and back", () => {
    const { rerender } = render(<Composer {...props({ sessionId: "s1", resetKey: "s1" })} />);
    type("write a script that copies invoices");
    expect(box().value).toBe("write a script that copies invoices");

    // Switch to another conversation (same mounted Composer, new resetKey).
    rerender(<Composer {...props({ sessionId: "s2", resetKey: "s2" })} />);
    expect(box().value).toBe(""); // new conversation starts empty

    // Come back to the original one.
    rerender(<Composer {...props({ sessionId: "s1", resetKey: "s1" })} />);
    expect(box().value).toBe("write a script that copies invoices");
  });

  it("restores a draft saved to localStorage on mount (page navigation round-trip)", () => {
    // Simulate navigating away: mount, type, then unmount (localStorage gets the draft).
    const { unmount } = render(<Composer {...props({ sessionId: "s1", resetKey: "s1" })} />);
    type("half a message for later");
    unmount();

    // Navigate back: a fresh Composer mount for the same session restores the draft.
    render(<Composer {...props({ sessionId: "s1", resetKey: "s1" })} />);
    expect(box().value).toBe("half a message for later");
  });

  it("does NOT restore a draft once the message was actually sent", () => {
    const onSend = vi.fn();
    const { rerender } = render(
      <Composer {...props({ sessionId: "s1", resetKey: "s1", onSend })} />,
    );
    type("this one gets sent");
    fireEvent.keyDown(box(), { key: "Enter" });
    expect(onSend).toHaveBeenCalledWith("this one gets sent", [], undefined);

    // Switch away and back: nothing should be restored.
    rerender(<Composer {...props({ sessionId: "s2", resetKey: "s2" })} />);
    rerender(<Composer {...props({ sessionId: "s1", resetKey: "s1" })} />);
    expect(box().value).toBe("");
    expect(loadDraft("s1")).toBeUndefined();
  });

  it("persists attachments and the picked skill through the store (serialization round-trip)", () => {
    const attachment = {
      kind: "pdf" as const,
      name: "brief.pdf",
      mime: "application/pdf",
      data_url: "data:application/pdf;base64,AAAA",
    };
    const skill = { name: "weekly-report", description: "Monday report", scope: "global" as const, enabled: true };
    saveDraft("s1", { text: "summarize the attached", attachments: [attachment], skill });

    const restored = loadDraft("s1");
    expect(restored?.text).toBe("summarize the attached");
    expect(restored?.attachments).toEqual([attachment]);
    expect(restored?.skill).toEqual(skill);

    // Empty drafts are pruned rather than left behind.
    saveDraft("s1", { text: "   ", attachments: [], skill: null });
    expect(loadDraft("s1")).toBeUndefined();
  });

  it("leaves other sessions' drafts untouched while switching", () => {
    const { rerender } = render(<Composer {...props({ sessionId: "s1", resetKey: "s1" })} />);
    type("draft for session one");
    rerender(<Composer {...props({ sessionId: "s2", resetKey: "s2" })} />);
    type("draft for session two");
    rerender(<Composer {...props({ sessionId: "s1", resetKey: "s1" })} />);
    expect(box().value).toBe("draft for session one");
  });
});
