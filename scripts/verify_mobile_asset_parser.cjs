// Exercise Metro's actual parser call, including the formerly looping ICNS case.
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const { spawnSync } = require('node:child_process');
if (process.argv.includes('--child')) {
  const moduleFlag = process.argv.indexOf('--module');
  assert(moduleFlag >= 0 && process.argv[moduleFlag + 1], 'Explicit installed Metro module required');
  const app = path.resolve(__dirname, '../mobile/voxagent-companion');
  const moduleFile = path.resolve(app, process.argv[moduleFlag + 1]);
  assert(moduleFile.startsWith(path.join(app, 'node_modules') + path.sep), 'Owned installed Metro required');
  const { getAssetSize } = require(moduleFile);
  const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jhAAAAABJRU5ErkJggg==', 'base64');
  assert.deepEqual(getAssetSize('png', png, 'synthetic.png'), { width: 1, height: 1 });
  const malformed = Buffer.alloc(16);
  malformed.write('icns');
  malformed.writeUInt32BE(16, 4);
  malformed.write('icp4', 8); // zero-length entry used to loop forever
  assert.throws(() => getAssetSize('png', malformed, 'synthetic.png'));
} else {
  const app = path.resolve(__dirname, '../mobile/voxagent-companion');
  const lock = JSON.parse(fs.readFileSync(path.join(app, 'package-lock.json')));
  const metros = Object.keys(lock.packages).filter(key => key.endsWith('node_modules/metro'));
  assert(metros.length > 0, 'No installed Metro parser identified');
  for (const key of metros) {
    const result = spawnSync(process.execPath, [__filename, '--child', '--module', key + '/src/Assets.js'], { timeout: 5000, encoding: 'utf8' });
    assert.equal(result.error, undefined, 'Metro asset parser hung or failed to start');
    assert.equal(result.status, 0, result.stderr);
  }
  console.log(`Metro PNG compatibility and malformed ICNS rejection passed for ${metros.length} installed copies`);
}
