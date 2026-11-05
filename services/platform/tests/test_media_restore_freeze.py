"""F-only contract tests. Synthetic adapters; no production, PG or credentials."""
from contextlib import contextmanager
from copy import deepcopy
import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / 'scripts/media_restore_freeze.py'
WINDOW = 'a' * 32  # synthetic fixture, never submitted to an operational tool
CREATED = '2026-10-28T00:00:00+00:00'
EXPIRES = '2026-10-28T00:15:00+00:00'
SENTINEL = 'PRIVATE-SYNTHETIC-SENTINEL'


def load():
    assert SCRIPT.exists(), 'F capture implementation is missing'
    spec = importlib.util.spec_from_file_location('freeze_under_test', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def lib():
    return load()


def existing_fixture():
    path = Path(__file__).with_name('test_mixed_media_restore_drill.py')
    spec = importlib.util.spec_from_file_location('existing_mixed_fixture', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def complete_pending_rows(state):
    """The actual f50/001–016 to_jsonb row shape, rather than a partial probe."""
    e=state['pending_exception']; stamp='2026-10-27T01:00:00+00:00'
    e['job'].update(product_id='easel',operation_key='synthetic-operation',
        request={'assets':[],'parameters':{'n':1,'size':'1024x1024'}},input_units={},
        model_definition={'id':e['job']['model'],'capability':'image.generate','test_only':False,'limits':{'max_assets':0}},
        lease_token=None,lease_until=stamp,cancel_requested_at=None,created_at=stamp,updated_at=stamp)
    for key in ('attempt','reference','association','blob','budget','reservation','invocation'):
        e[key]['created_at']=stamp
    e['blob']['expires_at']='2026-10-29T01:00:00+00:00'
    e['reservation'].update(rate_version='synthetic-rate',completion_key=None,created_xid='123',updated_at=stamp)
    e['invocation'].update(session_id='s',product_id='easel',invocation_key='synthetic-invocation',
                           expires_at='2026-10-29T01:00:00+00:00')
    # Actual JSON decoding produces independent objects; do not let shared fixture
    # references hide cross-row metadata mutations.
    return json.loads(json.dumps(state))


class Adapter:
    """A controlled command sink executes the real pinned v6 collector, no SQL I/O."""
    def __init__(self, lib):
        self.lib = lib
        self.contracts = lib.reviewed_contracts()
        self.calls = []
        self.snapshots = 0
        self.change = None
        self.inventory_generation = 0
        self.value = self.binding_fixture()
        fixture = existing_fixture()
        state = complete_pending_rows(fixture.mixed_state())
        state['models'] = [{'model_id':m, 'enabled':True} for m in self.contracts.mixed.MODELS]
        self.view = {'wallet':{'issued':10000000, 'available':6942425, 'held':1009728,
                              'spent':2047847, 'expired':0, 'refunded':0},
                     'media_state':state, 'text_pending':[{ 'id':'synthetic-text',
                         'status':'pending_reconciliation', 'actual':None, 'amount':9728,
                         'private':SENTINEL}],
                     'registry':[], 'files':{}, 'missing':{}}
        self.view['missing'] = {key:state['pending_exception']['blob'][key]
                               for key in ('storage_key','sha256','size','state')}
        self.view['missing'].update(job_id=fixture.PENDING, attempt_id='attempt', binding_hash='b'*64,
                                  http_reference_sha256=lib.digest(lib.canonical(state['pending_exception']['reference'])),
                                  staged_row_sha256=lib.digest(lib.canonical(state['pending_exception']['blob'])))
        for key in ('3'*64, '4'*64):
            data = ('fixture-'+key).encode()
            row = {'storage_key':key, 'sha256':lib.digest(data), 'size':len(data), 'state':'attached'}
            self.view['registry'].append(row)
            self.view['files'][key] = data
        self.view['registry'].append({k:self.view['missing'][k] for k in ('storage_key','sha256','size','state')})
        self.view['media_inventory'] = {'version':1,'uid':997,'gid':997,'mode':0o700,
            'files':[{k:row[k] for k in ('storage_key','sha256','size')}|{'uid':997,'gid':997,'mode':0o600}
                     for row in self.view['registry'] if row['state']=='attached'],
            'blob_bytes':sum(len(raw) for raw in self.view['files'].values()),
            'missing_staged':deepcopy(self.view['missing']),'missing_staged_count':1,
            'registry_sha256':lib.digest(lib.canonical(self.view['registry']))}

    def binding_fixture(self):
        m = self.contracts.mixed
        root_mount = '1 0 8:1 / / ro - ext4 /dev/sda1 rw\n'
        api_mount = root_mount + '2 1 8:1 /media '+str(m.STORAGE)+' rw - ext4 /dev/sda1 rw\n'
        processes = {}
        for kind in ('api','console'):
            processes[kind] = {'pid':100 if kind=='api' else 200, 'starttime':12345,
                'invocation_id':'b'*32, 'cwd':m.RELEASE, 'command_sha256':'c'*64,
                'executable_sha256':'d'*64, 'media_path':str(m.MEDIA) if kind=='api' else None,
                'mount_namespace':'mnt:[123]' if kind=='api' else 'mnt:[124]',
                'mountinfo':api_mount if kind=='api' else root_mount}
        migrations = sorted((Path(__file__).parents[1]/'src/platform_service/migrations').glob('*.sql'))[:16]
        value = {'target':self.lib.TARGET, 'boot_id':'a'*32, 'release':m.RELEASE,
                'candidate_sha256':m.WHEEL_SHA, 'source_pins':dict(self.lib.SOURCE_PINS),
                'migrations':[{'version':p.name,'checksum':self.lib.digest(p.read_bytes())} for p in migrations],
                'central_schema_sha256':'e'*64, 'central_config_sha256':'1'*64,
                'configurations':{name:{'sha256':'1'*64, 'uid':997 if name in (str(m.MEDIA),'/etc/easel-central/execution.json') else 0,
                    'gid':997 if name in (str(m.MEDIA),'/etc/easel-central/execution.json') else 0,
                    'mode':0o600, 'size':100} for name in m.SOURCE_FILES},
                'source_processes':processes,
                'production_containers':{name:{'id':str(i)*64,'image':'sha256:'+('9'*64 if 'logto' in name else self.contracts.core.IMAGE.split('sha256:')[1]),
                    'started_at':'2026-10-24T00:00:00Z'} for i,name in enumerate(
                        ('easel-central-pg-20261024','easel-identity-pg-v2-20261024','easel-logto-v2-20261024'),1)},
                'original_services':{name:{'ActiveState':'active','MainPID':str(100 if name=='easel-central-production' else 200),
                    'ActiveEnterTimestampMonotonic':'12345'} for name in
                    ('easel-central-production','easel-console-production','easel-gateway-tunnel','nginx')},
                'backup_definitions':{str(p):self.contracts.core.BASE_SHA if p==self.contracts.core.BASE else '8'*64 for p in
                    (self.contracts.core.BASE,Path('/etc/systemd/system/easel-encrypted-backup.service'),Path('/etc/systemd/system/easel-encrypted-backup.timer'))},
                'host_surface':{'mount_namespace':'mnt:[1]','mountinfo':root_mount}}
        value['configurations'][str(m.DROPINS['api'])]['sha256']=m.API_DROPIN_SHA
        return value

    def binding(self):
        return deepcopy(self.value)

    @contextmanager
    def exported_snapshot(self):
        self.snapshots += 1
        self.calls.append(('open', self.snapshots))
        yield f'1-2-{self.snapshots}'
        self.calls.append(('rollback', self.snapshots))
        if self.change and self.snapshots == 1:
            self.change(self)

    def observations(self, snapshot):
        self.calls.append(('observations', snapshot))
        return deepcopy(self.view)

    def command(self, argv, **kwargs):
        self.calls.append(('command', tuple(argv), kwargs))
        core = self.contracts.core
        if argv[3] == 'pg_dump':
            assert '--schema-only' in argv and any(v.startswith('--snapshot=') for v in argv)
            return b'-- fixed synthetic schema\nCREATE TABLE fixture.internal_feature_extensions(id text);'
        assert argv[4] == 'psql'
        raw = kwargs['data'].decode()
        assert raw.startswith('BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;')
        assert 'SET TRANSACTION SNAPSHOT ' in raw and raw.endswith('ROLLBACK;\n')
        statement = raw.split("SET LOCAL extra_float_digits=3;\n",1)[1][:-len(';\nROLLBACK;\n')]
        if statement == core.RELATIONS:
            value = [{'nspname':'fixture','relname':name,'relkind':'r'} for name in
                     ('credit_lots','credit_entries','execution_results','internal_feature_extensions')]
        elif 'FROM pg_largeobject_metadata' in statement: value = 0
        elif 'row_bytes' in statement:
            token = 'changed' if self.inventory_generation and 'execution_results' in statement else 'whole-row'
            value = {'n':1,'bytes':10,'sha256':self.lib.digest(token.encode())}
        elif "'lots_balanced'" in statement: value = {'lots_balanced':True,'journals_balanced':True}
        elif 'WHERE oid=10' in statement: value = 'synthetic-owner'
        elif statement == core.ROLES.format(ignore=core.literal('')):
            value = [{'rolname':'synthetic-owner','rolpassword':SENTINEL,'rolconnlimit':-1,'rolvaliduntil':None,
                      **{k:False for k in ('rolsuper','rolinherit','rolcreaterole','rolcreatedb','rolcanlogin','rolreplication','rolbypassrls')}}]
        elif statement == core.MEMBERS.format(ignore=core.literal('')): value = []
        elif statement == core.DATABASE:
            value = {'datname':SENTINEL,'owner':'synthetic-owner','encoding':'UTF8','datcollate':'C','datctype':'C',
                     'datlocprovider':'c','daticulocale':None,'datcollversion':None,'datconnlimit':-1,
                     'datallowconn':True,'datistemplate':False,'acl':[]}
        elif statement in (core.EXTENSIONS,core.CONSTRAINTS,core.SETTINGS.format(ignore=core.literal(''))): value = []
        else: raise AssertionError('unrecognized read-only synthetic statement')
        return json.dumps(value).encode()


def capture(lib, adapter):
    return lib.capture_freeze(adapter, adapter.contracts, window_id=WINDOW,
                              created_utc=CREATED, expires_utc=EXPIRES)


def test_real_pinned_collector_covers_all_tables_and_snapshot_lifetime():
    lib=load()
    a = Adapter(lib); f = capture(lib,a)
    assert f['inventory']['tables']['"fixture"."internal_feature_extensions"']['n'] == 1
    assert f['inventory']['roles'][0]['rolpassword'] == SENTINEL
    assert f['observations']['wallet']['held'] == 1009728
    assert f['observations']['media_state']['pending_jobs'] == 1
    assert f['observations']['media_state']['models'][0]['enabled'] is True
    assert f['approval'] == {'approved':False,'actions':[], 'paused_media_guard_executed':False}
    assert a.snapshots == 2
    assert len([c for c in a.calls if c[0]=='rollback']) == 2
    assert lib.canonical(f) == a.contracts.core.canonical(f)


def test_other_conserving_balance_is_a_new_capture_not_a_constant(lib):
    a=Adapter(lib); first=capture(lib,a)
    a.view['wallet'].update(available=6952153,held=1000000)
    a.view['text_pending']=[]
    second=capture(lib,a)
    assert first['observations']['wallet'] != second['observations']['wallet']
    assert second['approval']['approved'] is False


@pytest.mark.parametrize('change', ['inventory','wallet','pending','file','binding','source_mount'])
def test_fresh_snapshot_or_source_drift_rejects(lib,change):
    a=Adapter(lib)
    def mutate(a):
        if change=='inventory': a.inventory_generation=1
        elif change=='wallet': a.view['wallet'].update(available=6942424,held=1009729)
        elif change=='pending': a.view['text_pending'][0]['private']='changed'
        elif change=='file': a.view['files']['3'*64]=b'changed'
        elif change=='binding': a.value['central_config_sha256']='0'*64
        else: a.value['source_processes']['api']['mountinfo'] += '9 1 8:1 / /unrelated ro - ext4 /dev/sda1 rw\n'
    a.change=mutate
    with pytest.raises(lib.Rejected): capture(lib,a)


@pytest.mark.parametrize('change',['wheel','migration','source_pin','third_model','disabled_model','media_pending','actual','missing','extra_file','wallet_bool'])
def test_fixed_contracts_and_known_media_scope_fail_closed(lib,change):
    a=Adapter(lib)
    if change=='wheel':a.value['candidate_sha256']='0'*64
    elif change=='migration':a.value['migrations'][0]['checksum']='0'*64
    elif change=='source_pin':a.value['source_pins']['core']='0'*64
    elif change=='third_model':a.view['media_state']['models'].append({'model_id':'unknown','enabled':True})
    elif change=='disabled_model':a.view['media_state']['models'][0]['enabled']=False
    elif change=='media_pending':a.view['media_state']['pending_jobs']=2
    elif change=='actual':a.view['media_state']['pending_exception']['reservation']['actual']=0
    elif change=='missing':a.view['missing']['storage_key']='0'*64
    elif change=='extra_file':a.view['files']['9'*64]=b'unknown'
    else:a.view['wallet']['held']=True
    with pytest.raises(lib.Rejected): capture(lib,a)


@pytest.mark.parametrize('value',[{}, {'tables':{}}, {'n':True}, {'n':-1}, {'sha256':'bad'}])
def test_equal_partial_or_malformed_inventory_never_means_complete(lib,value):
    with pytest.raises(lib.Rejected): lib.validate_inventory(value)


def test_source_sandbox_is_strict_and_host_is_separate(lib):
    a=Adapter(lib); before=deepcopy(a.value)
    after=deepcopy(before);after['host_surface']['mountinfo'] += '9 1 8:1 / /new ro - ext4 /dev/sda1 rw\n'
    lib.assert_same_source_sandbox(before,after)
    after['source_processes']['api']['starttime'] += 1
    with pytest.raises(lib.Rejected):lib.assert_same_source_sandbox(before,after)


@pytest.mark.parametrize('raw',[b'{"x":1,"x":2}',b'{"x":NaN}',b'{"x":1.0}',b'[]',b'{"x":"\xff"}'])
def test_json_decoder_rejects_duplicates_nan_floats_and_invalid_utf8(lib,raw):
    with pytest.raises(lib.Rejected):lib.decode_canonical(raw)


def test_safe_allowlist_omits_nested_private_values(lib):
    f=capture(lib,Adapter(lib)); safe=lib.safe_summary(f)
    raw=json.dumps(safe)
    assert SENTINEL not in raw and 'rolpassword' not in raw and 'source_processes' not in raw
    assert set(safe)=={'format','freeze_sha256','window_id','approved','paused_media_guard_executed',
                       'inventory_sha256','binding_sha256','observations_sha256','table_count','media_jobs','text_pending_count'}


@pytest.fixture
def private_fs(lib,tmp_path,monkeypatch):
    # Only substitute the OS owner identity in synthetic temp files; production is root=0.
    monkeypatch.setattr(lib,'ROOT_UID',os.geteuid())
    tmp_path.chmod(0o700)
    return tmp_path


def test_private_roundtrip_is_exclusive_canonical_and_durable(lib,private_fs,monkeypatch):
    f=capture(lib,Adapter(lib)); calls=[]; original=lib.os.fsync
    monkeypatch.setattr(lib.os,'fsync',lambda fd:(calls.append(fd),original(fd))[1])
    receipt=lib.save_private_freeze(private_fs,f); p=Path(receipt['path'])
    assert p.stat().st_mode&0o777==0o600 and p.stat().st_nlink==1
    assert p.read_bytes()==lib.canonical(f) and len(calls)>=2
    assert lib.load_private_freeze(p,receipt['sha256'],lib.reviewed_contracts())==f
    with pytest.raises(lib.Rejected):lib.save_private_freeze(private_fs,f)


@pytest.mark.parametrize('kind',['directory_mode','file_mode','symlink','hardlink','bad_sha','tampered_pin','noncanonical'])
def test_private_paths_hashes_and_authority_are_fail_closed(lib,private_fs,kind):
    f=capture(lib,Adapter(lib));r=lib.save_private_freeze(private_fs,f);p=Path(r['path']);sha=r['sha256']
    if kind=='directory_mode':private_fs.chmod(0o755)
    elif kind=='file_mode':p.chmod(0o644)
    elif kind=='symlink':
        alias=private_fs/'alias';alias.symlink_to(p);p=alias
    elif kind=='hardlink':os.link(p,private_fs/'linked')
    elif kind=='bad_sha':sha='0'*64
    elif kind=='tampered_pin':
        f['source_pins']['core']='0'*64;p.write_bytes(lib.canonical(f));sha=lib.digest(p.read_bytes())
    else:p.write_bytes(json.dumps(f,indent=2).encode());sha=lib.digest(p.read_bytes())
    with pytest.raises(lib.Rejected):lib.load_private_freeze(p,sha,lib.reviewed_contracts())


def test_fsync_failure_does_not_emit_success_or_allow_overwrite(lib,private_fs,monkeypatch):
    f=capture(lib,Adapter(lib))
    monkeypatch.setattr(lib.os,'fsync',lambda fd:(_ for _ in ()).throw(OSError('private-synthetic-error')))
    with pytest.raises(lib.Rejected,match='freeze_rejected'):lib.save_private_freeze(private_fs,f)
    with pytest.raises(lib.Rejected):lib.save_private_freeze(private_fs,f)


def test_media_inventory_retains_existing_capture_blobs_metadata(lib):
    a=Adapter(lib);f=capture(lib,a)
    assert f['observations']['media_inventory']==a.view['media_inventory']
    assert f['observations']['media_inventory']['files'][0]['uid']==997


@pytest.mark.parametrize('field,value',[('uid',0),('mode',0o755),('missing_staged_count',0),('registry_sha256','0'*64)])
def test_media_inventory_must_match_registry_files_and_known_absence(lib,field,value):
    a=Adapter(lib);a.view['media_inventory'][field]=value
    with pytest.raises(lib.Rejected):capture(lib,a)


@pytest.mark.parametrize('change',['row_bool','row_bytes','row_sha','role_partial','database_partial','sequence','extra_top','money_bool'])
def test_full_inventory_closed_shape_rejects_malformed_equal_values(lib,change):
    f=capture(lib,Adapter(lib));v=deepcopy(f['inventory']);key=next(iter(v['tables']))
    if change=='row_bool':v['tables'][key]['n']=True
    elif change=='row_bytes':v['tables'][key]['bytes']=-1
    elif change=='row_sha':v['tables'][key]['sha256']='bad'
    elif change=='role_partial':v['roles'][0].pop('rolpassword')
    elif change=='database_partial':v['database'].pop('acl')
    elif change=='sequence':v['sequences']['s']=[True,False]
    elif change=='extra_top':v['ignored']={}
    else:v['money']['lots_balanced']=1
    with pytest.raises(lib.Rejected):lib.validate_inventory(v)


@pytest.mark.parametrize('change',['module_object','adapter_json','window','expires','created'])
def test_capture_rejects_untrusted_or_invalid_entry_before_commands(lib,change):
    a=Adapter(lib);contracts=a.contracts;window=WINDOW;created=CREATED;expires=EXPIRES;adapter=a
    if change=='module_object':contracts=type('Fake',(),{'core':a.contracts.core,'mixed':a.contracts.mixed})()
    elif change=='adapter_json':adapter={}
    elif change=='window':window='../invalid'
    elif change=='expires':expires='2026-10-28T00:15:01+00:00'
    else:created='2026-10-28T00:00:00'  # no UTC offset
    with pytest.raises(lib.Rejected):lib.capture_freeze(adapter,contracts,window_id=window,created_utc=created,expires_utc=expires)
    assert a.calls==[]


def test_paused_guard_never_runs_on_an_enabled_or_projected_state(lib,monkeypatch):
    a=Adapter(lib)
    monkeypatch.setattr(a.contracts.mixed,'mixed_window',lambda *a:pytest.fail('paused guard cannot run on projected data'))
    f=capture(lib,a)
    assert all(row['enabled']is True for row in f['observations']['media_state']['models'])
    assert f['approval']['paused_media_guard_executed']is False


def test_missing_actual_is_not_equivalent_to_unknown_actual(lib):
    a=Adapter(lib);a.view['text_pending'][0].pop('actual')
    with pytest.raises(lib.Rejected):capture(lib,a)


def test_missing_descriptor_cannot_disagree_with_its_original_staged_row(lib):
    a=Adapter(lib);a.view['missing']['sha256']='9'*64
    a.view['registry'][-1]['sha256']='9'*64
    a.view['media_inventory']['missing_staged']=deepcopy(a.view['missing'])
    a.view['media_inventory']['registry_sha256']=lib.digest(lib.canonical(a.view['registry']))
    with pytest.raises(lib.Rejected):capture(lib,a)


@pytest.mark.parametrize('change',['central_sha','api_dropin'])
def test_binding_hashes_reference_the_same_exact_configurations(lib,change):
    a=Adapter(lib)
    if change=='central_sha':a.value['central_config_sha256']='0'*64
    else:a.value['configurations'][str(a.contracts.mixed.DROPINS['api'])]['sha256']='0'*64
    with pytest.raises(lib.Rejected):capture(lib,a)


def test_source_container_and_backup_identity_are_part_of_the_frozen_binding(lib):
    a=Adapter(lib);f=capture(lib,a)
    assert f['binding']['production_containers']==a.value['production_containers']
    assert f['binding']['backup_definitions']==a.value['backup_definitions']


@pytest.mark.parametrize('change',['container_id','backup_sha','service_pid','pg_image'])
def test_original_control_plane_identity_is_strict(lib,change):
    a=Adapter(lib)
    if change=='container_id':a.change=lambda x:x.value['production_containers']['easel-central-pg-20261024'].update(id='7'*64)
    elif change=='backup_sha':a.value['backup_definitions'][str(a.contracts.core.BASE)]='0'*64
    elif change=='service_pid':a.value['original_services']['easel-central-production']['MainPID']='999'
    else:a.value['production_containers']['easel-central-pg-20261024']['image']='sha256:'+'0'*64
    with pytest.raises(lib.Rejected):capture(lib,a)


def test_safe_projection_rejects_malformed_nested_data_without_raw_errors(lib):
    f=capture(lib,Adapter(lib));f['observations']['media_state']['pending_exception']={}
    with pytest.raises(lib.Rejected,match='^freeze_rejected$'):lib.safe_summary(f)


def test_source_comparison_rejects_absent_surface(lib):
    with pytest.raises(lib.Rejected,match='^freeze_rejected$'):lib.assert_same_source_sandbox({}, {})


MEDIA_COUNTERS=tuple(key for key,value in existing_fixture().mixed_state().items() if type(value)is int)
PENDING_GROUPS=('job','attempt','reference','association','blob','budget','reservation','invocation')
PENDING_COLUMNS=tuple((group,key) for group,row in complete_pending_rows(existing_fixture().mixed_state())['pending_exception'].items() for key in row)


def rebind_missing(a):
    pending=a.view['media_state']['pending_exception']
    a.view['missing']['staged_row_sha256']=a.lib.digest(a.lib.canonical(pending['blob']))
    a.view['missing']['http_reference_sha256']=a.lib.digest(a.lib.canonical(pending['reference']))
    a.view['media_inventory']['missing_staged']=deepcopy(a.view['missing'])


@pytest.mark.parametrize('field',MEDIA_COUNTERS)
@pytest.mark.parametrize('case',['missing','wrong'])
def test_every_original_population_counter_is_required_and_exact(lib,field,case):
    a=Adapter(lib)
    if case=='missing':a.view['media_state'].pop(field)
    else:a.view['media_state'][field]+=1
    with pytest.raises(lib.Rejected):capture(lib,a)


@pytest.mark.parametrize('group',PENDING_GROUPS)
def test_all_original_pending_groups_are_required(lib,group):
    a=Adapter(lib);a.view['media_state']['pending_exception'].pop(group)
    with pytest.raises(lib.Rejected):capture(lib,a)


@pytest.mark.parametrize('group,key',PENDING_COLUMNS)
def test_complete_pending_row_columns_cannot_be_silently_omitted(lib,group,key):
    a=Adapter(lib);a.view['media_state']['pending_exception'][group].pop(key)
    rebind_missing(a)
    with pytest.raises(lib.Rejected):capture(lib,a)


@pytest.mark.parametrize('place',['state','groups','job','attempt','reference','association','blob','budget','reservation','invocation'])
def test_media_observation_schema_rejects_unreviewed_keys(lib,place):
    a=Adapter(lib);state=a.view['media_state']
    target=state if place=='state' else state['pending_exception'] if place=='groups' else state['pending_exception'][place]
    target['unreviewed']=SENTINEL
    rebind_missing(a)
    with pytest.raises(lib.Rejected):capture(lib,a)


@pytest.mark.parametrize('group,key,value',[
    ('job','capability','video.generate'),('job','model','unreviewed'),('job','funding_mode','test'),
    ('job','execution_state','completed'),('job','delivery_state','available'),('job','reason','waived'),
    ('job','request_hash','0'*64),('attempt','attempt_id','foreign'),('attempt','job_id','foreign'),
    ('attempt','binding_hash','0'*64),('attempt','provider_scope_hash','0'*64),('attempt','provider_request_hash','bad'),
    ('attempt','upstream_model','unreviewed'),('attempt','request_id',''),
    ('reference','namespace','object_id'),('reference','http_request_id','unknown'),('reference','response_body_sha256','bad'),
    ('association','http_request_id','foreign'),('association','http_status',500),('association','provider_job_id','invented'),
    ('blob','invocation_id','foreign'),('blob','properties',{'width':1,'height':1,'format':'PNG'}),('blob','attached_id','invented'),
    ('blob','user_id','foreign'),('budget','job_id','foreign'),('budget','max_bytes',1),('budget','max_files',2),
    ('budget','remaining_files',1),('budget','remaining_bytes',0),('budget','released_at','2026-10-27T01:00:00+00:00'),
    ('reservation','body_hash','0'*64),('reservation','idempotency_key','foreign'),('reservation','operation','execution'),
    ('reservation','product_id','foreign'),('invocation','operation_count',True),('invocation','held',0),
    ('invocation','spent',1),('invocation','session_id','foreign'),('invocation','product_id','foreign'),
])
def test_full_pending_evidence_budget_and_owner_bindings_are_not_relaxed(lib,group,key,value):
    a=Adapter(lib);a.view['media_state']['pending_exception'][group][key]=value
    rebind_missing(a)
    with pytest.raises(lib.Rejected):capture(lib,a)


@pytest.mark.parametrize('group,key,value',[
    ('job','user_id','foreign'),('budget','tenant_id','foreign'),('blob','billing_account_id','foreign'),
    ('invocation','session_id','foreign'),('job','authorization_epoch',True),('job','unreviewed_actor','foreign')])
def test_scope_and_current_actor_metadata_must_be_consistent(lib,group,key,value):
    a=Adapter(lib);a.view['media_state']['pending_exception'][group]['scope'][key]=value
    rebind_missing(a)
    with pytest.raises(lib.Rejected):capture(lib,a)


def test_enabled_validator_preserves_actual_observation_bytes(lib,monkeypatch):
    a=Adapter(lib);before=lib.canonical(a.view['media_state'])
    monkeypatch.setattr(a.contracts.mixed,'mixed_window',lambda *_:pytest.fail('paused guard must not be called'))
    lib.validate_enabled_media_state(a.view['media_state'],a.contracts)
    assert lib.canonical(a.view['media_state'])==before
    assert all(row['enabled']is True for row in a.view['media_state']['models'])


def test_private_writer_requires_the_current_actor_not_just_file_owner(lib,private_fs,monkeypatch):
    f=capture(lib,Adapter(lib));actor=lib.ROOT_UID
    monkeypatch.setattr(lib.os,'geteuid',lambda:actor+1)
    with pytest.raises(lib.Rejected):lib.save_private_freeze(private_fs,f)
    assert list(private_fs.iterdir())==[]


@pytest.mark.parametrize('group,key,value',[
    ('attempt','created_at',None),('budget','created_at','not-a-timestamp'),
    ('job','dispatched_at',None),('job','created_at','2026-10-27T01:00:00'),
    ('job','cancel_requested_at','2026-10-27T01:00:00'),('invocation','expires_at',None),
    ('reservation','rate_version',''),('reservation','completion_key','completed'),('reservation','created_xid',True),
    ('job','model_definition',{'id':'other','capability':'image.generate','test_only':False,'limits':{'max_assets':0}}),
    ('job','model_definition',{'id':'gpt-image-2.5-flare','capability':'image.generate','test_only':True,'limits':{'max_assets':0}}),
    ('job','request',{'assets':[],'parameters':{'n':True,'size':'1024x1024'}}),
])
def test_full_metadata_values_are_not_treated_as_opaque_success_flags(lib,group,key,value):
    a=Adapter(lib);a.view['media_state']['pending_exception'][group][key]=value
    rebind_missing(a)
    with pytest.raises(lib.Rejected):capture(lib,a)


def test_actual_paused_guard_still_rejects_true_and_is_never_substituted(lib):
    a=Adapter(lib);state=a.view['media_state'];before=lib.canonical(state)
    lib.validate_enabled_media_state(state,a.contracts)
    with pytest.raises(a.contracts.mixed.Rejected):a.contracts.mixed.mixed_window(state)
    assert lib.canonical(state)==before


def test_all_25_population_facts_match_the_pinned_original_contract(lib):
    path=SCRIPT.with_name('verify_mixed_media_restore_drill.py')
    tree=ast.parse(path.read_text())
    function=next(node for node in tree.body if isinstance(node,ast.FunctionDef)and node.name=='mixed_window')
    original=ast.literal_eval(function.body[0].value)
    assert lib.ENABLED_MEDIA_COUNTS==original and len(original)==25


def test_private_reader_checks_current_actor_before_opening_any_file(lib,private_fs,monkeypatch):
    f=capture(lib,Adapter(lib));r=lib.save_private_freeze(private_fs,f);actor=lib.ROOT_UID;contracts=lib.reviewed_contracts()
    monkeypatch.setattr(lib.os,'geteuid',lambda:actor+1)
    monkeypatch.setattr(lib.os,'open',lambda *a,**k:pytest.fail('wrong actor must reject before open'))
    with pytest.raises(lib.Rejected):lib.load_private_freeze(r['path'],r['sha256'],contracts)
