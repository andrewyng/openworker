// Rust and TypeScript ship the same versioned definitions. No runtime/model
// orchestration is generated here; this file copies configuration only.
import {readFile,writeFile} from "node:fs/promises";
const source=new URL("../../harness-sdk/src/agents/presets/coding-v1.json",import.meta.url);
const target=new URL("../src/coding-presets.ts",import.meta.url);
const data=JSON.parse(await readFile(source,"utf8"));
const output="// Generated from harness-sdk/src/agents/presets/coding-v1.json.\nexport const codingPresets = "+JSON.stringify(data,null,2)+" as const;\n";
if(process.argv.includes("--check")) {
  if(await readFile(target,"utf8")!==output)throw Error("Coding preset configuration is stale; run node scripts/sync-coding-presets.mjs");
} else await writeFile(target,output);
