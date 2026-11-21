import {test} from "node:test";
import assert from "node:assert/strict";
import {renderCompliance} from "../public/compliance.js";
import {allowed} from "../server.mjs";
test("metadata panel escapes fields and exposes bounded pagination and delivery",()=>{
 const html=renderCompliance({activities:[{id:"a",kind:"safety_evaluation",facts:{check:"<script>",outcome:"blocked",phase:"modelOutput",durationMs:12}}],hasMore:true,sinks:[{sink:"siem",pending:4,failures:1}]});
 assert.ok(html.includes("&lt;script&gt;"));assert.ok(!html.includes("<script>"));assert.ok(html.includes("Load more"));assert.ok(html.includes("4 pending"));assert.ok(html.includes("retry scheduled"));
 assert.ok(renderCompliance({error:"Denied"}).includes("Denied"));
});
test("browser compliance proxy admits only bounded metadata reads",()=>{
 for(const path of ["/v1/compliance/sessions/s_one/activities?after=12&limit=100","/v1/compliance/sessions/s_one/delivery"])assert.ok(allowed("GET",path));
 for(const path of ["/v1/compliance/sessions/s_one/content","/v1/compliance/sessions/s_one/activities?limit=501","/v1/compliance/sessions/s_one/activities?after=1&after=2","/v1/compliance/sessions/s_one/activities?includeContent=true"])assert.ok(!allowed("GET",path));
 assert.ok(!allowed("POST","/v1/compliance/sessions/s_one/activities"));
});
test("compliance catalogs and export admit only their bounded query and method",()=>{
 for(const path of [
   "/v1/compliance/decisions?app=app_finance&agent=invoice%401&from_ms=2&until_ms=3",
   "/v1/compliance/decisions?before_us=1795190000000123&before_id=s_one%3A42&limit=100",
   "/v1/compliance/agents?after=app_finance%3Ainvoice%401",
   "/v1/compliance/audit?before=100&app=app_finance&q=policy+change",
 ]) assert.ok(allowed("GET",path),path);
 for(const path of [
   "/v1/compliance/decisions?limit=101","/v1/compliance/decisions?before_us=1",
   "/v1/compliance/decisions?before_id=s_one%3A42","/v1/compliance/decisions?include_content=true",
   "/v1/compliance/decisions?app=app_finance&app=app_support",
   "/v1/compliance/audit?outcome=blocked","/v1/compliance/agents?limit=50",
   "/v1/compliance/decisions?q=%00","/v1/compliance/export",
 ]) assert.ok(!allowed("GET",path),path);
 assert.ok(allowed("POST","/v1/compliance/export"));
 assert.ok(allowed("GET","/v1/compliance/delivery"));
 assert.ok(allowed("POST","/v1/compliance/delivery"));
 assert.ok(!allowed("GET","/v1/compliance/delivery?credentials=true"));
 assert.ok(!allowed("POST","/v1/compliance/export?app=app_support"));
 assert.ok(!allowed("POST","/v1/compliance/audit"));
});
