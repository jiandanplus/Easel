import { requireDeliverables } from './deliverablesHarness.mjs';
import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';
const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');
const sourceUrl = new URL('../../web/frontend/src/lib/chatModels.ts', import.meta.url);
function loadModels() {
  assert.ok(existsSync(sourceUrl), 'chat selection logic must exist');
  const exports = {};
  runInNewContext(ts.transpileModule(readFileSync(sourceUrl, 'utf8'), {compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText, {exports,Object,Number,Array});
  return exports;
}
function loadReadiness() {
 const exports = {};
 runInNewContext(ts.transpileModule(readFileSync(new URL('../../web/frontend/src/lib/workbenchReadiness.ts',import.meta.url),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,{exports,Object,Number,Date});
 return exports;
}
const plain = value => JSON.parse(JSON.stringify(value));
const catalog = {source_revision:'source-one',expires_in:120,revision:'r1', runtime:'pi', service_source:'subscription', subscription_backend:'central', selected_model:'a', switchable:true, ready:true, reason:'', models:[
 {id:'a',label:'Alpha',default:true,reasoning:{status:'supported',efforts:['low','high']}},
 {id:'b',label:'Beta',default:false,reasoning:{status:'unknown',efforts:[]}},
 {id:'c',label:'Gamma',default:false,reasoning:{status:'unsupported',efforts:[]}}]};

test('server selected model is the initial explicit selection with no inferred effort', () => {
 const lib = loadModels();
 assert.deepEqual(plain(lib.resolveChatModelSelection(catalog)), {model:'a',catalog_revision:'r1'});
 assert.equal(lib.resolveChatModelSelection({...catalog, selected_model:'missing'}), undefined);
});
test('remembered model survives only the identical catalogue revision', () => {
 const lib=loadModels(), previous={model:'a',catalog_revision:'r1',reasoning_effort:'high'};
 assert.deepEqual(plain(lib.resolveChatModelSelection(catalog, previous)), previous);
 assert.equal(lib.resolveChatModelSelection({...catalog,revision:'r2'},previous), undefined);
 assert.equal(lib.resolveChatModelSelection({...catalog,models:catalog.models.slice(1)},previous),undefined);
});
test('unsupported and unknown capabilities never send an effort', () => {
 const lib=loadModels();
 for(const model of ['b','c']) assert.deepEqual(plain(lib.selectChatModel(catalog, model, {model:'a',catalog_revision:'r1',reasoning_effort:'high'})),{model,catalog_revision:'r1'});
 assert.equal(lib.selectChatModel(catalog,'missing'),undefined);
 assert.deepEqual(plain(lib.resolveChatModelSelection(catalog,{model:'b',catalog_revision:'r1',reasoning_effort:'high'})),{model:'b',catalog_revision:'r1'});
});
test('switching models clears the previous model effort even when both support reasoning', () => {
 const lib=loadModels(), next={...catalog,models:[...catalog.models,{id:'d',label:'Delta',reasoning:{status:'supported',efforts:['high']}}]};
 assert.deepEqual(plain(lib.selectChatModel(next,'d',{model:'a',catalog_revision:'r1',reasoning_effort:'high'})),{model:'d',catalog_revision:'r1'});
});
test('only an exact server offered effort can become a request selection', () => {
 const lib=loadModels(), selected={model:'a',catalog_revision:'r1'};
 assert.deepEqual(plain(lib.selectChatEffort(catalog,selected,'high')),{...selected,reasoning_effort:'high'});
 assert.equal(lib.selectChatEffort(catalog,selected,'xhigh'),undefined);
 assert.equal(lib.selectChatEffort(catalog,{model:'b',catalog_revision:'r1'},'high'),undefined);
 assert.deepEqual(plain(lib.selectChatEffort(catalog,{...selected,reasoning_effort:'high'},'')),selected);
});
test('selection snapshots are independent immutable request values', () => {
 const lib=loadModels(), selected={model:'a',catalog_revision:'r1',reasoning_effort:'low'};
 const sent=lib.snapshotChatModelSelection(selected);selected.model='b';
 assert.deepEqual(plain(sent),{model:'a',catalog_revision:'r1',reasoning_effort:'low'});
 assert.equal(Object.isFrozen(sent),true);
});
test('chat model directory bridge is read only, uncached, abortable, and credential free',async()=>{
 const api={},calls=[],controller=new AbortController();
 runInNewContext(ts.transpileModule(readFileSync(new URL('../../web/frontend/src/lib/api.ts',import.meta.url),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,{exports:api,require:requireDeliverables,window:{location:{pathname:'/'}},fetch:async(url,options)=>{calls.push({url,options});return{ok:true,json:async()=>catalog};}});
 assert.equal(typeof api.fetchChatModels,'function');await api.fetchChatModels(controller.signal);
 assert.equal(calls[0].url,'/api/chat/models');assert.equal(calls[0].options.cache,'no-store');assert.equal(calls[0].options.signal,controller.signal);assert.equal(calls[0].options.headers?.Authorization,undefined);
 await api.fetchChatModels(controller.signal,'own session',true);assert.equal(calls[1].url,'/api/chat/models?session_id=own%20session&refresh=true');
});

function pickerSetup({ focusModel = false } = {}) {
 const url=new URL('../../web/frontend/src/components/ComposerModelPicker.tsx',import.meta.url);
 assert.ok(existsSync(url),'composer model picker must exist');
 const requests=[],states=[],refs=[],effects=[],callbacks=[],queue=[],listeners=new Map(),timers=new Map();let si=0,ri=0,ei=0,ci=0,now=0,timerId=0;
 const FakeDate=class extends Date{static now(){return now;}};
 const docListeners=new Map(),doms=new Map();let renderedDoms=new Set(),activeElement=null;
 const emitFocus=target=>{activeElement=target;docListeners.get('focusin')?.({target});};
 const element=(type,props,...children)=>{
  const node={type,props:props||{},children:children.flat(Infinity)};
  if(focusModel&&['button','input','section'].includes(type)){
   const cls=props?.className||'',header=cls==='composer-model-back',trigger=cls==='composer-model-trigger';
   const key=header?'header':trigger?'trigger':`${type}:${cls}:${props?.key||text(node)}`;
   let dom=doms.get(key);
   if(!dom){dom={key,area:trigger?'trigger':'panel',focus(){emitFocus(dom);},contains(target){return type==='section'?target?.area==='panel':target===dom;},getBoundingClientRect(){return{right:800,top:700};},querySelector(){return doms.get('header');}};doms.set(key,dom);}
   renderedDoms.add(dom);node.dom=dom;if(props?.ref)props.ref.current=dom;
  }
  return node;
 };

 const React={createElement:element,Fragment:'fragment',
 useState(initial){const i=si++;if(!(i in states))states[i]=typeof initial==='function'?initial():initial;return[states[i],v=>states[i]=typeof v==='function'?v(states[i]):v];},
 useRef(initial){return refs[ri++] ||= {current:initial};},
 useCallback(fn,deps){const i=ci++,old=callbacks[i];if(!old||deps.length!==old.deps.length||deps.some((v,j)=>!Object.is(v,old.deps[j])))callbacks[i]={deps:[...deps],fn};return callbacks[i].fn;},
 useEffect(fn,deps){const i=ei++,old=effects[i];if(!old||deps.some((v,j)=>!Object.is(v,old.deps[j])))queue.push(()=>{old?.cleanup?.();effects[i]={deps,cleanup:fn()};});}};
 const calls=[],lib=loadModels(), exports={},win={innerWidth:1000,innerHeight:800,addEventListener(n,f){listeners.set(n,f);},removeEventListener(n){listeners.delete(n);}};
 const document={body:{area:'outside'},addEventListener(name,fn){docListeners.set(name,fn);},removeEventListener(name){docListeners.delete(name);}};
 runInNewContext(ts.transpileModule(readFileSync(url,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.React}}).outputText,{exports,require:n=>n==='react'?React:n==='react-dom'?{createPortal:v=>v}:n.endsWith('/chatModels')?lib:n.endsWith('/workbenchReadiness')?loadReadiness():n.endsWith('/api')?{fetchChatModels(signal,sessionId,refresh){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});requests.push({resolve,reject,signal,sessionId,refresh});return promise;}}:{},React,window:win,document,AbortController,Error,Number,Math,Object,Array,Date:FakeDate,setTimeout(fn,delay){const id=++timerId;timers.set(id,{fn,at:now+delay});return id;},clearTimeout(id){timers.delete(id);}});
 let props={sessionId:'one',disabled:false,refreshKey:0,onChange:(selection,ready)=>calls.push({selection,ready})};
 return {requests,calls,render(extra={}){props={...props,...extra};si=ri=ei=ci=0;renderedDoms=new Set();const tree=exports.default(props);if(focusModel&&activeElement?.area==='panel'&&!renderedDoms.has(activeElement))emitFocus(document.body);queue.splice(0).forEach(f=>f());return tree;},click(node){node.dom?.focus();node.props.onClick();},active:()=>activeElement,tabOutside(){emitFocus(document.body);},focus(){listeners.get('focus')?.();},advance(ms){now+=ms;const due=[...timers.entries()].filter(([,v])=>v.at<=now);for(const[id,value]of due){if(timers.has(id)){timers.delete(id);value.fn();}}},timerCount:()=>timers.size,cleanup(){effects.forEach(e=>e?.cleanup?.());}};
}
const nodes=(t,type)=>!t||typeof t!=='object'?[]:[...(t.type===type?[t]:[]),...(t.children||[]).flat(Infinity).flatMap(c=>nodes(c,type))];
const text=t=>typeof t==='string'?t:t&&typeof t==='object'?(t.children||[]).flat(Infinity).map(text).join(''):'';
const settle=()=>new Promise(resolve=>setImmediate(resolve));
test('picker loads real directory and preserves a valid lease while refreshing',async()=>{
 const v=pickerSetup();v.render();assert.equal(v.requests.length,1);assert.equal(v.requests[0].sessionId,'one');assert.equal(v.requests[0].refresh,false);assert.equal(v.calls.at(-1).ready,false);
 v.requests[0].resolve(catalog);await settle();let t=v.render();assert.equal(v.calls.at(-1).ready,true);assert.equal(v.calls.at(-1).selection.model,'a');assert.match(text(t),/Alpha/);
 v.focus();assert.equal(v.calls.at(-1).ready,true);assert.equal(v.requests.length,2);assert.equal(v.requests[1].refresh,true);v.requests[1].reject(Error('PRIVATE_BACKEND'));await settle();t=v.render();assert.equal(v.calls.at(-1).ready,false);assert.doesNotMatch(text(t),/PRIVATE_BACKEND/);assert.match(text(t),/Alpha/);v.cleanup();
});
test('late directory response cannot replace a newer configuration',async()=>{
 const v=pickerSetup();v.render();v.render({refreshKey:1});assert.equal(v.requests[0].signal.aborted,true);
 v.requests[1].resolve({...catalog,revision:'r2',selected_model:'b'});await settle();v.requests[0].resolve(catalog);await settle();const t=v.render();assert.match(text(t),/Beta/);assert.doesNotMatch(text(t),/Alpha/);assert.equal(v.calls.at(-1).selection.catalog_revision,'r2');v.cleanup();
});
test('fixed native default stays usable without an explicit model override',async()=>{
 const v=pickerSetup();v.render();v.requests[0].resolve({...catalog,switchable:false,ready:true,selected_model:'',models:[],reason:'native_default'});await settle();v.render();assert.equal(v.calls.at(-1).ready,true);assert.equal(v.calls.at(-1).selection,undefined);v.cleanup();
});
test('failed readiness never silently falls back to a default model',async()=>{
 const v=pickerSetup();v.render();v.requests[0].resolve({...catalog,ready:false,reason:'authentication_required'});await settle();v.render();assert.equal(v.calls.at(-1).ready,false);assert.equal(v.calls.at(-1).selection,undefined);v.cleanup();
});

test('composer send snapshots current selection, blocks unverified directory, and preserves draft until ready',()=>{
 const source=readFileSync(new URL('../../web/frontend/src/components/ChatPage.tsx',import.meta.url),'utf8');
 const parsed=ts.createSourceFile('ChatPage.tsx',source,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
 let handler;function visit(n){if(ts.isVariableDeclaration(n)&&n.name.getText(parsed)==='handleSend')handler=n;ts.forEachChild(n,visit);}visit(parsed);
 const sent=[],cleared=[];
 const run=(ready)=>runInNewContext(ts.transpileModule('('+handler.initializer.getText(parsed)+');',{compilerOptions:{target:ts.ScriptTarget.ES2022}}).outputText,{input:' hello ',attachments:[],isStreaming:false,uploading:false,modelReady:ready,readiness:{canSubmit:true},draftStore:{load:()=>({})},draftScopeKey:'scope',session:{id:'session'},crypto:{randomUUID:()=> 'turn'},composer:{prepare:()=>({submission:{submittedRevision:1}})},modelSelection:{model:'b',catalog_revision:'r1'},snapshotChatModelSelection:loadModels().snapshotChatModelSelection,onSend:(...args)=>sent.push(args),setInput:v=>cleared.push(v),setAttachments:()=>{}})();
 run(false);assert.equal(sent.length,0);assert.equal(cleared.length,0);run(true);assert.equal(sent.length,1);assert.equal(sent[0][2].model,'b');assert.equal(Object.isFrozen(sent[0][2]),true);
});
test('stream posts explicit model once and recovery carries only the owned job id',async()=>{
 const source=readFileSync(new URL('../../web/frontend/src/lib/api.ts',import.meta.url),'utf8'),parsed=ts.createSourceFile('api.ts',source,ts.ScriptTarget.Latest,true);
 const fn=parsed.statements.find(n=>ts.isFunctionDeclaration(n)&&n.name?.text==='streamChat');const calls=[];let finish;
 const done=new Promise(resolve=>finish=resolve);const stream=runInNewContext(ts.transpileModule(fn.getText(parsed).replace('export ','')+'\nstreamChat;',{compilerOptions:{target:ts.ScriptTarget.ES2022}}).outputText,{AbortController,TextDecoder,Date,Error,setTimeout,BASE:'',fetch:async(url,options)=>{calls.push({url,options});return new Response('event: done\ndata: {}\n\n');}});
 stream('hello',undefined,'s',()=>{},finish,e=>{throw e;},undefined,undefined,undefined,'turn',false,undefined,[],undefined,undefined,{model:'b',catalog_revision:'r1',reasoning_effort:'high'});await done;
 const payload=JSON.parse(calls[0].options.body);assert.equal(payload.model,'b');assert.equal(payload.reasoning_effort,'high');assert.equal(payload.catalog_revision,'r1');
 let recovered;const resumed=new Promise(resolve=>recovered=resolve);stream('',undefined,'s',()=>{},recovered,e=>{throw e;},undefined,undefined,undefined,'turn',true,undefined,[],undefined,undefined,{model:'c',catalog_revision:'changed'});await resumed;
 assert.match(calls[1].url,/jobs\/turn\/stream/);assert.equal(calls[1].options.method,undefined);assert.equal(calls[1].options.body,undefined);
});

const button=(tree,label)=>nodes(tree,'button').find(n=>text(n)===label);
const modelButton=(tree,label)=>nodes(tree,'button').find(n=>n.props.className==='composer-model-option'&&text(n).startsWith(label));
const trigger=tree=>nodes(tree,'button').find(n=>n.props.className==='composer-model-trigger');
test('picker model and effort actions follow server catalogue and switching clears effort',async()=>{
 const v=pickerSetup();v.render();v.requests[0].resolve(catalog);await settle();let t=v.render();trigger(t).props.onClick();t=v.render();
 button(t,'推理强度默认›').props.onClick();t=v.render();button(t,'高').props.onClick();t=v.render();assert.equal(v.calls.at(-1).selection.reasoning_effort,'high');
 button(t,'模型Alpha›').props.onClick();t=v.render();modelButton(t,'Beta').props.onClick();t=v.render();assert.equal(v.calls.at(-1).selection.model,'b');assert.equal(v.calls.at(-1).selection.reasoning_effort,undefined);assert.doesNotMatch(text(t),/尚未声明|该模型接口/);assert.equal(button(t,'推理强度默认›').props.disabled,true);v.cleanup();
});
test('failed refresh disables every old catalogue action rather than re-enabling send',async()=>{
 const v=pickerSetup();v.render();v.requests[0].resolve(catalog);await settle();let t=v.render();trigger(t).props.onClick();t=v.render();button(t,'模型Alpha›').props.onClick();v.render();
 v.focus();v.requests[1].reject(Error('network'));await settle();t=v.render();const beta=modelButton(t,'Beta');assert.equal(beta.props.disabled,true);const count=v.calls.length;beta.props.onClick();assert.equal(v.calls.length,count);assert.equal(v.calls.at(-1).ready,false);v.cleanup();
});
test('new catalogue revision requires explicit confirmation and then restores readiness',async()=>{
 const v=pickerSetup();v.render();v.requests[0].resolve(catalog);await settle();let t=v.render();trigger(t).props.onClick();v.render();v.focus();v.requests[1].resolve({...catalog,revision:'r2',source_revision:'changed-source'});await settle();t=v.render();assert.equal(v.calls.at(-1).ready,false);assert.match(text(t),/重新选择/);button(t,'模型选择模型›').props.onClick();t=v.render();modelButton(t,'Alpha').props.onClick();v.render();assert.equal(v.calls.at(-1).ready,true);assert.equal(v.calls.at(-1).selection.catalog_revision,'r2');v.cleanup();
});
test('running task closes the picker and disables the visible trigger',async()=>{
 const v=pickerSetup();v.render();v.requests[0].resolve(catalog);await settle();let t=v.render();trigger(t).props.onClick();t=v.render();assert.equal(nodes(t,'section').length,1);v.render({disabled:true});t=v.render();assert.equal(trigger(t).props.disabled,true);assert.equal(nodes(t,'section').length,0);const before=v.requests.length;v.focus();assert.equal(v.requests.length,before);v.cleanup();
});

test('App freezes the selected model before constructing the running turn',()=>{
 const source=readFileSync(new URL('../../web/frontend/src/App.tsx',import.meta.url),'utf8');
 const parsed=ts.createSourceFile('App.tsx',source,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);let declaration;
 function visit(n){if(ts.isVariableDeclaration(n)&&n.name.getText(parsed)==='startStream')declaration=n;ts.forEachChild(n,visit);}visit(parsed);
 const calls=[],given={model:'a',catalog_revision:'r1',reasoning_effort:'high'};
 const fn=runInNewContext(ts.transpileModule('('+declaration.initializer.getText(parsed)+');',{compilerOptions:{target:ts.ScriptTarget.ES2022}}).outputText,{
 useCallback:f=>f,snapshotChatModelSelection:loadModels().snapshotChatModelSelection,Date,Math,sessionStorage:{setItem(){}},setSessions(){},saveSessions(){},streamAcc:{current:{}},setStreams(){},typingBuf:{current:{}},startTypingPump(){},streamCtl:{current:{}},streamChat:(...args)=>{calls.push(args);return{};},appendAssistant(){},clearStream(){},draftStore:{},workbench:undefined,
 });fn('session','question',undefined,[],given);given.model='b';assert.equal(calls[0][15].model,'a');assert.equal(Object.isFrozen(calls[0][15]),true);
});

test('same source lease refresh preserves selected model and effort with a new request revision',async()=>{
 const v=pickerSetup();v.render();v.requests[0].resolve(catalog);await settle();let t=v.render();trigger(t).props.onClick();t=v.render();button(t,'推理强度默认›').props.onClick();t=v.render();button(t,'高').props.onClick();v.render();
 v.advance(115000);assert.equal(v.requests.length,2);assert.equal(v.requests[1].refresh,true);assert.equal(v.calls.at(-1).ready,true);
 v.requests[1].resolve({...catalog,revision:'r2'});await settle();v.render();assert.equal(v.calls.at(-1).ready,true);assert.equal(v.calls.at(-1).selection.model,'a');assert.equal(v.calls.at(-1).selection.catalog_revision,'r2');assert.equal(v.calls.at(-1).selection.reasoning_effort,'high');v.cleanup();assert.equal(v.timerCount(),0);
});

test('a pending refresh cannot extend the original catalogue expiry',async()=>{
 const v=pickerSetup();v.render();v.requests[0].resolve(catalog);await settle();v.render();v.advance(115000);assert.equal(v.calls.at(-1).ready,true);
 v.advance(5000);v.render();assert.equal(v.calls.at(-1).ready,false);assert.equal(v.requests.length,2);v.cleanup();
});
test('contract compatibility and source changes invalidate submission independently of directory readiness',async()=>{
 const decisions=[],workbench={version:1,build_id:'a'.repeat(64),workspace_scope:'b'.repeat(64),source_scope:'first'};
 const v=pickerSetup();v.render({workbench,onReadiness:value=>decisions.push(value)});v.requests[0].resolve(catalog);await settle();v.render();assert.equal(v.calls.at(-1).ready,true);assert.equal(decisions.at(-1).state,'ready');
 v.render({workbench:{...workbench,version:2}});assert.equal(v.calls.at(-1).ready,false);assert.equal(decisions.at(-1).state,'incompatible');
 v.render({workbench:{...workbench,source_scope:'second'}});assert.equal(v.calls.at(-1).ready,false);assert.equal(decisions.at(-1).state,'source-changed');v.cleanup();
});
test('a remounted old conversation still rejects a fresh directory for another account',async()=>{
 const decisions=[],workbench={version:1,build_id:'a'.repeat(64),workspace_scope:'b'.repeat(64),source_scope:'new-account'};
 const v=pickerSetup();v.render({workbench,expectedSourceScope:'original-account',onReadiness:value=>decisions.push(value)});
 v.requests[0].resolve(catalog);await settle();v.render();
 assert.equal(v.calls.at(-1).ready,false);assert.equal(decisions.at(-1).state,'source-changed');
 v.render();v.render();assert.equal(v.requests.length,1,'stable memo dependencies must not reload every render');v.cleanup();
});
test('a changed source never silently carries the model into the new authority',async()=>{
 const v=pickerSetup();v.render();v.requests[0].resolve(catalog);await settle();v.render();v.focus();v.requests[1].resolve({...catalog,revision:'r2',source_revision:'different-account'});await settle();v.render();assert.equal(v.calls.at(-1).ready,false);assert.equal(v.calls.at(-1).selection,undefined);v.cleanup();
});
test('expired lease while running disables the next send and refreshes only after the task stops',async()=>{
 const v=pickerSetup();v.render();v.requests[0].resolve(catalog);await settle();v.render();v.render({disabled:true});v.advance(120000);v.render();assert.equal(v.requests.length,1);assert.equal(v.calls.at(-1).ready,false);
 v.render({disabled:false});assert.equal(v.requests.length,2);assert.equal(v.requests[1].refresh,true);v.requests[1].resolve({...catalog,revision:'r2'});await settle();v.render();assert.equal(v.calls.at(-1).ready,true);v.cleanup();
});
test('expired directory responses fail closed without scheduling an immediate request loop',async()=>{
 const v=pickerSetup();v.render();v.requests[0].resolve({...catalog,expires_in:0});await settle();let t=v.render();trigger(t).props.onClick();t=v.render();assert.equal(v.calls.at(-1).ready,false);assert.match(text(t),/过期|刷新/);v.advance(120000);assert.equal(v.requests.length,1);v.cleanup();
});
test('public unavailable reasons explain the next user action while invalid values use a bounded fallback',()=>{
 const lib=loadModels();assert.match(lib.chatModelUnavailableMessage('请先登录订阅账号，再加载可用模型'),/登录/);assert.match(lib.chatModelUnavailableMessage('云端执行尚未开放'),/本地/);assert.ok(lib.chatModelUnavailableMessage('x'.repeat(201)).length<200);assert.equal(lib.chatModelUnavailableMessage('此对话属于其他服务来源，请新建对话'),'此对话属于其他服务来源，请新建对话');
});

test('source changes invalidate a choice independently of the opaque lease revision',async()=>{
 const v=pickerSetup();v.render();v.requests[0].resolve(catalog);await settle();v.render();v.focus();v.requests[1].resolve({...catalog,source_revision:'different-account'});await settle();v.render();assert.equal(v.calls.at(-1).ready,false);v.cleanup();
});

test('changing picker pages preserves focus inside the persistent header across separate actions',async()=>{
 const v=pickerSetup({focusModel:true});v.render();v.requests[0].resolve(catalog);await settle();let t=v.render();v.click(trigger(t));t=v.render();v.click(button(t,'推理强度默认›'));t=v.render();assert.equal(v.active()?.key,'header');assert.equal(nodes(v.render(),'section').length,1);
 v.click(button(t,'高'));t=v.render();assert.equal(v.active()?.key,'header');assert.equal(nodes(v.render(),'section').length,1);assert.equal(v.calls.at(-1).selection.reasoning_effort,'high');
 v.click(button(t,'模型Alpha›'));t=v.render();assert.equal(v.active()?.key,'header');v.click(modelButton(t,'Beta'));t=v.render();assert.equal(v.active()?.key,'header');assert.equal(nodes(v.render(),'section').length,1);assert.equal(v.calls.at(-1).selection.model,'b');v.cleanup();
});
test('real focus leaving the picker still closes it without stealing the new focus',async()=>{
 const v=pickerSetup({focusModel:true});v.render();v.requests[0].resolve(catalog);await settle();let t=v.render();v.click(trigger(t));t=v.render();assert.equal(nodes(t,'section').length,1);v.tabOutside();t=v.render();assert.equal(nodes(t,'section').length,0);assert.equal(v.active().area,'outside');v.cleanup();
});


test('remembered verified source restores the same model after a running composer remount', async () => {
 const v=pickerSetup(), decisions=[];
 const workbench={version:1,build_id:'a'.repeat(64),workspace_scope:'b'.repeat(64),source_scope:'same-account'};
 v.render({disabled:true,initialSelection:{model:'a',catalog_revision:'old-lease',reasoning_effort:'high'},initialSelectionSourceRevision:'source-one',workbench,onReadiness:value=>decisions.push(value)});
 assert.equal(v.requests.length,0);
 v.render({disabled:false});assert.equal(v.requests.length,1);assert.equal(v.requests[0].refresh,true);
 v.requests[0].resolve({...catalog,revision:'fresh-lease'});await settle();const tree=v.render();
 assert.match(text(tree),/Alpha/);assert.equal(decisions.at(-1).state,'ready');
 assert.equal(v.calls.at(-1).ready,true);assert.equal(v.calls.at(-1).selection.model,'a');
 assert.equal(v.calls.at(-1).selection.catalog_revision,'fresh-lease');assert.equal(v.calls.at(-1).selection.reasoning_effort,'high');v.cleanup();
});

test('remount does not rebind an unknown remembered authority to a fresh lease', async () => {
 const v=pickerSetup();v.render({disabled:true,initialSelection:{model:'a',catalog_revision:'old-lease'}});v.render({disabled:false});
 v.requests[0].resolve({...catalog,revision:'fresh-lease'});await settle();v.render();assert.equal(v.calls.at(-1).ready,false);assert.equal(v.calls.at(-1).selection,undefined);v.cleanup();
});

test('a changed directory source refuses remembered provenance even with the same model', async () => {
 const v=pickerSetup();v.render({disabled:true,initialSelection:{model:'a',catalog_revision:'old-lease'},initialSelectionSourceRevision:'other-account'});v.render({disabled:false});
 v.requests[0].resolve({...catalog,revision:'fresh-lease'});await settle();v.render();assert.equal(v.calls.at(-1).ready,false);assert.equal(v.calls.at(-1).selection,undefined);v.cleanup();
});


test('remembered model removed from the fresh source never falls back to the server default', async () => {
 const v=pickerSetup();v.render({disabled:true,initialSelection:{model:'b',catalog_revision:'old-lease'},initialSelectionSourceRevision:'source-one'});v.render({disabled:false});
 v.requests[0].resolve({...catalog,revision:'fresh-lease',models:catalog.models.filter(item=>item.id!=='b')});await settle();const tree=v.render();
 assert.equal(v.calls.at(-1).ready,false);assert.equal(v.calls.at(-1).selection,undefined);assert.doesNotMatch(text(tree),/Beta|Alpha/);v.cleanup();
});

test('remembered reasoning value must still be explicitly offered by the fresh directory', async () => {
 const v=pickerSetup();v.render({disabled:true,initialSelection:{model:'a',catalog_revision:'old-lease',reasoning_effort:'high'},initialSelectionSourceRevision:'source-one'});v.render({disabled:false});
 v.requests[0].resolve({...catalog,revision:'fresh-lease',models:catalog.models.map(item=>item.id==='a'?{...item,reasoning:{status:'supported',efforts:['low']}}:item)});await settle();v.render();assert.equal(v.calls.at(-1).ready,false);assert.equal(v.calls.at(-1).selection,undefined);v.cleanup();
});

test('both App composer routes retain only source-proven selections in UI memory',()=>{
 const source=readFileSync(new URL('../../web/frontend/src/App.tsx',import.meta.url),'utf8');
 const parsed=ts.createSourceFile('App.tsx',source,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX),routes=[];
 function visit(node){if((ts.isJsxSelfClosingElement(node)||ts.isJsxOpeningElement(node))&&node.tagName.getText(parsed)==='ChatPage')routes.push(node);ts.forEachChild(node,visit);}visit(parsed);
 assert.equal(routes.length,2);
 for(const route of routes){
  const props=new Map(route.attributes.properties.filter(ts.isJsxAttribute).map(prop=>[prop.name.getText(parsed),prop.initializer?.expression]));
  assert.ok(props.get('initialModelSourceRevision'),'source provenance must cross both Dashboard and ChatPage remount boundaries');
  const modelSelections={current:{}},selected={model:'b',catalog_revision:'lease-2',reasoning_effort:'high'},context={modelSelections,dashboardSession:{id:'dash'},activeSession:{id:'chat'}};
  const evaluate=node=>runInNewContext(ts.transpileModule('('+node.getText(parsed)+');',{compilerOptions:{target:ts.ScriptTarget.ES2022}}).outputText,context);
  const remember=evaluate(props.get('onRememberModel'));
  remember(selected,undefined);assert.equal(Object.keys(modelSelections.current).length,0,'unproven display selection cannot be remembered');
  remember(selected,'verified-source');assert.equal(evaluate(props.get('initialModelSelection')),selected);assert.equal(evaluate(props.get('initialModelSourceRevision')),'verified-source');
  assert.deepEqual(Object.keys(selected).sort(),['catalog_revision','model','reasoning_effort'],'provenance stays outside the request selection');
  remember(undefined);assert.equal(Object.keys(modelSelections.current).length,0);
 }
});

test('App account or workbench source changes clear all remembered model authorities',()=>{
 const source=readFileSync(new URL('../../web/frontend/src/App.tsx',import.meta.url),'utf8');
 const parsed=ts.createSourceFile('App.tsx',source,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);let effect;
 function visit(node){if(ts.isCallExpression(node)&&node.expression.getText(parsed)==='useEffect'&&node.arguments[0]?.getText(parsed).includes('modelSelections.current = {}'))effect=node;ts.forEachChild(node,visit);}visit(parsed);
 assert.ok(effect);const modelSelections={current:{one:{selection:{model:'a',catalog_revision:'old'},sourceRevision:'old-source'}}},calls=[];
 runInNewContext(ts.transpileModule(effect.getText(parsed),{compilerOptions:{target:ts.ScriptTarget.ES2022}}).outputText,{modelSelections,workbench:{source_scope:'new-authority'},useEffect:(callback,deps)=>calls.push({callback,deps})});
 assert.equal(calls[0].deps[0],'new-authority');calls[0].callback();assert.equal(Object.keys(modelSelections.current).length,0);
});
