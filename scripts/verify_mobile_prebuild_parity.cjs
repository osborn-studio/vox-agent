// Read-only comparison of disposable native prebuild outputs; never builds/signs a device app.
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
function files(root, prefix = '') {
  const result = new Map();
  for (const item of fs.readdirSync(path.join(root, prefix), {withFileTypes: true})) {
    const key = path.join(prefix, item.name);
    assert(!item.isSymbolicLink(), 'Prebuild fixture must not contain symlinks');
    if (item.isDirectory()) for (const [k, v] of files(root, key)) result.set(k, v);
    else if (item.isFile()) result.set(key, fs.readFileSync(path.join(root, key)));
    else throw new Error('Unsupported fixture entry');
  }
  return result;
}
function compare(beforeRoot, afterRoot) {
  const before = files(beforeRoot), after = files(afterRoot);
  assert(before.size > 0, 'Empty prebuild is not acceptance');
  assert.deepEqual([...before.keys()].sort(), [...after.keys()].sort());
  let projects = 0;
  for (const [key, content] of before) {
    const other = after.get(key);
    if (!key.endsWith('project.pbxproj')) { assert(content.equals(other), `Changed native file: ${key}`); continue; }
    projects++;
    const text = content.toString('utf8'), next = other.toString('utf8');
    const ids = [...text.matchAll(/\b[A-F0-9]{24}\b/g)].map(x => x[0]);
    const nextIds = [...next.matchAll(/\b[A-F0-9]{24}\b/g)].map(x => x[0]);
    assert(ids.length > 0 && ids.length === nextIds.length, 'Project ID occurrences changed');
    const forward = new Map(), reverse = new Map();
    ids.forEach((id, i) => {
      const candidate = nextIds[i];
      if (forward.has(id)) assert.equal(forward.get(id), candidate, 'Inconsistent project reference');
      if (reverse.has(candidate)) assert.equal(reverse.get(candidate), id, 'Project ID collision');
      forward.set(id, candidate); reverse.set(candidate, id);
    });
    assert.equal(text.replace(/\b[A-F0-9]{24}\b/g, id => forward.get(id)), next,
      'Native project changed beyond bijective ID renaming');
  }
  assert(projects > 0, 'No Xcode project produced');
  console.log(`Prebuild parity passed: ${before.size} files; bijective Xcode ID/reference renaming only`);
}
if (require.main === module) {
  assert(process.argv.length === 4, 'Usage: node verify_mobile_prebuild_parity.cjs baseline-ios candidate-ios');
  compare(process.argv[2], process.argv[3]);
}
module.exports = {compare};
