// node --experimental-strip-types lib/passwords.test.ts
import assert from 'node:assert/strict';
import { dummyHash, hashPassword, passwordProblem, verifyPassword } from './passwords.ts';

const hash = await hashPassword('correct horse battery staple');
assert.match(hash, /^scrypt\$32768\$8\$1\$[A-Za-z0-9+/=]+\$[A-Za-z0-9+/=]+$/);
assert.equal(await verifyPassword('correct horse battery staple', hash), true);
assert.equal(await verifyPassword('correct horse battery stapl', hash), false);
assert.equal(await verifyPassword('', hash), false);
console.log('ok  a password verifies against its own hash and nothing else');

assert.notEqual(await hashPassword('same'), await hashPassword('same'));
console.log('ok  the same password hashes differently each time (salted)');

// Composed and decomposed forms of the same accented text are one password.
assert.equal(await verifyPassword('caf\u00e9-au-lait', await hashPassword('cafe\u0301-au-lait')), true);
console.log('ok  unicode is normalised before hashing');

// The parameters travel in the string: change N in a stored hash and the same
// password no longer matches, so verify() is using the hash's N, not a constant.
const cheap = hash.replace(/^scrypt\$32768\$/, 'scrypt$16384$');
assert.equal(await verifyPassword('correct horse battery staple', cheap), false, 'params are read from the hash');
for (const bad of ['', 'bcrypt$x', 'scrypt$1$2$3', 'not a hash at all']) {
  assert.equal(await verifyPassword('anything', bad), false, `malformed hash accepted: ${bad}`);
}
console.log('ok  parameters are read from the stored hash; malformed hashes never verify');

assert.equal(await verifyPassword('anything', await dummyHash()), false);
assert.equal(await dummyHash(), await dummyHash(), 'the dummy is computed once');
console.log('ok  the dummy hash matches nothing and is computed once');

assert.equal(passwordProblem('short'), 'Use at least 8 characters.');
assert.equal(passwordProblem('x'.repeat(257)), 'Use at most 256 characters.');
assert.equal(passwordProblem('eightchr'), null);
console.log('ok  length is the only rule: 8 to 256 characters');

console.log('\nall password checks passed');
