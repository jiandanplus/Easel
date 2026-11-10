"""Real owned PG, exported snapshots, tools, crypto, publication and fresh reader.

No transport, source-facts, coverage or filesystem identity substitution.
The local UID policy is explicitly synthetic; this is not root/Linux/B3 proof.
"""

import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import psycopg
from psycopg.conninfo import make_conninfo
import pytest

from b2_owned_pg import _owned_pg, identity_fixture
from media_backup_b2_fixture import synthetic_keys


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "count,nonempty,identity_kind",
    [
        (16, False, "public"),
        (20, False, "public"),
        (16, True, "public"),
        (20, True, "public"),
        (16, False, "logto"),
        (16, True, "logto"),
    ],
)
async def test_actual_capture_to_committed_group_and_fresh_private_restore(
    tmp_path, count, nonempty, identity_kind
):
    from platform_service.media_backup_b2.anchor import RootPolicy
    from platform_service.media_backup_b2.bundles import ConfigInputs
    from platform_service.media_backup_b2.capture import (
        OwnedInputs,
        ChildRunner,
        read_source_catalog,
    )
    from platform_service.media_backup_b2.pipeline import build_backup
    from platform_service.media_backup_b2.profiles import (
        PUBLIC_016,
        LOCAL_020,
        LOGTO_145,
    )
    from platform_service.media_maintenance.control import DatabaseControl
    from platform_service.media_maintenance.filesystem import ControlLock
    from platform_service.media_maintenance.schema import (
        SchemaProfile,
        install_control_schema,
    )

    owned = _owned_pg(count)
    central = next(owned)
    identity_owned = None
    if identity_kind == "logto":
        private_config, roles = identity_fixture()

        class Identity:
            def __repr__(self):
                return "<OwnedSyntheticLogto145>"

            def dsn(self, role):
                assert role == "owner"
                return private_config["dsn"]

        identity = Identity()
        identity.roles = {
            "owner": roles["bootstrap"],
            **{str(i): name for i, name in enumerate(roles["tenant_roles"])},
        }
        identity_profile = LOGTO_145
    else:
        identity_owned = _owned_pg(16)
        identity = next(identity_owned)
        identity_profile = PUBLIC_016
    fds = []
    locks = configs = inputs = policy = source_root = runtime = None
    artifact_sha = None
    profile = PUBLIC_016 if count == 16 else LOCAL_020
    uid = (os.getuid(), os.getgid())
    try:
        control_profile = SchemaProfile(
            profile.name,
            "public",
            central.roles["runtime"],
            central.roles["controller"],
            tuple(central.migrations),
        )
        async with central.connection("owner") as connection:
            await install_control_schema(connection, control_profile)
            await connection.commit()
        for name in (
            "media",
            "control",
            "stage",
            "config",
            "backups",
            "anchors",
            "restores",
        ):
            (tmp_path / name).mkdir(mode=0o700)
        (tmp_path / "control" / "media.lock").write_bytes(b"")
        (tmp_path / "control" / "media.lock").chmod(0o600)
        (tmp_path / "config" / "maintenance").write_bytes(
            b"synthetic-backup-integration"
        )
        (tmp_path / "config" / "maintenance").chmod(0o600)
        names = (
            "media",
            "control",
            "stage",
            "config",
            "backups",
            "anchors",
            "restores",
        )
        fds = [
            os.open(tmp_path / name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            for name in names
        ]
        media, control_fd, stage, config_fd, backup, anchor, restore = fds
        locks = ControlLock(control_fd, "media.lock", owner=uid)
        controller = DatabaseControl(central.factory("controller"), locks=locks)
        if nonempty:
            import base64
            import httpx
            from b2_owned_media import DB
            from test_media_credits import setup
            from test_media_artifacts import image_bytes
            from platform_service.media_maintenance.filesystem import SourceRoot
            from platform_service.media_maintenance.runtime import (
                RuntimeGeneration,
                GuardedDatabase,
            )
            from platform_service.identity import IdentityService
            from platform_service.credits import CreditLedger
            from platform_service.media_config import MediaSettings, MediaProvider
            from platform_service.media_runtime import MediaRuntime

            source_root = SourceRoot(media, owner=uid)
            database = DB(central, "runtime")
            generation = RuntimeGeneration(database, locks, source=source_root)
            await generation.start()
            service, _, scope, *_ = await setup(
                DB(central, "owner"), maintenance=generation
            )
            service.db = GuardedDatabase(database)
            service.identity = IdentityService(database, None)
            service.ledger = CreditLedger(service.db)
            calls = []

            async def handler(request):
                calls.append(request)
                return httpx.Response(
                    200,
                    json={
                        "data": [{"b64_json": base64.b64encode(image_bytes()).decode()}]
                    },
                    headers={"x-request-id": "synthetic-integration"},
                )

            settings = MediaSettings(
                mode="test",
                storage_root=tmp_path / "media",
                providers={
                    "fixture": MediaProvider(
                        "1",
                        "http://127.0.0.1:19991/v1",
                        "synthetic-private",
                        {"fixture-image": "synthetic-upstream"},
                    )
                },
            )
            runtime = MediaRuntime(
                database,
                service.identity,
                settings,
                service=service,
                maintenance=generation,
                transport=httpx.MockTransport(handler),
            )
            await runtime.start()
            token = await runtime.mint(
                scope,
                {
                    "model": "fixture-image",
                    "capability": "image.generate",
                    "invocation_key": "whole-integration",
                },
            )
            result = await runtime.submit(
                token["task_token"],
                "one-operation",
                {
                    "model": "fixture-image",
                    "capability": "image.generate",
                    "prompt": "synthetic source",
                    "assets": [],
                    "parameters": {"n": 1},
                },
            )
            assert (
                result["financial_state"] == "settled"
                and len(calls) == 1
                and len(result["artifacts"]) == 1
            )
            artifact_sha = result["artifacts"][0]["sha256"]
            await runtime.close()
            runtime = None
        configs = ConfigInputs(
            config_fd,
            {"central": {"maintenance": "maintenance"}, "identity": {}, "media": {}},
            owner=uid,
        )
        catalogs = {}
        for kind, env, source_profile in (
            ("central", central, profile),
            ("identity", identity, identity_profile),
        ):
            with psycopg.connect(env.dsn("owner"), autocommit=True) as connection:
                catalogs[kind] = read_source_catalog(connection, source_profile)
        inputs = OwnedInputs(
            central.dsn("owner"),
            identity.dsn("owner"),
            identity_profile,
            tuple(central.roles.values()),
            tuple(identity.roles.values()),
            media,
            stage,
            configs,
            uid,
            None,
            catalogs,
            512 * 1024**2,
        )
        inputs.role_admin_dsns = {
            "central": make_conninfo(central.base, dbname=central.name),
            "identity": make_conninfo(
                central.base,
                dbname=__import__(
                    "psycopg.conninfo", fromlist=["conninfo_to_dict"]
                ).conninfo_to_dict(identity.dsn("owner"))["dbname"],
            ),
        }
        if identity_kind == "logto":
            from psycopg.conninfo import conninfo_to_dict

            inputs.administrative_grantors = (conninfo_to_dict(central.base)["user"],)
        container = central.container_id
        runner = ChildRunner(
            (
                "/usr/local/bin/docker",
                "exec",
                "--env",
                "PGUSER",
                "--env",
                "PGDATABASE",
                "--env",
                "PGPASSWORD",
                container,
                "pg_dump",
            ),
            ("/usr/local/bin/docker", "exec", "-i", container, "pg_restore"),
        )
        public, private, _ = synthetic_keys()
        policy = RootPolicy.from_operator(backup, owner=uid)
        backup_id = str(uuid4())
        result = await build_backup(
            controller,
            profile,
            inputs,
            public,
            backup,
            anchor,
            private_pem=private,
            backup_id=backup_id,
            target="owned-integration",
            child_runner=runner,
            root_policy=policy,
        )
        assert (
            result["status"] == "backup_committed"
            and result["commit_persisted"] is True
        )
        assert result["window_state"] == "OPEN" and result["window_restored"] is True
        with psycopg.connect(central.dsn("owner"), autocommit=True) as connection:
            assert connection.execute(
                "SELECT state FROM easel_backup_control.control"
            ).fetchone() == ("OPEN",)
            assert connection.execute(
                "SELECT count(*) FROM easel_backup_control.permits"
            ).fetchone() == (0,)
            assert [
                row[0]
                for row in connection.execute(
                    "SELECT action FROM easel_backup_control.audit ORDER BY id"
                )
            ][-5:] == ["DRAINING", "CAPTURING", "VERIFYING", "RECOVERING", "OPEN"]
        assert not runner.children and not runner.unknown_refs
        key_file = tmp_path / "synthetic-private.pem"
        key_file.write_bytes(private)
        key_file.chmod(0o600)
        job = os.open(
            tmp_path / "backups" / backup_id,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
        )
        try:
            script = r"""
import json,os,sys
from pathlib import Path
from platform_service.media_backup_b2.anchor import RootPolicy,trusted_anchor_intent
from platform_service.media_backup_b2.reader import read_committed
from platform_service.media_backup_b2.self_verify import verify_archive_content
from platform_service.media_backup_b2.bundles import decode_bundle
from media_backup.closure import restore_archive
backup,anchor,job,restore=map(int,sys.argv[1:5])
key=Path(sys.argv[5]).read_bytes();backup_id,commit=sys.argv[6:8];nonempty=sys.argv[8]=='1'
with RootPolicy.from_operator(backup,owner=(os.getuid(),os.getgid())) as policy:
 with trusted_anchor_intent(anchor,policy=policy,backup_id=backup_id,target='owned-integration',commit_sha256=commit) as intent:
  plan,source=read_committed(intent,job,key)
  with source:
   checked=verify_archive_content(source,plan)
   assert source.verified
  plan,source=read_committed(intent,job,key)
  with source:
   receipt=restore_archive(source,restore,'verified',plan)
   assert source.verified and receipt['status']=='private_stage_restored_verified'
  config_fd=os.open('verified/config/central',os.O_RDONLY|os.O_NOFOLLOW,dir_fd=restore)
  try:
   size=os.fstat(config_fd).st_size
   facts=decode_bundle(os.pread(config_fd,size,0),'central')['source_facts']
  finally:os.close(config_fd)
  assert facts['maintenance_snapshot']['tables']['control'][0]['state']=='CAPTURING'
  assert [r['action'] for r in facts['maintenance_snapshot']['tables']['audit']][-2:]==['DRAINING','CAPTURING']
  assert facts['snapshots']['cross_database_atomic'] is False
  assert set(facts['snapshot_projections']['central'])=={'public.'+name for name in facts['rows']}
  assert len(plan['blobs'])==int(nonempty)
  assert all(b['present'] is True for b in plan['blobs'])
  print(json.dumps({'status':receipt['status'],'archive_sha256':checked['archive_sha256'],'files':len(plan['files']),'profile':facts['profile'],'historical_control_state':'CAPTURING','blob_sha256':plan['blobs'][0]['sha256'] if nonempty else None}))
"""
            platform = Path(__file__).parents[1]
            environment = {
                "PATH": "/usr/bin:/bin",
                "PYTHONPATH": os.pathsep.join(
                    [str(platform / "src"), str(platform / "scripts")]
                ),
            }
            child = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    script,
                    str(backup),
                    str(anchor),
                    str(job),
                    str(restore),
                    str(key_file),
                    backup_id,
                    result["commit_sha256"],
                    str(int(nonempty)),
                ],
                cwd=tmp_path,
                env=environment,
                pass_fds=(backup, anchor, job, restore),
                capture_output=True,
                text=True,
                timeout=20,
            )
            assert child.returncode == 0, child.stderr
            receipt = json.loads(child.stdout)
            assert receipt["files"] == 7 and receipt["profile"] == profile.name
            assert receipt["historical_control_state"] == "CAPTURING"
            assert receipt["blob_sha256"] == artifact_sha
        finally:
            os.close(job)
        assert (tmp_path / "restores" / "verified" / "db" / "central.dump").is_file()
    finally:
        if runtime is not None:
            await runtime.close()
        if inputs is not None:
            inputs.close()
        if policy is not None:
            policy.close()
        if configs is not None:
            configs.close()
        if locks is not None:
            locks.close()
        if source_root is not None:
            source_root.close()
        for fd in reversed(fds):
            os.close(fd)
        if identity_owned is not None:
            identity_owned.close()
        owned.close()
