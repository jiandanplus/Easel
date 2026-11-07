import assert from 'node:assert/strict';
import {test} from 'node:test';
import {load,dependency} from './load.mjs';

// Exercise the real App event handler and hook state before passive effects.
// This small hook driver is not a browser/React scheduler acceptance test.
function hooks(){
  const slots=[];let cursor=0,pending=[];
  const changed=(a,b)=>!a||a.length!==b.length||a.some((v,i)=>v!==b[i]);
  return {
    begin(){cursor=0;pending=[];},flush(){for(const run of pending)run();},
    react:{...dependency('react'),
      useState(initial){const i=cursor++;if(!slots[i])slots[i]={value:typeof initial==='function'?initial():initial};const slot=slots[i];slot.set??=next=>{slot.value=typeof next==='function'?next(slot.value):next;};return[slot.value,slot.set];},
      useRef(initial){const i=cursor++;slots[i]??={current:initial};return slots[i];},
      useCallback(fn,deps){const i=cursor++;if(!slots[i]||changed(slots[i].deps,deps))slots[i]={value:fn,deps};return slots[i].value;},
      useEffect(fn,deps){const i=cursor++;if(!slots[i]||changed(slots[i].deps,deps)){const old=slots[i];slots[i]={deps};pending.push(()=>{old?.cleanup?.();slots[i].cleanup=fn();});}},
    },
  };
}
function find(tree,predicate){if(!tree||typeof tree!=='object')return null;if(predicate(tree))return tree;for(const child of [tree.props?.children].flat(Infinity)){const result=find(child,predicate);if(result)return result;}return null;}
test('real navigation clears stale data in both directions and repeated-page clicks recover',async()=>{
  const core=load('core.ts'),driver=hooks(),calls=[];
  const session={admin:{id:'synthetic-admin',role:'viewer',permissions:['console:read']},csrf_token:'synthetic-csrf'};
  class Client{setSession(){} async read(path,signal){calls.push({path,signal});if(path==='/api/session')return session;if(path==='/api/overview')return{users:7,wallets:[]};return{items:[{id:'synthetic-user'}],has_more:false,limit:25,offset:0};}}
  const App=load('App.tsx',{react:driver.react,'./core':{...core,ConsoleClient:Client}}).default;
  const render=()=>{driver.begin();return App();};
  const settle=()=>new Promise(resolve=>setImmediate(resolve));
  render();driver.flush();await settle();render();driver.flush();await settle();
  const ready=render();const previous=calls.find(c=>c.path==='/api/overview');assert.ok(previous);
  find(ready,n=>n.type==='button'&&n.props.children?.includes('用户')).props.onClick();
  const transition=render();const page=find(transition,n=>typeof n.type==='function'&&n.type.name==='DataPage');
  assert.equal(page.props.domain,'users');assert.equal(page.props.state.kind,'loading');assert.equal(previous.signal.aborted,true);
  driver.flush();await settle();const loaded=render();
  assert.equal(find(loaded,n=>typeof n.type==='function'&&n.type.name==='DataPage').props.state.data.items[0].id,'synthetic-user');
  // Clicking the current page must not leave it permanently in loading.
  find(loaded,n=>n.type==='button'&&n.props.children?.includes('用户')).props.onClick();
  const again=render();driver.flush();await settle();const final=render();
  assert.ok(again);assert.equal(find(final,n=>typeof n.type==='function'&&n.type.name==='DataPage').props.state.kind,'ready');
  const usersRequest=calls.filter(c=>c.path.startsWith('/api/users')).at(-1);
  find(final,n=>n.type==='button'&&n.props.children?.includes('今天')).props.onClick();
  const back=render();assert.equal(find(back,n=>typeof n.type==='function'&&n.type.name==='Overview').props.state.kind,'loading');
  assert.equal(usersRequest.signal.aborted,true);driver.flush();await settle();
  assert.equal(find(render(),n=>typeof n.type==='function'&&n.type.name==='Overview').props.state.data.users,7);
});
