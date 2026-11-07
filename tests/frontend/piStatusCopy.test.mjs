import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';
const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');
const source = readFileSync(new URL('../../web/frontend/src/components/Sidebar.tsx', import.meta.url), 'utf8');
const code = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React } }).outputText;
function nodes(tree,type){return !tree||typeof tree!=='object'?[]:[...(tree.type===type?[tree]:[]),...(tree.children||[]).flatMap(child=>nodes(child,type))]}
function render(runtime,detail='',gateway='disconnected') {
 const exports={};
 const React={createElement:(type,props,...children)=>({type,props:props||{},children:children.flat(Infinity)}),useState:initial=>[initial,()=>{}],useRef:()=>({current:null})};
 runInNewContext(code,{exports,require:name=>name==='react'?React:name.endsWith('/navigation')?{NAV_GROUPS:[]}:name.endsWith('/SidebarAccount')?{__esModule:true,default:'sidebar-account-entry'}:{},React});
 return exports.default({currentPage:'dashboard',sessions:[],gatewayStatus:gateway,apiDirect:false,agentRuntime:runtime,runtimeDetail:detail});
}
test('Pi provider, missing-runtime and central readiness details reach the user card without CLI installation copy',()=>{
 for(const detail of ['内置 Pi 供应商未配置','内置 Pi 运行时不可用，请重新安装完整应用','请先登录中央账号']){
  const status=nodes(render('pi',detail),'sidebar-account-entry')[0].props.runtime;
  assert.equal(status.name,'pi');assert.equal(status.connected,false);
  assert.equal(status.detail,detail);assert.doesNotMatch(status.detail,/CLI/);
 }
 const fallback=nodes(render('pi'),'sidebar-account-entry')[0].props.runtime;
 assert.equal(fallback.detail,'内置 Pi 配置待完善');assert.doesNotMatch(fallback.detail,/CLI/);
});
test('external Codex status passed to the user card retains its existing CLI explanation',()=>{
 for(const [gateway,expected] of [['connected','codex CLI 已安装'],['disconnected','codex CLI 未配置']]){
  const status=nodes(render('codex','',gateway),'sidebar-account-entry')[0].props.runtime;
  assert.equal(status.name,'codex');assert.equal(status.connected,gateway==='connected');
  assert.equal(status.detail,expected);
 }
});

test('dashboard delegates readiness to the shared composer without technical installation copy',()=>{
 const dashboard=readFileSync(new URL('../../web/frontend/src/components/DashboardPage.tsx',import.meta.url),'utf8');
 const output=ts.transpileModule(dashboard,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.React}}).outputText;
 const exports={};
 const React={createElement:(type,props,...children)=>({type,props:props||{},children:children.flat(Infinity)}),useState:initial=>[typeof initial==='function'?initial():initial,()=>{}],useRef:()=>({current:new Set()}),useEffect:()=>{},useSyncExternalStore:()=> 'paper'};
 runInNewContext(output,{exports,require:name=>name==='react'?React:name.endsWith('/whoami')?{getWhoamiCache:()=>({})}:name.endsWith('/theme')?{}:{},React,localStorage:{getItem:()=>null}});
 const getText=tree=>typeof tree==='string'?tree:!tree||typeof tree!=='object'?'':(tree.children||[]).map(getText).join('');
 const composer={type:'shared-composer',props:{'data-readiness':'checking'},children:['正在连接']};
 for(const detail of ['内置 Pi 供应商未配置','请先登录中央账号','内置 Pi 运行时不可用，请重新安装完整应用']){
  const tree=exports.default({persona:'',gatewayStatus:'disconnected',apiDirect:false,agentRuntime:'pi',runtimeDetail:detail,composer});
  assert.equal(nodes(tree,'shared-composer').length,1);assert.equal(getText(nodes(tree,'shared-composer')[0]),'正在连接');
  assert.equal(nodes(tree,'p').some(n=>n.props.className==='dash-connection'),false);assert.doesNotMatch(getText(tree),/CLI|供应商未配置|运行时不可用/);
 }
 const app=readFileSync(new URL('../../web/frontend/src/App.tsx',import.meta.url),'utf8');
 assert.match(app,/<DashboardPage[\s\S]*?composer=\{<ChatPage[\s\S]*?composerOnly[\s\S]*?workbench=\{workbench\}/);
});
