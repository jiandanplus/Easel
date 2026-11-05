import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';
const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');
const file = new URL('../../web/frontend/src/lib/deliverables.ts', import.meta.url);
assert.ok(existsSync(file), 'deliverable helpers must exist');
const exports = {};
runInNewContext(ts.transpileModule(readFileSync(file, 'utf8'), {compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,{exports,Error,URL});
const { deliverableReference, deliverableFromUrl, deliverableDownloadUrl, copyDeliverable, deliverableCopyLabel } = exports;
const ref = () => deliverableReference('旧书市集/修改稿.md', '修改稿.md', 'session-1');

test('exact references preserve Unicode paths and optional source without inventing a session', () => {
  assert.deepEqual(JSON.parse(JSON.stringify(ref())), {path:'旧书市集/修改稿.md',name:'修改稿.md',sessionId:'session-1'});
  assert.equal(deliverableReference('project/a.md').sessionId, undefined);
  assert.equal(deliverableFromUrl('#/outputs/project/a.md').path, 'project/a.md');
  assert.equal(deliverableFromUrl('/api/media/project/a%20b.md').name, 'a b.md');
});

test('references reject private, external, malformed, and traversing paths', () => {
  for (const path of ['../x.md','/x.md','a/./x.md','a//x.md','_harness/config.json','a/.private/x.md','a\\x.md','a/\u0000x.md','']) assert.equal(deliverableReference(path), null, path);
  for (const url of ['https://evil.invalid/api/media/a.md','//api/media/a.md','/api/media/%2e%2e/a.md','/api/media/%ZZ.md','javascript:alert(1)',null,undefined,123]) assert.equal(deliverableFromUrl(url), null, url);
});

test('download uses the exact safe original and is independent of preview text', () => {
  assert.equal(deliverableDownloadUrl(ref()), '/api/media/%E6%97%A7%E4%B9%A6%E5%B8%82%E9%9B%86/%E4%BF%AE%E6%94%B9%E7%A8%BF.md');
  assert.equal(deliverableDownloadUrl({path:'../secret',name:'secret'}), null);
});

test('copy loads actual document bytes and waits for clipboard success', async () => {
  const events = []; let finish;
  const work = copyDeliverable(ref(), async path => { events.push(path); return {path,content:'原稿\n\n修改稿',kind:'text',isBinary:false}; }, text => { events.push(text); return new Promise(resolve => { finish = resolve; }); });
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(events, ['旧书市集/修改稿.md','原稿\n\n修改稿']);
  let done = false; work.then(() => {done=true;}); await new Promise(resolve=>setImmediate(resolve)); assert.equal(done,false);
  finish(); await work; assert.equal(done,true);
});

test('copy never includes execution narration and rejects missing, binary, foreign, and failed clipboard data', async () => {
  let writes = 0;
  for (const result of [{path:ref().path,content:'',isBinary:true},{path:'other.md',content:'private',isBinary:false},{path:ref().path,content:null,isBinary:false},{path:ref().path,content:'unverified'}]) await assert.rejects(copyDeliverable(ref(),async()=>result,async()=>{writes++;}));
  await assert.rejects(copyDeliverable(ref(),async()=>{throw new Error('missing');},async()=>{writes++;}));
  await assert.rejects(copyDeliverable(ref(),async()=>({path:ref().path,content:'正文',isBinary:false}),async()=>{throw new Error('denied');}));
  assert.equal(writes,0);
});

test('copy labels distinguish a writing document from structured original source', () => {
  assert.equal(deliverableCopyLabel('文案.md'), '复制文案');
  assert.equal(deliverableCopyLabel('网页.html'), '复制原文');
  assert.equal(deliverableCopyLabel('data.json'), '复制原文');
  assert.equal(deliverableCopyLabel('video.mp4'), null);
});

function actionView({load=async path=>({path,content:'文案正文',isBinary:false}),write=async()=>{}}={}) {
  const state=[],refs=[],effects=[];let cursor=0,refCursor=0,effectCursor=0;const module={};
  const props={reference:ref(),onContinue:()=>{},onSource:()=>{}};
  const jsx=(type,props)=>({type,props:props||{}});
  const React={useState(initial){const n=cursor++;if(!(n in state))state[n]=typeof initial==='function'?initial():initial;return[state[n],value=>{state[n]=typeof value==='function'?value(state[n]):value;}];},useRef(initial){return refs[refCursor++] ||= {current:initial};},useEffect(fn,deps){const n=effectCursor++;if(!effects[n]||deps.some((x,i)=>x!==effects[n].deps[i])){effects[n]?.cleanup?.();effects[n]={deps,cleanup:fn()};}}};
  const source=readFileSync(new URL('../../web/frontend/src/components/DeliverableActions.tsx',import.meta.url),'utf8');
  runInNewContext(ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText,{exports:module,require:id=>id==='react'?React:id==='react/jsx-runtime'?{jsx,jsxs:jsx}:id.endsWith('/api')?{fetchOutputContent:load}:id.endsWith('/deliverables')?exports:{},navigator:{clipboard:{writeText:write}},Error});
  return {render(){cursor=refCursor=effectCursor=0;return module.default(props);},reference(value){props.reference=value;},cleanup(){effects.forEach(e=>e.cleanup?.());}};
}
function nodes(tree,type){return !tree||typeof tree!=='object'?[]:[...(tree.type===type?[tree]:[]),...([tree.props?.children].flat(Infinity)).flatMap(x=>nodes(x,type))];}
function text(tree){return tree==null||typeof tree==='boolean'?'':typeof tree==='object'?[tree.props?.children].flat(Infinity).map(text).join(''):String(tree);}
const settle=()=>new Promise(resolve=>setImmediate(resolve));

test('copy UI reports success only after clipboard settles and never invents a completed download', async()=>{
  let finish;const view=actionView({write:()=>new Promise(resolve=>{finish=resolve;})});
  nodes(view.render(),'button').find(n=>text(n)==='复制文案').props.onClick();await settle();assert.match(text(view.render()),/复制中/);assert.doesNotMatch(text(view.render()),/已复制|已下载/);
  finish();await settle();assert.match(text(view.render()),/已复制/);assert.equal(nodes(view.render(),'a')[0].props.download,'修改稿.md');view.cleanup();
});
test('copy failure stays recoverable and target switches discard late document and status', async()=>{
  const failed=actionView({write:async()=>{throw new Error('permission');}});nodes(failed.render(),'button')[0].props.onClick();await settle();assert.match(text(failed.render()),/复制失败/);assert.equal(nodes(failed.render(),'button')[0].props.disabled,false);failed.cleanup();
  let finish,writes=0;const view=actionView({load:()=>new Promise(resolve=>{finish=resolve;}),write:async()=>{writes++;}});nodes(view.render(),'button')[0].props.onClick();view.reference(deliverableReference('other/new.md'));view.render();finish({path:ref().path,content:'旧稿',isBinary:false});await settle();assert.equal(writes,0);assert.doesNotMatch(text(view.render()),/已复制|复制失败/);view.cleanup();
});
test('rapid copy activation shares one pending action and source-free file offers a new conversation', async()=>{
  let calls=0,finish;const view=actionView({load:()=>{calls++;return new Promise(resolve=>{finish=resolve;});}});view.reference(deliverableReference('project/new.md'));const first=nodes(view.render(),'button')[0];first.props.onClick();first.props.onClick();assert.equal(calls,1);assert.match(text(view.render()),/新建对话/);finish({path:'project/new.md',content:'文案',isBinary:false});await settle();view.cleanup();
});

function galleryView(artifacts, sourceSessionId) {
  const module={};const jsx=(type,props)=>({type,props:props||{}});let index=0;
  const source=readFileSync(new URL('../../web/frontend/src/components/ArtifactGallery.tsx',import.meta.url),'utf8');
  runInNewContext(ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText,{exports:module,require:id=>id==='react'?{useState:value=>typeof value==='number'?[index,v=>{index=v;}]:[value,()=>{}],useEffect:()=>{},useRef:value=>({current:value})}:id==='react/jsx-runtime'?{jsx,jsxs:jsx}:id.endsWith('/artifacts')?{mergeArtifacts:(...groups)=>groups.flat()}:id.endsWith('/deliverables')?exports:id.endsWith('/DeliverableActions')?{__esModule:true,default:'actions'}:{},Error});
  return module.default({artifacts,sourceSessionId,onContinue(){},onSource(){}});
}
test('conversation gallery uses the exact referenced original and source for edit actions',()=>{
  const tree=galleryView([{url:'/api/media/project/card.png',name:'card.png',kind:'image'}],'session-original');
  const action=nodes(tree,'actions')[0];assert.ok(action);assert.equal(action.props.reference.path,'project/card.png');assert.equal(action.props.reference.sessionId,'session-original');
  assert.equal(nodes(tree,'a').length,1);assert.equal(nodes(tree,'a')[0].props.target,'_blank');
});
test('untrusted gallery inputs never create previews or delivery links',()=>{
  assert.equal(galleryView([{url:'https://evil.invalid/a.png',name:'a.png',kind:'image'}]),null);
  assert.equal(galleryView([{url:'/api/media/_harness/config.json',name:'config.json',kind:'image'}]),null);
});
