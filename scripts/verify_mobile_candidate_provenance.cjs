'use strict';
const path=require('node:path');
const {verify}=require('../security/mobile-candidate/provenance.cjs');
const flag=process.argv.indexOf('--app-root');
if(flag<0||!process.argv[flag+1])throw new Error('Explicit isolated --app-root required; production adoption is not authorized');
console.log(verify(require('node:fs').realpathSync(process.argv[flag+1]),path.resolve(__dirname,'../security/mobile-candidate/frozen-manifest.json'),'f39d7ea35feab6cf0581b890e8628b60aa822dd74624e73149c19a5436e802b8'));
