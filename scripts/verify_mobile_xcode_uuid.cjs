// Parent-scoped uuid upgrade: exercise the CommonJS API actually consumed by xcode.
'use strict';
const assert = require('node:assert/strict');
const {createRequire} = require('node:module');
const path = require('node:path');
const fs = require('node:fs');
const mobile = createRequire(path.resolve(__dirname, '../mobile/voxagent-companion/package.json'));
const manifest = mobile('xcode/package.json');
assert.equal(manifest.version, '3.0.1', 'Reverify scoped override after xcode changes');
assert.equal(manifest.dependencies.uuid, '^7.0.3', 'Reverify changed parent uuid range');
const lock = JSON.parse(fs.readFileSync(path.resolve(__dirname, '../mobile/voxagent-companion/package-lock.json'), 'utf8'));
const parents = Object.entries(lock.packages).filter(([, pkg]) =>
  ['dependencies', 'optionalDependencies', 'peerDependencies', 'devDependencies'].some(field => pkg[field]?.uuid !== undefined)
).map(([location]) => location);
assert.deepEqual(parents, ['node_modules/xcode'], 'Reverify uuid hoisting for any additional consumer');
const copies = Object.keys(lock.packages).filter(location => location === 'node_modules/uuid' || location.endsWith('/node_modules/uuid'));
assert.deepEqual(copies, ['node_modules/uuid'], 'Reverify nested uuid copies');
const xcodeRequire = createRequire(mobile.resolve('xcode'));
assert.equal(xcodeRequire('uuid/package.json').version, '11.1.1');
const project = mobile('xcode').project('synthetic.pbxproj');
project.hash = {project: {
  archiveVersion: 1, classes: {}, objectVersion: 56,
  objects: {PBXGroup: {}}, rootObject: '000000000000000000000001'
}};
const ids = Array.from({length: 1000}, () => project.generateUuid());
assert.equal(new Set(ids).size, ids.length);
assert(ids.every(id => /^[A-F0-9]{24}$/.test(id)));
const group = project.addPbxGroup([], 'Synthetic', 'Synthetic');
assert(project.hash.project.objects.PBXGroup[group.uuid]);
const parsed = mobile('xcode/lib/parser/pbxproj').parse(project.writeSync());
assert(parsed.project.objects.PBXGroup[group.uuid]);
console.log('Scoped xcode/uuid CommonJS, ID generation and synthetic project roundtrip passed');
