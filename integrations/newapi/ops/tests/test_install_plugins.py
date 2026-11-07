"""Protocol tests use an HTTP fixture, never a real New API deployment."""
from __future__ import annotations

import base64
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import ssl
import tempfile
import unittest
from urllib.error import HTTPError
from urllib.parse import urlsplit


MODULE = Path(__file__).parents[1] / "install_plugins.py"
COMMIT = "a" * 40
REPO = f"https://raw.githubusercontent.com/QuantumNous/new-api-plugins/{COMMIT}"
PASSWORD = "secret-password-do-not-log"
TOKEN = "secret-access-token-do-not-log"


def target(key="incho", version="1.0.2", factory=False):
    source = f"// official fixture {key} {version}\n".encode()
    return {
        "key": key, "version": version, "sha256": hashlib.sha256(source).hexdigest(),
        "sourceUrl": f"{REPO}/plugins/tasks/{key}/{version}/plugin.js",
        "kind": "task", "apiVersion": 1, "minApiVersion": 1,
        "delivery": "factory" if factory else "marketplace", "factoryIncluded": factory,
    }, source


def installed(plugin, layer="override", enabled=True, **changes):
    row = {"meta": {"key": plugin["key"], "version": plugin["version"]},
           "source": layer, "source_hash": plugin["sha256"], "enabled": enabled,
           "active": True, "runtime_status": "registered" if enabled else "disabled",
           "remark": "keep user's remark"}
    row.update(changes)
    return row


class Response:
    def __init__(self, data):
        self.data = data if isinstance(data, bytes) else json.dumps(data).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, limit):
        return self.data[:limit]


class FakeHTTP:
    """An exact route fixture that also models upload/activation state changes."""
    def __init__(self, plugins, rows=()):
        self.plugins = plugins
        self.rows = copy.deepcopy(list(rows))
        self.versions = {p["key"]: [] for p, _ in plugins}
        self.sources = {p["sourceUrl"]: source for p, source in plugins}
        self.calls = []
        self.role = 100
        self.version = "v1.0.0-rc.45"
        self.feature = None
        self.global_enabled = True
        self.encrypted = False
        self.encryption_key = None
        self.upload_fail = set()
        self.readback_missing = set()
        self.runtime_error = False
        self.runtime_api_fail = False
        self.echo_failure = False
        self.login_challenge = False
        self.race_on_write_check = False
        self.list_reads = 0
        self.runtime_malformed = False
        self.did_upload = False

    def open(self, request, timeout):
        url, method = request.full_url, request.get_method()
        body = json.loads(request.data) if request.data else None
        headers = dict(request.header_items())
        self.calls.append((method, url, body, headers))
        path = urlsplit(url).path
        if url.startswith(REPO):
            if headers.get("Authorization"):
                raise AssertionError("management bearer leaked to source server")
            if url not in self.sources:
                raise HTTPError(url, 404, TOKEN, {}, io.BytesIO(PASSWORD.encode()))
            return Response(self.sources[url])
        if path == "/api/status":
            result = {"version": self.version, "password_login_enabled": True,
                      "password_login_encryption_enabled": self.encrypted}
            if self.feature is not None:
                result["plugin_feature_enabled"] = self.feature
            return self.ok(result)
        if path == "/api/user/login/encryption-key":
            return self.ok(self.encryption_key)
        if path == "/api/user/login":
            if self.echo_failure:
                return Response({"success": False, "message": PASSWORD + TOKEN})
            if self.login_challenge:
                return self.ok({"require_2fa": True, "challenge": TOKEN})
            return self.ok({"access_token": TOKEN, "user": {"role": self.role}})
        if headers.get("Authorization") != "Bearer " + TOKEN:
            raise AssertionError("missing console bearer token")
        if path == "/api/user/self":
            return self.ok({"role": self.role, "id": 1, "username": "root"})
        if path == "/api/option/":
            return self.ok([{"key": "TaskPluginEnabled", "value": str(self.global_enabled).lower()},
                            {"key": "unrelated", "value": PASSWORD}])
        if path == "/api/plugin/task/runtime/status":
            if self.did_upload and self.runtime_malformed:
                return self.ok({"plugin_errors": {}, "last_rebuild": [PASSWORD]})
            if self.did_upload and self.runtime_api_fail:
                raise HTTPError(url, 500, TOKEN, {}, io.BytesIO(PASSWORD.encode()))
            return self.ok({"current_generation": 3, "database_error": "",
                            "last_rebuild": {"status": "success"},
                            "plugin_errors": {"incho": PASSWORD} if self.runtime_error else {}})
        if path.endswith("/versions"):
            return self.ok(self.versions[path.split("/")[-2]])
        if path.endswith("/activate"):
            key = path.split("/")[-2]
            saved = next(row for row in self.versions[key] if row["version"] == body["version"])
            self.rows = [row for row in self.rows if row["meta"]["key"] != key]
            plugin = next(p for p, _ in self.plugins if p["key"] == key)
            self.rows.append(installed(plugin, enabled=saved["enabled"], remark=saved["remark"]))
            return self.ok(None)
        if path == "/api/plugin/task" and method == "POST":
            self.did_upload = True
            plugin = next(p for p, source in self.plugins if source.decode() == body["source"])
            key = plugin["key"]
            if key in self.upload_fail:
                return Response({"success": False, "message": PASSWORD + TOKEN})
            old = next((row for row in self.rows if row["meta"]["key"] == key), None)
            active = old is None or old["source"] == "factory"
            saved = {"key": key, "version": plugin["version"], "source_hash": plugin["sha256"],
                     "enabled": body["enabled"], "active": active, "remark": body["remark"]}
            self.versions[key].append(saved)
            if active:
                self.rows = [row for row in self.rows if row["meta"]["key"] != key]
                self.rows.append(installed(plugin, enabled=body["enabled"], remark=body["remark"]))
            return self.ok({"plugin": saved})
        if path == "/api/plugin/task" and method == "GET":
            self.list_reads += 1
            if self.race_on_write_check and self.list_reads == 2:
                self.rows.append(installed(self.plugins[0][0], enabled=False, source_hash="d" * 64))
            return self.ok([row for row in self.rows if not (
                self.did_upload and row["meta"]["key"] in self.readback_missing)])
        raise AssertionError(f"unexpected protocol route {method} {path}")

    @staticmethod
    def ok(data):
        return Response({"success": True, "data": data, "message": ""})

    @property
    def plugin_writes(self):
        return [(method, url, body) for method, url, body, _ in self.calls
                if method != "GET" and "/api/plugin/" in url]


class InstallerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if MODULE.exists():
            spec = importlib.util.spec_from_file_location("newapi_install_plugins", MODULE)
            cls.mod = importlib.util.module_from_spec(spec)
            import sys
            sys.modules[spec.name] = cls.mod
            spec.loader.exec_module(cls.mod)

    def setUp(self):
        self.assertTrue(MODULE.exists(), "batch installer implementation is missing")
        self.plugin, self.source = target()
        self.plugins = [(self.plugin, self.source)]

    def run_installer(self, fake, install=True, credentials=None):
        lock = {"schemaVersion": 1, "host": {"tag": "v1.0.0-rc.45"},
                "plugins": [p for p, _ in fake.plugins]}
        transport = self.mod.HTTPTransport(opener=fake)
        api = self.mod.NewAPI("https://api.example.test", transport)
        output = io.StringIO()
        report = self.mod.Installer(lock, api, transport).run(
            credentials or {"ADMIN_USERNAME": "root", "ADMIN_PASSWORD": PASSWORD},
            install=install, output=output)
        return report, output.getvalue()

    def test_plan_is_default_and_never_writes_plugin_configuration(self):
        fake = FakeHTTP(self.plugins)
        report, text = self.run_installer(fake, install=False)
        self.assertEqual(report.exit_code, 0)
        self.assertEqual(report.items[0]["action"], "upload")
        self.assertEqual(fake.plugin_writes, [])
        self.assertIn("plan", text)

    def test_upload_verifies_list_runtime_and_bearer_without_cookie_headers(self):
        fake = FakeHTTP(self.plugins)
        report, text = self.run_installer(fake)
        self.assertEqual(report.exit_code, 0, text)
        self.assertEqual(report.items[0]["state"], "verified")
        self.assertEqual(len(fake.plugin_writes), 1)
        body = fake.plugin_writes[0][2]
        self.assertEqual(body["sourceSha256"], self.plugin["sha256"])
        self.assertIs(body["force"], False)
        self.assertIs(body["enabled"], True)
        paths = [urlsplit(url).path for _, url, _, _ in fake.calls]
        self.assertEqual(paths[0], "/api/status")
        self.assertGreaterEqual(paths.count("/api/plugin/task/runtime/status"), 2)
        self.assertFalse(any("New-api-user" in headers or "Cookie" in headers for *_, headers in fake.calls))

    def test_factory_same_hash_is_verified_without_reupload(self):
        fake = FakeHTTP(self.plugins, [installed(self.plugin, "factory")])
        report, _ = self.run_installer(fake)
        self.assertEqual(report.exit_code, 0)
        self.assertEqual(report.items[0]["action"], "already_factory")
        self.assertEqual(fake.plugin_writes, [])

    def test_source_sha_mismatch_prevents_all_plugin_writes(self):
        fake = FakeHTTP(self.plugins)
        fake.sources[self.plugin["sourceUrl"]] = b"tampered"
        report, text = self.run_installer(fake)
        self.assertEqual(report.exit_code, 1)
        self.assertIn("sha256", text)
        self.assertEqual(fake.plugin_writes, [])

    def test_nonroot_stops_before_listing_or_mutation(self):
        fake = FakeHTTP(self.plugins)
        fake.role = 10
        report, text = self.run_installer(fake)
        self.assertEqual(report.exit_code, 1)
        self.assertIn("root", text)
        self.assertFalse(any("/api/plugin/" in url for _, url, _, _ in fake.calls))

    def test_same_key_version_different_hash_is_not_force_overwritten(self):
        row = installed(self.plugin, source_hash="f" * 64)
        fake = FakeHTTP(self.plugins, [row])
        report, text = self.run_installer(fake)
        self.assertEqual(report.exit_code, 1)
        self.assertIn("version_conflict", text)
        self.assertEqual(fake.plugin_writes, [])

    def test_inactive_version_hash_conflict_is_detected(self):
        fake = FakeHTTP(self.plugins)
        fake.versions["incho"] = [{"version": self.plugin["version"], "source_hash": "f" * 64}]
        report, text = self.run_installer(fake)
        self.assertEqual(report.exit_code, 1)
        self.assertIn("version_conflict", text)
        self.assertEqual(fake.plugin_writes, [])

    def test_same_installed_version_disabled_and_remark_are_preserved(self):
        row = installed(self.plugin, enabled=False)
        fake = FakeHTTP(self.plugins, [row])
        report, _ = self.run_installer(fake)
        self.assertEqual(report.exit_code, 0)
        self.assertEqual(report.items[0]["state"], "verified_preserved_disabled")
        self.assertEqual(fake.rows, [row])
        self.assertEqual(fake.plugin_writes, [])

    def test_update_official_override_activates_new_version_preserving_disabled_remark(self):
        old, old_source = target(version="1.0.1")
        fake = FakeHTTP(self.plugins, [installed(old, enabled=False)])
        fake.sources[old["sourceUrl"]] = old_source
        fake.versions["incho"] = [{"version": "1.0.1", "source_hash": old["sha256"],
                                    "enabled": False, "active": True}]
        report, text = self.run_installer(fake)
        self.assertEqual(report.exit_code, 0, text)
        self.assertEqual(len(fake.plugin_writes), 2)
        self.assertTrue(fake.plugin_writes[1][1].endswith("/incho/activate"))
        self.assertFalse(fake.plugin_writes[0][2]["enabled"])
        self.assertEqual(fake.plugin_writes[0][2]["remark"], "keep user's remark")

    def test_custom_plugin_key_and_configuration_are_untouched(self):
        custom, _ = target("my-private-plugin")
        row = installed(custom, enabled=False)
        fake = FakeHTTP(self.plugins, [row])
        report, _ = self.run_installer(fake)
        self.assertEqual(report.exit_code, 0)
        self.assertIn(row, fake.rows)
        self.assertTrue(all("my-private-plugin" not in url for _, url, _ in fake.plugin_writes))

    def test_custom_override_at_official_key_is_protected(self):
        old, old_source = target(version="1.0.1")
        fake = FakeHTTP(self.plugins, [installed(old, source_hash="e" * 64)])
        fake.sources[old["sourceUrl"]] = old_source
        report, text = self.run_installer(fake)
        self.assertEqual(report.exit_code, 1)
        self.assertIn("custom_override_protected", text)
        self.assertEqual(fake.plugin_writes, [])

    def test_partial_upload_failure_is_nonzero_and_reports_every_target(self):
        second = target("typesafe", "1.2.0")
        fake = FakeHTTP(self.plugins + [second])
        fake.upload_fail.add("incho")
        report, text = self.run_installer(fake)
        self.assertEqual(report.exit_code, 1)
        self.assertEqual(len(report.items), 2)
        states = {item["key"]: item["state"] for item in report.items}
        self.assertEqual(states["incho"], "failed")
        self.assertEqual(states["typesafe"], "verified")
        self.assertNotIn(PASSWORD, text)
        self.assertNotIn(TOKEN, text)

    def test_list_readback_missing_target_is_failure(self):
        fake = FakeHTTP(self.plugins)
        fake.readback_missing.add("incho")
        report, text = self.run_installer(fake)
        self.assertEqual(report.exit_code, 1)
        self.assertIn("readback", text)

    def test_runtime_readback_api_failure_is_failure_without_sensitive_body(self):
        fake = FakeHTTP(self.plugins)
        fake.runtime_api_fail = True
        report, text = self.run_installer(fake)
        self.assertEqual(report.exit_code, 1)
        self.assertIn("readback", text)
        self.assertNotIn(PASSWORD, text)
        self.assertNotIn(TOKEN, text)

    def test_runtime_plugin_error_is_failure_without_sensitive_error_text(self):
        fake = FakeHTTP(self.plugins)
        fake.runtime_error = True
        report, text = self.run_installer(fake)
        self.assertEqual(report.exit_code, 1)
        self.assertIn("runtime", text)
        self.assertNotIn(PASSWORD, text)

    def test_version_mismatch_stops_before_password_is_sent(self):
        fake = FakeHTTP(self.plugins)
        fake.version = "v0.13.2"
        report, text = self.run_installer(fake)
        self.assertEqual(report.exit_code, 1)
        self.assertIn("host_version_mismatch", text)
        self.assertFalse(any(method == "POST" for method, *_ in fake.calls))

    def test_explicit_feature_disabled_is_rejected(self):
        fake = FakeHTTP(self.plugins)
        fake.feature = False
        report, text = self.run_installer(fake)
        self.assertEqual(report.exit_code, 1)
        self.assertIn("plugin_feature_disabled", text)
        self.assertFalse(any(method == "POST" for method, *_ in fake.calls))

    def test_effective_global_plugin_switch_is_checked_without_reconfiguration(self):
        fake = FakeHTTP(self.plugins)
        fake.global_enabled = False
        report, text = self.run_installer(fake)
        self.assertEqual(report.exit_code, 1)
        self.assertIn("plugin_feature_disabled", text)
        self.assertNotIn(PASSWORD, text)
        self.assertEqual(fake.plugin_writes, [])

    def test_login_failure_does_not_echo_sensitive_response(self):
        fake = FakeHTTP(self.plugins)
        fake.echo_failure = True
        report, text = self.run_installer(fake)
        self.assertEqual(report.exit_code, 1)
        self.assertNotIn(PASSWORD, text)
        self.assertNotIn(TOKEN, text)

    def test_existing_token_avoids_password_login_and_still_checks_root(self):
        fake = FakeHTTP(self.plugins)
        report, _ = self.run_installer(fake, credentials={"ADMIN_ACCESS_TOKEN": TOKEN})
        self.assertEqual(report.exit_code, 0)
        self.assertFalse(any(url.endswith("/login") for _, url, _, _ in fake.calls))

    def test_login_challenge_does_not_attempt_to_bypass_second_factor(self):
        fake = FakeHTTP(self.plugins)
        fake.login_challenge = True
        report, text = self.run_installer(fake)
        self.assertEqual(report.exit_code, 1)
        self.assertIn("additional_verification_required", text)
        self.assertNotIn(TOKEN, text)
        self.assertEqual(fake.plugin_writes, [])

    def test_concurrent_configuration_change_aborts_target_before_upload(self):
        fake = FakeHTTP(self.plugins)
        fake.race_on_write_check = True
        report, text = self.run_installer(fake)
        self.assertEqual(report.exit_code, 1)
        self.assertIn("concurrent_configuration_change", text)
        self.assertEqual(fake.plugin_writes, [])

    def test_malformed_runtime_readback_is_reported_without_traceback(self):
        fake = FakeHTTP(self.plugins)
        fake.runtime_malformed = True
        report, text = self.run_installer(fake)
        self.assertEqual(report.exit_code, 1)
        self.assertIn("runtime_status_invalid", text)
        self.assertNotIn(PASSWORD, text)

    def test_icon_is_hash_verified_and_uploaded_as_data_uri(self):
        icon = b"<svg xmlns='http://www.w3.org/2000/svg'/>"
        self.plugin["icon"] = {"sourceUrl": f"{REPO}/plugins/tasks/incho/icon.svg",
                               "sha256": hashlib.sha256(icon).hexdigest(), "mediaType": "image/svg+xml"}
        fake = FakeHTTP(self.plugins)
        fake.sources[self.plugin["icon"]["sourceUrl"]] = icon
        report, text = self.run_installer(fake)
        self.assertEqual(report.exit_code, 0, text)
        self.assertEqual(fake.plugin_writes[0][2]["icon"], "data:image/svg+xml;base64," + base64.b64encode(icon).decode())

    def test_icon_hash_mismatch_prevents_plugin_upload(self):
        self.plugin["icon"] = {"sourceUrl": f"{REPO}/plugins/tasks/incho/icon.svg",
                               "sha256": "a" * 64, "mediaType": "image/svg+xml"}
        fake = FakeHTTP(self.plugins)
        fake.sources[self.plugin["icon"]["sourceUrl"]] = b"tampered icon"
        report, text = self.run_installer(fake)
        self.assertEqual(report.exit_code, 1)
        self.assertIn("sha256", text)
        self.assertEqual(fake.plugin_writes, [])

    def test_redirect_is_rejected_before_bearer_can_be_forwarded(self):
        with self.assertRaises(self.mod.SafeError):
            self.mod.RejectRedirect().redirect_request(None, None, 302, TOKEN, {}, "https://attacker.test")

    def test_transport_enforces_https_and_default_certificate_validation(self):
        with self.assertRaises(self.mod.SafeError):
            self.mod.NewAPI("http://api.example.test", self.mod.HTTPTransport(opener=FakeHTTP([])))
        transport = self.mod.HTTPTransport()
        self.assertEqual(transport.ssl_context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(transport.ssl_context.check_hostname)
        with self.assertRaises(self.mod.SafeError):
            transport.get_bytes("http://example.test/plugin.js")

    def test_lock_rejects_unpinned_or_unofficial_source_urls(self):
        for url in [self.plugin["sourceUrl"].replace(COMMIT, "main"),
                    self.plugin["sourceUrl"].replace("QuantumNous", "attacker"),
                    "https://example.test/plugin.js"]:
            with self.subTest(url=url):
                lock = {"schemaVersion": 1, "host": {"tag": "v1.0.0-rc.45"},
                        "plugins": [{**self.plugin, "sourceUrl": url}]}
                with self.assertRaises(self.mod.SafeError):
                    self.mod.validate_lock(lock)

    def test_private_env_file_is_parsed_without_shell_and_rejects_public_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "admin.env"
            path.write_text("ADMIN_USERNAME=root\nADMIN_PASSWORD='$(do-not-execute) `touch oops`'\n")
            path.chmod(0o600)
            values = self.mod.read_private_env(path)
            self.assertEqual(values["ADMIN_PASSWORD"], "$(do-not-execute) `touch oops`")
            path.chmod(0o644)
            with self.assertRaises(self.mod.SafeError):
                self.mod.read_private_env(path)

    def test_encrypted_login_matches_official_rsa_oaep_sha256_contract(self):
        try:
            from cryptography.hazmat.primitives import serialization, hashes
            from cryptography.hazmat.primitives.asymmetric import rsa, padding
        except ImportError:
            self.skipTest("optional cryptography dependency not installed")
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = private_key.public_key().public_bytes(serialization.Encoding.PEM,
                                                  serialization.PublicFormat.SubjectPublicKeyInfo).decode()
        fake = FakeHTTP(self.plugins)
        fake.encrypted = True
        fake.encryption_key = {"enabled": True, "kid": "fixture-kid", "public_key": pem}
        report, text = self.run_installer(fake)
        self.assertEqual(report.exit_code, 0, text)
        payload = next(body for _, url, body, _ in fake.calls if url.endswith("/login"))
        self.assertNotIn("password", payload)
        self.assertEqual(payload["encryption_key_id"], "fixture-kid")
        plaintext = private_key.decrypt(base64.b64decode(payload["password_encrypted"]),
            padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
        self.assertEqual(plaintext.decode(), PASSWORD)

    def test_long_unicode_password_uses_official_v2_envelope(self):
        try:
            from cryptography.hazmat.primitives import serialization, hashes
            from cryptography.hazmat.primitives.asymmetric import rsa, padding
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        except ImportError:
            self.skipTest("optional cryptography dependency not installed")
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = private_key.public_key().public_bytes(serialization.Encoding.PEM,
                                                  serialization.PublicFormat.SubjectPublicKeyInfo).decode()
        password = "长密码" * 90
        result = self.mod.encrypted_password(password, {"enabled": True, "kid": "kid", "public_key": pem})
        prefix, wrapped, nonce, ciphertext = result["password_encrypted"].split(".")
        self.assertEqual(prefix, "v2")
        key = private_key.decrypt(base64.b64decode(wrapped), padding.OAEP(
            mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=b"password-v2"))
        plaintext = AESGCM(key).decrypt(base64.b64decode(nonce), base64.b64decode(ciphertext), b"password-v2:kid")
        self.assertEqual(plaintext.decode(), password)


if __name__ == "__main__":
    unittest.main()
