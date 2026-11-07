import assert from 'node:assert/strict';
import { test } from 'node:test';
import { load, dependency } from './load.mjs';
const c=load('core.ts');
test('catalog submit controller locks payload on uncertain result and retries same operation',async()=>{
  assert.equal(typeof c.Submission,'function');let operations=0,calls=0;const client={operation(path,body,permission){operations++;return{key:'one-key',async send(){calls++;if(calls===1)throw new c.ConsoleError(0,'network');return {id:'saved'};}}}};
  const submission=new c.Submission();await assert.rejects(()=>submission.send(client,'/api/plans',{reason:'one'},'plans:publish'));
  assert.equal(submission.locked,true);await submission.send(client,'/api/plans',{reason:'changed'},'plans:publish');assert.equal(operations,1);assert.equal(calls,2);
});
test('duplicate in-flight submit cannot dispatch twice and conflict cannot be retried blindly',async()=>{
  assert.equal(typeof c.Submission,'function');let finish,calls=0;const client={operation(){return{key:'k',send(){calls++;return new Promise(resolve=>finish=resolve);}}}};
  const submission=new c.Submission();const pending=submission.send(client,'/api/models',{},'execution:publish');await assert.rejects(()=>submission.send(client,'/api/models',{},'execution:publish'));assert.equal(calls,1);finish({});await pending;
  const conflict=new c.Submission();const bad={operation(){return{key:'k',async send(){throw new c.ConsoleError(409,'conflict');}}}};
  await assert.rejects(()=>conflict.send(bad,'/api/models',{},'execution:publish'));assert.equal(conflict.conflict,true);await assert.rejects(()=>conflict.send(bad,'/api/models',{},'execution:publish'));
});
test('real app initially shows only session check, never business fixtures',()=>{
  const App=load('App.tsx').default;assert.equal(typeof App,'function');
  const html=dependency('react-dom/server').renderToStaticMarkup(dependency('react').createElement(App));assert.match(html,/正在确认管理会话/);assert.doesNotMatch(html,/9997344|示例用户|API Key/);
});
