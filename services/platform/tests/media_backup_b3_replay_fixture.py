"""Matching source-only graph. Native SQL, codec and archive leaves are fakes.

Original owner/producers, fixed capture reads, retained policy/anchor/reader
objects, stage producer, input parser and owner/target state machines execute.
This fixture does not qualify real PG, encryption or archive decoding.
"""
from contextlib import ExitStack
import copy
import dataclasses as dc
import hashlib
import os
from pathlib import Path
import threading
from types import SimpleNamespace as NS
from unittest.mock import patch
from uuid import uuid4


def sha(raw):return hashlib.sha256(raw).hexdigest()


def matching_graph(test,*,with_graph=False):
    from platform_service.media_backup_b3 import preflight as pf, roles
    from platform_service.media_backup_b2 import reconstruction_capture as cap, reconstruction_context as sc
    from platform_service.media_backup_b2 import anchor,reader,wire
    from platform_service.media_backup_b2.capture import OwnedInputs,ChildRunner
    from platform_service.media_backup_b2.contracts import CapturedSource
    from platform_service.media_backup_b2.durable import _File,_stamp
    from platform_service.media_maintenance import filesystem as fs
    from b3_source_owner import SourceOwner
    from psycopg.conninfo import make_conninfo
    from media_backup.contract import encode_manifest
    from media_backup_fixture import capture
    from test_media_backup_b2_reconstruction_capture import Native,Keeper,Cursor
    import test_media_backup_b3_target_factory as fh
    import test_media_backup_b3_dump_preflight as dump
    import test_media_backup_b3_target_lifecycle as lifecycle
    import test_media_backup_b3_target_contract as render

    stack=ExitStack();test.addCleanup(stack.close)
    # Parser/native target leaves reuse the existing bounded synthetic fixtures.
    case=fh.FactoryTests();case.setUp();test.addCleanup(case.doCleanups)
    ic=dump.FinalInputTests();ic.setUp();test.addCleanup(ic.doCleanups)
    a=ic.a;contexts=tuple(c for _,c in a.contexts);cancel=a.cancellation
    if with_graph:
        contexts=tuple(dc.replace(c,role_names=('grantor','member','owner'),allowed_grantors=('source_admin',)) for c in contexts)
        a=dc.replace(a,contexts=tuple(zip(('central','identity'),contexts)))
    base=Path(ic.tmp.name) if hasattr(ic,'tmp') else ic.base
    root=base/'replay-graph';root.mkdir(mode=0o700)
    def directory(name):
        path=root/name;path.mkdir(mode=0o700)
        fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY);stack.callback(os.close,fd)
        return fd
    backup=directory('backup');anchor_fd=directory('anchor');stage_parent=directory('stage')
    policy=anchor.RootPolicy.from_operator(backup,owner=(os.getuid(),os.getgid()));stack.callback(policy.close)
    fixture=stack.enter_context(capture(root/'capture'))
    observations=copy.deepcopy(ic.stage._observations)
    if with_graph:
        for obs in observations.values():
            obs['roles']['roles']=[[name,False,False,False,False,False,False,-1,None,False,None] for name in ('grantor','member','owner')]
            obs['roles']['credentials']=[[name,None,'1'] for name in ('grantor','member','owner')]
            obs['roles']['memberships']=[['owner','grantor',True,'source_admin',False,True,'1'],['owner','member',False,'grantor',True,True,'1']]
    facts=copy.deepcopy(ic.stage._facts)
    facts.update(profile=contexts[0].profile.name,
        schema=dict(relations=observations['central']['schema'],identity=dict(profile=contexts[1].profile.name,relations=observations['identity']['schema'])),
        rows=observations['central']['rows'],
        roles={k:dict(before=observations[k]['roles'],during=observations[k]['roles'],after=observations[k]['roles']) for k in ('central','identity')})
    from platform_service.media_backup_b3.contracts import canonical,digest
    facts_hash=digest(facts)
    payloads={'config/'+k:canonical({'source_facts':facts}) for k in ('central','identity','media')}
    payloads.update({'db/'+k+'.dump':k.encode() for k in ('central','identity')})
    payloads.update({'db/'+k+'.roles':canonical(dict(format='easel-roles-v1',inventory=observations[k]['roles'])) for k in ('central','identity')})
    plan=copy.deepcopy(fixture.plan);plan['snapshots']['source_sha256']=facts_hash
    for row in plan['files']:
        value=payloads[row['name']];row.update(size=len(value),sha256=sha(value))
    manifest=encode_manifest(plan);plan_hash=sha(manifest)
    backup_id=str(uuid4());window_id=str(uuid4());target='replay-graph'
    os.mkdir(backup_id,0o700,dir_fd=backup)
    job=os.open(backup_id,os.O_RDONLY|os.O_DIRECTORY,dir_fd=backup);stack.callback(os.close,job)
    object_bytes={'context':b'synthetic-context','archive':b'synthetic-archive'}
    commit=wire.encode_commit(dict(format='easel-backup-commit-v2',backup_id=backup_id,target=target,window_id=window_id,epoch=1,
        plan_sha256=plan_hash,registry_sha256=plan['registry_sha256'],objects={k:dict(name=k+'.easelbs2',bytes=len(v),sha256=sha(v)) for k,v in object_bytes.items()}))
    for name,raw in [('COMMITTED.json',commit)]+[(k+'.easelbs2',v) for k,v in object_bytes.items()]:
        fd=os.open(name,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600,dir_fd=job);os.write(fd,raw);os.close(fd)
    writer=anchor.trusted_anchor_intent(anchor_fd,policy=policy,backup_id=backup_id,target=target,commit_sha256=sha(commit))
    stack.callback(writer.close)
    reference=anchor.publish_anchor(anchor_fd,writer,commit)
    receipt=dict(plaintext_length=10,plaintext_sha256='a'*64,plan_sha256=plan_hash,registry_sha256=plan['registry_sha256'],
        ciphertext_bytes=len(object_bytes['archive']),ciphertext_sha256=sha(object_bytes['archive']),data_records=1)
    proof=dict(status='archive_content_verified',archive_bytes=10,archive_sha256='a'*64,plan_sha256=plan_hash,
        registry_sha256=plan['registry_sha256'],source_facts_sha256=facts_hash,file_count=7,blob_count=0)
    class Decoder:
        verified=True
        def read(self,n):return b''
        @property
        def receipt(self):return dict(receipt)
        def close(self):pass
    actual_readers=[]
    class Adapter:
        def __init__(self,limits):self.limits=limits
        def read(self,intent,job_fd,key,*,check):
            check();clone=intent._clone();directory=clone._open_job(job_fd)
            files=[_File(clone._directory,backup_id+'.json',wire.MAX_COMMIT),_File(directory,'COMMITTED.json',wire.MAX_COMMIT),
                   _File(directory,'context.easelbs2',1024),_File(directory,'archive.easelbs2',1024)]
            value=reader.OwnedArchiveReader(clone,directory,files,Decoder(),reference,
                dict(bytes=receipt['ciphertext_bytes'],sha256=receipt['ciphertext_sha256']),check=check)
            actual_readers.append(value);return copy.deepcopy(plan),value
        def origin(self,intent,job_fd,value):return pf._CommittedReadOrigin(intent,value,job_fd)
        def validate_plan(self,value,limits):return value
        def manifest(self,value,limits):return encode_manifest(value)
        def records(self,value):return value['files']
        def verify(self,value,plan,*,check,limits):value.read(1);return dict(proof)
        def restore(self,value,parent,name,plan,*,limits):
            os.mkdir(name,0o700,dir_fd=parent)
            for part in ('config','db','media'):os.mkdir(name+'/'+part,0o700,dir_fd=parent)
            for key,raw in payloads.items():
                fd=os.open(name+'/'+key,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600,dir_fd=parent);os.write(fd,raw);os.close(fd)
            value.read(1)
            return dict(status='private_stage_restored_verified',archive_bytes=10,archive_sha256='a'*64,
                manifest_sha256=plan_hash,registry_sha256=plan['registry_sha256'],file_count=7,blob_count=0)
        def bundles(self,*args):return dict(source_facts_sha256=facts_hash)
        def decode(self,*args):return dict(source_facts=facts)
        def canonical(self,value):return canonical(value)
        def facts(self,value):return facts
    read_intent=anchor.trusted_anchor_intent(anchor_fd,policy=policy,backup_id=backup_id,target=target,commit_sha256=sha(commit))
    stack.callback(read_intent.close)
    stage_a=dc.replace(a,stage_name='authenticated-stage')
    stack.enter_context(patch.object(pf.os,'fstatvfs',return_value=NS(f_bsize=4096,f_frsize=4096,f_bavail=10**9)))
    with patch.object(pf,'_B2Adapter',Adapter):
        stage=pf.prepare_authenticated_stage(read_intent,job,b'fake-codec-leaf',stage_a,stage_parent)
    stack.callback(stage.close)
    ic.stage=stage;ic.a=stage_a
    # Parser authority has the same original Root job/event; the producer binds
    # this newly authenticated original stage, not the abandoned helper stage.
    case.input=ic.inspect()
    source_hash=sha(b'123')
    case.policy=dc.replace(case.policy,_source=dc.replace(case.policy._source,_system_id_sha256=source_hash,
        _restore_id=a.restore_id,_started_at=a.started_at,_deadline=a.deadline),
        _capacity=dc.replace(case.policy._capacity,_restore_id=a.restore_id,_started_at=a.started_at,_deadline=a.deadline,_measured_at=a.started_at),
        _cancellation=cancel,_specs=tuple(dc.replace(s,restore_id=a.restore_id,started_at=a.started_at,deadline=a.deadline,source_system_id_sha256=source_hash,restored_role_names=contexts[0].role_names) for s in case.policy._specs))
    case.tools=dc.replace(case.tools,restore_id=a.restore_id,started_at=a.started_at,deadline=a.deadline)
    # Existing fake target renderings only depend on target fields, unchanged here.
    test.case=case;test.now[0]=a.started_at+1
    owners=[];producers=[];auths=[]
    for kind,context in zip(('central','identity'),contexts):
        owner=SourceOwner.__new__(SourceOwner);root_authority=object()
        owner._source_authority=root_authority;owner.name=context.database_name;owner.container_id=case.policy._specs[0].source_container_id
        owner.phase='READY';owner.cleanup_attempted=False;owner.active=0;owner.unknown=False
        owner.cancellation=cancel;owner.check_budget=lambda:None;owner.attempts=[];owner.roles={'owner':'owner'}
        owner.binding=dict(started=a.started_at,deadline=a.deadline);owner.records=[];owner.record=owner.records.append
        owner.base=make_conninfo(host='127.0.0.1',hostaddr='127.0.0.1',port='55432',dbname=owner.name,user='source_admin',password='synthetic')
        owner.make_conninfo=make_conninfo
        db=sc.DatabaseState(100,owner.name,42,'owner',6,'UTF8','C','C','c',None,None,None,None,None,-1,True,False,'pg_default','1')
        expected=roles.RoleReplayExpectation('owner',False,False,False,False,False,False,-1,None,False,None,None,roles.RolePurpose.RESTORED)
        dbexp=roles.DatabaseReplayExpectation(*tuple(getattr(db,f.name) for f in dc.fields(roles.DatabaseReplayExpectation) if f.name!='settings'),())
        secret=b'synthetic-denial';authrow=roles.AuthenticationRequirement('owner',roles.AuthenticationMode.DENY_NOLOGIN,'denial',sha(secret))
        role_expectations=(expected,);authrows=(authrow,);memberships=();graph_scope=None;material={'denial':secret}
        if with_graph:
            from platform_service.media_backup_b2.reconstruction_grants import GrantGraphScope
            role_expectations=tuple(dc.replace(expected,name=name,purpose=roles.RolePurpose.ORDINARY_GRANTOR if name=='grantor' else roles.RolePurpose.RESTORED) for name in context.role_names)
            bootstrap=roles.RoleReplayExpectation('source_admin',True,True,True,True,True,True,-1,None,True,None,None,roles.RolePurpose.BOOTSTRAP_HANDOVER,10)
            role_expectations=role_expectations+(bootstrap,)
            authrows=tuple(roles.AuthenticationRequirement(e.name,roles.AuthenticationMode.DENY_NULL if e.name=='source_admin' else roles.AuthenticationMode.DENY_NOLOGIN,e.name+'_denial',sha(secret)) for e in role_expectations)
            material={r.credential_id:secret for r in authrows}
            memberships=(('owner','grantor',True,'source_admin',False,True),('owner','member',False,'grantor',True,True))
            graph_scope=GrantGraphScope(kind,context.role_names)
        authdata=roles.ReplayAuthorizationData(roles.FORMAT,kind,a.restore_id,a.started_at,a.deadline,owner.container_id,source_hash,
            owner.name,'f'*64,role_expectations,dbexp,memberships,(),(),authrows,graph_scope)
        selection=roles.ReplaySelection('b3-replay-selection-v1',kind,('owner',),context.role_names,context.allowed_grantors,'f'*64,owner.container_id,source_hash,owner.name)
        owner.bind_replay_selection(selection,original_root_authorization=root_authority)
        auth=owner.bind_replay_authorization(authdata,original_comparison=context,original_root_authorization=root_authority,authentication_material=material)
        scope=sc.SourceReadScope(kind,owner.container_id,source_hash,owner.name,'source_admin',tuple(sorted(set(context.role_names)|{'source_admin'})))
        with patch.object(cap.time,'monotonic_ns',return_value=int((a.started_at+1)*1e9)):
            producer=owner.begin_reconstruction(scope,original_read_context=scope,original_comparison=context,
                started_at_ns=int(a.started_at*1e9),deadline_ns=int(a.deadline*1e9),cancellation=cancel)
        owners.append(owner);producers.append(producer);auths.append(auth)
    stack.enter_context(patch.object(cap.time,'monotonic_ns',return_value=int((a.started_at+1)*1e9)))
    inputs=OwnedInputs.__new__(OwnedInputs)
    inputs._preflight=None;inputs._exclusive=None;inputs._capture=None
    pair=cap.OwnedSourceCapture._for_inputs(inputs,original_contexts=contexts,producers=tuple(producers));inputs.companion=pair
    # All source native work is represented by the same fixed _read producer,
    # with its connection/observer leaf replaced by the known synthetic Native.
    binding=NS(window_id=window_id);window=fs.ExclusiveWindow(NS(verify=lambda:None),job,binding,_mint=fs._MINT)
    inputs._exclusive=window;inputs.binding=binding;inputs._deadline=int((a.started_at+40)*1e9)
    inputs._keepers={};inputs._keeper_snapshots={};inputs._keeper_stamps={};inputs._work_lock=threading.RLock();inputs._active_work=0;inputs._work_unknown=False
    pair._exclusive=window;pair._binding=binding;pair._phase_deadline=inputs._deadline
    pair._state=dict(cancelled=threading.Event(),phase='CAPTURING',persisted=True)
    pair._controller=NS(pending_window_ids=(),_holders={});pair._runner=ChildRunner(('fake',),('fake',));pair._policy=policy;pair._backup_id=backup_id
    parts={}
    for producer in producers:
        scope=producer._scope
        identity=[scope.database_name,'123','160015',123,'10:20:11,15','on','repeatable read','UTC','source_admin','source_admin',10,10]
        rows=[[10,'source_admin',True,True,True,True,True,True,-1,None,True,None,None,'1'],
              [42,'owner',False,False,False,False,False,False,-1,None,False,None,None,'1']]
        if with_graph:
            rows.extend([[oid,name,False,False,False,False,False,False,-1,None,False,None,None,'1'] for oid,name in ((43,'grantor'),(44,'member'))])
        database=[[100,scope.database_name,42,'owner',6,'UTF8','C','C','c',None,None,None,None,None,-1,True,False,'pg_default','1']]
        parts[scope.kind]=(scope,identity,rows,database,[])
    class GraphNative(Native):
        def execute(self,q,params=None):
            if q==cap._GRANTS:
                self.events.append(q)
                return Cursor([(42,'owner',43,'grantor',10,'source_admin',True,False,True,'1'),(42,'owner',44,'member',43,'grantor',False,True,True,'1')])
            return super().execute(q,params)
    with patch.object(cap.time,'monotonic_ns',return_value=int((a.started_at+1)*1e9)),patch.object(fs,'_duplicate_exclusive',side_effect=lambda fd:os.dup(fd)),patch.object(fs,'_close_exclusive',side_effect=os.close),patch.object(cap,'_literal_command',side_effect=lambda shape,value:shape.format(repr(value))):
        for producer in producers:
            keeper=Keeper('UTC');kind=producer._scope.kind
            with patch.object(cap,'_connect_native',side_effect=lambda *args,p=parts[kind]:(GraphNative(p) if with_graph else Native(p))):
                producer._read(sc.SourcePhase.BEFORE,keeper);inputs._keepers[kind]=keeper
                inputs._keeper_snapshots[kind]='00000003-0000001B-1';inputs._keeper_stamps[kind]=(123,'10:20:11,15')
                producer._read(sc.SourcePhase.DURING,keeper);observer=Keeper('UTC')
                producer._read(sc.SourcePhase.AFTER,observer);producer._observe_after_close(observer,None)
        captured=CapturedSource(plan,fixture.media_fd,fixture.fds,fixture.owner,facts_hash,binding)
        inputs._capture=captured;pair._bind_captured(captured)
    # Retained publication and writer evidence correspond to the actual local
    # files and actual trusted anchor issuance above; their originals then close.
    pair._slots=[dict(fd=os.dup(fd),identity=cap._directory_identity(os.fstat(fd)),state='HELD') for fd in (job,backup)]
    for slot in pair._slots:os.set_inheritable(slot['fd'],False);stack.callback(os.close,slot['fd'])
    streams={'context':dict(receipt),'archive':dict(receipt)}
    stamps={k:_stamp(os.stat(k+'.easelbs2',dir_fd=job)) for k in streams}
    pair._authenticated=dict(receipts=copy.deepcopy(streams),decoders=copy.deepcopy(streams),archive=copy.deepcopy(proof),stamps=stamps,
        original_receipts=streams,original_decoders=copy.deepcopy(streams),original_archive=copy.deepcopy(proof))
    pair._authenticated_pin=cap._pin({k:pair._authenticated[k] for k in ('receipts','decoders','archive','stamps')})
    pair._reference=reference;pair._commit=commit;pair._intent=writer;pair._writer_policy=writer._policy;pair._writer_issuer=policy._issuer;pair._writer_selector=writer._directory
    pair._writer_pin=(writer.backup_id,writer.target,writer.commit_sha256,writer._directory.identity,writer._policy._backup.identity,reference,sha(commit))
    pair._commit_stamp=_stamp(os.stat('COMMITTED.json',dir_fd=job))
    pair._state.update(phase='OPEN',owned_intent=writer,owned_commit_ref=reference,owned_commit_bytes=commit)
    pair._stage='OPEN';pair._terminal_state='TERMINAL_SUCCESS';pair._cleanup_unknown=False
    inputs._keepers.clear();window._closed=True
    with patch.object(cap.time,'monotonic_ns',return_value=int((a.started_at+1)*1e9)):
        for source,producer in zip(owners,producers):source.finish_reconstruction(producer)
    writer.close();read_intent.close()
    # Reader supplied intent and both actual cloned reader returns remain
    # retained after close and are distinct from the closed writer issuance.
    assert len(actual_readers)==2 and all(r._closed for r in actual_readers)
    return NS(case=case,pair=pair,owners=tuple(owners),authorizations=tuple(auths),stage=stage,
        phase_expired_at=a.started_at+41,readers=actual_readers)
