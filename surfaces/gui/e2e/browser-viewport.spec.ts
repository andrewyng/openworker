import { expect } from "@playwright/test";
import { test } from "./fixtures";

const FRAME =
  "data:image/svg+xml;charset=utf-8," +
  encodeURIComponent(`
    <svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720">
      <rect width="1280" height="720" fill="#f5f1e8"/>
      <rect x="90" y="80" width="1100" height="560" rx="20" fill="#fff" stroke="#d8dce1"/>
      <text x="145" y="170" font-family="system-ui" font-size="36" fill="#17191c">Browser Use fixture</text>
      <rect x="145" y="240" width="280" height="70" rx="12" fill="#2563eb"/>
      <text x="205" y="286" font-family="system-ui" font-size="22" fill="#fff">Open report</text>
    </svg>
  `);

function browserState() {
  return {
    open: true,
    status: "ready",
    active_tab_id: "tab_e2e",
    tabs: [
      {
        tab_id: "tab_e2e",
        title: "Browser Use fixture",
        url: "https://example.test/report",
        active: true,
        loading: false,
        can_go_back: true,
        can_go_forward: false,
      },
    ],
    screenshot_data_url: FRAME,
    frame_id: "frame_e2e",
    frame_sequence: 10,
    viewport_width: 1280,
    viewport_height: 720,
    dpr: 1,
  };
}

test("in-app Browser streams a frame, shows the agent pointer, and forwards shared human input", async ({
  page,
}) => {
  const browserRequests: { path: string; body: any }[] = [];
  const socketMessages: any[] = [];
  let pushBrowserMessage: ((message: Record<string, unknown>) => void) | null = null;

  await page.route("**/v1/browser/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const body = request.postDataJSON?.() || {};
    browserRequests.push({ path, body });
    if (path.endsWith("/close")) {
      return route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ ok: true }),
      });
    }
    return route.fulfill({
      contentType: "application/json",
      body: JSON.stringify(
        request.method() === "GET"
          ? browserState()
          : { ok: true, ...browserState() },
      ),
    });
  });

  await page.routeWebSocket(/\/ws\/browser\//, (socket) => {
    pushBrowserMessage = (message) => socket.send(JSON.stringify(message));
    socket.onMessage((message) => {
      try {
        socketMessages.push(JSON.parse(String(message)));
      } catch {
        socketMessages.push(message);
      }
    });
    setTimeout(() => {
      socket.send(
        JSON.stringify({
          type: "state",
          ...browserState(),
        }),
      );
      socket.send(
        JSON.stringify({
          type: "frame",
          tab_id: "tab_e2e",
          frame_id: "frame_e2e",
          sequence: 10,
          mime_type: "image/svg+xml",
          width: 1280,
          height: 720,
          metadata: { viewport_width: 1280, viewport_height: 720, dpr: 1 },
          data_url: FRAME,
        }),
      );
      socket.send(
        JSON.stringify({
          type: "visual_action",
          action_id: "act_e2e",
          tab_id: "tab_e2e",
          snapshot_id: "snap_e2e",
          frame_id: "frame_e2e",
          sequence: 12,
          phase: "move",
          kind: "click",
          target: { ref: "e7", x: 285, y: 275 },
          viewport: { width: 1280, height: 720, dpr: 1 },
        }),
      );
    }, 80);
  });

  await page.goto("/");

  await page.getByRole("button", { name: "Add workspace tab" }).click();
  await page.getByRole("menuitem", { name: "Browser" }).click();
  await expect(page.getByRole("tab", { name: /Browser/ })).toHaveAttribute(
    "aria-selected",
    "true",
  );

  const browser = page.getByTestId("browser-viewport");
  await expect(browser).toBeVisible();
  await expect(page.locator(".right-rail")).toHaveClass(/browser-mode/);
  await expect(browser.getByLabel("Address")).toHaveValue(
    "https://example.test/report",
  );
  await expect(browser.locator(".browser-frame")).toHaveAttribute(
    "data-frame-id",
    "frame_e2e",
  );
  await expect(browser.locator(".browser-ghost-cursor")).toHaveAttribute(
    "data-action-id",
    "act_e2e",
  );

  await expect(
    browser.getByRole("button", { name: /(?:Take|Return) control/ }),
  ).toHaveCount(0);

  pushBrowserMessage?.({
    type: "browser_dialog",
    tab_id: "tab_e2e",
    dialog_type: "prompt",
    message: "Name this report",
    default_value: "Draft",
  });
  const dialogInput = browser.getByRole("textbox", {
    name: "Page dialog response",
  });
  await expect(dialogInput).toHaveValue("Draft");
  await dialogInput.fill("Launch report");
  await browser.getByRole("button", { name: "Accept" }).click();
  expect(
    browserRequests.some(
      (request) =>
        request.path.endsWith("/dialog") &&
        request.body.action === "accept" &&
        request.body.prompt_text === "Launch report",
    ),
  ).toBe(true);

  const stage = browser.getByLabel("Browser content");
  await stage.click({ position: { x: 330, y: 240 } });
  const address = browser.getByLabel("Address");
  await address.fill("openworker.example");
  await address.press("Enter");

  await expect
    .poll(() => socketMessages.some((message) => message.type === "pointer"))
    .toBe(true);
  await expect
    .poll(() =>
      socketMessages.some(
        (message) =>
          message.type === "navigate" &&
          message.url === "https://openworker.example",
      ),
    )
    .toBe(true);

  await browser.getByRole("button", { name: "Go back" }).click();
  expect(
    browserRequests.some(
      (request) =>
        request.path.endsWith("/history") && request.body.action === "back",
    ),
  ).toBe(true);

  await page.getByRole("button", { name: "Close Browser" }).click();
  await expect(page.getByRole("tab", { name: /Browser/ })).toHaveCount(0);
  await expect(page.locator(".right-rail")).not.toHaveClass(/browser-mode/);
  expect(
    browserRequests.some((request) => request.path.endsWith("/close")),
  ).toBe(true);

  await page.getByRole("button", { name: "Add workspace tab" }).click();
  await page.getByRole("menuitem", { name: "Browser" }).click();
  await expect(browser).toBeVisible();
  await expect(page.getByRole("tab", { name: /Browser/ })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  expect(
    browserRequests.some((request) => request.path.endsWith("/open")),
  ).toBe(true);
});
