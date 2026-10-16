'use strict';
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict'),{spawnSync}=require('node:child_process');
const at=process.argv.indexOf('--app-root');if(at<0)throw Error('Explicit isolated --app-root required');const root=require('node:fs').realpathSync(process.argv[at+1]),repo=path.resolve(__dirname,'..');
const dir=fs.mkdtempSync(path.join(repo,'build','owned-gate-mutation-'));
try{
 for(const sub of ['scripts','security/mobile-candidate'])fs.mkdirSync(path.join(dir,sub),{recursive:true});
 for(const file of ['scripts/verify_mobile_candidate_provenance.cjs','security/mobile-candidate/provenance.cjs','security/mobile-candidate/frozen-manifest.json'])fs.copyFileSync(path.join(repo,file),path.join(dir,file));
 const run=(script,appRoot=root)=>spawnSync(process.execPath,[script,'--app-root',appRoot],{encoding:'utf8',timeout:30000});const gate=path.join(dir,'scripts/verify_mobile_candidate_provenance.cjs');assert.equal(run(gate).status,0);const alias=path.join(dir,'owned-root-alias');fs.symlinkSync(root,alias);assert.equal(run(gate,alias).status,0);
 fs.appendFileSync(path.join(dir,'security/mobile-candidate/frozen-manifest.json'),'\n');const invalid=run(gate);assert.equal(invalid.status,1);assert.match(invalid.stderr,/manifest provenance mismatch/);
 const attack=path.join(repo,'scripts/verify_mobile_candidate_attacks.cjs'),baseline=run(attack);assert.equal(baseline.status,2);assert.match(baseline.stderr,/preserved Forge compatibility positive/);
 const changed=path.join(dir,'scripts/canonical-regression.cjs'),source=fs.readFileSync(attack,'utf8');assert(source.includes('assert(canonical.verify(canonical))'));fs.writeFileSync(changed,source.replace('assert(canonical.verify(canonical))','assert(false, "synthetic canonical regression")'));const regression=run(changed);assert.equal(regression.status,1);assert.match(regression.stderr,/synthetic canonical regression/);
 console.log('PASS actual literal pin drift rejection and canonical-regression/compatibility exit distinction');
}finally{fs.rmSync(dir,{recursive:true})}
