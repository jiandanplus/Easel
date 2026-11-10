#!/usr/bin/env python3
"""Plan or install a pinned official New API plugin catalog.

All API/transport failures deliberately discard response bodies and exception
text: management APIs may echo credentials, source code or private settings.
The optional cryptography dependency is only needed for encrypted password login.
"""
from __future__ import annotations

import argparse
import base64
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import ssl
import stat
import sys
from typing import TextIO
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPSHandler, HTTPRedirectHandler, Request, build_opener


SOURCE_LIMIT = 8 << 20
ICON_LIMIT = 512 << 10
JSON_LIMIT = 16 << 20
OFFICIAL_REPO = "https://raw.githubusercontent.com/QuantumNous/new-api-plugins/"
SAFE_COMPONENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
SHA256 = re.compile(r"[a-f0-9]{64}\Z")
COMMIT = re.compile(r"[a-f0-9]{40}\Z")


class SafeError(Exception):
    """Only locally defined, non-sensitive error codes may cross this boundary."""


def require_https(url: str) -> None:
    try:
        parts = urlsplit(url)
        valid = parts.scheme == "https" and bool(parts.hostname) and not (
            parts.username or parts.password or parts.fragment)
        # Evaluating the port also rejects malformed authorities before networking.
        parts.port
    except (ValueError, TypeError):
        valid = False
    if not valid:
        raise SafeError("https_url_required")


class RejectRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # A redirect must never forward root credentials or bearer authentication.
        raise SafeError("http_redirect_rejected")


class HTTPTransport:
    def __init__(self, *, timeout: float = 30, ca_file: str | None = None, opener=None):
        self.ssl_context = ssl.create_default_context(cafile=ca_file)
        self.opener = opener if opener is not None else build_opener(
            RejectRedirect(), HTTPSHandler(context=self.ssl_context))
        self.timeout = timeout

    def get_bytes(self, url: str, *, limit: int = SOURCE_LIMIT) -> bytes:
        return self.request(url, limit=limit)

    def request(self, url: str, *, method="GET", payload=None, headers=None,
                limit: int = JSON_LIMIT) -> bytes:
        require_https(url)
        request_headers = {"User-Agent": "Easel-NewAPI-Catalog/1", "Accept": "application/json"}
        request_headers.update(headers or {})
        data = None
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        request = Request(url, data=data, method=method, headers=request_headers)
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                raw = response.read(limit + 1)
        except SafeError:
            raise
        except HTTPError as error:
            raise SafeError(f"http_status_{error.code}") from None
        except Exception:
            # Never print exception strings: urllib errors can include URL userinfo,
            # proxy secrets or response fragments supplied by the remote server.
            raise SafeError("https_request_failed") from None
        if len(raw) > limit:
            raise SafeError("response_size_limit_exceeded")
        return raw


def read_private_env(path: Path) -> dict[str, str]:
    """Read literal dotenv assignments, never source/eval a shell file."""
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.getuid():
                raise SafeError("credentials_file_must_be_owned_by_current_user_and_mode_0600")
            content = stream.read(65537)
    except SafeError:
        raise
    except (OSError, UnicodeError):
        raise SafeError("credentials_file_unreadable") from None
    if len(content) > 65536:
        raise SafeError("credentials_file_size_limit_exceeded")
    allowed = {"ADMIN_USERNAME", "ADMIN_PASSWORD", "ADMIN_ACCESS_TOKEN"}
    values = {}
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, value = line.partition("=")
        key = key.strip()
        if separator != "=" or key not in allowed or key in values:
            raise SafeError("credentials_file_invalid_assignment")
        try:
            parts = shlex.split(value, comments=True, posix=True)
        except ValueError:
            raise SafeError("credentials_file_invalid_quoting") from None
        if len(parts) != 1 or not parts[0]:
            raise SafeError("credentials_file_empty_or_invalid_value")
        values[key] = parts[0]
    if not values.get("ADMIN_ACCESS_TOKEN") and not (
        values.get("ADMIN_USERNAME") and values.get("ADMIN_PASSWORD")):
        raise SafeError("credentials_file_requires_admin_username_password_or_existing_access_token")
    return values


def validate_lock(lock: dict) -> None:
    try:
        if lock["schemaVersion"] != 1 or not isinstance(lock["plugins"], list) or not lock["plugins"]:
            raise ValueError
        if not SAFE_COMPONENT.fullmatch(lock["host"]["tag"]):
            raise ValueError
        seen = set()
        for plugin in lock["plugins"]:
            key, version = plugin["key"], plugin["version"]
            if key in seen or not SAFE_COMPONENT.fullmatch(key) or not SAFE_COMPONENT.fullmatch(version):
                raise ValueError
            seen.add(key)
            if not SHA256.fullmatch(plugin["sha256"]) or plugin["apiVersion"] != 1:
                raise ValueError
            if plugin.get("kind", "task") != "task" or plugin.get("minApiVersion", 1) > 1:
                raise ValueError
            source_url = plugin["sourceUrl"]
            if not source_url.startswith(OFFICIAL_REPO):
                raise ValueError
            remainder = source_url[len(OFFICIAL_REPO):]
            commit, _, path = remainder.partition("/")
            if not COMMIT.fullmatch(commit) or path != f"plugins/tasks/{key}/{version}/plugin.js":
                raise ValueError
            if "icon" in plugin:
                icon = plugin["icon"]
                media = {"image/svg+xml": "svg", "image/png": "png"}[icon["mediaType"]]
                if not SHA256.fullmatch(icon["sha256"]) or icon["sourceUrl"] != (
                    f"{OFFICIAL_REPO}{commit}/plugins/tasks/{key}/icon.{media}"):
                    raise ValueError
    except (ValueError, TypeError, KeyError):
        raise SafeError("invalid_official_catalog_lock") from None


def encrypted_password(password: str, key: dict) -> dict:
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding, rsa
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError:
        raise SafeError("encrypted_login_requires_cryptography_dependency") from None
    try:
        if key.get("enabled") is not True or not isinstance(key.get("kid"), str) or not key["kid"]:
            raise ValueError
        public = serialization.load_pem_public_key(key["public_key"].encode())
        if not isinstance(public, rsa.RSAPublicKey) or public.key_size < 2048:
            raise ValueError
        plaintext = password.encode("utf-8")
        oaep = lambda label: padding.OAEP(mgf=padding.MGF1(hashes.SHA256()),
                                          algorithm=hashes.SHA256(), label=label)
        if len(plaintext) <= public.key_size // 8 - 2 * 32 - 2:
            ciphertext = base64.b64encode(public.encrypt(plaintext, oaep(None))).decode()
        else:
            # Official v2 envelope supports long Unicode passwords.
            secret, nonce = os.urandom(32), os.urandom(12)
            wrapped = public.encrypt(secret, oaep(b"password-v2"))
            sealed = AESGCM(secret).encrypt(nonce, plaintext, ("password-v2:" + key["kid"]).encode())
            ciphertext = "v2." + ".".join(base64.b64encode(part).decode()
                                           for part in (wrapped, nonce, sealed))
        return {"password_encrypted": ciphertext, "encryption_key_id": key["kid"]}
    except Exception:
        raise SafeError("encrypted_login_key_or_password_invalid") from None


class NewAPI:
    def __init__(self, base_url: str, transport: HTTPTransport):
        require_https(base_url)
        parts = urlsplit(base_url)
        if parts.path not in ("", "/") or parts.query:
            raise SafeError("base_url_must_be_https_origin")
        self.base_url = base_url.rstrip("/")
        self.transport = transport
        self.token = None

    def request(self, path: str, *, method="GET", payload=None):
        headers = {"Authorization": "Bearer " + self.token} if self.token else {}
        raw = self.transport.request(self.base_url + path, method=method,
                                     payload=payload, headers=headers)
        try:
            result = json.loads(raw)
            if not isinstance(result, dict) or result.get("success") is not True:
                raise SafeError("api_request_rejected")
            return result.get("data")
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise SafeError("api_invalid_json") from None

    def login(self, credentials: dict, status: dict) -> None:
        if credentials.get("ADMIN_ACCESS_TOKEN"):
            self.token = credentials["ADMIN_ACCESS_TOKEN"]
        else:
            if status.get("password_login_enabled") is not True:
                raise SafeError("password_login_unavailable_use_existing_root_session")
            username, password = credentials.get("ADMIN_USERNAME"), credentials.get("ADMIN_PASSWORD")
            if not isinstance(username, str) or not username or not isinstance(password, str) or not password:
                raise SafeError("admin_credentials_missing")
            payload = {"username": username}
            if status.get("password_login_encryption_enabled") is True:
                key = self.request("/api/user/login/encryption-key")
                if not isinstance(key, dict):
                    raise SafeError("encrypted_login_key_unavailable")
                payload.update(encrypted_password(password, key))
            else:
                payload["password"] = password
            data = self.request("/api/user/login", method="POST", payload=payload)
            if not isinstance(data, dict) or not isinstance(data.get("access_token"), str) or not data["access_token"]:
                raise SafeError("login_incomplete_or_additional_verification_required")
            self.token = data["access_token"]
        # Recheck the authenticated identity, rather than trust login's user DTO.
        user = self.request("/api/user/self")
        if not isinstance(user, dict) or user.get("role") != 100:
            raise SafeError("root_identity_required")


@dataclass
class Report:
    mode: str
    items: list[dict]
    error: str = ""
    root_verified: bool = False
    runtime_verified: bool = False

    @property
    def exit_code(self) -> int:
        return int(bool(self.error) or any(item["state"] in ("failed", "blocked") for item in self.items))

    def emit(self, output: TextIO) -> None:
        print(json.dumps({"mode": self.mode, "root_verified": self.root_verified,
                          "runtime_verified": self.runtime_verified, "error": self.error,
                          "items": self.items, "exit_code": self.exit_code},
                         ensure_ascii=False, indent=2), file=output)


class Installer:
    def __init__(self, lock: dict, api: NewAPI, source_transport: HTTPTransport):
        validate_lock(lock)
        self.lock, self.api, self.sources = lock, api, source_transport

    def _download(self, entry: dict, limit=SOURCE_LIMIT) -> bytes:
        source = self.sources.get_bytes(entry["sourceUrl"], limit=limit)
        if hashlib.sha256(source).hexdigest() != entry["sha256"]:
            raise SafeError("source_sha256_mismatch")
        return source

    def _rows(self) -> dict[str, dict]:
        rows = self.api.request("/api/plugin/task")
        if not isinstance(rows, list):
            raise SafeError("plugin_list_invalid")
        result = {}
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("meta"), dict):
                raise SafeError("plugin_list_invalid")
            key = row["meta"].get("key")
            if not isinstance(key, str) or key in result:
                raise SafeError("plugin_list_invalid")
            result[key] = row
        return result

    def _runtime(self) -> dict:
        result = self.api.request("/api/plugin/task/runtime/status")
        if (not isinstance(result, dict) or not isinstance(result.get("plugin_errors"), dict)
                or not isinstance(result.get("last_rebuild"), dict)
                or type(result.get("current_generation")) is not int
                or result["last_rebuild"].get("status") not in ("success", "partial", "failed", "never")):
            raise SafeError("runtime_status_invalid")
        return result

    def _versions(self, key: str) -> list[dict]:
        result = self.api.request(f"/api/plugin/task/{key}/versions")
        if not isinstance(result, list) or any(not isinstance(row, dict) for row in result):
            raise SafeError("plugin_versions_invalid")
        return result

    @staticmethod
    def _configuration(row: dict | None):
        if row is None:
            return None
        return {field: row.get(field) for field in
                ("meta", "source", "source_hash", "enabled", "active", "remark")}

    def _check_global_enabled(self) -> None:
        # rc.45 /api/status has no task-plugin feature flag. The option map
        # includes the effective setting, including the environment default.
        options = self.api.request("/api/option/")
        if not isinstance(options, list):
            raise SafeError("plugin_feature_status_unavailable")
        matches = [row.get("value") for row in options
                   if isinstance(row, dict) and row.get("key") == "TaskPluginEnabled"]
        if len(matches) != 1 or matches[0] not in ("true", True):
            raise SafeError("plugin_feature_disabled_or_unavailable")

    def _official_override(self, plugin: dict, row: dict) -> bool:
        old_version = row["meta"].get("version")
        if not isinstance(old_version, str) or not SAFE_COMPONENT.fullmatch(old_version):
            return False
        # Verify older overrides against this same immutable official repository
        # commit. A private fork at an official key remains protected.
        url = plugin["sourceUrl"].rsplit("/", 2)[0] + f"/{old_version}/plugin.js"
        try:
            old_source = self.sources.get_bytes(url)
        except SafeError:
            return False
        return hashlib.sha256(old_source).hexdigest() == row.get("source_hash")

    def _prepare(self, plugin: dict, row: dict | None) -> dict:
        source = self._download(plugin)
        try:
            source_text = source.decode("utf-8")
        except UnicodeDecodeError:
            raise SafeError("plugin_source_not_utf8") from None
        versions = self._versions(plugin["key"])
        saved = next((version for version in versions
                      if isinstance(version, dict) and version.get("version") == plugin["version"]), None)
        if saved is not None and saved.get("source_hash") != plugin["sha256"]:
            raise SafeError("version_conflict")
        enabled, remark = True, ""
        action = "upload"
        if row is not None:
            if not isinstance(row.get("enabled"), bool) or not isinstance(row.get("remark", ""), str):
                raise SafeError("existing_plugin_configuration_invalid")
            enabled, remark = row["enabled"], row.get("remark", "")
            if row["meta"].get("version") == plugin["version"]:
                if row.get("source_hash") != plugin["sha256"]:
                    raise SafeError("version_conflict")
                action = "already_factory" if row.get("source") == "factory" else "already_installed"
            elif row.get("source") != "factory" and not self._official_override(plugin, row):
                raise SafeError("custom_override_protected")
        if saved is not None and action == "upload":
            # Activating an existing version must preserve its own stored settings.
            if saved.get("enabled") != enabled or saved.get("remark", "") != remark:
                raise SafeError("saved_version_configuration_conflict")
            action = "activate_existing"
        payload = {"source": source_text, "sourceSha256": plugin["sha256"],
                   "enabled": enabled, "remark": remark, "force": False}
        if "icon" in plugin and action == "upload":
            icon = self._download(plugin["icon"], limit=ICON_LIMIT)
            payload["icon"] = f"data:{plugin['icon']['mediaType']};base64," + base64.b64encode(icon).decode()
        return {"action": action, "payload": payload, "enabled": enabled,
                "saved": saved,
                "activate": action == "activate_existing" or (
                    action == "upload" and row is not None and row.get("source") != "factory")}

    def run(self, credentials: dict, *, install=False, output: TextIO = sys.stdout) -> Report:
        report = Report("install" if install else "plan", [
            {"key": plugin["key"], "version": plugin["version"],
             "action": "pending", "state": "blocked", "detail": "not_started"}
            for plugin in self.lock["plugins"]])
        prepared = {}
        try:
            status = self.api.request("/api/status")
            if not isinstance(status, dict) or status.get("version", "").lstrip("v") != self.lock["host"]["tag"].lstrip("v"):
                raise SafeError("host_version_mismatch")
            if status.get("plugin_feature_enabled") is False:
                raise SafeError("plugin_feature_disabled")
            self.api.login(credentials, status)
            report.root_verified = True
            self._check_global_enabled()
            rows = self._rows()
            self._runtime()  # Read-only capability and response-shape probe.
            for plugin, item in zip(self.lock["plugins"], report.items):
                try:
                    plan = self._prepare(plugin, rows.get(plugin["key"]))
                    prepared[plugin["key"]] = plan
                    item.update(action=plan["action"], state="planned", detail="sha256_verified")
                except SafeError as error:
                    item.update(state="failed", detail=str(error))
            if report.exit_code:
                # Complete preflight for every target; do not partially install an
                # unverified catalog or one containing a protected custom override.
                report.error = "preflight_failed_no_plugin_writes"
                return report
            if not install:
                return report
            for plugin, item in zip(self.lock["plugins"], report.items):
                plan = prepared[plugin["key"]]
                try:
                    if plan["action"] in ("upload", "activate_existing"):
                        # The API has no conditional-write/ETag contract. Recheck
                        # immediately before each write and never undo another
                        # administrator's concurrent edits after a failed upload.
                        latest = self._rows().get(plugin["key"])
                        latest_saved = next((version for version in self._versions(plugin["key"])
                            if version.get("version") == plugin["version"]), None)
                        if self._configuration(latest) != self._configuration(rows.get(plugin["key"])) or (
                                latest_saved != plan["saved"]):
                            raise SafeError("concurrent_configuration_change")
                    if plan["action"] == "upload":
                        self.api.request("/api/plugin/task", method="POST", payload=plan["payload"])
                    if plan["activate"]:
                        self.api.request(f"/api/plugin/task/{plugin['key']}/activate", method="POST",
                                         payload={"version": plugin["version"]})
                    item.update(state="awaiting_readback", detail="")
                except SafeError as error:
                    item.update(state="failed", detail="install_" + str(error))
            try:
                after, runtime = self._rows(), self._runtime()
                runtime_bad = bool(runtime.get("database_error")) or (
                    runtime.get("last_rebuild", {}).get("status") in ("failed", "partial"))
                if not isinstance(runtime.get("current_generation"), int) or runtime["current_generation"] <= 0:
                    runtime_bad = True
                for plugin, item in zip(self.lock["plugins"], report.items):
                    if item["state"] == "failed":
                        continue
                    row, plan = after.get(plugin["key"]), prepared[plugin["key"]]
                    if row is None or row["meta"].get("version") != plugin["version"] or (
                        row.get("source_hash") != plugin["sha256"] or row.get("enabled") != plan["enabled"] or
                        row.get("active") is not True or row.get("remark", "") != plan["payload"]["remark"]):
                        item.update(state="failed", detail="readback_version_hash_or_configuration_mismatch")
                    elif runtime_bad or plugin["key"] in runtime["plugin_errors"]:
                        item.update(state="failed", detail="runtime_readback_failed")
                    elif plan["enabled"] and row.get("runtime_status") != "registered":
                        item.update(state="failed", detail="runtime_plugin_not_registered")
                    elif not plan["enabled"] and row.get("runtime_status") not in ("disabled", "disabled_fallback"):
                        item.update(state="failed", detail="runtime_disabled_state_not_preserved")
                    else:
                        item.update(state="verified" if plan["enabled"] else "verified_preserved_disabled",
                                    detail="list_hash_configuration_and_runtime_verified")
                report.runtime_verified = not runtime_bad and not runtime["plugin_errors"]
                if not report.runtime_verified:
                    report.error = "runtime_readback_unhealthy"
            except SafeError as error:
                report.error = "readback_" + str(error)
                for item in report.items:
                    if item["state"] != "failed":
                        item.update(state="failed", detail="readback_unavailable")
            return report
        except SafeError as error:
            report.error = str(error)
            for item in report.items:
                if item["state"] == "blocked":
                    item["detail"] = "precondition_failed"
            return report
        finally:
            # Do not persist access/refresh tokens or include them in reports.
            self.api.token = None
            report.emit(output)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs="?", choices=("plan", "install"), default="plan")
    parser.add_argument("--base-url", required=True, help="HTTPS origin of the upgraded New API host")
    parser.add_argument("--credentials-file", required=True, type=Path,
                        help="private 0600 dotenv file; contents are never executed or printed")
    parser.add_argument("--lock", type=Path, default=Path(__file__).parents[1] / "catalog.lock.json")
    parser.add_argument("--ca-file", help="optional trusted CA PEM; TLS verification remains enabled")
    parser.add_argument("--timeout", type=float, default=30)
    args = parser.parse_args(argv)
    try:
        if args.timeout <= 0 or args.timeout > 120:
            raise SafeError("timeout_must_be_between_0_and_120_seconds")
        try:
            lock = json.loads(args.lock.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            raise SafeError("catalog_lock_unreadable_or_invalid_json") from None
        validate_lock(lock)
        credentials = read_private_env(args.credentials_file)
        transport = HTTPTransport(timeout=args.timeout, ca_file=args.ca_file)
        api = NewAPI(args.base_url, transport)
        return Installer(lock, api, transport).run(credentials, install=args.command == "install").exit_code
    except SafeError as error:
        print(json.dumps({"error": str(error), "exit_code": 1}), file=sys.stderr)
        return 1
    except Exception:
        # A traceback may retain credential-bearing locals or remote response text.
        print('{"error":"unexpected_local_error","exit_code":1}', file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
