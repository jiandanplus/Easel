/** Real Pi 1.4 → OpenAI SDK serialization → strict synthetic fetch gateway. */
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { pathToFileURL } from 'node:url';

const directory=process.argv[2],config=JSON.parse(await readFile(process.argv[3],'utf8'));
let unexpectedNetworkRequests=0;
globalThis.fetch=async()=>{unexpectedNetworkRequests++;throw new Error('nonmock_fetch_forbidden');};
const { runAgent }=await import(pathToFileURL(directory+'/worker.mjs'));
const { streamSimple }=await import(pathToFileURL(directory+'/node_modules/@earendil-works/pi-ai/dist/api/openai-responses.js'));
const statuses=[],schemas=[],events=[],hostRequests=[];
const usage={input_tokens:3,output_tokens:2,total_tokens:5};
let calls=0;

function streamBody(args) {
  const index=calls;
  const response={id:'resp_synthetic_'+index,status:'completed',usage,output:[]};
  let item;
  if(args) {
    item={type:'function_call',id:'fc_synthetic_'+index,call_id:'call_synthetic_'+index,
      name:'create_media',arguments:JSON.stringify(args)};
  } else {
    item={type:'message',id:'msg_synthetic_'+index,role:'assistant',status:'completed',
      content:[{type:'output_text',text:'Synthetic completion',annotations:[]}]};
  }
  response.output=[item];
  const list=[{type:'response.created',response:{...response,status:'in_progress',output:[]}},
    {type:'response.output_item.added',output_index:0,item:args?{...item,arguments:''}:{...item,content:[]}}];
  if(args)list.push({type:'response.function_call_arguments.delta',output_index:0,
                     item_id:item.id,delta:item.arguments});
  else list.push({type:'response.output_text.delta',output_index:0,item_id:item.id,
                  content_index:0,delta:'Synthetic completion'});
  list.push({type:'response.output_item.done',output_index:0,item},
            {type:'response.completed',response});
  return list.map(event=>'event: '+event.type+'\ndata: '+JSON.stringify(event)+'\n\n').join('');
}

async function gateway(input,init) {
  const request=new Request(input,init);
  assert.equal(request.method,'POST');
  assert.equal(request.url,'https://strict-synthetic-gateway.invalid/v1/responses');
  const payload=await request.json();
  calls++;
  const tools=payload.tools??[];
  schemas.push(tools.map(tool=>({name:tool.name,parameters:tool.parameters})));
  const bad=tools.find(tool=>tool.type==='function'&&tool.parameters?.type!=='object');
  if(bad) {
    statuses.push(400);
    return new Response(JSON.stringify({error:{type:'invalid_request_error',code:'tool_schema_root_object_required',
      param:bad.name+'.type',message:'Synthetic strict gateway requires object tool root'}}),
      {status:400,headers:{'content-type':'application/json','X-Oneapi-Request-Id':'synthetic-root-schema'}});
  }
  statuses.push(200);
  return new Response(streamBody(calls===1?config.arguments:null),
    {status:200,headers:{'content-type':'text/event-stream'}});
}

const input={type:'run',id:'synthetic-root-schema',session_id:'pi:00000000-0000-4000-8000-000000000002',
  system_prompt:'Synthetic schema acceptance only',prompt:'Synthetic task',messages:[],
  api_key:'SYNTHETIC_ONLY',tools_enabled:config.tools_enabled??true,
  model:{id:'deepseek-synthetic',baseUrl:'https://strict-synthetic-gateway.invalid/v1',maxTokens:512,contextWindow:8192},
  media_models:config.models};
await runAgent(input,{send:async value=>events.push(value),onAbort:()=>{},tool:async value=>{
  hostRequests.push({name:value.name,arguments:value.arguments});
  return {content:[{type:'text',text:'Synthetic host result'}],details:{ok:true}};
}},(model,context,options)=>streamSimple(model,context,{...options,fetch:gateway}));
console.log(JSON.stringify({statuses,schemas,host_requests:hostRequests,
  done:events.some(event=>event.type==='done'&&event.success===true),
  errors:events.filter(event=>event.type==='error').map(event=>event.code),
  real_network_requests:unexpectedNetworkRequests}));
