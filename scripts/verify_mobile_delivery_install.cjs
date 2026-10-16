'use strict';
// Explicit reviewed new-contract install path. The historical installer is unchanged.
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
if(process.argv.length!==4||process.argv[2]!=='--contract'||process.argv[3]!=='vox-canonical-signature-v1')throw Error('Explicit reviewed canonical contract required');
const repo=path.resolve(__dirname,'..'),hash=b=>crypto.createHash('sha256').update(b).digest('hex');
const moduleFile=path.join(repo,'security/mobile-candidate/provenance.cjs');
if(hash(fs.readFileSync(moduleFile))!=='e3e0e6cdecbbc3e9e4b56d5f910e7867cea56f629f895772aa8370952e24438a')throw Error('Install verifier module pin mismatch');
const contract=path.join(repo,'security/mobile-candidate/canonical-contract.json');
if(hash(fs.readFileSync(contract))!=='8dace77e63dc6948ab0b0062fd8ae0d1308c053edf9913b8c7c8348d5672ae71')throw Error('Install contract pin mismatch');
const app=path.join(repo,'mobile/voxagent-companion');
if(hash(fs.readFileSync(path.join(app,'package.json')))!=='a28f1dcbd81940311a56be3b0ba9cfaa1bb8ce8a1be95b2c0f3a620ef475ba11')throw Error('Install package contract mismatch');
const {verify}=require(moduleFile);
console.log(verify(app,path.join(repo,'security/mobile-candidate/frozen-manifest.json'),'fc1d7329d444f79c899eaeb3abd2fca25bd26cf678f3e86f89ed5b3752703a0c'));
console.log('Canonical installation provenance verified; historical installer unchanged; security behavior requires the separate complete gate');
