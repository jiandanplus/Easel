"""Explicitly selected owned synthetic test instance; no discovery or fallback."""
from contextlib import asynccontextmanager
import hashlib,os,re,secrets
from uuid import uuid4
import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict,make_conninfo
import pytest
from platform_service.db import MIGRATIONS_DIR
from b2_test_authority import selected_authority,read_identity_json

def identity_fixture():
    """Validate fixed synthetic identity siblings against the cached authority."""
    authority=selected_authority()
    private=read_identity_json(authority,'identity-dsn.private.json')
    roles=read_identity_json(authority,'identity-roles.safe.json')
    try:
        if set(private)!={'dsn'} or type(private['dsn']) is not str:raise ValueError
        if set(roles)!={'bootstrap','tenant_roles'} or type(roles['tenant_roles']) is not list:raise ValueError
        names=[roles['bootstrap'],*roles['tenant_roles']]
        if any(type(name) is not str or re.fullmatch('[A-Za-z_][A-Za-z0-9_]{0,62}',name) is None for name in names):raise ValueError
        if len(set(names))!=len(names):raise ValueError
        parsed=conninfo_to_dict(private['dsn'])
        allowed={'host','hostaddr','port','dbname','user','password','connect_timeout','sslmode','application_name'}
        if set(parsed)-allowed:raise ValueError
        if (parsed.get('host')!=authority.host or parsed.get('hostaddr')!=authority.host
                or parsed.get('port')!=str(authority.port)
                or parsed.get('dbname')!='easel_s1_test_logto_'+authority.nonce
                or parsed.get('user')!=roles['bootstrap'] or not parsed.get('password')
                or parsed.get('connect_timeout','5')!='5'
                or parsed.get('sslmode','disable')!='disable'):raise ValueError
    except Exception:
        raise ValueError('invalid owned B2 synthetic identity fixture') from None
    return private,roles

class OwnedPG:
    def __repr__(self):return "<OwnedB2PG>"
    def __init__(self,base,name,roles,passwords,migrations,container_id):
        self.base=base;self.name=name;self.roles=roles;self.passwords=passwords;self.migrations=migrations
        self.container_id=container_id
    def dsn(self,role):
        return make_conninfo(self.base,dbname=self.name,user=self.roles[role],password=self.passwords[role])
    @asynccontextmanager
    async def connection(self,role):
        try:connection=await psycopg.AsyncConnection.connect(self.dsn(role),autocommit=False)
        except Exception:raise RuntimeError("owned B2 role connection unavailable") from None
        try:yield connection
        finally:await connection.close()
    def factory(self,role):return lambda:self.connection(role)
def _owned_pg(count):
    authority=selected_authority()
    for key in tuple(os.environ):
        if key.startswith("PG") or key.startswith("EASEL"):os.environ.pop(key,None)
    base=make_conninfo(host=authority.host,hostaddr=authority.host,port=str(authority.port),dbname=authority.database,user=authority.user,password=authority.password,connect_timeout="5",sslmode="disable",application_name="easel_b2_owned_tests")
    suffix=uuid4().hex[:16];name="easel_s1_test_b2r_"+suffix
    roles={kind:"b2r_"+kind+"_"+suffix for kind in ("owner","runtime","controller")}
    passwords={kind:secrets.token_urlsafe(32) for kind in roles}
    migrations=[];created=[];db_created=False
    try:
        with psycopg.connect(base,autocommit=True) as admin:
            for kind,role in roles.items():
                admin.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(sql.Identifier(role),sql.Literal(passwords[kind])));created.append(role)
            admin.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(sql.Identifier(name),sql.Identifier(roles["owner"])));db_created=True
        env=OwnedPG(base,name,roles,passwords,migrations,authority.container_id)
        with psycopg.connect(env.dsn("owner")) as owner:
            owner.execute("CREATE TABLE public.schema_migrations(version text PRIMARY KEY,checksum text NOT NULL)")
            for path in sorted(MIGRATIONS_DIR.glob("*.sql"))[:count]:
                raw=path.read_bytes();owner.execute(raw.decode(),prepare=False);digest=hashlib.sha256(raw).hexdigest()
                owner.execute("INSERT INTO public.schema_migrations VALUES(%s,%s)",(path.name,digest));migrations.append((path.name,digest))
            owner.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {},{}").format(sql.Identifier(roles["runtime"]),sql.Identifier(roles["controller"])))
            owner.execute(sql.SQL("GRANT SELECT,INSERT,UPDATE,DELETE,TRUNCATE ON ALL TABLES IN SCHEMA public TO {}").format(sql.Identifier(roles["runtime"])))
            owner.execute(sql.SQL("GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA public TO {}").format(sql.Identifier(roles["runtime"])))
        yield env
    finally:
        with psycopg.connect(base,autocommit=True) as admin:
            if db_created:
                admin.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s AND pid<>pg_backend_pid()",(name,))
                admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))
            for role in reversed(created):admin.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))

@pytest.fixture(scope="session")
def b2_pg():yield from _owned_pg(20)
@pytest.fixture(scope="session")
def b2_m1_pg():yield from _owned_pg(16)
