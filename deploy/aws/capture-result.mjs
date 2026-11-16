// Read-only capture of the existing live result after the final UI-only update.
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { repo } from "../helm/local.mjs";
const { chromium } = await import(
  process.env.OW_PLAYWRIGHT_MODULE || "playwright"
);
const root = join(repo, ".ow-aws"),
  origin = "http://127.0.0.1:54350";
for (let i = 0; i < 30; i++) {
  try {
    if ((await fetch(origin, { signal: AbortSignal.timeout(1000) })).ok) break;
  } catch {}
  await new Promise((r) => setTimeout(r, 1000));
}
const proof = JSON.parse(
  await readFile(join(root, "taskdesk-test/live-qualification.json"), "utf8"),
);
const browser = await chromium.launch({ headless: true }),
  page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
try {
  await page.goto(origin + "/#" + proof.session);
  await page.locator("#key").waitFor();
  await page
    .locator("#key")
    .fill((await readFile(join(root, "access"), "utf8")).trim());
  await page.locator("#login-form button").click();
  await page.locator(".artifact").first().waitFor();
  await page.screenshot({
    path: join(root, "taskdesk-test/live.png"),
    fullPage: true,
  });
  console.log("Saved final live-result screenshot; no new model task.");
} finally {
  await browser.close();
}
