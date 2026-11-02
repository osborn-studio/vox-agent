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
if(hash(fs.readFileSync(path.join(app,'package.json')))!=='96ff38d01ae841dac5e5152c74f1d69eb4d9e22c943eb4255a7fddb54dde1ad0')throw Error('Install package contract mismatch');
const {verify}=require(moduleFile);
console.log(verify(app,path.join(repo,'security/mobile-candidate/frozen-manifest.json'),'f39d7ea35feab6cf0581b890e8628b60aa822dd74624e73149c19a5436e802b8'));
console.log('Canonical installation provenance verified; historical installer unchanged; security behavior requires the separate complete gate');
