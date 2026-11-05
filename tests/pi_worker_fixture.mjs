/** Noncommercial offline SDK fixture. No HTTP, accounts or provider keys. */
import assert from 'node:assert/strict';
import { pathToFileURL } from 'node:url';
const directory = process.argv[2];
const { runAgent } = await import(pathToFileURL(directory + '/worker.mjs'));
const { createAssistantMessageEventStream } = await import(pathToFileURL(directory + '/node_modules/@earendil-works/pi-ai/dist/index.js'));
const scenario = process.argv[3];
let attempts = 0, toolCalls = 0, abort, seenSignal = false;
const events = [], optionsSeen = [];
const input = { type:'run', id:'fixture-run', session_id:'pi:00000000-0000-4000-8000-000000000000',
  system_prompt:'Easel fixture baseline', prompt:'fixture', messages:[], api_key:'noncommercial-fixture',
  tools_enabled:true, model:{id:'fixture',baseUrl:'https://fixture.invalid/v1',maxTokens:512,contextWindow:8192} };
const usage = {input:1,output:1,cacheRead:0,cacheWrite:0,totalTokens:2,cost:{input:0,output:0,cacheRead:0,cacheWrite:0,total:0}};
function message(content,stopReason='stop') {
  return {role:'assistant',content,stopReason,api:'openai-responses',provider:'easel',model:'fixture',timestamp:Date.now(),usage};
}
function stream(model,context,options) {
  attempts++; optionsSeen.push(options);
  assert(context.messages.some(m => m.role === 'system'), 'owned baseline omitted');
  const s = createAssistantMessageEventStream();
  if (scenario === 'abort') {
    options.signal.addEventListener('abort', () => {
      seenSignal = true;
      s.push({type:'error',reason:'aborted',error:{...message([],'aborted'),errorMessage:'fixture-aborted'}});s.end();
    },{once:true});
    setImmediate(() => abort());
  } else if (scenario === 'error') {
    s.push({type:'error',reason:'error',error:{...message([],'error'),errorMessage:'private-provider-failure'}});s.end();
  } else {
    const moreTools = scenario === 'budget' || scenario === 'tool-budget' || scenario === 'invalid-tool-budget' || (scenario === 'tool' && attempts === 1);
    const count = ['tool-budget','invalid-tool-budget'].includes(scenario) ? 17 : 1;
    const content = moreTools ? Array.from({length:count},(_,i)=>({type:'toolCall',id:`t${attempts}-${i}`,name:'read_file',arguments:scenario === 'invalid-tool-budget' ? {} : {path:'fixture.txt'}})) : [{type:'text',text:'success'}];
    const final=message(content,moreTools?'toolUse':'stop');
    s.push({type:'start',partial:final});
    if (!moreTools) s.push({type:'text_delta',contentIndex:0,delta:'success',partial:final});
    s.push({type:'done',reason:final.stopReason,message:final});s.end();
  }
  return s;
}
await runAgent(input,{
  async send(value){events.push(value);},
  onAbort(fn){abort=fn;},
  async tool(value,signal){
    toolCalls++;
    assert(!signal.aborted);
    return {content:[{type:'text',text:'fixture file'}],details:{ok:true}};
  },
},stream);
assert(optionsSeen.every(o=>o.maxRetries===0 && o.maxTokens===512 && o.apiKey==='noncommercial-fixture' && o.signal));
// SDK header waiting must outlive the bounded central and native budgets.
assert(optionsSeen.every(o=>o.timeoutMs===330000));
if (scenario === 'error') assert(events.some(e=>e.type==='error') && !events.some(e=>e.type==='done'));
else if (scenario === 'abort') assert(seenSignal && events.some(e=>e.type==='error') && !events.some(e=>e.type==='done'));
else if (scenario === 'budget') assert(attempts===8 && toolCalls===8 && events.some(e=>e.type==='error'&&e.code==='turn_limit'));
else if (scenario === 'invalid-tool-budget') assert(attempts===1 && toolCalls===0 && events.some(e=>e.type==='error'&&e.code==='turn_limit'));
else if (scenario === 'tool-budget') assert(attempts===1 && toolCalls===16 && events.some(e=>e.type==='error'));
else assert(attempts===2 && toolCalls===1 && events.some(e=>e.type==='token'&&e.text==='success') && events.at(-1).type==='done');
console.log(JSON.stringify({scenario,attempts,toolCalls,success:true}));
