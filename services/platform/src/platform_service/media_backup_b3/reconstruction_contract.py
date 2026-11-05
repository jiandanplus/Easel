"""Private finite reconstruction DATA wire. Parsing grants no owner authority.

Independent of the v3 attestation frame and of the seven-file B2 archive.
Full source trees stay in original host custody; the worker gets a deduplicated
closed projection, the original plan pin and the exact raw database ACL.
"""
import base64
import binascii
from dataclasses import dataclass, fields
from enum import Enum
import json
import hashlib

from . import roles as r
from ..media_backup_b2.reconstruction_context import _Redacted

REQUEST_FORMAT = 'easel.b3.role-reconstruction-request.v1'
RESULT_FORMAT = 'easel.b3.role-reconstruction-result.v1'
TRANSITION_FORMAT = 'easel.b3.role-identity-transition.v1'
REQUEST_LIMIT = 32 * 1024 * 1024
PLAN_LIMIT = 24 * 1024 * 1024
RESULT_LIMIT = 4 * 1024 * 1024
AUTH_LIMIT = 2048 * 4096
DIAGNOSTIC_LIMIT = 65536

class ProtocolError(ValueError):
    def __init__(self): super().__init__('RECONSTRUCTION_PROTOCOL_REFUSED')

def require(condition):
    if not condition: raise ProtocolError()

class Operation(str, Enum):
    INITIAL_OBSERVE='INITIAL_OBSERVE'
    H_CREATE='H_CREATE'
    HANDOVER='HANDOVER'
    H_DROP='H_DROP'
    ROLES_APPLY='ROLES_APPLY'
    DB_CREATE='DB_CREATE'
    DB_FINALIZE='DB_FINALIZE'
    LOGIN_APPLY='LOGIN_APPLY'
    AUTH_CHECK='AUTH_CHECK'
    FINAL_SCAN='FINAL_SCAN'
    OBSERVE_RETAINED='OBSERVE_RETAINED'

READ_ONLY=frozenset((Operation.INITIAL_OBSERVE,Operation.AUTH_CHECK,Operation.FINAL_SCAN,Operation.OBSERVE_RETAINED))

def exact(value, keys):
    require(type(value) is dict and set(value)==set(keys))
    return value

def integer(value, low=1, high=2**63-1):
    require(type(value) is int and low<=value<=high)
    return value

def pin(value):
    require(type(value) is str and len(value)==64 and all(c in '0123456789abcdef' for c in value))
    return value

def text(value, maximum=1024):
    require(type(value) is str and bool(value) and '\x00' not in value)
    try: encoded=value.encode('utf-8')
    except UnicodeError: raise ProtocolError() from None
    require(len(encoded)<=maximum)
    return value

def bounded_frame(value, cap):
    # Check each streamed fragment before retaining it; never asdict a full plan.
    integer(cap)
    output=bytearray()
    try:
        for chunk in r._canonical_chunks(value):
            require(len(chunk)<=cap-len(output));output.extend(chunk)
    except Exception:
        raise ProtocolError() from None
    require(bool(output))
    return len(output).to_bytes(4,'big')+bytes(output)

def _frame(raw,cap):
    require(type(raw) is bytes and 4<len(raw)<=cap+4)
    require(int.from_bytes(raw[:4],'big')==len(raw)-4)
    def pairs(items):
        result={}
        for key,value in items:
            require(key not in result);result[key]=value
        return result
    def bad(value): raise ProtocolError()
    try: return json.loads(raw[4:],object_pairs_hook=pairs,parse_constant=bad)
    except Exception: raise ProtocolError() from None

def _tuple(value,maximum):
    require(type(value) is list and len(value)<=maximum)
    return tuple(value)

def _record(cls,value,**changes):
    exact(value,(f.name for f in fields(cls)))
    args=dict(value);args.update(changes)
    try: return cls(**args)
    except Exception: raise ProtocolError() from None

def _expectation(value):
    exact(value,(f.name for f in fields(r.RoleReplayExpectation)))
    try:
        return _record(r.RoleReplayExpectation,value,
            purpose=r.RolePurpose(value['purpose']),
            config=None if value['config'] is None else _tuple(value['config'],256))
    except Exception: raise ProtocolError() from None

@dataclass(frozen=True,repr=False)
class ReplayProgram(_Redacted):
    kind: str
    roles: tuple
    memberships: tuple
    database: r.DatabaseDefinition
    settings: tuple
    database_acls: tuple
    database_acl_raw: str | None
    handover: r.BootstrapHandoverRequirement | None
    authentication: tuple
    plan_sha256: str

def project_plan(plan):
    require(type(plan) is r.RoleDatabasePlan)
    plan.__post_init__()
    witnesses=[];indices={};memberships=[]
    for grant in plan.memberships:
        witness=grant.witness
        if witness not in indices:
            indices[witness]=len(witnesses);witnesses.append(witness)
        memberships.append(dict((f.name,getattr(grant,f.name)) for f in fields(grant) if f.name!='witness'))
        memberships[-1]['witness_index']=indices[witness]
    result=dict(kind=plan.kind,roles=plan.roles,memberships=memberships,witnesses=witnesses,
        database=plan.database,settings=plan.settings,database_acls=plan.database_acls,
        database_acl_raw=plan.source_database.acl,handover=plan.handover,
        authentication=plan.authentication,plan_sha256=r._hash(plan))
    # Canonical stream bounds before creating the independent JSON tree.
    return _frame(bounded_frame(result,PLAN_LIMIT),PLAN_LIMIT)

def decode_plan(value):
    exact(value,('kind','roles','memberships','witnesses','database','settings','database_acls',
        'database_acl_raw','handover','authentication','plan_sha256'))
    require(type(value['kind']) is str and value['kind'] in ('central','identity'))
    pin(value['plan_sha256'])
    # Native worker must not re-expand repeated source graphs/witness trees.
    witnesses=[]
    for raw in _tuple(value['witnesses'],r.MAX_EDGES):
        raw=_tuple(raw,r.MAX_EDGES)
        witnesses.append(raw if raw==('BOOTSTRAP_OID10',) else tuple(_tuple(e,6) for e in raw))
    grants=[]
    keys=tuple(f.name for f in fields(r.MembershipGrant) if f.name!='witness')+('witness_index',)
    for raw in _tuple(value['memberships'],r.MAX_EDGES):
        exact(raw,keys);idx=integer(raw['witness_index'],0,len(witnesses)-1)
        args={k:v for k,v in raw.items() if k!='witness_index'}
        try: grants.append(r.MembershipGrant(**args,witness=witnesses[idx]))
        except Exception: raise ProtocolError() from None
    definitions=[]
    for raw in _tuple(value['roles'],1024):
        exact(raw,('expectation','initial_login'))
        definitions.append(_record(r.RoleDefinition,raw,expectation=_expectation(raw['expectation'])))
    settings=[]
    for raw in _tuple(value['settings'],4096):
        try:
            settings.append(_record(r.ScopedSetting,raw,scope=r.SettingScope(raw['scope']),
                config=None if raw['config'] is None else _tuple(raw['config'],256)))
        except Exception: raise ProtocolError() from None
    handover=value['handover']
    if handover is not None:
        exact(handover,('expectation','source_oid'))
        handover=_record(r.BootstrapHandoverRequirement,handover,expectation=_expectation(handover['expectation']))
    auth=[]
    for raw in _tuple(value['authentication'],2048):
        try: auth.append(_record(r.AuthenticationRequirement,raw,mode=r.AuthenticationMode(raw['mode'])))
        except Exception: raise ProtocolError() from None
    acl=value['database_acl_raw']
    require(acl is None or type(acl) is str and len(acl.encode('utf-8'))<=PLAN_LIMIT)
    try:
        result=ReplayProgram(value['kind'],tuple(definitions),tuple(grants),
            _record(r.DatabaseDefinition,value['database']),tuple(settings),
            tuple(_record(r.DatabaseAclGrant,a) for a in _tuple(value['database_acls'],r.MAX_EDGES)),
            acl,handover,tuple(auth),value['plan_sha256'])
        # Re-prove the actual execution graph from the closed projection.
        planned=r.plan_memberships(tuple((g.role_name,g.member_name,g.admin_option,g.grantor_name,
            g.inherit_option,g.set_option) for g in result.memberships),
            bootstrap_name=handover.expectation.name if handover else None)
        require(result.memberships==planned)
        return result
    except Exception: raise ProtocolError() from None

_BINDING=('restore_id','job_dev','job_ino','kind','operation_id','ordinal','source_cid','target_cid',
    'system_id_sha256','input_sha256','context_sha256','pair_sha256','plan_sha256','rendering_sha256','started_ns','deadline_ns')
_POLICY=dict(request_limit=REQUEST_LIMIT,result_limit=RESULT_LIMIT,plan_limit=PLAN_LIMIT,
    auth_limit=AUTH_LIMIT,diagnostic_limit=DIAGNOSTIC_LIMIT)
_REQUEST=('format','binding','operation','policy','plan','identity_before','prerequisite','private_auth','remaining_ns','source_rendering','build')

@dataclass(frozen=True,repr=False)
class Request(_Redacted):
    binding: dict
    operation: Operation
    plan: ReplayProgram
    identity_before: object
    prerequisite: object
    private_auth: dict
    remaining_ns: int
    source_rendering: tuple
    build: dict

def _decode_request(raw):
    d=_frame(raw,REQUEST_LIMIT);exact(d,_REQUEST)
    require(d['format']==REQUEST_FORMAT)
    b=exact(d['binding'],_BINDING)
    for key in ('restore_id','operation_id'): text(b[key],128)
    for key in ('job_dev','job_ino','ordinal','started_ns','deadline_ns'): integer(b[key])
    require(b['started_ns']<b['deadline_ns'])
    for key in ('source_cid','target_cid','system_id_sha256','input_sha256','context_sha256','pair_sha256','plan_sha256','rendering_sha256'):pin(b[key])
    require(b['source_cid']!=b['target_cid'])
    exact(d['policy'],_POLICY)
    require(all(type(d['policy'][k]) is int and d['policy'][k]==v for k,v in _POLICY.items()))
    try: operation=Operation(d['operation'])
    except Exception: raise ProtocolError() from None
    bounded_frame(d['plan'],PLAN_LIMIT);program=decode_plan(d['plan'])
    require(b['kind']==program.kind and b['plan_sha256']==program.plan_sha256)
    identity=exact(d['identity_before'],('operational_name','operational_oid','source_name','source_oid',
        'helper_name','helper_oid','operational_attributes_sha256','operational_verifier_sha256','initdb_sha256'))
    for key in ('operational_name','source_name','helper_name'):
        if identity[key] is not None:
            try:r._name(identity[key])
            except Exception:raise ProtocolError() from None
    require(identity['operational_name'] is not None)
    integer(identity['operational_oid'],1,2**32-1)
    for key in ('source_oid','helper_oid'):
        if identity[key] is not None:integer(identity[key],1,2**32-1)
    pin(identity['operational_attributes_sha256'])
    for key in ('operational_verifier_sha256','initdb_sha256'):
        if operation is Operation.INITIAL_OBSERVE:require(identity[key] is None)
        else:pin(identity[key])
    if operation is Operation.INITIAL_OBSERVE:
        require(identity['operational_oid']==10 and identity['source_oid'] is None and identity['helper_oid'] is None)
    for key in ('source','helper'):
        require(identity[key+'_oid'] is None or identity[key+'_name'] is not None)
    names=[identity[k] for k in ('operational_name','source_name','helper_name') if identity[k] is not None]
    require(len(set(names))==len(names))
    prerequisite=exact(d['prerequisite'],('ordinal','head_sha256','record_sha256'))
    integer(prerequisite['ordinal'],0);pin(prerequisite['head_sha256']);pin(prerequisite['record_sha256'])
    require(prerequisite['ordinal']==b['ordinal']-1)
    auth=d['private_auth'];require(type(auth) is dict and 'operational_password' in auth)
    require(set(auth)<=set(('operational_password','helper_name','helper_password','helper_verifier','credentials')))
    decoded={};total=0
    for key,value in auth.items():
        if key=='credentials':
            require(type(value) is dict and len(value)<=2048)
            entries=value.items()
        else: entries=((key,value),)
        target={} if key=='credentials' else decoded
        for name,encoded in entries:
            text(name,128);require(type(encoded) is str and len(encoded)<=5464)
            try: secret=base64.b64decode(encoded,validate=True)
            except (ValueError,binascii.Error): raise ProtocolError() from None
            require(0<len(secret)<=4096 and base64.b64encode(secret).decode('ascii')==encoded)
            total+=len(secret);require(total<=AUTH_LIMIT);target[name]=secret
        if key=='credentials':decoded[key]=target
    if 'credentials' in decoded:
        expected={a.credential_id:a.credential_sha256 for a in program.authentication}
        require(set(decoded['credentials'])<=set(expected))
        require(all(hashlib.sha256(secret).hexdigest()==expected[name] for name,secret in decoded['credentials'].items()))
    if operation in (Operation.H_CREATE,Operation.HANDOVER,Operation.H_DROP):
        require(program.handover is not None)
        require(set(('helper_name','helper_password','helper_verifier'))<=set(decoded))
        require(decoded['helper_name'].decode('ascii','strict')==identity['helper_name'])
    else:
        require(not set(('helper_name','helper_password','helper_verifier'))&set(decoded))
    rendering=exact(d['source_rendering'],('timezone','datestyle'))
    rendering=tuple(text(rendering[k],4096) for k in ('timezone','datestyle'))
    require(r._hash(rendering)==b['rendering_sha256'])
    build=exact(d['build'],('artifact_sha256','manifest_sha256','entrypoint_sha256','client_sha256','program_sha256','executable_sha256',
        'image_id','cpu_nanos','memory_bytes','pids_limit','tmpfs_bytes'))
    for k in ('artifact_sha256','manifest_sha256','entrypoint_sha256','client_sha256','program_sha256','executable_sha256'):pin(build[k])
    require(type(build['image_id']) is str and build['image_id'].startswith('sha256:'));pin(build['image_id'][7:])
    for k,maximum in (('cpu_nanos',2*10**9),('memory_bytes',2*1024**3),('pids_limit',128),('tmpfs_bytes',256*1024**2)):
        integer(build[k],1,maximum)
    remaining=integer(d['remaining_ns']);require(remaining<=b['deadline_ns']-b['started_ns'])
    return Request(dict(b),operation,program,d['identity_before'],d['prerequisite'],decoded,remaining,rendering,dict(build))

def decode_request(raw):
    try:return _decode_request(raw)
    except Exception:raise ProtocolError() from None

def encode_request(document):
    raw=bounded_frame(document,REQUEST_LIMIT)
    decode_request(raw)
    return raw


_RESULT=('format','request_sha256','binding','operation','build','session','observation','installed_evidence')

def decode_result(raw,request_raw):
    """Strict DATA validation; original native and durable custody stays at Root."""
    try:
        q=decode_request(request_raw);d=exact(_frame(raw,RESULT_LIMIT),_RESULT)
        require(d['format']==RESULT_FORMAT)
        require(d['request_sha256']==hashlib.sha256(request_raw).hexdigest())
        require(d['binding']==q.binding and d['operation']==q.operation.value and d['build']==q.build)
        _installed_evidence(d['installed_evidence'],q.build)
        s=exact(d['session'],('before','after','commit_acknowledged','role_sha256','membership_sha256'))
        for key in ('before','after'):
            row=s[key];require(type(row) is list and len(row)==6)
            integer(row[0]);text(row[1]);integer(row[2],1,2**32-1);integer(row[3],1,2**32-1)
            require(row[2]==row[3]==q.identity_before['operational_oid'])
            require(type(row[4]) is str and row[4].isascii() and row[4].isdecimal())
            require(hashlib.sha256(row[4].encode('ascii')).hexdigest()==q.binding['system_id_sha256'])
            integer(row[5],160000,169999)
        require(s['before']==s['after'])
        if q.operation is Operation.INITIAL_OBSERVE:
            require(s['commit_acknowledged'] is False and s['role_sha256'] is None and s['membership_sha256'] is None)
            obs=exact(d['observation'],('operational_oid','operational_attributes_sha256','operational_verifier_sha256','initdb_sha256'))
            require(type(obs['operational_oid']) is int and obs['operational_oid']==10)
            require(obs['operational_attributes_sha256']==q.identity_before['operational_attributes_sha256'])
            for key in ('operational_attributes_sha256','operational_verifier_sha256','initdb_sha256'):pin(obs[key])
        else:
            require(q.operation is Operation.ROLES_APPLY and d['observation'] is None)
            require(s['commit_acknowledged'] is True);pin(s['role_sha256']);pin(s['membership_sha256'])
        return d
    except Exception:raise ProtocolError() from None


def _installed_evidence(e,build):
    from .network_wire import decode_network_observation
    from .network_profile import validate_network_profile
    hashes=('manifest_sha256','artifact_sha256','entrypoint_sha256','executable_sha256','program_sha256','client_sha256')
    exact(e,hashes+('pq_implementation','before','after'))
    for key in hashes:pin(e[key]);require(e[key]==build[key])
    require(e['pq_implementation']=='binary')
    stable=[]
    for side in ('before','after'):
        row=exact(e[side],('origins_sha256','security'));pin(row['origins_sha256'])
        s=exact(row['security'],('uid','gid','groups','capabilities','no_new_privileges','cgroup','resources','netns','network'))
        require(type(s['uid']) is int and type(s['gid']) is int and s['uid']==s['gid']==10001)
        require(type(s['groups']) is list and s['groups'] in ([],[10001]) and all(type(g) is int for g in s['groups']))
        caps=exact(s['capabilities'],('CapInh','CapPrm','CapEff','CapBnd','CapAmb'))
        require(all(type(v) is int and v==0 for v in caps.values()))
        require(s['no_new_privileges'] is True and s['cgroup']=='0::/')
        resources=exact(s['resources'],('memory.max','memory.swap.max','pids.max','cpu_quota','cpu_period'))
        for key,value in resources.items():integer(value,0)
        require(resources['memory.max']==build['memory_bytes'] and resources['memory.swap.max']==0 and resources['pids.max']==build['pids_limit'])
        require(resources['cpu_quota']>0 and resources['cpu_period']>0 and resources['cpu_quota']*10**9==build['cpu_nanos']*resources['cpu_period'])
        exact(s['netns'],('device','inode'))
        for v in s['netns'].values():integer(v)
        bounded_frame(s['network'],1024*1024)
        network=validate_network_profile(decode_network_observation(s['network']))
        require(s['network']['namespace_before']==s['network']['namespace_after']==s['netns'])
        stable.append(({k:v for k,v in s.items() if k!='network'},network.profile_sha256,network.security_sha256))
    require(stable[0]==stable[1])
    return e
