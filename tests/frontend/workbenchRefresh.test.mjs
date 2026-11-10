import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import {runInNewContext} from 'node:vm';
import {test} from 'node:test';
const require=createRequire(new URL('../../web/frontend/package.json',import.meta.url));const ts=require('typescript');
const source=readFileSync(new URL('../../web/frontend/src/App.tsx',import.meta.url),'utf8');const ast=ts.createSourceFile('App.tsx',source,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
const app=ast.statements.find(n=>ts.isFunctionDeclaration(n)&&n.name?.text==='App');const declaration=app.body.statements.find(n=>ts.isVariableStatement(n)&&n.declarationList.declarations.some(d=>d.name.getText(ast)==='refreshWorkbench'));
const code=ts.transpileModule(declaration.getText(ast)+'\nrefreshWorkbench;',{compilerOptions:{target:ts.ScriptTarget.ES2022}}).outputText;
test('background window focus refresh does not block a valid composer lease',async()=>{
  const checks=[],applied=[];const refresh=runInNewContext(code,{useCallback:f=>f,statusSequence:{current:0},setWorkbenchChecking:v=>checks.push(v),fetchStatus:async()=>({version:1}),applyStatusRef:{current:v=>applied.push(v)},setWorkbenchError(){},setGatewayStatus(){}});
  refresh(false);await Promise.resolve();await Promise.resolve();assert.equal(checks.length,0);assert.equal(applied.length,1);
  refresh(true);assert.deepEqual(checks,[true]);
});
