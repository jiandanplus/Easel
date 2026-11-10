"""Fixed PG16 utility compiler; typed DATA in, psycopg composables out.

This module has no connection, native executor, caller-provided SQL or authority.
Secret-bearing statements must only run after the private worker log guard.
"""
from collections import defaultdict, deque
from dataclasses import fields,dataclass
from psycopg.sql import SQL,Identifier,Literal
from . import roles as r
from .reconstruction_contract import ReplayProgram

class CompilationError(ValueError):
    def __init__(self):super().__init__('RECONSTRUCTION_SQL_REFUSED')

def _need(value):
    if not value:raise CompilationError()

def _typed(value,cls):
    _need(type(value) is cls)
    value.__post_init__()
    return value

def _program(value):
    _need(type(value) in (r.RoleDatabasePlan,ReplayProgram))
    if type(value) is r.RoleDatabasePlan:value.__post_init__()
    return value

def _boolean(value):
    _need(type(value) is bool)
    return SQL('TRUE' if value else 'FALSE')

def role_attributes(expectation, *, login=False):
    e=_typed(expectation,r.RoleReplayExpectation)
    _need(type(login) is bool)
    tokens=[]
    for enabled,positive,negative in ((e.superuser,'SUPERUSER','NOSUPERUSER'),
        (e.inherit,'INHERIT','NOINHERIT'),(e.createrole,'CREATEROLE','NOCREATEROLE'),
        (e.createdb,'CREATEDB','NOCREATEDB'),(login,'LOGIN','NOLOGIN'),
        (e.replication,'REPLICATION','NOREPLICATION'),(e.bypassrls,'BYPASSRLS','NOBYPASSRLS')):
        tokens.append(SQL(positive if enabled else negative))
    tokens.extend((SQL('CONNECTION LIMIT {}').format(Literal(e.connlimit)),
        SQL('PASSWORD {}').format(Literal(e.verifier)),
        SQL('VALID UNTIL {}').format(Literal(e.validuntil if e.validuntil is not None else 'infinity'))))
    # pg_authid NULL validity cannot be produced by VALID UNTIL 'infinity'.
    # Initial CREATE omits the clause for NULL; ALTER uses the worker exact
    # before-state invariant (NULL must already be NULL) and omits it as well.
    if e.validuntil is None:tokens.pop()
    return SQL(' ').join(tokens)

@dataclass(frozen=True,repr=False)
class SessionSetting:
    # Fixed program instruction; the raw value goes through set_config and
    # never through SQL SET's list-identifier flattening.
    key: str
    value: str
    statement: object
    def __repr__(self):return '<private fixed GUC instruction>'

def _role_settings(name,config):
    output=[SQL('ALTER ROLE {} RESET ALL').format(Identifier(name))]
    for value in config or ():
        key,sep,setting=value.partition('=');_need(bool(sep))
        output.append(SessionSetting(key,setting,SQL('ALTER ROLE {} SET {} FROM CURRENT').format(Identifier(name),Identifier(key))))
    return tuple(output)

def roles_apply(plan):
    p=_program(plan);output=[]
    for definition in p.roles:
        _typed(definition,r.RoleDefinition);e=definition.expectation
        output.append(SQL('CREATE ROLE {} WITH {}').format(Identifier(e.name),role_attributes(e)))
        output.extend(_role_settings(e.name,e.config))
    return tuple(output)

def membership(grant):
    g=_typed(grant,r.MembershipGrant)
    return SQL('GRANT {} TO {} WITH ADMIN {}, INHERIT {}, SET {} GRANTED BY {}').format(
        Identifier(g.role_name),Identifier(g.member_name),_boolean(g.admin_option),
        _boolean(g.inherit_option),_boolean(g.set_option),Identifier(g.grantor_name))

def database_create(definition):
    d=_typed(definition,r.DatabaseDefinition)
    _need(d.provider in ('c','i'))
    values=[SQL('OWNER {}').format(Identifier(d.owner_name)),
        SQL('TEMPLATE {}').format(Identifier(d.template_name)),
        SQL('ENCODING {}').format(Literal(d.encoding_name)),
        SQL('LC_COLLATE {}').format(Literal(d.collate)),SQL('LC_CTYPE {}').format(Literal(d.ctype)),
        SQL('LOCALE_PROVIDER '+('icu' if d.provider=='i' else 'libc')),
        SQL('TABLESPACE {}').format(Identifier(d.tablespace_name)),
        SQL('CONNECTION LIMIT -1'),SQL('IS_TEMPLATE FALSE'),SQL('ALLOW_CONNECTIONS TRUE')]
    if d.provider=='i':
        _need(d.icu_locale is not None)
        values.append(SQL('ICU_LOCALE {}').format(Literal(d.icu_locale)))
        if d.icu_rules is not None:values.append(SQL('ICU_RULES {}').format(Literal(d.icu_rules)))
    else:_need(d.icu_locale is None and d.icu_rules is None)
    if d.recorded_version is not None:values.append(SQL('COLLATION_VERSION {}').format(Literal(d.recorded_version)))
    return SQL('CREATE DATABASE {} WITH {}').format(Identifier(d.name),SQL(' ').join(values))

def settings(scoped_settings):
    _need(type(scoped_settings) is tuple and len(scoped_settings)<=4096)
    output=[]
    for s in scoped_settings:
        _typed(s,r.ScopedSetting)
        if s.scope is r.SettingScope.GLOBAL:prefix=SQL('ALTER ROLE ALL')
        elif s.scope is r.SettingScope.ROLE:prefix=SQL('ALTER ROLE {}').format(Identifier(s.role_name))
        elif s.scope is r.SettingScope.DATABASE:prefix=SQL('ALTER DATABASE {}').format(Identifier(s.database_name))
        else:prefix=SQL('ALTER ROLE {} IN DATABASE {}').format(Identifier(s.role_name),Identifier(s.database_name))
        output.append(SQL('{} RESET ALL').format(prefix))
        for config in s.config or ():
            key,sep,value=config.partition('=');_need(bool(sep))
            output.append(SessionSetting(key,value,SQL('{} SET {} FROM CURRENT').format(prefix,Identifier(key))))
    return tuple(output)

def plan_database_acl(owner,grants):
    """Owner-rooted grant-option graph, independently per privilege.

    Every edge is an original source edge; no invented temporary authority.
    Queue each eligible grantor once, then sort executable edges deterministically.
    Work/storage are bounded by the admitted edge count, not graph depth squared.
    """
    try:r._name(owner)
    except Exception:raise CompilationError() from None
    _need(type(grants) is tuple and len(grants)<=r.MAX_EDGES)
    waiting=defaultdict(list);seen=set()
    for g in grants:
        _typed(g,r.DatabaseAclGrant)
        _need(g.grantor_name!='PUBLIC' and not(g.grantee_name=='PUBLIC' and g.grantable))
        key=tuple(getattr(g,f.name) for f in fields(g))
        _need(key not in seen);seen.add(key)
        waiting[(g.privilege,g.grantor_name)].append(g)
    allowed={(p,owner) for p in ('CONNECT','CREATE','TEMPORARY')}
    queue=deque(sorted(allowed));result=[]
    while queue:
        key=queue.popleft()
        for g in sorted(waiting.pop(key,()),key=lambda x:(x.grantee_name,x.grantable,x.object_name)):
            result.append(g)
            new=(g.privilege,g.grantee_name)
            if g.grantable and new not in allowed:
                allowed.add(new);queue.append(new)
    _need(not waiting and len(result)==len(grants))
    return tuple(result)

def database_acl(database,owner,raw_acl,grants):
    try:r._name(database);r._name(owner)
    except Exception:raise CompilationError() from None
    if raw_acl is None:
        _need(grants==());return ()
    _need(type(raw_acl) is str)
    schedule=plan_database_acl(owner,grants)
    _need(all(g.object_name==database for g in schedule))
    output=[SQL('SET LOCAL ROLE {}').format(Identifier(owner)),
        SQL('REVOKE ALL PRIVILEGES ON DATABASE {} FROM PUBLIC, {}').format(Identifier(database),Identifier(owner)),SQL('RESET ROLE')]
    for g in schedule:
        grantee=SQL('PUBLIC') if g.grantee_name=='PUBLIC' else Identifier(g.grantee_name)
        output.extend((SQL('SET LOCAL ROLE {}').format(Identifier(g.grantor_name)),
            SQL('GRANT {} ON DATABASE {} TO {}{}').format(SQL(g.privilege),Identifier(database),grantee,
                SQL(' WITH GRANT OPTION' if g.grantable else '')),SQL('RESET ROLE')))
    return tuple(output)

def database_final_attributes(definition):
    d=_typed(definition,r.DatabaseDefinition)
    return (SQL('ALTER DATABASE {} WITH CONNECTION LIMIT {} IS_TEMPLATE {} ALLOW_CONNECTIONS {}').format(
        Identifier(d.name),Literal(d.connlimit),_boolean(d.istemplate),_boolean(d.allowconn)),)
