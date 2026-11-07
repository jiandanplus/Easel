/** Actual Pi 1.4 core with deterministic provider events; no network calls. */
import assert from 'node:assert/strict';
import { pathToFileURL } from 'node:url';
const directory = process.argv[2], scenario = process.argv[3];
const { runAgent } = await import(pathToFileURL(directory + '/worker.mjs'));
const { createAssistantMessageEventStream, getCurrentTools } = await import(pathToFileURL(directory + '/node_modules/@earendil-works/pi-ai/dist/index.js'));
const input = { type:'run', id:'fixture-media', session_id:'pi:00000000-0000-4000-8000-000000000000',
  system_prompt:'Easel synthetic media acceptance', prompt:'one synthetic image', messages:[], api_key:'noncommercial-fixture',
  tools_enabled:true, model:{id:'text-fixture',baseUrl:'https://fixture.invalid/v1',maxTokens:512,contextWindow:8192},
  media_models:scenario === 'media-off' ? [] : [JSON.parse(process.argv[4])] };
const usage = {input:1,output:1,cacheRead:0,cacheWrite:0,totalTokens:2,cost:{input:0,output:0,cacheRead:0,cacheWrite:0,total:0}};
let requests = 0, calls = 0;
const events=[];
function stream(model, context, options) {
  requests++;
  const tools=getCurrentTools(context.messages), names=tools.map(tool=>tool.name);
  assert.equal(names.includes('create_media'), scenario !== 'media-off');
  if(scenario !== 'media-off') {
    const create=tools.find(tool=>tool.name==='create_media');
    const schema=JSON.stringify(create.parameters);
    assert(schema.includes(input.media_models[0].id));
    if(scenario.startsWith('speech')) { assert(schema.includes('audio.speech'));assert(create.description.includes('3/2'));assert(create.description.includes('7/3'));assert(create.description.includes('verified_input_v1')); }
    assert(!schema.includes('api_key') && !schema.includes('wallet') && !schema.includes('base_url'));
  }
  const first=requests===1;
  const content=first ? [{type:'toolCall',id:'image-call-1',name:'create_media',arguments:process.argv[5] ? JSON.parse(process.argv[5]) : {model:scenario === 'media-invalid-model' ? 'foreign' : input.media_models[0]?.id, prompt:'blue circle', ...(scenario.startsWith('speech') ? {capability:'audio.speech',parameters:{response_format:scenario==='speech-invalid'?'exe':'wav'}} : {})}}] : [{type:'text',text:'done'}];
  const final={role:'assistant',content,stopReason:first?'toolUse':'stop',api:'openai-responses',provider:'easel',model:'text-fixture',timestamp:Date.now(),usage};
  const s=createAssistantMessageEventStream();
  s.push({type:'start',partial:final});s.push({type:'done',reason:final.stopReason,message:final});s.end();return s;
}
await runAgent(input, {send:async event=>events.push(event),onAbort:()=>{},tool:async value=>{
  calls++;assert.equal(value.name,'create_media');assert.equal(value.arguments.model,input.media_models[0].id);
  return {content:[{type:'text',text:'synthetic owned image'}],details:{ok:true}};
}}, stream);
assert.equal(calls,['media','speech'].includes(scenario)?1:0);
assert(events.some(event=>event.type==='done'));
console.log(JSON.stringify({success:true,scenario,requests,calls}));
