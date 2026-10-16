// Temporary, version- and content-pinned defense for unreleased upstream fixes.
// Keep npm audit unchanged: patched vulnerable version numbers still need review.
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const root = path.resolve(__dirname, '../mobile/voxagent-companion/node_modules');
const manifest = require('../security/mobile-dependency-patches.json');
const hash = text => crypto.createHash('sha256').update(text).digest('hex');
const checkOnly = process.argv.includes('--check');
const lock = require('../mobile/voxagent-companion/package-lock.json');
for (const name of Object.keys(manifest.packages)) {
  const locations = Object.keys(lock.packages).filter(key => key.endsWith(`/node_modules/${name}`) || key === `node_modules/${name}`);
  if (locations.length !== 1 || locations[0] !== `node_modules/${name}`) {
    throw new Error(`Review duplicate/nested dependency patches for ${name}`);
  }
}
for (const [name, version] of Object.entries(manifest.packages)) {
  const installed = JSON.parse(fs.readFileSync(path.join(root, name, 'package.json')));
  if (installed.version !== version) throw new Error(`Review security patch for ${name}@${installed.version}`);
}
// Validate every input before changing any file. Unknown upstream content fails closed.
const pending = manifest.patches.map(patch => {
  const target = path.join(root, patch.path);
  const current = fs.readFileSync(target, 'utf8');
  if (hash(current) === patch.after) return null;
  if (checkOnly || hash(current) !== patch.before) throw new Error(`Security patch mismatch: ${patch.path}`);
  let replacement = current;
  for (const edit of patch.edits) {
    if (replacement.split(edit.old).length !== 2) throw new Error(`Ambiguous patch: ${patch.path}`);
    replacement = replacement.replace(edit.old, () => edit.new);
  }
  if (hash(replacement) !== patch.after) throw new Error(`Invalid patch output: ${patch.path}`);
  return {target, replacement};
});
for (const patch of pending.filter(Boolean)) fs.writeFileSync(patch.target, patch.replacement);
console.log('Pinned mobile dependency defenses verified');
