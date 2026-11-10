import test from 'node:test';
import assert from 'node:assert/strict';
import {sourceTree,visibleSourceRows,sourceLanguage} from '../src/lib/source-tree.mjs';
const files=['README.md','src/agent.ts','rust/src/main.rs','src/directory.ts'].map(path=>({path,content:`Source ${path}`}));
test('source folders collapse independently and nested filtering reveals matching ancestors',()=>{
 const tree=sourceTree(files);
 assert.deepEqual(visibleSourceRows(tree,new Set()).map(r=>r.node.path),['rust','src','README.md']);
 assert.deepEqual(visibleSourceRows(tree,new Set(['src'])).map(r=>r.node.path),['rust','src','src/agent.ts','src/directory.ts','README.md']);
 const filtered=visibleSourceRows(tree,new Set(),'main.rs');
 assert.deepEqual(filtered.map(r=>[r.node.path,r.depth]),[['rust',1],['rust/src',2],['rust/src/main.rs',3]]);
 assert.equal(filtered.at(-1).node.file.content,'Source rust/src/main.rs');
 assert.equal(visibleSourceRows(tree,new Set(),'not-found').length,0);
});
test('invalid or conflicting manifest paths fail instead of producing an ambiguous tree',()=>{
 for(const paths of [['../secret'],['/absolute'],['a\\b'],['same','same'],['a','a/b']])assert.throws(()=>sourceTree(paths.map(path=>({path,content:''}))));
});
test('supported source files select the expected language',()=>{
 for(const [path,id]of [['src/agent.ts','ts'],['main.rs','rust'],['Cargo.toml','toml'],['Dockerfile','docker'],['README.md','markdown'],['a.mjs','js'],['a.json','json'],['a.html','html'],['a.css','css'],['Dockerfile.dockerignore','text']])assert.equal(sourceLanguage(path).id,id);
});
