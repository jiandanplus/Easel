"""Fixed installed reconstruction identities and measured provenance.

Private helpers; no configurable root, program, client or executor interface.
The manifest is independently pinned DATA, never an authorization response.
"""
import hashlib
import os
import stat
import sys
import re
from . import execution_worker as w
from .reconstruction_contract import require, exact, integer, pin
from .roles import _hash
from . import reconstruction_sql as _compiler
_COMPILER_CALLABLES=(_compiler.roles_apply,_compiler.membership)
_NETWORK_LIMIT=1024*1024

_BASE='/usr/local/lib/python3.12/site-packages/'
_PACKAGE=_BASE+'platform_service/media_backup_b3/'
_BUILD='/opt/easel/reconstruction-build.json'
_LEGACY='/opt/easel/worker-build.json'
_ARTIFACT='/opt/easel/artifact.whl'
_EXE='/usr/local/bin/python3.12'
_ALIAS='/opt/easel/bin/python'
_ENTRY=_PACKAGE+'reconstruction_worker.py'
_PROGRAM=tuple(sorted(_PACKAGE+n+'.py' for n in ('reconstruction_worker','reconstruction_sql','reconstruction_contract','roles','reconstruction_origin')))
_CLIENT=(_BASE+'psycopg/__init__.py',_BASE+'psycopg_binary/__init__.py',
    _BASE+'psycopg_binary/pq.cpython-312-aarch64-linux-gnu.so',
    _BASE+'psycopg_binary/_psycopg.cpython-312-aarch64-linux-gnu.so',
    _BASE+'psycopg_binary.libs/libpq-53e4e5f8.so.5.18')
_FIELDS=('format','architecture','artifact_path','artifact_sha256','entrypoint_path','entrypoint_sha256',
    'executable_path','executable_sha256','interpreter_alias','interpreter_target','files','program','program_sha256',
    'client','client_sha256','allowed_fileless_modules')

def _manifest(files):
    d=dict(format='easel.b3.reconstruction-build.v1',architecture='arm64',files=files,
        interpreter_alias=_ALIAS,interpreter_target=_EXE,allowed_fileless_modules=[])
    for role,path in (('artifact',_ARTIFACT),('entrypoint',_ENTRY),('executable',_EXE)):
        d[role+'_path']=path;d[role+'_sha256']=files[path]['sha256']
    for role,paths in (('program',_PROGRAM),('client',_CLIENT)):
        d[role]={p:files[p]['sha256'] for p in sorted(paths)};d[role+'_sha256']=_hash(d[role])
    return _validate(d)

def _validate(d):
    exact(d,_FIELDS)
    require(d['format']=='easel.b3.reconstruction-build.v1' and d['architecture']=='arm64')
    require(d['interpreter_alias']==_ALIAS and d['interpreter_target']==_EXE)
    f=d['files'];require(type(f) is dict and 1<=len(f)<=20000 and _BUILD not in f and _LEGACY in f)
    total=0
    for path,meta in f.items():
        w._path(path);exact(meta,('sha256','size','mode'));pin(meta['sha256'])
        total+=integer(meta['size'],0,512*1024**2)
        require(integer(meta['mode'],0,0o777)&0o22==0 and total<=2*1024**3)
    for role,path in (('artifact',_ARTIFACT),('entrypoint',_ENTRY),('executable',_EXE)):
        require(d[role+'_path']==path and path in f and d[role+'_sha256']==f[path]['sha256'])
    for role,paths in (('program',_PROGRAM),('client',_CLIENT)):
        require(all(p in f for p in paths));exact(d[role],paths)
        require(d[role]=={p:f[p]['sha256'] for p in sorted(paths)} and d[role+'_sha256']==_hash(d[role]))
    require(d['allowed_fileless_modules']==[])
    return d

def _parse(raw):
    require(type(raw) is bytes and 0<len(raw)<=8*1024**2)
    return _validate(w._json(raw))

def _stamp_file(path,deadline):
    fd,parent,leaf=w._open_immutable(path,deadline)
    try:
        s=os.fstat(fd);w._regular(s)
        require(w._stamp(s)==w._stamp(os.stat(leaf,dir_fd=parent,follow_symlinks=False)))
        return w._stamp(s)
    finally:os.close(fd);os.close(parent)

def _load(request,budget):
    deadline=int(budget.deadline*10**9);budget.guard()
    raw=w._immutable_bytes(_BUILD,8*1024**2,deadline)
    measured=hashlib.sha256(raw).hexdigest();require(measured==request.build['manifest_sha256'])
    d=_parse(raw)
    for role in ('artifact','entrypoint','executable','program','client'):
        require(d[role+'_sha256']==request.build[role+'_sha256'])
    stamps={};hashes={}
    for path,meta in d['files'].items():
        before=_stamp_file(path,deadline)
        hashes[path]=w._immutable_read(path,512*1024**2,deadline,meta)
        require(_stamp_file(path,deadline)==before);stamps[path]=before
    require(sys.executable==_ALIAS)
    parent,leaf=w._open_parent(_ALIAS,deadline)
    try:
        s=os.stat(leaf,dir_fd=parent,follow_symlinks=False)
        require(stat.S_ISLNK(s.st_mode) and s.st_uid==s.st_gid==0 and s.st_nlink==1)
        require(os.readlink(leaf,dir_fd=parent)==_EXE)
        require(w._stamp(s)==w._stamp(os.stat(leaf,dir_fd=parent,follow_symlinks=False)))
    finally:os.close(parent)
    # Only this trusted procfs link may resolve, to the verified executable inode.
    require(os.readlink('/proc/self/exe')==_EXE)
    actual=os.stat('/proc/self/exe');require((actual.st_dev,actual.st_ino)==stamps[_EXE][:2])
    budget.guard()
    return d,hashes,stamps,measured

def _callables():
    main=sys.modules.get('__main__');require(main is not None)
    spec=getattr(main,'__spec__',None)
    require(getattr(spec,'name',None)=='platform_service.media_backup_b3.reconstruction_worker')
    require(getattr(spec,'origin',None)==getattr(main,'__file__',None)==_ENTRY)
    require('platform_service.media_backup_b3.reconstruction_worker' not in sys.modules)
    from . import reconstruction_sql as sql
    require(all(getattr(main,name) is original for name,original in zip(('main','_initial_observe','_roles_apply','_connect','_installed_origin'),main._FIXED_CALLABLES)))
    require(len(main._FIXED_CALLABLES)==5 and sql is _compiler)
    require(sql.roles_apply is _COMPILER_CALLABLES[0] and sql.membership is _COMPILER_CALLABLES[1])
    for module,names,path in ((main,('main','_initial_observe','_roles_apply','_connect','_installed_origin'),_ENTRY),
            (sql,('roles_apply','membership'),_PACKAGE+'reconstruction_sql.py')):
        for name in names:
            fn=getattr(module,name,None)
            require(getattr(fn,'__module__',None)==module.__name__ and fn.__globals__ is module.__dict__)
            require(fn.__code__.co_filename==path and fn.__name__==name)
    import psycopg,psycopg.pq,psycopg.connection,psycopg_binary,psycopg_binary.pq,psycopg_binary._psycopg
    require(psycopg.pq.__impl__=='binary')
    require(psycopg.Connection is psycopg.connection.Connection)
    require(psycopg.connect.__func__ is psycopg.Connection.connect.__func__)
    require(psycopg.Connection.connect.__func__.__globals__ is psycopg.connection.__dict__)
    require(psycopg.Connection.connect.__func__.__code__.co_filename==_BASE+'psycopg/connection.py')
    require(psycopg.pq.PGconn is psycopg_binary.pq.PGconn)
    for module,path in zip((psycopg,psycopg_binary,psycopg_binary.pq,psycopg_binary._psycopg),_CLIENT[:4]):
        require(module.__file__==module.__spec__.origin==path)

def _origins(state,budget):
    d,hashes,stamps,_=state;deadline=int(budget.deadline*10**9)
    _callables();budget.guard();snapshot=dict(sys.modules)
    for path,stamp in stamps.items():require(_stamp_file(path,deadline)==stamp)
    module_digest=w._origins(hashes,[],hashes[_EXE],deadline)
    records=set();found=set()
    for line in w._text('/proc/self/maps',8*1024**2,deadline).splitlines():
        budget.guard()
        match=re.fullmatch(r'([0-9a-f]+)-([0-9a-f]+) ([r-][w-][x-][ps]) ([0-9a-f]+) ([0-9a-f]+):([0-9a-f]+) ([0-9]+)(?: +(.*))?',line,re.ASCII)
        require(match is not None)
        start,end,perms,offset,major,minor,inode,path=match.groups()
        require(int(start,16)<int(end,16))
        require(not path or ('memfd:' not in path and not path.endswith(' (deleted)')))
        if not path or not path.startswith('/'):
            require('x' not in perms or path in ('[vdso]','[vsyscall]'));continue
        if 'x' not in perms and path not in _CLIENT[2:] and '.so' not in path.rsplit('/',1)[-1]:continue
        require(path in hashes and int(inode)>0)
        stamp=stamps[path]
        require((os.makedev(int(major,16),int(minor,16)),int(inode))==stamp[:2])
        require(_stamp_file(path,deadline)==stamp)
        records.add((path,hashes[path],stamp[0],stamp[1]));found.add(path)
    require(set(_CLIENT[2:])<=found)
    require(snapshot.keys()==sys.modules.keys() and all(sys.modules[n] is v for n,v in snapshot.items()))
    budget.guard();return _hash(dict(modules=module_digest,maps=sorted(records)))

def _security(request,budget):
    from .network_reader import observe_worker_network
    from .network_wire import encode_network_observation
    from .network_profile import validate_network_profile
    deadline=int(budget.deadline*10**9);budget.guard()
    require(os.getuid()==os.geteuid()==os.getgid()==os.getegid()==10001)
    groups=sorted(os.getgroups());require(set(groups)<={10001})
    status={}
    for line in w._text('/proc/self/status',65536,deadline).splitlines():
        require(':' in line);key,value=line.split(':',1);require(key and key not in status);status[key]=value.strip()
    for key in ('Uid','Gid'):require(status.get(key,'').split()==['10001']*4)
    require(set(status.get('Groups','INVALID').split())<={'10001'})
    caps={}
    for key in ('CapInh','CapPrm','CapEff','CapBnd','CapAmb'):
        value=status.get(key,'');require(re.fullmatch('[0-9a-fA-F]{1,16}',value) is not None)
        caps[key]=int(value,16);require(caps[key]==0)
    require(status.get('NoNewPrivs')=='1')
    require(w._text('/proc/self/cgroup',128,deadline)=='0::/\n')
    resources={}
    for name,expected in (('memory.max',request.build['memory_bytes']),('memory.swap.max',0),('pids.max',request.build['pids_limit'])):
        value=w._number(w._text('/sys/fs/cgroup/'+name,128,deadline).removesuffix('\n'))
        require(value==expected);resources[name]=value
    cpu=w._text('/sys/fs/cgroup/cpu.max',128,deadline).removesuffix('\n').split(' ')
    require(len(cpu)==2);quota,period=map(w._number,cpu)
    require(quota>0 and period>0 and quota*10**9==request.build['cpu_nanos']*period)
    resources['cpu_quota']=quota;resources['cpu_period']=period
    ns=os.stat('/proc/self/ns/net');require(ns.st_dev>0 and ns.st_ino>0)
    network=observe_worker_network(deadline);validate_network_profile(network)
    wire=encode_network_observation(network)
    from .reconstruction_contract import bounded_frame
    bounded_frame(wire,_NETWORK_LIMIT)  # reserve both finite wires before any SQL
    require(wire['namespace_before']==wire['namespace_after']==dict(device=ns.st_dev,inode=ns.st_ino))
    budget.guard()
    return dict(uid=10001,gid=10001,groups=groups,capabilities=caps,no_new_privileges=True,
        cgroup='0::/',resources=resources,netns=dict(device=ns.st_dev,inode=ns.st_ino),network=wire)

def _observe(state,request,budget):
    security=_security(request,budget)
    origins=_origins(state,budget)
    return dict(security=security,origins_sha256=origins)

def _evidence(state,before,after):
    d,hashes,stamps,manifest=state
    require({k:v for k,v in before['security'].items() if k!='network'}==
            {k:v for k,v in after['security'].items() if k!='network'})
    return dict(manifest_sha256=manifest,artifact_sha256=hashes[_ARTIFACT],entrypoint_sha256=hashes[_ENTRY],
        executable_sha256=hashes[_EXE],program_sha256=_hash({p:hashes[p] for p in _PROGRAM}),
        client_sha256=_hash({p:hashes[p] for p in sorted(_CLIENT)}),pq_implementation='binary',
        before=before,after=after)
