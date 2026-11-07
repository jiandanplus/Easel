import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import {runInNewContext} from 'node:vm';
import {test} from 'node:test';
const require=createRequire(new URL('../../web/frontend/package.json',import.meta.url)),ts=require('typescript');
const catalog={source_revision:'verified-source',expires_in:120,revision:'lease-1',runtime:'pi',service_source:'subscription',subscription_backend:'central',selected_model:'a',switchable:true,ready:true,reason:'',models:[{id:'a',label:'Default Model',reasoning:{status:'supported',efforts:['high']}},{id:'b',label:'Chosen Model',reasoning:{status:'supported',efforts:['high']}}]};
const workbench={version:1,build_id:'a'.repeat(64),workspace_scope:'b'.repeat(64),source_scope:'same-account'};
const text=node=>node==null||typeof node==='boolean'?'':typeof node==='object'?(node.children||[]).map(text).join(''):String(node);
const nodes=(node,predicate)=>!node||typeof node!=='object'?[]:[...(predicate(node)?[node]:[]),...(node.children||[]).flatMap(child=>nodes(child,predicate))];
const settle=()=>new Promise(resolve=>setImmediate(resolve));

// Mount actual ChatPage + ComposerModelPicker together, retaining hook state at
// each React tree position and cleaning it when hero/page changes remount them.
// Unlike a shallow JSX test, child readiness callbacks run through the real
// ChatPage state before the rendered Send button is inspected.
function mount(startPage) {
 const frames=new Map(),modules=new Map(),requests=[],sent=[],values=new Map(),timers=new Map(),remembered=new Map(),commits=[];
 const fixture={page:startPage,session:{id:'same-session',messages:[],sourceScope:workbench.source_scope},stream:undefined,workbench,reconnects:0,workbenchChecking:false,workbenchError:false,modelRefreshKey:0};
 let current,dirty=false,tree,queue=[],visited,timerId=0,uuid=0;
 const React={Fragment:'fragment',createElement:(type,props,...children)=>({type,props:props||{},children:children.flat(Infinity)}),
  useState(initial){const frame=current,index=frame.cursor++;if(!(index in frame.hooks))frame.hooks[index]=typeof initial==='function'?initial():initial;return[frame.hooks[index],value=>{const next=typeof value==='function'?value(frame.hooks[index]):value;if(!Object.is(next,frame.hooks[index])){frame.hooks[index]=next;dirty=true;}}];},
  useRef(initial){const frame=current,index=frame.cursor++;return frame.hooks[index]||=( {current:initial} );},
  useCallback(fn,deps){const frame=current,index=frame.cursor++,old=frame.hooks[index];if(!old||deps.some((v,i)=>!Object.is(v,old.deps[i])))frame.hooks[index]={deps,fn};return frame.hooks[index].fn;},
  useEffect(fn,deps){const frame=current,index=frame.cursor++,old=frame.hooks[index];if(!old||deps.some((v,i)=>!Object.is(v,old.deps[i])))queue.push(()=>{old?.cleanup?.();frame.hooks[index]={deps,cleanup:fn()};});},
 };
 const window={addEventListener(){},removeEventListener(){},innerWidth:1000,innerHeight:800};
 const api={fetchChatModels(signal,sessionId,refresh){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});requests.push({signal,sessionId,refresh,resolve,reject});return promise;},validateDraftAttachments:async(sessionId,attachments)=>({version:1,session_id:sessionId,attachments:attachments.map(item=>({...item,state:'available'}))})};
 const realComponents=new Set(['ChatPage','ComposerModelPicker']);
 function module(name){if(modules.has(name))return modules.get(name);const exports={};modules.set(name,exports);const component=realComponents.has(name);
  const code=ts.transpileModule(readFileSync(new URL(`../../web/frontend/src/${component?'components':'lib'}/${name}.${component?'tsx':'ts'}`,import.meta.url),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.React}}).outputText;
  runInNewContext(code,{exports,require:n=>n==='react'?React:n==='react-dom'?{createPortal:node=>node}:n.endsWith('/api')?api: ['chatModels','useComposerDraft','composerDrafts','workbenchReadiness','taskLifecycle','ComposerModelPicker'].some(value=>n.endsWith('/'+value))?module(n.split('/').at(-1)):{default:()=>null},React,window,document:{body:{},addEventListener(){},removeEventListener(){}},navigator:{clipboard:{}},AbortController,Date,Math,Object,Number,Map,Set,JSON,Error,crypto:{randomUUID:()=>`new-turn-${++uuid}`},fetch:async()=>({json:async()=>({max_mb:50})}),requestAnimationFrame:fn=>fn(),setTimeout(fn,delay){const id=++timerId;timers.set(id,{fn,delay});return id;},clearTimeout:id=>timers.delete(id)});return exports;
 }
 const store=module('composerDrafts').createComposerDraftStore({getItem:key=>values.get(key)??null,setItem:(key,value)=>values.set(key,value)});
 const ChatPage=module('ChatPage').default;
 function renderNode(node,path){if(node==null||typeof node==='boolean'||typeof node!=='object')return node;if(typeof node.type==='function'){
  const id=`${path}:${node.type.name}:${node.props.key||''}`;visited.add(id);let frame=frames.get(id);if(!frame){frame={hooks:[],cursor:0};frames.set(id,frame);}frame.cursor=0;const parent=current;current=frame;const rendered=node.type(node.props);current=parent;return renderNode(rendered,`${id}/render`);
 }return{...node,children:node.children.map((child,index)=>renderNode(child,`${path}/${node.type}[${index}]`))};}
 function render(){let count=0;do{assert.ok(count++<30,'render effect loop');dirty=false;visited=new Set();queue=[];
  const memory=remembered.get(fixture.session.id);
  const props={apiDirect:true,composerOnly:fixture.page==='dashboard',session:fixture.session,stream:fixture.stream,workbench:fixture.workbench,workbenchChecking:fixture.workbenchChecking,workbenchError:fixture.workbenchError,modelRefreshKey:fixture.modelRefreshKey,onReconnect(){fixture.reconnects++;fixture.workbenchChecking=true;dirty=true;},expectedSourceScope:fixture.session.sourceScope,draftScopeKey:'scope',draftStore:store,initialModelSelection:memory?.selection,initialModelSourceRevision:memory?.sourceRevision,onRememberModel(selection,sourceRevision){if(selection)remembered.set(fixture.session.id,{selection,sourceRevision});else remembered.delete(fixture.session.id);},onSend(text,attachments,selection,submission){sent.push({text,selection});store.acknowledge('scope',fixture.session.id,submission.turnId,submission.revision);fixture.page='chat';fixture.session={...fixture.session,messages:[...fixture.session.messages,{role:'user',content:text}]};fixture.stream={content:'',thinking:'',activity:'',questions:[],phase:'running'};dirty=true;},onStop(){},onResend(){}};
  const page=React.createElement(ChatPage,props);tree=renderNode(React.createElement(fixture.page==='dashboard'?'main':'section',{},page),'root');commits.push(tree);
  for(const[id,frame]of frames)if(!visited.has(id)){for(const hook of frame.hooks)hook?.cleanup?.();frames.delete(id);}
  for(const effect of queue)effect();
 }while(dirty);return tree;}
 return{requests,sent,fixture,remembered,commits,store,render,refreshLease(){const entry=[...timers].find(([,value])=>value.delay>100000 && value.delay<=115000);assert.ok(entry);timers.delete(entry[0]);entry[1].fn();},flushDraft(){for(const[id,value]of timers)if(value.delay<1000){timers.delete(id);value.fn();}},complete(){fixture.stream=undefined;fixture.session={...fixture.session,messages:[...fixture.session.messages,{role:'assistant',content:'completed',taskState:'completed'}]};return render();},cleanup(){for(const frame of frames.values())for(const hook of frame.hooks)hook?.cleanup?.();}};
}
const button=(tree,label)=>nodes(tree,n=>n.type==='button'&&(n.props['aria-label']===label||text(n)===label))[0];
const input=tree=>nodes(tree,n=>n.type==='textarea')[0];
for(const page of ['chat','dashboard'])test(`${page} composer keeps the verified nondefault model through remount, done and new lease`,async()=>{
 const v=mount(page);let tree=v.render();assert.equal(v.requests.length,1);v.requests[0].resolve(catalog);await settle();tree=v.render();
 button(tree,'模型与推理强度：Default Model').props.onClick();tree=v.render();button(tree,'模型Default Model›').props.onClick();tree=v.render();
 nodes(tree,n=>n.type==='button'&&n.props.className==='composer-model-option'&&text(n).startsWith('Chosen Model'))[0].props.onClick();tree=v.render();
 button(tree,'推理强度默认›').props.onClick();tree=v.render();button(tree,'高').props.onClick();tree=v.render();
 input(tree).props.onChange({target:{value:'first synthetic message'}});tree=v.render();assert.equal(button(tree,'发送').props.disabled,false);button(tree,'发送').props.onClick();tree=v.render();assert.equal(v.sent.length,1);assert.equal(v.sent[0].selection.model,'b');
 v.complete();assert.ok(v.requests.length>=2);v.requests.at(-1).resolve({...catalog,revision:'lease-2'});await settle();tree=v.render();
 input(tree).props.onChange({target:{value:'next unsent draft'}});tree=v.render();v.flushDraft();tree=v.render();
 assert.match(text(tree),/Chosen Model/);assert.doesNotMatch(text(tree),/请选择模型/);assert.equal(button(tree,'发送').props.disabled,false);assert.equal(v.sent.length,1,'completion and typing never submit again');
 assert.equal(v.remembered.get('same-session').selection.catalog_revision,'lease-2');assert.equal(v.remembered.get('same-session').selection.reasoning_effort,'high');v.cleanup();
});


for(const invalidation of ['source','model-removed','effort-removed','unavailable','contract-source','contract-version'])test(`${invalidation} during completion keeps the mounted next draft blocked`,async()=>{
 const v=mount('chat');let tree=v.render();assert.equal(v.requests[0].refresh,false,'initial load retains existing cache policy');v.requests[0].resolve(catalog);await settle();tree=v.render();
 button(tree,'模型与推理强度：Default Model').props.onClick();tree=v.render();button(tree,'推理强度默认›').props.onClick();tree=v.render();button(tree,'高').props.onClick();tree=v.render();
 input(tree).props.onChange({target:{value:'first'}});tree=v.render();button(tree,'发送').props.onClick();tree=v.render();v.complete();
 const next={...catalog,revision:'lease-2'};
 if(invalidation==='source')next.source_revision='another-authority';
 if(invalidation==='model-removed')next.models=catalog.models.filter(item=>item.id!=='a');
 if(invalidation==='effort-removed')next.models=catalog.models.map(item=>({...item,reasoning:{status:'supported',efforts:['low']}}));
 if(invalidation==='unavailable'){next.ready=false;next.reason='当前渠道没有可用模型';}
 if(invalidation==='contract-source')v.fixture.workbench={...workbench,source_scope:'new-account'};
 if(invalidation==='contract-version')v.fixture.workbench={...workbench,version:2};
 v.requests.at(-1).resolve(next);await settle();tree=v.render();input(tree).props.onChange({target:{value:'next unsent draft'}});tree=v.render();v.flushDraft();tree=v.render();
 assert.equal(button(tree,'发送').props.disabled,true);assert.equal(v.sent.length,1);assert.equal(v.requests.every(request=>request.sessionId==='same-session'),true);
 if(invalidation==='source'){assert.equal(v.remembered.has('same-session'),false);assert.doesNotMatch(text(tree),/Default Model/);}
 if(invalidation==='unavailable')assert.doesNotMatch(text(tree),/Default Model/);
 v.cleanup();
});


for(const boundary of ['session','source','session-and-source'])test(`reused Dashboard composer invalidates the old lease immediately on ${boundary} change`,async()=>{
 const v=mount('dashboard');let tree=v.render();v.requests[0].resolve(catalog);await settle();tree=v.render();
 input(tree).props.onChange({target:{value:'original unsent draft'}});tree=v.render();assert.equal(button(tree,'发送').props.disabled,false);
 if(boundary!=='session'){v.fixture.workbench={...workbench,source_scope:'new-account'};}
 v.fixture.session={id:boundary==='source'?'same-session':'new-session',messages:[],sourceScope:boundary==='session'?workbench.source_scope:'new-account'};
 v.store.update('scope',v.fixture.session.id,{text:'new preloaded unsent draft'});const firstCommit=v.commits.length;
 tree=v.render();assert.equal(button(v.commits[firstCommit],'发送').props.disabled,true,'the very first render must reject the old target before passive effects');assert.doesNotMatch(text(v.commits[firstCommit]),/Default Model/);assert.equal(v.requests.length,2);assert.equal(v.requests[1].sessionId,v.fixture.session.id);
 input(tree).props.onChange({target:{value:'new unsent draft'}});tree=v.render();v.flushDraft();tree=v.render();
 assert.equal(button(tree,'发送').props.disabled,true,'previous target lease cannot enable Send while new GET is pending');assert.equal(v.sent.length,0);assert.doesNotMatch(text(tree),/Default Model/);v.cleanup();
});

test('late old-target directory cannot reestablish a lease after Dashboard changes scope',async()=>{
 const v=mount('dashboard');let tree=v.render();
 v.fixture.workbench={...workbench,source_scope:'new-account'};v.fixture.session={id:'new-session',messages:[],sourceScope:'new-account'};
 tree=v.render();assert.equal(v.requests[0].signal.aborted,true);assert.equal(v.requests.length,2);
 v.requests[0].resolve(catalog);await settle();tree=v.render();
 input(tree).props.onChange({target:{value:'new unsent draft'}});tree=v.render();v.flushDraft();tree=v.render();
 assert.equal(button(tree,'发送').props.disabled,true);assert.doesNotMatch(text(tree),/Default Model/);assert.equal(v.remembered.has('new-session'),false);assert.equal(v.sent.length,0);v.cleanup();
});


test('a pending old lease refresh cannot renew or overwrite the new Dashboard target',async()=>{
 const v=mount('dashboard');let tree=v.render();v.requests[0].resolve(catalog);await settle();tree=v.render();input(tree).props.onChange({target:{value:'old draft'}});tree=v.render();
 v.refreshLease();tree=v.render();assert.equal(v.requests.length,2);assert.equal(button(tree,'发送').props.disabled,false,'same-target unexpired lease remains usable during refresh');
 const oldRefresh=v.requests[1];v.fixture.workbench={...workbench,source_scope:'new-account'};v.fixture.session={id:'new-session',messages:[],sourceScope:'new-account'};v.store.update('scope','new-session',{text:'new draft'});
 tree=v.render();assert.equal(oldRefresh.signal.aborted,true);assert.equal(v.requests.length,3);assert.equal(button(tree,'发送').props.disabled,true);
 oldRefresh.resolve({...catalog,revision:'late-old-lease'});await settle();tree=v.render();assert.equal(button(tree,'发送').props.disabled,true);assert.doesNotMatch(text(tree),/Default Model/);
 const next={...catalog,revision:'new-target-lease',source_revision:'new-target-source',selected_model:'b'};v.requests[2].resolve(next);await settle();tree=v.render();
 assert.equal(button(tree,'发送').props.disabled,false);assert.match(text(tree),/Chosen Model/);assert.doesNotMatch(text(tree),/Default Model/);assert.equal(v.sent.length,0);assert.equal(v.remembered.get('new-session').selection.catalog_revision,'new-target-lease');v.cleanup();
});


async function reconnectFixture(page, failure = 'unavailable') {
 const v=mount(page);v.fixture.modelRefreshKey=1;
 v.store.update('scope','same-session',{text:'preserved next draft',attachments:[{id:'material-one',name:'reference.txt',path:'uploads/reference.txt',size:7,available:true}]});
 let tree=v.render();v.requests[0].resolve(catalog);await settle();tree=v.render();
 button(tree,'模型与推理强度：Default Model').props.onClick();tree=v.render();button(tree,'模型Default Model›').props.onClick();tree=v.render();
 nodes(tree,n=>n.type==='button'&&n.props.className==='composer-model-option'&&text(n).startsWith('Chosen Model'))[0].props.onClick();tree=v.render();
 button(tree,'推理强度默认›').props.onClick();tree=v.render();button(tree,'高').props.onClick();tree=v.render();
 v.flushDraft();v.refreshLease();tree=v.render();
 if(failure==='network')v.requests[1].reject(Error('synthetic catalogue unavailable'));
 else v.requests[1].resolve({...catalog,ready:false,reason:'central_unavailable',models:[]});
 await settle();tree=v.render();assert.equal(button(tree,'发送').props.disabled,true);assert.ok(button(tree,'重新连接'));
 return {v,tree,draft:v.store.load('scope','same-session')};
}
for(const page of ['chat','dashboard'])test(`${page} composer reconnect force-reads catalog and restores the same model/effort without changing draft or attachments`,async()=>{
 const {v,tree:failed,draft}=await reconnectFixture(page);button(failed,'重新连接').props.onClick();let tree=v.render();
 assert.equal(v.fixture.reconnects,1);assert.equal(v.requests.length,3,'composer reconnect must request catalog as well as workbench');
 assert.equal(v.requests[2].refresh,true,'explicit reconnect must bypass the server catalog cache');assert.equal(v.requests[2].sessionId,'same-session');
 assert.equal(button(tree,'发送').props.disabled,true);v.fixture.workbenchChecking=false;tree=v.render();assert.equal(button(tree,'发送').props.disabled,true,'status readiness cannot replace a fresh directory');
 v.requests[2].resolve({...catalog,revision:'reconnected-lease'});await settle();tree=v.render();
 assert.equal(button(tree,'发送').props.disabled,false);assert.match(text(tree),/Chosen Model/);assert.match(text(tree),/高/);
 assert.equal(v.remembered.get('same-session').selection.model,'b');assert.equal(v.remembered.get('same-session').selection.reasoning_effort,'high');
 assert.equal(input(tree).props.value,draft.text);assert.deepEqual(v.store.load('scope','same-session').attachments,draft.attachments);assert.equal(v.sent.length,0);v.cleanup();
});
for(const removed of ['model','effort'])test(`reconnect fails closed if fresh catalog removes remembered ${removed}`,async()=>{
 const {v,tree}=await reconnectFixture('chat');button(tree,'重新连接').props.onClick();v.render();assert.equal(v.requests.length,3);
 const next={...catalog,revision:'reconnected-lease',models:removed==='model'?catalog.models.filter(item=>item.id!=='b'):catalog.models.map(item=>item.id==='b'?{...item,reasoning:{status:'supported',efforts:['low']}}:item)};
 v.fixture.workbenchChecking=false;v.requests[2].resolve(next);await settle();const after=v.render();assert.equal(button(after,'发送').props.disabled,true);assert.equal(v.sent.length,0);assert.equal(input(after).props.value,'preserved next draft');v.cleanup();
});
test('a reconnect response from an old source cannot restore readiness or move the draft',async()=>{
 const {v,tree}=await reconnectFixture('dashboard');button(tree,'重新连接').props.onClick();v.render();assert.equal(v.requests.length,3);const old=v.requests[2];
 v.fixture.workbench={...workbench,source_scope:'other-account'};v.fixture.workbenchChecking=false;v.render();assert.equal(old.signal.aborted,true);
 old.resolve({...catalog,revision:'late-reconnect'});await settle();const after=v.render();assert.equal(button(after,'发送').props.disabled,true);assert.equal(input(after).props.value,'preserved next draft');assert.equal(v.sent.length,0);v.cleanup();
});
test('external refresh key decrease cannot cancel a simultaneous local reconnect',async()=>{
 const {v,tree}=await reconnectFixture('chat');v.fixture.modelRefreshKey=0;button(tree,'重新连接').props.onClick();v.render();
 assert.equal(v.requests.length,3);assert.equal(v.requests[2].refresh,true);assert.equal(v.sent.length,0);v.cleanup();
});
test('reconnect after a network catalogue failure never temporarily reuses the failed old lease',async()=>{
 const {v,tree}=await reconnectFixture('chat','network');button(tree,'重新连接').props.onClick();v.render();assert.equal(v.requests.length,3);
 v.fixture.workbenchChecking=false;const after=v.render();assert.equal(button(after,'发送').props.disabled,true,'failed lease stays invalid until the fresh request returns');assert.equal(v.sent.length,0);v.cleanup();
});

test('a pending reconnect is aborted on session change and cannot move the old draft',async()=>{
 const {v,tree,draft}=await reconnectFixture('dashboard');button(tree,'重新连接').props.onClick();v.render();assert.equal(v.requests.length,3);const old=v.requests[2];
 v.fixture.session={id:'another-session',messages:[],sourceScope:workbench.source_scope};v.fixture.workbenchChecking=false;
 let after=v.render();assert.equal(old.signal.aborted,true);assert.equal(v.requests.at(-1).sessionId,'another-session');assert.equal(v.requests.at(-1).refresh,false,'new session retains its initial load policy');
 old.resolve({...catalog,revision:'late-old-session'});await settle();after=v.render();
 assert.equal(button(after,'发送').props.disabled,true);assert.equal(input(after).props.value,'');assert.equal(v.store.load('scope','same-session').text,draft.text);assert.deepEqual(v.store.load('scope','same-session').attachments,draft.attachments);assert.equal(v.remembered.has('another-session'),false);assert.equal(v.sent.length,0);v.cleanup();
});
test('unmount aborts reconnect and a late directory cannot publish a new remembered lease',async()=>{
 const {v,tree}=await reconnectFixture('chat');button(tree,'重新连接').props.onClick();v.render();assert.equal(v.requests.length,3);
 const pending=v.requests[2],prior=JSON.stringify([...v.remembered]),commits=v.commits.length;v.cleanup();assert.equal(pending.signal.aborted,true);
 pending.resolve({...catalog,revision:'after-unmount'});await settle();assert.equal(JSON.stringify([...v.remembered]),prior);assert.equal(v.commits.length,commits);assert.equal(v.sent.length,0);
});
