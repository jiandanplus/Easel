import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import {runInNewContext} from 'node:vm';
import {test} from 'node:test';
const require=createRequire(new URL('../../web/frontend/package.json',import.meta.url));
const ts=require('typescript');
const source=readFileSync(new URL('../../web/frontend/src/App.tsx',import.meta.url),'utf8');
const ast=ts.createSourceFile('App.tsx',source,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
const app=ast.statements.find(n=>ts.isFunctionDeclaration(n)&&n.name?.text==='App');
const effect=app.body.statements.find(n=>ts.isExpressionStatement(n)&&n.getText(ast).includes('const hash = hashForWorkbenchLocation({ page: currentPage'));
assert.ok(effect);
const nav={};runInNewContext(ts.transpileModule(readFileSync(new URL('../../web/frontend/src/lib/workbenchNavigation.ts',import.meta.url),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,{exports:nav,URLSearchParams});
function sync(hash,page,id){const window={location:{hash}};runInNewContext(ts.transpileModule(effect.getText(ast),{compilerOptions:{target:ts.ScriptTarget.ES2022}}).outputText,{window,currentPage:page,activeSessionId:id,navigationFromHistory:{current:true},useEffect:f=>f(),...nav});return window.location.hash;}
test('selecting a new session always changes URL even after a hash-origin navigation',()=>{assert.equal(sync('#/chat?session=old','chat','new'),'#/chat?session=new');});
test('matching opaque preview locations are retained instead of stripped by state sync',()=>{assert.equal(sync('#/outputs?artifact=file-1','outputs','chat'),'#/outputs?artifact=file-1');});
