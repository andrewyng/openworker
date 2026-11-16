import test from 'node:test';
import assert from 'node:assert/strict';
import {windowsImports,validateWindowsRuntime} from './windows-imports.mjs';

// Minimal PE data for parser tests only; this is never run or packaged as a host.
function fixture(names,machine=0x8664) {
 const b=Buffer.alloc(4096),pe=0x80,optional=pe+24,section=optional+240;
 b.write('MZ');b.writeUInt32LE(pe,60);b.write('PE\0\0',pe);
 b.writeUInt16LE(machine,pe+4);b.writeUInt16LE(1,pe+6);b.writeUInt16LE(240,pe+20);
 b.writeUInt16LE(0x20b,optional);b.writeUInt32LE(16,optional+108);
 b.writeUInt32LE(0x1100,optional+120);b.writeUInt32LE((names.length+1)*20,optional+124);
 b.writeUInt32LE(0xe00,section+8);b.writeUInt32LE(0x1000,section+12);
 b.writeUInt32LE(0xe00,section+16);b.writeUInt32LE(0x200,section+20);
 let cursor=0x600;
 for(const [i,name] of names.entries()) {
  b.writeUInt32LE(0x1000+cursor-0x200,0x300+i*20+12);
  b.write(name+'\0',cursor);cursor+=name.length+1;
 }
 return b;
}
test('Windows package accepts OS libraries on x64 and ARM64',()=>{
 const names=['KERNEL32.dll','bcrypt.dll','api-ms-win-crt-runtime-l1-1-0.dll'];
 for(const machine of [0x8664,0xaa64])assert.deepEqual(validateWindowsRuntime(fixture(names,machine)),names);
});
test('Windows package rejects VC++ redistributable dependencies hidden by CI',()=>{
 for(const name of ['VCRUNTIME140.dll','vcruntime140_1.dll','MSVCP140.dll','CONCRT140.dll','MSVCR120.dll'])
  assert.throws(()=>validateWindowsRuntime(fixture(['kernel32.dll',name])),/external VC\+\+ libraries/);
});
test('Windows import inspection rejects truncation, unmapped names and missing terminators',()=>{
 const b=fixture(['kernel32.dll']);
 assert.throws(()=>windowsImports(b.subarray(0,100)),/Invalid Windows/);
 const unmapped=Buffer.from(b);unmapped.writeUInt32LE(0xdeadbeef,0x30c);
 assert.throws(()=>windowsImports(unmapped),/Invalid Windows/);
 const unterminated=Buffer.from(b);unterminated.writeUInt32LE(20,0x80+24+124);
 assert.throws(()=>windowsImports(unterminated),/Invalid Windows/);
});
