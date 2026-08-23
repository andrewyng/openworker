// A run killed by a server restart must say so — live, and after a reload.
//
// The incident: the server was restarted 19 tool-steps into a turn. The reply in flight was
// discarded, the transcript ended at the last tool result with no marker, and the UI kept
// showing a live run that no longer existed.
import { expect } from "@playwright/test";
import { killSessionSocket, test } from "./fixtures";

test("the server going down mid-turn ends the run visibly, and says how to pick it up", async ({
  page,
}) => {
  await page.goto("/");
  const box = page.getByPlaceholder(/Ask the coworker/);
  await box.fill("kill the server");
  await box.press("Enter");

  await expect(page.getByText(/restarted while this run was working/).first()).toBeVisible({
    timeout: 10_000,
  });
  // No Resume button live: the socket dies with the server, so the frame would go nowhere.
  // The notice says what to do instead, and the persisted marker carries the button.
  await expect(page.getByText(/Reload once it is back/).first()).toBeVisible();
  await expect(page.getByTestId("notice-retry")).toHaveCount(0);

  // And the composer is usable again — no turn_done ever arrives, so a UI that waits for
  // one stays stuck behind a spinner on a run that is already dead.
  await expect(box).toBeEnabled();
});

test("losing the socket mid-turn says so too — nothing else will report on that run", async ({
  page,
}) => {
  await page.goto("/");
  const box = page.getByPlaceholder(/Ask the coworker/);
  // The slow stream keeps the turn open while the socket dies underneath it.
  await box.fill("stream the epic");
  await box.press("Enter");
  await expect(page.getByText(/The epic scrolls ever onward/).first()).toBeVisible({
    timeout: 10_000,
  });

  // A SIGKILLed server never gets its `run_interrupted` out; all the client sees is a close.
  await killSessionSocket(page);
  await expect(page.getByText(/Lost the connection to the agent server/).first()).toBeVisible({
    timeout: 10_000,
  });
});

test("the marker is still there after a reload, on a session nobody was watching", async ({
  page,
}) => {
  // The common case: the restart happened while the app was closed. The transcript is
  // whatever the server wrote — including the marker its reap appended.
  await page.route(/\/v1\/sessions\/[^/]+\/messages$/, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        messages: [
          { role: "user", content: "go" },
          { role: "assistant", tool_calls: [{ id: "t1", function: { name: "shell", arguments: "{}" } }] },
          {
            role: "tool",
            tool_call_id: "t1",
            content: '{"error":"tool call not executed","reason":"the server restarted"}',
          },
          { role: "notice", kind: "server_restart", text: "The agent server restarted mid-run." },
        ],
      }),
    }),
  );
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();

  await expect(page.getByText("The agent server restarted mid-run.").first()).toBeVisible({
    timeout: 10_000,
  });
  await expect(page.getByTestId("notice-retry")).toHaveText("Resume");
});
