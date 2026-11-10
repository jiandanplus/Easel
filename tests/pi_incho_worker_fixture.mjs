/** Actual bundled Pi core, deterministic provider events, zero network. */
import assert from 'node:assert/strict';
import { pathToFileURL } from 'node:url';

const [directory, modelJson, argumentsJson, expectedCalls] = process.argv.slice(2);
const { runAgent } = await import(pathToFileURL(directory + '/worker.mjs'));
const { createAssistantMessageEventStream, getCurrentTools } = await import(pathToFileURL(directory + '/node_modules/@earendil-works/pi-ai/dist/index.js'));
const row = JSON.parse(modelJson), args = JSON.parse(argumentsJson);
const input = {type:'run',id:'fixture-incho',session_id:'pi:00000000-0000-4000-8000-000000000001',
  system_prompt:'Synthetic Incho schema acceptance',prompt:'one synthetic music operation',messages:[],
  api_key:'noncommercial-fixture',tools_enabled:true,
  model:{id:'text-fixture',baseUrl:'https://fixture.invalid/v1',maxTokens:512,contextWindow:8192},media_models:[row]};
const usage={input:1,output:1,cacheRead:0,cacheWrite:0,totalTokens:2,cost:{input:0,output:0,cacheRead:0,cacheWrite:0,total:0}};
let requests=0,calls=0;
const events=[];
function stream(model,context) {
  const create=getCurrentTools(context.messages).find(tool=>tool.name==='create_media');
  assert(create);
  const schema=JSON.stringify(create.parameters);
  assert(schema.includes('music.generate')&&schema.includes('lyric')&&schema.includes('"maximum":2'));
  assert(!schema.includes('api_key')&&!schema.includes('binding_hash')&&!schema.includes('base_url'));
  const first=++requests===1;
  const final={role:'assistant',content:first?[{type:'toolCall',id:'music-call',name:'create_media',arguments:args}]:[{type:'text',text:'done'}],
    stopReason:first?'toolUse':'stop',api:'openai-responses',provider:'easel',model:'text-fixture',timestamp:Date.now(),usage};
  const result=createAssistantMessageEventStream();
  result.push({type:'start',partial:final});result.push({type:'done',reason:final.stopReason,message:final});result.end();return result;
}
await runAgent(input,{send:async event=>events.push(event),onAbort:()=>{},tool:async value=>{
  calls++;assert.deepEqual(value.arguments,args);
  return {content:[{type:'text',text:'synthetic media operation'}],details:{ok:true}};
}},stream);
assert.equal(calls,Number(expectedCalls));
assert(events.some(event=>event.type==='done'));
console.log(JSON.stringify({ok:true,calls,requests}));
