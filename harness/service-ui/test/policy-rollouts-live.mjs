// Called by the PostgreSQL scanner qualification. Uses the real Management UI and API.
import {start} from "../server.mjs";
import {createRequire} from "node:module";
import {mkdir} from "node:fs/promises";
import assert from "node:assert/strict";

const require = createRequire(import.meta.url);
const {chromium} = require(process.env.OW_PLAYWRIGHT_MODULE);
const out = process.env.OW_COMPLIANCE_UI_OUTPUT ?? "/tmp/ow-rollout-qualification";
await mkdir(out, {recursive:true});
const ui = await start({host:"127.0.0.1", port:0, serviceUrl:process.env.OW_UI_TEST_URL,
  serviceId:"compliance-test", namespace:"test"});
const browser = await chromium.launch({headless:true, executablePath:process.env.OW_COMPLIANCE_CHROME});

try {
  const page = await browser.newPage({viewport:{width:1340,height:842}});
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto(ui.url + "/#compliance/rollouts");
  await page.locator("#token").fill(process.env.OW_UI_TEST_TOKEN);
  await page.locator("#login-form button[type=submit]").click();
  const row = page.getByRole("row").filter({hasText:"app_finance"});
  await row.getByText("Shadow",{exact:true}).waitFor();
  await page.screenshot({path:out+"/policy-rollouts.png"});
  await row.getByRole("link",{name:"Review decisions"}).click();
  await page.getByRole("cell",{name:"Would have blocked",exact:true}).first().waitFor();
  await page.getByRole("cell",{name:"Shadow check unavailable",exact:true}).first().waitFor();
  await page.getByRole("cell",{name:"Alerted",exact:true}).first().waitFor();
  await page.getByRole("combobox",{name:"Outcome",exact:true}).selectOption("would_block");
  await page.getByRole("button",{name:"Apply",exact:true}).click();
  await page.getByText("Page 1 · 2 shown",{exact:true}).waitFor();
  const detail = page.getByRole("complementary",{name:"Selected decision"});
  await detail.getByText("Observation only",{exact:true}).waitFor();
  assert.match(await detail.getByRole("link",{name:"Open source trace",exact:true}).getAttribute("href"), /s_shadow_finance\//);
  await page.screenshot({path:out+"/shadow-decisions.png"});
  await page.setViewportSize({width:390,height:844});
  await page.screenshot({path:out+"/shadow-decisions-mobile.png"});
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth<=window.innerWidth+1), "Mobile decision overflow");

  await page.getByRole("link",{name:"Policy rollouts",exact:true}).click();
  await row.getByRole("button",{name:/Edit Content scanner/}).click();
  await page.getByRole("combobox",{name:"Mode"}).selectOption("enforce");
  await page.getByText(/next affected operation/).waitFor();
  await page.screenshot({path:out+"/policy-rollouts-mobile.png"});
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth<=window.innerWidth+1), "Mobile rollout overflow");
  await page.getByRole("button",{name:"Save rollout"}).click();
  await row.getByText("Enforce",{exact:true}).waitFor();
  await page.reload();
  await row.getByText("Enforce",{exact:true}).waitFor();

  await page.getByRole("button",{name:"Add rollout"}).click();
  await page.getByLabel("App ID").fill("app_support");
  await page.getByRole("button",{name:"Save rollout"}).click();
  const support = page.getByRole("row").filter({hasText:"app_support"});
  await support.getByText("Shadow",{exact:true}).waitFor();
  await support.getByRole("button",{name:/Edit Content scanner/}).click();
  await page.getByRole("combobox",{name:"Mode"}).selectOption("inherit");
  await page.getByRole("button",{name:"Save rollout"}).click();
  await support.waitFor({state:"hidden"});
  await row.getByText("Enforce",{exact:true}).waitFor();
  assert.deepEqual(errors, []);
} finally {
  await browser.close();
  ui.server.close();
}
