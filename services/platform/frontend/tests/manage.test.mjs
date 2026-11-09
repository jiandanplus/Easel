import assert from 'node:assert/strict';
import { test } from 'node:test';
import { load } from './load.mjs';
const m = load('ManageDialog.tsx'), c = load('core.ts');
test('only ordinary plans within 100 points and 365 days can be issued from the page',()=>{
  assert.equal(m.issuable({id:'friend-v1',credits:'50000000',days:30}),true);
  assert.equal(m.issuable({id:'big-v1',credits:'100000001',days:30}),false);
  assert.equal(m.issuable({id:'long-v1',credits:'1',days:366}),false);
  assert.equal(m.issuable({id:'easel-owner-allowance-v1',credits:'100000000',days:365}),false);
  assert.equal(m.issuable({id:'easel-internal-experience-v1',credits:'10000000',days:7}),false);
});
test('tags split on Chinese and ASCII separators and stay small',()=>{
  assert.equal(JSON.stringify(m.parseTags('朋友，内测 vip')),'["朋友","内测","vip"]');assert.equal(m.parseTags('').length,0);
  assert.throws(()=>m.parseTags('a a'));assert.throws(()=>m.parseTags('x'.repeat(21)));assert.throws(()=>m.parseTags(Array.from({length:11},(_,i)=>'t'+i).join(',')));
});
test('management commands use exact paths and need the matching permission',()=>{
  const client=new c.ConsoleClient(async()=>new Response('{}'),()=> 'key-12345678');
  client.setSession({admin:{id:'a',role:'operator',permissions:['console:read','users:manage']},csrf_token:'csrf'});
  for(const path of ['/api/users/u1/status','/api/users/u1/logout','/api/users/u1/note'])assert.ok(client.operation(path,{},'users:manage'));
  assert.throws(()=>client.operation('/api/wallets/w/plans',{},'plans:issue'));
  assert.throws(()=>client.operation('/api/users/u1/delete',{},'users:manage'));
});
test('redemption and admin commands are allowed only on their exact paths',()=>{
  const client=new c.ConsoleClient(async()=>new Response('{}'),()=> 'key-12345678');
  client.setSession({admin:{id:'a',role:'admin',permissions:['console:read','redemption:manage','admins:manage']},csrf_token:'csrf'});
  for(const [path,permission] of [['/api/redemptions','redemption:manage'],['/api/redemptions/b1/disable','redemption:manage'],['/api/admin-requests/r1/approve','admins:manage'],['/api/admins/a1/disable','admins:manage']])assert.ok(client.operation(path,{},permission));
  assert.throws(()=>client.operation('/api/admins/a1/promote',{},'admins:manage'));
  const r=load('RedemptionsPage.tsx');
  assert.equal(r.codesText({name:'朋友',credits:'30000000',expires_at:'2026-12-07T00:00:00Z',codes:['AAAA-BBBB-CCCC-DDDD']}).split('\n')[1],'AAAA-BBBB-CCCC-DDDD');
});
test('plan editor builds exact multipliers and derives features from chosen models',()=>{
  const p=load('PlanEditor.tsx');
  assert.equal(JSON.stringify(p.multiplierFraction('0.8')),'{"numerator":"8","denominator":"10"}');
  assert.equal(JSON.stringify(p.multiplierFraction('1')),'{"numerator":"1","denominator":"1"}');
  for(const bad of ['0','-1','abc','1.23456'])assert.throws(()=>p.multiplierFraction(bad));
  const items=[{id:'t',feature:'text'},{id:'i',feature:'image'}];
  const plan=p.buildPlan({name:'专业版',credits:'50',days:'30',concurrency:'2',multiplier:'0.8',selected:['t','i'],overrides:{i:'0.5'}},items,'plan-x');
  assert.equal(plan.credits,'50000000');assert.equal(JSON.stringify(plan.features),'["image","text"]');assert.equal(JSON.stringify(plan.models),'["t","i"]');
  assert.equal(plan.pricing.models.i.numerator,'5');assert.equal(plan.test_only,false);
  assert.throws(()=>p.buildPlan({name:'x',credits:'1',days:'30',concurrency:'1',multiplier:'1',selected:[],overrides:{}},items,'p'));
});
test('model catalog shows prices in points',()=>{
  const m=load('ModelCatalogPage.tsx');
  assert.equal(m.partPoints({numerator:'1000000',denominator:'1'},1),'1');assert.equal(m.partPoints({numerator:'2000',denominator:'1'},1000),'2');
  const row={capability:'text',rate:{input:{numerator:'1',denominator:'2'},output:{numerator:'3',denominator:'2'}}};
  assert.equal(JSON.stringify(m.pricesOf(row)),'[["每百万输入 token","0.5"],["每百万输出 token","1.5"]]');
});
test('interface pages show the saved state and never need the key back',()=>{
  const p=load('ProviderConfigForm.tsx');
  assert.equal(p.CHANNELS.map(c=>c.label).join(),'对话与脚本,语音转写,配音,生图,视频,音乐');
  assert.equal(p.channelOf('text'),'chat');assert.equal(p.channelOf('audio.transcribe'),'transcribe');assert.equal(p.channelOf('image.edit'),'image');
  const base={id:'gateway',config:null,status:null};
  assert.equal(p.statusBadge(base,null)[1],'未配置');
  assert.equal(p.statusBadge(base,'https://g/v1')[1],'使用服务器配置');
  assert.equal(p.statusBadge({...base,id:'kling'},'https://g/v1')[1],'未配置');
  const saved={...base,config:{id:'x',base_url:'https://g/v1',key_hint:'abcd',options:{},updated_at:'t'}};
  assert.equal(p.statusBadge(saved,null)[1],'检测中…');
  assert.equal(p.statusBadge({...saved,status:{state:'ok',models:['a','b'],checked_at:'t'}},null)[1],'连接正常 · 可用模型 2 个');
  assert.equal(p.statusBadge({...saved,status:{state:'auth_failed',models:[],checked_at:'t'}},null)[1],'Key 无效或没有权限');
  assert.match(p.statusBadge({...saved,status:{state:'no_model_list',models:[],checked_at:'t'}},null)[1],/不提供模型列表/);
});
test('each page opens on a configured platform and names its interfaces',()=>{
  const p=load('ProviderConfigForm.tsx');
  const channel={channel:'video',platforms:[{id:'gateway',label:'网关',config:null},{id:'kling',label:'可灵',config:{key_hint:'abcd'}}]};
  assert.equal(p.initialPlatform(channel),'kling');
  assert.equal(p.initialPlatform({...channel,platforms:[channel.platforms[0]]}),'gateway');
  assert.equal(p.providerLabel(channel,'console-video-kling'),'后台配置 · 可灵');
  assert.equal(p.providerLabel(channel,'console-video'),'后台配置 · 网关');
  assert.equal(p.providerLabel(channel,'newapi'),'服务器配置 · newapi');
});
test('interface options follow the fields the server asks for',()=>{
  const p=load('ProviderConfigForm.tsx');
  const fields=[{key:'voices',label:'可选音色',required:true,list:true},{key:'group_id',label:'Group ID',required:false,list:false}];
  assert.equal(JSON.stringify(p.parseList('a, b，a\nc')),'["a","b","c"]');
  assert.equal(JSON.stringify(p.buildOptions([],{})),'{}');
  assert.throws(()=>p.buildOptions(fields,{}));
  assert.equal(JSON.stringify(p.buildOptions(fields,{voices:'alloy echo',group_id:' 12 '})),'{"voices":["alloy","echo"],"group_id":"12"}');
  assert.equal(p.joinKey(' k ','',false),'k');assert.equal(p.joinKey('','',false),null);
  assert.equal(p.joinKey('ak','sk',true),'ak|sk');assert.equal(p.joinKey('','',true),null);assert.throws(()=>p.joinKey('ak','',true));
  const client=new (load('core.ts').ConsoleClient)(async()=>new Response('{}'),()=> 'key-12345678');
  client.setSession({admin:{id:'a',role:'admin',permissions:['console:read','providers:manage']},csrf_token:'csrf'});
  assert.ok(client.operation('/api/provider-configs/video',{},'providers:manage'));
  assert.throws(()=>client.operation('/api/provider-configs/other',{},'providers:manage'));
});
