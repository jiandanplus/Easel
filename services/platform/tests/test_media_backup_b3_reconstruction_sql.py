"""Pure psycopg composables: never connect or execute."""
import dataclasses as dc
import importlib.util
import pytest
from platform_service.media_backup_b3 import roles
from test_media_backup_b3_roles import fixture,SECRET_VERIFIER

MODULE='platform_service.media_backup_b3.reconstruction_sql'
def api():
    assert importlib.util.find_spec(MODULE) is not None, 'missing typed fixed SQL compiler'
    return __import__(MODULE,fromlist=['*'])
def plan(): return roles.build_role_database_plan(**fixture())
def render(statements): return [(s.statement if type(s) is api().SessionSetting else s).as_string(None) for s in statements]
def acl(grantor,grantee,privilege='CONNECT',go=False):
    return roles.DatabaseAclGrant('','source_db','database',grantor,grantee,privilege,go)

def test_missing_compiler_is_red(): api()
def test_roles_all_attributes_nologin_null_verifier_and_validuntil():
    s=api();p=plan();text=' '.join(render(s.roles_apply(p)))
    assert 'NOLOGIN' in text and 'PASSWORD NULL' in text and 'VALID UNTIL' not in text
    assert 'CREATEROLE' in text and 'CONNECTION LIMIT' in text and '-1' in text
    e=dc.replace(p.roles[0].expectation,validuntil='2030-01-01 00:00:00+00')
    assert "VALID UNTIL '2030-01-01 00:00:00+00'" in s.role_attributes(e).as_string(None)
    assert 'CREATE ROLE "A"' not in text

def test_role_verifier_and_guc_are_literals():
    s=api();p=plan();e=dc.replace(p.roles[0].expectation,verifier=SECRET_VERIFIER,
        config=("search_path=a=b'; DROP ROLE x; --",))
    p=dc.replace(p,roles=(roles.RoleDefinition(e),))
    text=' '.join(render(s.roles_apply(p)))
    assert 'PASSWORD '+s.Literal(SECRET_VERIFIER).as_string(None) in text
    step=next(x for x in s.roles_apply(p) if type(x) is s.SessionSetting)
    assert step.key=='search_path' and step.value=="a=b'; DROP ROLE x; --"
    assert '"search_path" FROM CURRENT' in text

def test_membership_uses_actual_grantor_and_every_option():
    s=api();g=plan().memberships[0];text=s.membership(g).as_string(None)
    assert 'GRANTED BY "A"' in text
    assert 'ADMIN TRUE' in text and 'INHERIT FALSE' in text and 'SET TRUE' in text

def test_database_create_preserves_locale_encoding_tablespace_stages_access():
    s=api();d=dc.replace(plan().database,allowconn=False,connlimit=0)
    text=s.database_create(d).as_string(None)
    for term in ('TEMPLATE "template0"',"ENCODING 'LATIN1'","LC_COLLATE 'de_DE.ISO8859-1'",'TABLESPACE "custom_space"','ALLOW_CONNECTIONS TRUE','CONNECTION LIMIT -1'):
        assert term in text
    assert 'BEGIN' not in text

def test_icu_rules_and_recorded_version_are_not_replaced_by_actual():
    s=api();d=dc.replace(plan().database,provider='i',icu_locale='de-DE',icu_rules='&a<b',recorded_version='153.1',actual_version='153.2')
    text=s.database_create(d).as_string(None)
    assert "LOCALE_PROVIDER icu" in text and "ICU_LOCALE 'de-DE'" in text
    assert "ICU_RULES '&a<b'" in text and "COLLATION_VERSION '153.1'" in text
    assert '153.2' not in text

def test_settings_all_scopes_and_empty_equals_values():
    s=api();p=plan();text=' '.join(render(s.settings(p.settings)))
    assert 'ALTER ROLE ALL SET "timezone"' in text
    assert 'ALTER ROLE "R" SET "search_path"' in text
    assert 'ALTER DATABASE "source_db" SET "work_mem"' in text
    assert 'ALTER ROLE "M" IN DATABASE "source_db" RESET ALL' in text

def test_acl_separate_per_privilege_rooted_schedule():
    s=api();edges=(acl('G','M',go=True),acl('R','G',go=True),acl('M','N'),acl('R','M','CREATE'))
    schedule=s.plan_database_acl('R',edges)
    assert schedule.index(edges[1])<schedule.index(edges[0])<schedule.index(edges[2])
    text=' '.join(render(s.database_acl('source_db','R','raw',edges)))
    assert 'SET LOCAL ROLE "G"' in text and 'WITH GRANT OPTION' in text
    assert 'RESET ROLE' in text

@pytest.mark.parametrize('edges',[(acl('G','M'),),(acl('G','M',go=True),acl('M','G',go=True)),(acl('R','G','CREATE',True),acl('G','M','CONNECT'))])
def test_acl_no_invented_support_edges(edges):
    s=api()
    with pytest.raises(s.CompilationError):s.plan_database_acl('R',edges)

def test_null_acl_is_not_normalized_to_explicit_default():
    s=api();assert s.database_acl('source_db','R',None,())==()
    with pytest.raises(s.CompilationError):s.database_acl('source_db','R',None,(acl('R','G'),))

def test_final_attributes_deferred_until_final_phase():
    s=api();d=dc.replace(plan().database,allowconn=False,connlimit=0,istemplate=True)
    text=' '.join(render(s.database_final_attributes(d)))
    assert 'ALLOW_CONNECTIONS FALSE' in text and 'CONNECTION LIMIT 0' in text and 'IS_TEMPLATE TRUE' in text

@pytest.mark.parametrize('value',['public, pg_catalog','"one,two", "three four", public','"a""b", "$user"'])
def test_list_guc_preserves_raw_value_separately_from_alter_sql(value):
    s=api();p=plan();e=dc.replace(p.roles[0].expectation,config=('search_path='+value,))
    p=dc.replace(p,roles=(roles.RoleDefinition(e),))
    step=next(x for x in s.roles_apply(p) if type(x) is s.SessionSetting)
    assert step.value==value
    assert step.statement.as_string(None).endswith('SET "search_path" FROM CURRENT')
