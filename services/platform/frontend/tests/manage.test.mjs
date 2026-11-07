import assert from 'node:assert/strict';
import { test } from 'node:test';
import { load } from './load.mjs';
const m = load('ManageDialog.tsx'), c = load('core.ts');
test('only ordinary plans within 100 points and 365 days can be issued from the page',()=>{
  assert.equal(m.issuable({id:'friend-v1',credits:'50000000',days:30}),true);
  assert.equal(m.issuable({id:'big-v1',credits:'100000001',days:30}),false);
  assert.equal(m.issuable({id:'long-v1',credits:'1',days:366}),false);
  assert.equal(m.issuable({id:'easel-owner-allowance-v1',credits:'100000000',days:365}),false);
  assert.equal(m.issuable({id:'easel-internal-experience-v1',credits:'10000000',days:7}),false);
});
test('tags split on Chinese and ASCII separators and stay small',()=>{
  assert.equal(JSON.stringify(m.parseTags('朋友，内测 vip')),'["朋友","内测","vip"]');assert.equal(m.parseTags('').length,0);
  assert.throws(()=>m.parseTags('a a'));assert.throws(()=>m.parseTags('x'.repeat(21)));assert.throws(()=>m.parseTags(Array.from({length:11},(_,i)=>'t'+i).join(',')));
});
test('management commands use exact paths and need the matching permission',()=>{
  const client=new c.ConsoleClient(async()=>new Response('{}'),()=> 'key-12345678');
  client.setSession({admin:{id:'a',role:'operator',permissions:['console:read','users:manage']},csrf_token:'csrf'});
  for(const path of ['/api/users/u1/status','/api/users/u1/logout','/api/users/u1/note'])assert.ok(client.operation(path,{},'users:manage'));
  assert.throws(()=>client.operation('/api/wallets/w/plans',{},'plans:issue'));
  assert.throws(()=>client.operation('/api/users/u1/delete',{},'users:manage'));
});
