import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { readFileSync, statSync } from "node:fs";
import { createHash } from "node:crypto";
import { SDK_VERSION } from "./version.js";
import { HarnessError } from "./types.js";
const require = createRequire(import.meta.url);

export function validateRuntimePackage(directory:string, platform=process.platform, arch=process.arch):string {
  const pkg=JSON.parse(readFileSync(join(directory,"package.json"),"utf8"));
  const metadata=JSON.parse(readFileSync(join(directory,"runtime.json"),"utf8"));
  if(pkg.version!==SDK_VERSION||metadata.version!==SDK_VERSION||metadata.platform!==platform||metadata.arch!==arch)
    throw new HarnessError("Setup","Installed runtime package does not match this SDK/platform");
  const path=join(directory,"bin",platform==="win32"?"openworker-harness.exe":"openworker-harness");
  if(!statSync(path).isFile()||statSync(path).size>200*1024*1024)
    throw new HarnessError("Setup","Invalid runtime executable");
  const digest=createHash("sha256").update(readFileSync(path)).digest("hex");
  if(digest!==metadata.sha256)throw new HarnessError("Setup","Runtime checksum mismatch; reinstall the matching package");
  return path;
}
export function installedRuntime():string|undefined {
  if(!["darwin","linux","win32"].includes(process.platform)||!["arm64","x64"].includes(process.arch))return undefined;
  if(process.platform==="linux") {
    const report=process.report?.getReport() as {header?:{glibcVersionRuntime?:string}}|undefined;
    const header=report?.header;
    if(!header?.glibcVersionRuntime)return undefined;
  }
  let manifest:string;
  try {manifest=require.resolve(`@openworker/harness-runtime-${process.platform}-${process.arch}/package.json`);}
  catch(e) {if((e as NodeJS.ErrnoException).code==="MODULE_NOT_FOUND")return undefined;throw e;}
  return validateRuntimePackage(dirname(manifest));
}
/** Installed native host for bundling into a desktop application. No network or PATH search. */
export function resolveRuntimeExecutable():string {
  const executable=installedRuntime();
  if(!executable)throw new HarnessError("Setup",`No packaged runtime for ${process.platform}/${process.arch}. Install optional dependencies or supply an explicit matching executable.`);
  return executable;
}
export function validateRuntimeVersion(info:Record<string,unknown>,required=false):void {
  if((typeof info.sdk_version==="string"&&info.sdk_version!==SDK_VERSION)||(required&&info.sdk_version!==SDK_VERSION))
    throw new HarnessError("Setup",`SDK ${SDK_VERSION} requires the matching harness release; received ${String(info.sdk_version??"unversioned")}`);
}
