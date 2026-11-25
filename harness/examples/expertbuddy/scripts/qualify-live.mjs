// Explicit opt-in: this script calls the configured live edge application and model.
import assert from 'node:assert/strict';
import {mkdir,writeFile} from 'node:fs/promises';
const url=process.env.INSPECTIONBUDDY_URL??'http://127.0.0.1:4336';
const cases=[{sampleId:'door-reference',part:'Door panel',station:'Paint inspection',notes:'Handle hardware is not fitted yet at this assembly stage. Inspect the painted panel surface.'},{sampleId:'weld-porosity',part:'Chassis weld',station:'Body assembly',notes:'Visual inspection of the visible weld bead. No dimensional or NDT measurements are available.'}];
const result=[];
for(const c of cases){
 const response=await fetch(url+'/api/inspections',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({...c,requestId:'live-qual-'+c.sampleId+'-v1'})});
 const record=await response.json();assert.ok(response.ok,JSON.stringify(record));
 const stream=await fetch(url+`/api/inspections/${record.id}/events`,{signal:AbortSignal.timeout(180000)});assert.ok(stream.ok);const reader=stream.body.getReader(),decoder=new TextDecoder();let buffer='',done=false,latest=record;
 try{while(!done){const chunk=await reader.read();assert.ok(!chunk.done,'Stream ended before inspection completed');buffer+=decoder.decode(chunk.value,{stream:true});let at;while((at=buffer.indexOf('\n\n'))>=0){const frame=buffer.slice(0,at);buffer=buffer.slice(at+2);const line=frame.split('\n').find(v=>v.startsWith('data: '));if(!line)continue;latest=JSON.parse(line.slice(6));if(latest.status!=='analyzing'){done=true;break;}}}}finally{await reader.cancel();}
 assert.equal(latest.status,'review',JSON.stringify(latest.error));assert.ok(latest.report.title);assert.ok(latest.service.session.sessionId ?? latest.service.session.session_id);assert.ok(latest.trace?.summary?.models?.length);assert.ok(latest.trace.summary.usage.input.total>0);assert.ok(latest.trace.summary.usage.output.total>0);
 const reopened=await(await fetch(url+`/api/inspections/${record.id}`)).json();assert.deepEqual(reopened.report,latest.report);assert.equal(reopened.decision,null);
 const photo=await fetch(url+`/api/inspections/${record.id}/photo`);assert.equal(photo.status,200);assert.ok((await photo.arrayBuffer()).byteLength>10000);
 result.push({sampleId:c.sampleId,id:latest.id,status:latest.status,report:latest.report,service:latest.service,usage:latest.trace.summary.usage});console.log('PASS',c.sampleId,latest.report.severity,latest.report.recommendation,latest.report.title);
}
await mkdir(new URL('../output/',import.meta.url),{recursive:true});await writeFile(new URL('../output/live-qualification.json',import.meta.url),JSON.stringify(result,null,2));
