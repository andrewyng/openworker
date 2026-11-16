// Called by the real PostgreSQL/two-node compliance API test. No mocked HTTP data.
import {start} from "../server.mjs";
import {createRequire} from "node:module";
import {mkdir} from "node:fs/promises";
import assert from "node:assert/strict";
const require=createRequire(import.meta.url);
const {chromium}=require(process.env.OW_PLAYWRIGHT_MODULE);
const out=process.env.OW_COMPLIANCE_UI_OUTPUT??"/tmp/ow-safety-qualification";
await mkdir(out,{recursive:true});
const ui=await start({host:"127.0.0.1",port:0,serviceUrl:process.env.OW_UI_TEST_URL,serviceId:"compliance-test",namespace:"test"});
const browser=await chromium.launch({headless:true});
try{
 const page=await browser.newPage({viewport:{width:1440,height:950}});const errors=[];page.on("pageerror",e=>errors.push(e.message));
 await page.goto(ui.url+"/#sessions/s_one");await page.locator("#token").fill(process.env.OW_UI_TEST_TOKEN);await page.locator("#login-form button[type=submit]").click();
 await page.getByRole("button",{name:"Safety",exact:true}).click();await page.locator(".safety-finding").first().waitFor();
 assert.match(await page.locator("#trace-content").innerText(),/pii/);assert.ok(!(await page.locator("#trace-content").innerText()).includes("PRIVATE_REJECTED_SENTINEL"));
 await page.screenshot({path:out+"/compliance-desktop.png",fullPage:true});
 await page.locator(".safety-finding summary").first().click();assert.match(await page.locator(".safety-finding pre").first().innerText(),/blocked/);
 await page.setViewportSize({width:390,height:844});await page.screenshot({path:out+"/compliance-mobile.png",fullPage:true});
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+1),"Mobile horizontal overflow");assert.deepEqual(errors,[]);
 console.log("Compliance UI qualified at desktop and mobile widths");
}finally{await browser.close();await ui.close();}
