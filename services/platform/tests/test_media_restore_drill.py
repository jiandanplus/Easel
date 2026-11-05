"""Synthetic media backup boundaries; no real credentials or network."""
import importlib.util
import json
import os
from pathlib import Path
import stat
import zipfile
import io
import shutil
import subprocess
import uuid
import sys
from types import SimpleNamespace
import pytest

SCRIPT = Path(__file__).parents[1]/'scripts/verify_media_restore_drill.py'
CAPTURED_STAGE=Path(os.environ.get('EASEL_MEDIA_DRILL_FIXTURE_STAGE','/private/tmp/easel-media-m1.1-size-staging-20261027'))

@pytest.fixture
def lib():
    spec=importlib.util.spec_from_file_location('media_restore_drill',SCRIPT)
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value)
    return value

@pytest.fixture
def owned(tmp_path):
    root=tmp_path/'source';root.mkdir(mode=0o700)
    return root,os.geteuid(),os.getegid()

def row(lib,raw=b'synthetic-owned-image',key='a'*64,state='attached'):
    return {'storage_key':key,'sha256':lib.digest(raw),'size':len(raw),'state':state}

def put(root,key,raw=b'synthetic-owned-image'):
    path=root/key;path.write_bytes(raw);path.chmod(0o600);return path

def test_capture_restore_nonempty_bytes_metadata_and_safe_receipt(lib,owned,tmp_path):
    root,uid,gid=owned;record=row(lib);put(root,record['storage_key'])
    entries,inventory=lib.capture_blobs(root,[record],uid,gid)
    assert entries=={'media/blobs/'+record['storage_key']:b'synthetic-owned-image'}
    config=json.dumps({'storage_root':str(root),'secret':'synthetic-never-safe-output'}).encode()
    target=tmp_path/'restore';result=lib.restore_private_media(target,entries,inventory,config,uid,gid)
    restored=target/'media'/record['storage_key']
    assert restored.read_bytes()==b'synthetic-owned-image'
    assert (restored.stat().st_uid,restored.stat().st_gid,stat.S_IMODE(restored.stat().st_mode))==(uid,gid,0o600)
    assert stat.S_IMODE(restored.parent.stat().st_mode)==0o700
    assert (target/'configs/media.json').read_bytes()==config
    assert result['blob_count']==1 and result['blob_bytes']==len(b'synthetic-owned-image')
    assert result['nonempty_media_blobs_restore_verified'] is True
    assert record['storage_key'] not in json.dumps(result) and 'synthetic-never-safe-output' not in json.dumps(result)
    with pytest.raises(FileExistsError):lib.restore_private_media(target,entries,inventory,config,uid,gid)

def test_empty_directory_is_honest_empty_coverage(lib,owned,tmp_path):
    root,uid,gid=owned;entries,inventory=lib.capture_blobs(root,[],uid,gid)
    result=lib.restore_private_media(tmp_path/'restored',entries,inventory,b'{}',uid,gid)
    assert result['blob_count']==0 and result['media_blobs_covered'] is True
    assert result['nonempty_media_blobs_restore_verified'] is False
    assert result['coverage_basis']=='empty_registry_and_empty_owned_directory'

@pytest.mark.parametrize('change',['hash','size','missing','extra','symlink','hardlink','mode','owner','part','directory'])
def test_incomplete_or_unowned_source_refuses_instead_of_skipping(lib,owned,tmp_path,change):
    root,uid,gid=owned;record=row(lib);path=put(root,record['storage_key'])
    if change=='hash':record['sha256']='0'*64
    elif change=='size':record['size']+=1
    elif change=='missing':path.unlink()
    elif change=='extra':put(root,'b'*64)
    elif change=='symlink':path.unlink();path.symlink_to(tmp_path/'elsewhere')
    elif change=='hardlink':os.link(path,root/('b'*64))
    elif change=='mode':path.chmod(0o644)
    elif change=='owner':uid+=1
    elif change=='part':path.rename(str(path)+'.part')
    elif change=='directory':path.unlink();path.mkdir(mode=0o700)
    with pytest.raises(lib.Rejected):lib.capture_blobs(root,[record],uid,gid)

@pytest.mark.parametrize('key',['../x','A'*64,'a'*63,'a'*65,'a'*32+'.part','a'*32+'/x',''])
def test_registry_path_is_exact_lowercase_hex_only(lib,key):
    with pytest.raises(lib.Rejected):lib.blob_index([row(lib,key=key)])

def test_deleted_history_is_retained_without_resurrecting_file(lib,owned):
    root,uid,gid=owned;record=row(lib,state='deleted')
    entries,inventory=lib.capture_blobs(root,[record],uid,gid)
    assert not entries and inventory['deleted_count']==1
    put(root,record['storage_key'])
    with pytest.raises(lib.Rejected):lib.capture_blobs(root,[record],uid,gid)

@pytest.mark.parametrize('state',['staged','gc_pending','unknown'])
def test_transition_states_require_separate_quarantine_plan(lib,state):
    with pytest.raises(lib.Rejected):lib.blob_index([row(lib,state=state)])

def test_duplicate_keys_and_oversized_metadata_refused(lib):
    record=row(lib)
    for rows in [[record,record],[dict(record,size=0)],[dict(record,size=67108865)],[dict(record,sha256='0'*63)]]:
        with pytest.raises(lib.Rejected):lib.blob_index(rows)

def test_read_detects_same_fd_mutation(lib,owned,monkeypatch):
    root,uid,gid=owned;record=row(lib);path=put(root,record['storage_key'])
    original=os.read;changed=False
    def read(fd,size):
        nonlocal changed
        raw=original(fd,size)
        if raw and not changed:
            changed=True;path.write_bytes(b'changed-during-snapshot');path.chmod(0o600)
        return raw
    monkeypatch.setattr(lib.os,'read',read)
    with pytest.raises(lib.Rejected):lib.capture_blobs(root,[record],uid,gid)

def test_restore_rejects_tampered_entry_and_does_not_create_target(lib,owned,tmp_path):
    root,uid,gid=owned;record=row(lib);put(root,record['storage_key'])
    entries,inventory=lib.capture_blobs(root,[record],uid,gid)
    entries[next(iter(entries))]=b'wrong'
    target=tmp_path/'restore'
    with pytest.raises(lib.Rejected):lib.restore_private_media(target,entries,inventory,b'{}',uid,gid)
    assert not target.exists()

def test_media_configuration_binds_fixed_path_and_secret_stays_private(lib):
    raw=b'{"mode":"live","storage_root":"/var/lib/easel-central/media","providers":{"p":{"secret":"synthetic-only"}}}'
    value=lib.media_configuration(raw)
    assert value['storage_root']=='/var/lib/easel-central/media'
    for bad in [b'{}',raw.replace(b'/var/lib/easel-central/media',b'/tmp/media'),b'{"storage_root":"/var/lib/easel-central/media","storage_root":"/tmp"}']:
        with pytest.raises(lib.Rejected):lib.media_configuration(bad)

def test_initial_live_window_requires_exact_disabled_models_and_no_media_work(lib):
    state={'models':[{'model_id':name,'enabled':False} for name in lib.MODELS],
           'invocations':0,'jobs':0,'blobs':0,'budgets':0}
    lib.initial_window(state)
    for key in ('invocations','jobs','blobs','budgets'):
        with pytest.raises(lib.Rejected):lib.initial_window(dict(state,**{key:1}))
    bad=json.loads(json.dumps(state));bad['models'][0]['enabled']=True
    with pytest.raises(lib.Rejected):lib.initial_window(bad)
    with pytest.raises(lib.Rejected):lib.initial_window(dict(state,models=[]))

def test_explicit_media_environment_is_unique_and_fixed(lib):
    raw=b'# synthetic\nCENTRAL_DB_SCHEMA=fixture\nCENTRAL_MEDIA_CONFIG_FILE="/etc/easel-central/media.json"\n'
    lib.media_environment(raw)
    lib.media_environment(b'CENTRAL_DB_SCHEMA=fixture\n',allow_absent=True)
    for bad in [raw.replace(b'/etc/easel-central/media.json',b'/tmp/other'),raw+b'CENTRAL_MEDIA_CONFIG_FILE=/etc/easel-central/media.json\n',raw+b' CENTRAL_MEDIA_CONFIG_FILE = /tmp/other\n',b'CENTRAL_DB_SCHEMA=fixture\n']:
        with pytest.raises(lib.Rejected):lib.media_environment(bad)

def source_service(lib,kind):
    user='easel-central' if kind=='api' else 'easel-console';port='19874' if kind=='api' else '19875'
    env='/etc/easel-central/central.env' if kind=='api' else '/etc/easel-central/console.env'
    return ('[Service]\nUser='+user+'\nGroup='+user+'\nWorkingDirectory='+lib.SOURCE_RELEASE+'\nEnvironmentFile='+env+
            '\nExecStart='+lib.SOURCE_RELEASE+'/.venv/bin/python -m synthetic_cli --host 127.0.0.1 --port '+port+'\n').encode()

def test_cutover_dropins_have_exact_service_argv_and_media_scope(lib):
    mains={kind:source_service(lib,kind) for kind in ('api','console')}
    api=lib.expected_dropin(mains['api'],'api');console=lib.expected_dropin(mains['console'],'console')
    lib.verify_dropins({'api':api,'console':console},mains)
    for kind,old,new in [('api',b'19874',b'19879'),('console',b'19875',b'19876'),('api',lib.RELEASE.encode(),b'/tmp/other'),('api',b'CENTRAL_MEDIA_CONFIG_FILE',b'OTHER')]:
        bad={'api':api,'console':console};bad[kind]=bad[kind].replace(old,new)
        with pytest.raises(lib.Rejected):lib.verify_dropins(bad,mains)
    with pytest.raises(lib.Rejected):lib.verify_dropins({'api':api,'console':console+b'Environment=CENTRAL_MEDIA_CONFIG_FILE=/etc/easel-central/media.json\n'},mains)

def test_dropin_renderer_matches_deployment_helper_with_private_argv_kept_local(lib,monkeypatch):
    release=load_public_code(SCRIPT.with_name('prepare_m1_image_release.py'),'synthetic_release_helper')
    monkeypatch.setitem(sys.modules,'prepare_m1_image_release',release)
    cutover=load_public_code(SCRIPT.with_name('prepare_m1_image_cutover.py'),'synthetic_cutover_helper')
    for kind in ('api','console'):
        main=source_service(lib,kind)
        for raw in (main,main.replace(b'--host 127.0.0.1',b'--host=127.0.0.1').replace(b'--port ',b'--port=')):
            assert lib.expected_dropin(raw,kind)==cutover.render_dropin(raw.decode(),kind).encode()
        for bad in (main.replace(b'127.0.0.1',b'0.0.0.0'),main.replace(b'--host ',b'--host=127.0.0.1 --host '),main.replace(b'ExecStart=',b'ExecStart=x\nExecStart=')):
            with pytest.raises(lib.Rejected):lib.expected_dropin(bad,kind)

def test_actual_process_must_be_new_release_not_only_reloaded_unit_property(lib):
    main=source_service(lib,'api');argv=lib.source_argv(main,'api');argv[0]=lib.RELEASE+'/.venv/bin/python'
    record={'pid':123,'active':True,'cwd':lib.RELEASE,'argv':argv,'exe':'/synthetic/bin/python3.12',
            'venv_exe':'/synthetic/bin/python3.12','media_path':str(lib.MEDIA)}
    result=lib.verify_process_record(record,main,'api')
    assert result['pid']==123 and result['media_environment_match'] is True
    assert 'argv' not in result and 'media_path' not in result
    for key,value in [('cwd',lib.SOURCE_RELEASE),('argv',[lib.SOURCE_RELEASE+'/.venv/bin/python',*argv[1:]]),('exe','/other/python'),('media_path',None),('active',False)]:
        with pytest.raises(lib.Rejected):lib.verify_process_record(dict(record,**{key:value}),main,'api')

def test_all_source_configs_restore_exact_bytes_uid_gid_mode_and_reject_tampering(lib,tmp_path):
    # Override only the test allowlist. Production has eleven fixed absolute paths.
    names=('/etc/easel-central/media.json','/etc/systemd/system/api.service','/etc/systemd/system/api.service.d/90-m1-images.conf')
    entries={'configs'+name:('synthetic-only:'+name).encode() for name in names}
    uid,gid=os.geteuid(),os.getegid()
    metadata=[{'name':name,'uid':uid,'gid':gid,'mode':0o644 if name.endswith('.service') else 0o600,'sha256':lib.digest(raw),'size':len(raw)} for name,raw in entries.items()]
    destination=tmp_path/'configuration-restore'
    result=lib.restore_source_configurations(destination,entries,metadata,expected_names=set(entries))
    assert result['source_configuration_files_restored_verified'] is True and result['source_configuration_count']==3
    for item in metadata:
        path=destination/item['name'];assert path.read_bytes()==entries[item['name']]
        assert (path.stat().st_uid,path.stat().st_gid,stat.S_IMODE(path.stat().st_mode))==(uid,gid,item['mode'])
    bad=dict(entries);bad[next(iter(bad))]=b'tampered'
    with pytest.raises(lib.Rejected):lib.restore_source_configurations(tmp_path/'bad',bad,metadata,expected_names=set(entries))
    assert not (tmp_path/'bad').exists()
    with pytest.raises(lib.Rejected):lib.restore_source_configurations(tmp_path/'escape',{'../bad':b'x'},[{'name':'../bad','uid':uid,'gid':gid,'mode':0o600,'sha256':lib.digest(b'x'),'size':1}],expected_names={'../bad'})

def test_private_metadata_requires_exact_uid_gid_mode_and_single_regular_file(lib,tmp_path):
    path=tmp_path/'media.json';path.write_bytes(b'{}');path.chmod(0o600)
    uid,gid=os.geteuid(),os.getegid();lib.private_metadata(path,uid,gid)
    for owner,group in [(uid+1,gid),(uid,gid+1)]:
        with pytest.raises(lib.Rejected):lib.private_metadata(path,owner,group)
    path.chmod(0o644)
    with pytest.raises(lib.Rejected):lib.private_metadata(path,uid,gid)
    path.chmod(0o600);os.link(path,tmp_path/'hardlink')
    with pytest.raises(lib.Rejected):lib.private_metadata(path,uid,gid)

@pytest.mark.skipif(not (CAPTURED_STAGE/'dist/easel_central_platform-0.1.0-py3-none-any.whl').is_file(),reason='explicit captured frozen candidate fixture required')
def test_frozen_release_verifies_every_python_sql_and_exact_wheel(lib):
    stage=CAPTURED_STAGE
    wheel=(stage/'dist/easel_central_platform-0.1.0-py3-none-any.whl').read_bytes()
    read=lambda name:(stage/'src'/name).read_bytes()
    result=lib.verify_release_content(wheel,read)
    assert len(result)==16 and result[-1]=={'version':'016_m1_image_enablement.sql','checksum':lib.MIGRATION_016_SHA}
    def changed(name):return read(name)+b'changed' if name.endswith('media_runtime.py') else read(name)
    with pytest.raises(lib.Rejected):lib.verify_release_content(wheel,changed)
    with pytest.raises(lib.Rejected):lib.verify_release_content(wheel+b'changed',read)

def test_migration_binding_rejects_missing_added_changed_or_reordered(lib):
    expected=[{'version':'001.sql','checksum':'a'*64},{'version':'016_m1_image_enablement.sql','checksum':lib.MIGRATION_016_SHA}]
    lib.match_migrations(expected,expected)
    for actual in [expected[:1],expected+[{'version':'017.sql','checksum':'b'*64}],list(reversed(expected)),[expected[0],dict(expected[1],checksum='0'*64)]]:
        with pytest.raises(lib.Rejected):lib.match_migrations(expected,actual)

def test_enable_proof_is_computed_from_verified_databases_and_media(lib):
    plan={'nonce':'a'*32,'release':lib.RELEASE,'candidate_sha256':lib.WHEEL_SHA}
    databases=[{'kind':kind,'matched':True} for kind in ('central','identity')]
    media={'media_blobs_covered':True,'media_config_restored_verified':True,'permissions_restored_verified':True,'source_configuration_files_restored_verified':True,
           'blob_count':0,'blob_bytes':0,'nonempty_media_blobs_restore_verified':False,'coverage_basis':'empty_registry_and_empty_owned_directory'}
    value=lib.enable_proof(databases,media,plan,'b'*64,True)
    assert value['success'] is True and value['schema_016_restore_verified'] is True
    assert value['candidate_sha256']==lib.WHEEL_SHA and value['source_release']==lib.RELEASE
    assert value['blob_count']==0 and value['nonempty_media_blobs_restore_verified'] is False
    for result in [databases[:1],[{'kind':'central','matched':False},databases[1]]]:
        with pytest.raises(lib.Rejected):lib.enable_proof(result,media,plan,'b'*64,True)
    with pytest.raises(lib.Rejected):lib.enable_proof(databases,media,plan,'b'*64,False)
    with pytest.raises(lib.Rejected):lib.enable_proof(databases,dict(media,media_config_restored_verified=False),plan,'b'*64,True)

def test_failure_cleanup_never_stops_unverified_target(lib):
    from types import SimpleNamespace
    stopped=[];guarded=[]
    def guard(backup,item):
        guarded.append(item['name'])
        if item['name']=='wrong':raise lib.Rejected()
    def command(args,**kw):return b'[{"State":{"Running":true}}]'
    core=SimpleNamespace(guard_target=guard,stop_target=lambda backup,item:stopped.append(item['name']))
    result=lib.cleanup_owned(core,SimpleNamespace(command=command),[{'name':'owned','kind':'central'},{'name':'wrong','kind':'identity'}])
    assert guarded==['owned','wrong'] and stopped==['owned']
    assert result==[{'kind':'central','not_running_verified':True},{'kind':'identity','not_running_verified':False}]

def load_public_code(path,name):
    spec=importlib.util.spec_from_file_location(name,path);value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value

@pytest.mark.skipif(os.environ.get('EASEL_RESTORE_DRILL_DOCKER')!='1',reason='explicit owned synthetic Docker environment required')
def test_full_016_owned_registry_ciphertext_nonempty_fresh_restore(lib,owned,tmp_path,monkeypatch):
    core=load_public_code(SCRIPT.with_name('verify_encrypted_restore_drill.py'),'fixture_core_v6')
    assert lib.digest(SCRIPT.with_name('verify_encrypted_restore_drill.py').read_bytes())==lib.CORE_SHA
    public=Path('/private/tmp/easel-restore-drill-review-20261027')
    backup_code=load_public_code(public/'easel_encrypted_backup.py','fixture_backup')
    crypto=load_public_code(public/'sealed_transfer.py','fixture_crypto')
    assert lib.digest((public/'easel_encrypted_backup.py').read_bytes())==core.BASE_SHA
    assert lib.digest((public/'sealed_transfer.py').read_bytes())==backup_code.HELPER_SHA256
    docker=shutil.which('docker');assert docker
    popen=subprocess.Popen
    monkeypatch.setattr(core.subprocess,'Popen',lambda args,**kwargs:popen([docker if value=='/usr/bin/docker' else value for value in args],**kwargs))
    def run(args,maximum=core.MAXIMUM,data=None,timeout=90):
        proc=popen([docker if value=='/usr/bin/docker' else value for value in args],stdin=subprocess.PIPE if data is not None else subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        try:out,err=proc.communicate(data,timeout=timeout)
        except BaseException:proc.kill();proc.wait();raise
        if proc.returncode or len(out)>maximum:raise core.Rejected('synthetic_command_rejected')
        return out
    source=core.container_identity(uuid.uuid4().hex,'central');target=core.container_identity(uuid.uuid4().hex,'central')
    backup=SimpleNamespace(command=run,Rejected=core.Rejected,container_gate=lambda _:core.guard_target(backup,source))
    schema='synthetic_m1_'+uuid.uuid4().hex[:12];database='synthetic_media_restore'
    started=[];complete=False
    try:
        started.append(source);core.start_target(backup,source)
        core.write_sql(run,source,'postgres','CREATE DATABASE '+core.ident(database)+' OWNER '+core.ident(source['user'])+';')
        core.write_sql(run,source,database,'CREATE SCHEMA '+core.ident(schema)+';')
        core.write_sql(run,source,'postgres','ALTER DATABASE '+core.ident(database)+' SET search_path TO '+core.ident(schema)+',pg_catalog;')
        prefix='SET search_path TO '+core.ident(schema)+',pg_catalog;\n'
        core.write_sql(run,source,database,prefix+"CREATE TABLE schema_migrations(version text PRIMARY KEY,checksum text NOT NULL CHECK(checksum ~ '^[a-f0-9]{64}$'),applied_at timestamptz NOT NULL DEFAULT now());")
        migrations=[]
        for path in sorted((CAPTURED_STAGE/'src/platform_service/migrations').glob('*.sql')):
            raw=path.read_bytes();checksum=lib.digest(raw);migrations.append({'version':path.name,'checksum':checksum})
            core.write_sql(run,source,database,prefix+raw.decode()+"\nINSERT INTO schema_migrations(version,checksum) VALUES("+core.literal(path.name)+','+core.literal(checksum)+');')
        assert len(migrations)==16 and migrations[-1]['checksum']==lib.MIGRATION_016_SHA
        root,uid,gid=owned;raw=b'synthetic-real-owned-media-payload';record=row(lib,raw)
        put(root,record['storage_key'],raw)
        scope={'user_id':'synthetic-u','product_id':'easel','tenant_id':'synthetic-t','billing_account_id':'synthetic-wallet','session_id':'synthetic-session','authorization_epoch':0}
        scope_sql=core.literal(json.dumps(scope))+'::jsonb'
        seed="""
BEGIN;
INSERT INTO users(id) VALUES('synthetic-u');
INSERT INTO tenants(id,owner_user_id) VALUES('synthetic-t','synthetic-u');
INSERT INTO tenant_members(tenant_id,user_id,role) VALUES('synthetic-t','synthetic-u','owner');
INSERT INTO billing_accounts(id,tenant_id) VALUES('synthetic-wallet','synthetic-t');
INSERT INTO products(id) VALUES('easel');
INSERT INTO product_clients(product_id,client_id) VALUES('easel','desktop');
INSERT INTO external_identities(issuer,subject,user_id) VALUES('synthetic','subject','synthetic-u');
INSERT INTO identity_sessions(id,user_id,tenant_id,billing_account_id,product_id,client_id,authorization_epoch,issuer,subject,expires_at)
 VALUES('synthetic-session','synthetic-u','synthetic-t','synthetic-wallet','easel','desktop',0,'synthetic','subject',now()+interval '1 hour');
INSERT INTO media_models(id,definition,operator_id) VALUES('synthetic-image','{"capability":"image.generate"}','synthetic-operator');
INSERT INTO media_invocations(id,scope,session_id,user_id,product_id,tenant_id,billing_account_id,invocation_key,model,capability,funding_mode,expires_at)
 VALUES('synthetic-inv',SCOPE,'synthetic-session','synthetic-u','easel','synthetic-t','synthetic-wallet','one','synthetic-image','image.generate','test',now()+interval '1 hour');
INSERT INTO media_blobs(storage_key,kind,scope,user_id,tenant_id,billing_account_id,invocation_id,sha256,size,mime,properties,state,expires_at)
 VALUES(KEY,'asset',SCOPE,'synthetic-u','synthetic-t','synthetic-wallet','synthetic-inv',HASH,SIZE,'image/png','{}','staged',now()+interval '1 hour');
INSERT INTO media_assets(id,invocation_id,scope,user_id,product_id,tenant_id,billing_account_id,storage_key,sha256,size,mime,properties,expires_at)
 VALUES('synthetic-asset','synthetic-inv',SCOPE,'synthetic-u','easel','synthetic-t','synthetic-wallet',KEY,HASH,SIZE,'image/png','{}',now()+interval '1 hour');
UPDATE media_blobs SET state='attached',attached_id='synthetic-asset' WHERE storage_key=KEY;
COMMIT;
""".replace('SCOPE',scope_sql).replace('KEY',core.literal(record['storage_key'])).replace('HASH',core.literal(record['sha256'])).replace('SIZE',str(record['size']))
        core.write_sql(run,source,database,prefix+seed)
        rows=core.psql(run,source['name'],source['user'],database,lib.media_rows_sql(core,schema));assert rows==[record]
        files,media_inventory=lib.capture_blobs(root,rows,uid,gid)
        dump,roles,inventory,_=core.capture_database(backup,('central',source['name'],source['user'],database))
        assert len(inventory['constraints'])>=529 and inventory['money']=={'lots_balanced':True,'journals_balanced':True}
        config=b'{"storage_root":"/var/lib/easel-central/media","providers":{"p":{"secret":"synthetic-only"}}}'
        configs={'configs'+path:('synthetic-config:'+path).encode() for path in lib.SOURCE_FILES}
        configs['configs'+str(lib.MEDIA)]=config
        for kind in lib.UNITS:
            configs['configs'+str(lib.MAIN_UNITS[kind])]=source_service(lib,kind)
            configs['configs'+str(lib.DROPINS[kind])]=lib.expected_dropin(source_service(lib,kind),kind)
        config_metadata=[{'name':name,'uid':uid,'gid':gid,'mode':0o644 if name in {'configs'+str(path) for path in lib.MAIN_UNITS.values()} else 0o600,
            'sha256':lib.digest(value),'size':len(value)} for name,value in sorted(configs.items())]
        entries={'databases/central.dump':dump,'roles/central.sql':roles,'inventories/central.json':lib.canonical(inventory),
                 'inventories/media.json':lib.canonical(media_inventory),'inventories/configurations.json':lib.canonical(config_metadata),**configs,**files}
        manifest={'format':'easel-encrypted-backup-v2','files':[{'name':key,'length':len(value),'sha256':lib.digest(value)} for key,value in sorted(entries.items())]}
        payload=backup_code.archive(entries,manifest)
        # Fresh synthetic key stays in process memory; never reads the server key.
        key=crypto.rsa.generate_private_key(public_exponent=65537,key_size=3072)
        serialization=crypto.serialization
        pem=key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption())
        pub=key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo)
        identifier=str(uuid.uuid4());metadata={'transfer_id':identifier,'kind':'central','target':crypto.TARGET,'expected_sha256':lib.digest(payload),'expected_length':len(payload)}
        envelope=crypto.seal(pub,payload,metadata);cipher=tmp_path/'synthetic.sealed';cipher.write_bytes(envelope);cipher.chmod(0o600)
        clear=crypto.unseal(pem,cipher.read_bytes(),identifier,'central',crypto.TARGET)
        backup_code.verify_payload(clear,manifest)
        with lib.tarfile.open(fileobj=io.BytesIO(clear),mode='r:') as archive:
            restored={item.name:archive.extractfile(item).read() for item in archive.getmembers() if item.name!='manifest.json'}
        core.verify_members(restored,manifest)
        target['user']=inventory['bootstrap_role'];started.append(target)
        receipt=core.restore_one(backup,target,restored,inventory,r2_check_repair=True,r2_schema=schema)
        assert receipt['matched'] is True
        lib.match_migrations(migrations,core.psql(run,target['name'],target['user'],database,lib.migrations_sql(core,schema)))
        actual=core.psql(run,target['name'],target['user'],database,lib.media_rows_sql(core,schema));assert actual==rows
        delivered=lib.restore_private_media(tmp_path/'restored-nonempty',{name:value for name,value in restored.items() if name.startswith('media/blobs/')},
            json.loads(restored['inventories/media.json']),restored['configs/etc/easel-central/media.json'],uid,gid)
        assert delivered['nonempty_media_blobs_restore_verified'] is True and delivered['blob_count']==1
        configurations=lib.restore_source_configurations(tmp_path/'restored-configurations',{name:restored[name] for name in configs},json.loads(restored['inventories/configurations.json']))
        assert configurations['source_configuration_count']==11 and configurations['source_configuration_files_restored_verified'] is True
        core.match_inventories(inventory,core.collect_inventory(run,target['name'],target['user'],database))
        broken=bytearray(envelope);broken[-1]^=1
        with pytest.raises(crypto.SealedTransferError):crypto.unseal(pem,bytes(broken),identifier,'central',crypto.TARGET)
        if os.environ.get('EASEL_MEDIA_DRILL_EVIDENCE'):
            safe={'status':'synthetic_nonempty_016_restore_verified','script_sha256':lib.digest(SCRIPT.read_bytes()),
                'candidate_sha256':lib.WHEEL_SHA,'core_sha256':lib.CORE_SHA,'migration_016_sha256':lib.MIGRATION_016_SHA,
                'migrations':len(migrations),'constraint_count':len(inventory['constraints']),'tables':len(inventory['tables']),
                'rows':sum(value['n'] for value in inventory['tables'].values()),'schema_sha256':inventory['schema'],
                'complete_inventory_match':True,'registry_binding_match':True,'durable_ciphertext_restored':True,
                'tampered_ciphertext_rejected':True,'blob_count':1,'blob_bytes':len(raw),'source_uid':uid,'source_gid':gid,
                'uid_gid_and_600_700_restored':True,'source_configuration_count':11,'main_units_and_dropins_restored':True,
                'source_configuration_metadata_matched':True,'money':inventory['money'],'server_executed':False,'real_provider_requests':0}
            evidence=Path(os.environ['EASEL_MEDIA_DRILL_EVIDENCE']);evidence.parent.mkdir(parents=True,exist_ok=True)
            evidence.write_text(json.dumps(safe,sort_keys=True,indent=2)+'\n')
        complete=True
    finally:
        for item in reversed(started):
            try:core.stop_target(backup,item)
            except core.Rejected:
                if complete:raise
