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
