#!/usr/bin/env python3
"""Reviewable, fail-closed upgrade for the single verified 1Panel New API stack.

Default: read-only plan. --apply: verified backup, then recreate only New API.
No credentials, compose contents, subprocess output or HTTP responses are logged.
"""
from __future__ import annotations

import argparse
import copy
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import ssl
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from typing import TextIO
from urllib.parse import urlsplit
from urllib.request import HTTPSHandler, HTTPRedirectHandler, Request, build_opener


EXPECTED_HOST = "iv-ye8aaj2u4gcva4gofev6"
APP, PG, REDIS = "new-api", "new-api-db", "new-api-redis"
CONTAINERS = {APP: "1panel-new-api", PG: "1panel-new-api-db", REDIS: "1panel-new-api-redis"}
MOUNTS = {APP: {"/data": "data", "/app/logs": "logs"},
          PG: {"/var/lib/postgresql/data": "postgres-data"}, REDIS: {"/data": "redis-data"}}
COUNT_SQL = ("SELECT json_build_object('users',(SELECT count(*) FROM public.users),"
             "'channels',(SELECT count(*) FROM public.channels),"
             "'options',(SELECT count(*) FROM public.options));")


class SafeError(Exception):
    """Messages are fixed local codes; remote output must never enter this class."""


class Runner:
    def run(self, args, *, stdin_path=None, stdout_path=None, timeout=60) -> str:
        source = destination = None
        try:
            if stdin_path is not None:
                source = open(stdin_path, "rb")
            if stdout_path is not None:
                descriptor = os.open(stdout_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                destination = os.fdopen(descriptor, "wb")
            result = subprocess.run(args, stdin=source, stdout=destination or subprocess.PIPE,
                                    stderr=subprocess.PIPE, timeout=timeout, check=False)
            if result.returncode:
                raise SafeError("subprocess_failed")
            if destination is not None:
                destination.flush()
                os.fsync(destination.fileno())
                return ""
            return result.stdout.decode("utf-8").strip()
        except SafeError:
            raise
        except subprocess.TimeoutExpired:
            raise SafeError("subprocess_timeout") from None
        except (OSError, UnicodeError):
            raise SafeError("subprocess_unavailable_or_invalid_output") from None
        finally:
            if source is not None:
                source.close()
            if destination is not None:
                destination.close()


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise SafeError("status_redirect_rejected")


def read_status(url: str) -> dict:
    parsed = urlsplit(url)
    if (parsed.username or parsed.password or parsed.query or parsed.fragment
            or (parsed.scheme != "https" and not (
                parsed.scheme == "http" and parsed.hostname == "127.0.0.1"))):
        raise SafeError("status_url_must_be_https_or_explicit_loopback")
    try:
        opener = build_opener(NoRedirect(), HTTPSHandler(context=ssl.create_default_context()))
        with opener.open(Request(url, headers={"User-Agent": "Easel-NewAPI-Upgrade/1"}), timeout=10) as response:
            raw = response.read((2 << 20) + 1)
        if len(raw) > 2 << 20:
            raise SafeError("status_response_too_large")
        body = json.loads(raw)
        if not isinstance(body, dict) or body.get("success") is not True or not isinstance(body.get("data"), dict):
            raise SafeError("status_response_invalid")
        return body["data"]
    except SafeError:
        raise
    except Exception:
        raise SafeError("status_request_failed") from None


@dataclass
class UpgradeConfig:
    compose_file: Path
    backup_root: Path
    external_url: str = "https://api.drawbookai.cn"
    min_free_gib: float = 8
    wait_seconds: float = 300
    website_dir: Path = Path("/opt/1panel/www/sites/new-api")


def sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write(path: Path, data: bytes, *, template: Path | None = None) -> None:
    mode, uid, gid = 0o600, None, None
    if template is not None:
        info = template.stat()
        mode, uid, gid = stat.S_IMODE(info.st_mode), info.st_uid, info.st_gid
    descriptor, name = tempfile.mkstemp(prefix=".newapi-write-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), mode)
            if uid is not None:
                os.fchown(stream.fileno(), uid, gid)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary.exists():
            temporary.unlink()


def replace_app_image(original: bytes, target: str) -> bytes:
    """Change one direct image line; unsupported YAML shapes are refused."""
    try:
        text = original.decode("utf-8")
    except UnicodeDecodeError:
        raise SafeError("compose_not_utf8") from None
    lines = text.splitlines(keepends=True)
    top = [index for index, line in enumerate(lines) if re.fullmatch(r"services:\s*(?:#[^\r\n]*)?\r?\n?", line)]
    if len(top) != 1:
        raise SafeError("unsupported_compose_services_shape")
    start, end = top[0] + 1, len(lines)
    for index in range(start, len(lines)):
        if lines[index].strip() and not lines[index].lstrip().startswith("#") and not lines[index].startswith(" "):
            end = index
            break
    blocks = []
    for index in range(start, end):
        match = re.fullmatch(r"( +)(?:new-api|'new-api'|\"new-api\"):\s*(?:#[^\r\n]*)?\r?\n?", lines[index])
        if match:
            blocks.append((index, len(match[1])))
    if len(blocks) != 1:
        raise SafeError("unsupported_app_service_shape")
    block_start, indentation = blocks[0]
    block_end = end
    for index in range(block_start + 1, end):
        line = lines[index]
        if line.strip() and not line.lstrip().startswith("#") and len(line) - len(line.lstrip(" ")) <= indentation:
            block_end = index
            break
    child_indents = [len(line) - len(line.lstrip(" ")) for line in lines[block_start + 1:block_end]
                     if line.strip() and not line.lstrip().startswith("#")]
    if not child_indents:
        raise SafeError("app_image_line_missing")
    child_indent = min(child_indents)
    image_lines = []
    for index in range(block_start + 1, block_end):
        match = re.fullmatch(r"( {" + str(child_indent) + r"}image:\s*)([^\r\n]*)(\r?\n)?", lines[index])
        if match:
            image_lines.append((index, match))
    if len(image_lines) != 1:
        raise SafeError("app_image_line_missing_or_ambiguous")
    index, match = image_lines[0]
    value = match[2]
    comment = ""
    # Docker image references and ${ENV} substitutions do not contain '#'.
    if "#" in value:
        original_value, comment_text = value.split("#", 1)
        whitespace = original_value[len(original_value.rstrip()):]
        comment = whitespace + "#" + comment_text
    lines[index] = match[1] + json.dumps(target) + comment + (match[3] or "")
    return "".join(lines).encode("utf-8")


class Upgrader:
    def __init__(self, config: UpgradeConfig, lock: dict, runner=None, *, status_reader=read_status):
        self.config, self.lock = config, lock
        self.runner, self.status_reader = runner or Runner(), status_reader
        self.compose_file = config.compose_file.absolute()
        self.project = self.compose_file.parent
        self.backup = None
        self.original = self.new_compose = None
        self.effective = None
        self.manifest = {}
        self.quiescing = self.compose_changed = self.new_app_attempted = False
        self.complete = False
        self.lock_descriptor = None
        self.stage = "preflight"

    def _stage(self, name):
        self.stage = name
        self.manifest["stage"] = name
        if self.backup is not None:
            self._write_manifest()

    def _acquire_lock(self):
        # Lock is tied to the application, independent of backup-root choice.
        # Keep its inode after unlock; unlinking creates races between waiters.
        path = self.project / ".newapi-upgrade.lock"
        descriptor = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            info = os.fstat(descriptor)
            if info.st_uid != 0 or not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
                raise SafeError("upgrade_lock_must_be_root_owned_and_private")
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise SafeError("another_upgrade_apply_is_running") from None
            os.ftruncate(descriptor, 0)
            os.write(descriptor, (str(os.getpid()) + "\n").encode())
            self.lock_descriptor = descriptor
        except Exception:
            os.close(descriptor)
            raise
        if self.compose_file.read_bytes() != self.original or self._json(
                self._compose("config", "--format", "json"), "effective_compose_invalid") != self.effective:
            raise SafeError("compose_changed_before_exclusive_apply")

    def _compose(self, *args, timeout=60):
        return self.runner.run(["docker", "compose", "--project-directory", str(self.project),
                                "--file", str(self.compose_file), *args], timeout=timeout)

    def _json(self, raw, error):
        try:
            return json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            raise SafeError(error) from None

    def _inspect(self, service):
        identifiers = self._compose("ps", "--all", "--quiet", service).splitlines()
        if len(identifiers) != 1 or not identifiers[0].strip():
            raise SafeError("expected_single_service_container")
        data = self._json(self.runner.run(["docker", "inspect", identifiers[0].strip()]), "container_inspect_invalid")
        if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict):
            raise SafeError("container_inspect_invalid")
        row = data[0]
        if row.get("Name") != "/" + CONTAINERS[service]:
            raise SafeError("container_name_mismatch")
        return row

    def _assert_ports(self):
        for service in (PG, REDIS):
            row = self._inspect(service)
            for source in (row.get("HostConfig", {}).get("PortBindings", {}),
                           row.get("NetworkSettings", {}).get("Ports", {})):
                if not isinstance(source, dict):
                    raise SafeError("container_port_configuration_invalid")
                for bindings in source.values():
                    for binding in bindings or []:
                        if binding.get("HostIp") not in ("127.0.0.1", "::1"):
                            raise SafeError("database_or_redis_port_is_published")
        self._app_loopback_url(self._inspect(APP))

    def _app_loopback_url(self, row):
        # Check all bindings, including secondary IPv6 and other container ports.
        for ports in (row.get("HostConfig", {}).get("PortBindings", {}),
                      row.get("NetworkSettings", {}).get("Ports", {})):
            if not isinstance(ports, dict):
                raise SafeError("container_port_configuration_invalid")
            for bindings in ports.values():
                for binding in bindings or []:
                    if binding.get("HostIp") not in ("127.0.0.1", "::1"):
                        raise SafeError("application_port_is_not_loopback_only")
        bindings = row.get("NetworkSettings", {}).get("Ports", {}).get("3000/tcp") or []
        ipv4 = [binding for binding in bindings if binding.get("HostIp") == "127.0.0.1"]
        try:
            port = int(ipv4[0]["HostPort"])
            if not 0 < port < 65536:
                raise ValueError
        except (KeyError, IndexError, TypeError, ValueError):
            raise SafeError("application_loopback_port_unavailable") from None
        return f"http://127.0.0.1:{port}/api/status"

    def _pg(self, operation, *, database=None, stdin_path=None, stdout_path=None, timeout=600):
        # Credentials stay inside the existing PostgreSQL container environment.
        prefix = ["docker", "exec"]
        if stdin_path is not None:
            prefix.append("-i")
        prefix += ["--user", "postgres", CONTAINERS[PG], "sh", "-ec"]
        user = '"${POSTGRES_USER:-postgres}"'
        db = '"${POSTGRES_DB:-${POSTGRES_USER:-postgres}}"' if database is None else '"$1"'
        commands = {
            "size": f"exec psql -X -A -t -v ON_ERROR_STOP=1 -U {user} -d {db} -c 'SELECT pg_database_size(current_database());'",
            "counts": f"exec psql -X -A -t -v ON_ERROR_STOP=1 -U {user} -d {db} -c \"{COUNT_SQL}\"",
            "dump": f"exec pg_dump -U {user} -d {db} -Fc",
            "list": "exec pg_restore --list",
            "create": f"exec createdb -U {user} --template=template0 -- \"$1\"",
            "restore": f"exec pg_restore -U {user} -d \"$1\" --exit-on-error --no-owner --no-acl",
        }
        args = prefix + [commands[operation], "newapi-upgrade"]
        if database is not None:
            if not re.fullmatch(r"newapi_verify_[a-z0-9_]+", database):
                raise SafeError("validation_database_name_invalid")
            args.append(database)
        return self.runner.run(args, stdin_path=stdin_path, stdout_path=stdout_path, timeout=timeout)

    def _counts(self, database=None):
        values = self._json(self._pg("counts", database=database), "database_counts_invalid")
        if (not isinstance(values, dict) or set(values) != {"users", "channels", "options"}
                or any(type(value) is not int or value < 0 for value in values.values())):
            raise SafeError("database_counts_invalid")
        return values

    def _target(self):
        try:
            image = self.lock["host"]["image"]
            tag, digest = image["tag"], image["digest"]
            if (self.lock["schemaVersion"] != 1 or image["repository"] != "calciumion/new-api"
                    or tag != self.lock["host"]["tag"] or not re.fullmatch(r"v[0-9][A-Za-z0-9._-]*", tag)
                    or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest)):
                raise ValueError
            return f"calciumion/new-api:{tag}@{digest}"
        except (KeyError, TypeError, ValueError):
            raise SafeError("upgrade_image_lock_invalid") from None

    def _directory_size(self, path, excluded=()):
        total = 0
        for root, directories, files in os.walk(path, followlinks=False):
            directories[:] = [name for name in directories if Path(root, name) not in excluded]
            for name in files:
                item = Path(root, name)
                if not item.is_symlink():
                    total += item.stat().st_size
        return total

    def _preflight(self):
        if os.geteuid() != 0:
            raise SafeError("root_required")
        if self.runner.run(["hostname"]).strip() != EXPECTED_HOST:
            raise SafeError("target_hostname_mismatch")
        self.target = self._target()
        if self.compose_file.is_symlink() or not self.compose_file.is_file() or not self.project.is_dir():
            raise SafeError("compose_file_must_be_regular_file")
        self.original = self.compose_file.read_bytes()
        self.website = self.config.website_dir.absolute()
        if self.website.is_symlink() or not self.website.is_dir():
            raise SafeError("website_directory_must_exist_and_be_a_regular_directory")
        self.new_compose = replace_app_image(self.original, self.target)
        self.effective = self._json(self._compose("config", "--format", "json"), "effective_compose_invalid")
        if not isinstance(self.effective, dict) or set(self.effective.get("services", {})) != {APP, PG, REDIS}:
            raise SafeError("compose_services_mismatch")
        parsed = urlsplit(self.config.external_url)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
            raise SafeError("external_url_must_be_https_origin")
        self.snapshots = {}
        for service in (APP, PG, REDIS):
            settings = self.effective["services"][service]
            if settings.get("container_name") != CONTAINERS[service] or settings.get("build"):
                raise SafeError("compose_container_or_build_mismatch")
            snapshot = self._inspect(service)
            if snapshot.get("State", {}).get("Running") is not True:
                raise SafeError("all_original_services_must_be_running")
            if snapshot.get("Config", {}).get("Image") != settings.get("image"):
                raise SafeError("running_container_image_differs_from_compose")
            mounts = {row.get("Destination"): row for row in snapshot.get("Mounts", [])}
            for destination, directory in MOUNTS[service].items():
                expected = (self.project / directory).resolve()
                row = mounts.get(destination, {})
                configured = [volume for volume in settings.get("volumes", []) if volume.get("target") == destination]
                if (row.get("Type") != "bind" or Path(row.get("Source", "/")).resolve() != expected
                        or not expected.is_dir() or len(configured) != 1
                        or configured[0].get("type") != "bind"
                        or Path(configured[0].get("source", "/")).resolve() != expected):
                    raise SafeError("persistent_mount_mismatch")
            self.snapshots[service] = snapshot
        self._assert_ports()
        self.old_image = self.effective["services"][APP]["image"]
        if not isinstance(self.old_image, str) or not re.fullmatch(r"calciumion/new-api(?::[A-Za-z0-9._-]+)?(?:@sha256:[a-f0-9]{64})?", self.old_image):
            raise SafeError("unexpected_original_application_image")
        self.loopback_url = self._app_loopback_url(self.snapshots[APP])
        backup_root = self.config.backup_root.absolute()
        # Do not create directories in plan, and never recursively archive backups.
        if backup_root.is_symlink() or backup_root == self.project or self.project in backup_root.resolve().parents:
            raise SafeError("backup_root_must_be_outside_application_directory")
        if backup_root == self.website or self.website.resolve() in backup_root.resolve().parents:
            raise SafeError("backup_root_must_be_outside_website_directory")
        existing = backup_root
        while not existing.exists():
            existing = existing.parent
        if not existing.is_dir():
            raise SafeError("backup_parent_not_directory")
        size = int(self._pg("size"))
        if size <= 0:
            raise SafeError("database_size_invalid")
        excluded = tuple((self.project / name).resolve() for name in ("data", "logs", "postgres-data", "redis-data"))
        archive_bytes = self._directory_size(self.project, excluded) + self._directory_size(self.website) + sum(
            self._directory_size(self.project / name) for name in ("data", "logs", "redis-data"))
        required = max(int(self.config.min_free_gib * (1 << 30)), archive_bytes + size * 3 + (128 << 20))
        if shutil.disk_usage(existing).free < required:
            raise SafeError("insufficient_backup_disk_space")
        if shutil.disk_usage(self.project / "postgres-data").free < size * 2 + (128 << 20):
            raise SafeError("insufficient_restore_validation_database_disk_space")
        self.manifest = {"schema_version": 1, "host": EXPECTED_HOST,
            "old_image": self.old_image, "old_image_id": self.snapshots[APP]["Image"], "target_image": self.target,
            "compose_sha256": hashlib.sha256(self.original).hexdigest(),
            "website_source": str(self.website),
            "effective_compose_sha256": hashlib.sha256(json.dumps(self.effective, sort_keys=True).encode()).hexdigest(),
            "container_configuration_sha256": {service: hashlib.sha256(json.dumps({key: row.get(key)
                for key in ("Config", "HostConfig", "Mounts", "Image")}, sort_keys=True).encode()).hexdigest()
                for service, row in self.snapshots.items()},
            "sha256": {}, "source_counts": {}, "restore_counts": {}}

    def _write_manifest(self, complete=False):
        name = "manifest.json" if complete else "manifest.partial.json"
        atomic_write(self.backup / name, json.dumps(self.manifest, indent=2, sort_keys=True).encode() + b"\n")

    def _archive(self, source, name, arcname, excluded=()):
        destination = self.backup / name
        with destination.open("xb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            with tarfile.open(fileobj=stream, mode="w", dereference=False) as archive:
                def filter_member(member):
                    relative = Path(member.name).relative_to(arcname)
                    actual = source / relative
                    return None if actual.resolve() in excluded else member
                archive.add(source, arcname=arcname, recursive=True, filter=filter_member)
            stream.flush()
            os.fsync(stream.fileno())
        with tarfile.open(destination, "r") as archive:
            if not archive.getmembers():
                raise SafeError("empty_backup_archive")
        self.manifest["sha256"][name] = sha_file(destination)

    def _wait_running(self, service, *, deadline=None):
        if deadline is None:
            deadline = time.monotonic() + self.config.wait_seconds
        while True:
            row = self._inspect(service)
            if row.get("State", {}).get("Running") is True and row.get("State", {}).get("Health", {}).get("Status", "healthy") == "healthy":
                return row
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SafeError("container_health_verification_failed")
            time.sleep(min(2, remaining))

    def _start_originals(self):
        if self.compose_file.read_bytes() != self.original:
            if self.compose_changed and self.compose_file.read_bytes() == self.new_compose:
                atomic_write(self.compose_file, self.original, template=self.compose_file)
                self.compose_changed = False
            else:
                raise SafeError("compose_concurrent_change_blocks_original_service_recovery")
        self._compose("start", REDIS)
        self._wait_running(REDIS)
        self._compose("start", APP)
        app = self._wait_running(APP)
        if app.get("Image") != self.snapshots[APP]["Image"]:
            raise SafeError("original_application_image_recovery_mismatch")

    def _backup(self):
        self.config.backup_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = self.config.backup_root.stat()
        if info.st_uid != 0 or stat.S_IMODE(info.st_mode) & 0o077:
            raise SafeError("backup_root_must_be_root_owned_and_private")
        name = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(4)
        self.backup = self.config.backup_root / name
        self.backup.mkdir(mode=0o700)
        self.manifest["created_at"] = datetime.now(timezone.utc).isoformat()
        self.manifest["restore_database"] = "newapi_verify_" + name.lower().replace("-", "_")
        self._write_manifest()
        atomic_write(self.backup / "RESTORE.md", self._recovery_text().encode())
        self.quiescing = True
        self._stage("quiescing_application")
        self._compose("stop", "--timeout", "60", APP, timeout=90)
        if self._inspect(APP).get("State", {}).get("Running") is not False:
            raise SafeError("application_did_not_stop")
        self._stage("quiescing_redis")
        self._compose("stop", "--timeout", "60", REDIS, timeout=90)
        if self._inspect(REDIS).get("State", {}).get("Running") is not False:
            raise SafeError("redis_did_not_stop_for_cold_backup")
        if self._inspect(PG).get("State", {}).get("Running") is not True:
            raise SafeError("postgres_must_remain_running_for_logical_backup")
        self._stage("postgres_logical_dump")
        self.manifest["source_counts"] = self._counts()
        dump = self.backup / "postgres.dump"
        self._pg("dump", stdout_path=dump)
        with dump.open("rb") as stream:
            signature = stream.read(5)
        if dump.stat().st_size < 5 or signature != b"PGDMP":
            raise SafeError("postgres_dump_not_custom_format")
        self.manifest["sha256"][dump.name] = sha_file(dump)
        self._stage("configuration_and_cold_data_archives")
        excluded = tuple((self.project / name).resolve() for name in ("data", "logs", "postgres-data", "redis-data"))
        self._archive(self.project, "configuration.tar", "project", excluded)
        for directory in ("data", "logs", "redis-data"):
            self._archive(self.project / directory, directory + ".tar", directory)
        self._archive(self.website, "website.tar", "website")
        self._write_manifest()
        self._stage("independent_database_restore_validation")
        toc = self._pg("list", stdin_path=dump)
        if "TABLE" not in toc:
            raise SafeError("postgres_restore_list_has_no_tables")
        validation_db = self.manifest["restore_database"]
        self._pg("create", database=validation_db)
        self._pg("restore", database=validation_db, stdin_path=dump)
        self.manifest["restore_counts"] = self._counts(validation_db)
        if self.manifest["restore_counts"] != self.manifest["source_counts"]:
            raise SafeError("restore_validation_count_mismatch")
        for file_name, expected in self.manifest["sha256"].items():
            if sha_file(self.backup / file_name) != expected:
                raise SafeError("backup_sha256_verification_failed")
        self.manifest["restore_verified"] = True
        self._stage("backup_verified")
        self._write_manifest(complete=True)
        marker = json.dumps({"manifest_sha256": sha_file(self.backup / "manifest.json"),
                             "restore_database": validation_db, "restore_verified": True}) + "\n"
        atomic_write(self.backup / "COMPLETE", marker.encode())
        self.complete = True

    def _recovery_text(self):
        return f"""# New API recovery review\n\nBackup directory: {self.backup}\nOld image: {self.old_image}\nTarget image: {self.target}\n\nThis file is a recovery procedure, not evidence that recovery was executed.\nCOMPLETE is valid only after its manifest SHA256 and every archive hash match.\nThe verification database is intentionally retained; do not drop it automatically.\n\nBefore any new application startup, a backup failure may resume the exact stopped\noriginal Redis/application containers because the live database was not migrated.\nAfter any attempt to start the new image, never simply select the old image while\nkeeping the possibly migrated live database. Keep New API stopped. PostgreSQL and\nRedis may remain running for inspection. Preserve the failing database separately.\n\nFor recovery after migration, review with the administrator: restore postgres.dump\ninto a NEW, empty PostgreSQL database using pg_restore --exit-on-error; compare\nusers/channels/options counts with manifest.json. Restore configuration.tar (which\nincludes hidden files but excludes live PostgreSQL physical files), data.tar and\nlogs.tar into a separate application directory. website.tar contains the entire\n/opt/1panel/www/sites/new-api directory including hidden/proxy files. Restore it\nto an isolated review location before changing live proxy configuration. The\nupgrade does not modify OpenResty. Stop Redis before restoring the\nENTIRE redis-data.tar, including AOF manifests and incremental files, into a\nseparate volume. Use the old pinned image ID recorded in the manifest and the\noriginal compose/configuration, with the DSN explicitly pointed at the restored\ndatabase and Redis at its restored volume. Verify isolated health first, then\nreview an explicit cutover. Do not overwrite the migrated database or delete any\nvalidation database to make rollback appear successful.\n\nThis tool does not implement automated database rollback, a pre-migrated clone\ncutover, multi-node quiescing or a proven full application/Redis recovery drill.\nThe independent database restore verifies archive readability and selected row\ncounts; it is not proof that all production workflows or billing work.\n"""

    def _change_compose(self):
        if not self.complete or not (self.backup / "COMPLETE").is_file():
            raise SafeError("complete_backup_required_before_compose_change")
        marker = self._json((self.backup / "COMPLETE").read_text(), "backup_complete_marker_invalid")
        if (not isinstance(marker, dict) or marker.get("manifest_sha256") != sha_file(self.backup / "manifest.json")
                or marker.get("restore_verified") is not True):
            raise SafeError("backup_complete_marker_invalid")
        for file_name, expected in self.manifest["sha256"].items():
            if sha_file(self.backup / file_name) != expected:
                raise SafeError("backup_sha256_verification_failed")
        if self.compose_file.read_bytes() != self.original:
            raise SafeError("compose_changed_during_backup")
        atomic_write(self.compose_file, self.new_compose, template=self.compose_file)
        self.compose_changed = True
        changed = self._json(self._compose("config", "--format", "json"), "effective_compose_invalid")
        expected = copy.deepcopy(self.effective)
        expected["services"][APP]["image"] = self.target
        if changed != expected:
            raise SafeError("effective_compose_changed_outside_app_image")

    def _verify_upgrade(self):
        deadline = time.monotonic() + self.config.wait_seconds
        while True:
            try:
                app = self._wait_running(APP, deadline=deadline)
                if app.get("Image") != self.target_image_id or app.get("Config", {}).get("Image") != self.target:
                    raise SafeError("new_application_image_mismatch")
                self._assert_ports()
                for service in (PG, REDIS):
                    row = self._wait_running(service, deadline=deadline)
                    digest = hashlib.sha256(json.dumps({key: row.get(key) for key in
                        ("Config", "HostConfig", "Mounts", "Image")}, sort_keys=True).encode()).hexdigest()
                    if digest != self.manifest["container_configuration_sha256"][service]:
                        raise SafeError("database_or_redis_configuration_changed")
                for url in (self.loopback_url, self.config.external_url.rstrip("/") + "/api/status"):
                    status = self.status_reader(url)
                    if not isinstance(status, dict) or status.get("version") not in (
                            self.lock["host"]["tag"], self.lock["host"]["tag"].removeprefix("v")):
                        raise SafeError("new_application_status_version_mismatch")
                return
            except SafeError:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise SafeError("new_application_health_or_status_verification_failed") from None
                time.sleep(min(2, remaining))

    def run(self, *, apply=False, backup_only=False, output: TextIO = sys.stdout):
        report = {"mode": "apply" if apply else "plan", "state": "preflight_failed", "exit_code": 1,
                  "host": EXPECTED_HOST, "backup_path": "", "error": ""}
        try:
            self._preflight()
            report.update(old_image=self.old_image, target_image=self.target,
                          configuration_sha256=self.manifest["effective_compose_sha256"])
            if not apply:
                report.update(state="planned", exit_code=0)
                return report
            self._acquire_lock()
            self._stage("pulling_only_application_image")
            self.runner.run(["docker", "pull", self.target], timeout=600)
            image = self._json(self.runner.run(["docker", "image", "inspect", self.target]), "pulled_image_inspect_invalid")
            if (not isinstance(image, list) or len(image) != 1 or
                    "calciumion/new-api@" + self.lock["host"]["image"]["digest"] not in image[0].get("RepoDigests", [])):
                raise SafeError("pulled_image_digest_mismatch")
            self.target_image_id = image[0]["Id"]
            self._backup()
            report.update(backup_path=str(self.backup), restore_database=self.manifest["restore_database"])
            self._stage("starting_original_redis")
            self._compose("start", REDIS)
            self._wait_running(REDIS)
            if backup_only:
                self._start_originals()
                report.update(state="backup_verified_old_services_restored", exit_code=0)
                return report
            self._stage("updating_only_application_image")
            self._change_compose()
            # Mark before docker up: migrations can begin even if the CLI fails.
            self.new_app_attempted = True
            self._stage("starting_new_application_migrations_may_run")
            self._compose("up", "--detach", "--no-deps", "--pull", "never", APP, timeout=180)
            self._stage("verifying_container_loopback_and_https")
            self._verify_upgrade()
            report.update(state="upgraded_verified", exit_code=0)
            return report
        except (Exception, KeyboardInterrupt) as error:
            report["error"] = str(error) if isinstance(error, SafeError) else "unexpected_local_error"
            report["failure_stage"] = self.stage
            if self.backup is not None:
                report["backup_path"] = str(self.backup)
                report["restore_database"] = self.manifest.get("restore_database", "")
                self.manifest["failure"] = report["error"]
                try:
                    self._write_manifest()
                except Exception:
                    report["failure_manifest_write_failed"] = True
            if self.new_app_attempted:
                report["state"] = "upgrade_failed_manual_database_recovery_required"
                try:
                    self._compose("stop", "--timeout", "60", APP, timeout=90)
                    if self._inspect(APP).get("State", {}).get("Running") is not False:
                        raise SafeError("new_application_failed_to_stop")
                except Exception:
                    report["new_application_stop_failed"] = True
            elif self.quiescing:
                try:
                    self._start_originals()
                    report["state"] = "backup_failed_old_services_restored"
                except Exception:
                    report["state"] = "backup_failed_old_services_recovery_failed"
                    report["manual_recovery_required"] = True
            return report
        finally:
            if self.lock_descriptor is not None:
                os.close(self.lock_descriptor)
                self.lock_descriptor = None
            print(json.dumps(report, indent=2, sort_keys=True), file=output)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compose-file", type=Path, default=Path("/opt/1panel/apps/new-api/new-api/docker-compose.yml"))
    parser.add_argument("--backup-root", type=Path, required=True, help="root-owned private directory outside APP_DIR")
    parser.add_argument("--lock", type=Path, default=Path(__file__).parents[1] / "catalog.lock.json")
    parser.add_argument("--external-url", default="https://api.drawbookai.cn")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup-only", action="store_true", help="with --apply, validate backup then resume originals")
    parser.add_argument("--min-free-gib", type=float, default=8)
    parser.add_argument("--wait-seconds", type=float, default=300)
    args = parser.parse_args(argv)
    if args.min_free_gib < 1 or not 1 <= args.wait_seconds <= 600:
        parser.error("min-free-gib must be >=1; wait-seconds must be 1..600")
    if args.backup_only and not args.apply:
        parser.error("--backup-only requires --apply")
    def interrupt(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupt)
    try:
        lock = json.loads(args.lock.read_text())
        config = UpgradeConfig(args.compose_file, args.backup_root, args.external_url,
                               args.min_free_gib, args.wait_seconds)
        return Upgrader(config, lock).run(apply=args.apply, backup_only=args.backup_only)["exit_code"]
    except Exception:
        print('{"error":"local_upgrade_setup_failed","exit_code":1}', file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
