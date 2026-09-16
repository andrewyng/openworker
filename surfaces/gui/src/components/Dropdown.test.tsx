// The composer's model chip must let you type a model id the curated list doesn't have —
// the backend already accepts any string ("Adding any model string works at your own risk"),
// but a select-only picker gave no way in. `editable` turns the menu into a combobox while
// leaving every existing (non-editable) Dropdown untouched.
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Dropdown, type Option } from "./Dropdown";

afterEach(cleanup);

const OPTIONS: Option[] = [
  { value: "gpt-5.6-sol", label: "GPT-5.6 Sol" },
  { value: "anthropic:claude-fable-5", label: "Claude Fable 5" },
];

const open = () => fireEvent.click(screen.getByRole("button", { name: /GPT-5.6 Sol/ }));

describe("Dropdown (select mode, unchanged)", () => {
  it("picks an option and closes", () => {
    const onChange = vi.fn();
    render(<Dropdown value="gpt-5.6-sol" options={OPTIONS} onChange={onChange} />);
    open();
    fireEvent.click(screen.getByText("Claude Fable 5"));
    expect(onChange).toHaveBeenCalledWith("anthropic:claude-fable-5");
  });

  it("shows no filter box unless editable", () => {
    render(<Dropdown value="gpt-5.6-sol" options={OPTIONS} onChange={() => {}} />);
    open();
    expect(screen.queryByRole("combobox")).toBeNull();
  });
});

describe("Dropdown (editable combobox)", () => {
  const renderEditable = (onChange = vi.fn()) => {
    render(<Dropdown value="gpt-5.6-sol" options={OPTIONS} onChange={onChange} editable />);
    open();
    return onChange;
  };

  it("commits a typed model id that is not in the list", () => {
    const onChange = renderEditable();
    const input = screen.getByRole("combobox");
    fireEvent.change(input, { target: { value: "ollama:qwen3-coder:30b" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onChange).toHaveBeenCalledWith("ollama:qwen3-coder:30b");
  });

  it("trims whitespace and ignores an empty commit", () => {
    const onChange = renderEditable();
    const input = screen.getByRole("combobox");
    fireEvent.change(input, { target: { value: "  spaced:model  " } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onChange).toHaveBeenCalledWith("spaced:model");

    onChange.mockClear();
    fireEvent.change(input, { target: { value: "   " } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onChange).not.toHaveBeenCalled();
  });

  it("filters the curated options as you type", () => {
    renderEditable();
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "claude" } });
    // Scope to the menu — the trigger pill still shows the current model's label.
    const shown = screen.getAllByRole("option").map((o) => o.textContent);
    expect(shown.some((t) => t?.includes("Claude Fable 5"))).toBe(true);
    expect(shown.some((t) => t?.includes("GPT-5.6 Sol"))).toBe(false);
  });

  it("still lets you click a curated option", () => {
    const onChange = renderEditable();
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "claude" } });
    fireEvent.click(screen.getByText("Claude Fable 5"));
    expect(onChange).toHaveBeenCalledWith("anthropic:claude-fable-5");
  });

  it("selects the highlighted option with the arrow keys", () => {
    const onChange = renderEditable();
    const input = screen.getByRole("combobox");
    fireEvent.keyDown(input, { key: "ArrowDown" });
    fireEvent.keyDown(input, { key: "ArrowDown" });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onChange).toHaveBeenCalledWith("anthropic:claude-fable-5");
  });

  it("closes on Escape without committing, and keeps Escape from reaching the composer", () => {
    // Composer binds Escape to Stop — an open picker must claim it first, so we assert the
    // event never reaches an ancestor handler.
    const onChange = vi.fn();
    const onAncestorKeyDown = vi.fn();
    render(
      <div onKeyDown={onAncestorKeyDown}>
        <Dropdown value="gpt-5.6-sol" options={OPTIONS} onChange={onChange} editable />
      </div>,
    );
    open();
    const input = screen.getByRole("combobox");
    fireEvent.change(input, { target: { value: "draft:model" } });
    fireEvent.keyDown(input, { key: "Escape" });

    expect(onChange).not.toHaveBeenCalled();
    expect(screen.queryByRole("combobox")).toBeNull();
    expect(onAncestorKeyDown).not.toHaveBeenCalled();
  });

  it("exposes combobox/listbox semantics", () => {
    renderEditable();
    const input = screen.getByRole("combobox");
    expect(input.getAttribute("aria-expanded")).toBe("true");
    expect(input.getAttribute("aria-autocomplete")).toBe("list");
    expect(screen.getByRole("listbox")).toBeTruthy();
    expect(screen.getAllByRole("option").length).toBe(2);
  });
});
