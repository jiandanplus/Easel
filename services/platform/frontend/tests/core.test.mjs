import assert from 'node:assert/strict';
import { test } from 'node:test';
import { load } from './load.mjs';
const c = load('core.ts');
const implemented = name => assert.equal(typeof c[name], 'function', name + ' behavior must exist');
test('microcredits stay exact and unknown values never become zero', () => {
  implemented('formatCredits');
  assert.equal(c.formatCredits('9007199254740993000001'), '9007199254740993.000001');
  assert.equal(c.formatCredits('2656'), '0.002656');
  assert.equal(c.formatCredits('0'), '0');
  assert.equal(c.formatCredits(undefined), '未知');
  assert.equal(c.formatCredits('1.5'), '数据无效');
});
test('session stays in memory and all reads are same-origin without auth headers', async () => {
  implemented('ConsoleClient'); const calls=[];
  const client=new c.ConsoleClient(async (url, init)=>{calls.push({url,init});return Response.json({items:[],limit:25,offset:0,has_more:false});});
  await client.read('/api/users');
  assert.equal(calls[0].url,'/api/users'); assert.equal(calls[0].init.credentials,'same-origin');
  assert.equal(calls[0].init.cache,'no-store'); assert.equal(calls[0].init.headers.get('Authorization'),null);
  await assert.rejects(()=>client.read('https://hostile.test/api/users'));
});
test('write needs session permission and same-origin CSRF, network retry reuses key and bytes', async () => {
  implemented('ConsoleClient'); const calls=[];let fail=true;
  const client=new c.ConsoleClient(async (url,init)=>{calls.push({url,init});if(fail){fail=false;throw new TypeError('network');}return Response.json({id:'plan'});},()=> 'fixed-key');
  assert.throws(()=>client.operation('/api/plans',{definition:{id:'p'},reason:'reviewed'},'plans:publish'));
  client.setSession({admin:{id:'a',role:'operator',permissions:['console:read','plans:publish']},csrf_token:'csrf'});
  const op=client.operation('/api/plans',{definition:{id:'p'},reason:'reviewed'},'plans:publish');
  await assert.rejects(()=>op.send()); await op.send();
  assert.equal(calls.length,2);assert.equal(calls[0].init.headers.get('Idempotency-Key'),'fixed-key');
  assert.equal(calls[0].init.headers.get('X-Console-CSRF'),'csrf');assert.equal(calls[0].init.body,calls[1].init.body);
  assert.equal(calls[0].init.headers.get('Authorization'),null);
});
test('401 clears in-memory authority; 403 and 409 remain explicit without automatic replay', async()=>{
  implemented('ConsoleClient');
  for(const status of [401,403,409]){
    let count=0; const client=new c.ConsoleClient(async()=>{count++;return Response.json({error:{code:'idempotency_conflict',message:'PRIVATE-DETAIL'}},{status});});
    client.setSession({admin:{id:'a',role:'admin',permissions:['console:read','plans:publish']},csrf_token:'csrf'});
    await assert.rejects(()=>client.read('/api/plans'),e=>e.status===status&&!e.message.includes('PRIVATE-DETAIL'));
    assert.equal(count,1);if(status===401)assert.equal(client.session,null);
  }
});
test('operation cannot cross a replaced or logged-out session',async()=>{
  implemented('ConsoleClient');let count=0;const client=new c.ConsoleClient(async()=>{count++;return Response.json({});});
  client.setSession({admin:{id:'a',role:'operator',permissions:['plans:publish']},csrf_token:'first'});
  const op=client.operation('/api/plans',{reason:'r'},'plans:publish');
  client.setSession({admin:{id:'b',role:'admin',permissions:['plans:publish']},csrf_token:'second'});
  await assert.rejects(()=>op.send());assert.equal(count,0);
});
test('read generation rejects stale completion and aborts prior request',()=>{
  implemented('RequestGate'); const gate=new c.RequestGate();const first=gate.begin();const next=gate.begin();
  assert.equal(first.signal.aborted,true);assert.equal(first.current(),false);assert.equal(next.current(),true);gate.cancel();assert.equal(next.current(),false);
});
test('bounded list query encodes search and resets explicit offset',()=>{
  implemented('listPath');assert.equal(c.listPath('users','a&b',25),'/api/users?limit=25&offset=25&q=a%26b');
  assert.throws(()=>c.listPath('../secrets','',0));assert.throws(()=>c.listPath('users','x'.repeat(129),0));
});
test('forms build real definitions, enforce precision and never accept secret fields',()=>{
  implemented('buildDefinition');
  const p=c.buildDefinition('plans',{id:'p',name:'Starter',version:'1',products:'easel',features:'text',credits:'10.000001',days:'7',concurrency:'1',test_only:true});
  assert.equal(p.credits,'10000001');assert.deepEqual([...p.included_products],['easel']);
  assert.throws(()=>c.buildDefinition('plans',{id:'p',name:'n',version:'1',products:'easel',features:'text',credits:'1.0000001',days:'7',concurrency:'1',test_only:true}));
});
test('reason rejects controls locally instead of provoking an auth-shaped server error',()=>{
  implemented('validReason');assert.equal(c.validReason('  审核后发布  '),'审核后发布');
  assert.throws(()=>c.validReason('line1\nline2'));assert.throws(()=>c.validReason(''));assert.throws(()=>c.validReason('x'.repeat(256)));
});
test('offers preserve cents and models publish only explicit rational rates and limits',()=>{
  const offer=c.buildDefinition('offers',{id:'o',version:'1',test_only:false,plan_id:'p',amount:'12.01',description:'terms'});
  assert.equal(offer.amount_minor,'1201');assert.equal(offer.currency,'CNY');assert.equal(offer.terms.refund_policy,'unused_full_only');
  const fields={id:'m',name:'model',version:'1',products:'easel',test_only:false,feature:'text',provider_id:'server-provider',provider_version:'1',verification_ref:'reviewed-proof',rate_version:'r1',input_numerator:'1',input_denominator:'2',output_numerator:'3',output_denominator:'2',max_input_bytes:'1048576',max_input_tokens:'16384',max_output_tokens:'1024'};
  const model=c.buildDefinition('models',fields);assert.equal(model.verification,'live');assert.equal(model.rate.input.denominator,'2');assert.equal(model.limits.max_output_tokens,1024);
  assert.throws(()=>c.buildDefinition('models',{...fields,input_denominator:'0'}));assert.throws(()=>c.buildDefinition('models',{...fields,max_output_tokens:'4097'}));
});
test('logout sends CSRF and clears session only after confirmation',async()=>{
  const requests=[];const client=new c.ConsoleClient(async(url,init)=>{requests.push({url,init});return Response.json({ok:true});});
  client.setSession({admin:{id:'a',role:'viewer',permissions:['console:read']},csrf_token:'memory-only'});
  await client.logout();assert.equal(requests[0].url,'/auth/logout');assert.equal(requests[0].init.method,'POST');assert.equal(requests[0].init.headers.get('X-Console-CSRF'),'memory-only');assert.equal(client.session,null);
});
