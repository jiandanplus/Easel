"""Real generation domain attestation uses retained kernel FD identities."""
import os,sys
from uuid import uuid4
from contextlib import asynccontextmanager
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).parents[1]/'src'))
from b2_owned_pg import b2_pg
from test_media_maintenance_pg import installed,local_locks
from platform_service.media_maintenance.filesystem import SourceRoot,domain_attestation,verify_domain_rows
from platform_service.media_maintenance.runtime import RuntimeGeneration,operation,physical_call
from platform_service.media_maintenance.contracts import WorkerIdentity,OperationBinding,MaintenanceError
from platform_service.media_maintenance.control import DatabaseControl
from platform_service.media_maintenance.permits import admit_operation


def source(tmp_path,name='source'):
    directory=tmp_path/name;directory.mkdir(mode=0o700)
    anchor=os.open(tmp_path,os.O_RDONLY|os.O_DIRECTORY)
    try:return directory,SourceRoot.from_anchor(anchor,name)
    finally:os.close(anchor)


class DB:
    def __init__(self,env,role="runtime"):self.env,self.role=env,role
    @asynccontextmanager
    async def connection(self):
        async with self.env.connection(self.role) as c:
            from psycopg.rows import dict_row
            c.row_factory=dict_row
            async with c.transaction():yield c


@pytest.mark.asyncio
async def test_registered_without_attestation_cannot_mint_physical_operation(b2_pg,tmp_path):
    await installed(b2_pg);fd,locks=local_locks(tmp_path);control=DatabaseControl(b2_pg.factory('runtime'),locks=locks)
    worker=WorkerIdentity(str(uuid4()),str(uuid4()),'synthetic-host','synthetic-boot','1:2');await control.register_worker(worker)
    try:
        with pytest.raises(MaintenanceError):await admit_operation(control,locks,OperationBinding('job','unattested-job','attempt',worker,None))
        path,root=source(tmp_path)
        try:
            async with b2_pg.connection('controller') as c:
                from psycopg.rows import dict_row
                c.row_factory=dict_row;rows=await(await c.execute('SELECT * FROM easel_backup_control.worker_domains WHERE active')).fetchall()
            with pytest.raises(MaintenanceError):verify_domain_rows(rows,locks,root)
        finally:root.close()
    finally:locks.close();os.close(fd)


@pytest.fixture
def domain_pg():
    from b2_owned_pg import _owned_pg
    yield from _owned_pg(20)


@pytest.mark.asyncio
@pytest.mark.parametrize('failure',[None,'other_source','other_control','parent_replaced','namespace_payload'])
async def test_actual_attested_generation_rechecks_source_control_and_namespace(domain_pg,tmp_path,failure):
    b2_pg=domain_pg
    from psycopg.rows import dict_row
    from psycopg.types.json import Jsonb
    import psycopg
    await installed(b2_pg);fd,locks=local_locks(tmp_path);path,root=source(tmp_path)
    generation=RuntimeGeneration(DB(b2_pg),locks,source=root);permit=None
    try:
        await generation.start()
        async with b2_pg.connection('controller') as c:
            c.row_factory=dict_row;rows=await(await c.execute('SELECT * FROM easel_backup_control.worker_domains WHERE active')).fetchall()
        assert rows[0]['domain']==domain_attestation(locks,root)
        if failure=='other_source':
            other_path,other=source(tmp_path,'other-source')
            try:
                with pytest.raises(MaintenanceError):verify_domain_rows(rows,locks,other)
            finally:other.close()
        elif failure=='other_control':
            other=tmp_path/'other-control';other.mkdir(mode=0o700);(other/'media.lock').write_bytes(b'');(other/'media.lock').chmod(0o600)
            anchor=os.open(other,os.O_RDONLY|os.O_DIRECTORY)
            from platform_service.media_maintenance.filesystem import ControlLock
            alternate=ControlLock(anchor,'media.lock',owner=(os.getuid(),os.getgid()))
            try:
                with pytest.raises(MaintenanceError):verify_domain_rows(rows,alternate,root)
            finally:alternate.close();os.close(anchor)
        elif failure=='namespace_payload':
            domain=domain_attestation(locks,root);domain['source_namespace_sha256']='0'*64
            async with b2_pg.connection('runtime') as c:
                with pytest.raises(psycopg.errors.CheckViolation):await c.execute('SELECT easel_backup_control.attest_worker(%s,%s,%s)',(Jsonb(__import__('dataclasses').asdict(generation.identity)),generation.control._workers[(generation.identity.worker_id,generation.identity.generation)],Jsonb(domain)))
        else:
            from test_media_credits import setup
            service,_,_,context,*_=await setup(DB(b2_pg,'owner'),maintenance=generation)
            permit=await generation.acquire('upload',context['invocation_id'])
            from platform_service.media.artifacts import ArtifactStore,inspect_image
            from test_media_artifacts import image_bytes
            raw=image_bytes();info=inspect_image(raw);key=uuid4().hex+uuid4().hex
            # The path/domain gate is exercised after a real original-nonce
            # stage; an earlier missing WriteFence cannot satisfy this test.
            with operation(permit):await service.stage_blob(context,{'storage_key':key,**info})
            if failure=='parent_replaced':path.rename(path.with_name('old-source'));path.mkdir(mode=0o700)
            if failure is not None:
                with pytest.raises(MaintenanceError):await physical_call(ArtifactStore(path)._put_verified,raw,info,storage_key=key,permit=permit)
                assert not list(path.iterdir())
            else:
                stored=await physical_call(ArtifactStore(path)._put_verified,raw,info,storage_key=key,permit=permit)
                assert (path/stored['storage_key']).is_file()
    finally:
        if permit is not None and not permit._closed:
            await generation.unknown(permit)
            if not permit.physical_count:permit.release_failed_physical()
        root.close();locks.close();os.close(fd)



@pytest.mark.asyncio
async def test_other_actual_store_root_rejected_after_valid_same_nonce_staging(tmp_path):
    from b2_owned_media import owned_media
    from test_media_artifacts import image_bytes
    from platform_service.media.artifacts import ArtifactStore,inspect_image
    async with owned_media(tmp_path) as value:
        raw=image_bytes();info=inspect_image(raw);key=uuid4().hex+uuid4().hex;metadata={'storage_key':key,**info}
        permit=await value['generation'].acquire('upload',value['context']['invocation_id'])
        with operation(permit):await value['service'].stage_blob(value['context'],metadata)
        other=tmp_path/'wrong-store';other.mkdir(mode=0o700)
        with pytest.raises(MaintenanceError):await physical_call(ArtifactStore(other)._put_verified,raw,info,storage_key=key,permit=permit)
        assert not list(other.iterdir()) and not list((tmp_path/'media').iterdir())
