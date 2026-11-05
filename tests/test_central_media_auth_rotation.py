"""Synthetic real-shape auth responses; never reads a user's credential file."""
from datetime import datetime,timezone
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import threading
import time

import httpx
import pytest

from easel.platform.central import CentralClient
from easel.platform.media import MediaClient
from tests.test_central_platform_client import auth
from tests.test_central_media_host import ORIGIN,definition


def test_media_refresh_then_text_refresh_keeps_rotated_private_credentials_and_scope(tmp_path):
    current=[time.time()];generation=[0];calls=[]
    latest={'access':'ACCESS_INITIAL_SYNTHETIC_000000000000','refresh':'REFRESH_INITIAL_SYNTHETIC_000000000000'}
    original_scope=auth()['scope']
    def response_auth(*,expired=False):
        result=auth();result.update(access_token=latest['access'],refresh_token=latest['refresh'],
            expires_at=datetime.fromtimestamp(current[0]+(-1 if expired else 45),timezone.utc).isoformat(),
            refresh_expires_at=datetime.fromtimestamp(current[0]+1200,timezone.utc).isoformat())
        return result
    def handler(request):
        calls.append((request.method,request.url.path))
        if request.url.path=='/v2/auth/refresh':
            assert 'authorization'not in request.headers
            assert json.loads(request.content)=={'refresh_token':latest['refresh']}
            generation[0]+=1
            latest.update(access=f'ACCESS_ROTATED_SYNTHETIC_{generation[0]:016}',refresh=f'REFRESH_ROTATED_SYNTHETIC_{generation[0]:016}')
            return httpx.Response(200,json=response_auth())
        assert request.headers['authorization']=='Bearer '+latest['access']
        if request.url.path=='/v2/me':return httpx.Response(200,json={'scope':original_scope,'account':auth()['account']})
        assert request.url.path=='/v2/products/easel/media-models'
        return httpx.Response(200,json={'models':[definition()]})
    central=CentralClient(tmp_path,transport=httpx.MockTransport(handler),url=ORIGIN,clock=lambda:current[0])
    central.save_auth(response_auth(expired=True))
    media=MediaClient(tmp_path,central=central,clock=lambda:current[0])
    catalog=media.catalog()
    first=central.store.read()['auth']
    assert first['scope']==original_scope and first['refresh_token']==latest['refresh']
    assert first['access_token']==latest['access']and generation[0]==1
    current[0]+=20
    status=central.status()
    assert status['authenticated']and status['scope']==original_scope
    saved=central.store.read()['auth']
    assert saved['refresh_token']==latest['refresh']and saved['access_token']==latest['access']
    assert generation[0]==2 and saved['refresh_token']!=first['refresh_token']
    assert calls.count(('POST','/v2/auth/refresh'))==2
    assert all(path in{'/v2/auth/refresh','/v2/me','/v2/products/easel/media-models'}for _,path in calls)
    public=json.dumps({'catalog':catalog,'status':status})
    assert all(token not in public for token in[first['access_token'],first['refresh_token'],latest['access'],latest['refresh']])


@contextmanager
def rotation_server(fault):
    """A real HTTP boundary with the current server's consume/replay policy."""
    state={'consumed':False,'revoked':False,'posts':0}
    committed=threading.Event();release=threading.Event()
    initial=auth();initial['expires_at']=0
    fresh=auth();fresh.update(access_token='ACCESS_FRESH_SYNTHETIC_AFTER_COMMIT',refresh_token='REFRESH_FRESH_SYNTHETIC_AFTER_COMMIT')
    for key in('expires_at','refresh_expires_at'):
        fresh[key]=datetime.fromtimestamp(fresh[key],timezone.utc).isoformat()
    class Handler(BaseHTTPRequestHandler):
        protocol_version='HTTP/1.1'
        def log_message(self,*args):pass
        def send_json(self,status,value):
            raw=json.dumps(value).encode();self.send_response(status);self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw);self.wfile.flush()
        def do_POST(self):
            assert self.path=='/v2/auth/refresh'
            value=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            assert value=={'refresh_token':initial['refresh_token']}
            state['posts']+=1
            if state['consumed']:
                state['revoked']=True
                return self.send_json(401,{'error':{'code':'refresh_replayed'}})
            # Models IdentityService.refresh's committed consumed=true + mint.
            state['consumed']=True;committed.set()
            raw=json.dumps(fresh).encode()
            self.send_response(200);self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(raw)));self.end_headers()
            if fault=='none':self.wfile.write(raw);self.wfile.flush();return
            self.wfile.write(raw[:20]);self.wfile.flush()
            if fault=='stall':release.wait(2)
            self.close_connection=True
        def do_GET(self):
            assert self.path=='/v2/me'and self.headers['Authorization']=='Bearer '+fresh['access_token']
            self.send_json(200,{'scope':fresh['scope'],'account':fresh['account']})
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.daemon_threads=True
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:yield server,state,committed,initial,fresh
    finally:release.set();server.shutdown();server.server_close();thread.join(2)


@pytest.mark.parametrize('fault',['stall','truncated'])
@pytest.mark.parametrize('retry_client',['media','central'])
def test_committed_refresh_response_loss_is_fenced_before_any_automatic_reuse(tmp_path,fault,retry_client):
    """The original v22 reproduction is archived; current clients must not replay."""
    from easel.platform.client import PlatformError
    with rotation_server(fault)as(server,state,committed,initial,fresh):
        url=f'http://127.0.0.1:{server.server_port}'
        central=CentralClient(tmp_path,url=url);central.save_auth(initial)
        media=MediaClient(tmp_path,central=central)
        before=central.store.path.read_bytes()
        with pytest.raises(PlatformError):media._snapshot(deadline=time.monotonic()+.15)
        assert committed.wait(1)and state['consumed']and not state['revoked']
        saved=central.store.read()
        assert saved['auth']==json.loads(before)['auth']and state['posts']==1
        assert saved['refresh_guard']['state']in{'sending','uncertain'}
        if retry_client=='media':
            with pytest.raises(PlatformError)as error:media.catalog()
            assert error.value.code=='refresh_uncertain'
        else:
            result=central.status();assert not result['authenticated']and result['error']['code']=='refresh_uncertain'
        assert not state['revoked']and state['posts']==1
        assert central.store.read()['auth']==saved['auth']


def test_complete_real_http_refresh_commits_new_pair_without_replay_or_revocation(tmp_path):
    with rotation_server('none')as(server,state,committed,initial,fresh):
        central=CentralClient(tmp_path,url=f'http://127.0.0.1:{server.server_port}');central.save_auth(initial)
        media=MediaClient(tmp_path,central=central)
        assert media._snapshot()['token']==fresh['access_token']
        assert central.status()['authenticated']
        assert state['posts']==1 and state['consumed']and not state['revoked']
        assert central.store.read()['auth']['refresh_token']==fresh['refresh_token']
