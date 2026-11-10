"""Unapproved, pre-pause F contract; no production CLI or maintenance actions.

The adapter is reviewed Python code, never request JSON. It owns the read-only
exported snapshot lifetime and verified source/filesystem observations. This
module delegates the *whole* database inventory to the pinned v6 collector.
It does not prove an arbitrary adapter is read-only, authorize a window, run
the paused media guard, or replace the W/P/D controller and DR integration.

The future production adapter must compute the raw lot aggregate and complete
funded/unresolved text-reservation projection in that same read-only snapshot.
CreditLedger.summary is not suitable: it runs expiry writes. Historical resolved
jobs remain covered by the unfiltered inventory, not counted as outstanding holds.
No production adapter or caller-selectable source/module path is provided here.
"""
from copy import deepcopy
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import types

ROOT_UID = 0
TARGET = 'ser362540173134'
FORMAT = 'easel-media-restore-freeze-v1'
MAXIMUM = 4 * 1024 * 1024
MIGRATIONS_SHA256 = '9f590a873a540098b5704df5ecf4efeab7b9c966bcabd95bdc01d5f1293c55b6'
SOURCE_PINS = {
    'core':'4d3c4210469d6630dd4cbc71d8d5327e62aa972eedb7084685bb0df85a2c88ea',
    'mixed':'34df13489773a3e3bf8c28601e634546d494012923dde54b0bd76e792581271e',
    'wrapper':'c5ee12bd34b5b48a09b002382bfc9c1762a8cd6d32aa4d343d6709840b931d36',
    'old_window':'5e100195b83ce186833522112c09ce007f6b46ecdc5c81a2a269c2fd569b1287',
    'preparation':'ff9d019ef275798b24e12adcc25c8018c9f8f3ff244dc003133f2df4912c02ef',
}
SOURCE_FILES = {
    'core':'verify_encrypted_restore_drill.py', 'mixed':'verify_mixed_media_restore_drill.py',
    'wrapper':'capture_mixed_media_restore_plan.py', 'old_window':'media_restore_window.py',
    'preparation':'prepare_m1_image_release.py',
}
INVENTORY_KEYS = frozenset(('bootstrap_role','schema','tables','sequences','roles','memberships',
                           'database','extensions','constraints','settings','money'))
ENABLED_MEDIA_COUNTS = {
    'invocations':3,'jobs':3,'artifacts':2,'assets':0,'blobs':3,'budgets':3,
    'settled_completed_jobs':2,'valid_invocations':2,'released_budgets':2,'valid_png_artifacts':2,
    'valid_attached_blobs':2,'typed_terminal_evidence':2,'typed_receipts':2,'http_references':3,
    'attempts':3,'http_associations':3,'owners':1,'active_jobs':0,'pinned_blobs':1,'gc_claims':0,
    'pending_jobs':1,'valid_pending_jobs':1,'valid_pending_invocations':1,'unreleased_budgets':1,'valid_staged_blobs':1,
}
# Complete to_jsonb projections for the fixed f50/001–016 source. These are
# observation schemas, not a second data inventory or a rewritten paused guard.
PENDING_FIELDS = {
    'job':frozenset('actual amount attempt_id billing_account_id binding_hash cancel_requested_at capability created_at delivery_state dispatched_at execution_state funding_mode id input_units invocation_id lease_token lease_until model model_definition operation_key product_id reason request request_hash reservation_id scope status tenant_id updated_at user_id'.split()),
    'attempt':frozenset('attempt_id binding_hash created_at job_id provider_request_hash provider_scope_hash request_id upstream_model'.split()),
    'reference':frozenset('attempt_id binding_hash created_at http_request_id job_id namespace provider_scope_hash response_body_sha256'.split()),
    'association':frozenset('attempt_id binding_hash created_at http_request_id http_status job_id kind provider_job_id provider_response_id provider_scope_hash'.split()),
    'blob':frozenset('attached_id attempt_id billing_account_id created_at deleted_at expires_at gc_lease_until gc_token invocation_id job_id kind mime properties purged_at scope sha256 size state storage_key tenant_id user_id'.split()),
    'budget':frozenset('billing_account_id created_at job_id max_bytes max_files released_at remaining_bytes remaining_files scope tenant_id user_id'.split()),
    'reservation':frozenset('actual amount billing_account_id body_hash completion_key created_at created_xid funding_mode id idempotency_key operation product_id rate_version status tenant_id updated_at user_id'.split()),
    'invocation':frozenset('billing_account_id capability created_at expires_at funding_mode held id invocation_key model operation_count product_id scope session_id spent tenant_id user_id'.split()),
}
_TOKEN = object()


class Rejected(Exception):
    pass


def require(value):
    if not value:
        raise Rejected('freeze_rejected')


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _sha(value):
    require(type(value) is str and re.fullmatch('[a-f0-9]{64}',value) is not None)


def _integer(value, minimum=0):
    require(type(value) is int and minimum <= value <= 9223372036854775807)


def _json_value(value, depth=0):
    require(depth <= 32)
    if value is None or type(value) is bool:
        return
    if type(value) is int:
        require(-9223372036854775808 <= value <= 9223372036854775807)
    elif type(value) is str:
        try:
            value.encode('utf-8')
        except UnicodeError:
            raise Rejected('freeze_rejected') from None
    elif type(value) is list:
        for item in value:
            _json_value(item,depth+1)
    elif type(value) is dict:
        for key,item in value.items():
            require(type(key) is str)
            _json_value(key,depth+1)
            _json_value(item,depth+1)
    else:
        raise Rejected('freeze_rejected')


def _source_bytes(path, expected):
    fd = os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_nlink==1
                and not stat.S_IMODE(before.st_mode)&0o022 and before.st_size<=256*1024)
        raw = bytearray()
        while True:
            part = os.read(fd,65536)
            if not part:
                break
            raw.extend(part)
            require(len(raw)<=256*1024)
        require(_signature(before)==_signature(os.fstat(fd))==_signature(Path(path).lstat()))
        require(digest(raw)==expected)
        return bytes(raw)
    finally:
        os.close(fd)


class ReviewedContracts:
    """Closed source factory, not a configurable module/path import surface."""
    def __init__(self, token, core, mixed):
        require(token is _TOKEN)
        self.core,self.mixed,self._token = core,mixed,token


def _checked_sources():
    directory = Path(__file__).parent
    return {key:_source_bytes(directory/name,SOURCE_PINS[key]) for key,name in SOURCE_FILES.items()}


@lru_cache(maxsize=1)
def _contracts():
    sources = _checked_sources()
    loaded = {}
    for key in ('core','mixed'):
        module = types.ModuleType('fixed_freeze_'+key)
        module.__file__ = str(Path(__file__).with_name(SOURCE_FILES[key]))
        exec(compile(sources[key],module.__file__,'exec'),module.__dict__)
        loaded[key] = module
    return ReviewedContracts(_TOKEN,loaded['core'],loaded['mixed'])


def reviewed_contracts():
    _checked_sources()
    return _contracts()


def canonical(value):
    _json_value(value)
    try:
        raw = _contracts().core.canonical(value)
    except (ValueError,TypeError,UnicodeError,RecursionError):
        raise Rejected('freeze_rejected') from None
    require(len(raw)<=MAXIMUM)
    return raw


def _unique(pairs):
    result = {}
    for key,value in pairs:
        require(key not in result)
        result[key] = value
    return result


def decode_canonical(raw):
    require(type(raw) is bytes and len(raw)<=MAXIMUM)
    try:
        value = json.loads(raw.decode('utf-8'),object_pairs_hook=_unique,
                           parse_constant=lambda _:require(False))
        require(type(value) is dict and canonical(value)==raw)
        return value
    except (ValueError,TypeError,UnicodeError,RecursionError):
        raise Rejected('freeze_rejected') from None


def validate_inventory(value):
    """v6 equality is reused after schema validation, never as completeness proof."""
    require(type(value) is dict and set(value)==INVENTORY_KEYS)
    canonical(value)
    require(type(value['bootstrap_role']) is str and bool(value['bootstrap_role']))
    _sha(value['schema'])
    require(type(value['tables']) is dict and bool(value['tables'])
            and type(value['sequences']) is dict and len(value['tables'])+len(value['sequences'])<=2048)
    rows = size = 0
    for name,row in value['tables'].items():
        require(type(name) is str and bool(name) and type(row) is dict and set(row)=={'n','bytes','sha256'})
        _integer(row['n']); _integer(row['bytes']); _sha(row['sha256'])
        rows += row['n']; size += row['bytes']
    require(rows<=100000 and size<=128*1024*1024)
    for name,row in value['sequences'].items():
        require(type(name) is str and bool(name) and type(row) is list and len(row)==2
                and type(row[0]) is int and type(row[1]) is bool)
    for key in ('roles','memberships','extensions','constraints','settings'):
        require(type(value[key]) is list and all(type(row) is dict for row in value[key]))
    require(bool(value['roles']) and type(value['database']) is dict and bool(value['database']))
    # Closed shapes mirror the pinned SELECT projections; no fields are removed
    # from the returned inventory and all values still participate in equality.
    shapes = {
        'roles':{'rolname','rolsuper','rolinherit','rolcreaterole','rolcreatedb','rolcanlogin',
                 'rolreplication','rolconnlimit','rolvaliduntil','rolbypassrls','rolpassword'},
        'memberships':{'role','member','grantor','admin_option','inherit_option','set_option'},
        'extensions':{'extname','extversion','owner','schema','extrelocatable','config_tables','extcondition'},
        'constraints':{'schema','relation','domain','name','kind','convalidated','conislocal',
                       'coninhcount','connoinherit','condeferrable','condeferred','definition'},
        'settings':{'database','role','setconfig'},
    }
    for key,keys in shapes.items():
        require(all(set(row)==keys for row in value[key]))
    require(set(value['database'])=={'datname','owner','encoding','datcollate','datctype','datlocprovider',
            'daticulocale','datcollversion','datconnlimit','datallowconn','datistemplate','acl'})
    require(type(value['database']['acl']) is list and all(type(row) is dict and
            set(row)=={'grantor','grantee','privilege_type','is_grantable'} for row in value['database']['acl']))
    require(value['money']=={'lots_balanced':True,'journals_balanced':True}
            and all(type(v) is bool for v in value['money'].values()))


def _validate_binding(value, contracts):
    keys={'target','boot_id','release','candidate_sha256','source_pins','migrations',
          'central_schema_sha256','central_config_sha256','configurations','source_processes','host_surface',
          'production_containers','original_services','backup_definitions'}
    require(type(value) is dict and set(value)==keys)
    canonical(value)
    m = contracts.mixed
    require(value['target']==TARGET and value['release']==m.RELEASE and value['candidate_sha256']==m.WHEEL_SHA
            and value['source_pins']==SOURCE_PINS and re.fullmatch('[a-f0-9]{32}',value['boot_id']) is not None)
    require(type(value['migrations']) is list and len(value['migrations'])==16
            and digest(canonical(value['migrations']))==MIGRATIONS_SHA256)
    _sha(value['central_schema_sha256']); _sha(value['central_config_sha256'])
    configs = value['configurations']
    require(type(configs) is dict and set(configs)==set(m.SOURCE_FILES))
    for name,row in configs.items():
        require(type(row) is dict and set(row)=={'sha256','uid','gid','mode','size'})
        _sha(row['sha256']); _integer(row['size'],1)
        app_owned = name in (str(m.MEDIA),'/etc/easel-central/execution.json')
        require(type(row['uid']) is int and row['uid']==(997 if app_owned else 0)
                and type(row['gid']) is int and row['gid'] in ({0,997} if app_owned else {0})
                and type(row['mode']) is int and row['mode'] in ({0o600,0o644} if name in map(str,m.MAIN_UNITS.values()) else {0o600}))
    require(configs['/etc/easel-central/central.env']['sha256']==value['central_config_sha256']
            and configs[str(m.DROPINS['api'])]['sha256']==m.API_DROPIN_SHA)
    containers = value['production_containers']
    require(type(containers) is dict and set(containers)=={
        'easel-central-pg-20261024','easel-identity-pg-v2-20261024','easel-logto-v2-20261024'})
    for name,row in containers.items():
        require(type(row) is dict and set(row)=={'id','image','started_at'})
        _sha(row['id'])
        require(type(row['image']) is str and re.fullmatch('sha256:[a-f0-9]{64}',row['image']) is not None
                and type(row['started_at']) is str and 0<len(row['started_at'])<=128)
        if name in {source[1] for source in contracts.core.SOURCES}:
            require(row['image']==contracts.core.IMAGE.split('@',1)[1])
    services = value['original_services']
    require(type(services) is dict and set(services)=={'easel-central-production',
            'easel-console-production','easel-gateway-tunnel','nginx'})
    for row in services.values():
        require(type(row) is dict and set(row)=={'MainPID','ActiveState','ActiveEnterTimestampMonotonic'}
                and row['ActiveState']=='active' and type(row['MainPID']) is str
                and re.fullmatch('[0-9]{1,12}',row['MainPID']) is not None and int(row['MainPID'])>1
                and type(row['ActiveEnterTimestampMonotonic']) is str
                and re.fullmatch('[0-9]{1,20}',row['ActiveEnterTimestampMonotonic']) is not None)
    definitions = value['backup_definitions']
    require(type(definitions) is dict and set(definitions)=={str(contracts.core.BASE),
        '/etc/systemd/system/easel-encrypted-backup.service','/etc/systemd/system/easel-encrypted-backup.timer'}
        and definitions[str(contracts.core.BASE)]==contracts.core.BASE_SHA)
    for sha in definitions.values(): _sha(sha)
    require(type(value['source_processes']) is dict and set(value['source_processes'])=={'api','console'})
    for kind,row in value['source_processes'].items():
        require(type(row) is dict and set(row)=={'pid','starttime','invocation_id','cwd','command_sha256',
                    'executable_sha256','media_path','mount_namespace','mountinfo'})
        _integer(row['pid'],2); _integer(row['starttime'],1)
        require(row['pid']==int(services['easel-central-production' if kind=='api' else 'easel-console-production']['MainPID']))
        require(re.fullmatch('[a-f0-9]{32}',row['invocation_id']) is not None and row['cwd']==m.RELEASE
                and row['media_path']==(str(m.MEDIA) if kind=='api' else None))
        _sha(row['command_sha256']); _sha(row['executable_sha256'])
        require(type(row['mountinfo']) is str)
        m.verify_mount_namespace(row['mountinfo'].encode('utf-8'),row['mount_namespace'],kind)
    host = value['host_surface']
    require(type(host) is dict and set(host)=={'mount_namespace','mountinfo'}
            and type(host['mountinfo']) is str and 0<len(host['mountinfo'].encode())<=512*1024
            and re.fullmatch(r'mnt:\[[0-9]+\]',host['mount_namespace']) is not None)


def assert_same_source_sandbox(before, after):
    """Host Docker topology is a different surface, never a normalized source view."""
    require(type(before) is dict and type(after) is dict
            and type(before.get('source_processes')) is dict and type(after.get('source_processes')) is dict
            and set(before['source_processes'])==set(after['source_processes'])=={'api','console'})
    require(canonical(before['source_processes'])==canonical(after['source_processes']))


def _identifier(value):
    require(type(value) is str and 0<len(value)<=256 and not any(ord(c)<32 for c in value))


def _timestamp(value):
    require(type(value) is str)
    try:
        parsed=datetime.fromisoformat(value)
        require(parsed.utcoffset()==timezone.utc.utcoffset(parsed))
    except (ValueError,TypeError,OverflowError):
        raise Rejected('freeze_rejected') from None


def validate_enabled_media_state(state, contracts):
    """Validate actual enabled observations; never project flags or call paused P."""
    try:
        require(type(contracts) is ReviewedContracts and contracts is reviewed_contracts())
        canonical(state)
        m=contracts.mixed
        require(type(state) is dict and set(state)==set(ENABLED_MEDIA_COUNTS)|{'models','model_jobs','history','pending_exception'}
                and state['models']==[{'model_id':name,'enabled':True} for name in m.MODELS]
                and all(type(row['enabled']) is bool for row in state['models'])
                and state['model_jobs']==[{'model':m.MODELS[0],'jobs':2},{'model':m.MODELS[1],'jobs':1}]
                and all(type(row['jobs']) is int for row in state['model_jobs']))
        require(all(type(state[key]) is int and state[key]==value for key,value in ENABLED_MEDIA_COUNTS.items()))
        history=[{'id':key,'model':value,'status':'settled'} for key,value in m.SETTLED_JOBS.items()]
        history.append({'id':m.PENDING_JOB,'model':m.MODELS[0],'status':'pending_reconciliation'})
        require(state['history']==sorted(history,key=lambda row:row['id']))
        e=state['pending_exception']
        require(type(e) is dict and set(e)==set(PENDING_FIELDS))
        for key,fields in PENDING_FIELDS.items():
            require(type(e[key]) is dict and set(e[key])==fields)
            _timestamp(e[key]['created_at'])
        j,a,h,c,b,q,r,i=(e[key] for key in ('job','attempt','reference','association','blob','budget','reservation','invocation'))
        require(j['id']==m.PENDING_JOB and j['model']==m.MODELS[0] and j['capability']=='image.generate' and j['funding_mode']=='live'
                and (j['status'],j['execution_state'],j['delivery_state'],j['reason'])==
                    ('pending_reconciliation','unknown','processing','upstream_uncertain')
                and type(j['amount']) is int and j['amount']==1000000 and j['actual'] is None)
        for key in ('binding_hash','request_hash'): _sha(j[key])
        for key in ('attempt_id','invocation_id','reservation_id','operation_key'): _identifier(j[key])
        _timestamp(j['dispatched_at']); _timestamp(j['lease_until']); _timestamp(j['updated_at'])
        if j['cancel_requested_at'] is not None: _timestamp(j['cancel_requested_at'])
        if j['lease_token'] is not None: _identifier(j['lease_token'])
        require(type(j['request']) is dict and j['request'].get('assets')==[]
                and j['request'].get('parameters')=={'n':1,'size':'1024x1024'}
                and type(j['request']['parameters']['n']) is int and type(j['input_units']) is dict)
        definition=j['model_definition']
        require(type(definition) is dict and definition.get('id')==j['model']
                and definition.get('capability')=='image.generate' and definition.get('test_only') is False
                and type(definition.get('limits')) is dict and type(definition['limits'].get('max_assets')) is int
                and definition['limits']['max_assets']==0)
        scope=j['scope']
        require(type(scope) is dict and set(scope)=={'user_id','tenant_id','billing_account_id','product_id','session_id','authorization_epoch'})
        for key in ('user_id','tenant_id','billing_account_id','product_id','session_id'): _identifier(scope[key])
        _integer(scope['authorization_epoch'])
        require(all(j[key]==scope[key] for key in ('user_id','tenant_id','billing_account_id','product_id')))
        require(all(row['job_id']==j['id'] and row['attempt_id']==j['attempt_id'] for row in (a,h,c,b))
                and all(row['binding_hash']==j['binding_hash'] for row in (a,h,c))
                and h['provider_scope_hash']==a['provider_scope_hash']==c['provider_scope_hash']
                and a['upstream_model']==j['model'])
        _sha(a['provider_scope_hash']); _sha(a['provider_request_hash']); _identifier(a['request_id'])
        require(h['namespace']=='newapi_x_oneapi_request_id_v1'
                and type(h['http_request_id']) is str and re.fullmatch('[0-9]{23}[A-Za-z0-9]{16}',h['http_request_id']) is not None
                and c['kind']=='http' and type(c['http_status']) is int and c['http_status']==200
                and c['http_request_id']==h['http_request_id'] and c['provider_response_id'] is None and c['provider_job_id'] is None)
        _sha(h['response_body_sha256'])
        require(b['invocation_id']==j['invocation_id'] and b['kind']=='artifact' and b['state']=='staged' and b['mime']=='image/png'
                and b['properties']=={'width':1024,'height':1024,'format':'PNG'}
                and type(b['properties']['width']) is int and type(b['properties']['height']) is int
                and all(b[key] is None for key in ('attached_id','purged_at','deleted_at','gc_token','gc_lease_until')))
        m.blob_index([{key:b[key] for key in ('storage_key','sha256','size','state')}])
        _timestamp(b['expires_at'])
        for key in ('max_bytes','max_files','remaining_files','remaining_bytes'): _integer(q[key])
        require(q['job_id']==j['id'] and q['max_bytes']==20971520 and q['max_files']==1
                and q['remaining_files']==0 and q['remaining_bytes']==q['max_bytes']-b['size'] and q['released_at'] is None)
        require(r['id']==j['reservation_id'] and r['status']=='pending_reconciliation' and r['actual'] is None
                and type(r['amount']) is int and r['amount']==1000000 and r['funding_mode']=='live'
                and r['operation']=='media' and r['idempotency_key']==j['id'] and r['body_hash']==j['request_hash']
                and r['product_id']==scope['product_id'] and r['completion_key'] is None)
        _identifier(r['rate_version']); _timestamp(r['updated_at'])
        require((type(r['created_xid']) is int and r['created_xid']>=0)
                or (type(r['created_xid']) is str and re.fullmatch('[0-9]{1,20}',r['created_xid']) is not None))
        for key in ('operation_count','held','spent'): _integer(i[key])
        require(i['id']==j['invocation_id'] and (i['operation_count'],i['held'],i['spent'])==(1,1000000,0)
                and i['funding_mode']=='live' and i['model']==j['model'] and i['capability']=='image.generate'
                and i['product_id']==scope['product_id'] and i['session_id']==scope['session_id'])
        _identifier(i['invocation_key']); _timestamp(i['expires_at'])
        require(all(row[key]==j[key] for row in (b,q,i) for key in ('scope','user_id','tenant_id','billing_account_id'))
                and all(r[key]==j[key] for key in ('user_id','tenant_id','billing_account_id')))
    except Rejected:
        raise
    except Exception:
        raise Rejected('freeze_rejected') from None


def _observations(value, contracts, *, raw_files):
    require(type(value) is dict and set(value)=={'wallet','media_state','text_pending','registry','files','missing','media_inventory'})
    m = contracts.mixed
    wallet = value['wallet']
    require(type(wallet) is dict and set(wallet)=={'issued','available','held','spent','expired','refunded'})
    for amount in wallet.values(): _integer(amount)
    require(wallet['issued']==sum(wallet[k] for k in ('available','held','spent','expired','refunded')))
    state = value['media_state']
    validate_enabled_media_state(state,contracts)
    pending = state['pending_exception']; job = pending['job']
    m.validate_missing(value['missing'])
    require(value['missing']['storage_key']==pending['blob']['storage_key']
            and all(value['missing'][key]==pending['blob'][key] for key in ('sha256','size','state'))
            and value['missing']['binding_hash']==job['binding_hash']
            and value['missing']['attempt_id']==job['attempt_id']
            and value['missing']['staged_row_sha256']==digest(canonical(pending['blob']))
            and value['missing']['http_reference_sha256']==digest(canonical(pending['reference'])))
    index = m.blob_index(value['registry'])
    attached = {k:r for k,r in index.items() if r['state']=='attached'}
    staged = [r for r in index.values() if r['state']=='staged']
    require(len(index)==3 and len(attached)==2 and staged==[
        {k:value['missing'][k] for k in ('storage_key','sha256','size','state')}])
    require(type(value['files']) is dict and set(value['files'])==set(attached))
    media_inventory = value['media_inventory']
    require(type(media_inventory) is dict and set(media_inventory)=={'version','uid','gid','mode','files',
            'blob_bytes','missing_staged','missing_staged_count','registry_sha256'})
    for key,expected in {'version':1,'uid':997,'gid':997,'mode':0o700,'missing_staged_count':1}.items():
        require(type(media_inventory[key]) is int and media_inventory[key]==expected)
    require(media_inventory['missing_staged']==value['missing']
            and media_inventory['registry_sha256']==digest(canonical(value['registry'])))
    expected_files = [{k:row[k] for k in ('storage_key','size','sha256')}|{'uid':997,'gid':997,'mode':0o600}
                      for _,row in sorted(attached.items())]
    require(type(media_inventory['files']) is list and media_inventory['files']==expected_files
            and all(type(row[key]) is int for row in media_inventory['files'] for key in ('uid','gid','mode','size'))
            and type(media_inventory['blob_bytes']) is int
            and media_inventory['blob_bytes']==sum(row['size'] for row in attached.values())<=32*1024*1024)
    normalized = deepcopy({k:v for k,v in value.items() if k!='files'})
    normalized['files'] = {}
    for key,row in attached.items():
        payload = value['files'][key]
        if raw_files:
            require(type(payload) is bytes and len(payload)==row['size'] and digest(payload)==row['sha256'])
            normalized['files'][key] = {'sha256':digest(payload),'size':len(payload)}
        else:
            require(type(payload) is dict and payload=={k:row[k] for k in ('sha256','size')})
            normalized['files'][key] = payload
    text = value['text_pending']
    require(type(text) is list and len(text)<=100000)
    ids = set()
    for row in text:
        require(type(row) is dict and type(row.get('id')) is str and bool(row['id'])
                and {'id','status','actual','amount'}<=set(row)
                and row['id'] not in ids and row['id']!=m.PENDING_JOB
                and row.get('status')=='pending_reconciliation' and row.get('actual') is None)
        _integer(row.get('amount'),1); ids.add(row['id'])
    require(wallet['held']==job['amount']+sum(row['amount'] for row in text))
    canonical(normalized)
    return normalized


def _times(created, expires):
    try:
        start,end = datetime.fromisoformat(created),datetime.fromisoformat(expires)
        require(start.utcoffset()==end.utcoffset()==timezone.utc.utcoffset(start)
                and 0<(end-start).total_seconds()<=900)
    except (TypeError,ValueError,OverflowError):
        raise Rejected('freeze_rejected') from None


def _validate_document(freeze, contracts):
    require(type(contracts) is ReviewedContracts and contracts is reviewed_contracts())
    require(type(freeze) is dict and set(freeze)=={'format','window_id','created_utc','expires_utc',
            'tool_sha256','source_pins','binding','inventory','observations','approval'})
    canonical(freeze)
    require(freeze['format']==FORMAT and re.fullmatch('[a-f0-9]{32}',freeze['window_id']) is not None
            and freeze['tool_sha256']==digest(Path(__file__).read_bytes()) and freeze['source_pins']==SOURCE_PINS)
    _times(freeze['created_utc'],freeze['expires_utc'])
    _validate_binding(freeze['binding'],contracts)
    validate_inventory(freeze['inventory'])
    _observations(freeze['observations'],contracts,raw_files=False)
    require(freeze['approval']=={'approved':False,'actions':[],'paused_media_guard_executed':False}
            and freeze['approval']['approved'] is False and freeze['approval']['paused_media_guard_executed'] is False)


def _validate(freeze, contracts):
    try:
        _validate_document(freeze,contracts)
    except Rejected:
        raise
    except Exception:
        raise Rejected('freeze_rejected') from None


def capture_freeze(adapter, contracts, *, window_id, created_utc, expires_utc):
    """Capture through a trusted adapter; no adapter/data/module is read from CLI."""
    try:
        require(type(contracts) is ReviewedContracts and contracts is reviewed_contracts()
                and not isinstance(adapter,dict))
        require(type(window_id) is str and re.fullmatch('[a-f0-9]{32}',window_id) is not None)
        _times(created_utc,expires_utc)
        before = deepcopy(adapter.binding()); _validate_binding(before,contracts)
        source = contracts.core.SOURCES[0]
        captures = []
        for _ in range(2):
            with adapter.exported_snapshot() as snapshot:
                inventory = contracts.core.collect_inventory(adapter.command,*source[1:],snapshot)
                validate_inventory(inventory)
                observation = _observations(adapter.observations(snapshot),contracts,raw_files=True)
                final = contracts.core.collect_inventory(adapter.command,*source[1:],snapshot)
                validate_inventory(final); contracts.core.match_inventories(inventory,final)
                captures.append((inventory,observation))
        contracts.core.match_inventories(captures[0][0],captures[1][0])
        require(canonical(captures[0][1])==canonical(captures[1][1]))
        after = deepcopy(adapter.binding()); _validate_binding(after,contracts)
        assert_same_source_sandbox(before,after)
        require(canonical(before)==canonical(after))  # no target lifecycle occurs during F
        freeze = {'format':FORMAT,'window_id':window_id,'created_utc':created_utc,'expires_utc':expires_utc,
            'tool_sha256':digest(Path(__file__).read_bytes()),'source_pins':dict(SOURCE_PINS),
            'binding':before,'inventory':captures[0][0],'observations':captures[0][1],
            'approval':{'approved':False,'actions':[],'paused_media_guard_executed':False}}
        _validate(freeze,contracts)
        return freeze
    except Rejected:
        raise
    except Exception:
        # Do not leak adapter SQL/DSN/paths/source values through public errors.
        raise Rejected('freeze_rejected') from None


def safe_summary(freeze):
    _validate(freeze,reviewed_contracts())
    return {'format':FORMAT,'freeze_sha256':digest(canonical(freeze)),'window_id':freeze['window_id'],
        'approved':False,'paused_media_guard_executed':False,
        'inventory_sha256':digest(canonical(freeze['inventory'])),
        'binding_sha256':digest(canonical(freeze['binding'])),
        'observations_sha256':digest(canonical(freeze['observations'])),
        'table_count':len(freeze['inventory']['tables']),
        'media_jobs':freeze['observations']['media_state']['jobs'],
        'text_pending_count':len(freeze['observations']['text_pending'])}


def _signature(info):
    return (info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns,
            info.st_uid,info.st_gid,info.st_mode,info.st_nlink)


def _private_directory(path):
    require(os.geteuid()==ROOT_UID)
    path = Path(path)
    require(path.is_absolute() and '..' not in path.parts)
    for parent in path.parents:
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid in {0,ROOT_UID}
                and (not stat.S_IMODE(info.st_mode)&0o022 or info.st_uid==0 and info.st_mode&stat.S_ISVTX))
    fd = os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        require(stat.S_ISDIR(info.st_mode) and info.st_uid==ROOT_UID and stat.S_IMODE(info.st_mode)==0o700
                and _signature(info)==_signature(path.lstat()))
        return fd
    except BaseException:
        os.close(fd)
        raise


def _file_info(info):
    require(stat.S_ISREG(info.st_mode) and info.st_uid==ROOT_UID and stat.S_IMODE(info.st_mode)==0o600
            and info.st_nlink==1 and 0<info.st_size<=MAXIMUM)


def save_private_freeze(directory, freeze):
    try:
        _validate(freeze,reviewed_contracts())
        raw = canonical(freeze); directory = Path(directory)
        name = 'freeze-'+freeze['window_id']+'-private.json'
        parent = _private_directory(directory)
        try:
            fd = os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=parent)
            try:
                view = memoryview(raw)
                while view:
                    count = os.write(fd,view); require(count>0); view = view[count:]
                os.fchmod(fd,0o600); _file_info(os.fstat(fd)); os.fsync(fd)
            finally:
                os.close(fd)
            os.fsync(parent)
        finally:
            os.close(parent)
        return {'path':str(directory/name),'sha256':digest(raw),'safe':safe_summary(freeze)}
    except Rejected:
        raise
    except Exception:
        raise Rejected('freeze_rejected') from None


def load_private_freeze(path, expected_sha256, contracts):
    try:
        _sha(expected_sha256); path = Path(path); parent = _private_directory(path.parent)
        try:
            fd = os.open(path.name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=parent)
            try:
                before = os.fstat(fd); _file_info(before)
                raw = bytearray()
                while True:
                    part = os.read(fd,65536)
                    if not part: break
                    raw.extend(part); require(len(raw)<=MAXIMUM)
                require(_signature(before)==_signature(os.fstat(fd))==
                        _signature(os.stat(path.name,dir_fd=parent,follow_symlinks=False)))
            finally:
                os.close(fd)
        finally:
            os.close(parent)
        require(digest(raw)==expected_sha256)
        freeze = decode_canonical(bytes(raw)); _validate(freeze,contracts)
        require(path.name=='freeze-'+freeze['window_id']+'-private.json')
        return freeze
    except Rejected:
        raise
    except Exception:
        raise Rejected('freeze_rejected') from None
