// Check the actual PE imports; a developer machine can hide missing VC runtimes.
export function windowsImports(bytes) {
 const fail=()=>{throw Error('Invalid Windows PE import table');};
 const range=(offset,length)=>{if(!Number.isSafeInteger(offset)||offset<0||offset+length>bytes.length)fail();return offset;};
 const u16=offset=>bytes.readUInt16LE(range(offset,2));
 const u32=offset=>bytes.readUInt32LE(range(offset,4));
 if(bytes.toString('ascii',0,2)!=='MZ')fail();
 const pe=u32(60);range(pe,24);
 if(bytes.toString('ascii',pe,pe+4)!=='PE\0\0')fail();
 const count=u16(pe+6),optional=pe+24,optionalSize=u16(pe+20);
 if(!count||count>96||optionalSize<128||u16(optional)!==0x20b)fail();
 range(optional,optionalSize);
 if(u32(optional+108)<2)fail();
 const sections=optional+optionalSize;range(sections,count*40);
 const offsetFor=(rva,length)=>{
  for(let i=0;i<count;i++) {
   const section=sections+i*40,start=u32(section+12),rawSize=u32(section+16),raw=u32(section+20);
   if(rva>=start&&rva-start+length<=rawSize)return range(raw+rva-start,length);
  }
  fail();
 };
 const rva=u32(optional+120),size=u32(optional+124);
 if(!rva&&!size)return [];
 if(!rva||size<20||size>1024*1024)fail();
 const names=[];
 for(let i=0;i<Math.min(Math.floor(size/20),1024);i++) {
  const offset=offsetFor(rva+i*20,20);
  if(bytes.subarray(offset,offset+20).every(value=>value===0))return names;
  const nameRva=u32(offset+12);let name='';
  for(let j=0;j<256;j++) {
   const code=bytes[offsetFor(nameRva+j,1)];
   if(code===0)break;
   if(code<33||code>126||j===255)fail();
   name+=String.fromCharCode(code);
  }
  if(!/^[A-Za-z0-9_.-]+\.dll$/i.test(name))fail();
  names.push(name);
 }
 fail();
}

export function validateWindowsRuntime(bytes) {
 const imports=windowsImports(bytes);
 const external=imports.filter(name=>/^(?:vcruntime|msvcp|concrt|msvcr\d)/i.test(name));
 if(external.length)throw Error(`Windows runtime requires external VC++ libraries: ${external.join(', ')}. Build with crt-static and an explicit Cargo target.`);
 return imports;
}
