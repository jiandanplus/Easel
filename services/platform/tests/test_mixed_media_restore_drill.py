"""The known missing staged file is preserved as absence, never as delivery."""
import asyncio
import io
import shutil
import subprocess
import uuid
from types import SimpleNamespace
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import pytest

SCRIPT=Path(__file__).parents[1]/'scripts/verify_mixed_media_restore_drill.py'
PENDING='81a6155d-c7dd-4b98-bd3f-5e909a4d3ba9'
FLARE='a934ccdf-d78d-446c-95ec-7b8fd028a2fa'
SUNBURST='9ccc8430-3c29-46cc-9d5d-961be08f6acc'
MODELS=('gpt-image-2.5-flare','gpt-image-2.5-sunburst')

def load(path,name):
    spec=importlib.util.spec_from_file_location(name,path);lib=importlib.util.module_from_spec(spec);spec.loader.exec_module(lib);return lib

@pytest.fixture
def lib():return load(SCRIPT,'mixed_drill')

def pending_exception():
    scope={'user_id':'u','tenant_id':'t','billing_account_id':'w','product_id':'easel','session_id':'s','authorization_epoch':0}
    shared=dict(scope=scope,user_id='u',tenant_id='t',billing_account_id='w')
    job=dict(shared,id=PENDING,invocation_id='inv',attempt_id='attempt',reservation_id='reservation',binding_hash='b'*64,
        request_hash='c'*64,model=MODELS[0],capability='image.generate',funding_mode='live',amount=1000000,actual=None,
        status='pending_reconciliation',execution_state='unknown',delivery_state='processing',reason='upstream_uncertain',
        dispatched_at='2026-10-27T01:00:00Z')
    attempt=dict(job_id=PENDING,attempt_id='attempt',binding_hash='b'*64,provider_scope_hash='d'*64,provider_request_hash='e'*64,upstream_model=MODELS[0],request_id='synthetic-request')
    reference=dict(job_id=PENDING,attempt_id='attempt',binding_hash='b'*64,provider_scope_hash='d'*64,
        namespace='newapi_x_oneapi_request_id_v1',http_request_id='0'*23+'a'*16,response_body_sha256='f'*64)
    association=dict(job_id=PENDING,attempt_id='attempt',binding_hash='b'*64,provider_scope_hash='d'*64,kind='http',http_status=200,
        http_request_id=reference['http_request_id'],provider_response_id=None,provider_job_id=None)
    blob=dict(shared,storage_key='1'*64,kind='artifact',invocation_id='inv',job_id=PENDING,attempt_id='attempt',sha256='2'*64,size=1024,
        mime='image/png',properties={'width':1024,'height':1024,'format':'PNG'},state='staged',attached_id=None,
        purged_at=None,deleted_at=None,gc_token=None,gc_lease_until=None)
    budget=dict(shared,job_id=PENDING,max_bytes=20971520,max_files=1,remaining_bytes=20971520-1024,remaining_files=0,released_at=None)
    reservation=dict(id='reservation',status='pending_reconciliation',actual=None,amount=1000000,funding_mode='live',operation='media',
        idempotency_key=PENDING,body_hash='c'*64,user_id='u',tenant_id='t',billing_account_id='w',product_id='easel')
    invocation=dict(shared,id='inv',operation_count=1,held=1000000,spent=0,funding_mode='live',model=MODELS[0],capability='image.generate')
    return dict(job=job,attempt=attempt,reference=reference,association=association,blob=blob,budget=budget,reservation=reservation,invocation=invocation)

def mixed_state():
    state=load(Path(__file__).with_name('test_settled_media_restore_drill.py'),'old_state').settled_state()
    state.update(invocations=3,jobs=3,blobs=3,budgets=3,http_references=3,attempts=3,http_associations=3,
        pending_jobs=1,valid_pending_jobs=1,valid_pending_invocations=1,unreleased_budgets=1,valid_staged_blobs=1,pinned_blobs=1,active_jobs=0)
    state['model_jobs']=[{'model':MODELS[0],'jobs':2},{'model':MODELS[1],'jobs':1}]
    state['history']=[{'id':PENDING,'model':MODELS[0],'status':'pending_reconciliation'},
        {'id':FLARE,'model':MODELS[0],'status':'settled'},{'id':SUNBURST,'model':MODELS[1],'status':'settled'}]
    state['history'].sort(key=lambda row:row['id']);state['pending_exception']=pending_exception()
    return state

def test_known_pending_is_preserved_with_two_settled_jobs(lib):
    assert lib.mixed_window(mixed_state()) is None
    assert lib.missing_staged(mixed_state())['storage_key']=='1'*64
    assert lib.missing_staged(mixed_state())['staged_row_sha256']==lib.digest(lib.canonical(pending_exception()['blob']))

@pytest.mark.parametrize('key,value',[
    ('jobs',2),('pending_jobs',0),('valid_pending_jobs',0),('valid_pending_invocations',0),
    ('unreleased_budgets',0),('valid_staged_blobs',0),('pinned_blobs',0),('active_jobs',1),('gc_claims',1),
    ('typed_receipts',3),('typed_terminal_evidence',3),('http_references',2),('artifacts',3),('assets',1)])
def test_any_unexpected_state_rejects(lib,key,value):
    state=mixed_state();state[key]=value
    with pytest.raises(lib.Rejected):lib.mixed_window(state)

@pytest.mark.parametrize('section,key,value',[
    ('job','id','unknown-job'),('job','actual',0),('job','amount',999999),('job','reason','waived'),
    ('attempt','binding_hash','0'*64),('reference','attempt_id','another-attempt'),('reference','namespace','object_id'),
    ('reference','http_request_id','unknown'),('association','http_status',500),('blob','job_id','another-job'),
    ('blob','attempt_id','another-attempt'),('blob','state','attached'),('blob','attached_id','fake'),('blob','gc_token','token'),
    ('budget','remaining_files',1),('budget','released_at','now'),('budget','remaining_bytes',0),
    ('reservation','actual',0),('reservation','id','foreign'),('invocation','held',0),('invocation','spent',1000000)])
def test_pending_binding_actual_null_pin_quota_and_evidence_are_not_relaxed(lib,section,key,value):
    state=mixed_state();state['pending_exception'][section][key]=value
    with pytest.raises(lib.Rejected):lib.mixed_window(state)

def test_exact_history_and_all_private_pending_bytes_are_bound(lib):
    before=mixed_state();lib.match_mixed_window(before,copy.deepcopy(before))
    after=copy.deepcopy(before);after['pending_exception']['attempt']['request_id']='another-valid-id'
    lib.mixed_window(after)
    with pytest.raises(lib.Rejected):lib.match_mixed_window(before,after)
    for changed in ('unknown_id','wrong_model','extra'):
        state=mixed_state()
        if changed=='unknown_id':state['history'][0]['id']='unknown'
        elif changed=='wrong_model':state['history'][0]['model']=MODELS[1]
        else:state['history'].append(dict(state['history'][0]))
        with pytest.raises(lib.Rejected):lib.mixed_window(state)

def files(lib,root):
    root.mkdir(mode=0o700);rows=[]
    for i in (3,4):
        raw=(str(i)*32).encode();key=str(i)*64;path=root/key;path.write_bytes(raw);path.chmod(0o600)
        rows.append(dict(storage_key=key,sha256=lib.digest(raw),size=len(raw),state='attached'))
    row=pending_exception()['blob'];rows.append({key:row[key] for key in ('storage_key','sha256','size','state')})
    return rows

def test_exact_missing_staged_allowlist_is_absence_and_restores_no_placeholder(lib,tmp_path):
    root=tmp_path/'media';rows=files(lib,root);missing=lib.missing_staged(mixed_state())
    entries,inventory=lib.capture_blobs(root,rows,os.geteuid(),os.getegid(),missing)
    assert len(entries)==2 and inventory['missing_staged']==missing and inventory['missing_staged_count']==1
    result=lib.restore_private_media(tmp_path/'restored',entries,inventory,b'{}',os.geteuid(),os.getegid())
    assert result['known_missing_staged_absence_restored_verified'] is True and result['blob_count']==2
    assert not (tmp_path/'restored/media'/missing['storage_key']).exists()
    assert len(list((tmp_path/'restored/media').iterdir()))==2

@pytest.mark.parametrize('change',['no_allowlist','wrong_key','wrong_sha','wrong_size','staged_exists','attached_missing','unknown','partial','symlink'])
def test_any_extra_missing_or_unknown_file_and_mismatched_allowlist_reject(lib,tmp_path,change):
    root=tmp_path/'media';rows=files(lib,root);missing=lib.missing_staged(mixed_state())
    if change=='no_allowlist':missing=None
    elif change=='wrong_key':missing['storage_key']='5'*64
    elif change=='wrong_sha':missing['sha256']='6'*64
    elif change=='wrong_size':missing['size']+=1
    elif change=='staged_exists':(root/('1'*64)).write_bytes(b'not a delivered file')
    elif change=='attached_missing':(root/('3'*64)).unlink()
    elif change=='unknown':(root/'unknown').write_bytes(b'unknown')
    elif change=='partial':(root/('1'*64+'.part')).write_bytes(b'partial')
    else:(root/('1'*64)).symlink_to(root/('3'*64))
    with pytest.raises(lib.Rejected):lib.capture_blobs(root,rows,os.geteuid(),os.getegid(),missing)

def test_only_latest_api_dropin_exactly_adds_read_write_paths(lib):
    for kind in lib.UNITS:
        user='easel-central' if kind=='api' else 'easel-console';port='19874' if kind=='api' else '19875'
        main=('[Service]\nUser='+user+'\nGroup='+user+'\nWorkingDirectory='+lib.SOURCE_RELEASE+'\nEnvironmentFile=/etc/easel-central/'+('central' if kind=='api' else 'console')+'.env\nExecStart='+lib.SOURCE_RELEASE+'/.venv/bin/python -m fixture_cli --host 127.0.0.1 --port '+port+'\n').encode()
        expected=lib.expected_dropin(main,kind)
        assert (b'ReadWritePaths=/var/lib/easel-central/media\n' in expected)==(kind=='api')
        assert expected.count(b'ReadWritePaths=')==(1 if kind=='api' else 0)
        lib.verify_dropins({k:lib.expected_dropin(main_for(k,lib),k) for k in lib.UNITS},{k:main_for(k,lib) for k in lib.UNITS})

def test_actual_mount_namespace_requires_only_media_writable_and_protected_paths_ro(lib):
    raw=('1 0 8:1 / / ro - ext4 /dev/sda1 rw\n'
        '2 1 8:1 /etc /etc ro - ext4 /dev/sda1 rw\n'
        '3 1 8:1 /var/lib/easel-central/media /var/lib/easel-central/media rw - ext4 /dev/sda1 rw\n').encode()
    result=lib.verify_mount_namespace(raw,'mnt:[123456]','api')
    assert result['media_mount_rw'] is True and result['protected_paths_readonly'] is True
    assert result['mount_namespace_sha256']==lib.digest(b'mnt:[123456]')
    for changed in (raw.replace(b'media rw -',b'media ro -'),raw.replace(b'/etc ro -',b'/etc rw -'),
        raw+('4 1 8:1 / '+lib.RELEASE+' rw - ext4 /dev/sda1 rw\n').encode()):
        with pytest.raises(lib.Rejected):lib.verify_mount_namespace(changed,'mnt:[123456]','api')
    with pytest.raises(lib.Rejected):lib.verify_mount_namespace(raw,'unknown','api')

@pytest.mark.parametrize('mount_options,super_options',[
    ('rw','ro'),('rw','ro,rw'),('rw','nosuid'),('ro,rw','rw'),('nosuid','rw')])
def test_media_mount_requires_unambiguous_rw_on_mount_and_superblock(lib,mount_options,super_options):
    raw=('1 0 8:1 / / ro - ext4 /dev/sda1 rw\n'
        '2 1 8:1 /etc /etc ro - ext4 /dev/sda1 rw\n'
        '3 1 8:1 /var/lib/easel-central/media /var/lib/easel-central/media '+mount_options+
        ' - ext4 /dev/sda1 '+super_options+'\n').encode()
    with pytest.raises(lib.Rejected):lib.verify_mount_namespace(raw,'mnt:[123456]','api')

def test_protected_mount_is_readonly_if_superblock_is_readonly(lib):
    raw=('1 0 8:1 / / ro - ext4 /dev/sda1 rw\n'
        '2 1 8:1 /etc /etc rw - ext4 /dev/sda1 ro\n'
        '3 1 8:1 /var/lib/easel-central/media /var/lib/easel-central/media rw - ext4 /dev/sda1 rw\n'
        '4 1 8:1 / '+lib.RELEASE+' rw - ext4 /dev/sda1 ro\n').encode()
    assert lib.verify_mount_namespace(raw,'mnt:[123456]','api')['protected_paths_readonly'] is True

def main_for(kind,lib):
    user='easel-central' if kind=='api' else 'easel-console';port='19874' if kind=='api' else '19875'
    return ('[Service]\nUser='+user+'\nGroup='+user+'\nWorkingDirectory='+lib.SOURCE_RELEASE+'\nEnvironmentFile=/etc/easel-central/'+('central' if kind=='api' else 'console')+'.env\nExecStart='+lib.SOURCE_RELEASE+'/.venv/bin/python -m fixture_cli --host 127.0.0.1 --port '+port+'\n').encode()

def test_initial_and_settled_tools_and_core_remain_frozen():
    for name,expected in {
        'verify_settled_media_restore_drill.py':'49df4b72db760f88022c0dd3e7b4d8e566463fb12ab1a6044a8a2b59bf7f857d',
        'verify_media_restore_drill.py':'68dccf3699a3c9b06d79e5cf87609dcf567b2d6c93c8b3c6186cc40c3474e1d3',
        'capture_media_restore_plan.py':'af1906bf0a0d012f3033407e4a41ac136b3b37c5746dd2891ebf29a6ba43bd2b',
        'verify_encrypted_restore_drill.py':'4d3c4210469d6630dd4cbc71d8d5327e62aa972eedb7084685bb0df85a2c88ea'}.items():
        assert hashlib.sha256(SCRIPT.with_name(name).read_bytes()).hexdigest()==expected

def test_nonempty_proof_requires_preserved_pending_and_target_typed_state(lib):
    plan={'nonce':'a'*32,'release':lib.RELEASE,'candidate_sha256':lib.WHEEL_SHA,'missing_staged_sha256':'c'*64}
    db=[{'kind':kind,'matched':True} for kind in ('central','identity')]
    media={key:True for key in ('media_blobs_covered','media_config_restored_verified','permissions_restored_verified',
        'source_configuration_files_restored_verified','nonempty_media_blobs_restore_verified','settled_jobs_restore_verified',
        'typed_receipts_restore_verified','pending_job_restore_verified','pending_actual_null_preserved',
        'known_missing_staged_absence_restored_verified')}
    media.update(blob_count=2,missing_staged_count=1,missing_staged_sha256='c'*64,pending_hold_preserved=1000000)
    assert lib.enable_proof(db,media,plan,'b'*64,True)['success'] is True
    for key,value in [('pending_job_restore_verified',False),('pending_actual_null_preserved',False),
        ('known_missing_staged_absence_restored_verified',False),('missing_staged_count',0),('missing_staged_sha256','d'*64),
        ('pending_hold_preserved',0),('typed_receipts_restore_verified',False),('blob_count',1)]:
        with pytest.raises(lib.Rejected):lib.enable_proof(db,dict(media,**{key:value}),plan,'b'*64,True)


@pytest.mark.skipif(os.environ.get('EASEL_MIXED_RESTORE_DOCKER')!='1',reason='explicit owned synthetic restore environment required')
def test_two_pngs_plus_erofs_pending_hold_and_absence_survive_fresh_cipher_restore(lib,tmp_path,monkeypatch):
    fixture=load(Path(__file__).with_name('mixed_media_restore_fixture.py'),'settled_capture_fixture')
    if os.environ.get('EASEL_MIXED_SOURCE_HANDLE'):
        handle=fixture.private_read(Path(os.environ['EASEL_MIXED_SOURCE_HANDLE']))
    else:handle=asyncio.run(fixture.produce_fixture(tmp_path/'private-source'))
    core=load(SCRIPT.with_name('verify_encrypted_restore_drill.py'),'settled_capture_core')
    assert hashlib.sha256(SCRIPT.with_name('verify_encrypted_restore_drill.py').read_bytes()).hexdigest()==lib.CORE_SHA
    public=Path('/private/tmp/easel-restore-drill-review-20261027')
    backup_code=load(public/'easel_encrypted_backup.py','settled_fixture_backup')
    crypto=load(public/'sealed_transfer.py','settled_fixture_crypto')
    docker=shutil.which('docker');assert docker
    popen=subprocess.Popen
    monkeypatch.setattr(core.subprocess,'Popen',lambda args,**kwargs:popen([docker if value=='/usr/bin/docker' else value for value in args],**kwargs))
    def run(args,maximum=core.MAXIMUM,data=None,timeout=90):
        return fixture.command([docker if value=='/usr/bin/docker' else value for value in args],maximum=maximum,data=data,timeout=timeout)
    source=handle['source'];descriptor=('central',source['container'],source['user'],source['database'])
    core.SOURCES=(descriptor,)
    backup=SimpleNamespace(command=run,Rejected=core.Rejected)
    def gate(name):
        assert name==source['container'];assert fixture.guard(handle)['State']['Running'] is True
    backup.container_gate=gate
    monkeypatch.setattr(lib,'STORAGE',Path(handle['storage_root']))
    uid,gid=os.geteuid(),os.getegid()
    migrations=[{'version':path.name,'checksum':lib.digest(path.read_bytes())} for path in sorted((fixture.STAGE/'src/platform_service/migrations').glob('*.sql'))]
    target=core.container_identity(uuid.uuid4().hex,'central');target_started=False;complete=False
    try:
        assert asyncio.run(fixture.verify_fixture(handle))['spent']==2000000
        dump,roles,inventory,window,files,media_inventory=lib.capture_media_database(core,backup,descriptor,source['schema'],uid,gid,migrations)
        assert len(files)==2 and all(len(raw)>1000000 for raw in files.values())
        assert inventory['money']=={'lots_balanced':True,'journals_balanced':True} and len(inventory['constraints'])==529
        rows=core.psql(run,source['container'],source['user'],source['database'],lib.media_rows_sql(core,source['schema']))
        state=core.psql(run,source['container'],source['user'],source['database'],lib.window_sql(core,source['schema']));lib.mixed_window(state)
        assert media_inventory['mixed_window_sha256']==lib.digest(lib.canonical(state))
        assert lib.missing_staged(state)==media_inventory['missing_staged']
        assert not (Path(handle['storage_root'])/media_inventory['missing_staged']['storage_key']).exists()
        configurations={'configs'+path:('synthetic-only:'+path).encode() for path in lib.SOURCE_FILES}
        config=json.dumps({'mode':'live','storage_root':str(lib.STORAGE),'providers':{'fixture':{'secret':'synthetic-only'}}}).encode()
        configurations['configs'+str(lib.MEDIA)]=config
        for kind in lib.UNITS:
            user='easel-central' if kind=='api' else 'easel-console';port='19874' if kind=='api' else '19875'
            env='/etc/easel-central/central.env' if kind=='api' else '/etc/easel-central/console.env'
            main=('[Service]\nUser='+user+'\nGroup='+user+'\nWorkingDirectory='+lib.SOURCE_RELEASE+'\nEnvironmentFile='+env+'\nExecStart='+lib.SOURCE_RELEASE+'/.venv/bin/python -m fixture_cli --host 127.0.0.1 --port '+port+'\n').encode()
            configurations['configs'+str(lib.MAIN_UNITS[kind])]=main
            configurations['configs'+str(lib.DROPINS[kind])]=lib.expected_dropin(main,kind)
        metadata=[{'name':name,'uid':uid,'gid':gid,'mode':0o644 if name in {'configs'+str(path) for path in lib.MAIN_UNITS.values()} else 0o600,
            'size':len(raw),'sha256':lib.digest(raw)} for name,raw in sorted(configurations.items())]
        entries={'databases/central.dump':dump,'roles/central.sql':roles,'inventories/central.json':lib.canonical(inventory),
            'inventories/media.json':lib.canonical(media_inventory),'inventories/configurations.json':lib.canonical(metadata),**configurations,**files}
        manifest={'format':'easel-encrypted-backup-v2','files':[{'name':name,'length':len(raw),'sha256':lib.digest(raw)} for name,raw in sorted(entries.items())]}
        payload=backup_code.archive(entries,manifest)
        key=crypto.rsa.generate_private_key(public_exponent=65537,key_size=3072);serialization=crypto.serialization
        private=key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption())
        public_key=key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo)
        identifier=str(uuid.uuid4());envelope=crypto.seal(public_key,payload,{'transfer_id':identifier,'kind':'central','target':crypto.TARGET,'expected_sha256':lib.digest(payload),'expected_length':len(payload)})
        cipher=tmp_path/'fresh-mixed.sealed';cipher.write_bytes(envelope);cipher.chmod(0o600)
        clear=crypto.unseal(private,cipher.read_bytes(),identifier,'central',crypto.TARGET);backup_code.verify_payload(clear,manifest)
        with lib.tarfile.open(fileobj=io.BytesIO(clear),mode='r:') as archive:
            restored={item.name:archive.extractfile(item).read() for item in archive.getmembers() if item.name!='manifest.json'}
        core.verify_members(restored,manifest)
        target['user']=inventory['bootstrap_role'];target_started=True
        receipt=core.restore_one(backup,target,restored,inventory,r2_check_repair=True,r2_schema=source['schema'])
        assert receipt['matched'] is True
        actual_state=core.psql(run,target['name'],target['user'],source['database'],lib.window_sql(core,source['schema']))
        lib.match_mixed_window(state,actual_state)
        actual_rows=core.psql(run,target['name'],target['user'],source['database'],lib.media_rows_sql(core,source['schema']))
        assert actual_rows==rows and lib.digest(lib.canonical(actual_rows))==media_inventory['registry_sha256']
        result=lib.restore_private_media(tmp_path/'restored-private',{name:raw for name,raw in restored.items() if name.startswith('media/blobs/')},
            json.loads(restored['inventories/media.json']),restored['configs'+str(lib.MEDIA)],uid,gid)
        result.update(lib.restore_source_configurations(tmp_path/'restored-configurations',{name:restored[name] for name in configurations},json.loads(restored['inventories/configurations.json'])))
        assert result['blob_count']==2 and result['nonempty_media_blobs_restore_verified'] is True
        assert result['source_configuration_count']==11
        assert result['known_missing_staged_absence_restored_verified'] is True
        assert result['missing_staged_count']==1
        assert not (tmp_path/'restored-private/media'/media_inventory['missing_staged']['storage_key']).exists()
        core.match_inventories(inventory,core.collect_inventory(run,target['name'],target['user'],source['database']))
        altered=bytearray(envelope);altered[-1]^=1
        with pytest.raises(crypto.SealedTransferError):crypto.unseal(private,bytes(altered),identifier,'central',crypto.TARGET)
        if os.environ.get('EASEL_MIXED_RESTORE_EVIDENCE'):
            safe={'status':'synthetic_fixed_mixed_nonempty_restore_verified','tool_sha256':lib.digest(SCRIPT.read_bytes()),'candidate_sha256':lib.WHEEL_SHA,
                'migrations':16,'constraints':529,'tables':len(inventory['tables']),'rows':sum(row['n'] for row in inventory['tables'].values()),
                'blob_count':2,'blob_bytes':sum(map(len,files.values())),'complete_inventory_match':True,'mixed_typed_window_match':True,'pending_actual_null_preserved':True,'missing_staged_absence_preserved':True,
                'source_configuration_count':11,'wallet_spent':2000000,'wallet_held':1000000,'budgets_released':2,'typed_receipts':2,
                'ciphertext_bytes':len(envelope),'durable_ciphertext_restored':True,'tampered_ciphertext_rejected':True,
                'schema_sha256':inventory['schema'],'constraints_sha256':lib.digest(lib.canonical(inventory['constraints'])),
                'data_sha256':lib.digest(lib.canonical(inventory['tables'])),'real_provider_posts':0,'fake_transport_posts':3,'server_executed':False}
            path=Path(os.environ['EASEL_MIXED_RESTORE_EVIDENCE']);path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(safe,sort_keys=True,indent=2)+'\n')
        complete=True
    finally:
        if target_started:
            try:core.stop_target(backup,target)
            except core.Rejected:
                if complete:raise
        asyncio.run(fixture.stop_fixture(handle))
