import assert from 'node:assert/strict';
import { test } from 'node:test';
import { load } from './load.mjs';
const u = load('UserAdminDialog.tsx');
test('balance changes are signed points within the console ceiling', () => {
  assert.equal(u.pointsDelta('add', '100'), '+100');
  assert.equal(u.pointsDelta('deduct', '2.5'), '-2.5');
  for (const bad of ['0', '-1', 'abc', '1.1234567', '1000001']) assert.throws(() => u.pointsDelta('add', bad));
});
test('usernames follow the sign-in service rules', () => {
  assert.equal(u.usernameError('zhang_san'), '');
  assert.ok(u.usernameError('张三'));
  assert.ok(u.usernameError('1abc'));
});
test('mainland phones show without the country code', () => {
  assert.equal(u.phoneText('8613800138000'), '13800138000');
  assert.equal(u.phoneText('14155550100'), '+14155550100');
  assert.equal(u.phoneText(null), '');
});
