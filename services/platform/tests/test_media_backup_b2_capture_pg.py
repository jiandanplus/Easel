"""Actual owned PG16 snapshots/dumps. Synthetic identity DB is not Logto proof."""
import asyncio,hashlib,os
from pathlib import Path
import pytest
from b2_owned_pg import _owned_pg,identity_fixture

def test_owned_role_password_rotation_metadata_without_docker():
    """Metadata primitive only; no fake claim of a complete capture barrier."""
    import psycopg,secrets
    from psycopg import sql
    from platform_service.media_backup_b2.capture import role_credential_inventory
    owned=_owned_pg(16);env=next(owned)
    try:
        names=tuple(env.roles.values())
        with psycopg.connect(env.base,autocommit=True) as con:
            before=role_credential_inventory(con,names)
            con.execute(sql.SQL('ALTER ROLE {} PASSWORD {}').format(sql.Identifier(env.roles['runtime']),sql.Literal(secrets.token_urlsafe(32))))
            after=role_credential_inventory(con,names)
            assert before!=after and len(after)==3
    finally:owned.close()

def test_actual_keeper_restart_has_different_transaction_identity():
    import psycopg
    from platform_service.media_backup_b2.capture import keeper_identity
    owned=_owned_pg(16);env=next(owned)
    try:
        with psycopg.connect(env.dsn('owner'),autocommit=True) as con:
            con.execute('BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY')
            con.execute('SELECT pg_export_snapshot()');before=keeper_identity(con)
            con.execute('ROLLBACK');con.execute('BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY')
            after=keeper_identity(con)
            assert before!=after
    finally:owned.close()

def test_actual_logto_public_catalog_without_docker():
    """Pinned source catalog component; does not claim a completed data dump."""
    import json,stat,psycopg
    from platform_service.media_backup_b2.capture import read_source_catalog
    from platform_service.media_backup_b2.profiles import LOGTO_145
    private,_=identity_fixture()
    dsn=private['dsn']
    with psycopg.connect(dsn,connect_timeout=3,options='-c statement_timeout=3000',autocommit=True) as con:
        con.execute('BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY')
        catalog=read_source_catalog(con,LOGTO_145)
        tables={r[1] for r in catalog['columns'] if r[0]=='public' and r[2]=='r'}
        assert len(tables)==79 and len(catalog['enums'])==8
        tenant_columns=[r for r in catalog['columns'] if r[0:2]==['public','tenants']]
        assert tenant_columns and all(row[11] is True for row in tenant_columns)
        assert catalog['policies'] and catalog['indexes'] and catalog['functions']

def test_foreign_owned_role_grant_cannot_be_omitted_from_inventory():
    import psycopg
    from psycopg import sql
    from uuid import uuid4
    from platform_service.media_backup_b2.capture import roles_inventory
    from platform_service.media_backup_b2.contracts import B2Error
    owned=_owned_pg(16);env=next(owned);extra='b2capture_unknown_'+uuid4().hex[:16]
    try:
        with psycopg.connect(env.base,autocommit=True) as admin:
            admin.execute(sql.SQL('CREATE ROLE {} NOLOGIN').format(sql.Identifier(extra)))
        with psycopg.connect(env.dsn('owner'),autocommit=True) as con:
            roles_inventory(con,tuple(env.roles.values()))
            con.execute(sql.SQL('GRANT INSERT ON public.users TO {}').format(sql.Identifier(extra)))
            with pytest.raises(B2Error):roles_inventory(con,tuple(env.roles.values()))
    finally:
        owned.close()
        with psycopg.connect(env.base,autocommit=True) as admin:admin.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(extra)))

def test_profile_catalog_rejects_unversioned_extra_column():
    import psycopg
    from platform_service.media_backup_b2.capture import read_source_catalog,validate_profile_catalog
    from platform_service.media_backup_b2.profiles import PUBLIC_016
    from platform_service.media_backup_b2.contracts import B2Error
    owned=_owned_pg(16);env=next(owned)
    try:
        with psycopg.connect(env.dsn('owner'),autocommit=True) as con:
            validate_profile_catalog(PUBLIC_016,read_source_catalog(con,PUBLIC_016))
            con.execute('ALTER TABLE public.users ADD COLUMN unversioned_column text')
            with pytest.raises(B2Error):validate_profile_catalog(PUBLIC_016,read_source_catalog(con,PUBLIC_016))
    finally:owned.close()

def test_synthetic_complete_raw_rows_match_actual_pinned_columns():
    import psycopg
    from b2_capture_rows import artifact_rows
    from platform_service.media_backup_b2.capture import read_source_catalog
    from platform_service.media_backup_b2.profiles import PUBLIC_016
    from platform_service.media_backup_b2.source_facts import validate_raw_relations
    owned=_owned_pg(16);env=next(owned)
    try:
        with psycopg.connect(env.dsn('owner'),autocommit=True) as con:catalog=read_source_catalog(con,PUBLIC_016)
        columns={name:{r[6] for r in catalog['columns'] if r[0]=='public' and r[1]==name} for name in PUBLIC_016.tables}
        for kwargs in ({},{'terminal':True},{'terminal':True,'missing':True}):
            rows,_=artifact_rows(**kwargs)
            validate_raw_relations(rows,catalog)
            for table,values in rows.items():
                for row in values:assert row.keys()==columns[table],table
    finally:owned.close()

def test_raw_rows_use_actual_bounded_named_server_cursors():
    import psycopg,time
    from platform_service.media_backup_b2.capture import _BudgetConnection,_raw_rows
    from platform_service.media_backup_b2.profiles import PUBLIC_016
    owned=_owned_pg(16);env=next(owned);observed=[]
    try:
        with psycopg.connect(env.dsn('owner'),autocommit=True) as raw:
            con=_BudgetConnection(raw,time.monotonic_ns()+20_000_000_000)
            original=con.cursor
            def tracked(*args,**kwargs):
                cursor=original(*args,**kwargs);observed.append(type(cursor).__name__);return cursor
            con.cursor=tracked
            rows=_raw_rows(con,PUBLIC_016)
            assert len(rows['schema_migrations'])==16
            assert len(observed)==len(PUBLIC_016.tables) and set(observed)=={'ServerCursor'}
    finally:owned.close()

@pytest.fixture
def capture_databases(request):
    first=_owned_pg(16);second=None
    try:
        central=next(first)
        if request.node.callspec.params.get('fault')=='logto':
            private,roles=identity_fixture()
            class Identity:
                def __repr__(self):return '<OwnedSyntheticLogto145>'
                def dsn(self,kind):assert kind=='owner';return private['dsn']
            identity=Identity();identity.roles={'owner':roles['bootstrap'],**{str(i):name for i,name in enumerate(roles['tenant_roles'])}}
        else:second=_owned_pg(16);identity=next(second)
        yield central,identity
    finally:
        first.close()
        if second is not None:second.close()

@pytest.mark.asyncio
@pytest.mark.parametrize('fault',['control_sequence_before_capture','control_extra_audit_before_capture','audit_transition','attested_capture','control_sequence_gap','control_sequence_after_cas','control_wrong_cas','control_extra_audit','control_settings','control_inactive_worker','config','sequence_after','keeper','roles_after','password_after','same_snapshot','wrong_snapshot','sequence_during','facts','cancel_thread','logto','preflight_cancel','late_bind','late_capture','checkpoint_restart','capacity','private_owner','worker_domain','missing_domain','wrong_domain'])
async def test_owned_real_snapshot_capture_and_config_postcondition(capture_databases,tmp_path,fault):
    import psycopg
    from platform_service.media_backup_b2.capture import OwnedInputs,ChildRunner,capture_source,_schema
    from platform_service.media_backup_b2.bundles import ConfigInputs
    from platform_service.media_backup_b2.profiles import PUBLIC_016,LOGTO_145
    from platform_service.media_backup_b2.contracts import B2Error
    from platform_service.media_maintenance.schema import SchemaProfile,install_control_schema
    from platform_service.media_maintenance.control import DatabaseControl
    from platform_service.media_maintenance.filesystem import ControlLock
    central,identity=capture_databases
    identity_profile=LOGTO_145 if fault=='logto' else PUBLIC_016
    profile=SchemaProfile('public_001_016','public',central.roles['runtime'],central.roles['controller'],tuple(central.migrations))
    async with central.connection('owner') as con:
        await install_control_schema(con,profile);await con.commit()
    owner=(os.getuid(),os.getgid());fds=[];locks=exclusive=inputs=configs=None;worker_source=None
    try:
        for name in ('media','control','job','config'):(tmp_path/name).mkdir(mode=0o700)
        lock=tmp_path/'control/media.lock';lock.write_bytes(b'');lock.chmod(0o600)
        config=tmp_path/'config/maintenance';config.write_bytes(b'synthetic-only');config.chmod(0o600)
        media_fd,control_fd,job_fd,config_fd=[os.open(tmp_path/n,os.O_RDONLY|os.O_DIRECTORY) for n in ('media','control','job','config')];fds=[media_fd,control_fd,job_fd,config_fd]
        configs=ConfigInputs(config_fd,{'central':{'maintenance':'maintenance'},'identity':{},'media':{}},owner=owner)
        locks=ControlLock(control_fd,'media.lock',owner=owner)
        control=DatabaseControl(central.factory('controller'),locks=locks)
        expected={}
        for kind,env in (('central',central),('identity',identity)):
            with psycopg.connect(env.dsn('owner'),autocommit=True) as con:expected[kind]=_schema(con,PUBLIC_016 if kind=='central' else identity_profile)
        inputs=OwnedInputs(central.dsn('owner'),identity.dsn('owner'),identity_profile,tuple(central.roles.values()),tuple(identity.roles.values()),media_fd,job_fd,configs,owner,None,expected,256*1024**2)
        from psycopg.conninfo import make_conninfo,conninfo_to_dict
        inputs.role_admin_dsns={'central':make_conninfo(central.base,dbname=central.name),
            'identity':make_conninfo(central.base,dbname=conninfo_to_dict(identity.dsn('owner'))['dbname'])}
        if fault=='logto':
            from psycopg.conninfo import conninfo_to_dict
            inputs.administrative_grantors=(conninfo_to_dict(central.base)['user'],)
        from platform_service.media_maintenance.identity import host_identity
        if fault=='attested_capture':
            from platform_service.media_maintenance.identity import worker_identity
            from platform_service.media_maintenance.filesystem import SourceRoot
            runtime=DatabaseControl(central.factory('runtime'),locks=locks)
            worker=worker_identity();worker_source=SourceRoot(media_fd,owner=owner)
            await runtime.register_worker(worker);await runtime.attest_worker(worker,worker_source)
            historical=worker_identity();await runtime.register_worker(historical);await runtime.unregister_worker(historical)
        if fault in {'worker_domain','missing_domain','wrong_domain'}:
            from platform_service.media_maintenance.identity import worker_identity
            from platform_service.media_maintenance.filesystem import SourceRoot
            runtime=DatabaseControl(central.factory('runtime'),locks=locks);worker=worker_identity()
            await runtime.register_worker(worker)
            if fault!='missing_domain':
                fd=media_fd
                if fault=='wrong_domain':
                    other=tmp_path/'other-media';other.mkdir(mode=0o700);fd=os.open(other,os.O_RDONLY|os.O_DIRECTORY);fds.append(fd)
                worker_source=SourceRoot(fd,owner=owner);await runtime.attest_worker(worker,worker_source)
            if fault!='worker_domain':
                with pytest.raises(B2Error):inputs.preflight(PUBLIC_016,locks,host_identity=host_identity())
                return
        if fault=='private_owner':
            inputs.private_owner=(os.getuid()+1,os.getgid())
            with pytest.raises(B2Error):inputs.preflight(PUBLIC_016,locks,host_identity=host_identity())
            return
        if fault=='capacity':
            inputs.reserve_bytes=1
            with pytest.raises(B2Error):inputs.preflight(PUBLIC_016,locks,host_identity=host_identity())
            return
        if fault=='preflight_cancel':
            import threading
            from platform_service.media_backup_b2.capture import preflight_async
            entered=threading.Event();release=threading.Event();original_verify=configs.verify
            def blocked():entered.set();release.wait(3);return original_verify()
            configs.verify=blocked
            task=asyncio.create_task(preflight_async(inputs,PUBLIC_016,locks,host_identity=host_identity()))
            try:
                assert await asyncio.to_thread(entered.wait,2)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):await task
                with pytest.raises(B2Error):inputs.close()
                release.set()
                for _ in range(200):
                    if inputs._active_work==0:break
                    await asyncio.sleep(.01)
                assert inputs._active_work==0 and inputs._work_unknown
            finally:release.set();configs.verify=original_verify
            return
        original=inputs.preflight(PUBLIC_016,locks,host_identity=host_identity())
        binding=await control.begin_window(original);exclusive=await control.acquire_window_exclusive(binding)
        if fault=='late_bind':
            import time
            with pytest.raises(B2Error):inputs.bind_window(exclusive,phase_deadline_ns=time.monotonic_ns()-1)
            return
        phase_deadline=control.phase_deadline_ns(binding)
        if fault=='late_capture':
            import time
            phase_deadline=min(phase_deadline,time.monotonic_ns()+20_000_000)
        inputs.bind_window(exclusive,phase_deadline_ns=phase_deadline)
        if fault=='worker_domain':
            assert len(inputs._worker_registry)==1 and inputs._worker_registry[0]['domain']==inputs._domain
            with inputs._connect('central') as con:inputs._control(con,{'CAPTURING'})
            return
        if fault=='checkpoint_restart':
            from platform_service.media_backup_b2.capture import keeper_identity
            # Actual keeper/checkpoint component with real server window+EX;
            # no dump or source receipt is fabricated by this setup.
            for kind in ('central','identity'):
                keeper=inputs._connect(kind)
                keeper.execute('BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY')
                token=keeper.execute('SELECT pg_export_snapshot()').fetchone()[0]
                inputs._keepers[kind]=keeper;inputs._keeper_snapshots[kind]=token;inputs._keeper_stamps[kind]=keeper_identity(keeper)
            assert inputs.checkpoint()=={'keepers':2}
            keeper=inputs._keepers['central']
            keeper.raw.execute('ROLLBACK');keeper.raw.execute('BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY')
            with pytest.raises(B2Error):inputs.checkpoint()
            return
        container=central.container_id
        runner=ChildRunner(('/usr/local/bin/docker','exec','--env','PGUSER','--env','PGDATABASE','--env','PGPASSWORD',container,'pg_dump'),('/usr/local/bin/docker','exec','-i',container,'pg_restore'))
        if fault=='late_capture':
            # Wait against a correctly bound already-expired original budget;
            # no tool starts and capture may not reset it from current time.
            await asyncio.sleep(.03)
            with pytest.raises(B2Error):await capture_source(PUBLIC_016,exclusive,inputs,runner)
            assert not runner.children and exclusive.active_refs==0
            return
        original_run=runner.run;injected=False
        def inject(tool,args,**kwargs):
            nonlocal injected
            target=tool=='pg_dump' and '--format=custom' in args and not injected
            if target:
                injected=True
                if fault in {'same_snapshot','wrong_snapshot'}:
                    with psycopg.connect(central.dsn('owner'),autocommit=True) as con:con.execute("INSERT INTO public.users(id) VALUES('late-unrelated-text-user')")
                    if fault=='wrong_snapshot':args=[a for a in args if not a.startswith('--snapshot=')]
            result=original_run(tool,args,**kwargs)
            if target and fault=='sequence_during':
                with psycopg.connect(central.dsn('owner'),autocommit=True) as con:con.execute("SELECT nextval('public.credit_entries_id_seq')")
            return result
        runner.run=inject
        if fault in {'control_sequence_before_capture','control_extra_audit_before_capture'}:
            with psycopg.connect(central.dsn('owner'),autocommit=True) as con:
                if fault=='control_sequence_before_capture':con.execute("SELECT nextval('easel_backup_control.audit_id_seq')")
                else:con.execute("INSERT INTO easel_backup_control.audit(action,window_id,epoch,details) VALUES('unexpected',%s,%s,'{}')",(binding.window_id,binding.epoch))
            with pytest.raises(B2Error):await capture_source(PUBLIC_016,exclusive,inputs,runner)
            return
        if fault=='cancel_thread':
            import threading
            from platform_service.media_backup_b2 import capture as capture_module
            entered=threading.Event();release=threading.Event();original_rows=capture_module._raw_rows
            def blocked(*args):entered.set();release.wait(3);return original_rows(*args)
            capture_module._raw_rows=blocked
            task=asyncio.create_task(capture_source(PUBLIC_016,exclusive,inputs,runner))
            try:
                assert await asyncio.to_thread(entered.wait,2)
                assert not task.done()
                task.cancel()
                with pytest.raises(asyncio.CancelledError):await task
                assert exclusive.active_refs>0
                with pytest.raises(B2Error):inputs.close()
                release.set()
                for _ in range(200):
                    if exclusive.active_refs==0:break
                    await asyncio.sleep(.01)
                assert exclusive.active_refs==0
            finally:
                release.set();capture_module._raw_rows=original_rows
                if not task.done():await task
            return
        if fault in {'wrong_snapshot','sequence_during'}:
            with pytest.raises(B2Error):await capture_source(PUBLIC_016,exclusive,inputs,runner)
            assert exclusive.active_refs==0 and not runner.children
            return
        captured=await capture_source(PUBLIC_016,exclusive,inputs,runner)
        assert len(captured.fixed_fds)==7
        assert os.pread(captured.fixed_fds['db/central.dump'],5,0)==b'PGDMP'
        assert inputs.verify_after(captured)['stable_side_effects_sha256']
        assert inputs.checkpoint()=={'keepers':2}
        if fault in {'audit_transition','attested_capture'}:
            from platform_service.media_backup_b2.bundles import decode_bundle
            fd=captured.fixed_fds['config/central'];bundle=decode_bundle(os.pread(fd,os.fstat(fd).st_size,0),'central')
            history=bundle['source_facts']['maintenance_snapshot']
            assert history['snapshot']==inputs._keeper_snapshots['central']
            assert history['tables']['control'][0]['state']=='CAPTURING'
            assert set(history['tables'])=={'schema_migrations','settings','control','workers','worker_attestations','permits','audit'}
            assert 'CREATE TABLE easel_backup_control.audit (' in history['schema_sql']
            if fault=='attested_capture':
                import copy,hashlib
                from platform_service.media_backup_b2.bundles import canonical_bytes
                from platform_service.media_backup_b2.source_facts import validate_source_facts
                assert len(history['tables']['workers'])==2 and len(history['tables']['worker_attestations'])==1
                assert len(bundle['source_facts']['workers']['registry'])==1
                altered=copy.deepcopy(bundle['source_facts']);altered['workers']['registry']=[]
                altered['workers']['registry_sha256']=hashlib.sha256(canonical_bytes([])).hexdigest()
                with pytest.raises(B2Error):validate_source_facts(canonical_bytes(altered))
            dumped=runner.run('pg_restore',['--schema-only','--file=-'],exclusive=exclusive,deadline=inputs._deadline,input_fd=captured.fixed_fds['db/central.dump'])
            assert b'CREATE TABLE public.media_jobs (' in dumped and b'CREATE TABLE easel_backup_control.' not in dumped
            await control.transition(binding,expected_state='CAPTURING',next_state='VERIFYING',result={'objects':'durably_reopened'})
            assert inputs.verify_after(captured)['stable_side_effects_sha256']
            return
        if fault.startswith('control_'):
            if fault in {'control_sequence_after_cas','control_wrong_cas'}:
                await control.transition(binding,expected_state='CAPTURING',next_state='VERIFYING',result={'objects':'unknown' if fault=='control_wrong_cas' else 'durably_reopened'})
            with psycopg.connect(central.dsn('owner'),autocommit=True) as con:
                if fault in {'control_sequence_gap','control_sequence_after_cas'}:con.execute("SELECT nextval('easel_backup_control.audit_id_seq')")
                elif fault=='control_extra_audit':con.execute("INSERT INTO easel_backup_control.audit(action,window_id,epoch,details) VALUES('unexpected',%s,%s,'{}')",(binding.window_id,binding.epoch))
                elif fault=='control_settings':con.execute("UPDATE easel_backup_control.settings SET profile='local_001_020'")
                elif fault=='control_inactive_worker':con.execute("INSERT INTO easel_backup_control.workers VALUES('22222222-2222-4222-8222-222222222222','33333333-3333-4333-8333-333333333333','{}',%s,false)",('f'*64,))
            with pytest.raises(B2Error):inputs.verify_after(captured)
            return
        if fault=='logto':
            from platform_service.media_backup_b2.bundles import decode_bundle
            fd=captured.fixed_fds['config/central'];bundle=decode_bundle(os.pread(fd,os.fstat(fd).st_size,0),'central')
            assert bundle['source_facts']['schema']['identity']['profile']=='logto-1.45.0'
            assert len(inputs._before['schemas']['identity']['enums'])==8
            assert inputs.verify_after(captured)
            return
        if fault=='config':config.write_bytes(b'changed-synthetic')
        elif fault=='sequence_after':
            with psycopg.connect(central.dsn('owner'),autocommit=True) as con:con.execute("SELECT nextval('public.credit_entries_id_seq')")
        elif fault=='keeper':inputs._keepers['central'].close()
        elif fault=='roles_after':
            from psycopg import sql
            with psycopg.connect(central.base,autocommit=True) as con:con.execute(sql.SQL('ALTER ROLE {} NOINHERIT').format(sql.Identifier(central.roles['owner'])))
        elif fault=='password_after':
            import secrets
            from psycopg import sql
            with psycopg.connect(central.base,autocommit=True) as con:con.execute(sql.SQL('ALTER ROLE {} PASSWORD {}').format(sql.Identifier(central.roles['runtime']),sql.Literal(secrets.token_urlsafe(32))))
        elif fault=='same_snapshot':
            from platform_service.media_backup_b2.bundles import decode_bundle
            fd=captured.fixed_fds['config/central'];bundle=decode_bundle(os.pread(fd,os.fstat(fd).st_size,0),'central')
            assert not any(r['id']=='late-unrelated-text-user' for r in bundle['source_facts']['rows']['users'])
            assert inputs.verify_after(captured)
            return
        elif fault=='facts':
            from platform_service.media_backup_b2.source_facts import verify_archived_bundles
            from platform_service.media_backup_b2.bundles import decode_bundle,encode_bundle
            bundles={name:os.pread(fd,os.fstat(fd).st_size,0) for name,fd in captured.fixed_fds.items() if name.startswith('config/')}
            assert verify_archived_bundles(bundles,captured.plan['files'],captured.plan)['source_facts_sha256']==captured.source_facts_sha256
            central_bundle=decode_bundle(bundles['config/central'],'central');central_bundle['source_facts']['dumps'][0]['sha256']='a'*64
            bundles['config/central']=encode_bundle('central',central_bundle['items'],source_facts=central_bundle['source_facts'])
            with pytest.raises(B2Error):verify_archived_bundles(bundles,captured.plan['files'],captured.plan)
            return
        with pytest.raises(B2Error):inputs.verify_after(captured)
        assert exclusive.active_refs==0 and not runner.children
    finally:
        if inputs is not None:inputs.close()
        if exclusive is not None:exclusive.close()
        if locks is not None:locks.close()
        if configs is not None:configs.close()
        if worker_source is not None:worker_source.close()
        for fd in fds:os.close(fd)
