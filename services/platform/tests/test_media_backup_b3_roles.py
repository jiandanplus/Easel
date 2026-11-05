"""Synthetic closed replay DATA tests; never native resources."""
import unittest
from platform_service.media_backup_b3 import roles
from platform_service.media_backup_b2 import reconstruction_grants as grants

class Availability(unittest.TestCase):
    def test_pure_builder_available(self):
        self.assertTrue(callable(roles.build_role_database_pair))
        self.assertTrue(callable(grants.parse_grant_graph))

import base64
import dataclasses as dc
import hashlib
import itertools
from unittest.mock import patch
from platform_service.media_backup_b2 import reconstruction_context as c
from platform_service.media_backup_b2.contracts import B2Error
from platform_service.media_backup_b3.contracts import ComparisonContext,RestoreProfile,Table,Column
from test_media_backup_b3_target_contract import spec

SECRET_VERIFIER = 'SCRAM-SHA-256$4096:c2FsdA==$' + base64.b64encode(b'x'*32).decode() + ':' + base64.b64encode(b'y'*32).decode()

def fixture(provider='c'):
    scope = c.SourceReadScope('central','a'*64,hashlib.sha256(b'123').hexdigest(),'source_db','A',('A','G','M','R'))
    states = tuple(c.RoleState(oid,name,name=='A',True,False,False,name=='A',False,-1,None,False,
                              ('search_path=public',) if name=='R' else None,
                              SECRET_VERIFIER if name=='A' else None,'123')
                   for oid,name in ((10,'A'),(42,'R'),(43,'G'),(44,'M')))
    database = c.DatabaseState(100,'source_db',42,'R',8,'LATIN1','de_DE.ISO8859-1','de_DE.ISO8859-1',provider,
        'de-DE' if provider=='i' else None,'&a<b' if provider=='i' else None,None,'2.36','{}',8,False,True,'custom_space','333')
    settings = (c.SettingState(0,0,None,('timezone=Europe/Berlin',),'1'),
                c.SettingState(0,42,'R',('search_path=public',),'2'),
                c.SettingState(100,0,None,('work_mem=8MB',),'3'),
                c.SettingState(100,44,'M',(),'4'))
    phases = []
    for phase in (c.SourcePhase.BEFORE,c.SourcePhase.DURING,c.SourcePhase.AFTER):
        identity = c.SourceIdentity('source_db','123',160015,100+len(phases),'10:20:','on','repeatable read','Asia/Shanghai','A','A',10,10)
        phases.append(c.SourceSample(scope,phase,identity,states,database,settings,
            '00000001-00000002-1' if phase is c.SourcePhase.DURING else None,
            '10:20:' if phase is c.SourcePhase.DURING else None))
    expectations = tuple(roles.RoleReplayExpectation(*tuple(getattr(r,f) for f in ('name','superuser','inherit','createrole','createdb','login','replication','connlimit','validuntil','bypassrls','config','verifier')),
        roles.RolePurpose.BOOTSTRAP_HANDOVER if r.oid==10 else roles.RolePurpose.RESTORED,10 if r.oid==10 else None) for r in states)
    sd = roles.DatabaseReplayExpectation(*tuple(getattr(database,f.name) for f in dc.fields(roles.DatabaseReplayExpectation) if f.name!='settings'),roles._settings(phases[1]))
    auth = []
    for r in expectations:
        modes = (roles.AuthenticationMode.POSITIVE_SCRAM,roles.AuthenticationMode.DENY_WRONG_PASSWORD) if r.login else (roles.AuthenticationMode.DENY_NOLOGIN,)
        for i,mode in enumerate(modes):
            auth.append(roles.AuthenticationRequirement(r.name,mode,r.name+'_credential_'+str(i),'b'*64))
    memberships = (('R','G',True,'A',False,True),('R','M',False,'G',True,False))
    graphscope = grants.GrantGraphScope('central',('G','M','R'))
    a = roles.ReplayAuthorizationData(roles.FORMAT,'central','toy_job',100.,1900.,'a'*64,scope.system_identifier_sha256,'source_db','f'*64,
        expectations,sd,memberships,(),(),tuple(auth),graphscope)
    profile = RestoreProfile('toy',(Table('toy',(Column('id','integer',False),),('id',)),),'c'*64,'central','full_raw_and_copy')
    ctx = ComparisonContext('source_db',('G','M','R'),('A',),profile,(('toy',()),),('timezone','work_mem','search_path'))
    observation = {'roles':{'roles':[list(e.attributes()) for e in expectations if e.name!='A'],
        'credentials':[[r.name,r.verifier,r.auth_xmin] for r in states if r.name!='A'],
        'memberships':[list(e)+['777'] for e in memberships], 'acls':[]},
        'schema':{'database':[['source_db','R','{}',8,False,True]]}}
    graphrows = [(42,'R',43,'G',10,'A',True,False,True,'777'),(42,'R',44,'M',43,'G',False,True,False,'777')]
    graphs = tuple(grants.parse_grant_graph(graphscope,s,graphrows) for s in phases)
    ts = spec(source_container_id='a'*64,source_system_id_sha256=scope.system_identifier_sha256,restored_role_names=('G','M','R','peer_role'))
    return dict(sample=tuple(phases),observation=observation,context=ctx,authorization=a,target_spec=ts,provenance={'plan_sha256':'d'*64},grant_sample=graphs)


class ReplayTests(unittest.TestCase):
    def bad(self, fn):
        with self.assertRaises(B2Error) as caught:
            fn()
        self.assertEqual(str(caught.exception),'invalid_backup')

    def test_full_libc_and_icu_values_settings_and_private_evidence(self):
        for provider in ('c','i'):
            kw = fixture(provider)
            plan = roles.build_role_database_plan(**kw)
            self.assertEqual(plan.database.encoding_name,'LATIN1')
            self.assertEqual(plan.database.icu_rules,'&a<b' if provider=='i' else None)
            self.assertEqual(plan.database.template_name,'template0')
            self.assertEqual(len(plan.settings),4)
            self.assertEqual(plan.source_database,kw['sample'][1].database)
            self.assertEqual(plan.source_memberships[0][-1],'777')
            self.assertEqual(tuple(r.expectation.name for r in plan.roles),('G','M','R'))
            self.assertEqual(plan.handover.source_oid,10)
            self.assertTrue(all(not r.initial_login for r in plan.roles))
            self.assertNotIn('source_db',repr(plan))
            self.assertNotIn(SECRET_VERIFIER,str(plan.safe_summary()))
            with self.assertRaises(dc.FrozenInstanceError):
                plan.kind = 'identity'

    def test_every_exact_role_projection_and_credential_xmin_rejects(self):
        for field in range(1,11):
            kw=fixture(); row=kw['observation']['roles']['roles'][0]
            row[field] = not row[field] if type(row[field]) is bool else ('changed' if field!=7 else 1)
            self.bad(lambda:roles.build_role_database_plan(**kw))
        for field in (1,2):
            kw=fixture(); kw['observation']['roles']['credentials'][0][field]='changed'
            self.bad(lambda:roles.build_role_database_plan(**kw))
        kw=fixture(); kw['observation']['roles']['roles'][0][1]=0
        self.bad(lambda:roles.build_role_database_plan(**kw))

    def test_unauthorized_database_and_setting_values_reject(self):
        for field,value in (('collate','C'),('encoding_name','UTF8'),('acl',None),('tablespace_name','pg_default'),('recorded_version','other')):
            kw=fixture(); a=kw['authorization']
            def attempt():
                kw['authorization']=dc.replace(a,database=dc.replace(a.database,**{field:value}))
                roles.build_role_database_plan(**kw)
            self.bad(attempt)
        kw=fixture(); a=kw['authorization']; settings=a.database.settings
        kw['authorization']=dc.replace(a,database=dc.replace(a.database,settings=settings[:-1]))
        self.bad(lambda:roles.build_role_database_plan(**kw))

    def test_no_handover_from_oid10_observation(self):
        kw=fixture(); a=kw['authorization']; kw['observation']['roles']['memberships']=[]
        kw['authorization']=dc.replace(a,roles=tuple(r for r in a.roles if r.name!='A'),memberships=(),authentication=tuple(r for r in a.authentication if r.role_name!='A'),grant_graph_scope=None)
        kw['grant_sample']=None
        plan=roles.build_role_database_plan(**kw)
        self.assertIsNone(plan.handover)

    def test_graph_exact_reference_stable_identity_and_xmin(self):
        kw=fixture(); gs=kw['grant_sample']
        self.assertIs(gs[1].scope,kw['authorization'].grant_graph_scope)
        self.assertIs(gs[1].source_sample,kw['sample'][1])
        self.assertEqual(gs[0].stable_private_value(),gs[2].stable_private_value())
        self.assertNotEqual(gs[0].source_sample_pin,gs[1].source_sample_pin)
        altered=dc.replace(gs[2].edges[0],xmin='778')
        kw['grant_sample']=(gs[0],gs[1],dc.replace(gs[2],edges=(altered,gs[2].edges[1])))
        self.bad(lambda:roles.build_role_database_plan(**kw))

    def test_graph_oid_name_duplicate_and_bounds(self):
        kw=fixture(); scope=kw['authorization'].grant_graph_scope; sample=kw['sample'][1]
        row=(42,'R',43,'G',10,'A',True,False,True,'777')
        for rows in ((row,row),((42,'WRONG',43,'G',10,'A',True,False,True,'777'),), (row,)*16385):
            self.bad(lambda:grants.parse_grant_graph(scope,sample,rows))
        self.bad(lambda:grants.GrantEdge(True,'R',43,'G',10,'A',True,False,True,'777'))

    def test_closure_and_export_missing_support_reject(self):
        kw=fixture(); kw['observation']['roles']['memberships'].pop(0)
        self.bad(lambda:roles.build_role_database_plan(**kw))
        kw=fixture(); a=kw['authorization']; kw['authorization']=dc.replace(a,roles=tuple(r for r in a.roles if r.name!='G'),authentication=tuple(r for r in a.authentication if r.role_name!='G'))
        self.bad(lambda:roles.build_role_database_plan(**kw))

    def test_authentication_modes_and_md5_nologin(self):
        kw=fixture(); a=kw['authorization']; kw['authorization']=dc.replace(a,authentication=a.authentication[1:])
        self.bad(lambda:roles.build_role_database_plan(**kw))
        # Full observed noLOGIN MD5 DATA is supported and remains byte-exact.
        kw=fixture(); phases=tuple(dc.replace(s,roles=tuple(dc.replace(r,verifier='md5'+'1'*32) if r.name=='M' else r for r in s.roles)) for s in kw['sample'])
        kw['sample']=phases; a=kw['authorization']; kw['authorization']=dc.replace(a,roles=tuple(dc.replace(r,verifier='md5'+'1'*32) if r.name=='M' else r for r in a.roles))
        for row in kw['observation']['roles']['credentials']:
            if row[0]=='M': row[1]='md5'+'1'*32
        kw['grant_sample']=tuple(dc.replace(g,source_sample=s) for g,s in zip(kw['grant_sample'],phases))
        self.assertEqual(next(r for r in roles.build_role_database_plan(**kw).source_roles if r.name=='M').verifier,'md5'+'1'*32)


class PrefixTests(unittest.TestCase):
    def bad(self, edges, bootstrap='A'):
        with self.assertRaises(B2Error):roles.plan_memberships(tuple(edges),bootstrap_name=bootstrap)

    def test_external_and_longer_direct_admin_chains_deterministic(self):
        edges=(('R','G',True,'A',False,True),('R','H',True,'G',True,False),('R','M',False,'H',False,False))
        expected=roles.plan_memberships(edges,bootstrap_name='A')
        for p in itertools.permutations(edges):
            self.assertEqual(roles.plan_memberships(p,bootstrap_name='A'),expected)
        self.assertEqual(expected[-1].grantor_name,'H')

    def test_all_edge_cycles_self_and_no_superuser_shortcut(self):
        self.bad((('R','M',False,'A',False,False),('M','R',False,'A',False,False)))
        self.bad((('R','R',False,'A',False,False),))
        self.bad((('R','M',False,'SUPER',True,True),))
        self.bad((('R','G',True,'G',False,False),))

    def test_direct_admin_not_inherited_reachability(self):
        self.bad((('R','G',True,'A',True,True),('G','H',True,'A',True,True),('R','M',False,'H',True,True)))

    def test_circular_admin_cascade_and_independent_surviving_admin(self):
        prefix=[('R','G',True,'A',True,True),('R','H',True,'G',True,True)]
        edge=('R','G',True,'H',True,True)
        self.assertTrue(roles._circular_admin(prefix,edge,'A'))
        prefix.append(('R','H',True,'A',False,False))
        self.assertFalse(roles._circular_admin(prefix,edge,'A'))
        self.assertTrue(roles._circular_admin(prefix,('R','A',True,'G',True,True),'A'))
        self.assertFalse(roles._circular_admin(prefix,('R','A',True,'A',True,True),'A'))

    def test_all_rows_to_recipient_deleted_and_nonadmin_cascades(self):
        prefix=[('R','G',True,'A',True,True),('R','G',False,'H',True,True),('R','H',True,'G',True,True)]
        self.assertTrue(roles._circular_admin(prefix,('R','G',True,'H',True,True),'A'))

    def test_bool_option_and_duplicate_keys_reject(self):
        self.bad((('R','M',1,'A',True,True),))
        self.bad((('R','M',False,'A',True,True),('R','M',True,'A',False,False)))

class HardeningTests(unittest.TestCase):
    def test_raw_datacl_only_in_source_evidence(self):
        plan=roles.build_role_database_plan(**fixture())
        self.assertNotIn('acl',{f.name for f in dc.fields(plan.database)})
        self.assertNotIn('expectation',{f.name for f in dc.fields(plan.database)})
        self.assertEqual(plan.source_database.acl,'{}')

    def test_bound_exhaustion_is_safe_explicit_failure(self):
        with patch.object(roles,'MAX_PREFIX_WORK',0):
            with self.assertRaises(roles.RolePlanningError) as caught:
                roles.plan_memberships((('R','G',True,'A',True,True),),bootstrap_name='A')
        self.assertEqual(caught.exception.code,'BOUNDED_EXHAUSTION')
        self.assertEqual(str(caught.exception),'invalid_backup')

    def test_unsupported_md5_login_is_not_positive_auth(self):
        kw=fixture(); a=kw['authorization']; old=next(r for r in a.roles if r.name=='A')
        new=dc.replace(old,verifier='md5'+'1'*32)
        kw['authorization']=dc.replace(a,roles=tuple(new if r.name=='A' else r for r in a.roles))
        phases=tuple(dc.replace(s,roles=tuple(dc.replace(r,verifier=new.verifier) if r.name=='A' else r for r in s.roles)) for s in kw['sample'])
        kw['sample']=phases; kw['grant_sample']=tuple(dc.replace(g,source_sample=s) for g,s in zip(kw['grant_sample'],phases))
        with self.assertRaises(B2Error):roles.build_role_database_plan(**kw)

    def test_empty_and_null_configs_acl_survive_without_defaults(self):
        for raw_acl in (None,'{}',''):
            kw=fixture(); a=kw['authorization']
            phases=tuple(dc.replace(s,database=dc.replace(s.database,acl=raw_acl)) for s in kw['sample'])
            kw['sample']=phases
            kw['authorization']=dc.replace(a,database=dc.replace(a.database,acl=raw_acl))
            kw['observation']['schema']['database'][0][2]=raw_acl
            kw['grant_sample']=tuple(dc.replace(g,source_sample=s) for g,s in zip(kw['grant_sample'],phases))
            plan=roles.build_role_database_plan(**kw)
            self.assertIsNone(next(r for r in plan.source_roles if r.name=='G').config)
            self.assertEqual(next(s for s in plan.source_settings if s.role_name=='M').config,())
            self.assertEqual(plan.source_database.acl,raw_acl)

    def test_b3_leading_zero_verifier_boundary_unchanged(self):
        e=fixture()['authorization'].roles[0]
        with self.assertRaises(B2Error):dc.replace(e,verifier=SECRET_VERIFIER.replace('$4096:','$04096:'))

    def test_graph_scope_copy_and_transaction_copy_fail(self):
        for field in ('scope','source_sample'):
            kw=fixture(); gs=kw['grant_sample']; copied=dc.replace(getattr(gs[1],field))
            kw['grant_sample']=(gs[0],dc.replace(gs[1],**{field:copied}),gs[2])
            with self.assertRaises(B2Error):roles.build_role_database_plan(**kw)

    def test_safe_ast_no_native_or_action_opcodes(self):
        import ast
        from pathlib import Path
        for module in (roles,grants):
            source=Path(module.__file__).read_text()
            tree=ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node,ast.Import):
                    self.assertTrue(all(n.name in {'dataclasses','enum','hashlib','json','math'} for n in node.names))
                if isinstance(node,ast.ImportFrom):
                    self.assertNotIn(node.module,('os','subprocess','socket','ctypes','psycopg','execution','target_factory'))
            for name in ('argv','dsn','callback','executor','sql'):
                self.assertNotIn(name,{f.name for cls in (roles.RoleDatabasePlan,roles.ReplayAuthorizationData) for f in dc.fields(cls)})


class AdditionalProjectionTests(unittest.TestCase):
    def test_pinned_encoding_table_rejects_mismatch_clientonly_alias(self):
        d=fixture()['authorization'].database
        for values in ({'encoding_id':6}, {'encoding_name':'UTF-8'}, {'encoding_id':35,'encoding_name':'SJIS'}):
            with self.assertRaises(B2Error):dc.replace(d,**values)
        self.assertEqual(dc.replace(d,encoding_id=6,encoding_name='UTF8').encoding_name,'UTF8')

    def test_nologin_bootstrap_null_and_md5_handover(self):
        for verifier in (None,'md5'+'2'*32):
            kw=fixture(); a=kw['authorization']
            phases=tuple(dc.replace(s,roles=tuple(dc.replace(r,login=False,verifier=verifier) if r.name=='A' else r for r in s.roles)) for s in kw['sample'])
            auth=tuple(r for r in a.authentication if r.role_name!='A')+(roles.AuthenticationRequirement('A',roles.AuthenticationMode.DENY_NOLOGIN,'A_denial','b'*64),)
            kw['authorization']=dc.replace(a,roles=tuple(dc.replace(r,login=False,verifier=verifier) if r.name=='A' else r for r in a.roles),authentication=auth)
            kw['sample']=phases
            kw['grant_sample']=tuple(dc.replace(g,source_sample=s) for g,s in zip(kw['grant_sample'],phases))
            plan=roles.build_role_database_plan(**kw)
            self.assertFalse(plan.handover.expectation.login)
            self.assertEqual(plan.handover.expectation.verifier,verifier)

    def test_database_acl_and_acl_only_handover_exact(self):
        kw=fixture(); a=kw['authorization']
        acl=('',a.database_name,'database','A','PUBLIC','CONNECT',False)
        kw['authorization']=dc.replace(a,memberships=(),database_acls=(acl,),allowed_builtin_references=('PUBLIC',))
        kw['observation']['roles']['memberships']=[]
        kw['observation']['roles']['acls']=[list(acl)]
        kw['grant_sample']=tuple(dc.replace(g,edges=()) for g in kw['grant_sample'])
        plan=roles.build_role_database_plan(**kw)
        self.assertEqual(plan.database_acls[0].privilege,'CONNECT')
        self.assertEqual(plan.handover.source_oid,10)
        kw['observation']['roles']['acls'][0][4]='UNKNOWN'
        with self.assertRaises(B2Error):roles.build_role_database_plan(**kw)

    def test_pair_is_pure_atomic_and_rejects_one_failed_kind(self):
        left=fixture();right=fixture()
        rs=dc.replace(right['sample'][0].scope,kind='identity')
        right['sample']=tuple(dc.replace(s,scope=rs) for s in right['sample'])
        a=right['authorization'];gs=dc.replace(a.grant_graph_scope,kind='identity')
        right['authorization']=dc.replace(a,kind='identity',grant_graph_scope=gs)
        profile=dc.replace(right['context'].profile,source_kind='identity',evidence_mode='copy_only')
        right['context']=dc.replace(right['context'],profile=profile,row_order_pins=(),source_kind='identity',evidence_mode='copy_only')
        right['grant_sample']=tuple(dc.replace(g,scope=gs,source_sample=s) for g,s in zip(right['grant_sample'],right['sample']))
        for kw in (left,right):kw['target_spec']=dc.replace(kw['target_spec'],kind=kw['authorization'].kind,restored_role_names=('G','M','R'))
        kwargs={plural:tuple(kw[singular] for kw in (left,right)) for plural,singular in (
            ('samples','sample'),('observations','observation'),('contexts','context'),('authorizations','authorization'),('target_specs','target_spec'),('provenance','provenance'),('grant_samples','grant_sample'))}
        before=roles._hash(kwargs)
        plans=roles.build_role_database_pair(**kwargs)
        self.assertEqual(tuple(p.kind for p in plans),('central','identity'))
        self.assertEqual(before,roles._hash(kwargs))
        right['observation']['roles']['credentials'][0][2]='different'
        with self.assertRaises(B2Error):roles.build_role_database_pair(**kwargs)
        self.assertEqual(plans[0].safe_summary()['role_count'],3)


class ReviewRegressionTests(unittest.TestCase):
    def without_edges(self):
        kw = fixture()
        kw['authorization'] = dc.replace(kw['authorization'], memberships=(), grant_graph_scope=None)
        kw['observation']['roles']['memberships'] = []
        kw['grant_sample'] = None
        return kw

    def test_explicit_bootstrap_without_edges_and_other_database_owner(self):
        kw = self.without_edges()
        kw['context'] = dc.replace(kw['context'], role_names=('A','G','M','R'), allowed_grantors=())
        a = kw['authorization']
        kw['observation']['roles']['roles'] = [list(r.attributes()) for r in a.roles]
        kw['observation']['roles']['credentials'] = [[r.name,r.verifier,r.auth_xmin] for r in kw['sample'][1].roles]
        kw['target_spec'] = dc.replace(kw['target_spec'], restored_role_names=('A','G','M','R'))
        plan = roles.build_role_database_plan(**kw)
        self.assertEqual(plan.database.owner_name, 'R')
        self.assertEqual(plan.memberships, ())
        self.assertEqual(plan.handover.expectation.name, 'A')
        self.assertNotIn('A', tuple(r.expectation.name for r in plan.roles))

    def test_setting_only_bootstrap_requires_exact_independent_policy(self):
        for scope in (roles.SettingScope.ROLE, roles.SettingScope.DATABASE_ROLE):
            kw = self.without_edges()
            database_oid = 0 if scope is roles.SettingScope.ROLE else 100
            config = ('timezone=UTC',)
            phases = []
            for s in kw['sample']:
                states = tuple(dc.replace(r, config=config) if r.name == 'A' and database_oid == 0 else r for r in s.roles)
                phases.append(dc.replace(s, roles=states, settings=s.settings+(c.SettingState(database_oid,10,'A',config,'5'),)))
            kw['sample'] = tuple(phases)
            a = kw['authorization']
            expected = tuple(dc.replace(r,config=config) if r.name == 'A' and database_oid == 0 else r for r in a.roles)
            kw['authorization'] = dc.replace(a, roles=expected, database=dc.replace(a.database,settings=roles._settings(phases[1])))
            plan = roles.build_role_database_plan(**kw)
            self.assertEqual(plan.handover.expectation.name, 'A')
            self.assertIn(roles.ScopedSetting(scope, 'source_db' if database_oid else None, 'A', config), plan.settings)
            kw['authorization'] = dc.replace(kw['authorization'], roles=tuple(r for r in expected if r.name != 'A'), authentication=tuple(r for r in a.authentication if r.role_name != 'A'))
            with self.assertRaises(B2Error):
                roles.build_role_database_plan(**kw)

    def test_authorization_limit_stops_before_full_projection_or_expansion(self):
        a = fixture()['authorization']
        shared = tuple('guc_'+str(i)+'='+'x'*128 for i in range(32))
        settings = tuple(roles.ScopedSetting(roles.SettingScope.ROLE,None,'role_'+str(i),shared) for i in range(32))
        database = dc.replace(a.database,settings=settings)
        scalar_calls = []
        dumps = roles.json.dumps
        def scalar_only(value, *args, **kwargs):
            self.assertIn(type(value), (str,int,float,bool,type(None)), 'whole projection serialized')
            scalar_calls.append(value)
            return dumps(value,*args,**kwargs)
        with patch.object(roles,'MAX_AUTHORIZATION_BYTES',2048,create=True), patch.object(roles,'_value',side_effect=AssertionError('whole projection materialized')), patch.object(roles.json,'dumps',side_effect=scalar_only):
            with self.assertRaises(B2Error) as caught:
                dc.replace(a,database=database)
        self.assertEqual(str(caught.exception),'invalid_backup')
        self.assertLess(len(scalar_calls),100)

    def test_hash_streams_nested_records_with_unchanged_canonical_digest(self):
        import json
        values = (fixture()['authorization'], roles.build_role_database_plan(**fixture()),
                  {'转义\\\"\n': ('中😀', None, True, False, -12, 1.25, roles.SettingScope.ROLE)})
        for value in values:
            expected = hashlib.sha256(json.dumps(roles._value(value),sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
            with patch.object(roles,'_value',side_effect=AssertionError('whole tree materialized')):
                self.assertEqual(roles._hash(value),expected)


    def test_authorization_exact_byte_limit_preserves_existing_whitespace_count(self):
        import json
        a = fixture()['authorization']
        a = dc.replace(a, restore_id='中😀\\\"\n')
        size = len(json.dumps(roles._value(a),ensure_ascii=False).encode())
        with patch.object(roles,'MAX_AUTHORIZATION_BYTES',size):
            self.assertEqual(dc.replace(a),a)
        with patch.object(roles,'MAX_AUTHORIZATION_BYTES',size-1):
            with self.assertRaises(B2Error) as caught:
                dc.replace(a)
        self.assertEqual(str(caught.exception),'invalid_backup')
        self.assertEqual(roles.MAX_AUTHORIZATION_BYTES,8*1024*1024)

    def test_long_scalar_encoding_is_fragmented_and_digest_exact(self):
        import json
        value = {'x': ('中😀\\\"\n'*1500,)}
        expected = hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
        dumps = roles.json.dumps
        lengths = []
        def bounded_scalar(value,*args,**kwargs):
            self.assertIs(type(value),str)
            lengths.append(len(value))
            self.assertLessEqual(len(value),1024)
            return dumps(value,*args,**kwargs)
        with patch.object(roles.json,'dumps',side_effect=bounded_scalar):
            self.assertEqual(roles._hash(value),expected)
        self.assertGreater(len(lengths),3)

    def test_incidental_bootstrap_even_with_policy_cannot_be_auto_selected(self):
        kw = self.without_edges()
        with self.assertRaises(B2Error) as caught:
            roles.build_role_database_plan(**kw)
        self.assertEqual(str(caught.exception),'invalid_backup')
