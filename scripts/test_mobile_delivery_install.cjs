'use strict';
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict'),crypto=require('node:crypto'),{spawnSync}=require('node:child_process');
const at=process.argv.indexOf('--fixture-root');if(at<0)throw Error('Explicit owned --fixture-root required');
const repo=path.resolve(__dirname,'..'),original=fs.realpathSync(process.argv[at+1]),build=fs.realpathSync(path.join(repo,'build'));
if(!original.startsWith(build+path.sep))throw Error('Owned build fixture required');
const sha=f=>crypto.createHash('sha256').update(fs.readFileSync(f)).digest('hex');
const pins=JSON.parse(fs.readFileSync(path.join(repo,'security/mobile-candidate/safety-verifiers.json')));
// The containing runner pins this test and this table before execution.
for(const rel of ['scripts/verify_mobile_delivery_install.cjs','security/mobile-candidate/provenance.cjs','security/mobile-candidate/canonical-contract.json','security/mobile-candidate/frozen-manifest.json']){
 assert.equal(sha(path.join(original,rel)),pins[rel],'fixture source pin: '+rel);
}
const root=fs.mkdtempSync(path.join(build,'install-vectors-'));
const copy=rel=>{const dest=path.join(root,rel);fs.mkdirSync(path.dirname(dest),{recursive:true});fs.copyFileSync(path.join(original,rel),dest);};
for(const rel of ['scripts/verify_mobile_delivery_install.cjs','security/mobile-candidate/provenance.cjs','security/mobile-candidate/canonical-contract.json','security/mobile-candidate/frozen-manifest.json','mobile/voxagent-companion/package.json','mobile/voxagent-companion/package-lock.json'])copy(rel);
const manifest=JSON.parse(fs.readFileSync(path.join(original,'security/mobile-candidate/frozen-manifest.json')));
for(const parent of [...manifest.parents,...manifest.criticalDependencies])for(const rel of Object.keys(parent.files))copy('mobile/voxagent-companion/'+rel);
// Independent ordinary copies: interruption can damage only this throwaway fixture.
const entry=path.join(root,'scripts/verify_mobile_delivery_install.cjs');
const run=args=>spawnSync(process.execPath,[entry,...args],{encoding:'utf8',timeout:30000});
const args=['--contract','vox-canonical-signature-v1'];assert.equal(run(args).status,0);
for(const invalid of [[],['--contract','historical-forge'],[...args,'--skip']]){const r=run(invalid);assert.equal(r.status,1);assert.match(r.stderr,/Explicit reviewed canonical contract required/);}
let rejected=3;
for(const [rel,pattern]of [
 ['security/mobile-candidate/provenance.cjs',/module pin mismatch/],
 ['security/mobile-candidate/canonical-contract.json',/contract pin mismatch/],
 ['mobile/voxagent-companion/package.json',/package contract mismatch/],
 ['security/mobile-candidate/frozen-manifest.json',/manifest provenance mismatch/],
 ['mobile/voxagent-companion/package-lock.json',/lock provenance mismatch/],
 ['mobile/voxagent-companion/node_modules/@expo/code-signing-certificates/build/main.js',/parent source mismatch/],
]){const file=path.join(root,rel),before=fs.readFileSync(file);try{fs.appendFileSync(file,'\n');const r=run(args);assert.equal(r.status,1,rel);assert.match(r.stderr,pattern,rel);rejected++;}finally{fs.writeFileSync(file,before);}assert.equal(run(args).status,0,'restored disposable baseline');}
fs.rmSync(root,{recursive:true});
console.log(JSON.stringify({actualInstallerBaseline:true,rejectedVectors:rejected,restoredBaseline:true,fixtureInstallerPinned:true,isolatedMutationCopies:true,scope:'owned disposable fixture only; original graph never mutated; no external calls'}));
