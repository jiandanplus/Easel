import assert from 'node:assert/strict';
import { test } from 'node:test';
import { dependency, load } from './load.mjs';
const React=dependency('react');const {renderToStaticMarkup}=dependency('react-dom/server');
const views=load('views.tsx');
function render(name,props){assert.equal(typeof views[name],'function',name+' must exist');return renderToStaticMarkup(React.createElement(views[name],props));}
test('login and verified pending states never offer self authorization',()=>{
  const login=render('AuthScreen',{state:{kind:'anonymous'},onRetry(){}});assert.match(login,/href="\/auth\/login"/);assert.doesNotMatch(login,/password|Bearer|localStorage/);
  const pending=render('AuthScreen',{state:{kind:'pending',requestId:'REQ-123'},onRetry(){}});assert.match(pending,/REQ-123/);assert.match(pending,/等待运营者授权/);assert.doesNotMatch(pending,/授予权限<|成为管理员/);
});
test('list distinguishes unknown error empty and loaded state with escaped server text',()=>{
  const base={domain:'users',permissions:['console:read'],query:'',offset:0,onSearch(){},onPage(){},onInspect(){},onPublish(){},onAction(){},onRetry(){}};
  assert.match(render('DataPage',{...base,state:{kind:'loading'}}),/正在读取/);
  assert.match(render('DataPage',{...base,state:{kind:'error',message:'读取暂不可用'}}),/读取暂不可用/);
  assert.match(render('DataPage',{...base,state:{kind:'ready',data:{items:[],has_more:false,limit:25,offset:0}}}),/暂无记录/);
  const html=render('DataPage',{...base,state:{kind:'ready',data:{items:[{id:'<script>bad</script>',active:true}],has_more:true,limit:25,offset:0}}});assert.ok(!html.includes('<script>bad'));assert.match(html,/&lt;script&gt;/);assert.match(html,/下一页/);
});
test('viewer has no publication or money mutation actions',()=>{
  const html=render('DataPage',{domain:'plans',permissions:['console:read'],query:'',offset:0,state:{kind:'ready',data:{items:[],has_more:false}},onSearch(){},onPage(){},onInspect(){},onPublish(){},onAction(){},onRetry(){}});
  assert.doesNotMatch(html,/发布新版本|发点|退款|标记付款|强制结算/);
});
test('list refuses a stale overview payload without throwing or inventing an empty result',()=>{
  const html=render('DataPage',{domain:'users',permissions:['console:read'],query:'',offset:0,state:{kind:'ready',data:{users:7,wallets:[]}},onSearch(){},onPage(){},onInspect(){},onPublish(){},onAction(){},onRetry(){}});
  assert.match(html,/数据格式|正在读取/);assert.doesNotMatch(html,/暂无记录|本页 0 条/);
});
test('overview separates genuine funding modes and disabled payments without fabricated totals',()=>{
  const html=render('Overview',{state:{kind:'ready',data:{users:2,products:1,orders:0,executions:4,pending_reconciliation:0,payments_mode:'disabled',wallets:[{funding_mode:'live',available:'9997344',spendable_available:'9997344',held:'0',spent:'2656'}]}}});
  assert.match(html,/支付未启用/);assert.match(html,/9\.997344/);assert.match(html,/0\.002656/);assert.match(html,/正式资金/);
});
test('spendable and book balance remain separate, missing spendable never falls back to book balance',()=>{
  const html=render('Overview',{state:{kind:'ready',data:{wallets:[{funding_mode:'live',available:'10000000',spendable_available:'1000000',frozen_available:'9000000',unposted_expired_available:'0'}]}}});
  assert.match(html,/账本可用/);assert.match(html,/可用余额/);assert.match(html,/冻结余额/);assert.match(html,/未入账过期/);
  const missing=render('Overview',{state:{kind:'ready',data:{wallets:[{funding_mode:'live',available:'10000000'}]}}});
  assert.match(missing,/未知<small>可用余额/);
});
test('protected offers do not expose withdrawal and detail audits show safe result summary',()=>{
  const html=render('DataPage',{domain:'offers',permissions:['console:read','billing:publish'],query:'',offset:0,state:{kind:'ready',data:{items:[{id:'internal',withdrawn:false,protected:true}],has_more:false}},onSearch(){},onPage(){},onInspect(){},onPublish(){},onAction(){},onRetry(){}});
  assert.doesNotMatch(html,/>撤回<\/button>/);
  const detail=render('DetailFields',{data:{result_summary:{enabled:true,version:'approved-v2'}}});assert.match(detail,/approved-v2/);
});
test('users list and summary name people by username and email',()=>{
  const list=render('DataPage',{domain:'users',permissions:['console:read'],query:'',offset:0,state:{kind:'ready',data:{items:[{id:'user-1',username:'alice',email:'alice@example.test',spendable_available:'3',active:true,created_at:'2026-11-06T00:00:00Z'}],limit:25,offset:0,has_more:false}},onSearch(){},onPage(){},onInspect(){},onPublish(){},onAction(){},onRetry(){}});
  assert.match(list,/用户名/);assert.match(list,/alice@example\.test/);assert.match(list,/alice/);
  const named=render('UserSummary',{data:{id:'user-1',active:true,created_at:'2026-11-06T00:00:00Z',identities:[{username:'alice',email:'alice@example.test',email_verified:true}],wallet_details:[]}});
  assert.match(named,/alice · alice@example\.test/);
  const unnamed=render('UserSummary',{data:{id:'user-2',active:true,created_at:'2026-11-06T00:00:00Z',identities:[{username:null,email:null,email_verified:false}],wallet_details:[]}});
  assert.match(unnamed,/下次登录后显示/);
});
test('admins see the fixed allowance action only when no owner allowance is active',()=>{
  const wallet={id:'wallet-1',spendable_available:'0',spent:'0',held:'0',pending_reconciliation:0,memberships:[],reservations:[]};
  const base={id:'user-1',active:true,created_at:'2026-11-06T00:00:00Z',identities:[]};
  assert.match(render('UserSummary',{data:{...base,wallet_details:[wallet]},onAllowance(){}}),/发放所有者额度/);
  assert.doesNotMatch(render('UserSummary',{data:{...base,wallet_details:[wallet]}}),/发放所有者额度/);
  const active={...wallet,memberships:[{id:'m',plan_id:'easel-owner-allowance-v1',expires_at:'2999-01-01T00:00:00Z'}]};
  const html=render('UserSummary',{data:{...base,wallet_details:[active]},onAllowance(){}});
  assert.doesNotMatch(html,/发放所有者额度<\/button>/);assert.match(html,/到期前不能再发/);
});
