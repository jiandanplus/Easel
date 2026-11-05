"""Pure/AST/fake sequencing tests only: none proves actual B2 authentication.

Root actual-reader tests are a separate inspected source; no fixture imports here.
"""
import ast
import copy
import hashlib
import io
import os
from pathlib import Path
import pickle
import tempfile
import types
import unittest
from unittest.mock import patch

from platform_service.media_backup_b3 import preflight as p
from platform_service.media_backup_b3.contracts import (
    CATALOG_WIDTHS, ROLE_WIDTHS, Column, Table, RestoreProfile, ComparisonContext,
    canonical, digest, B3Error,
)

ROOT = Path(__file__).resolve().parents[1]


def context(kind):
    schema = {name: [] for name in CATALOG_WIDTHS}
    schema['database'] = [['source', 'owner', None, -1, True, False]]
    schema['columns'] = [['public','item','r','owner',None,1,'id','integer',True,None,None,False,False,None,None,None,'']]
    pinned = {**schema, 'database': [['<trusted-database>','owner',None,-1,True,False]]}
    mode = 'copy_only' if kind == 'identity' else 'full_raw_and_copy'
    profile = RestoreProfile('synthetic', (Table('item',(Column('id','integer',False),),('id',)),), digest(pinned), kind, mode)
    ctx = ComparisonContext('source', ('owner',), (), profile,
        () if kind == 'identity' else (('item',()),), source_kind=kind, evidence_mode=mode)
    roles = {name: [] for name in ROLE_WIDTHS}
    roles['roles'] = [['owner',False,False,False,False,False,False,-1,None,False,None]]
    roles['credentials'] = [['owner',None,'1']]
    observation = dict(schema=schema,roles=roles,rows={} if kind == 'identity' else {'item':[]},
        copy={'public.item':dict(columns=['id'],count=0,rows_sha256='0'*64)},sequences=[])
    return ctx, observation


def authority():
    return p.PreflightAuthority('restore1','stage1',10.,100.,2*1024**2,0,
        (('central',context('central')[0]),('identity',context('identity')[0])),
        p.StageLimits(max_file_bytes=10000,max_archive_bytes=100000,max_members=20,max_manifest_bytes=10000,chunk_bytes=16))


class GuardTests(unittest.TestCase):
    def test_authority_closed_deadline_and_limits(self):
        a=authority()
        with patch.object(p.time,'monotonic',return_value=99.): a.check()
        with patch.object(p.time,'monotonic',return_value=100.):
            with self.assertRaises(B3Error): a.check()
        with self.assertRaises(Exception): a.deadline=200.
        with self.assertRaises(B3Error): p.StageLimits(chunk_bytes=1048577)
        with self.assertRaises(B3Error): p.PreflightAuthority('r','s',0.,1801.,100,0,a.contexts)
        with self.assertRaises(B3Error): p.PreflightAuthority('r','../s',0.,1.,100,0,a.contexts)
        with self.assertRaises(B3Error): p.PreflightAuthority('r','s',0.,1.,100,0,tuple(reversed(a.contexts)))

    def test_hash_guard_entry_reads_eof_and_legacy(self):
        tree=ast.parse((ROOT/'src/platform_service/media_backup_b2/durable.py').read_text())
        node=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='_File')
        code=ast.Module(body=[node],type_ignores=[])
        def require(ok):
            if not ok: raise ValueError('bad')
        env=dict(os=os,hashlib=hashlib,_CHUNK=2,require=require,validate_int=lambda *a:None,
            validate_hash=lambda *a:None,B2Error=ValueError)
        exec(compile(code,'durable._File','exec'),env)
        with tempfile.TemporaryFile() as stream:
            stream.write(b'abcdef');stream.flush()
            obj=object.__new__(env['_File']); obj._fd=stream.fileno();obj.stamp=(0,0,0,0,0,1,6);obj.verify=lambda:None
            h=hashlib.sha256(b'abcdef').hexdigest(); calls=[]
            obj.hash_all(6,h)  # unchanged default call
            obj.hash_all(6,h,check=lambda:calls.append(1))
            self.assertGreaterEqual(len(calls),10)
            with self.assertRaises(ValueError): obj.hash_all(6,h,check=1)
            count=[0]
            def cancel():
                count[0]+=1
                if count[0]==4: raise KeyboardInterrupt()
            with self.assertRaises(KeyboardInterrupt): obj.hash_all(6,h,check=cancel)
            os.fstat(stream.fileno())  # caller remains owned

    def test_reader_guard_ast_and_no_final_capability(self):
        tree=ast.parse((ROOT/'src/platform_service/media_backup_b2/reader.py').read_text())
        read=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='read_committed')
        self.assertIn('check',[n.arg for n in read.args.kwonlyargs])
        source=Path(p.__file__).read_text()
        self.assertNotIn('class VerifiedRestoreInput',source)
        self.assertNotIn('TargetFactory',source)
        with self.assertRaises(TypeError): p.AuthenticatedRestoreStage()


    def test_read_committed_ast_cancellation_closes_only_owned(self):
        tree=ast.parse((ROOT/'src/platform_service/media_backup_b2/reader.py').read_text())
        nodes=[n for n in tree.body if isinstance(n,(ast.ClassDef,ast.FunctionDef)) and n.name in ('OwnedArchiveReader','read_committed')]
        for phase in ('entry','context.easelbs2','rsa','context_decode','archive.easelbs2','retained'):
            with self.subTest(phase=phase):
                owned=[];state=['entry']; cancelled=[False]
                def require(ok):
                    if not ok:raise ValueError('bad')
                def guard():
                    if state[0]==phase:
                        cancelled[0]=True;raise KeyboardInterrupt()
                reference=types.SimpleNamespace(backup_id='backup',target='target',plan_sha256=hashlib.sha256(b'p').hexdigest(),registry_sha256='r')
                class Intent:
                    backup_id='backup'
                    def __init__(self):self.closed=False;self._directory=object()
                    def _clone(self):value=Intent();owned.append(value);return value
                    def _check(self):pass
                    def _read_anchor(self):return reference
                    def _open_job(self,fd):value=Directory();owned.append(value);return value
                    def _validate_commit(self,raw):return reference
                    def close(self):self.closed=True
                class Directory:
                    closed=False
                    def verify(self):pass
                    def close(self):self.closed=True
                class File:
                    closed=False
                    def __init__(self,directory,name,maximum):self.name=name;owned.append(self)
                    def read_all(self,maximum):return b'x'
                    def hash_all(self,size,sha,*,check=None):state[0]=self.name;check()
                    def verify(self):pass
                    def close(self):self.closed=True
                class Decoder:
                    closed=False;verified=True;reads=0
                    def __init__(self,key,source,expected):self.source=source;owned.append(self)
                    def read(self,size):
                        state[0]='context_decode' if self.source.name=='context.easelbs2' else 'retained'
                        self.reads+=1;return b'p' if self.reads==1 else b''
                    @property
                    def receipt(self):return dict(ciphertext_bytes=1,ciphertext_sha256='s',plan_sha256=reference.plan_sha256,registry_sha256='r')
                    def close(self):self.closed=True
                def recipient(key):state[0]='rsa';return 'recipient'
                row=dict(bytes=1,sha256='s')
                wire=types.SimpleNamespace(MAX_COMMIT=10,MAX_CONTEXT=100,CHUNK_BYTES=10,cipher_budget=lambda kind:100,
                    decode_commit=lambda raw:dict(objects=dict(context=row,archive=row)))
                env=dict(threading=__import__('threading'),require=require,B2Error=ValueError,io=io,hashlib=hashlib,
                    RootAnchorIntent=Intent,_File=File,_record=lambda ref:b'x',wire=wire,_context_length=lambda source:1,
                    _recipient=recipient,ExpectedV2=lambda *a:None,V2Decoder=Decoder,decode_manifest=lambda raw:dict(registry_sha256='r',blobs=[],references=[]),
                    registry_digest=lambda *a:'r',physical_size=lambda plan:1)
                exec(compile(ast.Module(body=nodes,type_ignores=[]),'reader_AST','exec'),env)
                original=Intent()
                with tempfile.TemporaryFile() as caller:
                    with self.assertRaises(KeyboardInterrupt):
                        plan,reader=env['read_committed'](original,caller.fileno(),b'key',check=guard)
                        reader.read(1)
                    self.assertTrue(cancelled[0]);self.assertTrue(all(x.closed for x in owned))
                    self.assertFalse(original.closed);os.fstat(caller.fileno())


class StageTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();os.chmod(self.tmp.name,0o700)
        self.parent=os.open(self.tmp.name,os.O_RDONLY|os.O_DIRECTORY)
        self.clock=patch.object(p.time,'monotonic',return_value=11.);self.clock.start()
        self.a=authority()
        self.payloads={'config/'+k:b'{}' for k in ('central','identity','media')}
        self.payloads.update({'db/'+k+suffix:b'dump' for k in ('central','identity') for suffix in ('.dump','.roles')})
        self.rows=[dict(name=n,size=len(v),sha256=hashlib.sha256(v).hexdigest()) for n,v in sorted(self.payloads.items())]

    def tearDown(self): self.clock.stop();os.close(self.parent);self.tmp.cleanup()

    def create(self):
        os.mkdir('stage1',0o700,dir_fd=self.parent)
        stage=os.open('stage1',os.O_RDONLY|os.O_DIRECTORY,dir_fd=self.parent)
        try:
            for part in ('config','db','media'): os.mkdir(part,0o700,dir_fd=stage)
            for name,data in self.payloads.items():
                fd=os.open('stage1/'+name,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600,dir_fd=self.parent)
                os.write(fd,data);os.close(fd)
        finally: os.close(stage)

    def test_retained_stage_rehashes_rejects_mutation_and_closes_owned_only(self):
        self.create(); owned=p._StageFiles(self.parent,self.a,self.rows)
        self.assertEqual(owned.read_small('config/central',100),b'{}')
        fd=os.open('stage1/db/central.dump',os.O_WRONLY,dir_fd=self.parent);os.pwrite(fd,b'evil',0);os.close(fd)
        with self.assertRaises(B3Error): owned.verify()
        fds=list(owned._files.values());owned.close();owned.close()
        for fd in fds:
            with self.assertRaises(OSError):os.fstat(fd)
        os.fstat(self.parent)
        self.assertTrue(Path(self.tmp.name,'stage1').is_dir())

    def test_reject_extra_missing_symlink_mode_and_hash(self):
        for case in ('extra','missing','link','hardlink','mode','hash'):
            with self.subTest(case=case):
                with tempfile.TemporaryDirectory() as td:
                    old=self.tmp.name; oldparent=self.parent
                    self.parent=os.open(td,os.O_RDONLY|os.O_DIRECTORY);os.chmod(td,0o700)
                    try:
                        self.create()
                        target=Path(td,'stage1/db/central.dump')
                        if case=='extra': Path(td,'stage1/media/extra').write_bytes(b'x')
                        if case=='missing':target.unlink()
                        if case=='link':target.unlink();target.symlink_to('/dev/null')
                        if case=='hardlink':os.link(target,Path(td,'hardlink'))
                        if case=='mode':target.chmod(0o644)
                        if case=='hash':target.write_bytes(b'evil')
                        with self.assertRaises((B3Error,OSError)): p._StageFiles(self.parent,self.a,self.rows)
                    finally: os.close(self.parent);self.parent=oldparent

    def test_facts_bind_actual_kind_profile_roles_and_raw_copy(self):
        central,obs=context('central');_,identity=context('identity')
        facts=dict(profile='synthetic',schema=dict(relations=obs['schema'],identity=dict(profile='synthetic',relations=identity['schema'])),
            rows=obs['rows'],roles={k:dict(before=obs['roles'],during=obs['roles'],after=obs['roles']) for k in ('central','identity')},
            snapshot_projections={k:obs['copy'] for k in ('central','identity')},
            sequences={k:dict(before=[],dump=[],after=[]) for k in ('central','identity')})
        roles={k:canonical(dict(format='easel-roles-v1',inventory=obs['roles'])) for k in ('central','identity')}
        bound=p._bind_facts(facts,roles,self.a)
        self.assertEqual(bound['central'],obs)
        for case in ('identity_profile','central_profile','central_rows','copy_columns','role_phase','sequence_phase','credential'):
            with self.subTest(case=case):
                bad=copy.deepcopy(facts);badroles=dict(roles)
                if case=='identity_profile':bad['schema']['identity']['profile']='wrong'
                if case=='central_profile':bad['profile']='wrong'
                if case=='central_rows':bad['rows']={}
                if case=='copy_columns':bad['snapshot_projections']['identity']['public.item']['columns']=[]
                if case=='role_phase':bad['roles']['identity']['after']={}
                if case=='sequence_phase':bad['sequences']['central']['after']=[{}]
                if case=='credential':badroles['central']=roles['central'].replace(b'"1"',b'"2"')
                with self.assertRaises(B3Error):p._bind_facts(bad,badroles,self.a)

    def test_crossbinding_requires_eof_and_both_exact_receipts(self):
        r=dict(plaintext_length=10,plaintext_sha256='a'*64,plan_sha256='b'*64,registry_sha256='c'*64,
            ciphertext_bytes=20,ciphertext_sha256='d'*64,data_records=1)
        proof=dict(status='archive_content_verified',archive_bytes=10,archive_sha256='a'*64,plan_sha256='b'*64,registry_sha256='c'*64)
        closure=dict(status='private_stage_restored_verified',archive_bytes=10,archive_sha256='a'*64,manifest_sha256='b'*64,registry_sha256='c'*64)
        p._crossbind(proof,r,r,closure)
        for key in r:
            changed={**r,key:0}
            with self.assertRaises(B3Error):p._crossbind(proof,r,changed,closure)
        with self.assertRaises(B3Error):p._crossbind(proof,r,r,{**closure,'archive_sha256':'e'*64})


class SequenceTests(unittest.TestCase):
    """A private fake adapter proves orchestration only, never authentication."""
    setUp=StageTests.setUp
    tearDown=StageTests.tearDown
    create=StageTests.create

    def fake(self, failure=None):
        owner=self;events=[];readers=[]
        _,obs=context('central');_,identity=context('identity')
        facts=dict(profile='synthetic',schema=dict(relations=obs['schema'],identity=dict(profile='synthetic',relations=identity['schema'])),
            rows=obs['rows'],roles={k:dict(before=obs['roles'],during=obs['roles'],after=obs['roles']) for k in ('central','identity')},
            snapshot_projections={k:obs['copy'] for k in ('central','identity')},
            sequences={k:dict(before=[],dump=[],after=[]) for k in ('central','identity')})
        raw=canonical(facts);source=hashlib.sha256(raw).hexdigest()
        self.payloads['config/central']=canonical(dict(source_facts=facts))
        for k in ('central','identity'):
            self.payloads['db/'+k+'.roles']=canonical(dict(format='easel-roles-v1',inventory=obs['roles']))
        self.rows=[dict(name=n,size=len(v),sha256=hashlib.sha256(v).hexdigest()) for n,v in sorted(self.payloads.items())]
        plan=dict(files=self.rows,blobs=[],registry_sha256='c'*64,snapshots=dict(source_sha256=source))
        manifest=canonical(plan);ph=hashlib.sha256(manifest).hexdigest()
        receipt=dict(plaintext_length=10,plaintext_sha256='a'*64,plan_sha256=ph,registry_sha256='c'*64,
            ciphertext_bytes=20,ciphertext_sha256='d'*64,data_records=1)
        proof=dict(status='archive_content_verified',archive_bytes=10,archive_sha256='a'*64,plan_sha256=ph,
            registry_sha256='c'*64,source_facts_sha256=source,file_count=7,blob_count=0)
        class Reader:
            verified=False;closed=False
            def read(self,size):self.verified=True;return b''
            @property
            def receipt(self):
                if not self.verified:raise AssertionError('EOF required')
                return dict(receipt)
            def close(self):self.closed=True;events.append('close')
        class Fake:
            def __init__(self,limits):self.limits=limits
            def read(self,intent,job,key,*,check):
                check();events.append('read');reader=Reader();readers.append(reader)
                if failure=='read' and len(readers)==1:raise B3Error('bad')
                return dict(plan),reader
            def origin(self,intent,job,reader):return types.SimpleNamespace(verify=lambda:None)
            def manifest(self,value,limits):
                if failure=='plan2' and len(readers)==2:return b'changed'
                return manifest
            def validate_plan(self,value,limits):return value
            def physical_size(self,value,limits):return 10000
            def records(self,value):return owner.rows
            def verify(self,reader,plan,*,check,limits):
                events.append('verify');check()
                if failure=='verify':raise B3Error('bad')
                if failure!='eof1':reader.read(1)
                return dict(proof)
            def restore(self,reader,parent,name,plan,*,limits):
                events.append('restore');owner.create()
                if failure!='eof2':reader.read(1)
                if failure=='restore':raise B3Error('bad')
                if failure=='deadline':owner.a.cancellation.set()
                value=dict(status='private_stage_restored_verified',archive_bytes=10,archive_sha256='a'*64,
                    manifest_sha256=ph,registry_sha256='c'*64,file_count=7,blob_count=0)
                if failure=='crossbind':value['archive_sha256']='e'*64
                if failure=='member':Path(owner.tmp.name,'stage1/db/central.dump').write_bytes(b'evil')
                return value
            def bundles(self,bundles,records,plan):
                events.append('bundles')
                if failure=='bundles':raise B3Error('bad')
                return dict(source_facts_sha256=source)
            def decode(self,value,kind):return dict(source_facts=facts)
            def canonical(self,value):return canonical(value)
            def facts(self,value):events.append('facts');return facts
        return Fake,events,readers

    def test_stage_retains_exact_actual_read_origins_before_reader_close(self):
        fake,events,readers=self.fake()
        supplied=object(); origins=[]
        class WithOrigin(fake):
            def origin(self,intent,job,reader):
                self_test.assertIs(intent,supplied)
                self_test.assertIs(reader,readers[-1])
                self_test.assertFalse(reader.closed)
                self_test.assertTrue(reader.verified)
                value=types.SimpleNamespace(verify=lambda:None)
                origins.append(value)
                return value
        self_test=self
        with patch.object(p,'_B2Adapter',WithOrigin):
            stage=p.prepare_authenticated_stage(supplied,0,b'fake',self.a,self.parent)
        self.assertEqual(len(origins),2)
        self.assertEqual(stage._origin,tuple(origins))
        self.assertTrue(all(r.closed for r in readers))
        self.assertEqual(set(stage.safe_receipt),{'authenticated_stage','dump_preflight_complete',
            'plan_sha256','registry_sha256','source_facts_sha256','archive_sha256','ciphertext_sha256'})
        stage.close()

    def test_two_pass_sequence_intermediate_defensive_safe_receipt(self):
        fake,events,readers=self.fake()
        with patch.object(p,'_B2Adapter',fake):
            stage=p.prepare_authenticated_stage(object(),0,b'fake',self.a,self.parent)
        self.assertEqual(events[:5],['read','verify','close','read','restore'])
        self.assertTrue(all(r.closed for r in readers))
        self.assertNotIn('dump',str(stage.safe_receipt.get('roles','')))
        self.assertFalse(stage.safe_receipt['dump_preflight_complete'])
        changed=stage.safe_receipt;changed['authenticated_stage']=False
        self.assertTrue(stage.safe_receipt['authenticated_stage'])
        self.assertNotIn('owner',repr(stage))
        with self.assertRaises(TypeError):pickle.dumps(stage)
        stage.close();stage.close();os.fstat(self.parent)
        with self.assertRaises(B3Error):stage.verify()

    def test_allocation_admission_precedes_stage_and_public_receipt_remeasures(self):
        from dataclasses import replace
        fake,events,readers=self.fake()
        with patch.object(p,'_B2Adapter',fake):
            with self.assertRaises(B3Error):
                p.prepare_authenticated_stage(object(),0,b'fake',replace(self.a,reserved_bytes=100000),self.parent)
        self.assertNotIn('restore',events);self.assertFalse(Path(self.tmp.name,'stage1').exists())
        fake,events,readers=self.fake()
        with patch.object(p,'_B2Adapter',fake):
            stage=p.prepare_authenticated_stage(object(),0,b'fake',self.a,self.parent)
        with patch.object(p,'_allocated_bytes',return_value=self.a.reserved_bytes+512):
            with self.assertRaises(B3Error):stage.safe_receipt
        self.assertTrue(stage._closed)
        os.fstat(self.parent)

    def test_rejection_matrix_no_replacement_and_stage_retention(self):
        for failure in ('verify','eof1','plan2','restore','eof2','crossbind','member','bundles','deadline'):
            with self.subTest(failure=failure):
                # New temporary parent per independent test case, never retry one invocation.
                with tempfile.TemporaryDirectory() as td:
                    os.chmod(td,0o700);oldparent=self.parent;oldtmp=self.tmp
                    self.parent=os.open(td,os.O_RDONLY|os.O_DIRECTORY);self.tmp=types.SimpleNamespace(name=td);self.a=authority()
                    try:
                        fake,events,readers=self.fake(failure)
                        with patch.object(p,'_B2Adapter',fake):
                            with self.assertRaises(B3Error):p.prepare_authenticated_stage(object(),0,b'fake',self.a,self.parent)
                        self.assertLessEqual(events.count('read'),2)
                        self.assertTrue(all(r.closed for r in readers))
                        self.assertEqual(Path(td,'stage1').exists(),failure not in ('verify','eof1','plan2'))
                        os.fstat(self.parent)
                    finally:os.close(self.parent);self.parent=oldparent;self.tmp=oldtmp


class OriginTests(unittest.TestCase):
    def test_actual_boundary_origin_survives_reader_close_and_refuses_replaced_originals(self):
        from platform_service.media_backup_b2 import anchor,reader as rm
        from platform_service.media_backup_b2.contracts import CommitRef
        ref=CommitRef('12345678-1234-4234-8234-123456789abc','target','a'*64,'b'*64,'c'*64)
        issuer=object();policy=types.SimpleNamespace(_issuer=issuer,_backup=types.SimpleNamespace(identity=(1,2,3,4,5)))
        class Intent:
            backup_id=ref.backup_id;target=ref.target;commit_sha256=ref.commit_sha256
            def __init__(self):
                self._policy=policy;self._directory=types.SimpleNamespace(identity=(6,7,8,9,10))
            def _validate_commit(self,raw):
                self_test.assertEqual(raw,b'commit');return ref
        class File:
            stamp=(1,2,3,4,5,1,6,7,8)
            def read_all(self,maximum):return b'commit'
        class Reader:
            verified=True;_closed=False
            def _check(self):self_test.assertFalse(self._closed)
        self_test=self
        with tempfile.TemporaryDirectory() as td:
            fd=os.open(td,os.O_RDONLY|os.O_DIRECTORY)
            try:
                supplied=Intent();actual=Intent();r=Reader();r._intent=actual
                r._directory=types.SimpleNamespace(identity=p._identity(os.fstat(fd)))
                r._files=[File() for _ in range(4)];r._reference=ref
                r._receipt={'ciphertext_bytes':10,'ciphertext_sha256':'d'*64};r._row={'bytes':10,'sha256':'d'*64}
                with patch.object(anchor,'RootAnchorIntent',Intent),patch.object(rm,'OwnedArchiveReader',Reader):
                    origin=p._CommittedReadOrigin(supplied,r,fd)
                self.assertIs(origin._supplied,supplied);self.assertIs(origin._intent,actual)
                self.assertIs(origin._issuer,issuer)
                r._closed=True;origin.verify()  # historical reader is deliberately closed
                r._row['bytes']=11
                with self.assertRaises(B3Error):origin.verify()
                r._row['bytes']=10;origin.verify()
                origin._supplied=Intent()
                with self.assertRaises(B3Error):origin.verify()
                origin._supplied=supplied
                actual._policy=types.SimpleNamespace(_issuer=issuer,_backup=policy._backup)
                with self.assertRaises(B3Error):origin.verify()
            finally:os.close(fd)

class ReviewFixTests(unittest.TestCase):
    """F1/F2 regressions: structural contracts and newly created temporary FS only."""
    setUp=StageTests.setUp
    tearDown=StageTests.tearDown
    create=StageTests.create

    def test_f1_mutable_context_and_raw_pin_pairs_rejected(self):
        a=authority()
        for contexts in ((list(a.contexts[0]),a.contexts[1]),(a.contexts[0],list(a.contexts[1]))):
            with self.assertRaises(B3Error):
                p.PreflightAuthority('r','s',10.,100.,2000000,0,contexts)
        ctx=a.contexts[0][1]
        from dataclasses import replace
        mutable=['item',()]
        bad=replace(ctx,row_order_pins=(mutable,))
        with self.assertRaises(B3Error):
            p.PreflightAuthority('r','s',10.,100.,2000000,0,(('central',bad),a.contexts[1]))
        mutable[1]=('0'*64,)
        self.assertEqual(a.contexts[0][1].row_order_pins,(('item',()),))

    def test_f2_estimate_boundary_tiny_members_and_directory_allowance(self):
        from dataclasses import replace
        rows=copy.deepcopy(self.rows)
        rows.extend(dict(name='media/'+format(i,'032x'),size=1,sha256='0'*64) for i in range(50))
        a=replace(self.a,limits=p.StageLimits(max_members=100),reserved_bytes=10*1024**2)
        budget=p._AllocationBudget.bind(self.parent,a,rows)
        self.assertEqual(budget.directory_names,('stage1','stage1/config','stage1/db','stage1/media'))
        self.assertGreaterEqual(budget.estimate,len(rows)*65536+4*65536)
        # A small-member tar fits this reservation; allocation policy must reject.
        tar_bytes=10240+len(rows)*1024
        with self.assertRaises(B3Error):p._AllocationBudget.bind(self.parent,replace(a,reserved_bytes=tar_bytes),rows)
        p._AllocationBudget.bind(self.parent,replace(a,reserved_bytes=budget.estimate),rows)
        with self.assertRaises(B3Error):p._AllocationBudget.bind(self.parent,replace(a,reserved_bytes=budget.estimate-1),rows)

    def test_f2_geometry_device_and_observed_blocks_fail_closed(self):
        from dataclasses import replace
        a=replace(self.a,reserved_bytes=2*1024**2)
        budget=p._AllocationBudget.bind(self.parent,a,self.rows)
        geometry=budget.geometry
        with patch.object(p,'_geometry',return_value=(geometry[0]*2,geometry[1]*2)):
            with self.assertRaises(B3Error):budget.check(self.parent,a)
        with tempfile.TemporaryDirectory() as other:
            os.chmod(other,0o700);otherfd=os.open(other,os.O_RDONLY|os.O_DIRECTORY)
            try:
                with self.assertRaises(B3Error):budget.check(otherfd,a)
            finally:os.close(otherfd)
        for bad in (types.SimpleNamespace(f_bsize=0,f_frsize=4096),types.SimpleNamespace(f_bsize=4096,f_frsize=3)):
            with patch.object(p.os,'fstatvfs',return_value=bad):
                with self.assertRaises(B3Error):p._AllocationBudget.bind(self.parent,a,self.rows)
        with self.assertRaises(B3Error):p._AllocationBudget.bind(self.parent,a,[*self.rows,dict(name='media/aa/bb',size=1,sha256='0'*64)])
        with self.assertRaises(B3Error):replace(budget,parent_identity=(budget.parent_identity[0]+1,*budget.parent_identity[1:])).check(self.parent,a)
        for blocks in (None,-1,True):
            with self.assertRaises(B3Error):p._allocated_bytes(types.SimpleNamespace(st_blocks=blocks))
        self.create();owned=p._StageFiles(self.parent,a,self.rows,budget)
        try:
            with patch.object(p,'_allocated_bytes',return_value=a.reserved_bytes+512):
                with self.assertRaises(B3Error):owned.verify()
        finally:owned.close()


class HistoricalSequenceBindingTests(unittest.TestCase):
    """Synthetic binding only; complete B2 history authentication remains upstream."""

    @staticmethod
    def sequence(schema='public', name='item_id_seq', last_value=7):
        control=schema=='easel_backup_control'
        return dict(schema=schema,name=name,owner='owner',data_type='bigint',
            start=1,increment=1,min=1,max=2**63-1,cache=1,cycle=False,
            owned_schema=schema,owned_table='audit' if control else 'item',
            owned_column='id',dependency='i' if control else 'a',
            last_value=last_value,is_called=True)

    @staticmethod
    def definition(sequence):
        return [sequence[k] for k in ('schema','name','data_type','start','increment',
            'min','max','cache','cycle','owned_schema','owned_table','owned_column','dependency')]

    def fixture(self, *, central_control=True, identity_control=False):
        from dataclasses import replace
        public=self.sequence()
        historical=self.sequence('easel_backup_control','audit_id_seq',last_value=913)
        contexts=[];observations={}
        for kind,control in (('central',central_control),('identity',identity_control)):
            ctx,obs=context(kind)
            seqs=([historical] if control else [])+[public]
            obs['schema']['sequence_definitions']=[self.definition(s) for s in seqs]
            pinned=copy.deepcopy(obs['schema'])
            pinned['database'][0][0]='<trusted-database>'
            ctx=replace(ctx,profile=replace(ctx.profile,catalog_sha256=digest(pinned)))
            contexts.append((kind,ctx));observations[kind]=obs
        central=observations['central'];identity=observations['identity']
        facts=dict(profile='synthetic',schema=dict(relations=central['schema'],
            identity=dict(profile='synthetic',relations=identity['schema'])),
            rows=central['rows'],roles={k:dict(before=o['roles'],during=o['roles'],after=o['roles'])
                for k,o in observations.items()},snapshot_projections={k:o['copy'] for k,o in observations.items()},
            sequences={k:{phase:copy.deepcopy([public]) for phase in ('before','dump','after')}
                for k in observations},
            maintenance_snapshot=dict(sequences=[historical] if central_control else [],
                tables=dict(audit=[dict(id=913,action='synthetic-history')]),raw_marker='unchanged'))
        roles={k:canonical(dict(format='easel-roles-v1',inventory=o['roles'])) for k,o in observations.items()}
        # Real authority/context types; no running clock, source IO or DB access.
        a=replace(authority(),contexts=tuple(contexts))
        return facts,roles,a

    def bind(self,facts,roles,a):
        with patch.object(p.time,'monotonic',return_value=11.):
            return p._bind_facts(facts,roles,a)

    def test_exact_sorted_public_and_historical_union_preserves_values_and_inputs(self):
        facts,roles,a=self.fixture();original=copy.deepcopy(facts)
        try:bound=self.bind(facts,roles,a)
        except B3Error:self.fail('complete central catalog requires public plus historical sequences')
        self.assertEqual(bound['central']['sequences'],[
            facts['maintenance_snapshot']['sequences'][0],facts['sequences']['central']['before'][0]])
        self.assertEqual(bound['central']['sequences'][0]['last_value'],913)
        self.assertEqual(bound['identity']['sequences'],facts['sequences']['identity']['before'])
        self.assertEqual(bound['central']['schema'],facts['schema']['relations'])
        self.assertEqual(facts,original)
        bound['central']['sequences'][0]['last_value']=1
        self.assertEqual(facts,original)

    def test_missing_extra_duplicate_or_misnamespaced_history_rejected(self):
        for case in ('missing','extra','duplicate','schema','name'):
            with self.subTest(case=case):
                facts,roles,a=self.fixture();seqs=facts['maintenance_snapshot']['sequences']
                if case=='missing':seqs.clear()
                if case=='extra':seqs.append(self.sequence('easel_backup_control','other_seq'))
                if case=='duplicate':seqs.append(copy.deepcopy(seqs[0]))
                if case=='schema':seqs[0]['schema']='public'
                if case=='name':seqs[0]['name']='other_seq'
                with self.assertRaises(B3Error):self.bind(facts,roles,a)

    def test_history_definition_owner_dependency_and_range_mutations_rejected(self):
        changes=dict(owner='untrusted_owner',data_type='integer',start=2,increment=2,min=0,
            max=10000,cache=2,cycle=True,owned_schema='public',owned_table='item',
            owned_column='other',dependency='a',last_value=0,is_called=1)
        for key,value in changes.items():
            with self.subTest(field=key):
                facts,roles,a=self.fixture()
                facts['maintenance_snapshot']['sequences'][0][key]=value
                with self.assertRaises(B3Error):self.bind(facts,roles,a)

    def test_history_without_control_catalog_is_not_silently_discarded(self):
        facts,roles,a=self.fixture(central_control=False)
        facts['maintenance_snapshot']['sequences']=[self.sequence('easel_backup_control','audit_id_seq')]
        with self.assertRaises(B3Error):self.bind(facts,roles,a)

    def test_identity_cannot_borrow_central_historical_sequence(self):
        facts,roles,a=self.fixture(identity_control=True)
        with self.assertRaises(B3Error):self.bind(facts,roles,a)
        # Putting control history in the public inventory is also unsupported.
        facts,roles,a=self.fixture(identity_control=True)
        for phase in ('before','dump','after'):
            facts['sequences']['identity'][phase].insert(0,copy.deepcopy(facts['maintenance_snapshot']['sequences'][0]))
        with self.assertRaises(B3Error):self.bind(facts,roles,a)

    def test_public_omission_phase_mutation_and_control_injection_rejected(self):
        for case in ('omission','phase_mutation','control_injection','extra'):
            with self.subTest(case=case):
                facts,roles,a=self.fixture()
                if case=='omission':
                    for phase in ('before','dump','after'):facts['sequences']['central'][phase].clear()
                if case=='phase_mutation':facts['sequences']['central']['dump'][0]['last_value']+=1
                if case=='control_injection':
                    for phase in ('before','dump','after'):
                        facts['sequences']['central'][phase].insert(0,copy.deepcopy(facts['maintenance_snapshot']['sequences'][0]))
                if case=='extra':
                    for phase in ('before','dump','after'):
                        facts['sequences']['central'][phase].append(self.sequence(name='other_seq'))
                with self.assertRaises(B3Error):self.bind(facts,roles,a)

    def test_empty_history_only_when_catalog_has_no_control_sequence(self):
        facts,roles,a=self.fixture(central_control=False)
        bound=self.bind(facts,roles,a)
        self.assertEqual(bound['central']['sequences'],facts['sequences']['central']['before'])
        del facts['maintenance_snapshot']
        self.assertEqual(self.bind(facts,roles,a),bound)

    def test_closed_control_scope_cannot_be_redefined_by_catalog(self):
        from dataclasses import replace
        for key,value in (('name','other_seq'),('schema','other_control')):
            with self.subTest(field=key):
                facts,roles,a=self.fixture()
                seq=facts['maintenance_snapshot']['sequences'][0];seq[key]=value
                schema=facts['schema']['relations']
                schema['sequence_definitions']=[self.definition(seq),self.definition(self.sequence())]
                schema['sequence_definitions'].sort(key=lambda row:(row[0],row[1]))
                pin=copy.deepcopy(schema);pin['database'][0][0]='<trusted-database>'
                ctx=a.contexts[0][1]
                a=replace(a,contexts=(('central',replace(ctx,profile=replace(ctx.profile,catalog_sha256=digest(pin)))),a.contexts[1]))
                with self.assertRaises(B3Error):self.bind(facts,roles,a)


if __name__=='__main__':unittest.main()
