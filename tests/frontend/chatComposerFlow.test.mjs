import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';
const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');
const plain = value => JSON.parse(JSON.stringify(value));
function setup() {
  const states=[],refs=[],effects=[],callbacks=[],queue=[],values=new Map(),requests=[],sent=[],queried=[];let si=0,ri=0,ei=0,ci=0,uuid=0;
  const listeners=new Map(),timers=new Map();let timerId=0;
  const React={
    createElement:(type,props,...children)=>({type,props:props||{},children:children.flat(Infinity)}),Fragment:'fragment',
    useState(initial){const i=si++;if(!(i in states))states[i]=typeof initial==='function'?initial():initial;return[states[i],v=>states[i]=typeof v==='function'?v(states[i]):v];},
    useRef(initial){return refs[ri++] ||= {current:initial};},
    useEffect(fn,deps){const i=ei++,old=effects[i];if(!old||deps.some((v,j)=>!Object.is(v,old.deps[j])))queue.push(()=>{old?.cleanup?.();effects[i]={deps,cleanup:fn()};});},
    useCallback(fn,deps){const i=ci++,old=callbacks[i];if(!old||deps.some((v,j)=>!Object.is(v,old.deps[j])))callbacks[i]={deps,fn};return callbacks[i].fn;},
  };
  const win={addEventListener:(name,fn)=>listeners.set(name,fn),removeEventListener:(name)=>listeners.delete(name)};
  let uploadResolve;
  const api={validateDraftAttachments(sessionId,attachments,signal){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});requests.push({sessionId,attachments:plain(attachments),signal,resolve,reject});return promise;},uploadFiles:async()=>[{id:'uploaded',name:'新素材.txt',path:'outputs/inbox/chat1/uploaded.txt'}],adoptOversize:async()=>[]};
  const cache=new Map();
  function module(name){if(cache.has(name))return cache.get(name);const exports={};cache.set(name,exports);const isComponent=name==='ChatPage';
    const code=ts.transpileModule(readFileSync(new URL(`../../web/frontend/src/${isComponent?'components':'lib'}/${name}.${isComponent?'tsx':'ts'}`,import.meta.url),'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.React}}).outputText;
    runInNewContext(code,{exports,require:n=>n==='react'?React:n.endsWith('/api')?api:n.endsWith('/chatModels')?module('chatModels'):n.endsWith('/useComposerDraft')?module('useComposerDraft'):n==='./composerDrafts'?module('composerDrafts'):n.endsWith('/workbenchReadiness')?module('workbenchReadiness'):n.endsWith('/taskLifecycle')?module('taskLifecycle'):{default:n},React,window:win,document:{},navigator:{clipboard:{writeText:async()=>{}}},AbortController,Date,Math,Object,Number,Map,Set,JSON,Error,crypto:{randomUUID:()=>`turn-${++uuid}`},fetch:async()=>({json:async()=>({max_mb:50})}),requestAnimationFrame:fn=>fn(),setTimeout(fn){const id=++timerId;timers.set(id,fn);return id;},clearTimeout:id=>timers.delete(id)});return exports;
  }
  const store=module('composerDrafts').createComposerDraftStore({getItem:k=>values.get(k)??null,setItem:(k,v)=>values.set(k,v)});
  let props={apiDirect:false,session:{id:'chat1',messages:[]},draftScopeKey:'scope1',draftStore:store,workbench:{version:1,build_id:'a'.repeat(64),workspace_scope:'b'.repeat(64),source_scope:'c'.repeat(64)},onSend:(...args)=>sent.push(args),onStop(){},onResend(){},onQueryTurn:id=>queried.push(id)};
  return {store,requests,sent,queried,deferUpload(){api.uploadFiles=()=>new Promise(resolve=>{uploadResolve=resolve;});},finishUpload(value){uploadResolve(value);},render(extra={}){props={...props,...extra};si=ri=ei=ci=0;const tree=module('ChatPage').default(props);queue.splice(0).forEach(fn=>fn());return tree;},flush(){for(const [id,fn] of [...timers]){timers.delete(id);fn();}},cleanup(){effects.forEach(e=>e?.cleanup?.());},props};
}
const nodes=(tree,type)=>!tree||typeof tree!=='object'?[]:[...(!type||tree.type===type?[tree]:[]),...(tree.children||[]).flat(Infinity).flatMap(child=>nodes(child,type))];
const text=tree=>typeof tree==='string'?tree:tree&&typeof tree==='object'?(tree.children||[]).flat(Infinity).map(text).join(''):'';
const button=(tree,title)=>nodes(tree,'button').find(node=>node.props.title===title||node.props['aria-label']===title||text(node)===title);
function ready(tree){const picker=nodes(tree).find(node=>node.props.sessionId&&node.props.onChange);assert.ok(picker);picker.props.onChange({model:'one',catalog_revision:'r1'},true);picker.props.onReadiness?.({state:'ready',canSubmit:true,label:'可以开始',action:'none'});}
const settle=()=>new Promise(resolve=>setImmediate(resolve));

test('chat example fills the draft without a model request',()=>{
  const v=setup();let tree=v.render();const example=nodes(tree,'button').find(node=>text(node).includes('写小红书文案'));example.props.onClick();tree=v.render();
  assert.equal(v.sent.length,0);assert.match(nodes(tree,'textarea')[0].props.value,/小红书/);v.cleanup();
});
test('chat submission keeps its snapshot until an accepted receipt and blocks a second click',()=>{
  const v=setup();let tree=v.render();ready(tree);tree=v.render();nodes(tree,'textarea')[0].props.onChange({target:{value:' 三个要求 '}});tree=v.render();button(tree,'发送').props.onClick();tree=v.render();
  assert.equal(v.sent.length,1);assert.equal(v.sent[0][0],'三个要求');assert.equal(v.sent[0][3].turnId,'turn-1');assert.equal(v.sent[0][3].scopeKey,'scope1');
  assert.equal(nodes(tree,'textarea')[0].props.value,' 三个要求 ');assert.equal(button(tree,'发送').props.disabled,true);button(tree,'发送').props.onClick();assert.equal(v.sent.length,1);
  button(tree,'查询状态').props.onClick();assert.deepEqual(v.queried,['turn-1']);v.cleanup();
});
test('accepted refresh clears only the submitted revision and preserves next draft',()=>{
  const v=setup();let tree=v.render();ready(tree);tree=v.render();nodes(tree,'textarea')[0].props.onChange({target:{value:'first'}});tree=v.render();button(tree,'发送').props.onClick();tree=v.render();
  nodes(tree,'textarea')[0].props.onChange({target:{value:'next'}});tree=v.render();const submitted=v.sent[0][3];v.store.acknowledge('scope1','chat1',submitted.turnId,submitted.revision);
  v.render({draftRefreshKey:1});tree=v.render();assert.equal(nodes(tree,'textarea')[0].props.value,'next');v.cleanup();
});
test('running and stopping retain editable draft and upload access without another send',()=>{
  const v=setup();let tree=v.render({stream:{content:'',thinking:'',activity:'',questions:[],phase:'stopping'}});
  const input=nodes(tree,'textarea')[0];assert.notEqual(input.props.disabled,true);input.props.onChange({target:{value:'next while running'}});tree=v.render();
  assert.equal(button(tree,'添加素材').props.disabled,false);assert.equal(button(tree,'停止生成').props.disabled,true);assert.equal(v.sent.length,0);v.cleanup();
});
test('restored missing attachment is retained and prevents send',async()=>{
  const v=setup();v.store.update('scope1','chat1',{text:'with material',attachments:[{id:'a',name:'材料.txt',path:'outputs/inbox/chat1/a.txt'}]});
  let tree=v.render();ready(tree);tree=v.render();assert.equal(v.requests.length,1);assert.equal(button(tree,'发送').props.disabled,true);
  v.requests[0].resolve({version:1,session_id:'chat1',attachments:[{id:'a',name:'材料.txt',path:'outputs/inbox/chat1/a.txt',state:'missing'}]});await settle();tree=v.render();
  assert.match(text(tree),/材料.txt/);assert.match(text(tree),/重新添加/);assert.equal(button(tree,'发送').props.disabled,true);v.cleanup();
});
test('late attachment validation cannot overwrite a different scope or newer attachment set',async()=>{
  const v=setup();v.store.update('scope1','chat1',{attachments:[{id:'a',name:'A.txt',path:'outputs/inbox/chat1/a.txt'}]});v.render();assert.equal(v.requests.length,1);
  v.store.update('scope2','chat2',{text:'new scope',attachments:[{id:'b',name:'B.txt',path:'outputs/inbox/chat2/b.txt'}]});v.render({draftScopeKey:'scope2',session:{id:'chat2',messages:[]}});v.render();
  v.requests[0].resolve({version:1,session_id:'chat1',attachments:[{id:'a',name:'A.txt',path:'outputs/inbox/chat1/a.txt',state:'missing'}]});await settle();const tree=v.render();
  assert.equal(nodes(tree,'textarea')[0].props.value,'new scope');assert.match(text(tree),/B.txt/);assert.doesNotMatch(text(tree),/A.txt/);v.cleanup();
});
test('composer-only shares the input contract and persistent label without hero text',()=>{
  const v=setup();const tree=v.render({composerOnly:true});assert.equal(nodes(tree,'textarea').length,1);assert.equal(nodes(tree,'h1').length,0);assert.equal(nodes(tree,'textarea')[0].props.placeholder,'写下创作要求…');
  assert.ok(nodes(tree,'label').some(node=>node.props.htmlFor===nodes(tree,'textarea')[0].props.id));assert.doesNotMatch(text(tree),/一站式|Enter 发送|API 直连/);v.cleanup();
});
test('Chinese composition confirmation never submits a request',()=>{
  const v=setup();let tree=v.render();ready(tree);tree=v.render();nodes(tree,'textarea')[0].props.onChange({target:{value:'中文'}});tree=v.render();
  nodes(tree,'textarea')[0].props.onKeyDown({key:'Enter',shiftKey:false,nativeEvent:{isComposing:true},preventDefault(){throw Error('IME intercepted');}});assert.equal(v.sent.length,0);v.cleanup();
});
test('an old-scope upload neither blocks nor adds material to the next scope',async()=>{
  const v=setup();v.deferUpload();let tree=v.render();const upload=nodes(tree,'input').find(node=>node.props.type==='file');
  upload.props.onChange({target:{files:[{name:'old.txt',size:1}],value:'old.txt'}});tree=v.render();assert.equal(button(tree,'添加素材').props.disabled,true);
  v.render({draftScopeKey:'scope2',session:{id:'chat2',messages:[]}});tree=v.render();assert.equal(button(tree,'添加素材').props.disabled,false);
  v.finishUpload([{id:'old',name:'old.txt',path:'outputs/inbox/chat1/old.txt'}]);await settle();tree=v.render();assert.doesNotMatch(text(tree),/old.txt/);v.cleanup();
});
test('rapid duplicate invocations of one send handler produce only one submission',()=>{
 const v=setup();let tree=v.render();ready(tree);tree=v.render();nodes(tree,'textarea')[0].props.onChange({target:{value:'once'}});tree=v.render();const send=button(tree,'发送').props.onClick;
 send();send();assert.equal(v.sent.length,1);v.cleanup();
});
