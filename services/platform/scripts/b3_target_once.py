"""Root-only synthetic B2 -> B3 final dump-preflight once caller. Import is stdlib-only.

Execute only with Root's ready-source authority; no discovery/container lifecycle.
Signal checks do not prove cancellation of remote Docker exec or blocking IO.
"""
import argparse
import base64
import threading
import asyncio
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import signal
import stat
import sys
import time
import traceback
from uuid import uuid4

REPO = Path(__file__).resolve().parents[3]
# Runtime roots are supplied and independently pinned by Root's sealed envelope.
SOURCE = REPO / 'services/platform'
TESTTOOLS = None
SHARED_SITE = None
SELF = Path(__file__).resolve()
SOCKET = 'unix:///Users/jiandan/.docker/run/docker.sock'
FIXED = frozenset(('config/central','config/identity','config/media','db/central.dump',
                   'db/identity.dump','db/central.roles','db/identity.roles'))
SCOPE = dict(case='centralPUBLIC016+identityPUBLIC016-empty-media',
             operations=['synthetic_setup','source_read','B2_capture','B3_authenticated_stage','B3_dump_preflight','target_lifecycle_qualification'],
             resource_creation=['own_synthetic_databases_roles','private_filesystem','two_target_containers_volumes'],
             capture_limit=512*1024**2, reservation=512*1024**2,
             admission_free=8*1024**3, running_free=2*1024**3,
             seconds=900, parser_sleep=720, SQL_restore=False, source_lifetime_unchanged=True, retries=0)


DOCKER_PATH='/Applications/Docker.app/Contents/Resources/bin/docker'
DOCKER_SHA='73206884cd100a165e20fbab2b1f9e09e0ae8fc959ec9b02fed46152a99c5e79'
PARSER_IMAGE='sha256:9e2cf6287f25c3151f4c9421ccce2b17f3e60061c111a29165e0d2cdd9ff0a97'
SOURCE_IMAGE_REF='postgres@'+PARSER_IMAGE
PARSER_TOOL_SHA='7831a506282d19fa0a89e020c8a522aa493692e6738fe022e114809b3c0abce2'
TEMPLATE_PINS={
 'central':dict(kind='central',path=str(REPO/'services/platform/tests/fixtures/media_backup_b3/dump_screen/public016-toc-policy.synthetic.json'),sha256='33e5a8e87d65e2cb0697ada155560c7b335cc37b535269dafdffe15f1812a120',selected=798,header=802),
 'identity':dict(kind='identity',path=str(SOURCE/'tests/fixtures/media_backup_b3/dump_screen/public016-identity-toc-policy.synthetic.json'),sha256='0f7a2de46f95d77e4029792380da821e3fffac9f156d0dc15d131560619d2052',selected=755,header=759)}


APPROVED_API={
 'dump_preflight.py':'88783dc9e5f5f9c3cfd229eddf12946cff617db8f41ed7870a6cd39131773f84',
 'preflight.py':'8df5431fb06a811ea0c89e29b53a7f9cc1b742ab81043003fbea603ef41a0404',
 'parser_runner.py':'2cc3d0370c40da7a9482da738ab76cae79869005a458dcdec0a1f4f29750a7ef',
 'contracts.py':'68238751ffe32499200332ec9d93819cf4d06226c219f91a7a3b6e3bffda7138',
 'target_factory.py':'8f466494b09d0f4874577bd23ff4ce3592c9400dc4d50c04057299ec88d1510e',
 'target_lifecycle.py':'367fe66da40e9d937721d67d468f00e10fc5f0125fbfe3000a3dc2ac257d5401',
 'target_contract.py':'79ea449288d6d0672af0ef89d19a8d38adcb3803e90296ac82c4b6a9674378e9'}

def parser_plan(m):
    """Pure planned argv; Root executes and pins it independently, never here."""
    restore_id='b3-'+m['operation_nonce'][:32]
    return [DOCKER_PATH,'-H',SOCKET,'--config',str(Path(m['directory'])/'parser-config'),
        'create','--pull','never','--name','easel-'+restore_id,
        '--label','purpose=b3-dump-parser','--label','operation_nonce='+restore_id,
        '--network','none','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges',
        '--cpus','1','--memory','512m','--memory-swap','512m','--pids-limit','32',
        '--user','999:999','--restart','no','--ipc','private',
        '--tmpfs','/var/lib/postgresql/data:rw,noexec,nosuid,nodev,size=1m,uid=999,gid=999,mode=0700',
        '--entrypoint','/bin/sleep',PARSER_IMAGE,'720']


def closed_json(raw):
    def pairs(values):
        result={}
        for key,value in values:
            require(key not in result);result[key]=value
        return result
    return json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda _:require(False))


def validate_descriptor(raw,m,manifest_hash):
    """No archive input: independent Root closed document + case identity."""
    d=closed_json(raw)
    require(type(d) is dict and set(d)=={'format','manifest_sha256','operation_nonce',
        'restore_id','cid','image_id','tool_sha256','inspect_base64','inspect_sha256','started','deadline'})
    require(d['format']=='easel-b3-root-parser-descriptor-v1'
        and d['manifest_sha256']==manifest_hash and d['operation_nonce']==m['operation_nonce']
        and d['restore_id']=='b3-'+m['operation_nonce'][:32]
        and d['started']==m['started'] and d['deadline']==m['deadline'])
    require(type(d['cid']) is str and re.fullmatch('[0-9a-f]{64}',d['cid'])
        and d['cid']!=m['source']['container_id'] and d['image_id']==PARSER_IMAGE
        and d['tool_sha256']==PARSER_TOOL_SHA)
    require(type(d['inspect_base64']) is str)
    try:projection=base64.b64decode(d['inspect_base64'],validate=True)
    except (ValueError,TypeError):raise ValueError('invalid parser descriptor') from None
    require(sha(projection)==d['inspect_sha256'])
    p=closed_json(projection)
    require(canonical(p)==projection and p['Id']==d['cid'] and p['Image']==PARSER_IMAGE
        and p['Config']['Labels']==dict(purpose='b3-dump-parser',operation_nonce=d['restore_id'])
        and p['Config']['Cmd']==['720'])
    # Complete closed inspect validation is additionally performed by the REAL
    # ParserAuthority constructor, only after Root actual imports.
    return d


def template_policy(raw,pin,owner,factory):
    """Only Root test-known PUBLIC016 fixture templates. Never candidate TOC."""
    require(sha(raw)==pin['sha256'] and type(owner) is str
        and re.fullmatch('[A-Za-z_][A-Za-z0-9_]{0,62}',owner))
    d=closed_json(raw);kind=pin['kind']
    require(kind in ('central','identity'))
    expected={'provenance','toc_sha256','logical_records','selected_count','category_counts'} if kind=='central' else {'format','header_entries','logical_records','toc_sha256'}
    require(type(d) is dict and set(d)==expected)
    require(type(d['toc_sha256']) is str and re.fullmatch('[0-9a-f]{64}',d['toc_sha256']))
    records=d['logical_records'];require(type(records) is list and len(records)==pin['selected'])
    if kind=='central':
        require(d['selected_count']==pin['selected'] and type(d['provenance']) is str and type(d['category_counts']) is dict)
        counts={}
        for row in records:counts[row[0]]=counts.get(row[0],0)+1
        require(counts==d['category_counts'])
    else:require(d['format']=='easel-root-known-fixture-toc-policy-v1' and d['header_entries']==pin['header'])
    mapped=[]
    for row in records:
        require(type(row) is list and len(row)==4 and all(type(x) is str for x in row))
        require(all('fixture_role' not in x and 'easel_s1_test_b2' not in x for x in row[:3]))
        require(row[3] in ('fixture_role','pg_database_owner','-'))
        mapped.append((*row[:3],owner if row[3]=='fixture_role' else row[3]))
    return factory(tuple(mapped),pin['header'])


def context_from_replay_selection(connection,source_owner,profile,kind,api,selection):
    """Pre-capture export selection, explicitly independent of source observations."""
    from types import SimpleNamespace
    from dataclasses import replace
    require(getattr(source_owner,'_replay_selection',None) is selection)
    require(selection.kind==kind and selection.database_name==source_owner.name)
    require(tuple(sorted(source_owner.roles.values()))==selection.business_role_names)
    require(getattr(source_owner,'_reconstruction_producer',None) is None)
    view=SimpleNamespace(name=source_owner.name,roles={str(i):name for i,name in enumerate(selection.replay_role_names)})
    context,catalog=context_from_source(connection,view,profile,kind,api)
    context=replace(context,allowed_grantors=selection.allowed_grantors)
    require(context.role_names==selection.replay_role_names)
    return context,catalog


def admit_owned_role_plan(graph,execution_owner,source_owners,pair,authorizations):
    """Internal opt-in join; registers original graph before admission can fail."""
    require('role_admission' not in graph)
    graph['role_admission']=dict(owner=execution_owner,pair=pair,source_owners=source_owners,authorizations=authorizations)
    return execution_owner._admit_role_plan(pair,source_owners=source_owners,authorizations=authorizations)


def begin_owned_source_capture(source_owners,inputs,contexts,read_scopes,*,original_read_contexts,
        started_at_ns,deadline_ns,cancellation):
    """Internal development join. No CLI selection or production admission mode."""
    from b3_source_owner import SourceOwner
    from platform_service.media_backup_b2.capture import OwnedInputs
    from platform_service.media_backup_b2.reconstruction_capture import OwnedSourceCapture
    from dataclasses import asdict
    comparison_pin=lambda context:sha(canonical(asdict(context)))
    from platform_service.media_backup_b3.contracts import ComparisonContext
    require(type(source_owners) is tuple and len(source_owners)==2 and source_owners[0] is not source_owners[1])
    require(type(inputs) is OwnedInputs and type(contexts) is tuple and len(contexts)==2)
    require(type(read_scopes) is tuple and len(read_scopes)==2 and type(original_read_contexts) is tuple and len(original_read_contexts)==2)
    from psycopg.conninfo import conninfo_to_dict
    for kind,owner,context,scope in zip(('central','identity'),source_owners,contexts,read_scopes):
        require(type(owner) is SourceOwner and type(context) is ComparisonContext)
        require(context.source_kind==kind and context.database_name==owner.name and scope.kind==kind)
        selection=getattr(owner,'_replay_selection',None)
        if selection is None:require(context.role_names==tuple(sorted(owner.roles.values())))
        else:
            require(selection.business_role_names==tuple(sorted(owner.roles.values())))
            require(context.role_names==selection.replay_role_names and context.allowed_grantors==selection.allowed_grantors)
            require(getattr(owner,'_replay_authorization',None) is not None)
            require(owner._replay_authorization.original_comparison is context)
        require(scope.database_name==owner.name and set(context.role_names)<=set(scope.selected_role_names))
        require(original_read_contexts[0 if kind=='central' else 1] is scope)
        source=conninfo_to_dict(inputs.central_dsn if kind=='central' else inputs.identity_dsn)
        admin=conninfo_to_dict(inputs.role_admin_dsns[kind]);base=conninfo_to_dict(owner.base)
        require(all(source.get(k)==admin.get(k)==base.get(k) for k in ('host','hostaddr','port')))
        require(source.get('dbname')==admin.get('dbname')==owner.name)
        require(source.get('user')==owner.roles['owner'] and admin.get('user')==base.get('user')==scope.connection_admin)
    pins=tuple(comparison_pin(c) for c in contexts)
    producers=tuple(owner.begin_reconstruction(scope,original_read_context=read,original_comparison=context,
        started_at_ns=started_at_ns,deadline_ns=deadline_ns,cancellation=cancellation)
        for owner,scope,read,context in zip(source_owners,read_scopes,original_read_contexts,contexts))
    require(all(owner._reconstruction_producer is p for owner,p in zip(source_owners,producers)))
    require(tuple(comparison_pin(c) for c in contexts)==pins)
    pair=OwnedSourceCapture._for_inputs(inputs,original_contexts=contexts,producers=producers)
    inputs.bind_reconstruction(pair)
    return pair


def finish_owned_source_capture(source_owners,pair):
    require(type(source_owners) is tuple and len(source_owners)==2)
    require(all(owner is p._owner and owner._reconstruction_producer is p for owner,p in zip(source_owners,pair._producers)))
    require(pair._terminal_state=='TERMINAL_SUCCESS')
    for owner,p in zip(source_owners,pair._producers):owner.finish_reconstruction(p)


def finish_known_failed_owned_source_capture(source_owners,pair):
    require(type(source_owners) is tuple and len(source_owners)==2)
    require(all(owner is p._owner and owner._reconstruction_producer is p for owner,p in zip(source_owners,pair._producers)))
    require(pair._terminal_state=='FAILED_TERMINAL')
    for owner,p in zip(source_owners,pair._producers):owner.finish_known_failed_reconstruction(p)


def release_owned_source_capture(pair):
    from platform_service.media_backup_b2.reconstruction_capture import OwnedSourceCapture
    require(type(pair) is OwnedSourceCapture)
    require(all(p._owner._reconstruction_attempt['state']=='TERMINAL' for p in pair._producers))
    pair.close_publication()


def graph_state(graph):
    """Snapshot original ownership graph BEFORE any close/generator finalizer.

    Only safe counters/booleans. Poll is observation of retained local handles;
    zero is required for normal success, any observed exit permits disposition.
    """
    active=0;sticky=False;source_observation_error=False;source_publication_fds=0
    inputs=graph.get('inputs')
    if inputs is not None:
        acquired=False
        try:
            acquired=inputs._work_lock.acquire(blocking=False)
            if not acquired:source_observation_error=True
            else:
                observed_active=inputs._active_work;observed_sticky=inputs._work_unknown
                require(type(observed_active) is int and observed_active>=0 and type(observed_sticky) is bool)
                active=observed_active;sticky=observed_sticky
        except BaseException:source_observation_error=True
        finally:
            if acquired:
                try:inputs._work_lock.release()
                except BaseException:source_observation_error=True
    if inputs is not None and getattr(inputs,'companion',None) is not None:
        try:
            child=inputs.companion.snapshot()
            active+=child['active']+child['retained_refs']
            source_publication_fds=child['publication_fds']
            sticky=sticky or child['unknown']
            source_observation_error=source_observation_error or child['observation_error']
        except BaseException:source_observation_error=True
    b2=graph.get('runner');b2_children=False;b2_unknown=False;b2_observation_error=False
    try:
        b2_children=bool(b2 and b2.children);b2_unknown=bool(b2 and b2.unknown_refs)
    except BaseException:b2_observation_error=True
    parser=graph.get('parser_runner');parser_unknown=False;live=0;nonzero=0;observed=0
    if parser is not None:
        try:
            parser_unknown=parser.remote_state not in ('NOT_STARTED','TERMINAL')
            processes=list(parser._retained_processes)
            current=getattr(parser,'_active_process',None)
            if current is not None and all(current is not p for p in processes):processes.append(current)
            for process in processes:
                try:rc=process.poll()
                except BaseException:rc=None
                if type(rc) is not int:live+=1
                else:
                    observed+=1
                    if rc!=0:nonzero+=1
        except BaseException:parser_unknown=True;live+=1
    target_unknown=False;target_live=0
    observer=graph.get('observer')
    if observer is not None:
        target_unknown=observer._unknown or observer._active
        for process in observer._processes:
            try:rc=process.poll()
            except BaseException:rc=None
            if type(rc) is not int:target_live+=1
    factory=graph.get('factory')
    try:
        for attempt in (factory._attempts.values() if factory is not None else ()):
            target_unknown=target_unknown or attempt._unknown
            # READY is reached only after producer-validated transport/metadata
            # and durable final journal. Nonzero is not intrinsically unknown,
            # but an operation name or arbitrary polled terminal is not proof.
            ready=(attempt._state=='READY' and attempt._sequence==8 and len(attempt._records)==8)
            if not ready:target_unknown=True
            entries=attempt._outputs
            if len(entries)!=len(attempt._processes):target_unknown=True
            for process in attempt._processes:
                try:rc=process.poll()
                except BaseException:rc=None
                if type(rc) is not int:target_live+=1;continue
                matches=[entry for entry in entries if entry.get('process') is process]
                if len(matches)!=1:target_unknown=True;continue
                entry=matches[0]
                if type(entry.get('terminal_code')) is not int or entry['terminal_code']!=rc:
                    target_unknown=True;continue
                if not all(type(entry.get(k)) is str and re.fullmatch('[a-f0-9]{64}',entry[k])
                    for k in ('stdout_sha256','stderr_sha256')):target_unknown=True
                allowed={'volume_absence':(1,),'container_absence':(1,),
                    'ready':(0,1,2),'deny_password':(2,)}
                if rc not in allowed.get(entry.get('operation'),(0,)):target_unknown=True
    except BaseException:target_unknown=True;target_live+=1
    source_owner_unknown=False;source_owner_active=0
    try:
        for owner in graph.get('source_owners',[]):
            source_owner_unknown=source_owner_unknown or owner.unknown or owner.phase not in ('READY','CLOSED')
            source_owner_active+=owner.active
    except BaseException:
        source_owner_unknown=True;source_owner_active+=1
    execution={name:0 for name in ('execution_owner_count','execution_target_count','execution_phase_claims',
        'execution_committed_count','execution_local_terminal_count','execution_local_live_or_unobserved',
        'execution_remote_live_or_unobserved','execution_active_work','execution_retained_owner_count')}
    execution.update(execution_unknown=False,execution_observation_error=False,execution_disposition_retained=False)
    execution_owner=graph.get('execution_owner')
    if execution_owner is not None:
        try:
            from platform_service.media_backup_b3.execution import RootExecutionOwner
            require(type(execution_owner) is RootExecutionOwner and execution_owner._factory is factory)
            require(execution_owner._input is graph.get('finalobj'))
            snapshot=execution_owner.snapshot()
            require(type(snapshot) is dict and set(snapshot)==set(execution))
            for name,value in snapshot.items():
                require(type(value) is type(execution[name]))
                if type(value) is int:require(value>=0)
            require(snapshot['execution_owner_count']==1)
            execution=snapshot
        except BaseException:
            execution.update(execution_owner_count=1,execution_unknown=True,execution_observation_error=True,
                execution_local_live_or_unobserved=1,execution_remote_live_or_unobserved=1)
    unknown=bool(any(execution[k] for k in ('execution_unknown','execution_observation_error',
        'execution_local_live_or_unobserved','execution_remote_live_or_unobserved','execution_active_work')) or graph.get('cleanup_failed') or source_owner_unknown or source_owner_active or graph.get('operation_failed') or target_unknown or target_live or active or sticky or source_observation_error or b2_children or b2_unknown or b2_observation_error or parser_unknown or live or nonzero)
    result=dict(source_publication_fds=source_publication_fds,source_owner_unknown=source_owner_unknown,source_owner_active=source_owner_active,target_unknown=target_unknown,target_local_live_or_unobserved=target_live,source_active_work=active,source_work_unknown=sticky,source_observation_error=source_observation_error,
        b2_local_children=b2_children,b2_unknown_refs=b2_unknown,b2_observation_error=b2_observation_error,parser_remote_unknown=parser_unknown,
        parser_local_live_or_unobserved=live,parser_local_nonzero=nonzero,parser_local_terminal_count=observed,unknown=unknown,**execution)
    if 'role_admission' in graph:
        try:
            admission=graph['role_admission'];require(admission['owner'] is execution_owner)
            require(inputs is not None and inputs.companion is admission['pair'])
            require(execution_owner._role_slot is None or execution_owner._role_slot.pair is admission['pair'])
            role=execution_owner.role_admission_snapshot()
            result['role_admission']=role
            result['unknown']=result['unknown'] or role['unknown'] or role['observation_error']
        except BaseException:
            result['role_admission']=dict(unknown=True,observation_error=True)
            result['unknown']=True
    return result


def check_parser_start_budget(m,now=None):
    now=time.monotonic() if now is None else now
    require(m['started']<=now and m['deadline']-now>=720)


def disposition_work_terminal(state):
    # Sticky audit flags and remote UNKNOWN require explicit Root disposition;
    # they are never reset. Original local handles and source work must end.
    return not (state.get('execution_active_work',0) or state.get('execution_local_live_or_unobserved',0) or state.get('execution_observation_error',False) or state.get('source_owner_active',0) or state['target_local_live_or_unobserved'] or state['source_active_work'] or state['source_observation_error']
        or state['b2_local_children'] or state['b2_observation_error'] or state['parser_local_live_or_unobserved'])


def require(value):
    if not value:
        raise ValueError('invalid synthetic once-run authority')


def check_source_image(value):
    require(type(value) is str and value==SOURCE_IMAGE_REF)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      allow_nan=False).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def stamp(st):
    return [st.st_dev, st.st_ino, st.st_mode, st.st_uid, st.st_gid,
            st.st_nlink, st.st_size, st.st_mtime_ns, st.st_ctime_ns]


def private_read(path, maximum=16*1024**2):
    path = Path(path)
    require(path.is_absolute() and str(path)==os.path.normpath(path))
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for component in path.parts[1:-1]:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd); fd=child
        parent=os.fstat(fd)
        require(stat.S_IMODE(parent.st_mode)==0o700 and (parent.st_uid,parent.st_gid)==(os.getuid(),os.getgid()))
        filefd=os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        try:
            before=os.fstat(filefd)
            require(stat.S_ISREG(before.st_mode) and stat.S_IMODE(before.st_mode)==0o600
                    and (before.st_uid,before.st_gid)==(os.getuid(),os.getgid()) and before.st_nlink==1
                    and before.st_size<=maximum)
            raw=os.pread(filefd,maximum+1,0)
            require(len(raw)==before.st_size and stamp(before)==stamp(os.fstat(filefd)))
            return raw, dict(file=stamp(before), parent=stamp(parent)[:5])
        finally: os.close(filefd)
    finally: os.close(fd)


def write_new(path, raw):
    fd=os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        os.fchown(fd,os.getuid(),os.getgid()); os.fchmod(fd,0o600)
        with os.fdopen(fd,'wb',closefd=False) as out:
            out.write(raw); out.flush(); os.fsync(fd)
    finally: os.close(fd)
    parent_fd=os.open(Path(path).parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(parent_fd)
    finally:os.close(parent_fd)


def mkdir_private(path):
    os.mkdir(path,0o700)
    os.chown(path,os.getuid(),os.getgid()); os.chmod(path,0o700)
    parent_fd=os.open(Path(path).parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(parent_fd)
    finally:os.close(parent_fd)


def check_manifest(m, now=None):
    now=time.monotonic() if now is None else now
    require(m['scope']==SCOPE and m['uid']==os.getuid() and m['gid']==os.getgid())
    require(type(m['started']) is float and m['deadline']==m['started']+900)
    require(m['started']<=now<m['deadline'])


def source_binding(path, expected):
    raw,custody=private_read(path,16384)
    require(sha(raw)==expected['sha256'])
    data=json.loads(raw)
    require(all(data[k]==expected[k] for k in ('container_id','image','nonce')))
    require(data['database']=='easel_s1_test_b2_'+data['nonce'])
    require(re.fullmatch('[0-9a-f]{64}',data['container_id'])
            and re.fullmatch('[0-9a-f]{32}',data['nonce']))
    return custody


PK_SQL="""SELECT c.relname,array_agg(a.attname ORDER BY k.ordinality)
FROM pg_constraint p JOIN pg_class c ON c.oid=p.conrelid
JOIN pg_namespace n ON n.oid=c.relnamespace
CROSS JOIN LATERAL unnest(p.conkey) WITH ORDINALITY k(attnum,ordinality)
JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum=k.attnum
WHERE p.contype='p' AND n.nspname='public' GROUP BY c.relname ORDER BY c.relname"""


def context_from_source(connection, env, profile, kind, api):
    """Independent catalog/PK/raw reads, before capture; no archive argument."""
    connection.execute('BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY')
    require(connection.execute('SHOW transaction_isolation').fetchone()==('repeatable read',))
    require(connection.execute('SHOW transaction_read_only').fetchone()==('on',))
    catalog=api['catalog'](connection,profile)
    pks={name:list(columns) for name,columns in connection.execute(PK_SQL).fetchall()}
    tables=[]
    for name in sorted(profile.tables):
        columns=[]
        for row in catalog['columns']:
            if row[0]=='public' and row[1]==name and row[2] in ('r','p') and row[6] is not None:
                require(row[16] in ('','s'))  # PG16 ordinary or stored-generated; retain both
                col=dict(name=row[6],type=row[7],nullable=not row[8])
                labels=[labels for enum,labels in catalog['enums'] if enum==row[7]]
                if labels:
                    require(len(labels)==1); col['enum_labels']=list(labels[0])
                columns.append(col)
        require(name in pks and columns)
        tables.append(dict(name=name,columns=columns,primary_key=pks[name]))
    normalized=copy.deepcopy(catalog)
    require(len(normalized['database'])==1 and normalized['database'][0][0]==env.name)
    normalized['database'][0][0]='<trusted-database>'
    evidence='full_raw_and_copy' if kind=='central' else 'copy_only'
    spec=dict(format='easel-restore-profile-v1',name=profile.name,tables=tables,
              catalog_sha256=api['digest'](normalized),source_kind=kind,evidence_mode=evidence)
    parsed=api['parse'](api['canonical'](spec))
    rows=api['raw_rows'](connection,profile) if kind=='central' else {}
    pins=tuple((name,tuple(api['digest'](r) for r in rows[name])) for name in sorted(profile.tables)) if kind=='central' else ()
    ctx=api['context'](database_name=env.name,role_names=tuple(sorted(env.roles.values())),
                       allowed_grantors=('pg_database_owner',),profile=parsed,
                       row_order_pins=pins,source_kind=kind,evidence_mode=evidence)
    return ctx,catalog


def safe_receipt(receipt):
    require(type(receipt) is dict and set(receipt)=={'authenticated_stage','dump_preflight_complete','plan_sha256',
        'registry_sha256','source_facts_sha256','archive_sha256','ciphertext_sha256','dump_inspection','parser'})
    require(receipt['authenticated_stage'] is True and receipt['dump_preflight_complete'] is True)
    ishash=lambda x:type(x) is str and re.fullmatch('[0-9a-f]{64}',x) is not None
    require(all(ishash(v) for k,v in receipt.items() if k.endswith('sha256')))
    parser=receipt['parser']
    require(type(parser) is dict and set(parser)=={'cid','image_id','tool_sha256','docker_sha256','inspect_sha256','authority_sha256'})
    require(parser['image_id']==PARSER_IMAGE and parser['tool_sha256']==PARSER_TOOL_SHA and parser['docker_sha256']==DOCKER_SHA)
    require(all(ishash(v) for k,v in parser.items() if k!='image_id'))
    inspections=receipt['dump_inspection'];require(type(inspections) is dict and set(inspections)=={'central','identity'})
    for kind,info in inspections.items():
        require(type(info) is dict and set(info)=={'toc_entries','toc','schema','data','tables','sequences','rows',
            'toc_equal','schema_equal','data_equal','copy_equal','sequences_equal'})
        require(info['toc_entries']==TEMPLATE_PINS[kind]['selected'])
        for flag in ('toc_equal','schema_equal','data_equal','copy_equal','sequences_equal'):require(info[flag] is True)
        for count in ('toc_entries','tables','sequences','rows'):require(type(info[count]) is int and info[count]>=0)
        for mode,maximum in (('toc',1024**2),('schema',8*1024**2),('data',16*1024**2)):
            proof=info[mode];require(type(proof) is dict and set(proof)=={'size','sha256'}
                and type(proof['size']) is int and 0<proof['size']<=maximum and ishash(proof['sha256']))
    return copy.deepcopy(receipt)


async def actual(m, directory, outcome, parser_descriptor, envelope):
    # These are real imports and real calls; only Root --execute reaches this code.
    for path in reversed((SOURCE/'src',SOURCE/'scripts',SOURCE/'tests',Path(envelope['config']['runtime']['testtools']))):
        sys.path.insert(0,str(path))
    import psycopg
    from psycopg.conninfo import make_conninfo
    from b2_test_authority import selected_authority
    from b3_source_owner import SourceOwner
    from platform_service.db import MIGRATIONS_DIR
    from media_backup_b2_fixture import synthetic_keys
    from platform_service.media_backup_b2.anchor import RootPolicy,trusted_anchor_intent
    from platform_service.media_backup_b2.bundles import ConfigInputs
    from platform_service.media_backup_b2.capture import OwnedInputs,ChildRunner,read_source_catalog,_raw_rows
    from platform_service.media_backup_b2.pipeline import build_backup
    from platform_service.media_backup_b2.profiles import PUBLIC_016
    from platform_service.media_backup_b3.contracts import ComparisonContext,parse_restore_profile,canonical,digest
    from platform_service.media_backup_b3.preflight import PreflightAuthority,StageLimits,prepare_authenticated_stage
    from platform_service.media_backup_b3.parser_runner import ParserAuthority,NetworkNoneParserRunner,inspect_projection
    from platform_service.media_backup_b3.dump_screen import TocPolicy
    from platform_service.media_backup_b3.dump_preflight import (DumpInspectionPolicy,VerifiedRestoreInput,
        inspect_authenticated_stage,_profile_sha256,_parser_sha256)
    from platform_service.media_maintenance.control import DatabaseControl
    from platform_service.media_maintenance.filesystem import ControlLock
    from platform_service.media_maintenance.schema import SchemaProfile,install_control_schema
    origins={}
    for name,module in list(sys.modules.items()):
        if name.startswith(('platform_service','media_backup')) or name in ('b3_source_owner','b2_test_authority'):
            path=getattr(module,'__file__',None)
            if path:
                path=Path(path).resolve(); require(path.is_relative_to(SOURCE))
                origins[name]=dict(path_sha256=sha(str(path).encode()),sha256=sha(path.read_bytes()))
    for name,module in list(sys.modules.items()):
        path=getattr(module,'__file__',None)
        if path:
            path=Path(path).resolve()
            if path.is_relative_to(SOURCE) or 'site-packages' in path.parts or path.is_relative_to(Path(envelope['config']['runtime']['testtools'])):
                require(str(path) in m['code'] and sha(path.read_bytes())==m['code'][str(path)])
    outcome['origins']=origins
    os.environ['EASEL_B2_TEST_AUTHORITY_FILE']=m['authority_path']
    authority=selected_authority()
    require(all(getattr(authority,k)==m['source'][k] for k in ('container_id','image','nonce')))
    for key in tuple(os.environ):
        if key.startswith(('PG','EASEL')):os.environ.pop(key,None)
    source_owners=[];generators=[];fds=[];closers=[];runner=None;stageobj=None;inputs=None;parser_runner=None;finalobj=None;factory=None;handles=[];execution_owner=None
    cancellation=threading.Event();_ACTIVE_EVENTS.append(cancellation)
    observer=SourceObserver(m,directory)
    def check_budget():
        check_manifest(m)
        free=os.statvfs(directory)
        require(free.f_bavail*free.f_frsize>=SCOPE['running_free'])
    try:
        observer.inspect()
        # Attach explicit non-finalizing owners BEFORE setup or native connection entry.
        for _ in range(2):
            check_budget()
            env=SourceOwner(authority,MIGRATIONS_DIR,check_budget,cancellation)
            source_owners.append(env)
            env.bind_journal(directory/'source-ownership',dict(operation_nonce=m['operation_nonce'],
                started=m['started'],deadline=m['deadline'],source_container_id=authority.container_id,
                manifest_sha256=sha(canonical(m))))
            env.prepare()
            require(env.name.startswith('easel_s1_test_b2r_') and env.container_id==authority.container_id)
            if len(source_owners)==1: central=env
            else: identity=env
        owner=(os.getuid(),os.getgid())
        async with central.connection('owner') as connection:
            await install_control_schema(connection,SchemaProfile(PUBLIC_016.name,'public',
                central.roles['runtime'],central.roles['controller'],tuple(central.migrations)))
            await connection.commit()
        for name in ('media','control','capture','config','backups','anchors','stage-parent'):
            mkdir_private(directory/name)
            fd=os.open(directory/name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW);fds.append(fd)
            require((os.fstat(fd).st_uid,os.fstat(fd).st_gid)==owner)
        media,control,capture,config,backup,anchor,stage_parent=fds
        parser_work=os.open(directory/'parser-work',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW);fds.append(parser_work)
        write_new(directory/'control/media.lock',b'')
        write_new(directory/'config/maintenance',b'synthetic-backup-integration')
        locks=ControlLock(control,'media.lock',owner=owner);closers.append(locks)
        controller=DatabaseControl(central.factory('controller'),locks=locks)
        configs=ConfigInputs(config,{'central':{'maintenance':'maintenance'},'identity':{},'media':{}},owner=owner);closers.append(configs)
        contexts=[];catalogs={}
        api=dict(catalog=read_source_catalog,raw_rows=_raw_rows,parse=parse_restore_profile,
                 canonical=canonical,digest=digest,context=ComparisonContext)
        for kind,env in (('central',central),('identity',identity)):
            check_budget()
            with env.sync_connection('owner') as connection:
                ctx,catalog=context_from_source(connection,env,PUBLIC_016,kind,api)
                contexts.append((kind,ctx));catalogs[kind]=catalog
        outcome['independent_contexts_before_capture']=True
        outcome['context_sha256']=[sha(canonical(dict(kind=k,catalog=v.profile.catalog_sha256,
            row_pins=v.row_order_pins))) for k,v in contexts]
        inputs=OwnedInputs(central.dsn('owner'),identity.dsn('owner'),PUBLIC_016,
            tuple(central.roles.values()),tuple(identity.roles.values()),media,capture,configs,owner,None,catalogs,SCOPE['capture_limit'])
        closers.append(inputs)
        inputs.role_admin_dsns={kind:make_conninfo(central.base,dbname=env.name) for kind,env in (('central',central),('identity',identity))}
        prefix=(DOCKER_PATH,'--host',SOCKET,'exec')
        runner=ChildRunner((*prefix,'--env','PGUSER','--env','PGDATABASE','--env','PGPASSWORD',authority.container_id,'pg_dump'),
                           (*prefix,'-i',authority.container_id,'pg_restore'))
        public,private,_=synthetic_keys()
        write_new(directory/'synthetic-private.pem',private)
        policy=RootPolicy.from_operator(backup,owner=owner);closers.append(policy)
        backup_id=str(uuid4());target='synthetic-b3-final-preflight'
        check_budget()
        result=await build_backup(controller,PUBLIC_016,inputs,public,backup,anchor,
            private_pem=private,backup_id=backup_id,target=target,child_runner=runner,root_policy=policy)
        write_new(directory/'capture-result.private.json',canonical(result))
        require(result['status']=='backup_committed' and result['commit_persisted'] is True
                and result['window_restored'] is True and result['window_state']=='OPEN')
        require(not runner.children and not runner.unknown_refs)
        outcome['capture_committed']=True
        require(not graph_state(locals())['unknown'])
        source_baseline=observer.snapshot(central,identity,psycopg,PUBLIC_016)
        write_new(directory/'source-baseline.private.json',canonical(source_baseline))
        check_budget()
        stage_authority=PreflightAuthority(restore_id='b3-'+m['operation_nonce'][:32],stage_name='authenticated-stage',
            started_at=m['started'],deadline=m['deadline'],reserved_bytes=SCOPE['reservation'],
            minimum_free_bytes=SCOPE['running_free'],contexts=tuple(contexts),
            limits=StageLimits(max_file_bytes=64*1024**2,max_archive_bytes=128*1024**2),cancellation=cancellation)
        job=os.open(backup_id,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=backup);fds.append(job)
        with trusted_anchor_intent(anchor,policy=policy,backup_id=backup_id,target=target,commit_sha256=result['commit_sha256']) as intent:
            stageobj=prepare_authenticated_stage(intent,job,private,stage_authority,stage_parent)
            stageobj.verify()
            descriptor=parser_descriptor
            # Root descriptor retains the independently pinned FULL raw inspect.
            # ParserAuthority consumes only the approved canonical projection.
            parser_inspect=inspect_projection(closed_json(base64.b64decode(descriptor['inspect_base64'],validate=True)))
            parserauthority=ParserAuthority(cid=descriptor['cid'],nonce=stage_authority.restore_id,
                image_id=descriptor['image_id'],docker_path=DOCKER_PATH,docker_sha256=DOCKER_SHA,
                socket=SOCKET,docker_config_dir=str(directory/'parser-config'),pg_restore_sha256=PARSER_TOOL_SHA,
                expected_inspect=parser_inspect,
                expected_inspect_sha256=sha(parser_inspect),work_dir_fd=parser_work,
                uid=owner[0],gid=owner[1],started_at=m['started'],deadline=m['deadline'],
                reserved_bytes=64*1024**2,minimum_free_bytes=SCOPE['running_free'],cancellation=stage_authority.cancellation)
            parser_runner=NetworkNoneParserRunner(parserauthority)
            toc_policies=tuple((kind,template_policy(Path(TEMPLATE_PINS[kind]['path']).read_bytes(),
                TEMPLATE_PINS[kind],env.roles['owner'],TocPolicy)) for kind,env in (('central',central),('identity',identity)))
            inspection_policy=DumpInspectionPolicy(stage_authority.restore_id,m['started'],m['deadline'],
                tuple((kind,ctx.profile.name,_profile_sha256(ctx),ctx.profile.catalog_sha256) for kind,ctx in contexts),
                toc_policies,_parser_sha256(parserauthority))
            finalobj=inspect_authenticated_stage(stageobj,parser_runner,inspection_policy)
            require(type(finalobj) is VerifiedRestoreInput)
            finalobj.verify();receipt=safe_receipt(finalobj.safe_receipt)
            require(receipt['parser']==dict(cid=parserauthority.cid,image_id=parserauthority.image_id,
                tool_sha256=PARSER_TOOL_SHA,docker_sha256=DOCKER_SHA,inspect_sha256=parserauthority.expected_inspect_sha256,
                authority_sha256=_parser_sha256(parserauthority)))
            factory=make_factory(m,envelope,stage_authority,contexts,directory,fds)
            if envelope['config']['format']=='b3-target-root-config-attest-v1':
                from platform_service.media_backup_b3.execution import (RootExecutionOwner,RootExecutionPolicy,
                    WorkerPins,TargetProbePins,ExecutionLimits)
                data=envelope['config']['execution_policy']
                worker=dict(data['worker']);worker['public_env']=tuple(worker['public_env'])
                probe=dict(data['target_probe']);probe['tools']=tuple(tuple(row) for row in probe['tools'])
                policy=RootExecutionPolicy(WorkerPins(**worker),TargetProbePins(**probe),ExecutionLimits(**data['limits']))
                execution_owner=RootExecutionOwner.__new__(RootExecutionOwner)
                RootExecutionOwner.__init__(execution_owner,factory,finalobj,policy)
            for kind in ('central','identity'):
                check_budget();handles.append(execution_owner.create_target(kind) if execution_owner is not None else factory.create(finalobj,kind))
            target_receipts=[handle.safe_receipt() for handle in handles]
            if execution_owner is not None:
                # ATTEST reports the original full ComparisonContext pins used
                # by its bindings; target-only keeps the pre-capture projection.
                outcome['context_sha256']=list(execution_owner._context_pins)
                for kind,target in zip(('central','identity'),target_receipts):target['target_cid']=execution_owner._targets[kind].cid
                summaries=[]
                for kind in ('central','identity'):
                    check_budget();summary=execution_owner.attest(kind)
                    attempt=execution_owner._attempts[kind]
                    from platform_service.media_backup_b3.execution_contract import _binding_data
                    require(summary['binding_sha256']==sha(canonical(_binding_data(attempt.execution_policy.binding))))
                    summaries.append(dict(kind=kind,**summary,binding_data=_binding_data(attempt.execution_policy.binding),input_sha256=execution_owner._input_pin,
                        context_sha256=execution_owner._context_pins[0 if kind=='central' else 1],
                        target_identity_sha256=execution_owner._targets[kind].identity_pin,
                        job_device=execution_owner._job[0],job_inode=execution_owner._job[1],
                        started_at_ns=execution_owner._started_at_ns,deadline_ns=execution_owner._deadline_ns))
                outcome['execution']=dict(format='b3-target-worker-attestation-v1',target_bound=True,
                    attestation_complete=True,SQL_restore=False,isolated_restore_verified=False,
                    input_sha256=execution_owner._input_pin,context_sha256=dict(zip(('central','identity'),execution_owner._context_pins)),
                    job_device=execution_owner._job[0],job_inode=execution_owner._job[1],
                    started_at_ns=execution_owner._started_at_ns,deadline_ns=execution_owner._deadline_ns,
                    summaries=summaries)
            require(not graph_state(locals())['unknown'])
            source_post=observer.snapshot(central,identity,psycopg,PUBLIC_016)
            write_new(directory/'source-post.private.json',canonical(source_post))
            require(source_post==source_baseline)
            outcome.update(targets=target_receipts,source_preservation=dict(
                scope='post-capture-to-post-target-before-own-source-cleanup',
                baseline_sha256=sha(canonical(source_baseline)),post_sha256=sha(canonical(source_post)),equal=True))
            normal_graph=locals().copy()
            normal=graph_state(normal_graph)
            outcome['normal_work_state']=normal
            require(not normal['unknown'] and parser_runner.remote_state=='TERMINAL')
            if execution_owner is None:
                finalobj.close();finalobj=None;stageobj=None
        require(stat.S_ISDIR(os.stat(directory/'stage-parent/authenticated-stage',follow_symlinks=False).st_mode))
        os.fstat(stage_parent);os.fstat(job)
        require(os.listdir(directory/'stage-parent/authenticated-stage/media')==[])
        files={}
        for name in sorted(FIXED):
            raw,custody=private_read(directory/'stage-parent/authenticated-stage'/name,64*1024**2)
            require(custody['file'][3:5]==list(owner));files[name]=dict(size=len(raw),sha256=sha(raw))
        outcome.update(receipt=receipt,files=files,stage_retained_after_close=execution_owner is None,caller_fds_alive=True,
                       dump_preflight_complete=True,SQL_restore=False)
    finally:
        # Preserve the *original* graph before any close can release ownership.
        graph=locals().copy()
        graph['operation_failed']=sys.exc_info()[0] is not None
        state=graph_state(graph)
        unknown=state['unknown']
        outcome['final_work_state']=state
        outcome['child_or_remote_unknown']=unknown
        if unknown:
            graph['active_error']=sys.exc_info()
            if graph['active_error'][0] is not None:
                outcome['failure_class']=graph['active_error'][0].__name__
            hold_unknown(graph,m,directory,outcome)
        else:
            cleanup_known(graph,outcome)
            if outcome.get('cleanup_refused_live_work'):
                graph.setdefault('active_error',sys.exc_info())
                hold_unknown(graph,m,directory,outcome)
        if execution_owner is not None and outcome.get('synthetic_cleanup_complete') and not graph_state(graph)['unknown']:
            require(stat.S_ISDIR(os.stat(directory/'stage-parent/authenticated-stage',follow_symlinks=False).st_mode))
            outcome['stage_retained_after_close']=True
        if graph.get('cleanup_traces'):
            write_new(directory/'cleanup-exceptions.private.txt','\n'.join(graph['cleanup_traces']).encode())


# The unresolved path must not return to ordinary coroutine/interpreter teardown.
_QUARANTINED=[]
_RETAINED=[]
_ACTIVE_EVENTS=[]


def cleanup_known(graph,outcome):
    """Single original cleanup phase; first ambiguity retains every remaining item."""
    if graph.get('cleanup_failed'):
        outcome['cleanup_refused_live_work']=True
        return
    if graph.get('cleanup_attempted'):return
    state=graph_state(graph)
    if (state['unknown'] and not graph.get('release_unknown_refs')) or not disposition_work_terminal(state):
        outcome['cleanup_refused_live_work']=True
        return
    execution_owner=graph.get('execution_owner');retained=False
    if execution_owner is not None and state['execution_unknown']:
        matched=graph.get('matched_disposition')
        if not matched or not disposition_permits(matched[0],matched[1],matched[2],graph=graph):
            outcome['cleanup_refused_live_work']=True;return
        try:
            document_hash=sha(canonical(matched[0]))
            execution_owner._retain_disposition(document_hash,matched[0]['execution_owner_sha256'])
            require(execution_owner.snapshot()['execution_disposition_retained'] is True)
            graph['disposition_audit']=[dict(phase='EXECUTION_RETAIN',state='RETAINED',resource=execution_owner,
                document_sha256=document_hash,binding_sha256=matched[0]['execution_owner_sha256'])]
            graph['execution_disposition_state']='RETAINED';retained=True
            outcome.update(execution_disposition_state='RETAINED',disposition_sha256=document_hash,
                remote_status_unknown=True,success=False,status='FAILED')
        except BaseException:
            graph['retention_error']=sys.exc_info();outcome['cleanup_refused_live_work']=True;return
    graph['cleanup_attempted']=True
    graph['cleanup_attempts']=[]
    if all(g is not graph for g in _RETAINED):_RETAINED.append(graph)
    resources=[];inputs=graph.get('inputs')
    admission=graph.get('role_admission')
    if admission is not None:
        try:
            require(admission['owner'] is execution_owner and inputs is not None and inputs.companion is admission['pair'])
            role=execution_owner.role_admission_snapshot()
            require(not role['unknown'] and not role['observation_error'])
            if not role['closed']:resources.append(('ROLE_CONSUMER',execution_owner))
            if state.get('source_publication_fds',0):resources.append(('SOURCE_PUBLICATION',admission['pair']))
        except BaseException:
            outcome['cleanup_refused_live_work']=True;graph['cleanup_attempted']=False;return
    elif state.get('source_publication_fds',0):
        outcome['cleanup_refused_live_work']=True
        graph['cleanup_attempted']=False
        return
    if execution_owner is not None and not retained:resources.append(('EXECUTION_CLOSE',execution_owner))
    if inputs is not None and not graph.get('inputs_closed'):resources.append(('INPUTS',inputs))
    if graph.get('release_unknown_refs') and graph.get('runner'):
        resources.extend(('REF',r) for r in graph['runner'].unknown_refs)
    final=graph.get('finalobj') or graph.get('stageobj')
    if final is not None:resources.append(('FINAL',final))
    resources.extend(('CLOSER',r) for r in reversed(graph.get('closers',[])) if r is not inputs)
    resources.extend(('SOURCE',r) for r in reversed(graph.get('source_owners',[])))
    resources.extend(('LEGACY',r) for r in reversed(graph.get('generators',[])))
    resources.extend(('FD',fd) for fd in reversed(graph.get('fds',[])))
    if retained:
        excluded={slot.fd for slot in execution_owner._fd_slots}
        excluded.update(fd for a in execution_owner._attempts.values() for native in a.operations for pair in native.fds for fd in pair
            if (id(native),fd) not in execution_owner._native_closed)
        resources=[(phase,resource) for phase,resource in resources
            if not (phase=='FD' and resource in excluded) and resource is not execution_owner]
    graph['cleanup_resources']=resources
    outcome.pop('cleanup_refused_live_work',None)
    outcome['cleanup_error_classes']=[];graph['cleanup_traces']=[]
    for cursor,(phase,resource) in enumerate(resources):
        graph['cleanup_phase']=phase;graph['cleanup_current']=resource
        attempt=dict(cursor=cursor,phase=phase,resource=resource,state='ACTIVE')
        graph['cleanup_attempts'].append(attempt)
        try:
            if phase=='ROLE_CONSUMER':
                resource._close_role_plan_consumer()
                require(resource.role_admission_snapshot()['consumer_held'] is False)
            elif phase=='SOURCE_PUBLICATION':
                release_owned_source_capture(resource)
                require(resource.snapshot()['publication_fds']==0)
            elif phase=='EXECUTION_CLOSE':
                attempt['host_fd_slots']=tuple(resource._fd_slots)
                attempt['native_close_attempts']=resource._native_close_attempted
                attempt['native_close_outcomes']=resource._native_closed
                resource.close()
                closed_state=resource.snapshot()
                attempt['close_snapshot']=closed_state
                require(not any(closed_state[k] for k in ('execution_unknown','execution_observation_error',
                    'execution_local_live_or_unobserved','execution_remote_live_or_unobserved','execution_active_work')))
            elif phase=='FD':os.close(resource)
            elif phase=='SOURCE':resource.cleanup(graph)
            else:resource.close()
            attempt['state']='TERMINAL'
            if phase=='INPUTS':graph['inputs_closed']=True
        except BaseException as error:
            attempt.update(state='UNKNOWN',error=error,exc_info=sys.exc_info())
            graph['cleanup_failed']=True;graph['active_error']=sys.exc_info()
            graph['cleanup_traces'].append(traceback.format_exc())
            outcome['cleanup_error_classes'].append(type(error).__name__)
            outcome.update(success=False,status='FAILED',cleanup_refused_live_work=True,
                synthetic_cleanup_complete=False,remote_status_unknown=True)
            return
    graph['cleanup_phase']='COMPLETE';graph['cleanup_current']=None
    outcome['synthetic_cleanup_complete']=True
    outcome['final_work_state']=graph_state(graph)


def disposition_permits(value,m,manifest_hash,*,graph=None):
    owner=None if graph is None else graph.get('execution_owner')
    expected={'action','operation_nonce','manifest_sha256','remote_terminal_confirmed'}
    if owner is not None:expected.add('execution_owner_sha256')
    if not (type(value) is dict and set(value)==expected and value['action']=='cleanup-owned-synthetic'
        and value['operation_nonce']==m['operation_nonce'] and value['manifest_sha256']==manifest_hash
        and value['remote_terminal_confirmed'] is True):return False
    if owner is not None:
        try:
            require(not graph.get('cleanup_failed'))
            state=graph_state(graph)
            require(state['execution_owner_count']==1 and not state['execution_observation_error'])
            require(type(value['execution_owner_sha256']) is str
                and value['execution_owner_sha256']==owner._retention_binding())
        except BaseException:return False
    return True


def quarantine_unknown(graph,m,outcome,*,publish,observe,pause,manifest_hash):
    """Hold original handles until explicit Root disposition; no automatic exit.

    Hooks isolate pure lifecycle tests. The actual path supplies only private
    status/disposition FS IO and observation sleep, never new active work.
    """
    _QUARANTINED.append(graph)
    outcome.update(success=False,status='FAILED',quarantine_entered=True,cleanup_deferred_unknown_remote=True,
                   synthetic_cleanup_complete=False,remote_status_unknown=True,
                   original_deadline_unchanged=True)
    def observation_error(error):
        classes=outcome.setdefault('observation_error_classes',[])
        if type(error).__name__ not in classes and len(classes)<16:classes.append(type(error).__name__)
    try:publish(dict(outcome))
    except BaseException as error:observation_error(error)
    while True:
        try:
            disposition=observe()
            if disposition_permits(disposition,m,manifest_hash,graph=graph) and disposition_work_terminal(graph_state(graph)):
                # Root has observed original remote handles and now explicitly
                # authorizes disposal of this exact graph, never a replacement.
                graph['matched_disposition']=(copy.deepcopy(disposition),dict(m),manifest_hash)
                graph['release_unknown_refs']=True
                outcome.update(root_disposition_applied=True,remote_status_unknown=graph.get('execution_owner') is not None,
                    disposition_work_state=graph_state(graph))
                cleanup_known(graph,outcome)
                if outcome.get('cleanup_refused_live_work'):
                    pause();continue
                _QUARANTINED.remove(graph)
                return
            pause()
        except BaseException as error:
            # Ordinary exceptions/interrupts must not unwind suspended fixtures.
            observation_error(error)
            try:pause()
            except BaseException as error:observation_error(error)


def hold_unknown(graph,m,directory,outcome):
    # Observation keeps ownership after the active budget is over. It does not
    # authorize additional active work or claim cancellation/extend its deadline.
    def signal_observed(signum,frame):
        outcome['observation_signal_received']=True
    outcome['original_deadline']=m['deadline']
    def observe():
        try:raw,_=private_read(directory/'root-disposition.private.json',4096)
        except FileNotFoundError:return None
        return json.loads(raw)
    def publish(value):
        for number in (signal.SIGALRM,signal.SIGTERM,signal.SIGINT):
            signal.signal(number,signal_observed)
        signal.setitimer(signal.ITIMER_REAL,0)
        error=graph.get('active_error')
        if error and error[0] is not None:
            write_new(directory/'quarantine-exception.private.txt',
                      ''.join(traceback.format_exception(*error)).encode())
        write_new(directory/'result.safe.json',canonical(value))
    quarantine_unknown(graph,m,outcome,
        publish=publish,
        observe=observe,pause=lambda:time.sleep(1),manifest_hash=outcome['manifest_sha256'])



# Task3B2c owned qualification additions. Historical source provenance is frozen
# in the operator packet; no ignored historical runtime/result is imported.
SOURCE_CID='50a0546be54eaffc51aec9be6af238c6b54637f5e215294fe2c22a2fb3a93853'
TOOLS=(('pg_isready','/usr/lib/postgresql/16/bin/pg_isready','dd551a6d129ec93fe3b307a7f7634c2753eb742c5ef786e2189f349d357f71f2'),
       ('psql','/usr/lib/postgresql/16/bin/psql','2cbb857ac3889d22cfe0dce6581cf2321ce69bcb253cc8b55b981914cd3ee22a'),
       ('cat','/usr/bin/cat','0d5a5dd543ed8d31220320eb513e4680ab303e48cb22a5edbf27a5a91561a71e'),
       ('sha256sum','/usr/bin/sha256sum','7911a1f0aec4678c540bd78ff886f48d42790c8689a82ed0ae25ed79ed5dd512'))


def fd_read(fd,maximum=16*1024**2,empty=False):
    require(type(fd) is int and fd>=0)
    before=os.fstat(fd)
    require(stat.S_ISREG(before.st_mode) and stat.S_IMODE(before.st_mode)==0o600
        and (before.st_uid,before.st_gid)==(os.geteuid(),os.getegid())
        and before.st_nlink==1 and before.st_size<=maximum)
    raw=os.pread(fd,maximum+1,0)
    require(len(raw)==before.st_size and stamp(before)==stamp(os.fstat(fd)))
    require(not empty or raw==b'')
    return raw


def report_write(fd,value):
    fd_read(fd,empty=True)
    raw=canonical(value);require(len(raw)<=1024**2)
    offset=0
    while offset<len(raw):
        count=os.pwrite(fd,raw[offset:],offset);require(count>0);offset+=count
    os.fsync(fd)


def closed(value,keys):
    require(type(value) is dict and set(value)==set(keys.split()))


def hash_value(value):
    require(type(value) is str and re.fullmatch('[a-f0-9]{64}',value) is not None)


def pinned_file(pin,maximum=16*1024**2):
    closed(pin,'path sha256')
    hash_value(pin['sha256']);raw,_=private_read(pin['path'],maximum)
    require(sha(raw)==pin['sha256']);return raw


def validate_config(c,now=None):
    attest=c.get('format')=='b3-target-root-config-attest-v1'
    closed(c,'format directory started deadline operation_nonce source authority runtime source_proof capacity_proof original_job'+(' execution_policy' if attest else ''))
    require(c['format'] in ('b3-target-root-config-v1','b3-target-root-config-attest-v1'))
    if attest:
        policy=c['execution_policy'];closed(policy,'worker target_probe limits')
        worker=policy['worker'];probe=policy['target_probe'];limits=policy['limits']
        closed(worker,'image_id architecture artifact_sha256 dependency_manifest_sha256 entrypoint_sha256 observer_sha256 pg_restore_sha256 image_inspect_sha256 public_env')
        closed(probe,'image_id image_manifest_sha256 image_config_sha256 tools dependency_closure_sha256 probe_schema_sha256')
        closed(limits,'cpu_nanos memory_bytes memory_swap_bytes pids_limit tmpfs_bytes envelope_limit private_result_limit diagnostic_limit')
        for row in (worker,probe):
            for name,value in row.items():
                if name.endswith('sha256'):hash_value(value)
        require(type(worker['image_id']) is str and re.fullmatch('sha256:[a-f0-9]{64}',worker['image_id']))
        require(worker['architecture'] in ('arm64','amd64') and probe['image_id']==PARSER_IMAGE)
        env=worker['public_env'];require(type(env) is list and len(env)==5)
        for value,key in zip(env,('PATH','LANG','GPG_KEY','PYTHON_VERSION','PYTHON_SHA256')):
            require(type(value) is str and value.startswith(key+'=') and len(key)+1<len(value)<=len(key)+4097
                and all(32<=ord(ch)<127 for ch in value))
            content=value.split('=',1)[1]
            if key=='PATH':
                require(all(path.startswith('/') and all(p not in ('','.','..') for p in path[1:].split('/')) for path in content.split(':')))
            elif key=='LANG':require(content in ('C','C.UTF-8','en_US.utf8'))
            elif key=='GPG_KEY':require(re.fullmatch('[A-F0-9]{40}',content) is not None)
            elif key=='PYTHON_VERSION':require(re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+',content) is not None)
            else:hash_value(content)
        tools=probe['tools'];require(type(tools) is list and len(tools)==4)
        for row,role in zip(tools,('sha256sum','stat','readlink','cat')):
            require(type(row) is list and len(row)==3 and row[:2]==[role,'/usr/bin/'+role]);hash_value(row[2])
        for name,maximum in zip(('cpu_nanos','memory_bytes','memory_swap_bytes','pids_limit','tmpfs_bytes','envelope_limit','private_result_limit','diagnostic_limit'),(2*10**9,2*1024**3,2*1024**3,128,256*1024**2,65536,1024**2,65536)):
            require(type(limits[name]) is int and 0<limits[name]<=maximum)
        require(limits['memory_bytes']==limits['memory_swap_bytes'])
    require(type(c['started']) is float and type(c['deadline']) is float
        and c['deadline']==c['started']+900)
    now=time.monotonic() if now is None else now
    require(c['started']<=now<c['deadline'])
    hash_value(c['operation_nonce'])
    require(type(c['directory']) is str and Path(c['directory']).is_absolute()
        and str(Path(c['directory']))==os.path.normpath(c['directory'])
        and '..' not in Path(c['directory']).parts)
    closed(c['source'],'container_id image nonce sha256')
    require(c['source']['container_id']==SOURCE_CID)
    check_source_image(c['source']['image']);hash_value(c['source']['sha256'])
    require(re.fullmatch('[a-f0-9]{32}',c['source']['nonce']) is not None)
    closed(c['authority'],'path sha256');require(c['authority']['sha256']==c['source']['sha256'])
    closed(c['runtime'],'prefix testtools dependencies')
    require(type(c['runtime']['dependencies']) is dict and bool(c['runtime']['dependencies']))
    for path,digest in c['runtime']['dependencies'].items():
        require(Path(path).is_absolute());hash_value(digest)
    for name in ('source_proof','capacity_proof','original_job'):closed(c[name],'path sha256')
    return c


def dependency_hashes(runtime):
    # Root runtime inventories are checked only in Root prepare/execute. The
    # implementer never opens these paths. Repo closure is complete and frozen.
    files={SELF,SOURCE/'scripts/verify-b3-target-lifecycle.py',SOURCE/'scripts/b3_target_launch.py'}
    for root in (SOURCE/'src',SOURCE/'scripts',SOURCE/'tests'):
        files.update(p for p in root.rglob('*') if p.is_file()
            and '__pycache__' not in p.parts and p.suffix!='.pyc')
    result={str(p):sha(p.read_bytes()) for p in sorted(files)}
    for path,digest in runtime['dependencies'].items():
        require(sha(Path(path).read_bytes())==digest);result[path]=digest
    for name in ('execution.py','execution_io.py','execution_baseline.py','execution_observer.py','execution_contract.py'):
        require(str(SOURCE/'src/platform_service/media_backup_b3'/name) in result)
    for name,expected in APPROVED_API.items():
        require(result[str(SOURCE/'src/platform_service/media_backup_b3'/name)]==expected)
    return result


def source_document(c):
    raw=pinned_file(c['authority'],16384);data=closed_json(raw)
    require(data['container_id']==SOURCE_CID and data['host']=='127.0.0.1' and data['port']==51430)
    require(all(data[k]==c['source'][k] for k in ('container_id','image','nonce')))
    source_binding(c['authority']['path'],c['source'])
    return data


def proof_documents(c):
    original=closed_json(pinned_file(c['original_job']))
    closed(original,'format operation_nonce started deadline supervisor_pid supervisor_nonce')
    require(original['format']=='b3-root-original-job-v1' and all(original[k]==c[k] for k in ('operation_nonce','started','deadline')))
    require(type(original['supervisor_pid']) is int and original['supervisor_pid']>0
        and type(original['supervisor_nonce']) is str and bool(original['supervisor_nonce']))
    hash_value(original['supervisor_nonce'])
    source=closed_json(pinned_file(c['source_proof']))
    capacity=closed_json(pinned_file(c['capacity_proof']))
    closed(source,'format operation_nonce started deadline observed_at container_id image_id system_id_sha256 volume_names admin_name inspect_sha256 inspect_base64 tools_sha256')
    closed(capacity,'format operation_nonce started deadline measured_at device peak_bytes reserved_bytes measurement_sha256 measurement_path')
    require(source['format']=='b3-independent-source-proof-v1' and capacity['format']=='b3-independent-capacity-proof-v1')
    for doc in (source,capacity):
        require(all(doc[k]==c[k] for k in ('operation_nonce','started','deadline')))
    require(c['started']<=source['observed_at']<c['deadline'])
    require(source['container_id']==SOURCE_CID and source['image_id']==PARSER_IMAGE)
    for name in ('system_id_sha256','inspect_sha256','tools_sha256'):hash_value(source[name])
    inspect_raw=base64.b64decode(source['inspect_base64'],validate=True)
    require(sha(inspect_raw)==source['inspect_sha256'])
    inspection=closed_json(inspect_raw)
    require(canonical(inspection)==inspect_raw and inspection['Id']==SOURCE_CID and inspection['Image']==PARSER_IMAGE
        and inspection['State']['Running'] is True and inspection['State']['Pid']>0 and not inspection['ExecIDs'])
    require(sorted(mount['Name'] for mount in inspection['Mounts'] if mount['Type']=='volume')==sorted(source['volume_names']))
    require(type(source['volume_names']) is list and bool(source['volume_names']))
    require(type(capacity['device']) is int and type(capacity['peak_bytes']) is int
        and type(capacity['reserved_bytes']) is int and capacity['peak_bytes']>=0
        and capacity['reserved_bytes']>=10*1024**3+capacity['peak_bytes'])
    require(c['started']<=capacity['measured_at']<c['deadline']);hash_value(capacity['measurement_sha256'])
    measurement=closed_json(pinned_file(dict(path=capacity['measurement_path'],sha256=capacity['measurement_sha256'])))
    validate_measurement(measurement,capacity,c)
    return source,capacity


def target_specs(m,roles):
    from platform_service.media_backup_b3.target_contract import TargetSpec
    from platform_service.media_backup_b3.target_lifecycle import _PUBLIC_ENV
    source=m['source_proof'];result=[]
    for kind in ('central','identity'):
        pin=m['targets'][kind];raw,_=private_read(pin['env_file_path'],16384)
        require(sha(raw)==pin['env_file_sha256'])
        result.append(TargetSpec(restore_id='b3-'+m['operation_nonce'][:32],kind=kind,
            operation_nonce=pin['operation_nonce'],started_at=m['started'],deadline=m['deadline'],
            image_id=PARSER_IMAGE,source_container_id=SOURCE_CID,
            source_system_id_sha256=source['system_id_sha256'],source_volume_names=tuple(source['volume_names']),
            source_admin_name=source['admin_name'],restored_role_names=tuple(sorted(roles)),bootstrap_role=pin['bootstrap_role'],
            docker_path=DOCKER_PATH,docker_socket=SOCKET,docker_config_path=str(Path(m['directory'])/'target-config'),
            env_file_path=pin['env_file_path'],env_file_sha256=pin['env_file_sha256'],
            expected_env_sha256=sha(canonical(sorted(_PUBLIC_ENV+tuple(raw.decode().splitlines())))),
            admission_bytes=8*1024**3,running_budget_bytes=2*1024**3))
    return tuple(result)


def make_factory(m,envelope,authority,contexts,directory,fds):
    from platform_service.media_backup_b3.target_factory import RootTargetFactory,RuntimeToolPolicy
    from platform_service.media_backup_b3.target_lifecycle import RootSourceProof,RootCapacityProof,RootFactoryPolicy,_PUBLIC_ENV
    from platform_service.media_backup_b3.target_contract import build_target_plan
    source,capacity=proof_documents(envelope['config'])
    require(source==m['source_proof'] and capacity==m['capacity_proof'])
    roles=tuple(sorted(set().union(*(set(ctx.role_names) for _,ctx in contexts))))
    specs=target_specs(m,roles)
    require([dict(volume=list(build_target_plan(s).volume_argv),container=list(build_target_plan(s).container_argv)) for s in specs]==m['target_plans'])
    fd=os.open(directory,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW);fds.append(fd)
    require(os.fstat(fd).st_dev==capacity['device'])
    common=(authority.restore_id,m['started'],m['deadline'])
    policy=RootFactoryPolicy(specs,
        RootSourceProof(*common,SOURCE_CID,source['system_id_sha256'],tuple(source['volume_names']),source['admin_name'],envelope['config']['source_proof']['sha256']),
        RootCapacityProof(*common,capacity['measured_at'],capacity['device'],capacity['peak_bytes'],capacity['reserved_bytes'],capacity['measurement_sha256']),
        authority.cancellation,str(directory),fd,os.getuid(),os.getgid(),DOCKER_SHA,TOOLS[:2],_PUBLIC_ENV)
    return RootTargetFactory(policy,RuntimeToolPolicy(*common,PARSER_IMAGE,TOOLS[2:]))


def prepare_fd(manifest_fd,authority_fd,report_fd):
    request=closed_json(fd_read(manifest_fd));closed(request,'format config_sha256')
    require(request['format']=='b3-target-prepare-request-v1')
    envelope=closed_json(fd_read(authority_fd));closed(envelope,'config')
    c=validate_config(envelope['config']);require(sha(canonical(c))==request['config_sha256'])
    fd_read(report_fd,empty=True)
    document=source_document(c);source,capacity=proof_documents(c)
    require(source['admin_name']==document['user'])
    require(Path(sys.prefix)==Path(c['runtime']['prefix']))
    code=dependency_hashes(c['runtime'])
    free=os.statvfs(Path(c['directory']).parent)
    require(free.f_bavail*free.f_frsize>=max(8*1024**3,capacity['reserved_bytes']))
    mkdir_private(c['directory']);directory=Path(c['directory'])
    require(os.stat(directory).st_dev==capacity['device'])
    m=dict(format='b3-target-once-manifest-v1',uid=os.getuid(),gid=os.getgid(),
        started=c['started'],deadline=c['deadline'],operation_nonce=c['operation_nonce'],directory=c['directory'],
        directory_custody=stamp(os.stat(directory))[:5],scope=SCOPE,source=c['source'],authority_path=c['authority']['path'],
        config_sha256=sha(canonical(c)),code=code,source_proof=source,capacity_proof=capacity,targets={})
    for name in ('parser-work','parser-config','target-config'):mkdir_private(directory/name)
    # Bootstrap secrets are independent fresh random bytes, never source reuse.
    for kind in ('central','identity'):
        nonce=secrets.token_hex(16);role='b3_boot_'+secrets.token_hex(16)
        raw=('POSTGRES_USER='+role+'\nPOSTGRES_PASSWORD='+secrets.token_urlsafe(48)+'\nPOSTGRES_DB=postgres\nPOSTGRES_HOST_AUTH_METHOD=scram-sha-256\nPOSTGRES_INITDB_ARGS=--encoding=UTF8 --locale=C --auth-host=scram-sha-256\n').encode()
        path=directory/(kind+'.env');write_new(path,raw)
        m['targets'][kind]=dict(operation_nonce=nonce,bootstrap_role=role,env_file_path=str(path),env_file_sha256=sha(raw))
    sys.path.insert(0,str(SOURCE/'src'))
    from platform_service.media_backup_b3.target_contract import build_target_plan
    # Rendering does not depend on later source-role union; actual construction
    # rechecks the exact union from independently captured contexts.
    specs=target_specs(m,('qualification_placeholder',))
    m['target_plans']=[dict(volume=list(build_target_plan(s).volume_argv),container=list(build_target_plan(s).container_argv)) for s in specs]
    m['parser_plan']=parser_plan(m)
    raw=canonical(m);write_new(directory/'manifest.private.json',raw)
    write_new(directory/'seal.private.sha256',sha(raw).encode())
    result=dict(prepared=True,manifest_sha256=sha(raw),original_deadline=m['deadline'],actual_executed=False)
    report_write(report_fd,result);return result


def launch_record(raw,m,manifest_hash,pid=None):
    value=closed_json(raw)
    closed(value,'format manifest_sha256 operation_nonce started deadline caller_pid caller_ppid supervisor_pid supervisor_nonce tool_session launched_at')
    require(value['format']=='b3-root-external-launch-v1')
    require(value['manifest_sha256']==manifest_hash and all(value[k]==m[k] for k in ('operation_nonce','started','deadline')))
    require(type(value['caller_pid']) is int and value['caller_pid']==(os.getpid() if pid is None else pid))
    require(type(value['caller_ppid']) is int and value['caller_ppid']>0
        and (pid is not None or value['caller_ppid']==os.getppid()))
    hash_value(value['supervisor_nonce'])
    require(type(value['supervisor_pid']) is int and value['supervisor_pid']>0 and value['supervisor_pid']!=value['caller_pid'])
    require(type(value['tool_session']) is str and 0<len(value['tool_session'])<=256)
    require(m['started']<=value['launched_at']<m['deadline'])
    return value


def execute_fd(manifest_fd,authority_fd,report_fd):
    raw=fd_read(manifest_fd);m=closed_json(raw);manifest_hash=sha(raw)
    fd_read(report_fd,empty=True)
    envelope=closed_json(fd_read(authority_fd));closed(envelope,'config parser_descriptor external_launch external_caller')
    c=validate_config(envelope['config']);directory=Path(m['directory'])
    require(c['directory']==m['directory'] and m['format']=='b3-target-once-manifest-v1')
    require(m['directory_custody']==stamp(os.stat(directory,follow_symlinks=False))[:5])
    stored,_=private_read(directory/'manifest.private.json');seal,_=private_read(directory/'seal.private.sha256')
    require(raw==stored and manifest_hash==seal.decode() and m['config_sha256']==sha(canonical(c)))
    require(envelope['external_launch']['path']==str(directory/'launch.once'))
    launch_raw=pinned_file(envelope['external_launch'])
    launch=prelaunch_record(launch_raw,m,manifest_hash)
    original=closed_json(pinned_file(c['original_job']))
    require(all(launch[k]==original[k] for k in ('supervisor_pid','supervisor_nonce')))
    require(envelope['external_caller']['path']==str(directory/'caller.once'))
    caller=closed_json(pinned_file(envelope['external_caller']))
    closed(caller,'launch_sha256 binding')
    require(caller['launch_sha256']==sha(launch_raw))
    launch_record(canonical(caller['binding']),m,manifest_hash)
    require(all(caller['binding'][k]==launch[k] for k in launch if k!='format'))
    # Exclusive durable burn precedes imports/native calls. Partial writes remain.
    write_new(directory/'execute.once',canonical(dict(manifest_sha256=manifest_hash,pid=os.getpid(),operation_nonce=m['operation_nonce'],deadline=m['deadline'])))
    outcome=dict(manifest_sha256=manifest_hash,actual_executed=False,success=False,status='FAILED',
        isolated_restore_verified=False,production=False,provider=False,timer=False)
    previous={}
    try:
        check_manifest(m);check_parser_start_budget(m)
        require(Path(sys.prefix)==Path(c['runtime']['prefix']))
        require(m['code']==dependency_hashes(c['runtime']))
        document=source_document(c);source,capacity=proof_documents(c)
        require(source['admin_name']==document['user'])
        require(source==m['source_proof'] and capacity==m['capacity_proof'])
        require(sha(Path(DOCKER_PATH).read_bytes())==DOCKER_SHA)
        require(m['parser_plan']==parser_plan(m))
        descriptor=validate_descriptor(pinned_file(envelope['parser_descriptor'],2*1024**2),m,manifest_hash)
        def expired(signum,frame):
            for event in _ACTIVE_EVENTS:event.set()
            outcome['signal_received']=True
            raise TimeoutError('qualification interrupted')
        for number in (signal.SIGALRM,signal.SIGTERM,signal.SIGINT):previous[number]=signal.signal(number,expired)
        signal.setitimer(signal.ITIMER_REAL,m['deadline']-time.monotonic())
        outcome['actual_executed']=True
        asyncio.run(actual(m,directory,outcome,descriptor,envelope))
        check_manifest(m)
        require(outcome.get('synthetic_cleanup_complete') and not outcome.get('quarantine_entered'))
        if c['format']=='b3-target-root-config-attest-v1':
            for state in (outcome.get('normal_work_state',{}),outcome.get('final_work_state',{})):
                require(state.get('execution_owner_count')==1 and state.get('execution_target_count')==2
                    and state.get('execution_phase_claims')==2 and state.get('execution_committed_count')==2
                    and type(state.get('execution_local_terminal_count')) is int and state['execution_local_terminal_count']>0
                    and not state.get('unknown',True))
            require(outcome.get('execution',{}).get('attestation_complete') is True)
            outcome.update(success=True,status='target_worker_attestation_verified')
        else:outcome.update(success=True,status='target_lifecycle_verified')
    except BaseException:
        write_new(directory/'exception.private.txt',traceback.format_exc().encode())
        outcome.update(success=False,status='FAILED',error_category='qualification_failed')
    finally:
        signal.setitimer(signal.ITIMER_REAL,0)
        for number,handler in previous.items():signal.signal(number,handler)
        report_write(report_fd,outcome)
    return outcome


def main():
    parser=argparse.ArgumentParser(description='Root synthetic target lifecycle qualification')
    mode=parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--prepare',action='store_true');mode.add_argument('--execute',action='store_true')
    for name in ('manifest','authority','report'):parser.add_argument('--'+name+'-fd',type=int,required=True)
    args=parser.parse_args()
    validate_fd_set(args.manifest_fd,args.authority_fd,args.report_fd)
    try:
        result=(prepare_fd if args.prepare else execute_fd)(args.manifest_fd,args.authority_fd,args.report_fd)
        return 0 if result.get('prepared') or result.get('success') else 1
    except BaseException:
        # Public category only; partial private files and once guards survive.
        print('qualification_rejected',file=sys.stderr);return 1


class SourceObserver:
    """Root-only fixed read operations; sticky interruption owns all original IO.

    Source thread work belongs to OwnedInputs. These synchronous reads remain in
    the original actor and are supervised externally; no background coroutine or
    finally can destroy source fixtures after an ambiguous native interruption.
    """
    def __init__(self,m,directory):
        self.m=m;self.directory=directory;self._processes=[];self._fds=[]
        self._unknown=False;self._active=False;self._sequence=0;self.connections=[]

    def inspect(self):
        import subprocess
        require(not self._unknown and not self._active);check_manifest(self.m)
        self._active=True
        try:
            streams=[]
            for suffix in ('out','err'):
                path=self.directory/('source-observe-'+str(self._sequence)+'-'+suffix)
                fd=os.open(path,os.O_RDWR|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
                self._fds.append(fd);streams.append(fd)
            self._sequence+=1
            child=object.__new__(subprocess.Popen);self._processes.append(child)
            subprocess.Popen.__init__(child,[DOCKER_PATH,'-H',SOCKET,'--config',str(self.directory/'target-config'),
                'inspect','--type','container',SOURCE_CID],stdin=subprocess.DEVNULL,stdout=streams[0],stderr=streams[1],
                env=dict(PATH='/usr/bin:/bin',LANG='C',LC_ALL='C',HOME=str(self.directory)),close_fds=True)
            while True:
                check_manifest(self.m)
                require(os.fstat(streams[0]).st_size<=2*1024**2 and os.fstat(streams[1]).st_size<=65536)
                rc=child.poll()
                if rc is not None:break
                time.sleep(.02)
            require(type(rc) is int and rc==0 and os.fstat(streams[1]).st_size==0)
            raw=os.pread(streams[0],2*1024**2+1,0);value=closed_json(raw)
            require(type(value) is list and len(value)==1)
            require(sha(canonical(value[0]))==self.m['source_proof']['inspect_sha256'])
            self._active=False;return value[0]
        except BaseException:
            self._unknown=True;raise

    def snapshot(self,central,identity,psycopg,profile):
        from psycopg.conninfo import make_conninfo
        from platform_service.media_backup_b2.capture import (read_source_catalog,_raw_rows,
            roles_inventory,role_credential_inventory,snapshot_copy_inventory,sequence_inventory)
        from platform_service.media_backup_b2.maintenance_snapshot import read_control_rows
        inspection=self.inspect();self._active=True
        try:
            result=dict(source_inspect=inspection,databases={},files={})
            for kind,env in (('central',central),('identity',identity)):
                check_manifest(self.m)
                connection=psycopg.connect(make_conninfo(env.base,dbname=env.name,connect_timeout=5),autocommit=True)
                self.connections.append(connection)
                connection.execute('BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY')
                connection.execute("SET LOCAL statement_timeout='5000ms'")
                connection.execute("SET LOCAL lock_timeout='1000ms'")
                require(connection.execute('SHOW transaction_read_only').fetchone()==('on',))
                require(connection.execute('SHOW transaction_isolation').fetchone()==('repeatable read',))
                system,admin=connection.execute('SELECT system_identifier::text,current_user FROM pg_control_system()').fetchone()
                require(sha(system.encode())==self.m['source_proof']['system_id_sha256'] and admin==self.m['source_proof']['admin_name'])
                roles=tuple(sorted(env.roles.values()))
                catalog=read_source_catalog(connection,profile)
                present=connection.execute("SELECT nspname FROM pg_namespace WHERE nspname='easel_backup_control'").fetchall()
                require(present==([('easel_backup_control',)] if kind=='central' else []))
                result['databases'][kind]=dict(catalog=catalog,
                    roles=roles_inventory(connection,roles,administrative_grantors=(admin,)),
                    credentials=role_credential_inventory(connection,roles),
                    rows=_raw_rows(connection,profile) if kind=='central' else {},
                    copy=snapshot_copy_inventory(connection),
                    sequences=sequence_inventory(connection,schemas=('public','easel_backup_control') if kind=='central' else ('public',)),
                    control=read_control_rows(connection) if kind=='central' else None,
                    system_id_sha256=sha(system.encode()),admin=admin)
                connection.execute('ROLLBACK');connection.close()
            for name in ('config','media'):
                root=self.directory/name
                require(not root.is_symlink())
                result['files'][name]={'directory':stamp(os.stat(root))}
                for path in sorted(root.rglob('*')):
                    require(path.is_file() and not path.is_symlink())
                    raw,custody=private_read(path,64*1024**2)
                    result['files'][name][str(path.relative_to(root))]=dict(sha256=sha(raw),custody=custody)
            require(len(canonical(result))<=32*1024**2)
            self._active=False;return result
        except BaseException:
            self._unknown=True;raise


def validate_fd_set(*fds):
    require(len(fds)==3 and len(set(fds))==3)
    identities=[]
    for fd in fds:
        fd_read(fd);info=os.fstat(fd);identities.append((info.st_dev,info.st_ino))
    require(len(set(identities))==3)


def validate_measurement(value,capacity,config):
    closed(value,'format operation_nonce started deadline measured_at device method components peak_bytes reserved_bytes')
    require(value['format']=='b3-root-capacity-measurement-v1' and value['method']=='measured-components-plus-conservative-peak')
    for key in ('operation_nonce','started','deadline','measured_at','device','peak_bytes','reserved_bytes'):
        require(value[key]==capacity[key])
    require(type(value['components']) is list and len(value['components'])==6)
    names=('source_capture','authenticated_stage','parser_outputs','central_target','identity_target','journals_and_logs')
    for item,name in zip(value['components'],names):
        closed(item,'name observation measured_bytes peak_allowance_bytes')
        require(item['name']==name and item['observation'] in ('measured-current','not-created'))
        require(type(item['measured_bytes']) is int and type(item['peak_allowance_bytes']) is int
            and 0<=item['measured_bytes']<=item['peak_allowance_bytes'])
        require(item['observation']!='not-created' or item['measured_bytes']==0)
    require(sum(item['peak_allowance_bytes'] for item in value['components'])==capacity['peak_bytes'])


def prelaunch_record(raw,m,manifest_hash):
    value=closed_json(raw)
    closed(value,'format manifest_sha256 operation_nonce started deadline supervisor_pid supervisor_nonce launched_at')
    require(value['format']=='b3-root-external-launch-intent-v1')
    require(value['manifest_sha256']==manifest_hash and all(value[k]==m[k] for k in ('operation_nonce','started','deadline')))
    require(type(value['supervisor_pid']) is int and value['supervisor_pid']>0)
    hash_value(value['supervisor_nonce'])
    require(m['started']<=value['launched_at']<m['deadline'])
    return value
