"""Isolated synthetic -m globals and leaf OS/client inputs. No subprocess.

Executes current source bytes at the fixed installed code filenames. The fake
filesystem uses real temporary FDs and inode identities. Production verification,
protocol, dispatch, SQL session and host custody methods are never replaced.
"""
import builtins
import hashlib
import importlib
import importlib.machinery
import os
from pathlib import Path
import stat
import types
import json
from platform_service.media_backup_b3 import reconstruction_origin as constants
from test_media_backup_b3_network_fixtures import observation

class Installed:
    def __init__(self,root,connect,clock=lambda:100.):
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True)
        self.connect=connect;self.clock=clock;self.fds=None;self.modules={};self.network=observation()
        self.real_os=os;self.files={};self.maps='';self.status=''
        self.os=types.SimpleNamespace(**{n:getattr(os,n) for n in dir(os) if not n.startswith('__')})
        self.os.open=self.open;self.os.stat=self.stat;self.os.lstat=lambda p:self.stat(p,follow_symlinks=False)
        self.os.fstat=lambda fd:self.owned(os.fstat(fd))
        self.os.getuid=self.os.geteuid=self.os.getgid=self.os.getegid=lambda:10001
        self.os.getgroups=lambda:[10001];self.os.isatty=lambda fd:False
        self.os.readlink=self.readlink
        self.os.read=lambda fd,n:os.read(self.fds[0] if fd==0 else fd,n)
        self.os.write=lambda fd,data:os.write(self.fds[fd] if fd in (1,2) else fd,data)
        self.os.set_blocking=lambda fd,v:os.set_blocking(self.fds[fd] if fd in (0,1,2) else fd,v)
        self.os.get_blocking=lambda fd:os.get_blocking(self.fds[fd] if fd in (0,1,2) else fd)
        self.select=types.SimpleNamespace(select=self.wait)
        self.sys=types.SimpleNamespace(argv=[constants._ENTRY,'reconstruct_worker'],platform='linux',
            executable=constants._ALIAS,flags=types.SimpleNamespace(isolated=1,dont_write_bytecode=1),modules=self.modules)
        self.time=types.SimpleNamespace(monotonic=clock,monotonic_ns=lambda:int(clock()*10**9))
        self.platform=types.SimpleNamespace(machine=lambda:'aarch64')
        import psycopg.sql
        self.client=types.ModuleType('psycopg');self.client.sql=psycopg.sql
        self.connection=types.ModuleType('psycopg.connection')
        self.connection.__dict__['_connect_leaf']=connect
        exec(compile('class Connection:\n @classmethod\n def connect(cls,**kwargs):return _connect_leaf(**kwargs)\n',constants._BASE+'psycopg/connection.py','exec'),self.connection.__dict__)
        self.client.Connection=self.connection.Connection;self.client.connect=self.connection.Connection.connect
        self.client.connection=self.connection
        self.binary=types.ModuleType('psycopg_binary');self.binary.pq=types.ModuleType('psycopg_binary.pq')
        self.binary._psycopg=types.ModuleType('psycopg_binary._psycopg');self.binary.pq.PGconn=type('PGconn',(),{})
        self.client.pq=types.ModuleType('psycopg.pq');self.client.pq.__impl__='binary';self.client.pq.PGconn=self.binary.pq.PGconn
        for module,path in zip((self.client,self.binary,self.binary.pq,self.binary._psycopg),constants._CLIENT[:4]):self.identity(module,path)
        self.identity(self.connection,constants._BASE+'psycopg/connection.py')
        self.identity(self.client.pq,constants._BASE+'psycopg/pq/__init__.py')
        self.loaded={}
        for name in ('reconstruction_sql','execution_worker','reconstruction_origin','reconstruction_worker'):
            source=Path(constants.__file__).with_name(name+'.py').read_bytes()
            module=types.ModuleType('__main__' if name=='reconstruction_worker' else 'platform_service.media_backup_b3.'+name)
            module.__package__='platform_service.media_backup_b3'
            path=constants._PACKAGE+name+'.py';self.identity(module,path)
            if name=='reconstruction_worker':module.__spec__.name='platform_service.media_backup_b3.reconstruction_worker'
            module.__dict__['__builtins__']=dict(vars(builtins),__import__=self.importer)
            self.loaded[name]=module
            # Suppress only the standard module invocation footer during loading;
            # the test calls the actual main after wiring real synthetic pipes.
            code=source.decode().rsplit("if __name__",1)[0] if name in ('reconstruction_worker','execution_worker') else source.decode()
            exec(compile(code,path,'exec'),module.__dict__)
        self.worker=self.loaded['reconstruction_worker'];self.origin=self.loaded['reconstruction_origin']
        for path in set(constants._PROGRAM+constants._CLIENT+(constants._ARTIFACT,constants._EXE,constants._LEGACY,
                constants._PACKAGE+'execution_worker.py',constants._BASE+'psycopg/connection.py',constants._BASE+'psycopg/pq/__init__.py')):
            source=Path(constants.__file__).with_name(Path(path).name)
            data=source.read_bytes() if path.startswith(constants._PACKAGE) and source.exists() else b'public synthetic bytes'
            self.put(path,data)
        alias=self.root/constants._ALIAS[1:];alias.parent.mkdir(parents=True,exist_ok=True);alias.symlink_to(constants._EXE)
        self.manifest=constants._manifest(self.files)
        self.manifest_raw=json.dumps(self.manifest,sort_keys=True,separators=(',',':')).encode()
        self.put(constants._BUILD,self.manifest_raw,inventory=False)
        self.status='Uid:\t10001 10001 10001 10001\nGid:\t10001 10001 10001 10001\nGroups:\t10001\nNoNewPrivs:\t1\n'+''.join(k+':\t0000000000000000\n' for k in ('CapInh','CapPrm','CapEff','CapBnd','CapAmb'))
        self.put('/proc/self/status',self.status.encode(),False);self.put('/proc/self/cgroup',b'0::/\n',False)
        for n,v in (('memory.max',256*1024**2),('memory.swap.max',0),('pids.max',64),('cpu.max','100000 100000')):self.put('/sys/fs/cgroup/'+n,(str(v)+'\n').encode(),False)
        lines=[]
        for i,path in enumerate(constants._CLIENT[2:]+(constants._EXE,)):
            s=os.stat(self.root/path[1:]);lines.append(f'{4096+i*4096:x}-{8192+i*4096:x} r-xp 00000000 {os.major(s.st_dev):x}:{os.minor(s.st_dev):x} {s.st_ino} {path}')
        self.maps='\n'.join(lines)+'\n';self.put('/proc/self/maps',self.maps.encode(),False)
    def wait(self,reads,writes,errors,timeout):
        # Real installed select sees fixed fd0/1. Synthetic originals may retain
        # host FDs above FD_SETSIZE; Kqueue preserves bounded readiness semantics.
        import selectors
        assert not errors
        with selectors.DefaultSelector() as selector:
            for fd in reads:selector.register(self.fds[fd] if fd in (0,1,2) else fd,selectors.EVENT_READ,fd)
            for fd in writes:selector.register(self.fds[fd] if fd in (0,1,2) else fd,selectors.EVENT_WRITE,fd)
            events=selector.select(timeout)
            return ([k.data for k,mask in events if mask&selectors.EVENT_READ],
                    [k.data for k,mask in events if mask&selectors.EVENT_WRITE],[])
    def identity(self,module,path):
        module.__file__=path;module.__spec__=importlib.machinery.ModuleSpec(module.__name__,None,origin=path)
        self.modules[module.__name__]=module
    def put(self,path,data,inventory=True):
        p=self.root/path[1:];p.parent.mkdir(parents=True,exist_ok=True)
        if p.exists():p.chmod(0o644)
        p.write_bytes(data);p.chmod(0o444)
        if inventory:self.files[path]=dict(sha256=hashlib.sha256(data).hexdigest(),size=len(data),mode=0o444)
    def owned(self,s):
        keys=('st_dev','st_ino','st_mode','st_nlink','st_size','st_mtime_ns','st_ctime_ns')
        return types.SimpleNamespace(**{k:getattr(s,k) for k in keys},st_uid=0,st_gid=0)
    def open(self,path,flags,*args,**kw):
        if isinstance(path,str) and path.startswith('/'):
            path=str(self.root/path[1:]) if path!='/' else str(self.root)
        return os.open(path,flags,*args,**kw)
    def stat(self,path,*args,**kw):
        if path=='/proc/self/ns/net':return types.SimpleNamespace(st_dev=self.network.namespace_before.device,st_ino=self.network.namespace_before.inode)
        if path=='/proc/self/exe':path=constants._EXE
        if isinstance(path,str) and path.startswith('/'):path=str(self.root/path[1:])
        return self.owned(os.stat(path,*args,**kw))
    def readlink(self,path,*args,**kw):
        if path=='/proc/self/exe':return constants._EXE
        if path.startswith('/'):path=str(self.root/path[1:])
        return os.readlink(path,*args,**kw)
    def importer(self,name,globals=None,locals=None,fromlist=(),level=0):
        if name=='os':return self.os
        if name=='sys':return self.sys
        if name=='platform':return self.platform
        if name=='time':return self.time
        if name=='select':return self.select
        if name.startswith('psycopg_binary'):return self.binary
        if name.startswith('psycopg') and name!='psycopg.sql':return self.client
        if level==1:
            if name=='network_reader':return types.SimpleNamespace(observe_worker_network=lambda deadline:self.network)
            if name in self.loaded:return self.loaded[name]
            if name=='':
                actual=importlib.import_module('platform_service.media_backup_b3')
                return types.SimpleNamespace(**{n:self.loaded.get(n) or getattr(actual,n,None) or importlib.import_module('platform_service.media_backup_b3.'+n) for n in fromlist})
        return builtins.__import__(name,globals,locals,fromlist,level)
    def build(self):
        return {**{k:self.manifest[k] for k in ('artifact_sha256','entrypoint_sha256','executable_sha256','program_sha256','client_sha256')},'manifest_sha256':hashlib.sha256(self.manifest_raw).hexdigest()}
