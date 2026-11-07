import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';
const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');
const source = readFileSync(new URL('../../web/frontend/src/components/BundledProviderSettings.tsx', import.meta.url), 'utf8');
const parsed = ts.createSourceFile('BundledProviderSettings.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const declaration = parsed.statements.find(n => ts.isFunctionDeclaration(n) && n.name?.text === 'BundledProviderSettings');
const {outputText} = ts.transpileModule(declaration.getText(parsed).replace('export default ', '') + '\nBundledProviderSettings;', {compilerOptions:{target:ts.ScriptTarget.ES2022, jsx:ts.JsxEmit.React}});
const settle = () => new Promise(resolve => setImmediate(resolve));
function setup({error = '', fetchFailures = 0} = {}) {
  const states=[]; let cursor=0; let started=false; let reads=0; const saves=[]; const changes=[];
  const component=runInNewContext(outputText, {
    Error,
    React:{ createElement:(type,props,...children)=>({type,props:props||{},children:children.flat()}) },
    useState:initial=>{const i=cursor++; if(!(i in states)) states[i]=initial; return [states[i], value=>{states[i]=typeof value==='function'?value(states[i]):value}];},
    useEffect:callback=>{if(!started){started=true;callback()}},
    fetchBundledProvider:async()=>{reads++;if(reads<=fetchFailures) throw new Error('临时读取不可用');return {configured:true,base_url:'http://127.0.0.1:9000/v1',model:'easel-fake',key_present:true}},
    saveBundledProvider:async value=>{saves.push(value);if(error) throw new Error(error);return {configured:value.secret_action!=='clear',base_url:value.base_url,model:value.model,key_present:value.secret_action!=='clear'}},
  });
  return {saves,changes,get reads(){return reads},render(){cursor=0; return component({disabled:false,version:'0.146.0',onSaved:model=>changes.push(model)})}};
}
function nodes(tree,type){if(!tree||typeof tree!=='object')return [];return [...(tree.type===type?[tree]:[]),...(tree.children||[]).flatMap(child=>nodes(child,type))]}
function text(tree){if(typeof tree==='string')return tree;if(!tree||typeof tree!=='object')return '';return (tree.children||[]).map(text).join('')}
test('bundled provider masks stored key and requires replacement on endpoint change',async()=>{
 const view=setup();view.render();await settle();let tree=view.render();
 assert.match(text(tree),/内置.*0.146.0/);
 const inputs=nodes(tree,'input');assert.equal(inputs[2].props.type,'password');assert.equal(inputs[2].props.value,'');
 inputs[0].props.onChange({target:{value:'https://new.example/v1'}});tree=view.render();
 nodes(tree,'button').find(n=>text(n)==='保存供应商').props.onClick();await settle();
 assert.equal(view.saves.length,0);assert.match(text(view.render()),/重新输入 Key/);
 nodes(view.render(),'input')[2].props.onChange({target:{value:'dummy-new'}});
 nodes(view.render(),'button').find(n=>text(n)==='保存供应商').props.onClick();await settle();
 assert.equal(view.saves[0].secret_action,'replace');assert.equal(view.saves[0].api_key,'dummy-new');
 assert.equal(nodes(view.render(),'input')[2].props.value,'');assert.deepEqual(view.changes,['easel-fake']);
});
test('saving same endpoint preserves key deliberately and clear disables configuration',async()=>{
 const view=setup();view.render();await settle();
 nodes(view.render(),'button').find(n=>text(n)==='保存供应商').props.onClick();await settle();
 assert.equal(view.saves[0].secret_action,'preserve');assert.equal(view.saves[0].api_key,'');
 nodes(view.render(),'button').find(n=>text(n)==='清除 Key').props.onClick();await settle();
 assert.equal(view.saves[1].secret_action,'clear');assert.equal(view.changes.at(-1),'');
});
test('provider save failure displays safe backend error without changing active model',async()=>{
 const view=setup({error:'请等待当前任务完成'});view.render();await settle();
 nodes(view.render(),'button').find(n=>text(n)==='保存供应商').props.onClick();await settle();
 assert.match(text(view.render()),/保存失败：请等待当前任务完成/);assert.deepEqual(view.changes,[]);
});
test('initial provider read failure can retry in the same panel without exposing the key',async()=>{
 const view=setup({fetchFailures:1});view.render();await settle();let tree=view.render();
 assert.match(text(tree),/读取失败/);assert.ok(nodes(tree,'input').every(n=>n.props.disabled));
 const retry=nodes(tree,'button').find(n=>text(n)==='重新读取供应商');
 assert.ok(retry,'explicit read retry remains available after initial GET failure');
 assert.equal(retry.props.disabled,false);retry.props.onClick();await settle();tree=view.render();
 assert.equal(view.reads,2);assert.ok(nodes(tree,'input').every(n=>!n.props.disabled));
 assert.equal(nodes(tree,'input')[2].props.value,'');assert.deepEqual(view.changes,[]);
 nodes(tree,'button').find(n=>text(n)==='保存供应商').props.onClick();await settle();
 assert.equal(view.saves.length,1);assert.equal(view.saves[0].secret_action,'preserve');
});
