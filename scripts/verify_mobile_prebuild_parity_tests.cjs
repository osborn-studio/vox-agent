'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {compare} = require('./verify_mobile_prebuild_parity.cjs');
const root = fs.mkdtempSync(path.join(os.tmpdir(), 'vox-prebuild-parity-'));
try {
  const before = path.join(root, 'before'), after = path.join(root, 'after');
  fs.mkdirSync(before); fs.mkdirSync(after);
  const a = 'AAAAAAAAAAAAAAAAAAAAAAAA', b = 'BBBBBBBBBBBBBBBBBBBBBBBB';
  const c = 'CCCCCCCCCCCCCCCCCCCCCCCC', d = 'DDDDDDDDDDDDDDDDDDDDDDDD';
  const first = `${a} = {ref = ${b};}; ${b} = {ref = ${a};};`;
  const next = `${c} = {ref = ${d};}; ${d} = {ref = ${c};};`;
  fs.writeFileSync(path.join(before, 'project.pbxproj'), first);
  fs.writeFileSync(path.join(after, 'project.pbxproj'), next);
  for (const dir of [before, after]) fs.writeFileSync(path.join(dir, 'Info.plist'), 'synthetic');
  compare(before, after);
  fs.writeFileSync(path.join(after, 'project.pbxproj'), next.replace(`ref = ${c}`, `ref = ${d}`));
  assert.throws(() => compare(before, after), /Inconsistent project reference|Project ID collision/);
  fs.writeFileSync(path.join(after, 'project.pbxproj'), next);
  fs.writeFileSync(path.join(after, 'Info.plist'), 'changed');
  assert.throws(() => compare(before, after), /Changed native file/);
  fs.writeFileSync(path.join(after, 'Info.plist'), 'synthetic');
  fs.writeFileSync(path.join(after, 'unexpected'), 'synthetic');
  assert.throws(() => compare(before, after));
  console.log('4 isolated prebuild parity acceptance/refusal scenarios passed');
} finally {
  // Only this process's newly created synthetic fixture directory.
  fs.rmSync(root, {recursive: true, force: true});
}
