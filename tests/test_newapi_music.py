"""Protocol fixtures for explicit New API music mode; no supplier calls."""
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import http.server
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError


SCRIPTS = Path(__file__).parents[1] / "skills/shared/scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location("ai_music_newapi_test", SCRIPTS / "ai_music.py")
music = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(music)
KEY = "gateway-key-not-for-audio-download"
PREFIX = "https://gateway.test/custom/prefix"
AUDIO = "https://audio.test/generated.mp3?signature=fixture"


class Response:
    def __init__(self, value):
        self.body = value if isinstance(value, bytes) else json.dumps(value).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.body


class MusicModeTest(unittest.TestCase):
    def setUp(self):
        self.assertTrue(hasattr(music, "resolve_api_mode"), "explicit New API music mode is missing")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name) / "track.mp3"
        self.args = argparse.Namespace(provider=None, model=None, prompt="gentle piano",
            lyrics=None, instrumental=True, duration=None, output=str(self.output),
            timeout=10, poll_interval=1)
        self.env = {"MUSIC_API_MODE": "new-api", "MUSIC_API_KEY": KEY,
                    "MUSIC_BASE_URL": PREFIX, "DASHSCOPE_API_KEY": KEY,
                    "DASHSCOPE_BASE_URL": PREFIX}
        self.calls = []
        self.responses = []

    def open(self, request, timeout):
        self.calls.append({"method": request.get_method(), "url": request.full_url,
            "headers": {key.lower(): value for key, value in request.header_items()},
            "payload": json.loads(request.data) if request.data else None, "timeout": timeout})
        self.assertTrue(self.responses, "unexpected extra HTTP call / fallback")
        return Response(self.responses.pop(0))

    def generate(self, provider, responses, *, failure=False):
        self.responses = list(responses)
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, self.env, clear=True), patch.object(music.urllib.request, "urlopen", self.open), \
                patch.object(music.urllib.request, "build_opener") as builder, \
                patch.object(music.time, "sleep"), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            builder.return_value.open.side_effect = self.open
            if failure:
                with self.assertRaises(SystemExit):
                    music.PROVIDER_GENERATORS[provider](self.args)
                return stderr.getvalue()
            return music.PROVIDER_GENERATORS[provider](self.args)

    def completed_music(self, **output):
        return {"output": {"task_id": "private-task", "task_status": "SUCCEEDED",
                "finish_reason": "stop", "audio": {"url": AUDIO}, **output}, "usage": {"duration": 35}}

    def suno(self, status="SUCCESS", data=None):
        return {"code": "success", "data": {"task_id": "native-id", "status": status,
            "data": [{"id": "clip-1", "audio_url": AUDIO}] if data is None else data}}

    def assert_credential_free_download(self):
        self.assertEqual(self.calls[-1]["url"], AUDIO)
        self.assertEqual(self.calls[-1]["method"], "GET")
        self.assertNotIn("authorization", self.calls[-1]["headers"])
        self.assertNotIn(KEY, json.dumps(self.calls[-1]))
        self.assertEqual(self.output.read_bytes(), b"fixture-mp3")

    def test_dashscope_uses_sync_private_route_and_input_fields_without_async_header(self):
        result = self.generate("dashscope", [self.completed_music(), b"fixture-mp3"])
        self.assertEqual(result, self.output)
        self.assertEqual(len(self.calls), 2)
        request = self.calls[0]
        self.assertEqual(request["url"], PREFIX + "/easel/dashscope/api/v1/services/audio/music/generation")
        self.assertEqual(request["method"], "POST")
        self.assertEqual(request["payload"], {"model": "fun-music-v1",
            "input": {"prompt": "gentle piano", "is_instrumental": True}})
        self.assertNotIn("x-dashscope-async", request["headers"])
        self.assertEqual(request["headers"]["authorization"], "Bearer " + KEY)
        self.assert_credential_free_download()

    def test_dashscope_lyrics_and_explicit_model_are_mapped_without_legacy_parameters(self):
        self.args.lyrics = "hello world lyrics"
        self.args.model = "fun-music-preview"
        self.generate("dashscope", [self.completed_music(), b"fixture-mp3"])
        self.assertEqual(self.calls[0]["payload"], {"model": "fun-music-preview", "input": {
            "prompt": "gentle piano", "lyrics": "hello world lyrics", "is_instrumental": False}})

    def test_dashscope_rejects_unsupported_duration_before_any_http(self):
        self.args.duration = 30
        text = self.generate("dashscope", [], failure=True)
        self.assertIn("duration", text)
        self.assertEqual(self.calls, [])

    def test_dashscope_requires_complete_status_and_finish_reason_even_when_audio_exists(self):
        for output in ({"task_status": "RUNNING"}, {"task_status": "FAILED"},
                       {"task_status": "UNKNOWN"}, {"task_status": ""}, {"finish_reason": "length"}):
            with self.subTest(output=output):
                self.calls = []
                self.generate("dashscope", [self.completed_music(**output)], failure=True)
                self.assertEqual(len(self.calls), 1)
                self.assertEqual(self.calls[0]["method"], "POST")

    def test_dashscope_business_error_does_not_poll_download_or_fallback(self):
        response = self.completed_music()
        response.update(code="InvalidApiKey", message=KEY)
        text = self.generate("dashscope", [response], failure=True)
        self.assertEqual(len(self.calls), 1)
        self.assertNotIn(KEY, text)

    def test_dashscope_missing_sync_audio_does_not_search_unrelated_urls_or_poll(self):
        response = self.completed_music(audio={})
        response["documentation"] = "https://example.test/guide.mp3"
        self.generate("dashscope", [response], failure=True)
        self.assertEqual(len(self.calls), 1)

    def test_newapi_dashscope_requires_explicit_gateway_base(self):
        self.env.pop("DASHSCOPE_BASE_URL")
        self.generate("dashscope", [], failure=True)
        self.assertEqual(self.calls, [])

    def test_suno_native_submit_string_id_and_fetch_path_are_used(self):
        self.generate("suno-compatible", [{"code": "success", "data": "id/with ?#"}, self.suno(), b"fixture-mp3"])
        self.assertEqual(self.calls[0]["url"], PREFIX + "/suno/submit/music")
        self.assertEqual(self.calls[0]["payload"], {
            "gpt_description_prompt": "gentle piano", "make_instrumental": True})
        self.assertEqual(self.calls[1]["url"], PREFIX + "/suno/fetch/id%2Fwith%20%3F%23")
        self.assertEqual(self.calls[1]["headers"]["authorization"], "Bearer " + KEY)
        self.assert_credential_free_download()

    def test_suno_explicit_mv_and_lyrics_are_native_custom_payload(self):
        self.args.model = "chirp-v3-5"
        self.args.lyrics = "[Verse] Sing a song"
        self.generate("suno-compatible", [{"code": "success", "data": "id"}, self.suno(), b"fixture-mp3"])
        self.assertEqual(self.calls[0]["payload"], {"mv": "chirp-v3-5", "prompt": "[Verse] Sing a song",
            "tags": "gentle piano", "make_instrumental": False})
        self.assertNotIn("model", self.calls[0]["payload"])

    def test_suno_newapi_does_not_inherit_legacy_music_1_default(self):
        self.generate("suno-compatible", [{"code": "success", "data": "id"}, self.suno(), b"fixture-mp3"])
        self.assertNotIn("mv", self.calls[0]["payload"])
        self.assertNotIn("music-1", json.dumps(self.calls[0]))

    def test_suno_known_pending_states_only_query_until_success(self):
        responses = [{"code": "success", "data": "id"}]
        responses += [self.suno(state) for state in ("NOT_START", "SUBMITTED", "QUEUED", "IN_PROGRESS", "SUCCESS")]
        responses.append(b"fixture-mp3")
        self.generate("suno-compatible", responses)
        self.assertEqual(sum(call["method"] == "POST" for call in self.calls), 1)
        self.assertEqual(len(self.calls), 7)

    def test_suno_unknown_failed_or_missing_state_is_terminal_without_download_or_fallback(self):
        for status in ("UNKNOWN", "FAILURE", "", "COMPLETE", "unrecognized"):
            with self.subTest(status=status):
                self.calls = []
                self.generate("suno-compatible", [{"code": "success", "data": "id"}, self.suno(status)], failure=True)
                self.assertEqual(len(self.calls), 2)
                self.assertEqual(sum(call["method"] == "POST" for call in self.calls), 1)

    def test_suno_submit_business_error_with_id_does_not_trigger_poll_or_legacy_resubmit(self):
        text = self.generate("suno-compatible", [{"code": "error", "data": "id", "message": KEY}], failure=True)
        self.assertEqual(len(self.calls), 1)
        self.assertNotIn(KEY, text)

    def test_suno_poll_business_error_with_audio_is_not_success(self):
        response = self.suno()
        response["code"] = "quota_exceeded"
        self.generate("suno-compatible", [{"code": "success", "data": "id"}, response], failure=True)
        self.assertEqual(len(self.calls), 2)

    def test_suno_success_with_only_image_url_is_rejected(self):
        response = self.suno(data=[{"id": "clip", "image_url": "https://example.test/cover.png"}])
        self.generate("suno-compatible", [{"code": "success", "data": "id"}, response], failure=True)
        self.assertEqual(len(self.calls), 2)

    def test_suno_nested_native_task_data_wrapper_is_supported(self):
        nested = {"data": {"task_id": "upstream-id", "data": [{"id": "clip", "audio_url": AUDIO}]}}
        self.generate("suno-compatible", [{"code": "success", "data": "id"}, self.suno(data=nested), b"fixture-mp3"])
        self.assert_credential_free_download()

    def test_suno_query_timeout_never_resubmits(self):
        with patch.object(music.time, "monotonic", side_effect=[0, 11]):
            self.generate("suno-compatible", [{"code": "success", "data": "id"}], failure=True)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0]["method"], "POST")

    def test_suno_duration_is_rejected_in_newapi_instead_of_silently_ignored(self):
        self.args.duration = 30
        text = self.generate("suno-compatible", [], failure=True)
        self.assertIn("duration", text)
        self.assertEqual(self.calls, [])

    def test_explicit_env_model_is_mv_in_newapi_and_model_in_legacy(self):
        self.env["MUSIC_MODEL"] = "chirp-v3-5"
        self.generate("suno-compatible", [{"code": "success", "data": "id"}, self.suno(), b"fixture-mp3"])
        self.assertEqual(self.calls[0]["payload"]["mv"], "chirp-v3-5")
        self.assertNotIn("model", self.calls[0]["payload"])
        self.calls = []
        self.env["MUSIC_API_MODE"] = "legacy"
        self.generate("suno-compatible", [{"id": "id"}, {"status": "complete", "audio_url": AUDIO}, b"fixture-mp3"])
        self.assertEqual(self.calls[0]["payload"]["model"], "chirp-v3-5")
        self.assertNotIn("mv", self.calls[0]["payload"])

    def test_http_error_is_redacted_and_does_not_retry_in_either_mode(self):
        for mode in ("legacy", "new-api"):
            with self.subTest(mode=mode):
                self.env["MUSIC_API_MODE"] = mode
                error = HTTPError(PREFIX, 502, "bad gateway", {}, io.BytesIO(KEY.encode()))
                stderr = io.StringIO()
                with patch.dict(os.environ, self.env, clear=True), \
                        patch.object(music.urllib.request, "urlopen", side_effect=error) as network, \
                        patch.object(music.urllib.request, "build_opener") as builder, \
                        contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit):
                    builder.return_value.open.side_effect = error
                    music.generate_suno(self.args)
                self.assertEqual(network.call_count + builder.return_value.open.call_count, 1)
                if mode == "new-api":
                    self.assertNotIn(KEY, stderr.getvalue())

    def test_base_with_explicit_v1_prefix_is_not_rewritten_or_guessed(self):
        self.env["MUSIC_BASE_URL"] = PREFIX + "/v1/"
        self.generate("suno-compatible", [{"code": "success", "data": "id"}, self.suno(), b"fixture-mp3"])
        self.assertEqual(self.calls[0]["url"], PREFIX + "/v1/suno/submit/music")
        self.assertEqual(self.calls[1]["url"], PREFIX + "/v1/suno/fetch/id")

    def test_legacy_dashscope_retains_async_endpoint_model_payload_and_query(self):
        self.env.pop("MUSIC_API_MODE")
        self.args.duration = 30
        self.generate("dashscope", [{"output": {"task_id": "legacy-id"}},
            {"output": {"task_status": "SUCCEEDED", "audio_url": AUDIO}}, b"fixture-mp3"])
        self.assertEqual(self.calls[0]["url"], PREFIX + "/services/aigc/text2audio/generation")
        self.assertEqual(self.calls[0]["payload"], {"model": "audio-generation", "input": {"prompt": "gentle piano"},
            "parameters": {"duration": 30, "instrumental": True}})
        self.assertEqual(self.calls[0]["headers"]["x-dashscope-async"], "enable")
        self.assertEqual(self.calls[1]["url"], PREFIX + "/tasks/legacy-id")
        self.assert_credential_free_download()

    def test_legacy_suno_retains_generate_feed_and_music_1(self):
        self.env["MUSIC_API_MODE"] = "legacy"
        self.args.lyrics = "legacy lyrics"
        self.args.duration = 45
        self.generate("suno-compatible", [{"data": {"id": "legacy-id"}},
            {"data": {"status": "complete", "audio_url": AUDIO}}, b"fixture-mp3"])
        self.assertEqual(self.calls[0]["url"], PREFIX + "/generate")
        self.assertEqual(self.calls[0]["payload"], {"model": "music-1", "prompt": "gentle piano",
            "lyrics": "legacy lyrics", "make_instrumental": False, "duration": 45})
        self.assertEqual(self.calls[1]["url"], PREFIX + "/feed/legacy-id")
        self.assert_credential_free_download()

    def test_unknown_mode_does_not_send_any_request(self):
        self.env["MUSIC_API_MODE"] = "guess-from-domain"
        self.generate("suno-compatible", [], failure=True)
        self.assertEqual(self.calls, [])

    def test_check_newapi_requires_dashscope_base_and_stays_offline(self):
        self.env.pop("DASHSCOPE_BASE_URL")
        with patch.dict(os.environ, self.env, clear=True), patch.object(music.urllib.request, "urlopen") as network, \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            result = music.cmd_check(argparse.Namespace(provider="dashscope"))
        self.assertEqual(result, 1)
        network.assert_not_called()


class NewAPIRedirectRegressionTest(unittest.TestCase):
    """Real sockets: two different local ports are distinct HTTP origins."""
    def setUp(self):
        self.foreign_requests = []
        self.source_requests = []
        seen, received = self.foreign_requests, self.source_requests

        class Target(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                seen.append(self.headers.get("Authorization"))
                payload = b'{"code":"success","data":"unexpected-follow"}'
                self.send_response(200)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            do_POST = do_GET

        target = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Target)

        class Source(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                received.append(self.command)
                if self.command == "POST":
                    self.rfile.read(int(self.headers.get("Content-Length", "0")))
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.1:{target.server_port}/foreign")
                self.send_header("Content-Length", "0")
                self.end_headers()

            do_POST = do_GET

        self.source = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Source)
        self.servers = [target, self.source]
        for server in self.servers:
            threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()

    def tearDown(self):
        for server in reversed(self.servers):
            server.shutdown()
            server.server_close()

    def assert_redirect_rejected(self, method):
        url = f"http://127.0.0.1:{self.source.server_port}/gateway"
        headers = {"Authorization": "Bearer " + KEY}
        stderr = io.StringIO()
        rejected = False
        with contextlib.redirect_stderr(stderr):
            try:
                if method == "POST":
                    music.http_post(url, headers, {"model": "fixture"}, redact_errors=True)
                else:
                    music.http_get(url, headers, redact_errors=True)
            except SystemExit:
                rejected = True
        self.assertEqual(self.foreign_requests, [], "redirect target must receive zero requests and zero credentials")
        self.assertTrue(rejected, "redirect must fail without fallback or resubmission")
        self.assertEqual(self.source_requests, [method])
        self.assertNotIn(KEY, stderr.getvalue())

    def test_newapi_post_cross_origin_redirect_never_reaches_target(self):
        self.assert_redirect_rejected("POST")

    def test_newapi_get_cross_origin_redirect_never_reaches_target(self):
        self.assert_redirect_rejected("GET")


if __name__ == "__main__":
    unittest.main()
