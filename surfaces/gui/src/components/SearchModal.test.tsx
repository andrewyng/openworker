import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { SessionInfo } from "../types";
import { SearchModal } from "./SearchModal";

const sessions: SessionInfo[] = [
  {
    session_id: "s1",
    title: "Beijing launch notes",
    workspace: "",
    agent: "chat",
    model: "gpt-5.6-sol",
    mode: "interactive",
    updated_at: "2026-07-25T10:00:00Z",
    messages: 2,
  },
];

afterEach(cleanup);

describe("SearchModal keyboard selection", () => {
  it("does not open a chat when Enter confirms an active IME composition", () => {
    const onSelect = vi.fn();
    const onClose = vi.fn();
    render(<SearchModal sessions={sessions} onSelect={onSelect} onClose={onClose} />);

    const input = screen.getByPlaceholderText("Search chats");
    fireEvent.change(input, { target: { value: "北京" } });
    fireEvent.keyDown(input, { key: "Enter", isComposing: true });
    fireEvent.keyDown(input, { key: "Enter", keyCode: 229 });

    expect(onSelect).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
    expect((input as HTMLInputElement).value).toBe("北京");
  });

  it("still opens the highlighted chat on a normal Enter", () => {
    const onSelect = vi.fn();
    const onClose = vi.fn();
    render(<SearchModal sessions={sessions} onSelect={onSelect} onClose={onClose} />);

    fireEvent.keyDown(screen.getByPlaceholderText("Search chats"), { key: "Enter" });

    expect(onSelect).toHaveBeenCalledWith("s1", "", "chat");
    expect(onClose).toHaveBeenCalledOnce();
  });
});
