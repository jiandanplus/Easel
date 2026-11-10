"""Actual installed main must refuse invocation before READY or SQL."""
from unittest.mock import patch
from platform_service.media_backup_b3 import reconstruction_worker as w, execution_worker as old

def test_actual_main_rejects_wrong_interpreter_before_ready():
    with patch('sys.argv',['worker','reconstruct_worker']),patch('sys.executable','/wrong/python'),patch.object(old,'_write_result') as write,patch.object(w,'_connect') as connect:
        assert w.main()==1
        write.assert_not_called();connect.assert_not_called()

def test_entry_deadline_cannot_restart_at_ready():
    with patch.object(w,'_clock',return_value=100.):
        b=w._Budget()
        assert b.deadline is not None, 'pre-request deadline must exist before READY'

import pytest

@pytest.mark.parametrize('fault',[None,'hash','writable','alias','main','callable','fileless','pq','map_inode','deleted','cap','memory','netns','late_module','network_cap','replacement','symlink_parent','frame_zero','frame_oversize','frame_duplicate','frame_trailing','frame_truncated','frame_missing_eof','frame_fragmented','io_interrupt','deadline_sql','deadline_post','cache','namespace','second_entry','connection','adapter','nonexec_client','uid','nnp','swap','pids','cpu'])
def test_actual_main_full_measured_binary_chain(tmp_path,fault):
    import os,json,threading
    from reconstruction_installed_fixture import Installed
    from test_media_backup_b3_reconstruction_worker import _session_fixture
    from platform_service.media_backup_b3 import reconstruction_contract as c
    request,connection=_session_fixture(w)
    clock=[100.]
    fake=Installed(tmp_path/'installed',lambda **kwargs:connection,clock=lambda:clock[0])
    from test_media_backup_b3_reconstruction_contract import document
    d=document()
    d['binding']=request.binding;d['identity_before']=request.identity_before;d['build'].update(fake.build())
    raw=c.encode_request(d)
    from platform_service.media_backup_b3 import reconstruction_origin as o
    import types
    if fault=='replacement':
        opened={};replaced=[False];open_leaf=fake.os.open;read_leaf=fake.os.read
        def tracking_open(path,*args,**kwargs):
            fd=open_leaf(path,*args,**kwargs);opened[fd]=path;return fd
        def replacing_read(fd,size):
            data=read_leaf(fd,size)
            if opened.get(fd)=='artifact.whl' and data and not replaced[0]:
                replaced[0]=True;(fake.root/o._ARTIFACT[1:]).unlink();fake.put(o._ARTIFACT,b'public synthetic bytes',False)
            return data
        fake.os.open=tracking_open;fake.os.read=replacing_read
    elif fault=='symlink_parent':
        parent=fake.root/'usr/local/lib';parent.rename(parent.with_name('lib-real'));parent.symlink_to('lib-real')
    elif fault=='frame_missing_eof':
        read=fake.os.read;consumed=[0]
        def missing_eof(fd,size):
            data=read(fd,size)
            if fd==0:
                consumed[0]+=len(data)
                if consumed[0]==len(raw):clock[0]=161.
            return data
        fake.os.read=missing_eof
    elif fault=='cache':fake.client.__cached__='/tmp/unapproved.pyc'
    elif fault=='namespace':
        import importlib.machinery
        module=types.ModuleType('namespace');module.__spec__=importlib.machinery.ModuleSpec('namespace',None)
        module.__spec__.submodule_search_locations=['/tmp/unapproved'];fake.modules['namespace']=module
    elif fault=='second_entry':fake.modules['platform_service.media_backup_b3.reconstruction_worker']=fake.worker
    elif fault=='connection':fake.client.connect=lambda **kwargs:connection
    elif fault=='adapter':fake.binary._psycopg.__spec__.origin='/tmp/adapter.so'
    elif fault=='nonexec_client':fake.put('/proc/self/maps',fake.maps.replace('r-xp','r--p').encode(),False)
    elif fault=='uid':fake.os.getuid=lambda:0
    elif fault=='nnp':fake.put('/proc/self/status',fake.status.replace('NoNewPrivs:\t1','NoNewPrivs:\t0').encode(),False)
    elif fault=='swap':fake.put('/sys/fs/cgroup/memory.swap.max',b'1\n',False)
    elif fault=='pids':fake.put('/sys/fs/cgroup/pids.max',b'1\n',False)
    elif fault=='cpu':fake.put('/sys/fs/cgroup/cpu.max',b'1 100000\n',False)
    elif fault=='frame_zero':raw=b'\0\0\0\0'
    elif fault=='frame_oversize':raw=(c.REQUEST_LIMIT+1).to_bytes(4,'big')
    elif fault=='frame_duplicate':
        payload=b'{"format":"duplicate",'+raw[5:];raw=len(payload).to_bytes(4,'big')+payload
    elif fault=='frame_trailing':raw+=b'x'
    elif fault=='frame_truncated':raw=raw[:-1]
    elif fault=='io_interrupt':
        import errno
        write=fake.os.write;read=fake.os.read;attempts=[0,0]
        def interrupted_write(fd,data):
            attempts[0]+=1
            if attempts[0]==1:raise BlockingIOError(errno.EAGAIN,'public synthetic retry')
            if attempts[0]==2:raise InterruptedError()
            return write(fd,data)
        def interrupted_read(fd,size):
            attempts[1]+=1
            if attempts[1]==1:raise InterruptedError()
            return read(fd,size)
        fake.os.write=interrupted_write;fake.os.read=interrupted_read
    elif fault=='deadline_sql':
        def expired(**kwargs):clock[0]=161.;return connection
        fake.connection.__dict__['_connect_leaf']=expired
    elif fault=='deadline_post':
        close=connection.close
        def expired_close():close();clock[0]=161.
        connection.close=expired_close
    elif fault=='network_cap':fake.origin._NETWORK_LIMIT=1
    elif fault=='hash':fake.put(o._ARTIFACT,b'changed bytes',False)
    elif fault=='writable':(fake.root/o._ARTIFACT[1:]).chmod(0o666)
    elif fault=='alias':
        p=fake.root/o._ALIAS[1:];p.unlink();p.symlink_to('/wrong/executable')
    elif fault=='main':fake.worker.__spec__.origin='/wrong/entry.py'
    elif fault=='callable':fake.worker._roles_apply=lambda *a:None
    elif fault=='fileless':fake.modules['injected']=types.ModuleType('injected')
    elif fault=='pq':fake.client.pq.__impl__='python'
    elif fault=='map_inode':fake.put('/proc/self/maps','\n'.join(' '.join(row.split()[:4]+[str(int(row.split()[4])+1)]+row.split()[5:]) for row in fake.maps.splitlines()).encode(),False)
    elif fault=='deleted':fake.put('/proc/self/maps',fake.maps.replace(o._CLIENT[-1],o._CLIENT[-1]+' (deleted)').encode(),False)
    elif fault=='cap':fake.put('/proc/self/status',fake.status.replace('CapBnd:\t0000000000000000','CapBnd:\t0000000000000001').encode(),False)
    elif fault=='memory':fake.put('/sys/fs/cgroup/memory.max',b'1\n',False)
    elif fault=='netns':fake.os.stat=lambda *a,**k:types.SimpleNamespace(st_dev=1,st_ino=2) if a[0]=='/proc/self/ns/net' else fake.stat(*a,**k)
    elif fault=='late_module':
        def late(**kwargs):fake.modules['late']=types.ModuleType('late');return connection
        fake.connection.__dict__['_connect_leaf']=late
    rin,win=os.pipe();rout,wout=os.pipe();rerr,werr=os.pipe()
    fake.fds=(rin,wout,werr)
    result=[]
    def run():
        result.append(fake.worker.main());os.close(wout);os.close(werr)
    t=threading.Thread(target=run);t.start()
    try:
        ready=os.read(rout,len(w.READY_FRAME));assert ready==w.READY_FRAME
        if fault=='frame_fragmented':
            for i in range(0,len(raw),7):os.write(win,raw[i:i+7])
        else:os.write(win,raw)
        if fault!='frame_missing_eof':os.close(win);win=None
        out=bytearray()
        while True:
            chunk=os.read(rout,65536)
            if not chunk:break
            out.extend(chunk)
        t.join(2)
        success=fault in (None,'frame_fragmented','io_interrupt','nonexec_client')
        assert result==([0] if success else [1])
        assert os.read(rerr,100)==b''
        if success:
            decoded=c.decode_result(bytes(out),raw)
            assert decoded['installed_evidence']['pq_implementation']=='binary'
            assert connection.commits==1
        else:
            assert out==b''
            assert connection.commits==(1 if fault in ('late_module','deadline_post') else 0)

    finally:
        for fd in (rin,win,rout,rerr):
            if fd is not None:os.close(fd)
        t.join(2)

def test_synthetic_wait_preserves_virtual_fd_above_select_limit(tmp_path):
    import os,fcntl
    from reconstruction_installed_fixture import Installed
    fake=Installed(tmp_path/'highfd',lambda **kw:None)
    r,w=os.pipe();high=fcntl.fcntl(r,fcntl.F_DUPFD,1100)
    try:
        fake.fds=(high,w,2)
        assert fake.wait([0],[],[],0)==([],[],[])
        os.write(w,b'x')
        assert fake.wait([0],[],[],0)==([0],[],[])
        assert os.read(high,1)==b'x'
    finally:os.close(r);os.close(w);os.close(high)


@pytest.mark.parametrize('field',['argv','platform','architecture','isolated','bytecode','tty','entry_expired'])
def test_actual_main_invocation_and_entry_budget_before_ready(tmp_path,field):
    from reconstruction_installed_fixture import Installed
    clock=[100.];calls=[]
    f=Installed(tmp_path/'entry',lambda **kw:calls.append(kw),clock=lambda:clock[0])
    writes=[];f.os.write=lambda fd,data:writes.append((fd,len(data)))
    if field=='argv':f.sys.argv=['worker','attest_worker']
    elif field=='platform':f.sys.platform='darwin'
    elif field=='architecture':f.platform.machine=lambda:'x86_64'
    elif field=='isolated':f.sys.flags.isolated=0
    elif field=='bytecode':f.sys.flags.dont_write_bytecode=0
    elif field=='tty':f.os.isatty=lambda fd:True
    else:clock[0]=161.
    assert f.worker.main()==1 and not writes and not calls
