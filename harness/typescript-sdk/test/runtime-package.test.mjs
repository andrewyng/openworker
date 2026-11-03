import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createHash} from 'node:crypto';
import {validateRuntimePackage,validateRuntimeVersion} from '../dist/runtime-package.js';
import {SDK_VERSION} from '../dist/version.js';
test('native package identity, checksum and release negotiation fail closed',async()=>{
 const root=await mkdtemp(join(tmpdir(),'ow-native-package-'));
 try {
  await mkdir(join(root,'bin'));const data=Buffer.from('fixture-not-executed');
  await writeFile(join(root,'bin','openworker-harness'),data);
  await writeFile(join(root,'package.json'),JSON.stringify({version:SDK_VERSION}));
  const metadata={version:SDK_VERSION,platform:'darwin',arch:'arm64',sha256:createHash('sha256').update(data).digest('hex')};
  await writeFile(join(root,'runtime.json'),JSON.stringify(metadata));
  assert.equal(validateRuntimePackage(root,'darwin','arm64'),join(root,'bin','openworker-harness'));
  assert.throws(()=>validateRuntimePackage(root,'linux','arm64'));
  await writeFile(join(root,'bin','openworker-harness'),'corrupted');assert.throws(()=>validateRuntimePackage(root,'darwin','arm64'),/checksum/);
  validateRuntimeVersion({sdk_version:SDK_VERSION},true);
  validateRuntimeVersion({},false); // Explicit legacy/custom host only.
  assert.throws(()=>validateRuntimeVersion({},true));
  assert.throws(()=>validateRuntimeVersion({sdk_version:'0.0.0'}));
 }finally{await rm(root,{recursive:true,force:true});}
});
