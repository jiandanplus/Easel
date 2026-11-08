import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';
const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');
const settle = () => new Promise(resolve => setImmediate(resolve));
const model = { id:'gpt-image-2.5-flare', name:'Flare', capability:'image.generate', test_only:false, readiness:'live', rate:{version:'v1',meters:[{unit:'image',basis:'verified_delivery_v1',numerator:'1000000',denominator:'1'}]} };
const nodes = (tree, type) => !tree || typeof tree !== 'object' ? [] : [...(tree.type === type ? [tree] : []), ...(tree.children || []).flatMap(n => nodes(n,type))];
const text = tree => typeof tree === 'string' || typeof tree === 'number' ? String(tree) : !tree || typeof tree !== 'object' ? '' : (tree.children || []).map(text).join('');
function setup({read = async () => ({models:[model]}), signature = 'A', status = async () => ({id:signature})} = {}) {
 const source=readFileSync(new URL('../../web/frontend/src/components/CentralMediaSettings.tsx',import.meta.url),'utf8');
 const code=ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.React}}).outputText;
 const states=[],effects=[],exports={},calls=[],listeners=new Set();let cursor=0,ec=0,version=0,props={accountSignature:signature,revision:0};
 const React={Fragment:'fragment',createElement:(type,props,...children)=>({type,props:props||{},children:children.flat(Infinity)}),useState(initial){const i=cursor++;if(!(i in states))states[i]=typeof initial==='function'?initial():initial;return [states[i],v=>states[i]=typeof v==='function'?v(states[i]):v]},useEffect(fn,deps){const i=ec++;if(!effects[i]||deps.some((v,j)=>v!==effects[i].deps[j])){effects[i]?.cleanup?.();effects[i]={deps,cleanup:fn()}}}};
 const api={centralMediaModels:async signal=>{calls.push(signal);return read(signal)},centralStatus:status,centralAccountSignature:s=>s.id||null,centralAccountBoundaryVersion:()=>version,subscribeCentralAccountBoundary:fn=>{listeners.add(fn);return()=>listeners.delete(fn)}};
 runInNewContext(code,{exports,require:name=>name==='react'?React:name.includes('/api')?api:{},React,Promise,Error,BigInt,AbortController});
 return {calls,render(){cursor=0;ec=0;return exports.default(props)},props(value){props={...props,...value}},invalidate(){version++;listeners.forEach(fn=>fn())},cleanup(){effects.forEach(e=>e.cleanup?.())}};
}
test('image models show verified image price without credentials or explanatory paragraphs',async()=>{const v=setup();v.render();await settle();const t=v.render();assert.match(text(t),/Flare/);assert.match(text(t),/1 点\/张/);assert.match(text(t),/可用/);assert.doesNotMatch(text(t),/微点|本阶段|API Key|https:|供应商|钱包|请联系/);assert.equal(nodes(t,'input').length,0);v.cleanup()});
test('every capability shows its own unit, including discounted fractions',async()=>{const m=(id,capability,unit,numerator,denominator)=>({...model,id,name:id,capability,rate:{version:'v',meters:[{unit,basis:'verified_delivery_v1',numerator,denominator}]}});const v=setup({read:async()=>({models:[m('V','video.generate','video_ms','2000','1'),m('M','music.generate','audio_clip','4000000','5'),m('S','audio.speech','unicode_scalar','100','1')]})});v.render();await settle();const t=text(v.render());assert.match(t,/视频 · 2 点\/秒/);assert.match(t,/音乐 · 0\.8 点\/首/);assert.match(t,/语音合成 · 1 点\/万字/);v.cleanup()});
test('no login does not fetch a catalog',async()=>{const v=setup({signature:null});v.render();await settle();assert.equal(v.calls.length,0);assert.match(text(v.render()),/登录/);v.cleanup()});
test('transport failure never renders a false empty or a stale prior model',async()=>{const v=setup({read:async()=>{throw new Error('private upstream URL')}});v.render();await settle();assert.match(text(v.render()),/暂不可用/);assert.doesNotMatch(text(v.render()),/private|暂无可用/);v.cleanup()});
test('empty catalog has an explicit empty state and can refresh',async()=>{const v=setup({read:async()=>({models:[]})});v.render();await settle();assert.match(text(v.render()),/暂无可用媒体模型/);nodes(v.render(),'button')[0].props.onClick();v.render();await settle();assert.equal(v.calls.length,2);v.cleanup()});
for(const change of ['identity','boundary','unmount'])test(`${change} rejects late media catalog`,async()=>{let complete;const v=setup({read:()=>new Promise(r=>{complete=r})});v.render();await settle();if(change==='identity'){v.props({accountSignature:'B'});v.render()}else if(change==='boundary')v.invalidate();else v.cleanup();complete({models:[model]});await settle();if(change!=='unmount')assert.doesNotMatch(text(v.render()),/Flare/);v.cleanup()});
test('changed identity at final check never displays other account model data',async()=>{let count=0;const v=setup({status:async()=>({id:++count===1?'A':'B'})});v.render();await settle();assert.doesNotMatch(text(v.render()),/Flare/);assert.match(text(v.render()),/账号/);v.cleanup()});
