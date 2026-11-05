"""One serialized refresh with a durable fence for an unknown response.

Callers hold the existing central session file lock. No token or response body
is logged, and an uncertain refresh is never automatically submitted again.
"""
from hashlib import sha256
import json
import logging
import os
import re
import time
from uuid import uuid4

from easel.harnesses.base import HarnessError
from .client import PlatformError

GUARD='refresh_guard'
AUTH_FAILURES=frozenset({'invalid_authentication','session_invalid','refresh_replayed'})
ACCESS_FAILURES=frozenset({'invalid_authentication','session_invalid'})


class RefreshUncertain(PlatformError):
    def __init__(self):
        super().__init__('refresh_uncertain',409)
        self.args=('登录状态待确认，请重新登录',)


def scope_hash(scope):
    return sha256(json.dumps(scope,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()


def diagnostic(code,status,path,scope):
    # Constant/validated metadata only. Never log exceptions, URLs, query, body,
    # access/refresh hashes or credentials, including on transport failures.
    code=code if isinstance(code,str)and re.fullmatch(r'[a-z_]{1,64}',code)else'auth_unavailable'
    path=path.split('?',1)[0]
    if not re.fullmatch(r'/v2/[A-Za-z0-9/_-]{1,240}',path):path='/v2/auth'
    status=status if type(status)is int and 100<=status<=599 else 0
    logging.getLogger('easel.auth').warning(json.dumps({'code':code,'status':status,'path':path,
        'scope_hash':scope_hash(scope),'time':time.time()},separators=(',',':')))


def guard_present(state):
    # Unknown/malformed guard contents also fail closed. Only an explicit local
    # logout/new login or a successful fenced replacement can remove one.
    return GUARD in state


def guard_matches(state):
    marker=state.get(GUARD);auth=state.get('auth')
    return bool(isinstance(marker,dict)and isinstance(auth,dict)
        and marker.get('scope_hash')==scope_hash(auth.get('scope'))
        and marker.get('origin_hash')==sha256(state.get('url','').encode()).hexdigest()
        and isinstance(auth.get('refresh_token'),str)
        and marker.get('refresh_hash')==sha256(auth['refresh_token'].encode()).hexdigest())


def write_durable(store,value):
    store.write(value)
    if os.name!='nt':
        descriptor=os.open(store.directory,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:os.fsync(descriptor)
        finally:os.close(descriptor)


def access(central,state,*,send,clock,force=False):
    auth=state.get('auth')
    if not auth:raise PlatformError('invalid_authentication',401)
    central._validate_auth(auth)
    if guard_present(state):raise RefreshUncertain()
    if auth['refresh_expires_at']<=clock():raise PlatformError('invalid_authentication',401)
    if not force and auth['expires_at']>clock()+30:return auth['access_token']
    marker={'version':1,'state':'sending','operation_id':str(uuid4()),'scope_hash':scope_hash(auth['scope']),
            'origin_hash':sha256(state['url'].encode()).hexdigest(),
            'refresh_hash':sha256(auth['refresh_token'].encode()).hexdigest(),'started_at':clock()}
    marked={**state,GUARD:marker}
    try:write_durable(central.store,marked)
    except(HarnessError,OSError,ValueError):
        diagnostic('refresh_guard_unavailable',503,'/v2/auth/refresh',auth['scope'])
        raise RefreshUncertain()from None
    state[GUARD]=marker
    try:
        fresh=send(auth['refresh_token'])
        central._validate_auth(fresh)
        if fresh['scope']!=auth['scope']or fresh['account']['id']!=auth['account']['id']:
            raise PlatformError('account_changed',409)
    except Exception as error:
        if isinstance(error,PlatformError)and error.status==401 and error.code in AUTH_FAILURES:
            state.pop('auth',None);state.pop(GUARD,None)
            write_durable(central.store,state)
            diagnostic(error.code,error.status,'/v2/auth/refresh',auth['scope'])
            raise
        uncertain={**marked,GUARD:{**marker,'state':'uncertain'}}
        try:write_durable(central.store,uncertain);state[GUARD]=uncertain[GUARD]
        except(HarnessError,OSError,ValueError):pass
        diagnostic('refresh_uncertain',getattr(error,'status',0),'/v2/auth/refresh',auth['scope'])
        if isinstance(error,(KeyboardInterrupt,SystemExit)):raise
        raise RefreshUncertain()from None
    replacement={**state,'auth':fresh};replacement.pop(GUARD,None)
    try:write_durable(central.store,replacement)
    except(HarnessError,OSError,ValueError):
        # Do not roll back to old credentials: replace/fsync may already have
        # committed. The actual file contains either new auth or the pre-POST
        # fence; a restart reads that outcome and cannot resend the old refresh.
        diagnostic('refresh_save_uncertain',503,'/v2/auth/refresh',auth['scope'])
        raise RefreshUncertain()from None
    state.clear();state.update(replacement)
    diagnostic('refresh_completed',200,'/v2/auth/refresh',auth['scope'])
    return fresh['access_token']


def access_error(error):
    if isinstance(error,PlatformError)and error.status==401:
        error.auth_phase='access'
    return error
