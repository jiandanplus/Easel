import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');
const settle = () => new Promise(resolve => setImmediate(resolve));
const out = { configured: true, url: 'https://accounts.example.invalid', authenticated: false, account: null, scope: null, login: { pending: false } };
const inside = (account = { email: 'member@example.invalid', id: 'member-id' }) => ({ ...out, authenticated: true, account, scope: { user_id: 'member-id' } });
const pending = { ...out, login: { pending: true, expires_at: Date.now()/1000+300 } };
const auth = () => ({ authorization_url: 'https://accounts.example.invalid/oidc/easel/transaction', expires_at: Date.now()/1000+300 });
const deferred = () => { let resolve, reject; const promise = new Promise((yes,no) => { resolve=yes; reject=no; }); return {promise,resolve,reject}; };
const nodes = (tree,type) => !tree || typeof tree!=='object' ? [] : [...(tree.type===type?[tree]:[]), ...(tree.children||[]).flat(Infinity).flatMap(child=>nodes(child,type))];
const text = tree => typeof tree==='string'?tree:tree&&typeof tree==='object'?(tree.children||[]).flat(Infinity).map(text).join(''):'';
const avatar = tree => nodes(tree,'button').find(n=>n.props.className?.split(' ').includes('sidebar-account'));
const button = (tree,label) => nodes(tree,'button').find(n=>text(n)===label);
const card = tree => nodes(tree,'section').find(n=>n.props.role==='dialog');

function setup({blocked=false,height=800,rect={left:18,right:62,top:710,bottom:754}}={}) {
 const source=readFileSync(new URL('../../web/frontend/src/components/SidebarAccount.tsx',import.meta.url),'utf8');
 const code=ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.React}}).outputText;
 const states=[],refs=[],effects=[],queued=[],requests=[],starts=[],cancels=[],logouts=[],windows=[],timers=new Map();
 const docEvents=new Map(),winEvents=new Map(); let cursor=0,refCursor=0,effectCursor=0,writes=0,timerId=0,accountNav=0,settingsNav=0,focusCount=0,now=0;
 const add=(map,name,fn)=>{if(!map.has(name))map.set(name,new Set());map.get(name).add(fn);};
 const remove=(map,name,fn)=>map.get(name)?.delete(fn);
 const React={Fragment:'fragment',createElement(type,props,...children){
   const node={type,props:props||{},children:children.flat(Infinity)};
   if(props?.ref&&typeof props.ref==='object'){const trigger=type==='button'&&props.className?.split(' ').includes('sidebar-account');props.ref.current={focus(){if(trigger)focusCount++;},getBoundingClientRect:()=>rect,querySelector:()=>null,contains:target=>target?.area===(trigger?'trigger':'card')};}
   return node;
  },
  useState(initial){const i=cursor++;if(!(i in states))states[i]=typeof initial==='function'?initial():initial;return[states[i],value=>{writes++;states[i]=typeof value==='function'?value(states[i]):value;}];},
  useRef(initial){const i=refCursor++;return refs[i]||=( {current:initial});},
  useEffect(fn,deps){const i=effectCursor++,old=effects[i];if(!old||!deps||deps.some((v,n)=>!Object.is(v,old.deps[n])))queued.push(()=>{old?.cleanup?.();effects[i]={deps,cleanup:fn()};});},
 };
 const api={centralStatus(signal){const d={...deferred(),signal};requests.push(d);return d.promise;},centralStartLogin(){const d=deferred();starts.push(d);return d.promise;},centralCancelLogin(){const d=deferred();cancels.push(d);return d.promise;},centralLogout(){const d=deferred();logouts.push(d);return d.promise;}};
 const win={innerWidth:1000,innerHeight:height,addEventListener:(n,f)=>add(winEvents,n,f),removeEventListener:(n,f)=>remove(winEvents,n,f),open(){if(blocked){windows.push(null);return null;}const h={closed:false,opener:{},navigations:[],focused:0,location:{replace(url){h.navigations.push(url);}},focus(){h.focused++;},close(){h.closed=true;}};windows.push(h);return h;}};
 const document={body:{},addEventListener:(n,f)=>add(docEvents,n,f),removeEventListener:(n,f)=>remove(docEvents,n,f)};
 const exports={};
 runInNewContext(code,{exports,require:name=>name==='react'?React:name==='react-dom'?{createPortal:child=>child}:name.endsWith('/api')?api:{},React,window:win,document,AbortController,URL,Date,Number,Error,Promise,setTimeout:(fn,delay=0)=>{const i=++timerId;timers.set(i,{fn,at:now+delay});return i;},clearTimeout:i=>timers.delete(i)});
 const render=(props={})=>{cursor=0;refCursor=0;effectCursor=0;const tree=exports.default({onOpenAccount:()=>accountNav++,onOpenSettings:()=>settingsNav++,settingsOpen:false,...props});queued.splice(0).forEach(fn=>fn());return tree;};
 return{render,requests,starts,cancels,logouts,windows,timers,writes:()=>writes,focusCount:()=>focusCount,accountNav:()=>accountNav,settingsNav:()=>settingsNav,
  async load(value){render();requests[0].resolve(value);await settle();return render();},
  focus(){for(const fn of [...(winEvents.get('focus')||[])])fn();},
  emit(name,event={}){for(const fn of [...(docEvents.get(name)||[])])fn({preventDefault(){},...event});},
  async tick(ms=1000){
   const target=now+ms;
   while(true){
    const next=[...timers.entries()].filter(([,timer])=>timer.at<=target).sort((a,b)=>a[1].at-b[1].at||a[0]-b[0])[0];
    if(!next)break;
    const [id,timer]=next;now=timer.at;timers.delete(id);timer.fn();await settle();
   }
   now=target;await settle();
  },
  unmount(){effects.forEach(e=>e?.cleanup?.());},
  listenerCount:()=>[...docEvents.values(),...winEvents.values()].reduce((n,s)=>n+s.size,0),
 };
}

test('loading avatar is disabled; retained unverified scope never starts login',async()=>{
 const v=setup();assert.equal(avatar(v.render()).props.disabled,true);
 v.requests[0].resolve({...out,account:{email:'stale@example.invalid'},scope:{user_id:'stale'}});await settle();avatar(v.render()).props.onClick();
 assert.ok(card(v.render()));assert.equal(v.starts.length,0);assert.doesNotMatch(text(v.render()),/stale@example/);
});
test('authenticated avatar opens card and delegates existing settings/account routes',async()=>{
 const v=setup();let t=await v.load(inside());assert.equal(card(t),undefined);avatar(t).props.onClick();t=v.render();
 assert.match(text(card(t)),/member@example.invalid/);assert.match(text(card(t)),/已登录/);assert.equal(v.starts.length,0);
 button(t,'设置').props.onClick();assert.equal(v.settingsNav(),1);assert.equal(card(v.render()),undefined);
 avatar(v.render()).props.onClick();button(v.render(),'账号与订阅').props.onClick();assert.equal(v.accountNav(),1);
});
test('real email/id/fallback are displayed without invented paid membership',async()=>{
 for(const [account,label] of [[{email:'mail@example.invalid',id:'id'},'mail@example.invalid'],[{id:'short-id'},'short-id'],[{},'统一账号']]){
  const v=setup();avatar(await v.load(inside(account))).props.onClick();assert.match(text(card(v.render())),new RegExp(label));assert.doesNotMatch(text(card(v.render())),/Pro|剩余.*%/);v.unmount();
 }
});
test('signed-out click preopens one protected blank window and locks duplicate start',async()=>{
 const v=setup(),t=await v.load(out);avatar(t).props.onClick();avatar(t).props.onClick();
 assert.equal(v.windows.length,1);assert.equal(v.starts.length,1);assert.equal(v.windows[0].opener,null);assert.equal(v.windows[0].navigations.length,0);
 v.starts[0].resolve(auth());await settle();assert.deepEqual(v.windows[0].navigations,[auth().authorization_url]);assert.equal(v.timers.size,1);
});
test('blocked popup keeps original transaction and renders an explicit safe recovery link',async()=>{
 const v=setup({blocked:true});avatar(await v.load(out)).props.onClick();v.starts[0].resolve(auth());await settle();const t=v.render(),a=nodes(t,'a')[0];
 assert.equal(a.props.href,auth().authorization_url);assert.equal(a.props.target,'_blank');assert.match(a.props.rel,/noopener/);assert.match(text(t),/未能自动|阻止|手动/);assert.equal(v.starts.length,1);
});
test('continuing owned pending login focuses original window without another start',async()=>{
 const v=setup();avatar(await v.load(out)).props.onClick();v.starts[0].resolve(auth());await settle();avatar(v.render()).props.onClick();
 assert.equal(v.starts.length,1);assert.equal(v.windows.length,1);assert.equal(v.windows[0].focused,1);
});
test('pre-existing pending without URL is not silently restarted',async()=>{
 const v=setup();avatar(await v.load(pending)).props.onClick();const t=v.render();assert.equal(v.starts.length,0);assert.ok(button(t,'取消登录'));assert.equal(nodes(t,'a').length,0);assert.equal(v.timers.size,1);
});
test('unsafe, wrong-origin and expired destinations never navigate and close blank window',async()=>{
 for(const result of [{...auth(),authorization_url:'javascript:alert(1)'},{...auth(),authorization_url:'https://other.example.invalid/login'},{...auth(),expires_at:0}]){
  const v=setup();avatar(await v.load(out)).props.onClick();v.starts[0].resolve(result);await settle();
  assert.equal(v.windows[0].closed,true);assert.equal(v.windows[0].navigations.length,0);assert.equal(nodes(v.render(),'a').length,0);assert.match(text(v.render()),/登录暂不可用|登录地址无效|登录未能发起/);v.unmount();
 }
});
test('start failure closes only owned blank window and hides raw server details',async()=>{
 const v=setup();avatar(await v.load(out)).props.onClick();v.starts[0].reject(new Error('PRIVATE_PROVIDER_KEY'));await settle();
 assert.equal(v.windows[0].closed,true);assert.doesNotMatch(text(v.render()),/PRIVATE_PROVIDER_KEY/);assert.equal(v.starts.length,1);
});
test('lost start response requires status lookup and preserves a discovered pending transaction',async()=>{
 const v=setup();avatar(await v.load(out)).props.onClick();v.starts[0].reject(new Error('TRANSPORT_UNCERTAIN'));await settle();
 assert.equal(avatar(v.render()).props['aria-label'],'账号状态待确认');avatar(v.render()).props.onClick();avatar(v.render()).props.onClick();
 assert.equal(v.starts.length,1);button(v.render(),'重试账号状态').props.onClick();assert.equal(v.requests.length,2);
 v.requests[1].resolve(pending);await settle();assert.ok(button(v.render(),'取消登录'));assert.equal(v.starts.length,1);assert.equal(nodes(v.render(),'a').length,0);
});
test('unknown/unconfigured status offers settings and retry without automatic start/logout',async()=>{
 for(const s of [{...inside(),authenticated:false,error:{code:'platform_unavailable',message:'PRIVATE_LOG'}},{...inside(),error:{code:'platform_unavailable',message:'PRIVATE_LOG'}},{...out,configured:false,error:{code:'not_configured',message:'PRIVATE_CONFIG'}}]){
  const v=setup();avatar(await v.load(s)).props.onClick();const t=v.render();assert.ok(button(t,'设置'));assert.ok(button(t,'重试账号状态'));assert.equal(v.starts.length,0);assert.equal(v.logouts.length,0);assert.doesNotMatch(text(t),/member@example|PRIVATE_/);v.unmount();
 }
});
test('focus refresh hides stale identity and aborted late response cannot replace latest signed-out state',async()=>{
 const v=setup();avatar(await v.load(inside())).props.onClick();assert.match(text(v.render()),/member@example/);
 v.focus();assert.doesNotMatch(text(v.render()),/member@example/);v.focus();assert.equal(v.requests[1].signal.aborted,true);
 v.requests[2].resolve(out);await settle();v.requests[1].resolve(inside({email:'obsolete@example.invalid'}));await settle();assert.doesNotMatch(text(v.render()),/obsolete@example|member@example/);assert.equal(v.starts.length,0);
});
test('pending polling is single-flight and stops on verified completion',async()=>{
 const v=setup();await v.load(pending);assert.equal(v.timers.size,1);await v.tick();assert.equal(v.requests.length,2);assert.equal(v.timers.size,1);
 await v.tick();assert.equal(v.requests.length,2);v.requests[1].resolve(inside());await settle();avatar(v.render()).props.onClick();assert.match(text(card(v.render())),/已登录/);assert.equal(v.timers.size,0);
});
test('cancel invalidates in-flight poll and rejects late authenticated result',async()=>{
 const v=setup();avatar(await v.load(pending)).props.onClick();await v.tick();button(v.render(),'取消登录').props.onClick();assert.equal(v.requests[1].signal.aborted,true);
 v.cancels[0].resolve(out);await settle();v.requests[1].resolve(inside({email:'late@example.invalid'}));await settle();assert.equal(v.timers.size,0);assert.doesNotMatch(text(v.render()),/late@example/);assert.equal(v.starts.length,0);
});
test('failed cancellation retains pending and resumes lookup without restarting login',async()=>{
 const v=setup();avatar(await v.load(pending)).props.onClick();button(v.render(),'取消登录').props.onClick();v.cancels[0].reject(new Error('PRIVATE_CANCEL'));await settle();
 assert.match(text(v.render()),/取消.*未完成|取消.*失败|取消暂不可用/);assert.doesNotMatch(text(v.render()),/PRIVATE_CANCEL/);assert.equal(v.starts.length,0);assert.equal(v.timers.size,1);await v.tick();assert.equal(v.requests.length,2);
});
test('logout failure never claims success; successful logout updates avatar only after response',async()=>{
 const v=setup();avatar(await v.load(inside())).props.onClick();button(v.render(),'退出登录').props.onClick();v.logouts[0].reject(new Error('PRIVATE_LOGOUT'));await settle();
 assert.doesNotMatch(text(v.render()),/PRIVATE_LOGOUT|已退出/);assert.equal(v.starts.length,0);button(v.render(),'重试账号状态').props.onClick();v.requests.at(-1).resolve(inside());await settle();
 let t=v.render();if(!card(t))avatar(t).props.onClick();button(v.render(),'退出登录').props.onClick();v.logouts[1].resolve(out);await settle();
 assert.equal(avatar(v.render()).props['aria-label'],'登录 / 注册');assert.equal(card(v.render()),undefined);
});
test('outside pointer and Escape close card and restore avatar focus',async()=>{
 const v=setup();avatar(await v.load(inside())).props.onClick();v.render();v.emit('pointerdown',{target:{area:'outside'}});assert.equal(card(v.render()),undefined);assert.equal(v.focusCount(),1);
 avatar(v.render()).props.onClick();v.render();v.emit('keydown',{key:'Escape'});assert.equal(card(v.render()),undefined);assert.equal(v.focusCount(),2);
});
test('short viewport reserves space above the anchored bottom and scrolls inside the card',async()=>{
 for(const height of [300,400]){
  const v=setup({height,rect:{left:18,right:62,top:height-100,bottom:height-56}});avatar(await v.load(inside())).props.onClick();v.render();
  const position=card(v.render()).props.style;assert.ok(position.maxHeight>=0);assert.ok(position.bottom>=12);assert.ok(position.maxHeight+position.bottom<=height-12);v.unmount();
 }
});
test('keyboard focus leaving the card closes it without stealing the new focus',async()=>{
 const v=setup();avatar(await v.load(inside())).props.onClick();v.render();v.emit('focusin',{target:{area:'card'}});assert.ok(card(v.render()));
 v.emit('focusin',{target:{area:'outside'}});assert.equal(card(v.render()),undefined);assert.equal(v.focusCount(),0);
});
test('settings navigation closes card and refresh replaces reads with listeners cleaned up',async()=>{
 const v=setup();avatar(await v.load(inside())).props.onClick();v.render();v.render({settingsOpen:true});assert.equal(card(v.render({settingsOpen:true})),undefined);assert.equal(v.requests.length,2);
 v.render({settingsOpen:false});assert.equal(v.requests.length,3);assert.equal(v.requests[1].signal.aborted,true);v.unmount();assert.equal(v.listenerCount(),0);
});
test('unmount closes a still-blank window and ignores late start/status results',async()=>{
 const v=setup();avatar(await v.load(out)).props.onClick();v.unmount();const writes=v.writes();assert.equal(v.windows[0].closed,true);
 v.starts[0].resolve(auth());await settle();assert.equal(v.windows[0].navigations.length,0);assert.equal(v.writes(),writes);assert.equal(v.timers.size,0);assert.equal(v.listenerCount(),0);
});
test('unmount leaves an already handed-off login window open but stops owned polling',async()=>{
 const v=setup();avatar(await v.load(out)).props.onClick();v.starts[0].resolve(auth());await settle();v.render();
 assert.equal(v.windows[0].navigations.length,1);v.unmount();assert.equal(v.windows[0].closed,false);assert.equal(v.timers.size,0);assert.equal(v.listenerCount(),0);
});
test('unmount aborts its outstanding status read and ignores a late identity payload',async()=>{
 const v=setup();v.render();v.unmount();const writes=v.writes();assert.equal(v.requests[0].signal.aborted,true);
 v.requests[0].resolve(inside({email:'after-unmount@example.invalid'}));await settle();assert.equal(v.writes(),writes);assert.equal(v.listenerCount(),0);
});


test('never-settling initial status releases the disabled avatar exactly at its 30 second deadline',async()=>{
 const v=setup();assert.equal(avatar(v.render()).props.disabled,true);
 await v.tick(29_999);assert.equal(avatar(v.render()).props.disabled,true);assert.equal(v.requests[0].signal.aborted,false);
 await v.tick(1);let t=v.render();assert.equal(avatar(t).props.disabled,false);assert.equal(avatar(t).props['aria-label'],'账号状态待确认');assert.equal(avatar(t).props['aria-busy'],false);assert.equal(v.requests[0].signal.aborted,true);
 avatar(t).props.onClick();t=v.render();assert.ok(card(t));assert.ok(button(t,'重试账号状态'));button(t,'设置').props.onClick();assert.equal(v.settingsNav(),1);
 await v.tick(120_000);assert.equal(v.requests.length,1);assert.equal(v.timers.size,0);assert.equal(v.starts.length,0);assert.equal(v.cancels.length,0);assert.equal(v.logouts.length,0);
});
test('aborted status that ignores abort cannot publish late success or failure after deadline',async()=>{
 for(const outcome of ['success','failure']){
  const v=setup();v.render();await v.tick(30_000);const writes=v.writes();
  if(outcome==='success')v.requests[0].resolve(inside({email:'too-late@example.invalid'}));else v.requests[0].reject(new Error('PRIVATE_LATE'));
  await settle();assert.equal(v.writes(),writes);assert.equal(avatar(v.render()).props['aria-label'],'账号状态待确认');assert.doesNotMatch(text(v.render()),/too-late|PRIVATE_LATE/);assert.equal(v.timers.size,0);v.unmount();
 }
});
test('explicit retry after timeout owns an independent full deadline and recovers normally',async()=>{
 const v=setup();v.render();await v.tick(30_000);avatar(v.render()).props.onClick();button(v.render(),'重试账号状态').props.onClick();
 assert.equal(v.requests.length,2);assert.equal(avatar(v.render()).props.disabled,true);await v.tick(29_999);assert.equal(v.requests[1].signal.aborted,false);
 v.requests[1].resolve(inside());await settle();assert.equal(avatar(v.render()).props['aria-label'],'用户菜单');assert.equal(v.timers.size,0);
 v.requests[0].resolve(out);await settle();assert.equal(avatar(v.render()).props['aria-label'],'用户菜单');assert.equal(v.starts.length,0);
});
test('obsolete deadline cannot expire a newer focus read or clear its deadline ownership',async()=>{
 const v=setup();v.render();const obsolete=[...v.timers.values()][0];await v.tick(10_000);v.focus();assert.equal(v.requests[0].signal.aborted,true);
 const current=[...v.timers.values()][0];assert.notEqual(current,obsolete);const writes=v.writes();obsolete?.fn();await settle();assert.equal(v.writes(),writes);assert.equal(v.timers.size,1);
 await v.tick(20_000);assert.equal(avatar(v.render()).props.disabled,true);assert.equal(v.requests[1].signal.aborted,false);
 await v.tick(10_000);assert.equal(avatar(v.render()).props.disabled,false);assert.equal(v.requests[1].signal.aborted,true);assert.equal(v.timers.size,0);
});
test('completed read clears its deadline and a queued obsolete callback cannot touch later retry',async()=>{
 const v=setup();v.render();const oldDeadline=[...v.timers.values()][0];await v.tick(29_999);v.requests[0].resolve(out);await settle();
 assert.equal(avatar(v.render()).props['aria-label'],'登录 / 注册');assert.equal(v.timers.size,0);v.focus();const writes=v.writes();oldDeadline?.fn();await settle();assert.equal(v.writes(),writes);assert.equal(v.requests[1].signal.aborted,false);assert.equal(v.timers.size,1);
 await v.tick(30_000);assert.equal(avatar(v.render()).props['aria-label'],'账号状态待确认');
});
test('timeout hides retained logged-in identity and never treats retained scope as signed out',async()=>{
 const v=setup();avatar(await v.load(inside())).props.onClick();v.render();v.focus();assert.doesNotMatch(text(v.render()),/member@example/);await v.tick(30_000);
 let t=v.render();assert.equal(avatar(t).props['aria-label'],'账号状态待确认');if(!card(t))avatar(t).props.onClick();t=v.render();assert.ok(button(t,'重试账号状态'));assert.equal(button(t,'退出登录'),undefined);assert.doesNotMatch(text(t),/member@example|已登录|登录 \/ 注册/);assert.equal(v.starts.length,0);assert.equal(v.logouts.length,0);
 v.focus();v.requests.at(-1).resolve({...inside(),authenticated:false});await settle();t=v.render();if(!card(t))avatar(t).props.onClick();assert.equal(v.starts.length,0);assert.ok(button(v.render(),'重试账号状态'));
});
test('pending timeout hides stale login link and verified labels, preserves original context for explicit recovery',async()=>{
 const v=setup();avatar(await v.load(out)).props.onClick();v.starts[0].resolve(auth());await settle();v.render();await v.tick();assert.equal(v.requests.length,2);
 await v.tick(30_000);let t=v.render();assert.equal(avatar(t).props['aria-label'],'账号状态待确认');assert.equal(v.timers.size,0);assert.equal(nodes(t,'a').length,0);assert.doesNotMatch(text(t),/登录进行中|正在登录|等待登录|等待完成/);assert.ok(button(t,'重试账号状态'));assert.equal(button(t,'取消登录'),undefined);
 const focused=v.windows[0].focused;avatar(t).props.onClick();assert.equal(v.windows[0].focused,focused);assert.equal(v.windows[0].closed,false);assert.equal(v.starts.length,1);assert.equal(v.cancels.length,0);assert.equal(v.logouts.length,0);
 t=v.render();if(!card(t))avatar(t).props.onClick();button(v.render(),'重试账号状态').props.onClick();v.requests[2].resolve(pending);await settle();t=v.render();assert.ok(button(t,'取消登录'));assert.equal(nodes(t,'a')[0].props.href,auth().authorization_url);assert.equal(v.timers.size,1);assert.equal(v.starts.length,1);
});
test('cancel action removes only its active poll deadline and obsolete timeout cannot alter action response',async()=>{
 const v=setup();avatar(await v.load(pending)).props.onClick();v.render();await v.tick();const expired=[...v.timers.values()][0];assert.ok(expired,'poll read owns a deadline');button(v.render(),'取消登录').props.onClick();assert.equal(v.requests[1].signal.aborted,true);assert.equal(v.timers.size,0);
 v.cancels[0].resolve(inside());await settle();const writes=v.writes();expired?.fn();await settle();assert.equal(v.writes(),writes);assert.equal(avatar(v.render()).props['aria-label'],'用户菜单');assert.equal(v.timers.size,0);assert.equal(v.starts.length,0);
});
test('unmount clears deadline and ignores queued deadline plus late read outcomes',async()=>{
 const v=setup();v.render();const expired=[...v.timers.values()][0];assert.ok(expired,'initial read owns a deadline');v.unmount();const writes=v.writes();assert.equal(v.timers.size,0);assert.equal(v.requests[0].signal.aborted,true);
 expired?.fn();await v.tick(60_000);v.requests[0].resolve(inside());await settle();assert.equal(v.writes(),writes);assert.equal(v.listenerCount(),0);assert.equal(v.timers.size,0);
});


test('queued obsolete poll timer cannot replace a newer focus read or its deadline',async()=>{
 const v=setup();await v.load(pending);const oldPoll=[...v.timers.values()][0];v.focus();const deadline=[...v.timers.values()][0],writes=v.writes();
 oldPoll.fn();await settle();assert.equal(v.requests.length,2);assert.equal(v.requests[1].signal.aborted,false);assert.equal(v.writes(),writes);assert.equal([...v.timers.values()][0],deadline);
 await v.tick(30_000);assert.equal(avatar(v.render()).props['aria-label'],'账号状态待确认');
});
test('queued obsolete poll timer cannot resume reads after a completed cancel action',async()=>{
 const v=setup();avatar(await v.load(pending)).props.onClick();v.render();const oldPoll=[...v.timers.values()][0];button(v.render(),'取消登录').props.onClick();v.cancels[0].resolve(out);await settle();const writes=v.writes();
 oldPoll.fn();await settle();assert.equal(v.requests.length,1);assert.equal(v.writes(),writes);assert.equal(v.timers.size,0);assert.equal(avatar(v.render()).props['aria-label'],'登录 / 注册');
});
test('queued obsolete poll timer cannot disturb a newer pending poll timer',async()=>{
 const v=setup();await v.load(pending);const oldPoll=[...v.timers.values()][0];v.focus();v.requests[1].resolve(pending);await settle();const newPoll=[...v.timers.values()][0],writes=v.writes();
 oldPoll.fn();await settle();assert.equal(v.requests.length,2);assert.equal(v.writes(),writes);assert.equal([...v.timers.values()][0],newPoll);await v.tick();assert.equal(v.requests.length,3);assert.equal(v.timers.size,1);
});
