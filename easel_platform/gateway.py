"""Fixed-destination Responses gateway with bounded accounting parser."""
import asyncio
import hashlib
import json
import secrets
import httpx
import anyio
from fastapi import Request
from fastapi.responses import JSONResponse,StreamingResponse
from .auth import PlatformError,digest
from .ledger import Ledger

MAX_REQUEST=1024*1024
MAX_EVENT=256*1024
MAX_JSON=2*1024*1024

async def read_body(request,limit):
    data=bytearray()
    async for chunk in request.stream():
        if len(data)+len(chunk)>limit:raise PlatformError(400,'invalid_request')
        data.extend(chunk)
    try:
        obj=json.loads(data)
        if not isinstance(obj,dict):raise ValueError()
        return obj
    except (ValueError,UnicodeError,RecursionError):raise PlatformError(400,'invalid_request') from None

def bearer(request):
    value=request.headers.get('authorization','')
    if not value.startswith('Bearer ') or len(value)>512 or not value.isascii():raise PlatformError(401,'invalid_authentication')
    return value[7:]

class Gateway:
    def __init__(self,store,transport=None):
        self.store,self.settings,self.transport=store,store.settings,transport
        self.ledger=Ledger(store)

    @property
    def ready(self):return all((self.settings.upstream_url,self.settings.upstream_key,self.settings.upstream_model,self.settings.public_model))

    def models(self,owner):
        allowed=any(g['status']=='active' and self.settings.public_model in g['models'] for g in self.store.entitlements(owner))
        return {'models':[{'id':self.settings.public_model}] if self.ready and allowed else [],'ready':bool(self.ready),'authentication_verified':False,'reason':None if self.ready else 'gateway_not_configured'}

    def mint(self,user,model):
        if not self.ready:raise PlatformError(503,'gateway_not_configured')
        if model!=self.settings.public_model:raise PlatformError(403,'model_not_allowed')
        token=secrets.token_urlsafe(32)
        with self.store.transaction() as db:
            s=db.execute('SELECT * FROM sessions WHERE id=? AND revoked=0 AND refresh_expires_at>?',(user['session_id'],self.store.clock())).fetchone()
            if not s:raise PlatformError(401,'invalid_authentication')
            grants=db.execute('SELECT * FROM grants WHERE account_id=? ORDER BY ends_at,id',(user['account_id'],)).fetchall()
            g=next((r for r in grants if r['status']=='active' and r['starts_at']<=self.store.clock()<r['ends_at'] and model in json.loads(r['models'])),None)
            if not g:raise PlatformError(403,'entitlement_required')
            end=min(self.store.clock()+3600,s['refresh_expires_at'],g['ends_at'])
            db.execute('INSERT INTO task_tokens VALUES(?,?,?,?,?,?)',(digest(token),user['account_id'],user['session_id'],g['id'],model,end))
        return {'token':token,'expires_at':end,'account_id':user['account_id'],'model':model,'base_url_path':'/v1'}

    def authorize(self,token):
        with self.store.transaction() as db:
            r=db.execute("SELECT t.* FROM task_tokens t JOIN sessions s ON s.id=t.session_id WHERE t.hash=? AND t.expires_at>? AND s.revoked=0 AND s.refresh_expires_at>?",(digest(token),self.store.clock(),self.store.clock())).fetchone()
            if not r:raise PlatformError(401,'invalid_authentication')
            self.ledger._grant(db,r['owner'],r['grant_id'],r['model'])
            return dict(r)

    async def close(self,response,client):
        # Cleanup completes even when the consumer's cancel scope is cancelled.
        with anyio.CancelScope(shield=True):
            try:
                if response is not None:await response.aclose()
            finally:await client.aclose()

    def usage(self,obj):
        u=obj.get('usage') if isinstance(obj,dict) else None
        if not isinstance(u,dict):raise PlatformError(502,'invalid_upstream_usage')
        a,b=u.get('input_tokens'),u.get('output_tokens')
        self.ledger.validate_usage(a,b)
        return a,b

    @staticmethod
    def text_only(obj):
        """Byte admission is valid only for stateless text and client executed tools."""
        def reject():raise PlatformError(400,'unsupported_request_shape')
        if any(k in obj for k in ('previous_response_id','conversation','prompt')) or obj.get('background',False) is not False:reject()
        tools=obj.get('tools',[])
        if not isinstance(tools,list) or any(not isinstance(t,dict) or t.get('type') not in ('function','custom') for t in tools):reject()
        choice=obj.get('tool_choice')
        if isinstance(choice,dict) and choice.get('type') not in ('function','custom'):reject()
        value=obj.get('input','')
        if not isinstance(value,(str,list)):reject()
        allowed={'message','input_text','output_text','function_call','function_call_output','custom_tool_call','custom_tool_call_output','reasoning','summary_text','reasoning_text'}
        pending=[value]
        while pending:
            item=pending.pop()
            if isinstance(item,list):pending.extend(item)
            elif isinstance(item,dict):
                if any(k in item for k in ('file_id','image_url','file_url','audio','video')):reject()
                if 'type' in item and item['type'] not in allowed:reject()
                pending.extend(item.values())

    async def responses(self,request):
        task=self.authorize(bearer(request))
        if not self.ready:raise PlatformError(503,'gateway_not_configured')
        obj=await read_body(request,MAX_REQUEST)
        if obj.get('model')!=task['model']:raise PlatformError(403,'model_not_allowed')
        if type(obj.get('stream',False)) is not bool:raise PlatformError(400,'invalid_request')
        self.text_only(obj)
        bound=obj.get('max_output_tokens',4096)
        if type(bound) is not int or not 1<=bound<=4096:raise PlatformError(400,'invalid_request')
        # Only the Responses schema is forwarded; routing overrides are rejected.
        if any(k in obj for k in ('base_url','upstream_url','api_key','authorization')):raise PlatformError(400,'invalid_request')
        obj['max_output_tokens']=bound
        try:canonical=json.dumps(obj,ensure_ascii=False,separators=(',',':'),sort_keys=True,allow_nan=False).encode('utf-8')
        except (ValueError,UnicodeError,RecursionError):raise PlatformError(400,'invalid_request') from None
        obj['model']=self.settings.upstream_model
        payload=json.dumps(obj,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode('utf-8')
        key=request.headers.get('idempotency-key') or secrets.token_hex(16)
        if len(key)>256 or not key.isascii() or any(ord(c)<32 for c in key):raise PlatformError(400,'invalid_request')
        # Reauthorize after body receipt; reserve atomically rechecks grant/session.
        task=self.authorize(bearer(request))
        record=self.ledger.reserve(owner=task['owner'],grant=task['grant_id'],model=task['model'],session_id=task['session_id'],key=key,body_hash=hashlib.sha256(canonical).hexdigest(),tokens=max(len(canonical),len(payload))+bound)
        rid=record['id']
        client=None
        response=None
        dispatched=False
        try:
            client=httpx.AsyncClient(transport=self.transport,trust_env=False,follow_redirects=False,timeout=httpx.Timeout(60,connect=10))
            self.ledger.dispatch(rid)
            dispatched=True
            response=await client.send(client.build_request('POST',self.settings.upstream_url.rstrip('/')+'/responses',content=payload,headers={'Authorization':'Bearer '+self.settings.upstream_key,'Content-Type':'application/json','Accept':'text/event-stream' if obj.get('stream') else 'application/json'}),stream=True)
            if response.status_code!=200:raise PlatformError(502,'upstream_error')
            if obj.get('stream'):
                return StreamingResponse(self.stream(response,client,rid),media_type='text/event-stream',headers={'X-Request-ID':rid})
            raw=bytearray()
            async for chunk in response.aiter_bytes():
                if len(raw)+len(chunk)>MAX_JSON:raise PlatformError(502,'upstream_response_too_large')
                raw.extend(chunk)
            try:result=json.loads(raw)
            except (ValueError,UnicodeError):raise PlatformError(502,'invalid_upstream_response') from None
            counts=self.usage(result)
            if result.get('status') not in (None,'completed'):raise PlatformError(502,'upstream_error')
            await self.close(response,client)
            self.ledger.settle(rid,*counts)
            return JSONResponse(result,headers={'X-Request-ID':rid})
        except BaseException as exc:
            try:
                if client is not None:await self.close(response,client)
            finally:
                if dispatched:self.ledger.pending(rid)
                else:self.ledger.release(rid)
            if isinstance(exc,(asyncio.CancelledError,KeyboardInterrupt,SystemExit)):raise
            if isinstance(exc,PlatformError):raise
            raise PlatformError(502,'upstream_unavailable') from None

    async def stream(self,response,client,rid):
        complete=False
        buffer=bytearray()
        try:
            async for chunk in response.aiter_bytes():
                # Byte parsing keeps split UTF-8 sequences intact and bounds each event.
                for start in range(0,len(chunk),8192):
                    buffer.extend(chunk[start:start+8192])
                    while True:
                        # Normalize CRLF only after a complete delimiter exists.
                        end=buffer.find(b'\n\n');width=2
                        cr=buffer.find(b'\r\n\r\n')
                        if cr>=0 and (end<0 or cr<end):end,width=cr,4
                        if end<0:break
                        if end>MAX_EVENT:raise PlatformError(502,'upstream_event_too_large')
                        event=bytes(buffer[:end+width]);del buffer[:end+width]
                        lines=event.decode('utf-8').splitlines()
                        names=[line[6:].strip() for line in lines if line.startswith('event:')]
                        name=names[-1] if names else None
                        data='\n'.join(line[5:].lstrip(' ') for line in lines if line.startswith('data:'))
                        if not data:
                            if name in ('response.completed','response.failed','response.incomplete','error'):raise PlatformError(502,'invalid_upstream_usage')
                            yield event;continue
                        if data=='[DONE]':raise PlatformError(502,'invalid_upstream_usage')
                        try:value=json.loads(data)
                        except ValueError:raise PlatformError(502,'invalid_upstream_response') from None
                        if not isinstance(value,dict):raise PlatformError(502,'invalid_upstream_response')
                        kind=value.get('type')
                        if name and name!='message' and name!=kind:raise PlatformError(502,'invalid_upstream_response')
                        if kind=='response.completed':
                            counts=self.usage(value.get('response'))
                            await self.close(response,client)
                            self.ledger.settle(rid,*counts)
                            complete=True
                            yield event
                            return
                        if kind in ('error','response.failed','response.incomplete'):raise PlatformError(502,'upstream_error')
                        yield event
                    if len(buffer)>MAX_EVENT:raise PlatformError(502,'upstream_event_too_large')
            raise PlatformError(502,'invalid_upstream_usage')
        except (Exception,) as exc:
            code=exc.code if isinstance(exc,PlatformError) else 'upstream_unavailable'
            yield ('event: error\ndata: '+json.dumps({'type':'error','error':{'code':code}})+'\n\n').encode()
        finally:
            await self.close(response,client)
            if not complete:self.ledger.pending(rid)
