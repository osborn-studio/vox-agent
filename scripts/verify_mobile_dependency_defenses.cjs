'use strict';
const assert = require('node:assert/strict');
const {createRequire} = require('node:module');
const requireMobile = createRequire(require('node:path').resolve(__dirname, '../mobile/voxagent-companion/package.json'));
const braces = requireMobile('braces');
const forge = requireMobile('node-forge');
const baseline = process.argv.includes('--baseline');
const deep = '{'.repeat(4900) + 'x' + '}'.repeat(4900);
if (baseline) {
  assert.throws(() => braces.compile(deep), RangeError);
} else {
  for (const method of ['parse', 'compile', 'expand', 'stringify']) {
    for (const pattern of [deep, '('.repeat(4900) + 'x' + ')'.repeat(4900), '{'.repeat(4900) + 'x']) {
      assert.throws(() => braces[method](pattern), /Brace nesting exceeds security limit/);
    }
  }
  for (const method of ['compile', 'expand', 'stringify']) {
    let ast = {type: 'text', value: 'x'};
    for (let i = 0; i < 4900; i++) ast = {type: 'root', nodes: [ast]};
    assert.throws(() => braces[method](ast), /Brace nesting exceeds security limit/);
  }
  assert.deepEqual(braces.expand('src/{a,b}/{1..3}.ts'), ['src/a/1.ts','src/a/2.ts','src/a/3.ts','src/b/1.ts','src/b/2.ts','src/b/3.ts']);
  assert.equal(braces.stringify(braces.parse('src/{a,b}.ts')), 'src/{a,b}.ts');
  // Preserve upstream stringify's omitted parent argument, including invalid braces.
  for (const pattern of ['{a}', 'x{a}y', '{a,b}', '{a', 'a}']) {
    assert.equal(braces.stringify(braces.parse(pattern), {escapeInvalid: true}), pattern);
  }
  const invalidBrace = {type: 'root', nodes: [{type: 'brace', nodes: [
    {type: 'open', value: '{'}, {type: 'text', value: 'x'}, {type: 'close', value: '}'}
  ]}]};
  assert.equal(braces.stringify(invalidBrace, {escapeInvalid: true}), '{x}');
}
// Synthetic test keys only; generate a malformed signed DigestInfo to reproduce
// lax verification independently of the effort needed to forge a real signature.
const keys = forge.pki.rsa.generateKeyPair({bits: 1024, e: 0x10001});
const a = forge.asn1;
const node = (type, constructed, value) => a.create(a.Class.UNIVERSAL, type, constructed, value);
const md = forge.md.sha256.create().update('Vox dependency regression synthetic text');
const digest = md.digest().getBytes();
function signature(parameters, extra) {
  const algorithm = [node(a.Type.OID, false, a.oidToDer(forge.oids.sha256).getBytes())];
  if (parameters) algorithm.push(node(a.Type.NULL, false, ''));
  if (extra) algorithm.push(node(a.Type.OCTETSTRING, false, 'unexpected'));
  const der = a.toDer(node(a.Type.SEQUENCE, true, [node(a.Type.SEQUENCE, true, algorithm), node(a.Type.OCTETSTRING, false, digest)])).getBytes();
  return forge.pki.rsa.encrypt(der, keys.privateKey, 0x01);
}
for (const parameters of [true, false]) {
  assert.equal(keys.publicKey.verify(digest, signature(parameters, false)), true);
  if (baseline) assert.equal(keys.publicKey.verify(digest, signature(parameters, true)), true);
  else assert.throws(() => keys.publicKey.verify(digest, signature(parameters, true)), /DigestInfo/);
}
assert.equal(keys.publicKey.verify('wrong digest', signature(true, false)), false);
console.log(baseline ? 'Both vulnerable behaviors reproduced' : 'Brace depth and RSA nested-element regressions passed');
