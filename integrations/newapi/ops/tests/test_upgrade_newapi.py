"""Fake subprocess integration tests; this suite never contacts Docker or a host."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch


MODULE = Path(__file__).parents[1] / "upgrade_newapi.py"
SECRET = "production-password-must-not-appear"
DIGEST = "sha256:" + "a" * 64
TARGET = "calciumion/new-api:v1.0.0-rc.45@" + DIGEST
OLD = "calciumion/new-api:v0.13.2"
HOST = "iv-ye8aaj2u4gcva4gofev6"
APP, PG, REDIS = "new-api", "new-api-db", "new-api-redis"
CONTAINERS = {APP: "1panel-new-api", PG: "1panel-new-api-db", REDIS: "1panel-new-api-redis"}


class FakeRunner:
    def __init__(self, mod, project):
        self.mod, self.project = mod, project
        self.calls = []
        self.fail = ""
        self.states = {APP: True, PG: True, REDIS: True}
        self.current_image = OLD
        self.hostname = HOST
        self.public_database_port = False
        self.tamper_effective_config = False
        self.count_mismatch = False
        self.verify_database = ""
        self.restore_seen = False
        self.app_bindings = None
        self.wrong_digest = False
        self.old_recovery_fails = False
        self.config = {"name": "new-api", "services": {
            APP: {"image": OLD, "container_name": CONTAINERS[APP],
                  "environment": {"SQL_DSN": SECRET, "REDIS_CONN_STRING": SECRET},
                  "ports": [{"target": 3000, "published": "3000"}],
                  "volumes": [{"type": "bind", "source": str(project / path), "target": destination}
                              for path, destination in [("data", "/data"), ("logs", "/app/logs")]]},
            PG: {"image": "postgres:16", "container_name": CONTAINERS[PG],
                 "environment": {"POSTGRES_USER": "newapi", "POSTGRES_DB": "newapi", "POSTGRES_PASSWORD": SECRET},
                 "volumes": [{"type": "bind", "source": str(project / "postgres-data"),
                              "target": "/var/lib/postgresql/data"}]},
            REDIS: {"image": "redis:7", "container_name": CONTAINERS[REDIS],
                    "volumes": [{"type": "bind", "source": str(project / "redis-data"), "target": "/data"}]}
        }, "networks": {"default": {"name": "new-api_default"}}}

    def _inspect(self, service):
        item = self.config["services"][service]
        ports = {"3000/tcp": [{"HostIp": "127.0.0.1", "HostPort": "3000"}]} if service == APP else {}
        if service == APP and self.app_bindings is not None:
            ports = self.app_bindings
        if self.public_database_port and service == PG:
            ports = {"5432/tcp": [{"HostIp": "0.0.0.0", "HostPort": "5432"}]}
        return {"Id": service + "-id", "Name": "/" + CONTAINERS[service],
                "Image": "sha256:" + ("b" if self.current_image == OLD or service != APP else "c") * 64,
                "Config": {"Image": self.current_image if service == APP else item["image"], "Env": ["SECRET=" + SECRET]},
                "HostConfig": {"PortBindings": ports}, "NetworkSettings": {"Ports": ports},
                "State": {"Running": self.states[service], "Health": {"Status": "healthy"}},
                "Mounts": [{"Type": "bind", "Source": v["source"], "Destination": v["target"]}
                           for v in item["volumes"]]}

    def run(self, args, *, stdin_path=None, stdout_path=None, timeout=60):
        self.calls.append((list(args), stdin_path, stdout_path))
        if args == ["hostname"]:
            return self.hostname
        if args[:3] == ["docker", "image", "inspect"]:
            digest = "sha256:" + "f" * 64 if self.wrong_digest else DIGEST
            return json.dumps([{"RepoDigests": ["calciumion/new-api@" + digest], "Id": "sha256:" + "c" * 64}])
        if args[:2] == ["docker", "pull"]:
            if self.fail == "pull":
                raise self.mod.SafeError("subprocess_failed")
            return "pulled"
        if args[:2] == ["docker", "inspect"]:
            service = next(s for s in self.states if args[2] in (s + "-id", CONTAINERS[s]))
            return json.dumps([self._inspect(service)])
        if args[:2] == ["docker", "compose"]:
            verb_index = args.index("--file") + 2
            verb = args[verb_index]
            remaining = args[verb_index + 1:]
            if verb == "config":
                config = copy.deepcopy(self.config)
                if TARGET in (self.project / "docker-compose.yml").read_text():
                    config["services"][APP]["image"] = TARGET
                    if self.tamper_effective_config:
                        config["services"][REDIS]["image"] = "redis:999"
                return json.dumps(config)
            if verb == "ps":
                return remaining[-1] + "-id"
            if verb == "stop":
                self.states[remaining[-1]] = False
                return "stopped"
            if verb == "start":
                if self.old_recovery_fails:
                    raise self.mod.SafeError("subprocess_failed")
                self.states[remaining[-1]] = True
                return "started"
            if verb == "up":
                self.current_image = TARGET
                self.states[APP] = True
                if self.fail == "migration":
                    raise self.mod.SafeError("subprocess_failed")
                return "recreated app"
            raise AssertionError(f"unhandled compose verb {verb}")
        if args[:2] == ["docker", "exec"]:
            command = " ".join(args)
            if "pg_database_size" in command:
                return "12345"
            if "pg_dump" in command:
                if self.fail == "dump":
                    raise self.mod.SafeError("subprocess_failed")
                Path(stdout_path).write_bytes(b"PGDMP-test-logical-backup")
                return ""
            if "pg_restore" in command and "--list" in command:
                if self.fail == "list":
                    raise self.mod.SafeError("subprocess_failed")
                self.assert_dump(stdin_path)
                return "; Archive created\n1; TABLE public users newapi\n"
            if "createdb" in command:
                self.verify_database = args[-1]
                return ""
            if "pg_restore" in command:
                if self.fail == "restore":
                    raise self.mod.SafeError("subprocess_failed")
                self.assert_dump(stdin_path)
                self.restore_seen = True
                return ""
            if "json_build_object" in command:
                if self.count_mismatch and self.verify_database and args[-1] == self.verify_database:
                    return json.dumps({"users": 0, "channels": 0, "options": 0})
                return json.dumps({"users": 2, "channels": 0, "options": 23})
            raise AssertionError("unhandled postgres operation")
        raise AssertionError("unhandled subprocess operation")

    @staticmethod
    def assert_dump(path):
        if not Path(path).read_bytes().startswith(b"PGDMP"):
            raise AssertionError("archive missing")


class UpgradeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if MODULE.exists():
            spec = importlib.util.spec_from_file_location("newapi_upgrade", MODULE)
            cls.mod = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = cls.mod
            spec.loader.exec_module(cls.mod)

    def setUp(self):
        self.assertTrue(MODULE.exists(), "production upgrade tool is missing")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name) / "new-api"
        self.project.mkdir()
        self.website = Path(self.temp.name) / "website"
        self.website.mkdir()
        (self.website / ".proxy-hidden").write_text("website hidden config")
        (self.website / "proxy.conf").write_text("proxy_pass http://127.0.0.1:3000;")
        for directory in ("data", "logs", "postgres-data", "redis-data/appendonlydir"):
            (self.project / directory).mkdir(parents=True, exist_ok=True)
        (self.project / ".env").write_text("PASSWORD=" + SECRET)
        (self.project / "data/state.db").write_bytes(b"app-data")
        (self.project / "logs/log.txt").write_text("log")
        (self.project / "postgres-data/PG_VERSION").write_text("16")
        (self.project / "redis-data/appendonlydir/appendonly.aof.manifest").write_text("redis-aof")
        self.compose = self.project / "docker-compose.yml"
        self.original = ("services:\n  new-api:\n    image: " + OLD + " # retain-comment\n"
                         "    environment:\n      SQL_DSN: ${SQL_DSN}\n"
                         "  new-api-db:\n    image: postgres:16\n  new-api-redis:\n    image: redis:7\n").encode()
        self.compose.write_bytes(self.original)
        self.backups = Path(self.temp.name) / "backups"
        self.lock = {"schemaVersion": 1, "host": {"tag": "v1.0.0-rc.45", "image": {
            "repository": "calciumion/new-api", "tag": "v1.0.0-rc.45", "digest": DIGEST}}}
        self.runner = FakeRunner(self.mod, self.project)
        self.config = self.mod.UpgradeConfig(self.compose, self.backups, min_free_gib=0, wait_seconds=0.02,
                                            website_dir=self.website)
        self.output = io.StringIO()

    def execute(self, *, apply=False, backup_only=False, status_reader=None, uid=0):
        def healthy_status(url):
            return {"version": "v1.0.0-rc.45"}
        original_stat = Path.stat
        original_fstat = os.fstat
        def fake_root_backup_owner(path, *args, **kwargs):
            result = original_stat(path, *args, **kwargs)
            if path == self.backups:
                fields = list(result)
                fields[4] = 0
                return os.stat_result(fields)
            return result
        def fake_root_fstat(descriptor):
            fields = list(original_fstat(descriptor))
            fields[4] = 0
            return os.stat_result(fields)
        with patch.object(os, "geteuid", return_value=uid), patch.object(Path, "stat", fake_root_backup_owner), \
                patch.object(os, "fstat", fake_root_fstat):
            return self.mod.Upgrader(self.config, self.lock, self.runner,
                status_reader=status_reader or healthy_status).run(apply=apply,
                    backup_only=backup_only, output=self.output)

    def test_default_plan_is_read_only_and_redacts_all_configuration(self):
        report = self.execute()
        self.assertEqual(report["exit_code"], 0)
        self.assertEqual(report["state"], "planned")
        self.assertFalse(self.backups.exists())
        self.assertFalse((self.project / ".newapi-upgrade.lock").exists())
        self.assertEqual(self.compose.read_bytes(), self.original)
        self.assertFalse(any("pull" in args or "stop" in args or "up" in args for args, *_ in self.runner.calls))
        self.assertNotIn(SECRET, self.output.getvalue())

    def test_wrong_hostname_or_nonroot_is_fail_closed(self):
        self.runner.hostname = "wrong-host"
        report = self.execute()
        self.assertEqual(report["exit_code"], 1)
        self.assertEqual(report["error"], "target_hostname_mismatch")
        self.runner.hostname = HOST
        report = self.execute(uid=501)
        self.assertEqual(report["error"], "root_required")
        self.assertFalse(self.backups.exists())

    def test_missing_expected_service_or_mount_is_rejected_before_stop(self):
        self.runner.config["services"].pop(REDIS)
        report = self.execute(apply=True)
        self.assertEqual(report["exit_code"], 1)
        self.assertNotIn(False, self.runner.states.values())
        self.assertEqual(self.compose.read_bytes(), self.original)

    def test_public_postgres_or_redis_ports_are_rejected(self):
        self.runner.public_database_port = True
        report = self.execute(apply=True)
        self.assertEqual(report["error"], "database_or_redis_port_is_published")
        self.assertEqual(self.runner.states, {APP: True, PG: True, REDIS: True})

    def test_complete_backup_precedes_only_app_image_change_and_app_recreate(self):
        report = self.execute(apply=True)
        self.assertEqual(report["exit_code"], 0, self.output.getvalue())
        self.assertEqual(report["state"], "upgraded_verified")
        backup = Path(report["backup_path"])
        self.assertTrue((backup / "COMPLETE").is_file())
        self.assertTrue(self.runner.restore_seen)
        self.assertTrue(report["restore_database"].startswith("newapi_verify_"))
        self.assertEqual(self.runner.verify_database, report["restore_database"])
        self.assertIn(TARGET.encode(), self.compose.read_bytes())
        self.assertEqual(self.compose.read_bytes().replace(('"' + TARGET + '"').encode(), OLD.encode()), self.original)
        pulls = [args for args, *_ in self.runner.calls if args[:2] == ["docker", "pull"]]
        self.assertEqual(pulls, [["docker", "pull", TARGET]])
        ups = [args for args, *_ in self.runner.calls if "up" in args]
        self.assertEqual(len(ups), 1)
        self.assertEqual(ups[0][-1], APP)
        self.assertIn("--no-deps", ups[0])
        self.assertEqual(self.runner.states, {APP: True, PG: True, REDIS: True})
        self.assertNotIn(SECRET, self.output.getvalue())

    def test_backup_archives_include_hidden_config_and_complete_redis_but_no_hot_pg(self):
        report = self.execute(apply=True, backup_only=True)
        self.assertEqual(report["exit_code"], 0)
        backup = Path(report["backup_path"])
        with tarfile.open(backup / "configuration.tar") as archive:
            names = archive.getnames()
        self.assertIn("project/.env", names)
        self.assertFalse(any("postgres-data/PG_VERSION" in name for name in names))
        with tarfile.open(backup / "redis-data.tar") as archive:
            self.assertIn("redis-data/appendonlydir/appendonly.aof.manifest", archive.getnames())
        with tarfile.open(backup / "website.tar") as archive:
            self.assertIn("website/.proxy-hidden", archive.getnames())
            self.assertIn("website/proxy.conf", archive.getnames())
        manifest = json.loads((backup / "manifest.json").read_text())
        for name, digest in manifest["sha256"].items():
            self.assertEqual(hashlib.sha256((backup / name).read_bytes()).hexdigest(), digest)
        self.assertEqual(self.compose.read_bytes(), self.original)
        self.assertEqual(self.runner.current_image, OLD)
        self.assertEqual(self.runner.states, {APP: True, PG: True, REDIS: True})
        self.assertNotIn(SECRET, (backup / "manifest.json").read_text())

    def test_dump_failure_restores_stopped_original_services_without_editing_compose(self):
        self.runner.fail = "dump"
        report = self.execute(apply=True)
        self.assertEqual(report["exit_code"], 1)
        self.assertEqual(report["state"], "backup_failed_old_services_restored")
        self.assertEqual(self.runner.current_image, OLD)
        self.assertEqual(self.runner.states, {APP: True, PG: True, REDIS: True})
        self.assertEqual(self.compose.read_bytes(), self.original)
        self.assertFalse((Path(report["backup_path"]) / "COMPLETE").exists())

    def test_restore_list_or_restore_failure_prevents_upgrade_and_recovers_old_services(self):
        for stage in ("list", "restore"):
            with self.subTest(stage=stage):
                self.runner.fail = stage
                report = self.execute(apply=True)
                self.assertEqual(report["exit_code"], 1)
                self.assertEqual(report["state"], "backup_failed_old_services_restored")
                self.assertEqual(self.runner.current_image, OLD)
                self.assertEqual(self.runner.states, {APP: True, PG: True, REDIS: True})
                self.assertEqual(self.compose.read_bytes(), self.original)

    def test_restore_count_mismatch_prevents_complete_and_upgrade(self):
        self.runner.count_mismatch = True
        report = self.execute(apply=True)
        self.assertEqual(report["error"], "restore_validation_count_mismatch")
        self.assertFalse((Path(report["backup_path"]) / "COMPLETE").exists())
        self.assertEqual(self.runner.states, {APP: True, PG: True, REDIS: True})

    def test_compose_change_outside_app_image_is_restored_before_start(self):
        self.runner.tamper_effective_config = True
        report = self.execute(apply=True)
        self.assertEqual(report["exit_code"], 1)
        self.assertEqual(report["error"], "effective_compose_changed_outside_app_image")
        self.assertEqual(self.compose.read_bytes(), self.original)
        self.assertEqual(self.runner.current_image, OLD)
        self.assertEqual(self.runner.states, {APP: True, PG: True, REDIS: True})

    def test_migration_failure_never_starts_old_image_against_potentially_migrated_db(self):
        self.runner.fail = "migration"
        report = self.execute(apply=True)
        self.assertEqual(report["exit_code"], 1)
        self.assertEqual(report["state"], "upgrade_failed_manual_database_recovery_required")
        self.assertFalse(self.runner.states[APP])
        self.assertTrue(self.runner.states[PG])
        self.assertTrue(self.runner.states[REDIS])
        self.assertEqual(self.runner.current_image, TARGET)
        self.assertIn(TARGET.encode(), self.compose.read_bytes())
        self.assertTrue((Path(report["backup_path"]) / "COMPLETE").exists())
        self.assertFalse(any("start" in args and args[-1] == APP for args, *_ in self.runner.calls))
        self.assertNotIn(SECRET, self.output.getvalue())

    def test_external_status_wrong_version_is_not_upgrade_success(self):
        report = self.execute(apply=True, status_reader=lambda url: {"version": "v0.13.2"})
        self.assertEqual(report["exit_code"], 1)
        self.assertEqual(report["state"], "upgrade_failed_manual_database_recovery_required")
        self.assertFalse(self.runner.states[APP])

    def test_pulled_image_digest_mismatch_stops_before_quiescing(self):
        self.runner.wrong_digest = True
        report = self.execute(apply=True)
        self.assertEqual(report["error"], "pulled_image_digest_mismatch")
        self.assertEqual(self.runner.states, {APP: True, PG: True, REDIS: True})
        self.assertFalse(self.backups.exists())

    def test_backup_tampering_after_complete_prevents_compose_modification(self):
        original = self.mod.Upgrader._change_compose
        def tamper_then_change(upgrader):
            (upgrader.backup / "data.tar").write_bytes(b"tampered")
            return original(upgrader)
        with patch.object(self.mod.Upgrader, "_change_compose", tamper_then_change):
            report = self.execute(apply=True)
        self.assertEqual(report["error"], "backup_sha256_verification_failed")
        self.assertEqual(self.compose.read_bytes(), self.original)
        self.assertEqual(self.runner.current_image, OLD)
        self.assertEqual(self.runner.states, {APP: True, PG: True, REDIS: True})

    def test_backup_failure_with_failed_old_recovery_is_reported_as_manual_attention(self):
        self.runner.fail = "dump"
        self.runner.old_recovery_fails = True
        report = self.execute(apply=True)
        self.assertEqual(report["state"], "backup_failed_old_services_recovery_failed")
        self.assertTrue(report["manual_recovery_required"])
        self.assertFalse(self.runner.states[APP])

    def test_tar_failure_restores_original_services(self):
        with patch.object(self.mod.Upgrader, "_archive", side_effect=OSError(SECRET)):
            report = self.execute(apply=True)
        self.assertEqual(report["state"], "backup_failed_old_services_restored")
        self.assertEqual(self.runner.states, {APP: True, PG: True, REDIS: True})
        self.assertNotIn(SECRET, self.output.getvalue())

    def test_status_verification_reads_loopback_and_https(self):
        urls = []
        def status(url):
            urls.append(url)
            return {"version": "v1.0.0-rc.45"}
        report = self.execute(apply=True, status_reader=status)
        self.assertEqual(report["exit_code"], 0)
        self.assertEqual(urls, ["http://127.0.0.1:3000/api/status", "https://api.drawbookai.cn/api/status"])

    def test_subprocess_errors_discard_sensitive_output(self):
        completed = self.mod.subprocess.CompletedProcess(["docker"], 1, SECRET.encode(), SECRET.encode())
        with patch.object(self.mod.subprocess, "run", return_value=completed):
            with self.assertRaises(self.mod.SafeError) as caught:
                self.mod.Runner().run(["docker"])
        self.assertEqual(str(caught.exception), "subprocess_failed")

    def test_apply_flock_rejects_concurrent_execution_before_pull_or_stop(self):
        import fcntl
        path = self.project / ".newapi-upgrade.lock"
        with path.open("w") as lock:
            path.chmod(0o600)
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            report = self.execute(apply=True)
        self.assertEqual(report["error"], "another_upgrade_apply_is_running")
        self.assertFalse(any(args[:2] == ["docker", "pull"] or "stop" in args for args, *_ in self.runner.calls))
        self.assertEqual(self.runner.states, {APP: True, PG: True, REDIS: True})

    def test_every_app_mapping_is_loopback_and_no_additional_port_is_public(self):
        scenarios = [
            {"3000/tcp": [{"HostIp": "0.0.0.0", "HostPort": "3000"}]},
            {"3000/tcp": [{"HostIp": "127.0.0.1", "HostPort": "3000"},
                          {"HostIp": "::", "HostPort": "3000"}]},
            {"3000/tcp": [{"HostIp": "127.0.0.1", "HostPort": "3000"}],
             "8080/tcp": [{"HostIp": "0.0.0.0", "HostPort": "8080"}]},
        ]
        for bindings in scenarios:
            with self.subTest(bindings=bindings):
                self.runner.app_bindings = bindings
                report = self.execute(apply=True)
                self.assertEqual(report["error"], "application_port_is_not_loopback_only")
                self.assertEqual(self.runner.states, {APP: True, PG: True, REDIS: True})
                self.assertFalse(self.backups.exists())

    def test_missing_website_backup_source_is_fail_closed(self):
        self.config.website_dir = self.website / "missing"
        report = self.execute(apply=True)
        self.assertEqual(report["error"], "website_directory_must_exist_and_be_a_regular_directory")
        self.assertEqual(self.runner.states, {APP: True, PG: True, REDIS: True})


if __name__ == "__main__":
    unittest.main()
