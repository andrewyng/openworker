import { describe, expect, it } from "vitest";
import { shouldStartWindowDrag } from "./windowDrag";

const press = (target: EventTarget | null, button = 0) => ({ button, target });

describe("shouldStartWindowDrag", () => {
  it("drags from empty title-bar space", () => {
    expect(shouldStartWindowDrag(press(document.createElement("div")))).toBe(true);
  });

  it.each([
    '<button><svg><path /></svg></button>',
    '<a href="#"><span>link</span></a>',
    '<span role="button"><span>button</span></span>',
    '<span role="menuitem">item</span>',
    '<span role="link">link</span>',
    '<input />',
    '<textarea></textarea>',
    '<select><option>option</option></select>',
    '<label><span>label</span></label>',
    '<div contenteditable="true"><span>edit</span></div>',
  ])("does not start a native drag from an interactive control: %s", (html) => {
    const bar = document.createElement("div");
    bar.innerHTML = html;
    for (const target of bar.querySelectorAll("*")) {
      expect(shouldStartWindowDrag(press(target))).toBe(false);
    }
  });

  it.each([1, 2])("ignores mouse button %s", (button) => {
    expect(shouldStartWindowDrag(press(document.createElement("div"), button))).toBe(false);
  });

  it("tolerates targets without closest", () => {
    expect(shouldStartWindowDrag(press(null))).toBe(true);
    expect(shouldStartWindowDrag(press(document.createTextNode("title")))).toBe(true);
  });
});
