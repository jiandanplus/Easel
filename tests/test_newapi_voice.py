"""Owner-bound private voice plugin contracts; mocked HTTP only."""
from __future__ import annotations

import argparse
import base64
import contextlib
import importlib
import http.server
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

SCRIPTS = Path(__file__).parents[1] / "skills/shared/scripts"
sys.path.insert(0, str(SCRIPTS))
import voice_clone as voice

MODULE = SCRIPTS / "newapi_voice.py"
ROOT = "https://gateway.test/custom/prefix"
KEY = "gateway-only-not-supplier-key"
UPLOAD = "task_" + "A" * 32
VOICE = "task_" + "B" * 32
REVIEW = "task_" + "C" * 32
READY = "task_" + "D" * 32
TASK = "task_" + "E" * 32
EXTERNAL = "https://audio.example.test/speech.mp3"
MP3 = b"ID3voice-audio"


class Response:
    def __init__(self, value, content_type=None):
        self.body = value if isinstance(value, bytes) else json.dumps(value).encode()
        self.headers = {"Content-Type": content_type or ("audio/mpeg" if isinstance(value, bytes) else "application/json")}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, limit=None):
        return self.body if limit is None else self.body[:limit]


class FakeHTTP:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def open(self, request, timeout):
        headers = {key.lower(): value for key, value in request.header_items()}
        content_type = headers.get("content-type", "")
        payload = json.loads(request.data) if request.data and content_type.startswith("application/json") else request.data
        self.calls.append({"method": request.get_method(), "url": request.full_url,
                           "headers": headers, "payload": payload, "timeout": timeout})
        if not self.responses:
            raise AssertionError("unexpected extra HTTP call / duplicate submit")
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result if isinstance(result, Response) else Response(result)


class NewAPIVoiceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if MODULE.exists():
            cls.gateway = importlib.import_module("newapi_voice")

    def setUp(self):
        self.assertTrue(MODULE.exists(), "explicit New API private voice client is missing")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.sample = Path(self.temp.name) / "sample.wav"
        self.sample.write_bytes(b"reference-audio")
        self.output = Path(self.temp.name) / "speech.mp3"
        self.args = argparse.Namespace(provider="minimax", model=None, sample=str(self.sample),
            sample_url=None, name=None, voice_id=None, text="你好，世界", emotion="愤怒",
            sample_text="样本中的对应文字", speed=1.0, output=str(self.output))
        self.env = {"VOICE_API_MODE": "new-api", "NEW_API_BASE_URL": ROOT, "NEW_API_KEY": KEY,
                    "NEW_API_VOICE_TIMEOUT": "5", "NEW_API_VOICE_POLL_INTERVAL": "0.1"}
        self.stdout = io.StringIO()
        self.stderr = io.StringIO()

    def invoke(self, command, responses, *, failure=False):
        fake = FakeHTTP(responses)
        with patch.dict(os.environ, self.env, clear=True), \
                patch.object(self.gateway.urllib.request, "build_opener", return_value=fake), \
                patch.object(self.gateway.time, "sleep"), contextlib.redirect_stdout(self.stdout), \
                contextlib.redirect_stderr(self.stderr):
            if failure:
                with self.assertRaises(SystemExit):
                    getattr(voice, "cmd_" + command)(self.args)
            else:
                self.assertEqual(getattr(voice, "cmd_" + command)(self.args), 0)
        return fake.calls

    def dash_result(self, ident=READY, status="OK", model="cosyvoice-v3-plus"):
        return {"output": {"task_id": ident, "task_status": "SUCCEEDED", "voice_task_id": ident,
                           "voice_status": status, "target_model": model}, "usage": {"count": 1}}

    def fish_result(self, status="SUCCESS", **fields):
        result = {"task_id": TASK, "status": status}
        if status == "SUCCESS":
            result.update(content_url=f"/v1/tasks/{TASK}/artifacts/audio/content", mime_type="audio/mpeg",
                          size=len(MP3), expires_at=int(time.time()) + 600)
        return {**result, **fields}

    def test_minimax_upload_clone_returns_only_public_voice_task_id(self):
        calls = self.invoke("enroll", [
            {"task_id": UPLOAD, "status": "SUCCESS", "file_task_id": UPLOAD},
            {"task_id": VOICE, "status": "SUCCESS", "voice_task_id": VOICE}])
        self.assertEqual([call["url"] for call in calls], [ROOT + "/easel/minimax/v1/files/upload", ROOT + "/easel/minimax/v1/voice_clone"])
        self.assertIn(b'name="purpose"\r\n\r\nvoice_clone', calls[0]["payload"])
        self.assertIn(b'name="file"; filename="sample.wav"', calls[0]["payload"])
        self.assertEqual(calls[1]["payload"], {"file_task_id": UPLOAD})
        self.assertIn("voice_id = " + VOICE, self.stdout.getvalue())
        self.assertFalse(any("GroupId" in call["url"] for call in calls))

    def test_minimax_synthesis_uses_public_voice_id_and_validated_complete_hex(self):
        self.args.voice_id = VOICE
        calls = self.invoke("clone", [{"task_id": TASK, "status": "SUCCESS", "data": {"audio": MP3.hex(), "status": 2},
                                       "extra_info": {"usage_characters": 5, "audio_format": "mp3"}}])
        self.assertEqual(calls[0]["url"], ROOT + "/easel/minimax/v1/t2a_v2")
        self.assertEqual(calls[0]["payload"], {"model": "easel-minimax-speech-2.8-hd", "voice_task_id": VOICE,
            "text": self.args.text, "speed": 1.0, "emotion": "angry"})
        self.assertEqual(self.output.read_bytes(), MP3)

    def test_minimax_rejects_supplier_voice_id_before_billable_post(self):
        self.args.voice_id = "raw-upstream-voice-id"
        self.assertEqual(self.invoke("clone", [], failure=True), [])

    def test_minimax_upload_business_error_does_not_try_clone_or_reupload(self):
        calls = self.invoke("enroll", [{"error": {"message": KEY}}], failure=True)
        self.assertEqual(len(calls), 1)
        self.assertNotIn(KEY, self.stderr.getvalue())

    def test_minimax_registration_failure_preserves_upload_recovery_id(self):
        calls = self.invoke("enroll", [{"task_id": UPLOAD, "status": "SUCCESS", "file_task_id": UPLOAD},
                                       {"error": {"message": KEY}}], failure=True)
        self.assertEqual(len(calls), 2)
        self.assertIn("已上传 file_task_id=" + UPLOAD, self.stderr.getvalue())
        self.assertIn("--resume-id " + UPLOAD, self.stderr.getvalue())
        self.assertNotIn("✅ 音色已登记", self.stdout.getvalue())

    def test_minimax_resume_reads_upload_stage_then_registers_without_reupload(self):
        self.args.resume_id = UPLOAD
        self.args.sample = None
        calls = self.invoke("enroll", [{"task_id": UPLOAD, "status": "SUCCESS", "file_task_id": UPLOAD},
            {"task_id": VOICE, "status": "SUCCESS", "voice_task_id": VOICE}])
        self.assertEqual([call["method"] for call in calls], ["GET", "POST"])
        self.assertEqual(calls[0]["url"], ROOT + "/easel/minimax/v1/tasks/" + UPLOAD)
        self.assertEqual(calls[1]["payload"], {"file_task_id": UPLOAD})
        self.assertFalse(any(call["url"].endswith("/files/upload") for call in calls))

    def test_minimax_resume_rejects_non_upload_stage_without_registration_post(self):
        self.args.resume_id = VOICE
        calls = self.invoke("enroll", [{"task_id": VOICE, "status": "SUCCESS", "voice_task_id": VOICE}], failure=True)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["method"], "GET")

    def test_minimax_pending_or_invalid_audio_never_falls_back(self):
        self.args.voice_id = VOICE
        for response in [
            {"task_id": TASK, "status": "IN_PROGRESS"},
            {"task_id": TASK, "status": "SUCCESS", "data": {"audio": "zz", "status": 2}},
            {"task_id": TASK, "status": "SUCCESS", "data": {"audio": MP3.hex(), "status": 1}},
        ]:
            with self.subTest(response=response):
                calls = self.invoke("clone", [response], failure=True)
                self.assertEqual(len(calls), 1)
                self.assertFalse(self.output.exists())

    def test_dashscope_enroll_create_once_and_verify_same_origin_until_ready(self):
        self.args.provider = "dashscope"
        self.args.sample_url = "https://sample.example.test/voice.wav"
        self.args.model = "cosyvoice-v3-plus"
        self.args.name = "myvoice"
        calls = self.invoke("enroll", [self.dash_result(VOICE, "UNVERIFIED"),
            self.dash_result(REVIEW, "DEPLOYING"), self.dash_result(READY)])
        self.assertEqual(calls[0]["payload"], {"model": "voice-enrollment", "input": {
            "action": "create_voice", "target_model": "cosyvoice-v3-plus", "prefix": "myvoice", "url": self.args.sample_url}})
        self.assertEqual(calls[0]["url"], ROOT + "/easel/dashscope/voice/enroll")
        for call in calls[1:]:
            self.assertEqual(call["url"], ROOT + "/easel/dashscope/voice/verify")
            self.assertEqual(call["payload"], {"model": "voice-enrollment", "input": {"action": "query_voice", "voice_task_id": VOICE}})
        self.assertIn("voice_id = " + READY, self.stdout.getvalue())

    def test_dashscope_clone_reads_verified_target_and_synthesizes_same_model(self):
        self.args.provider = "dashscope"
        self.args.voice_id = READY
        result = {"output": {"task_id": TASK, "task_status": "SUCCEEDED", "finish_reason": "stop", "audio": {"url": EXTERNAL}}}
        calls = self.invoke("clone", [self.dash_result(), result, MP3])
        self.assertEqual(calls[0]["url"], ROOT + "/easel/dashscope/voice/tasks/" + READY)
        self.assertEqual(calls[0]["method"], "GET")
        self.assertEqual(calls[1]["payload"], {"model": "cosyvoice-v3-plus", "input": {
            "text": self.args.text, "voice_task_id": READY, "format": "mp3", "rate": 1.0, "instruction": "用愤怒的语气说"}})
        self.assertEqual(calls[2]["url"], EXTERNAL)
        self.assertNotIn("authorization", calls[2]["headers"])
        self.assertEqual(self.output.read_bytes(), MP3)

    def test_dashscope_model_mismatch_or_unready_voice_does_not_submit_synthesis(self):
        self.args.provider = "dashscope"
        self.args.voice_id = READY
        self.args.model = "cosyvoice-v2"
        calls = self.invoke("clone", [self.dash_result()], failure=True)
        self.assertEqual(len(calls), 1)
        self.args.model = None
        calls = self.invoke("clone", [self.dash_result(status="DEPLOYING")], failure=True)
        self.assertEqual(len(calls), 1)

    def test_dashscope_deploying_timeout_never_recreates_voice(self):
        self.args.provider = "dashscope"
        self.args.sample_url = "https://sample.example.test/voice.wav"
        self.env["NEW_API_VOICE_TIMEOUT"] = "1"
        with patch.object(self.gateway.time, "monotonic", side_effect=[0, 0, 2]):
            calls = self.invoke("enroll", [self.dash_result(VOICE, "UNVERIFIED", "cosyvoice-v2"),
                                          self.dash_result(REVIEW, "DEPLOYING", "cosyvoice-v2")], failure=True)
        self.assertEqual(sum(call["url"].endswith("/enroll") for call in calls), 1)
        self.assertEqual(len(calls), 2)

    def test_dashscope_verify_error_never_creates_second_voice(self):
        self.args.provider = "dashscope"
        self.args.sample_url = "https://sample.example.test/voice.wav"
        calls = self.invoke("enroll", [self.dash_result(VOICE, "UNVERIFIED", "cosyvoice-v2"), {"code": "error", "message": KEY}], failure=True)
        self.assertEqual(len(calls), 2)
        self.assertNotIn(KEY, self.stderr.getvalue())
        self.assertIn("已登记待审核 voice_task_id=" + VOICE, self.stderr.getvalue())
        self.assertIn("--resume-id " + VOICE, self.stderr.getvalue())
        self.assertNotIn("✅ 音色已登记", self.stdout.getvalue())

    def test_dashscope_resume_reads_create_stage_model_then_verifies_without_recreate(self):
        self.args.provider = "dashscope"
        self.args.resume_id = VOICE
        self.args.sample_url = None
        calls = self.invoke("enroll", [self.dash_result(VOICE, "UNVERIFIED"), self.dash_result(READY)])
        self.assertEqual([call["method"] for call in calls], ["GET", "POST"])
        self.assertEqual(calls[0]["url"], ROOT + "/easel/dashscope/voice/tasks/" + VOICE)
        self.assertEqual(calls[1]["url"], ROOT + "/easel/dashscope/voice/verify")
        self.assertEqual(calls[1]["payload"]["input"]["voice_task_id"], VOICE)
        self.assertIn("voice_id = " + READY, self.stdout.getvalue())

    def test_dashscope_resume_wrong_stage_or_target_model_prevents_verify_post(self):
        self.args.provider = "dashscope"
        self.args.resume_id = VOICE
        self.args.model = "cosyvoice-v2"
        calls = self.invoke("enroll", [self.dash_result(VOICE, "UNVERIFIED")], failure=True)
        self.assertEqual(len(calls), 1)
        self.args.model = None
        calls = self.invoke("enroll", [self.dash_result(VOICE, "OK")], failure=True)
        self.assertEqual(len(calls), 1)

    def test_fish_inline_reference_submit_poll_generic_content_with_gateway_auth(self):
        self.args.provider = "fish-audio"
        calls = self.invoke("clone", [self.fish_result("SUBMITTED"), self.fish_result(), MP3])
        self.assertEqual(calls[0]["url"], ROOT + "/easel/fish/v1/tts")
        self.assertEqual(calls[0]["payload"], {"model": "easel-fish-s2.1-pro", "text": self.args.text,
            "format": "mp3", "speed": 1.0, "references": [{"audio": base64.b64encode(self.sample.read_bytes()).decode(), "text": self.args.sample_text}]})
        self.assertEqual(calls[1]["url"], ROOT + "/easel/fish/v1/tasks/" + TASK)
        self.assertEqual(calls[2]["url"], ROOT + f"/v1/tasks/{TASK}/artifacts/audio/content")
        self.assertTrue(all(call["headers"].get("authorization") == "Bearer " + KEY for call in calls))
        self.assertEqual(self.output.read_bytes(), MP3)

    def test_fish_administrator_preset_reference_id_is_only_forwarded_as_reference_id(self):
        self.args.provider = "fish-audio"
        self.args.sample = None
        self.args.voice_id = "admin-approved-preset"
        calls = self.invoke("clone", [self.fish_result(), MP3])
        self.assertEqual(calls[0]["payload"]["reference_id"], "admin-approved-preset")
        self.assertNotIn("references", calls[0]["payload"])

    def test_fish_reference_validation_precedes_submit(self):
        self.args.provider = "fish-audio"
        self.args.sample_text = ""
        self.assertEqual(self.invoke("clone", [], failure=True), [])
        self.args.sample_text = "transcript"
        self.sample.write_bytes(b"x" * ((1 << 20) + 1))
        self.assertEqual(self.invoke("clone", [], failure=True), [])

    def test_fish_expired_or_unknown_outcome_never_reposts(self):
        self.args.provider = "fish-audio"
        for result in (self.fish_result(artifact_expired=True), self.fish_result(expires_at=1),
                       self.fish_result("UNKNOWN"), {"error": {"message": KEY}}):
            with self.subTest(result=result):
                calls = self.invoke("clone", [result], failure=True)
                self.assertEqual(len(calls), 1)
                self.assertFalse(self.output.exists())

    def test_fish_content_location_and_size_are_verified_before_saving(self):
        self.args.provider = "fish-audio"
        calls = self.invoke("clone", [self.fish_result(content_url="https://evil.test/content")], failure=True)
        self.assertEqual(len(calls), 1)
        calls = self.invoke("clone", [self.fish_result(size=len(MP3) + 1), MP3], failure=True)
        self.assertEqual(len(calls), 2)
        self.assertFalse(self.output.exists())

    def test_fish_poll_does_not_start_after_total_query_deadline(self):
        self.args.provider = "fish-audio"
        with patch.object(self.gateway.time, "monotonic", side_effect=[0, 0, 6]):
            calls = self.invoke("clone", [self.fish_result("SUBMITTED")], failure=True)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["method"], "POST")

    def test_download_same_origin_gets_key_external_origin_never_does(self):
        self.args.provider = "dashscope"
        self.args.voice_id = READY
        for url, expected_auth in ((ROOT + "/audio.mp3", True), (EXTERNAL, False),
                                   ("https://gateway.test:444/audio.mp3", False)):
            with self.subTest(url=url):
                result = {"output": {"task_id": TASK, "task_status": "SUCCEEDED", "finish_reason": "stop", "audio": {"url": url}}}
                calls = self.invoke("clone", [self.dash_result(), result, MP3])
                self.assertEqual("authorization" in calls[-1]["headers"], expected_auth)

    def test_redirects_are_rejected_without_forwarding_bearer(self):
        request = self.gateway.urllib.request.Request(ROOT, headers={"Authorization": "Bearer " + KEY})
        with self.assertRaises(self.gateway.VoiceError):
            self.gateway.NoRedirect().redirect_request(request, None, 302, "", {}, "https://evil.test")

    def test_http_error_never_retries_a_billable_post_and_does_not_echo_credentials(self):
        self.args.provider = "fish-audio"
        error = HTTPError(ROOT, 502, "bad", {}, io.BytesIO(KEY.encode()))
        calls = self.invoke("clone", [error], failure=True)
        self.assertEqual(len(calls), 1)
        self.assertNotIn(KEY, self.stderr.getvalue())

    def test_check_gateway_mode_only_requires_gateway_vars_and_never_prints_key(self):
        self.env["MINIMAX_API_KEY"] = "supplier-must-not-be-used"
        self.assertEqual(self.invoke("check", []), [])
        self.assertNotIn("MINIMAX_GROUP_ID", self.stdout.getvalue())
        self.assertNotIn(KEY, self.stdout.getvalue() + self.stderr.getvalue())

    def test_missing_gateway_key_does_not_reuse_supplier_key(self):
        self.env.pop("NEW_API_KEY")
        self.env["MINIMAX_API_KEY"] = "supplier-key"
        self.assertEqual(self.invoke("enroll", [], failure=True), [])
        self.assertIn("NEW_API_KEY", self.stderr.getvalue())

    def test_custom_mode_keeps_existing_minimax_dispatch_and_check_requirements(self):
        self.env = {"MINIMAX_API_KEY": "supplier-key", "MINIMAX_GROUP_ID": "group"}
        with patch.dict(os.environ, self.env, clear=True), patch.object(voice, "enroll_minimax", return_value="legacy-id") as enroll, \
                contextlib.redirect_stdout(self.stdout):
            self.assertEqual(voice.cmd_enroll(self.args), 0)
        enroll.assert_called_once_with(self.args)
        self.assertIn("legacy-id", self.stdout.getvalue())
        self.env.pop("MINIMAX_GROUP_ID")
        with patch.dict(os.environ, self.env, clear=True), contextlib.redirect_stdout(self.stdout), contextlib.redirect_stderr(self.stderr):
            self.assertEqual(voice.cmd_check(self.args), 3)
        self.assertIn("MINIMAX_GROUP_ID", self.stdout.getvalue() + self.stderr.getvalue())

    def test_resume_id_is_explicit_cli_and_rejected_in_custom_mode_before_upload(self):
        parsed = voice.build_parser().parse_args(["enroll", "--provider", "minimax", "--resume-id", UPLOAD])
        self.assertEqual(parsed.resume_id, UPLOAD)
        self.args.resume_id = UPLOAD
        with patch.dict(os.environ, {"VOICE_API_MODE": "custom"}, clear=True), \
                patch.object(voice, "enroll_minimax") as legacy, contextlib.redirect_stderr(self.stderr), \
                self.assertRaises(SystemExit):
            voice.cmd_enroll(self.args)
        legacy.assert_not_called()

    def test_newapi_mode_leaves_gemini_and_openai_core_configuration_dispatch_unchanged(self):
        for provider, helper in (("gemini", "clone_gemini"), ("openai-compatible", "clone_openai")):
            with self.subTest(provider=provider):
                self.args.provider = provider
                self.env = {"VOICE_API_MODE": "new-api"}
                with patch.dict(os.environ, self.env, clear=True), patch.object(voice, helper, return_value=self.output) as core, \
                        contextlib.redirect_stdout(self.stdout):
                    self.assertEqual(voice.cmd_clone(self.args), 0)
                core.assert_called_once()


class VoiceRedirectRegressionTest(unittest.TestCase):
    def setUp(self):
        self.gateway = importlib.import_module("newapi_voice")
        self.target_requests = []
        self.source_auth = []
        seen, source_auth = self.target_requests, self.source_auth

        class Target(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                seen.append(self.headers.get("Authorization"))
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()

            do_POST = do_GET

        self.target = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Target)
        target_port = self.target.server_port

        class Source(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                source_auth.append(self.headers.get("Authorization"))
                if self.command == "POST":
                    self.rfile.read(int(self.headers.get("Content-Length", "0")))
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.1:{target_port}/foreign")
                self.send_header("Content-Length", "0")
                self.end_headers()

            do_POST = do_GET

        self.source = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Source)
        self.servers = [self.target, self.source]
        for server in self.servers:
            threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()

    def tearDown(self):
        for server in reversed(self.servers):
            server.shutdown()
            server.server_close()

    def test_actual_post_redirect_never_forwards_or_replays(self):
        client = self.gateway.Client(f"http://127.0.0.1:{self.source.server_port}/prefix", KEY)
        with self.assertRaises(self.gateway.VoiceError):
            client.json("/submit", payload={"fixture": True})
        self.assertEqual(self.target_requests, [])
        self.assertEqual(self.source_auth, ["Bearer " + KEY])

    def test_actual_artifact_redirect_never_forwards_gateway_key(self):
        client = self.gateway.Client(f"http://127.0.0.1:{self.source.server_port}/prefix", KEY)
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "speech.mp3"
            with self.assertRaises(self.gateway.VoiceError):
                client.download(client.endpoint("/artifact"), out)
            self.assertFalse(out.exists())
        self.assertEqual(self.target_requests, [])
        self.assertEqual(self.source_auth, ["Bearer " + KEY])


if __name__ == "__main__":
    unittest.main()
