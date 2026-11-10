import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import {runInNewContext} from 'node:vm';
import {test} from 'node:test';
const require=createRequire(new URL('../../web/frontend/package.json',import.meta.url));
const ts=require('typescript');
const source=readFileSync(new URL('../../web/frontend/src/components/PlatformAccountSettings.tsx',import.meta.url),'utf8');
const parsed=ts.createSourceFile('panel.tsx',source,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
const fn=parsed.statements.find(n=>ts.isFunctionDeclaration(n)&&n.name?.text==='LegacyPlatformAccountSettings');
const {outputText}=ts.transpileModule(fn.getText(parsed).replace('export ','')+'\nLegacyPlatformAccountSettings;', {compilerOptions:{target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.React}});
const settle=()=>new Promise(r=>setImmediate(r));
function nodes(t,type){return !t||typeof t!=='object'?[]:[...(t.type===type?[t]:[]),...(t.children||[]).flatMap(n=>nodes(n,type))]}
function text(t){return typeof t==='string'||typeof t==='number'?String(t):!t||typeof t!=='object'?'':(t.children||[]).map(text).join('')}
function setup({authenticated=false,error='',errorCode='',grants=[],stale=false,plansError=false,grantsError=false,usageError=false,entitlementsPromise}={}){
 const state=[];let cursor=0;const effects=[];let ec=0;const submitted=[];let logouts=0;
 const status={configured:true,url:'http://127.0.0.1:8790',authenticated,session_id:stale?'revoked-local-session':'',account:authenticated?{id:'a',email:'test@example.invalid',email_verified:false}:null};
 const component=runInNewContext(outputText,{Error,Date,Promise,React:{Fragment:'fragment',createElement:(type,props,...children)=>({type,props:props||{},children:children.flat()})},
 useState:initial=>{let i=cursor++;if(!(i in state))state[i]=initial;return[state[i],v=>state[i]=typeof v==='function'?v(state[i]):v]},
 useEffect:(cb,deps)=>{let i=ec++;if(!effects[i]||deps.some((d,j)=>d!==effects[i][j])){effects[i]=deps;cb()}},
 platformStatus:async()=>status,platformConfigure:async()=>status,
 platformAuthenticate:async(mode,value)=>{submitted.push({mode,...value});if(error)throw Object.assign(new Error(error),{code:errorCode});return {...status,authenticated:true,account:{email:value.email}}},
 platformLogout:async()=>{logouts++;status.authenticated=false;status.session_id='';return status},platformRevoke:async()=>({ok:true}),
 platformPlans:async()=>{if(plansError)throw new Error('catalog unavailable');return {plans:[{id:'test',name:'Synthetic plan',tokens:8000,days:1,concurrency:1,models:['alias'],test_only:true}]};},
 platformEntitlements:async()=>{if(entitlementsPromise)return entitlementsPromise;if(grantsError)throw new Error('grants unavailable');return {entitlements:grants}},platformUsage:async()=>{if(usageError)throw new Error('usage unavailable');return {used_tokens:72,reserved_tokens:25,remaining_tokens:99999,pending_reconciliation:1}},platformDevices:async()=>({sessions:[]})});
 return {submitted,get logouts(){return logouts},render(){cursor=0;ec=0;return component()}};
}
test('accessible register/login clears password even on safe backend error',async()=>{
 const view=setup({error:'会话已过期'});view.render();await settle();let tree=view.render();
 assert.ok(nodes(tree,'label').some(n=>n.props.htmlFor==='platform-email'));
 nodes(tree,'input').find(n=>n.props.id==='platform-email').props.onChange({target:{value:'test@example.invalid'}});
 nodes(tree,'input').find(n=>n.props.type==='password').props.onChange({target:{value:'synthetic-password-123'}});
 nodes(view.render(),'button').find(n=>text(n)==='注册测试账号').props.onClick();await settle();
 assert.equal(view.submitted[0].mode,'register');assert.equal(view.submitted[0].password,'synthetic-password-123');
 assert.equal(nodes(view.render(),'input').find(n=>n.props.type==='password').props.value,'');assert.match(text(view.render()),/会话已过期/);
});
test('server plan catalog retains test entitlement and offers no payment action',async()=>{
 const view=setup();view.render();await settle();const tree=view.render();
 assert.match(text(tree),/Synthetic plan/);assert.match(text(tree),/测试权益/);
 assert.doesNotMatch(text(tree),/购买|支付|¥|￥/);
});
test('available quota sums only active grants while lifetime usage and pending stay distinct',async()=>{
 const now=Date.now()/1000;
 const view=setup({authenticated:true,grants:[{id:'active',status:'active',starts_at:now-10,ends_at:now+1000,remaining_tokens:300,reserved_tokens:25,used_tokens:3,models:['alias']},{id:'expired',status:'active',starts_at:0,ends_at:1,remaining_tokens:4000,reserved_tokens:0,used_tokens:69,models:['alias']}]});
 view.render();await settle();const value=text(view.render());assert.match(value,/当前可用.*300/);assert.match(value,/累计已用.*72/);assert.match(value,/预留.*25/);assert.match(value,/待核算.*1/);assert.match(value,/已过期/);assert.match(value,/邮箱未验证/);assert.doesNotMatch(value,/99999/);
});

test('fresh login rejection identifies credentials while status expiry remains separate',async()=>{
 const view=setup({error:'平台登录已失效，请重新登录',errorCode:'invalid_authentication'});view.render();await settle();
 nodes(view.render(),'input').find(n=>n.props.id==='platform-password').props.onChange({target:{value:'synthetic-password-123'}});
 nodes(view.render(),'form')[1].props.onSubmit({preventDefault(){}});await settle();
 assert.match(text(view.render()),/登录失败，请检查邮箱和密码/);
});

test('revoked local session exposes explicit clearing before fresh login',async()=>{
 const view=setup({stale:true});view.render();await settle();
 const clear=nodes(view.render(),'button').find(n=>text(n)==='清除失效登录');assert.ok(clear);assert.equal(clear.props.disabled,false);
 clear.props.onClick();await settle();view.render();await settle();assert.equal(view.logouts,1);assert.equal(nodes(view.render(),'button').some(n=>text(n)==='清除失效登录'),false);
 nodes(view.render(),'input').find(n=>n.props.type==='password').props.onChange({target:{value:'synthetic-password-123'}});
 nodes(view.render(),'form')[1].props.onSubmit({preventDefault(){}});await settle();assert.equal(view.submitted[0].mode,'login');assert.equal(nodes(view.render(),'input').find(n=>n.props.type==='password').props.value,'');
});

test('settings account navigation mounts account panel without changing model section state',()=>{
 const panelSource=readFileSync(new URL('../../web/frontend/src/components/SettingsPanel.tsx',import.meta.url),'utf8');
 const parsedPanel=ts.createSourceFile('settings.tsx',panelSource,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
 const declaration=parsedPanel.statements.find(n=>ts.isFunctionDeclaration(n)&&n.name?.text==='SettingsPanel');
 const output=ts.transpileModule(declaration.getText(parsedPanel).replace('export default ','')+'\nSettingsPanel;', {compilerOptions:{target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.React}}).outputText;
 const states=[];let cursor=0;
 const component=runInNewContext(output,{React:{Fragment:'fragment',createElement:(type,props,...children)=>({type,props:props||{},children:children.flat()})},
 useState:initial=>{let i=cursor++;if(!(i in states))states[i]=typeof initial==='function'?initial():initial;return[states[i],v=>states[i]=typeof v==='function'?v(states[i]):v]},useEffect:()=>{},useCallback:cb=>cb,useRef:value=>({current:value}),
 CHANNELS:[],SETTINGS_GROUPS:[{label:'账号',items:[{id:'profile',label:'个人中心'}]},{label:'智能体',items:[{id:'model',label:'AI 服务商'}]}],NAV_ICONS:{},ThemeSettings:'theme',HarnessSettings:'harness',MediaGatewaySettings:'media-gateway',PlatformAccountSettings:'platform-account',ProfileSettings:'platform-account',EnvBoard:'env',IconSlidersHorizontal:'icon',IconPackage:'icon',IconEllipsis:'icon'});
 const render=()=>{cursor=0;return component({onClose(){}})};
 assert.equal(nodes(render(),'platform-account').length,0);
 nodes(render(),'button').find(n=>text(n)==='个人中心').props.onClick();
 assert.equal(nodes(render(),'platform-account').length,1);
 nodes(render(),'button').find(n=>text(n)==='AI 服务商').props.onClick();
 assert.equal(nodes(render(),'platform-account').length,0);
});


test('failed entitlements remain unknown rather than confirmed zero or no grant',async()=>{
 const view=setup({authenticated:true,grantsError:true});view.render();await settle();const value=text(view.render());
 assert.doesNotMatch(value,/当前无测试权益|当前可用 tokens0/);assert.match(value,/权益.*(未知|无法|失败)/);assert.match(value,/累计已用 tokens72/);
});
test('catalog or usage failure does not suppress successful entitlement reads',async()=>{
 const now=Date.now()/1000;
 for(const failure of [{plansError:true},{usageError:true}]){
  const view=setup({authenticated:true,...failure,grants:[{id:'grant',plan_id:'test',status:'active',starts_at:now-1,ends_at:now+100,remaining_tokens:321,used_tokens:0,reserved_tokens:0,models:['alias']}]});
  view.render();await settle();assert.match(text(view.render()),/当前可用 tokens321/);assert.doesNotMatch(text(view.render()),/当前无测试权益/);
 }
});


test('pending entitlements stay unknown until successful confirmed empty result',async()=>{
 let resolve;const entitlementsPromise=new Promise(done=>{resolve=done});
 const view=setup({authenticated:true,entitlementsPromise});view.render();await settle();
 let value=text(view.render());assert.match(value,/当前可用 tokens未知/);assert.doesNotMatch(value,/当前无测试权益/);
 resolve({entitlements:[]});await settle();value=text(view.render());
 assert.match(value,/当前可用 tokens0/);assert.match(value,/当前无测试权益/);
});
