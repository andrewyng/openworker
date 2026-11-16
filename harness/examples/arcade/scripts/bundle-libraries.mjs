// electron-builder prunes dependency examples. Explicitly stage only the addons
// we support into app-owned build resources before compiling/packaging.
import { createRequire } from 'node:module';
import { dirname, join } from 'node:path';
import { mkdirSync, copyFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
const require=createRequire(import.meta.url);
const threeBuild=dirname(require.resolve('three'));
const out=fileURLToPath(new URL('../build/vendor/three-addons/',import.meta.url));
for(const file of ['loaders/GLTFLoader.js','utils/BufferGeometryUtils.js','utils/SkeletonUtils.js']){
 const dest=join(out,file);mkdirSync(dirname(dest),{recursive:true});
 copyFileSync(join(threeBuild,'../examples/jsm',file),dest);
}
