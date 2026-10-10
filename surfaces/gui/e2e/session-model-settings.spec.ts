import { expect, type Page } from "@playwright/test";
import { test } from "./fixtures";

// UX-056: beside the model, a grey reasoning-effort pill (slider over the model's own
// levels) and a brain for the thinking switch — each only when the model has it.
const NONE = { thinking: { support: "not_supported" }, reasoning: { support: "not_supported" } };
const CLAUDE = {
  thinking: { support: "not_supported" },
  reasoning: { support: "supported", levels: ["low", "medium", "high", "xhigh", "max"], default: "high" },
};
const NEMOTRON = { thinking: { support: "supported", default: true }, reasoning: { support: "not_supported" } };

async function serve(page: Page, controls: object) {
  const state = { thinking: null as boolean | null, reasoning_effort: null as string | null };
  const posted: object[] = [];
  await page.route(/\/v1\/sessions\/[^/]+\/model-settings/, async (route) => {
    if (route.request().method() === "POST") {
      const body = route.request().postDataJSON();
      posted.push(body);
      Object.assign(state, body);
      return route.fulfill({ json: { ok: true, ...state, controls } });
    }
    return route.fulfill({ json: { ...state, controls } });
  });
  return posted;
}

test("nothing beside the model when it has neither control", async ({ page }) => {
  await serve(page, NONE);
  await page.goto("/");
  await expect(page.getByPlaceholder(/Ask the coworker/)).toBeVisible();
  await expect(page.getByTestId("effort-pill")).toHaveCount(0);
  await expect(page.getByTestId("thinking-toggle")).toHaveCount(0);
});

test("reasoning effort: the pill shows the level; the slider marks the default and resets", async ({ page }) => {
  const posted = await serve(page, CLAUDE);
  await page.goto("/");
  const pill = page.getByTestId("effort-pill");
  await expect(pill).toHaveText("High");
  await pill.click();
  const menu = page.getByTestId("effort-menu");
  await expect(menu).toContainText("Reasoning effort");
  await expect(menu).toContainText("Extra high");
  await expect(menu).toContainText("Default");
  await expect(menu).not.toContainText("Off");
  await expect(page.getByTestId("effort-reset")).toHaveCount(0);
  // The lowest level sits at the very left end of the track: the thumb touches it.
  const slider = page.getByTestId("effort-slider");
  const track = (await slider.boundingBox())!;
  await page.mouse.click(track.x + 4, track.y + track.height / 2);
  await expect(pill).toHaveText("Low");
  // The thumb slides into place; wait for it to settle at the left end.
  await expect.poll(async () => (await page.getByTestId("effort-thumb").boundingBox())!.x - track.x).toBeLessThanOrEqual(4);
  await expect(page.getByTestId("effort-reset")).toHaveText("Reset to High");
  await page.getByTestId("effort-reset").click();
  await expect(pill).toHaveText("High");
  expect(posted).toEqual([{ reasoning_effort: "low" }, { reasoning_effort: null }]);
});

test("reasoning effort: drag to the far right end, and the keys step and jump", async ({ page }) => {
  const posted = await serve(page, CLAUDE);
  await page.goto("/");
  await page.getByTestId("effort-pill").click();
  const slider = page.getByTestId("effort-slider");
  const track = (await slider.boundingBox())!;
  const y = track.y + track.height / 2;
  // Drag from the default all the way past the right end.
  await page.mouse.move(track.x + track.width / 2, y);
  await page.mouse.down();
  await page.mouse.move(track.x + track.width + 40, y, { steps: 5 });
  await page.mouse.up();
  await expect(page.getByTestId("effort-pill")).toHaveText("Max");
  await expect
    .poll(async () => {
      const thumb = (await page.getByTestId("effort-thumb").boundingBox())!;
      return track.x + track.width - (thumb.x + thumb.width);
    })
    .toBeLessThanOrEqual(4);
  await slider.focus();
  await page.keyboard.press("ArrowLeft");
  await expect(page.getByTestId("effort-pill")).toHaveText("Extra high");
  await page.keyboard.press("Home");
  await expect(page.getByTestId("effort-pill")).toHaveText("Low");
  await expect(slider).toHaveAttribute("aria-valuetext", "Low");
  expect(posted).toEqual([{ reasoning_effort: "max" }, { reasoning_effort: "xhigh" }, { reasoning_effort: "low" }]);
  await expect(page.getByTestId("thinking-toggle")).toHaveCount(0);
});

test("thinking: the brain flips it and back to the default", async ({ page }) => {
  const posted = await serve(page, NEMOTRON);
  await page.goto("/");
  const brain = page.getByTestId("thinking-toggle");
  await expect(brain).toHaveAttribute("aria-pressed", "true");
  await brain.click();
  await expect(brain).toHaveAttribute("aria-pressed", "false");
  await brain.click();
  await expect(brain).toHaveAttribute("aria-pressed", "true");
  expect(posted).toEqual([{ thinking: false }, { thinking: null }]);
  await expect(page.getByTestId("effort-pill")).toHaveCount(0);
});
