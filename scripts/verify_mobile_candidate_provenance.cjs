'use strict';
const path=require('node:path');
const {verify}=require('../security/mobile-candidate/provenance.cjs');
const flag=process.argv.indexOf('--app-root');
if(flag<0||!process.argv[flag+1])throw new Error('Explicit isolated --app-root required; production adoption is not authorized');
console.log(verify(require('node:fs').realpathSync(process.argv[flag+1]),path.resolve(__dirname,'../security/mobile-candidate/frozen-manifest.json'),'fc1d7329d444f79c899eaeb3abd2fca25bd26cf678f3e86f89ed5b3752703a0c'));
