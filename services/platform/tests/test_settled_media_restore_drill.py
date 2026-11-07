"""Settled nonempty recovery: synthetic only, production scripts remain frozen."""
import copy
import hashlib
import importlib.util
import json
import os
import shutil
import asyncio
import io
import subprocess
import uuid
from types import SimpleNamespace
from pathlib import Path
import pytest

SCRIPT=Path(__file__).parents[1]/'scripts/verify_settled_media_restore_drill.py'
MODELS=('gpt-image-2.5-flare','gpt-image-2.5-sunburst')

@pytest.fixture
def lib():
    spec=importlib.util.spec_from_file_location('settled_drill',SCRIPT);value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value

def settled_state():
    return {'models':[{'model_id':model,'enabled':False} for model in MODELS],
        'model_jobs':[{'model':model,'jobs':1} for model in MODELS],
        'invocations':2,'jobs':2,'artifacts':2,'assets':0,'blobs':2,'budgets':2,
        'settled_completed_jobs':2,'valid_invocations':2,'released_budgets':2,
        'valid_png_artifacts':2,'valid_attached_blobs':2,'typed_terminal_evidence':2,'typed_receipts':2,
        'http_references':2,'attempts':2,'http_associations':2,'owners':1,
        'active_jobs':0,'pinned_blobs':0,'gc_claims':0}

def test_two_settled_owned_images_are_admitted_without_reissuing_credit(lib):
    assert lib.settled_window(settled_state()) is None
    assert hashlib.sha256(SCRIPT.with_name('verify_media_restore_drill.py').read_bytes()).hexdigest()=='68dccf3699a3c9b06d79e5cf87609dcf567b2d6c93c8b3c6186cc40c3474e1d3'

@pytest.mark.parametrize('key,value',[
    ('active_jobs',1),('settled_completed_jobs',1),('valid_invocations',1),('released_budgets',1),
    ('valid_png_artifacts',1),('valid_attached_blobs',1),('typed_terminal_evidence',1),('typed_receipts',1),
    ('http_references',1),('attempts',1),('http_associations',1),('pinned_blobs',1),('gc_claims',1),
    ('jobs',3),('artifacts',3),('assets',1),('blobs',3),('budgets',3),('invocations',3),('owners',2)])
def test_pending_gc_unreleased_budget_incomplete_evidence_and_foreign_rows_reject(lib,key,value):
    state=settled_state();state[key]=value
    with pytest.raises(lib.Rejected):lib.settled_window(state)

def test_models_must_be_disabled_and_one_job_each_exactly(lib):
    for change in ('enabled','same_model','extra_model','missing_model'):
        state=settled_state()
        if change=='enabled':state['models'][0]['enabled']=True
        elif change=='same_model':state['model_jobs'][0]['jobs']=2;state['model_jobs'].pop()
        elif change=='extra_model':state['models'].append({'model_id':'another','enabled':False})
        else:state['models'].pop()
        with pytest.raises(lib.Rejected):lib.settled_window(state)

def test_window_hash_refuses_any_drift_between_snapshot_and_live_recheck(lib):
    before=settled_state();lib.match_settled_window(before,copy.deepcopy(before))
    after=copy.deepcopy(before);after['pinned_blobs']=1
    with pytest.raises(lib.Rejected):lib.match_settled_window(before,after)

def test_unknown_or_wrong_typed_state_fails_closed(lib):
    for state in ({},dict(settled_state(),jobs=True),dict(settled_state(),unexpected=0)):
        with pytest.raises(lib.Rejected):lib.settled_window(state)

def test_nonempty_proof_requires_two_files_and_target_settled_typed_queries(lib):
    plan={'nonce':'a'*32,'release':lib.RELEASE,'candidate_sha256':lib.WHEEL_SHA}
    databases=[{'kind':kind,'matched':True} for kind in ('central','identity')]
    media={'media_blobs_covered':True,'media_config_restored_verified':True,'permissions_restored_verified':True,
        'source_configuration_files_restored_verified':True,'blob_count':2,'nonempty_media_blobs_restore_verified':True,
        'settled_jobs_restore_verified':True,'typed_receipts_restore_verified':True}
    assert lib.enable_proof(databases,media,plan,'b'*64,True)['success'] is True
    for key,value in [('blob_count',0),('blob_count',1),('nonempty_media_blobs_restore_verified',False),
                      ('settled_jobs_restore_verified',False),('typed_receipts_restore_verified',False)]:
        with pytest.raises(lib.Rejected):lib.enable_proof(databases,dict(media,**{key:value}),plan,'b'*64,True)

def load_code(path,name):
    spec=importlib.util.spec_from_file_location(name,path);value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value

@pytest.mark.skipif(not os.environ.get('EASEL_SETTLED_SOURCE_HANDLE'),reason='explicit owned synthetic source handle required')
def test_actual_nonpublic_window_resolves_immutable_storage_function_in_scope(lib):
    helper=load_code(Path(__file__).with_name('settled_media_restore_fixture.py'),'scoped_fixture')
    handle=helper.private_read(Path(os.environ['EASEL_SETTLED_SOURCE_HANDLE']));helper.guard(handle)
    core=load_code(SCRIPT.with_name('verify_encrypted_restore_drill.py'),'scoped_core')
    docker=shutil.which('docker');assert docker
    def run(args,**kwargs):return helper.command([docker if value=='/usr/bin/docker' else value for value in args],**kwargs)
    source=handle['source']
    state=core.psql(run,source['container'],source['user'],source['database'],lib.window_sql(core,source['schema']))
    lib.settled_window(state)

@pytest.mark.skipif(os.environ.get('EASEL_SETTLED_RESTORE_DOCKER')!='1',reason='explicit owned synthetic restore environment required')
def test_two_settled_pngs_typed_receipts_wallet_and_raw_inventory_survive_fresh_cipher_restore(lib,tmp_path,monkeypatch):
    fixture=load_code(Path(__file__).with_name('settled_media_restore_fixture.py'),'settled_capture_fixture')
    if os.environ.get('EASEL_SETTLED_SOURCE_HANDLE'):
        handle=fixture.private_read(Path(os.environ['EASEL_SETTLED_SOURCE_HANDLE']))
    else:handle=asyncio.run(fixture.produce_fixture(tmp_path/'private-source'))
    core=load_code(SCRIPT.with_name('verify_encrypted_restore_drill.py'),'settled_capture_core')
    assert hashlib.sha256(SCRIPT.with_name('verify_encrypted_restore_drill.py').read_bytes()).hexdigest()==lib.CORE_SHA
    public=Path('/private/tmp/easel-restore-drill-review-20261027')
    backup_code=load_code(public/'easel_encrypted_backup.py','settled_fixture_backup')
    crypto=load_code(public/'sealed_transfer.py','settled_fixture_crypto')
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
        state=core.psql(run,source['container'],source['user'],source['database'],lib.window_sql(core,source['schema']));lib.settled_window(state)
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
        cipher=tmp_path/'fresh-settled.sealed';cipher.write_bytes(envelope);cipher.chmod(0o600)
        clear=crypto.unseal(private,cipher.read_bytes(),identifier,'central',crypto.TARGET);backup_code.verify_payload(clear,manifest)
        with lib.tarfile.open(fileobj=io.BytesIO(clear),mode='r:') as archive:
            restored={item.name:archive.extractfile(item).read() for item in archive.getmembers() if item.name!='manifest.json'}
        core.verify_members(restored,manifest)
        target['user']=inventory['bootstrap_role'];target_started=True
        receipt=core.restore_one(backup,target,restored,inventory,r2_check_repair=True,r2_schema=source['schema'])
        assert receipt['matched'] is True
        actual_state=core.psql(run,target['name'],target['user'],source['database'],lib.window_sql(core,source['schema']))
        lib.match_settled_window(state,actual_state)
        actual_rows=core.psql(run,target['name'],target['user'],source['database'],lib.media_rows_sql(core,source['schema']))
        assert actual_rows==rows and lib.digest(lib.canonical(actual_rows))==media_inventory['registry_sha256']
        result=lib.restore_private_media(tmp_path/'restored-private',{name:raw for name,raw in restored.items() if name.startswith('media/blobs/')},
            json.loads(restored['inventories/media.json']),restored['configs'+str(lib.MEDIA)],uid,gid)
        result.update(lib.restore_source_configurations(tmp_path/'restored-configurations',{name:restored[name] for name in configurations},json.loads(restored['inventories/configurations.json'])))
        assert result['blob_count']==2 and result['nonempty_media_blobs_restore_verified'] is True
        assert result['source_configuration_count']==11
        core.match_inventories(inventory,core.collect_inventory(run,target['name'],target['user'],source['database']))
        altered=bytearray(envelope);altered[-1]^=1
        with pytest.raises(crypto.SealedTransferError):crypto.unseal(private,bytes(altered),identifier,'central',crypto.TARGET)
        if os.environ.get('EASEL_SETTLED_RESTORE_EVIDENCE'):
            safe={'status':'synthetic_settled_nonempty_restore_verified','tool_sha256':lib.digest(SCRIPT.read_bytes()),'candidate_sha256':lib.WHEEL_SHA,
                'migrations':16,'constraints':529,'tables':len(inventory['tables']),'rows':sum(row['n'] for row in inventory['tables'].values()),
                'blob_count':2,'blob_bytes':sum(map(len,files.values())),'complete_inventory_match':True,'settled_typed_window_match':True,
                'source_configuration_count':11,'wallet_spent':2000000,'wallet_held':0,'budgets_released':2,'typed_receipts':2,
                'ciphertext_bytes':len(envelope),'durable_ciphertext_restored':True,'tampered_ciphertext_rejected':True,
                'schema_sha256':inventory['schema'],'constraints_sha256':lib.digest(lib.canonical(inventory['constraints'])),
                'data_sha256':lib.digest(lib.canonical(inventory['tables'])),'real_provider_posts':0,'fake_transport_posts':2,'server_executed':False}
            path=Path(os.environ['EASEL_SETTLED_RESTORE_EVIDENCE']);path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(safe,sort_keys=True,indent=2)+'\n')
        complete=True
    finally:
        if target_started:
            try:core.stop_target(backup,target)
            except core.Rejected:
                if complete:raise
        asyncio.run(fixture.stop_fixture(handle))
