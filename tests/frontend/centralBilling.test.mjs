import { requireDeliverables } from './deliverablesHarness.mjs';
import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';
const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript'), React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const sourceUrl = new URL('../../web/frontend/src/components/CentralBillingSettings.tsx', import.meta.url);
const settle = () => new Promise(resolve => setImmediate(resolve));
const status = { configured:true, authenticated:true, url:'https://central.invalid', scope:{user_id:'one',session_id:'session',tenant_id:'tenant',billing_account_id:'wallet',authorization_epoch:0} };
const offer = {id:'fixture-cny-v1',plan_id:'test-v1',version:'1',name:'共享测试套餐',currency:'CNY',amount_minor:'1',credits:'9007199254740993000001',days:30,concurrency:2,included_products:['easel'],features:['text'],test_only:true,terms:{refund_policy:'unused_full_only',description:'隔离测试条款'}};
const catalog = {mode:'test',channels:[{id:'wechat',label:'微信',enabled:true},{id:'alipay',label:'支付宝',enabled:true}],offers:[offer]};
const order = {id:'A'.repeat(32),status:'pending',channel:'wechat',mode:'test',currency:'CNY',amount_minor:'1',offer,created_at:'2026-10-22T00:00:00+00:00',expires_at:'2099-01-01T00:00:00Z',paid_at:null,credit_state:'pending',checkout:{qr_code:'weixin://test',qr_data_url:'data:image/svg+xml;base64,PHN2Zy8+',expires_at:'2099-01-01T00:00:00Z'},refund:null};
function load(hooks=React, api={}, extra={}) {
 assert.ok(existsSync(sourceUrl),'checkout component must exist');
 const code=ts.transpileModule(readFileSync(sourceUrl,'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.React}}).outputText;
 const exports={};runInNewContext(code,{exports,require:name=>name==='react'?hooks:name.endsWith('/api')?api:{},React,Date,Error,BigInt,Promise,Set,Map,AbortController,crypto:{randomUUID:()=> 'stable-checkout-key'},setTimeout,clearTimeout,...extra});return exports;
}
const nodes=(tree,type)=>!tree||typeof tree!=='object'?[]:[...(tree.type===type?[tree]:[]),...(tree.children||[]).flatMap(item=>nodes(item,type))];
const text=tree=>typeof tree==='string'||typeof tree==='number'?String(tree):!tree||typeof tree!=='object'?'':(tree.children||[]).map(text).join('');
function setup(overrides={}) {
 const states=[],refs=[],effects=[],queue=[],timers=new Map();let cursor=0,refCursor=0,effectCursor=0,timerId=0,mounted=true;
 const calls={create:[],get:0,refresh:0,close:0,granted:0,signals:[]};
 const api={centralBillingCatalog:async()=>overrides.catalog||catalog,centralOrders:async()=>({orders:overrides.orders||[]}),centralCreateOrder:async(body,signal)=>{calls.create.push(body);calls.signals.push(signal);return overrides.create?overrides.create(body):order;},centralOrder:async(id,signal)=>{calls.get++;calls.signals.push(signal);return overrides.get?overrides.get():order;},centralRefreshOrder:async()=>{calls.refresh++;return overrides.refresh?overrides.refresh():order;},centralCloseOrder:async()=>{calls.close++;return {...order,status:'pending'};}};
 const hooks={...React,createElement:(type,props,...children)=>({type,props:props||{},children:children.flat(Infinity)}),useState(initial){const i=cursor++;if(!(i in states))states[i]=typeof initial==='function'?initial():initial;return [states[i],value=>{if(mounted)states[i]=typeof value==='function'?value(states[i]):value;}];},useRef(initial){const i=refCursor++;return refs[i]||=( {current:initial});},useEffect(callback,deps){const i=effectCursor++;if(!effects[i]||deps.some((value,k)=>!Object.is(value,effects[i].deps[k])))queue.push(()=>{effects[i]?.cleanup?.();effects[i]={deps,cleanup:callback()};});}};
 const mod=load(hooks,api,{React:hooks,setTimeout:fn=>{const id=++timerId;timers.set(id,fn);return id;},clearTimeout:id=>timers.delete(id)});
 let currentStatus=overrides.status||status;
 const render=()=>{cursor=refCursor=effectCursor=0;const tree=mod.BillingSession({status:currentStatus,onFinancialChange:()=>calls.granted++});queue.splice(0).forEach(fn=>fn());return tree;};
 const flush=async()=>{render();await settle();render();await settle();return render();};
 return {mod,calls,timers,render,flush,currentOrder:()=>nodes(render(),mod.BillingOrderView)[0]?.props.order,setStatus:s=>currentStatus=s,async click(label){const button=nodes(render(),'button').find(node=>text(node).includes(label));assert.ok(button,label);assert.equal(Boolean(button.props.disabled),false);button.props.onClick();await settle();return render();},async tick(){const fns=[...timers.values()];timers.clear();fns.forEach(fn=>fn());await settle();return render();},unmount(){mounted=false;effects.forEach(effect=>effect?.cleanup?.());}};
}
const checkoutText=view=>text(view.mod.BillingOrderView({order:view.currentOrder(),now:Date.now()}));
test('CNY and microcredits retain exact integer precision',()=>{const mod=load();assert.equal(mod.formatCny('9007199254740993'),'¥90071992547409.93');assert.equal(mod.formatCny('1'),'¥0.01');assert.equal(mod.formatCny('1.5'),'金额无效');assert.equal(mod.formatCredits(offer.credits),'9007199254740993.000001');});
test('real React renders backend order snapshot refund and expiry without object children',()=>{const mod=load();const html=renderToStaticMarkup(React.createElement(mod.BillingOrderView,{order:{...order,status:'refunded',refund:{id:'r',status:'reconciliation_required',reason:'fixture'}},now:Date.now()}));assert.match(html,/共享测试套餐/);assert.match(html,/¥0.01/);assert.match(html,/人工核算/);assert.match(html,/测试/);assert.doesNotMatch(html,/\[object Object\]/);const expired=renderToStaticMarkup(React.createElement(mod.BillingOrderView,{order:{...order,checkout:{...order.checkout,expires_at:'2000-01-01T00:00:00Z'}},now:Date.now()}));assert.match(expired,/二维码已过期/);assert.doesNotMatch(expired,/<img/);});
test('disabled and unauthenticated users cannot purchase while test mode is explicit',async()=>{const disabled=setup({catalog:{...catalog,mode:'disabled'}});assert.match(text(await disabled.flush()),/支付暂未开放/);assert.equal(nodes(disabled.render(),'button').some(node=>text(node).includes('购买')),false);const anon=setup({status:{...status,authenticated:false,scope:null}});assert.match(text(await anon.flush()),/登录后购买或查看订单/);assert.equal(nodes(anon.render(),'button').some(node=>text(node).includes('购买')),false);const enabled=setup();assert.match(text(await enabled.flush()),/模拟支付.*测试权益/);});
test('creation retries preserve idempotency key and never trust client price',async()=>{let attempt=0;const view=setup({create:async()=>{if(++attempt===1)throw new Error('网络中断');return order;}});await view.flush();await view.click('购买');assert.match(text(view.render()),/网络中断/);await view.click('购买');assert.equal(view.calls.create.length,2);assert.equal(view.calls.create[0].idempotency_key,view.calls.create[1].idempotency_key);assert.deepEqual(Object.keys(view.calls.create[0]).sort(),['channel','idempotency_key','offer_id']);});
test('refresh only requests provider query and cannot mark pending as paid',async()=>{const view=setup();await view.flush();await view.click('购买');await view.click('查询支付状态');assert.equal(view.calls.refresh,1);assert.doesNotMatch(text(view.render()),/已确认到账/);});
test('serialized bounded polling stops on unmount and ignores an in-flight result',async()=>{let resolve;const view=setup({get:()=>new Promise(done=>resolve=done)});await view.flush();await view.click('购买');await view.tick();await view.tick();assert.equal(view.calls.get,1);view.unmount();resolve({...order,status:'paid',credit_state:'granted'});await settle();assert.equal(view.timers.size,0);assert.equal(view.calls.granted,0);assert.ok(view.calls.signals.some(signal=>signal.aborted));});
test('granted payment refreshes parent once and terminal state survives stale response',async()=>{let resolve;const view=setup({get:()=>new Promise(done=>resolve=done),refresh:async()=>({...order,status:'paid',credit_state:'granted'})});await view.flush();await view.click('购买');await view.tick();await view.click('查询支付状态');resolve(order);await settle();const tree=view.render();assert.match(text(tree),/已确认到账/);assert.equal(view.calls.granted,1);assert.equal(view.timers.size,0);});
test('history restores pending orders and unknown state remains unknown',async()=>{const view=setup({orders:[order]});await view.flush();await view.click('查看订单');assert.ok(nodes(view.render(),'button').find(node=>text(node).includes('查询支付状态')));const mod=load();const html=renderToStaticMarkup(React.createElement(mod.BillingOrderView,{order:{...order,status:'provider_unknown'},now:Date.now()}));assert.match(html,/状态待确认/);assert.doesNotMatch(html,/已确认到账|支付失败/);});
test('account identity determines keyed billing session to discard old requests',()=>{const mod=load();const a=mod.default({status,onFinancialChange:()=>{}}),b=mod.default({status:{...status,scope:{...status.scope,user_id:'two',session_id:'new'}},onFinancialChange:()=>{}});assert.notEqual(a.key,b.key);});
test('browser billing API sends only isolated uncached routes with cancellation',async()=>{const source=readFileSync(new URL('../../web/frontend/src/lib/api.ts',import.meta.url),'utf8');const exports={},requests=[],controller=new AbortController();runInNewContext(ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS}}).outputText,{exports,require:requireDeliverables,window:{location:{pathname:'/easel/'}},Error,Object,fetch:async(url,options)=>{requests.push({url,options});return{ok:true,json:async()=>({})};}});await exports.centralBillingCatalog(controller.signal);await exports.centralOrders(controller.signal);await exports.centralOrder(order.id,controller.signal);await exports.centralCreateOrder({offer_id:offer.id,channel:'wechat',idempotency_key:'stable'},controller.signal);await exports.centralRefreshOrder(order.id,controller.signal);await exports.centralCloseOrder(order.id,controller.signal);assert.deepEqual(requests.map(r=>r.url),['billing/catalog','orders','orders/'+order.id,'orders','orders/'+order.id+'/refresh','orders/'+order.id+'/close'].map(p=>'/easel/api/platform/central/'+p));assert.ok(requests.every(r=>r.options.cache==='no-store'&&r.options.signal===controller.signal&&!r.options.headers?.Authorization));assert.deepEqual(JSON.parse(requests[3].options.body),{offer_id:offer.id,channel:'wechat',idempotency_key:'stable'});});

test('live checkout requires a positive commercial price and a formal offer',async()=>{const testOnly=setup({catalog:{...catalog,mode:'live'}});await testOnly.flush();assert.equal(nodes(testOnly.render(),'button').some(node=>text(node).includes('购买')),false);const zero=setup({catalog:{...catalog,mode:'live',offers:[{...offer,test_only:false,amount_minor:'0'}]}});await zero.flush();assert.equal(nodes(zero.render(),'button').some(node=>text(node).includes('购买')),false);});
test('old account creation cannot change new account or refresh its wallet',async()=>{let complete;const previous=setup({create:()=>new Promise(resolve=>complete=resolve)});await previous.flush();await previous.click('购买');previous.unmount();const current=setup({status:{...status,scope:{...status.scope,user_id:'two',session_id:'new'}}});await current.flush();complete({...order,status:'paid',credit_state:'granted'});await settle();assert.equal(previous.calls.granted,0);assert.equal(current.calls.granted,0);assert.doesNotMatch(text(current.render()),/已确认到账/);});
test('polling is bounded and malicious QR URLs never render',async()=>{const view=setup();await view.flush();await view.click('购买');for(let i=0;i<185;i++)await view.tick();assert.equal(view.calls.get,180);assert.equal(view.timers.size,0);const mod=load();const html=renderToStaticMarkup(React.createElement(mod.BillingOrderView,{order:{...order,checkout:{...order.checkout,qr_data_url:'https://evil.test/qr.svg'}},now:Date.now()}));assert.doesNotMatch(html,/<img/);assert.match(html,/收款码暂未就绪/);});


test('newer reconciliation replaces refunded even above Number safe integer revision',async()=>{
 const refunded={...order,revision:'9007199254740992',status:'refunded',credit_state:'revoked',checkout:null,refund:{id:'r',status:'succeeded'}};
 const review={...refunded,revision:'9007199254740993',status:'reconciliation_required',credit_state:'manual_review'};
 const view=setup({orders:[refunded],refresh:async()=>review});
 await view.flush();await view.click('查看订单');await view.click('查询支付状态');
 assert.match(text(view.render()),/待人工核算/);assert.doesNotMatch(text(view.render()),/已确认到账/);
});
test('stale paid cannot replace a newer reconciliation snapshot or refresh its balance',async()=>{
 const review={...order,revision:'12',status:'reconciliation_required',credit_state:'manual_review',refund:{id:'r',status:'manual_reconciliation'}};
 const view=setup({orders:[review],refresh:async()=>({...order,revision:'11',status:'paid',credit_state:'granted'})});
 await view.flush();const before=view.calls.granted;await view.click('查看订单');await view.click('查询支付状态');
 assert.match(text(view.render()),/待人工核算/);assert.doesNotMatch(text(view.render()),/已确认到账/);assert.equal(view.calls.granted,before);
});
for(const [currentRefund,staleRefund] of [['pending',null],['failed','pending'],['succeeded','failed'],['manual_reconciliation','failed']]) {
 test(`stale refund ${staleRefund} cannot overwrite newer ${currentRefund}`,async()=>{
  const current={...order,revision:'20',status:currentRefund==='succeeded'?'refunded':'paid',credit_state:currentRefund==='succeeded'?'revoked':'granted',refund:{id:'r',status:currentRefund}};
  const stale={...order,revision:'19',status:'paid',credit_state:'granted',refund:staleRefund?{id:'r',status:staleRefund}:null};
  const view=setup({orders:[current],refresh:async()=>stale});await view.flush();await view.click('查看订单');
  const refreshes=view.calls.granted;await view.click('查询支付状态');assert.equal(view.currentOrder(),current);assert.equal(view.calls.granted,refreshes);
 });
}
test('equal revision cannot replace the accepted financial snapshot',async()=>{
 const current={...order,revision:'20',status:'paid',credit_state:'granted',refund:{id:'r',status:'failed'}};
 const view=setup({orders:[current],refresh:async()=>({...current,refund:{id:'r',status:'pending'}})});
 await view.flush();await view.click('查看订单');await view.click('查询支付状态');assert.match(checkoutText(view),/退款未完成/);assert.doesNotMatch(checkoutText(view),/点数已冻结/);
});
test('missing revisions preserve refund and review facts but still reveal reconciliation after refund',async()=>{
 const refunded={...order,status:'refunded',credit_state:'revoked',refund:{id:'r',status:'succeeded'}};
 let next={...refunded,status:'reconciliation_required',credit_state:'manual_review'};
 const view=setup({orders:[refunded],refresh:async()=>next});await view.flush();await view.click('查看订单');await view.click('查询支付状态');assert.match(text(view.render()),/待人工核算/);
 next={...order,status:'paid',credit_state:'granted'};await view.click('查询支付状态');assert.match(text(view.render()),/待人工核算/);
 for(const state of ['pending','failed','manual_reconciliation']) {
  const current={...order,status:'paid',credit_state:'granted',refund:{id:'r',status:state}};
  const fallback=setup({orders:[current],refresh:async()=>({...current,refund:state==='failed'?{id:'r',status:'pending'}:null})});
  await fallback.flush();await fallback.click('查看订单');await fallback.click('查询支付状态');assert.equal(fallback.currentOrder(),current);
 }
});
test('accepted financial changes refresh parent once per change, including freeze failure retry revoke and review',async()=>{
 const paid={...order,revision:'2',status:'paid',credit_state:'granted',checkout:null};
 let next=paid;
 const view=setup({orders:[paid],refresh:async()=>next});await view.flush();await view.click('查看订单');assert.equal(view.calls.granted,1);
 const changes=[
  {...paid,revision:'3',refund:{id:'r',status:'pending'}},
  {...paid,revision:'4',refund:{id:'r',status:'failed'}},
  {...paid,revision:'5',refund:{id:'r2',status:'pending'}},
  {...paid,revision:'6',status:'refunded',credit_state:'revoked',refund:{id:'r2',status:'succeeded'}},
  {...paid,revision:'7',status:'reconciliation_required',credit_state:'manual_review',refund:{id:'r2',status:'manual_reconciliation'}},
 ];
 for(const [index,change] of changes.entries()) {
  next=change;await view.click('查询支付状态');assert.equal(view.calls.granted,index+2,change.refund.status);
  await view.click('查询支付状态');await view.flush();assert.equal(view.calls.granted,index+2,'unchanged polls and renders must not refresh');
 }
 next={...changes.at(-1),revision:'8'};await view.click('查询支付状态');assert.equal(view.calls.granted,6,'revision alone has no financial effect');
 await view.click('刷新目录和订单');await view.flush();assert.equal(view.calls.granted,6,'stale list must not refresh or replace review');assert.match(text(view.render()),/待人工核算/);
});
test('large bounded generated SVG QR renders and payloads above one million characters stay hidden',()=>{
 const mod=load();
 const renderQr=length=>renderToStaticMarkup(React.createElement(mod.BillingOrderView,{order:{...order,checkout:{...order.checkout,qr_data_url:'data:image/svg+xml;base64,'+'A'.repeat(length)}},now:Date.now()}));
 assert.match(renderQr(450000),/<img/);assert.doesNotMatch(renderQr(1000001),/<img/);assert.match(renderQr(1000001),/收款码暂未就绪/);
});

test('account integration refreshes wallet and entitlements for financial callback without a request loop',async()=>{
 const source=readFileSync(new URL('../../web/frontend/src/components/CentralAccountSettings.tsx',import.meta.url),'utf8');
 const states=[],refs=[],effects=[],queue=[];let cursor=0,refCursor=0,effectCursor=0;
 const calls={status:0,wallet:0,entitlements:0};
 const api={
  centralAccountSignature:value=>value.authenticated ? 'fixture-scope' : null,
  centralStatus:async()=>{calls.status++;return {...status,login:{pending:false}};},
  centralPlans:async()=>({plans:[]}),centralWallet:async()=>{calls.wallet++;return {available:'0'};},
  centralEntitlements:async()=>{calls.entitlements++;return {active:false,product_id:'easel',features:[],memberships:[]};},
  centralUsage:async()=>({usage:[]}),centralDevices:async()=>({sessions:[]}),
 };
 const hooks={...React,createElement:(type,props,...children)=>({type,props:props||{},children:children.flat(Infinity)}),
  useState(initial){const i=cursor++;if(!(i in states))states[i]=typeof initial==='function'?initial():initial;return [states[i],value=>{states[i]=typeof value==='function'?value(states[i]):value;}];},
  useRef(initial){const i=refCursor++;return refs[i]||={current:initial};},
  useEffect(callback,deps){const i=effectCursor++;if(!effects[i]||deps.some((value,k)=>!Object.is(value,effects[i].deps[k])))queue.push(()=>{effects[i]?.cleanup?.();effects[i]={deps,cleanup:callback()};});},
 };
 const exports={};runInNewContext(ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.React}}).outputText,{exports,require:name=>name==='react'?hooks:name.endsWith('/api')?api:name.includes('CentralBillingSettings')?{__esModule:true,default:'billing'}:{},React:hooks,Date,Error,BigInt,Promise,AbortController,setTimeout,clearTimeout});
 const render=()=>{cursor=refCursor=effectCursor=0;const tree=exports.default();queue.splice(0).forEach(fn=>fn());return tree;};
 const flush=async()=>{render();await settle();render();await settle();return render();};
 await flush();assert.deepEqual(calls,{status:1,wallet:1,entitlements:1});
 const billing=nodes(render(),'billing')[0];assert.equal(typeof billing.props.onFinancialChange,'function');
 billing.props.onFinancialChange();await flush();assert.deepEqual(calls,{status:2,wallet:2,entitlements:2});
 for(let i=0;i<5;i++)await flush();assert.deepEqual(calls,{status:2,wallet:2,entitlements:2});
 effects.forEach(effect=>effect?.cleanup?.());
});


test('same revision detail enriches restored list QR and later metadata cannot erase it',async()=>{
 const metadata={...order,revision:'8',checkout:{...order.checkout,qr_data_url:null}};
 const detail={...metadata,checkout:{...order.checkout}};
 const view=setup({orders:[metadata],get:async()=>detail});await view.flush();await view.click('查看订单');
 assert.match(checkoutText(view),/收款码暂未就绪/);assert.equal(view.calls.granted,0);
 await view.tick();assert.equal(view.currentOrder().checkout.qr_data_url,order.checkout.qr_data_url);
 assert.equal(nodes(view.mod.BillingOrderView({order:view.currentOrder(),now:Date.now()}),'img').length,1);assert.equal(view.calls.granted,0);
 await view.click('刷新目录和订单');await view.flush();assert.equal(view.currentOrder().checkout.qr_data_url,order.checkout.qr_data_url);assert.equal(view.calls.granted,0);
});
test('equal revision QR enrichment never accepts mismatching status credit or refund fields',async()=>{
 const metadata={...order,revision:'8',checkout:{...order.checkout,qr_data_url:null}};
 const conflicting={...metadata,status:'paid',credit_state:'granted',refund:{id:'r',status:'succeeded'},checkout:{...order.checkout}};
 const view=setup({orders:[metadata],refresh:async()=>conflicting});await view.flush();await view.click('查看订单');await view.click('查询支付状态');
 assert.equal(view.currentOrder().status,'pending');assert.equal(view.currentOrder().credit_state,'pending');assert.equal(view.currentOrder().refund,null);
 assert.equal(view.currentOrder().checkout.qr_data_url,order.checkout.qr_data_url);assert.equal(view.calls.granted,0);assert.doesNotMatch(text(view.render()),/已确认到账|已退款/);
});
for(const [label,checkout] of [
 ['external image',{...order.checkout,qr_data_url:'https://evil.test/qr.svg'}],
 ['oversized image',{...order.checkout,qr_data_url:'data:image/svg+xml;base64,'+'A'.repeat(1000001)}],
 ['different payment target',{...order.checkout,qr_code:'weixin://different'}],
 ['different expiry',{...order.checkout,expires_at:'2098-01-01T00:00:00Z'}],
]) {
 test(`equal revision QR enrichment rejects ${label}`,async()=>{
  const metadata={...order,revision:'8',checkout:{...order.checkout,qr_data_url:null}};
  const view=setup({orders:[metadata],refresh:async()=>({...metadata,checkout})});await view.flush();await view.click('查看订单');await view.click('查询支付状态');
  assert.equal(view.currentOrder(),metadata);assert.match(checkoutText(view),/收款码暂未就绪/);assert.equal(view.calls.granted,0);
 });
}
