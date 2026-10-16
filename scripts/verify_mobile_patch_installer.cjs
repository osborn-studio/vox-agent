// Exercise the actual installer in disposable fixtures; never edit the real install.
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {spawnSync} = require('node:child_process');
const {createHash} = require('node:crypto');
const repo = path.resolve(__dirname, '..');
const manifest = require('../security/mobile-dependency-patches.json');
const hash = s => createHash('sha256').update(s).digest('hex');
let cases = 0;
function fixture(test) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'vox-patch-test-'));
  const moduleRoot = path.join(dir, 'mobile/voxagent-companion');
  const write = (name, value) => {
    const file = path.join(dir, name);
    fs.mkdirSync(path.dirname(file), {recursive: true});
    fs.writeFileSync(file, value);
  };
  try {
    write('scripts/patch_mobile_dependencies.cjs', fs.readFileSync(path.join(__dirname, 'patch_mobile_dependencies.cjs')));
    write('security/mobile-dependency-patches.json', JSON.stringify(manifest));
    const lock = {packages: {}};
    for (const [name, version] of Object.entries(manifest.packages)) {
      lock.packages[`node_modules/${name}`] = {version};
      write(`mobile/voxagent-companion/node_modules/${name}/package.json`, JSON.stringify({version}));
    }
    write('mobile/voxagent-companion/package-lock.json', JSON.stringify(lock));
    for (const patch of manifest.patches) {
      let source = fs.readFileSync(path.join(repo, 'mobile/voxagent-companion/node_modules', patch.path), 'utf8');
      assert.equal(hash(source), patch.after, 'Run installed security checks first');
      for (const edit of [...patch.edits].reverse()) {
        assert.equal(source.split(edit.new).length, 2);
        source = source.replace(edit.new, () => edit.old);
      }
      assert.equal(hash(source), patch.before);
      write(`mobile/voxagent-companion/node_modules/${patch.path}`, source);
    }
    const run = (...args) => spawnSync(process.execPath, [path.join(dir, 'scripts/patch_mobile_dependencies.cjs'), ...args], {encoding: 'utf8', timeout: 10000});
    const snapshot = () => manifest.patches.map(p => hash(fs.readFileSync(path.join(moduleRoot, 'node_modules', p.path))));
    test({dir, moduleRoot, write, lock, run, snapshot});
    cases++;
  } finally {
    fs.rmSync(dir, {recursive: true, force: true});
  }
}
fixture(({run, snapshot}) => {
  const before = snapshot();
  assert.equal(run('--check').status, 1);
  assert.deepEqual(snapshot(), before, 'check mode must not write');
  assert.equal(run().status, 0);
  assert.deepEqual(snapshot(), manifest.patches.map(p => p.after));
  assert.equal(run().status, 0, 'reapplication must succeed');
  assert.equal(run('--check').status, 0);
});
fixture(({moduleRoot, run, snapshot}) => {
  // Corrupt the LAST input to catch partial application before validation finishes.
  fs.appendFileSync(path.join(moduleRoot, 'node_modules', manifest.patches.at(-1).path), '\n// changed upstream\n');
  const before = snapshot();
  assert.equal(run().status, 1);
  assert.deepEqual(snapshot(), before);
});
fixture(({write, run, snapshot}) => {
  write('mobile/voxagent-companion/node_modules/braces/package.json', '{"version":"3.0.4"}');
  const before = snapshot();
  assert.equal(run().status, 1);
  assert.deepEqual(snapshot(), before);
});
fixture(({lock, write, run, snapshot}) => {
  lock.packages['node_modules/example/node_modules/braces'] = {version: '3.0.3'};
  write('mobile/voxagent-companion/package-lock.json', JSON.stringify(lock));
  const before = snapshot();
  assert.equal(run().status, 1);
  assert.deepEqual(snapshot(), before);
});
console.log(`${cases} isolated patch installer scenarios passed`);
