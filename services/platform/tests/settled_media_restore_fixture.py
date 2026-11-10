"""Synthetic live-path, frozen-016 PostgreSQL fixture. No SSH or real upstream.

The coordinator imports no platform_service modules. A -I worker checks all
Python/SQL bytes against the fixed wheel before importing its staged package.
Connection metadata stays in an exclusive local 0700 directory / 0600 file.
Every container and volume is new and labelled; stop preserves both objects.
"""
import asyncio
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import random
import re
import secrets
import shutil
import stat
import subprocess
import sys
import time
from uuid import uuid4
from urllib.parse import urlsplit
import zipfile

STAGE = Path('/private/tmp/easel-media-m1.1-size-staging-20261027')
WHEEL_SHA = 'f50e52511e941d3bea8e724eddbd68330e20eaa6339c5e2437b3880ca9705abf'
IMAGE = 'postgres@sha256:9e2cf6287f25c3151f4c9421ccce2b17f3e60061c111a29165e0d2cdd9ff0a97'
MODELS = ('gpt-image-2.5-flare', 'gpt-image-2.5-sunburst')
NAMESPACE = 'newapi_x_oneapi_request_id_v1'
LABEL = 'easel.settled-media-fixture'


class FixtureRejected(ValueError):
    pass


def require(value):
    if not value:
        raise FixtureRejected('synthetic media fixture rejected')


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def verify_frozen_source(stage=STAGE):
    stage = Path(stage)
    try:
        require(stage.resolve() == STAGE)
        wheel = stage/'dist/easel_central_platform-0.1.0-py3-none-any.whl'
        raw = wheel.read_bytes()
        require(digest(raw) == WHEEL_SHA)
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            names = sorted(name for name in archive.namelist() if name.startswith('platform_service/') and name.endswith(('.py', '.sql')))
            current = sorted(str(path.relative_to(stage/'src')) for path in (stage/'src/platform_service').rglob('*') if path.is_file() and path.suffix in ('.py', '.sql'))
            require(names == current and len(names) == 76)
            require(all((stage/'src'/name).read_bytes() == archive.read(name) for name in names))
            migrations = [name for name in names if '/migrations/' in name]
            require(len(migrations) == 16 and migrations[-1].endswith('/016_m1_image_enablement.sql'))
        return dict(wheel_sha256=WHEEL_SHA, source_count=76, migration_count=16)
    except (OSError, ValueError, zipfile.BadZipFile):
        raise FixtureRejected('frozen 016 source rejected') from None


def private_write(path, value):
    raw = (json.dumps(value, sort_keys=True, indent=2) + '\n').encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def private_read(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid() and info.st_nlink == 1 and stat.S_IMODE(info.st_mode) == 0o600 and info.st_size <= 65536)
        return json.loads(stream.read(65537))


def validate_connection_metadata(info):
    """No indirect host/service/PG options, even in a replaced fixture file."""
    try:
        require(re.fullmatch('[a-f0-9]{32}', info['nonce']) is not None)
        require(info['host']=='127.0.0.1' and type(info['port']) is int and 1<=info['port']<=65535)
        require(info['database']=='easel_s1_test_media_'+info['nonce'][:12] and info['schema']=='central')
        require(info['bootstrap_user']=='fixture_owner' and info['bootstrap_oid']==10)
        parsed=urlsplit(info['dsn'])
        require(parsed.scheme=='postgresql' and parsed.hostname=='127.0.0.1' and parsed.port==info['port']
                and parsed.username==info['bootstrap_user'] and parsed.path=='/'+info['database']
                and not parsed.query and not parsed.fragment and re.fullmatch('[A-Za-z0-9_-]{43}',parsed.password or '') is not None)
    except (KeyError, TypeError, ValueError):
        raise FixtureRejected('local fixture connection rejected') from None


def command(args, *, timeout=120, data=None, maximum=16*1024*1024):
    try:
        result = subprocess.run(args, input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, check=False)
        require(result.returncode == 0 and len(result.stdout) <= maximum and len(result.stderr) <= 1024*1024)
        return result.stdout
    except (OSError, subprocess.TimeoutExpired):
        raise FixtureRejected('local fixture command rejected') from None


def docker(*args, **options):
    executable = shutil.which('docker')
    require(executable is not None)
    return command([executable, *args], **options)


def guard(handle):
    nonce = handle['nonce']
    require(re.fullmatch('[a-f0-9]{32}', nonce) is not None)
    require(handle['source']['container'] == 'easel-media-source-' + nonce)
    require(handle['volume'] == 'easel-media-source-data-' + nonce)
    item = json.loads(docker('inspect', handle['source']['container']))[0]
    require(item['Config']['Labels'] == {LABEL: nonce})
    require(item['Config']['Image'] == IMAGE)
    require(item['Config']['Cmd']==['-c','log_statement=none','-c','log_min_error_statement=panic','-c','log_min_messages=panic'])
    require(item['HostConfig']['Memory'] == item['HostConfig']['MemorySwap'] == 512*1024*1024
            and item['HostConfig']['PidsLimit'] == 128 and item['HostConfig']['NanoCpus']==750000000
            and item['HostConfig']['NetworkMode']=='bridge' and item['HostConfig']['LogConfig']['Type']=='none')
    mounts = item['Mounts']
    require(len(mounts) == 1 and mounts[0]['Name'] == handle['volume'] and mounts[0]['Destination'] == '/var/lib/postgresql/data')
    require(item['HostConfig']['PortBindings'] == {'5432/tcp': [{'HostIp': '127.0.0.1', 'HostPort': ''}]})
    volume=json.loads(docker('volume','inspect',handle['volume']))[0]
    require(volume['Labels']=={LABEL:nonce} and volume['Driver']=='local' and not volume['Options'])
    return item


def inspect_stopped(handle):
    return guard(handle)['State']['Running'] is False


async def stop_fixture(handle):
    def stop():
        if guard(handle)['State']['Running']:
            docker('stop', '--time', '10', handle['source']['container'])
        require(inspect_stopped(handle))
    await asyncio.to_thread(stop)


def worker(handle, action):
    # -I ignores cwd/PYTHONPATH and an already imported development package.
    raw = command([sys.executable, '-I', '-B', str(Path(__file__).resolve()), action,
                   handle['connection_file']], timeout=180, maximum=65536)
    return json.loads(raw)


async def verify_fixture(handle):
    guard(handle)
    return await asyncio.to_thread(worker, handle, '--verify')


async def produce_fixture(private_root, *, stage=STAGE):
    verify_frozen_source(stage)
    root = Path(private_root).absolute()
    require(root.parent.resolve() == root.parent and not root.exists() and not root.is_symlink())
    root.mkdir(mode=0o700)
    nonce = uuid4().hex
    name, volume = 'easel-media-source-' + nonce, 'easel-media-source-data-' + nonce
    database, user, schema = 'easel_s1_test_media_' + nonce[:12], 'fixture_owner', 'central'
    handle = dict(nonce=nonce, volume=volume, image=IMAGE, connection_file=str(root/'connection-private.json'),
                  storage_root=str(root/'media'), source=dict(kind='central', container=name, database=database, user=user, schema=schema, bootstrap_oid=10),
                  guard=dict(label_key=LABEL, memory_bytes=512*1024*1024, pids_limit=128,
                             nano_cpus=750000000, port_host='127.0.0.1', log_driver='none'))
    password = secrets.token_urlsafe(32)
    envfile = root/'postgres-private.env'
    fd = os.open(envfile, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        stream.write('POSTGRES_USER='+user+'\nPOSTGRES_DB='+database+'\nPOSTGRES_PASSWORD='+password+'\n')
    try:
        # Existing local image only: a fixture must never implicitly pull one.
        docker('image', 'inspect', IMAGE)
        docker('volume', 'create', '--label', LABEL+'='+nonce, volume)
        docker('create', '--name', name, '--label', LABEL+'='+nonce, '--memory', '512m', '--memory-swap', '512m',
               '--cpus', '0.75', '--pids-limit', '128', '--network', 'bridge', '--log-driver', 'none',
               '--publish', '127.0.0.1::5432', '--env-file', str(envfile), '--mount',
               'type=volume,src='+volume+',dst=/var/lib/postgresql/data', IMAGE,
               '-c', 'log_statement=none', '-c', 'log_min_error_statement=panic', '-c', 'log_min_messages=panic')
        guard(handle)
        docker('start', name)
        ready = False
        for _ in range(120):
            try:
                running = docker('exec', name, 'cat', '/proc/1/comm').strip() == b'postgres'
                if running:
                    docker('exec', name, 'pg_isready', '-U', user, '-d', database)
                    ready = True
                    break
            except FixtureRejected:
                pass
            await asyncio.sleep(0.25)
        require(ready)
        port = json.loads(docker('inspect', name))[0]['NetworkSettings']['Ports']['5432/tcp']
        require(len(port) == 1 and port[0]['HostIp'] == '127.0.0.1')
        value = dict(dsn='postgresql://'+user+':'+password+'@127.0.0.1:'+port[0]['HostPort']+'/'+database,
                     host='127.0.0.1', port=int(port[0]['HostPort']), database=database, schema=schema,
                     bootstrap_user=user, bootstrap_oid=10, nonce=nonce, storage_root=handle['storage_root'], stage=str(STAGE))
        private_write(root/'connection-private.json', value)
        private_write(root/'fixture-handle-private.json', handle)
        handle['postconditions'] = await asyncio.to_thread(worker, handle, '--seed')
        private_write(root/'postconditions.json', handle['postconditions'])
        return handle
    except BaseException:
        # Never remove fixture objects/data, nor act on an unverified container.
        try:
            await stop_fixture(handle)
        except Exception:
            pass
        raise


def png(seed):
    from PIL import Image
    raw = random.Random(seed).randbytes(1024*1024)
    stream = io.BytesIO()
    Image.frombytes('L', (1024, 1024), raw).save(stream, format='PNG')
    return stream.getvalue()


def model_definition(model):
    return dict(id=model, version='synthetic-size-v2-016', name=model+' synthetic fixture', capability='image.generate',
        feature='image', products=['easel'], test_only=False, provider_id='newapi_images', provider_version='synthetic-016',
        adapter='openai_images_v1', protocol_version='1', verification='live', verification_ref='synthetic-only:'+digest(model.encode()),
        rate={'version':'internal-one-point-per-image-20261027-v1','meters':[{'unit':'image','basis':'verified_delivery_v1','numerator':'1000000','denominator':'1'}]},
        limits={'max_input_bytes':65536,'max_assets':0,'max_artifact_bytes':20971520,'max_units':{'image':1}},
        parameters={'type':'object','properties':{'n':{'type':'integer','minimum':1,'maximum':1},'size':{'type':'string','enum':['1024x1024']}},'required':['n','size'],'additionalProperties':False})


async def seed(db, info):
    from platform_service.config import Settings, OIDCClient
    from platform_service.contracts import OperatorIdentity, Scope
    from platform_service.identity import IdentityService
    from platform_service.internal_grants import InternalGrantPolicy, InternalGrantService
    from platform_service.internal_features import InternalFeatureService
    from platform_service.media.core import MediaService
    from platform_service.media_config import MediaSettings, MediaProvider
    from platform_service.media_runtime import MediaRuntime
    import httpx
    settings = Settings(info['dsn'], 'http://127.0.0.1:18874', issuer='http://127.0.0.1:3011/oidc',
                        oidc_clients={'easel':OIDCClient('synthetic-client','synthetic-only')}, allow_loopback_http=True, db_schema=info['schema'])
    environment = 'fixture-'+info['nonce']
    policy = InternalGrantPolicy(environment, settings.public_url, info['host'], info['port'], info['database'], info['schema'], settings.issuer, 'a'*64)
    scope = Scope('u','easel','t','wallet','fixture-session',0)
    async with db.connection() as c:
        await c.execute("INSERT INTO users(id) VALUES ('u'),('other')")
        await c.execute("INSERT INTO tenants(id,owner_user_id) VALUES ('t','u'),('foreign','other')")
        await c.execute("INSERT INTO tenant_members(tenant_id,user_id,role) VALUES ('t','u','owner'),('foreign','other','owner')")
        await c.execute("INSERT INTO billing_accounts(id,tenant_id) VALUES ('wallet','t'),('foreign-wallet','foreign')")
        await c.execute("INSERT INTO products(id) VALUES ('easel')")
        await c.execute("INSERT INTO product_clients(product_id,client_id) VALUES ('easel','synthetic-client')")
        await c.execute("INSERT INTO external_identities(issuer,subject,user_id,email_verified) VALUES (%s,'synthetic-subject','u',true)", (settings.issuer,))
        await c.execute("INSERT INTO identity_transactions(id,product_id,client_id,callback_uri,handoff_challenge,local_state,expires_at,state_hash,cookie_hash,nonce_hash,consumed) VALUES ('synthetic-proof','easel','synthetic-client','http://127.0.0.1:37689/platform-auth/callback',%s,'state',clock_timestamp()+interval '5 minutes',%s,%s,%s,true)", ('a'*43,'b'*64,'c'*64,'d'*64))
        await c.execute("INSERT INTO identity_handoffs(code_hash,transaction_id,user_id,tenant_id,billing_account_id,issuer,subject,expires_at,consumed) VALUES (%s,'synthetic-proof','u','t','wallet',%s,'synthetic-subject',clock_timestamp()+interval '1 minute',true)", ('e'*64,settings.issuer))
        await c.execute("INSERT INTO identity_sessions(id,user_id,tenant_id,billing_account_id,product_id,client_id,authorization_epoch,issuer,subject,expires_at,source_transaction_id) VALUES ('fixture-session','u','t','wallet','easel','synthetic-client',0,%s,'synthetic-subject',clock_timestamp()+interval '7 days','synthetic-proof')", (settings.issuer,))
        await c.execute("INSERT INTO identity_tokens(token_hash,session_id,kind,expires_at) VALUES (%s,'fixture-session','refresh',clock_timestamp()+interval '7 days')", ('f'*64,))
    operator = OperatorIdentity('synthetic-operator', frozenset({'internal:issue','internal:extend_features','media:publish'}))
    issued = await InternalGrantService(db, settings, policy).issue(operator, environment=environment, wallet='wallet', session=scope.session_id, key='synthetic-once', reason='Isolated synthetic restore fixture; no payment')
    await InternalFeatureService(db).extend(operator, environment=environment, grant_id=issued['id'], product='easel', feature='image', key='synthetic-image', reason='Isolated synthetic image right')
    service = MediaService(db, IdentityService(db,None))
    configured = MediaSettings(mode='live', storage_root=Path(info['storage_root']), providers={'newapi_images':MediaProvider(
        'synthetic-016','https://fixture.invalid/v1','synthetic-only',dict(zip(MODELS,MODELS)),capabilities=('image.generate',),response_profile='newapi_images_v1')})
    calls=[]
    images={model:png(index+101) for index,model in enumerate(MODELS)}
    def fake(request):
        require(request.method=='POST' and request.url.host=='fixture.invalid' and request.url.path=='/v1/images/generations')
        value=json.loads(request.content)
        require(value['model'] in MODELS and value['n']==1 and value['size']=='1024x1024')
        calls.append(value['model']); index=MODELS.index(value['model'])
        correlation='2026102700000000000000'+str(index)+'SyntheticRef000'+str(index)
        require(re.fullmatch('[0-9]{23}[A-Za-z0-9]{16}',correlation) is not None)
        return httpx.Response(200,json={'created':1793067144,'data':[{'b64_json':base64.b64encode(images[value['model']]).decode()}]},headers={'x-oneapi-request-id':correlation})
    runtime=MediaRuntime(db,service.identity,configured,service=service,transport=httpx.MockTransport(fake))
    try:
        for model in MODELS:
            await service.publish(model_definition(model),operator)
            await service.set_enabled(model,True,operator)
            token=await runtime.mint(scope,dict(model=model,capability='image.generate',invocation_key='fixture-'+model))
            request=dict(model=model,capability='image.generate',prompt='Synthetic restore image; no real provider call',assets=[],parameters={'n':1,'size':'1024x1024'})
            result=await runtime.submit(token['task_token'],'fixture-once',request)
            require(result['financial_state']=='settled' and result['actual']=='1000000')
            require((await runtime.artifact(scope,result['artifacts'][0]['id'],content=True))[0]==images[model])
            require((await runtime.submit(token['task_token'],'fixture-once',request))['id']==result['id'])
            await service.set_enabled(model,False,operator)
        require(calls==list(MODELS))
    finally:
        await runtime.close()


async def postconditions(db, info):
    from PIL import Image
    from platform_service.contracts import Scope
    from platform_service.credits import CreditLedger
    from platform_service.errors import PlatformError
    from platform_service.identity import IdentityService
    from platform_service.media.core import MediaService
    scope=Scope('u','easel','t','wallet','fixture-session',0)
    async with db.connection() as c:
        migrations=await (await c.execute('SELECT version,checksum FROM schema_migrations ORDER BY version')).fetchall()
        require(len(migrations)==16 and migrations[-1]['version']=='016_m1_image_enablement.sql')
        for row in migrations:require(row['checksum']==digest((STAGE/'src/platform_service/migrations'/row['version']).read_bytes()))
        role=await (await c.execute('SELECT oid FROM pg_roles WHERE rolname=current_user')).fetchone(); require(role['oid']==10)
        jobs=await (await c.execute('SELECT * FROM media_jobs ORDER BY model')).fetchall()
        require(len(jobs)==2 and [r['model'] for r in jobs]==list(MODELS))
        for row in jobs:
            require((row['status'],row['execution_state'],row['delivery_state'],row['amount'],row['actual'],row['funding_mode'])==('settled','completed','available',1000000,1000000,'live'))
            require(row['request']['assets']==[] and row['request']['parameters']=={'n':1,'size':'1024x1024'} and row['model_definition']['test_only'] is False)
        invocations=await (await c.execute('SELECT operation_count,held,spent FROM media_invocations')).fetchall()
        require(len(invocations)==2 and all((r['operation_count'],r['held'],r['spent'])==(1,0,1000000) for r in invocations))
        for table in ('media_receipts','media_terminal_evidence'):
            rows=await (await c.execute('SELECT * FROM '+table)).fetchall();require(len(rows)==2)
            for row in rows:
                require(row['provider_response_id'] is None and row['reference_kind']=='http_request_id' and row['http_reference_namespace']==NAMESPACE and re.fullmatch('[0-9]{23}[A-Za-z0-9]{16}',row['http_reference_value']))
                require(row['quantities']=={'image':1} and set(row['basis_evidence'])=={'verified_delivery_v1'} and len(row['evidence_hash'])==64)
        references=await (await c.execute('SELECT * FROM media_http_response_references')).fetchall(); require(len(references)==2 and all(r['namespace']==NAMESPACE and len(r['response_body_sha256'])==64 for r in references))
        blobs=await (await c.execute("SELECT *,expires_at>clock_timestamp()+interval '30 minutes' fresh FROM media_blobs")).fetchall()
        artifacts=await (await c.execute('SELECT * FROM media_artifacts')).fetchall(); require(len(blobs)==len(artifacts)==2)
        for blob in blobs:
            require(blob['state']=='attached' and blob['fresh'] and all(blob[k] is None for k in ('purged_at','deleted_at','gc_token','gc_lease_until')))
            require((blob['user_id'],blob['tenant_id'],blob['billing_account_id'])==('u','t','wallet'))
            path=Path(info['storage_root'])/blob['storage_key']; require(path.stat().st_uid==os.geteuid() and stat.S_IMODE(path.stat().st_mode)==0o600 and path.stat().st_nlink==1)
            raw=path.read_bytes();require(digest(raw)==blob['sha256'] and len(raw)==blob['size'] and len(raw)>1000000)
            with Image.open(io.BytesIO(raw)) as image: image.load();require(image.size==(1024,1024) and image.format=='PNG')
            artifact=next(r for r in artifacts if r['id']==blob['attached_id'])
            require(artifact['storage_key']==blob['storage_key'] and artifact['sha256']==blob['sha256'] and artifact['properties']=={'width':1024,'height':1024,'format':'PNG'} and artifact['scope']==blob['scope'])
        require(set(p.name for p in Path(info['storage_root']).iterdir())==set(b['storage_key'] for b in blobs))
        budgets=await (await c.execute('SELECT * FROM media_storage_budgets')).fetchall();require(len(budgets)==2 and all(r['released_at'] is not None and r['remaining_bytes']==r['remaining_files']==0 for r in budgets))
        states=await (await c.execute('SELECT * FROM media_model_states ORDER BY model_id')).fetchall();require(len(states)==2 and all(r['enabled'] is False for r in states))
        for table in ('media_assets','execution_jobs','billing_orders'):
            require((await (await c.execute('SELECT count(*) n FROM '+table)).fetchone())['n']==0)
        require((await (await c.execute("SELECT count(*) n FROM media_provider_associations WHERE kind<>'http'")).fetchone())['n']==0)
        require((await (await c.execute('SELECT count(*) n FROM (SELECT journal_id FROM credit_entries GROUP BY journal_id HAVING sum(delta)<>0) unbalanced')).fetchone())['n']==0)
    summary=await CreditLedger(db).summary(scope);require(summary['issued']=='10000000' and summary['spent']=='2000000' and summary['available']=='8000000' and summary['reserved']=='0')
    service=MediaService(db,IdentityService(db,None))
    from dataclasses import replace
    for artifact in artifacts:
        try:await service.get_artifact(replace(scope,user_id='other',tenant_id='foreign',billing_account_id='foreign-wallet'),artifact['id'])
        except PlatformError:pass
        else:raise FixtureRejected('foreign artifact accessible')
    return dict(jobs=2,settled=2,artifacts=2,blobs=2,http_references=2,receipts=2,terminal_evidence=2,amount=2000000,spent=2000000,held=0,storage_budgets_released=2,models_disabled=2,pngs_decoded=2,provider_posts=0,fake_transport_posts=2,migration_count=16,source_count=76)


async def worker_main(action, path):
    require(action in ('--seed','--verify'))
    info=private_read(path); verify_frozen_source(info['stage'])
    validate_connection_metadata(info)
    require(not any(name=='platform_service' or name.startswith('platform_service.') for name in sys.modules))
    sys.path.insert(0,str(STAGE/'src'))
    import platform_service
    require(Path(platform_service.__file__).resolve().is_relative_to(STAGE/'src/platform_service'))
    import psycopg
    from psycopg import sql
    from platform_service.db import Database
    if action=='--seed':
        async with await psycopg.AsyncConnection.connect(info['dsn'],autocommit=True) as control:
            await control.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(info['schema'])))
    db=Database(info['dsn'],schema=info['schema'],max_size=3)
    await db.open()
    try:
        if action=='--seed':
            await db.migrate()
            await seed(db,info)
        result=await postconditions(db,info)
        print(json.dumps(result,sort_keys=True))
    finally:await db.close()


if __name__=='__main__':
    try:
        require(len(sys.argv)==3)
        asyncio.run(worker_main(sys.argv[1],sys.argv[2]))
    except Exception as error:
        # Only fixed code locations/type; never exception text, SQL or locals.
        import traceback
        if len(sys.argv)==3:
            report={'error_type':type(error).__name__,'stack':[{'function':f.name,'line':f.lineno} for f in traceback.extract_tb(error.__traceback__)]}
            try:private_write(Path(sys.argv[2]).parent/('worker-failure-'+sys.argv[1][2:]+'.json'),report)
            except Exception:pass
        print('synthetic media fixture worker rejected',file=sys.stderr)
        sys.exit(1)
