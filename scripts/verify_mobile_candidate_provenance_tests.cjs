'use strict';
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {verify,sha}=require('../security/mobile-candidate/provenance.cjs');
const idx=process.argv.indexOf('--app-root');if(idx<0)throw new Error('Explicit isolated app root required');
const repo=path.resolve(__dirname,'..'),root=require('node:fs').realpathSync(process.argv[idx+1]);
if(!root.startsWith(path.join(repo,'build')+path.sep))throw new Error('Mutation probes allowed only in repository build fixtures');
const manifest=path.join(repo,'security/mobile-candidate/frozen-manifest.json'),hash=sha(fs.readFileSync(manifest)),m=JSON.parse(fs.readFileSync(manifest));
const run=read=>verify(root,manifest,hash,read);run();let count=0;
assert.throws(()=>verify(root,manifest,'0'.repeat(64)),/provenance/);count++;
const lockPath=path.join(root,'package-lock.json');
for(const name of ['braces','micromatch','node-forge']){const lock=JSON.parse(fs.readFileSync(lockPath));lock.packages['node_modules/'+name]={version:'unsafe'};assert.throws(()=>run(f=>f===lockPath?Buffer.from(JSON.stringify(lock)):fs.readFileSync(f)),/regression/);count++;}
const source=path.resolve(root,Object.keys(m.parents[0].files)[0]);assert.throws(()=>run(f=>f===source?Buffer.from('unapplied or drift'):fs.readFileSync(f)),/source mismatch/);count++;
const lock=JSON.parse(fs.readFileSync(lockPath));lock.packages[m.parents[0].path].version='999';
const changedLock=Buffer.from(JSON.stringify(lock)),changedManifest=Buffer.from(JSON.stringify({...m,lockSHA256:sha(changedLock)}));
assert.throws(()=>verify(root,manifest,sha(changedManifest),f=>f===manifest?changedManifest:f===lockPath?changedLock:fs.readFileSync(f)),/^Error: parent provenance mismatch$/);count++;
const criticalSource=path.resolve(root,Object.keys(m.criticalDependencies[0].files)[0]);assert.throws(()=>run(f=>f===criticalSource?Buffer.from('critical drift'):fs.readFileSync(f)),/^Error: parent source mismatch$/);count++;

const nonce=require('node:crypto').randomUUID();const extra=path.join(root,m.parents[0].path,'__owned_candidate_probe__'+nonce);
for(const kind of ['extra','symlink']){try{if(kind==='extra')fs.writeFileSync(extra,'fixture',{flag:'wx'});if(kind==='symlink')fs.symlinkSync(source,extra);assert.throws(()=>run(),kind==='extra'?/^Error: extra or missing parent file$/:/^Error: unsafe parent file$/);count++;}finally{fs.unlinkSync(extra)}}
// Link outside the package inventory: only nlink can distinguish this mutation.
const outside=path.join(root,'__owned_hardlink_probe__'+nonce);
try{fs.linkSync(source,outside);assert.throws(()=>run(),/^Error: hardlinked parent file$/);count++;}finally{fs.unlinkSync(outside)}
for(const name of ['node-forge','@expo/cli']){const dir=path.join(root,'node_modules/__owned_candidate_probe__'+nonce);fs.mkdirSync(dir);try{fs.writeFileSync(path.join(dir,'package.json'),JSON.stringify({name,version:'unsafe'}));assert.throws(()=>run(),/regression|inventory mismatch/);count++;}finally{fs.rmSync(dir,{recursive:true})}}
for(const [dirname,name,message] of [['node-forge','renamed-safe','installed legacy dependency regression'],['.hidden-owned-'+nonce,'renamed-safe','hidden dependency directory'],['__owned_critical_'+nonce,'tsyringe','critical dependency inventory mismatch']]){const dir=path.join(root,'node_modules',dirname);assert.equal(fs.existsSync(dir),false);fs.mkdirSync(dir);try{fs.writeFileSync(path.join(dir,'package.json'),JSON.stringify({name,version:'fixture'}));assert.throws(()=>run(),error=>error.message===message);count++;}finally{fs.rmSync(dir,{recursive:true})}}
const metadataDir=path.join(root,'node_modules','__owned_metadata_'+nonce);fs.mkdirSync(metadataDir);try{fs.symlinkSync(source,path.join(metadataDir,'package.json'));assert.throws(()=>run(),/^Error: unsafe dependency metadata$/);count++;}finally{fs.rmSync(metadataDir,{recursive:true})}
run();console.log(`${count} candidate provenance negative cases and unchanged baseline passed`);
