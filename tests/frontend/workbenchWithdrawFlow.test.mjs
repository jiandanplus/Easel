import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';
const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');
const src = readFileSync(new URL('../../web/frontend/src/App.tsx', import.meta.url), 'utf8');
const ast = ts.createSourceFile('App.tsx', src, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const app = ast.statements.find(n => ts.isFunctionDeclaration(n) && n.name?.text === 'App');
const declaration = app.body.statements.find(n => ts.isVariableStatement(n) && n.declarationList.declarations.some(d => d.name.getText(ast) === 'withdrawOriginalTurn'));
assert.ok(declaration);
const code = ts.transpileModule(declaration.getText(ast) + '\nwithdrawOriginalTurn;', { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText;
function setup(read) {
  const scope = {current:'owner'}, events = [], draft = {text:'next revision',attachments:[{id:'file'}], submission:{clientTurnId:'turn',requestHash:'a'.repeat(64)}};
  const withdraw = runInNewContext(code, { useCallback: f=>f, currentDraftScopeRef:scope,
    draftStore:{load:()=>draft,abandonSubmission(){events.push('abandon'); delete draft.submission;}},
    setResendNotice(v){events.push(v.message);}, withdrawChatTurn:read,
    setDraftRefresh(){events.push('refresh');}, streamCtl:{current:{session:{abort(){events.push('abort');}}}},
    clearStream(){events.push('clear');}, sessionsRef:{current:[{id:'session',messages:[{role:'user'}]}]},
    appendAssistant(){events.push('append');}, queryOriginalTurn:async()=>events.push('query'),
    handleStopStream:async(...args)=>events.push(['stop',...args]),
  });
  return {scope,draft,events,withdraw:()=>withdraw('session','turn')};
}
test('only a matching durable rejection releases submission metadata while preserving next draft', async()=>{
  const view=setup(async()=>({state:'rejected',accepted:false,not_dispatched:true,request_hash:'a'.repeat(64)}));
  await view.withdraw(); assert.equal(view.draft.text,'next revision'); assert.equal(view.draft.attachments.length,1);
  assert.equal(view.draft.submission,undefined); assert.ok(view.events.indexOf('abandon')<view.events.indexOf('abort'));
});
test('accepted withdrawal races query and stop the exact original task', async()=>{
  const view=setup(async()=>({state:'running',accepted:true,request_hash:'a'.repeat(64)}));
  await view.withdraw(); assert.ok(view.draft.submission); assert.ok(!view.events.includes('abandon'));
  assert.deepEqual(view.events.find(Array.isArray),['stop','session','turn']);
});
test('unknown, mismatching or late cross-owner replies never abandon a submission', async()=>{
  for(const result of [{state:'unknown',accepted:false,request_hash:'a'.repeat(64)},{state:'rejected',not_dispatched:true,request_hash:'b'.repeat(64)}]) {
    const view=setup(async()=>result);await view.withdraw();assert.ok(view.draft.submission);assert.ok(!view.events.includes('abort'));
  }
  let complete;const view=setup(()=>new Promise(resolve=>complete=resolve));const pending=view.withdraw();view.scope.current='other';
  complete({state:'rejected',not_dispatched:true,request_hash:'a'.repeat(64)});await pending;assert.ok(view.draft.submission);assert.ok(!view.events.includes('abort'));
});
