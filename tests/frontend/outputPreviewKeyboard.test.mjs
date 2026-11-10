import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';
const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');
const file = new URL('../../web/frontend/src/lib/deliverables.ts', import.meta.url);
assert.ok(existsSync(file), 'dialog helpers must exist');
const exports = {};
runInNewContext(ts.transpileModule(readFileSync(file, 'utf8'), {compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,{exports,Error,URL});
const { mountPreviewDialog, trapPreviewKey } = exports;
function fixture() {
  const document = {activeElement:null};
  const first={focus(){document.activeElement=first;},getClientRects:()=>[{}]}, last={focus(){document.activeElement=last;},getClientRects:()=>[{}]};
  const dialog={open:false,ownerDocument:document,showModal(){this.open=true;this.modal=true;},close(){this.open=false;},querySelectorAll:()=>[first,last],querySelector:()=>first};
  const opener={isConnected:true,focus(){document.activeElement=opener;}};
  return {dialog,document,first,last,opener};
}
test('preview uses browser modal top layer and restores the actual opener on close', () => {
  const f=fixture();const cleanup=mountPreviewDialog(f.dialog,f.opener);
  assert.equal(f.dialog.modal,true);assert.equal(f.dialog.open,true);assert.equal(f.document.activeElement,f.first);
  cleanup();assert.equal(f.dialog.open,false);assert.equal(f.document.activeElement,f.opener);
});
test('preview cycles forward and reverse tab within its focusable controls', () => {
  const f=fixture(); let prevented=0; const event=shiftKey=>({key:'Tab',shiftKey,preventDefault(){prevented++;}});
  f.document.activeElement=f.last; trapPreviewKey(event(false),f.dialog); assert.equal(f.document.activeElement,f.first);
  f.document.activeElement=f.first; trapPreviewKey(event(true),f.dialog); assert.equal(f.document.activeElement,f.last); assert.equal(prevented,2);
  f.document.activeElement=f.first;trapPreviewKey(event(false),f.dialog);assert.equal(prevented,2);
});
test('closing never focuses a removed opener and leaves non-tab keys untouched', () => {
  const f=fixture();f.opener.isConnected=false;const cleanup=mountPreviewDialog(f.dialog,f.opener);cleanup();assert.equal(f.document.activeElement,f.first);
  let prevented=false;trapPreviewKey({key:'ArrowDown',preventDefault(){prevented=true;}},f.dialog);assert.equal(prevented,false);
});

function viewOutputs({failPreview=false, filename='文案.md', reason='preview', onPreviewClose=()=>{}}={}) {
  const module={};const state=[],refs=[],effects=[];let cursor=0,refCursor=0,effectCursor=0;
  const jsx=(type,props)=>({type,props:props||{}});
  const React={useState(initial){const n=cursor++;if(!(n in state))state[n]=typeof initial==='function'?initial():initial;return[state[n],value=>{state[n]=typeof value==='function'?value(state[n]):value;}];},useRef(initial){return refs[refCursor++] ||= {current:initial};},useEffect(fn,deps){const n=effectCursor++;if(!effects[n]||deps.some((x,i)=>x!==effects[n].deps[i])){effects[n]?.cleanup?.();effects[n]={deps,fn,pending:true};}},useMemo:fn=>fn(),useCallback:fn=>fn};
  const file={type:'file',kind:'text',name:filename,path:'project/'+filename,mtime:1700000000};
  const source=readFileSync(new URL('../../web/frontend/src/components/OutputsPage.tsx',import.meta.url),'utf8');
  const api={fetchOutputs:async()=>[file],fetchOutputContent:async path=>{if(failPreview)throw new Error(reason);return {path,content:'实际文案',kind:'text',isBinary:false};},mediaUrl:path=>'/api/media/'+path.split('/').map(encodeURIComponent).join('/'),deleteOutput:async()=>{}};
  runInNewContext(ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText,{exports:module,require:id=>id==='react'?React:id==='react/jsx-runtime'?{jsx,jsxs:jsx}:id.endsWith('/api')?api:id.endsWith('/sanitize')?{renderMarkdown:x=>x}:id.endsWith('/outputSearch')?{filterLibraryNodes:n=>n}:id.endsWith('/deliverables')?exports:id.endsWith('/DeliverableActions')?{__esModule:true,default:'actions'}:{},window:{confirm:()=>false},document:{activeElement:null},setTimeout,clearTimeout,Date,Error});
  const props={resolveSource:()=> 'session-original',onContinue(){},onSource(){},onPreviewClose};
  return {file,render(){cursor=refCursor=effectCursor=0;return module.default(props);},effects(){effects.forEach(e=>{if(e.pending){e.pending=false;e.cleanup=e.fn();}});},cleanup(){effects.forEach(e=>e.cleanup?.());}};
}
function nodes(tree,type){return !tree||typeof tree!=='object'?[]:[...(tree.type===type?[tree]:[]),...([tree.props?.children].flat(Infinity)).flatMap(x=>nodes(x,type))];}
function text(tree){return tree==null||typeof tree==='boolean'?'':typeof tree==='object'?[tree.props?.children].flat(Infinity).map(text).join(''):String(tree);}
const settle=()=>new Promise(resolve=>setImmediate(resolve));

test('file cards and breadcrumb are buttons and preview is named modal with independent actions', async()=>{
  const view=viewOutputs();view.render();view.effects();await settle();let tree=view.render();
  const opener=nodes(tree,'button').find(n=>n.props['aria-label']==='打开 文案.md');assert.ok(opener);await opener.props.onClick({currentTarget:{isConnected:true,focus(){}}});
  tree=view.render();const dialog=nodes(tree,'dialog')[0];assert.ok(dialog);assert.equal(dialog.props['aria-labelledby'],'output-preview-title');
  const action=nodes(tree,'actions')[0];assert.equal(action.props.reference.path,view.file.path);assert.equal(action.props.reference.sessionId,'session-original');
  assert.ok(nodes(tree,'button').some(n=>n.props['aria-label']==='关闭预览'));assert.ok(nodes(tree,'button').some(n=>text(n)==='内容库'));
  let cancelled=false;dialog.props.onCancel({preventDefault(){cancelled=true;}});assert.equal(cancelled,true);assert.equal(nodes(view.render(),'dialog').length,0);view.cleanup();
});
test('failed text preview retains the exact file download and a reload action', async()=>{
  const view=viewOutputs({failPreview:true});view.render();view.effects();await settle();let tree=view.render();
  await nodes(tree,'button').find(n=>n.props['aria-label']==='打开 文案.md').props.onClick({currentTarget:{isConnected:true,focus(){}}});tree=view.render();
  assert.match(text(tree),/预览加载失败/);assert.ok(nodes(tree,'button').some(n=>text(n)==='重新加载'));assert.equal(nodes(tree,'actions')[0].props.reference.path,view.file.path);view.cleanup();
});
test('closing a preview notifies its source navigation without starting another task', async()=>{
  let closed=0; const view=viewOutputs({onPreviewClose:()=>closed++});view.render();view.effects();await settle();
  await nodes(view.render(),'button').find(n=>n.props['aria-label']==='打开 文案.md').props.onClick({currentTarget:{isConnected:true,focus(){}}});
  nodes(view.render(),'button').find(n=>n.props['aria-label']==='关闭预览').props.onClick();
  assert.equal(closed,1); assert.equal(nodes(view.render(),'dialog').length,0);view.cleanup();
});

test('HTML preview failures and missing/forbidden files keep delivery actions without empty success', async()=>{
  for (const [filename,reason,label] of [['card.html','preview','预览加载失败'],['文案.md','404','文件不存在'],['文案.md','403','无权访问']]) {
    const view=viewOutputs({failPreview:true,filename,reason});view.render();view.effects();await settle();let tree=view.render();
    await nodes(tree,'button').find(n=>n.props['aria-label']==='打开 '+filename).props.onClick({currentTarget:{isConnected:true,focus(){}}});tree=view.render();
    assert.match(text(tree),new RegExp(label));assert.equal(nodes(tree,'actions')[0].props.reference.path,view.file.path);assert.equal(nodes(tree,'iframe').length,0);view.cleanup();
  }
});
