"""Explicit trusted synthetic media factory: fresh owned DB and real permits."""
from contextlib import asynccontextmanager
import os
from pathlib import Path
from uuid import uuid4
from psycopg.rows import dict_row
from b2_owned_pg import _owned_pg
from platform_service.media_maintenance.schema import SchemaProfile,install_control_schema
from platform_service.media_maintenance.filesystem import ControlLock,SourceRoot
from platform_service.media_maintenance.runtime import RuntimeGeneration,GuardedDatabase,operation


class DB:
    def __init__(self,env,role):self.env,self.role=env,role
    @asynccontextmanager
    async def connection(self):
        async with self.env.connection(self.role) as connection:
            connection.row_factory=dict_row
            async with connection.transaction():yield connection


@asynccontextmanager
async def owned_media(tmp_path,*,definition=None):
    owned=_owned_pg(20);env=next(owned);locks=None;fd=None;generation=None;source=None
    try:
        profile=SchemaProfile('local_001_020','public',env.roles['runtime'],env.roles['controller'],tuple(env.migrations))
        async with env.connection('owner') as c:await install_control_schema(c,profile);await c.commit()
        root=tmp_path/'control';root.mkdir(mode=0o700);(root/'media.lock').write_bytes(b'');(root/'media.lock').chmod(0o600)
        fd=os.open(root,os.O_RDONLY|os.O_DIRECTORY);locks=ControlLock(fd,'media.lock',owner=(os.getuid(),os.getgid()))
        source_path=tmp_path/'media';source_path.mkdir(mode=0o700)
        anchor=os.open(tmp_path,os.O_RDONLY|os.O_DIRECTORY)
        try:source=SourceRoot.from_anchor(anchor,'media')
        finally:os.close(anchor)
        database=DB(env,'runtime');generation=RuntimeGeneration(database,locks,source=source);await generation.start()
        from test_media_credits import setup
        service,ledger,scope,context,text,text_context=await setup(DB(env,'owner'),media_definition=definition,maintenance=generation)
        from platform_service.identity import IdentityService
        from platform_service.credits import CreditLedger
        service.db=GuardedDatabase(database);service.identity=IdentityService(database,None);service.ledger=CreditLedger(service.db)
        yield {'env':env,'owner':DB(env,'owner'),'db':database,'generation':generation,'locks':locks,'source':source,'service':service,'ledger':ledger,'scope':scope,'context':context,'text':text,'text_context':text_context}
    finally:
        if generation is not None:
            for permit in generation.permits:
                if not permit._closed:
                    try:await generation.unknown(permit)
                    except Exception:pass
                    if not permit.physical_count:permit.release_failed_physical()
        if source is not None:source.close()
        if locks is not None:locks.close()
        if fd is not None:os.close(fd)
        try:next(owned)
        except StopIteration:pass


async def admitted(value,*,key='synthetic-job',request=None,bindings=None):
    from test_media_credits import REQUEST,BINDINGS
    generation=value['generation'];permit=await generation.acquire('job',str(uuid4()),str(uuid4()))
    with operation(permit):
        job=await value['service'].admit(value['context'],key=key,request=request or REQUEST,bindings=bindings or BINDINGS)
    return job,permit
