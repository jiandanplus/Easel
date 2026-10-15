import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import {runInNewContext} from 'node:vm';
import {test} from 'node:test';
const require=createRequire(new URL('../../web/frontend/package.json',import.meta.url));
const ts=require('typescript');
const source=readFileSync(new URL('../../web/frontend/src/lib/api.ts',import.meta.url),'utf8');
const {outputText}=ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS}});
function setup(response){
 const calls=[];const api={};
 runInNewContext(outputText,{exports:api,window:{location:{pathname:'/'}},Error,Object,fetch:async(url,options)=>{calls.push({url,options});return response}});
 return {api,calls};
}
test('account bridge submits credentials only to request body without browser storage',async()=>{
 const {api,calls}=setup({ok:true,json:async()=>({authenticated:true})});
 await api.platformAuthenticate('login',{email:'synthetic@example.invalid',password:'synthetic-password-123',device_name:'test'});
 assert.equal(calls[0].url,'/api/platform/login');
 assert.equal(JSON.parse(calls[0].options.body).password,'synthetic-password-123');
 assert.equal(calls[0].options.headers.Authorization,undefined);
 assert.equal(calls[0].options.cache,'no-store');
});
test('structured backend errors retain safe message and code without object stringification',async()=>{
 const {api}=setup({ok:false,status:401,statusText:'Unauthorized',json:async()=>({detail:{code:'invalid_authentication',message:'请重新登录'}})});
 await assert.rejects(api.platformStatus(),error=>error.message==='请重新登录'&&error.code==='invalid_authentication');
 assert.equal(api.apiErrorMessage({detail:[{input:'secret'}]}),'');
 assert.equal(api.apiErrorMessage({detail:'plain error'}),'plain error');
});
