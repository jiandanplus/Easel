"""Offline HTTP contracts for the opt-in New API video client; no supplier calls."""
from __future__ import annotations

import argparse
import contextlib
import http.server
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError


SCRIPTS = Path(__file__).parents[1] / "skills/shared/scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location("ai_video_newapi_test", SCRIPTS / "ai_video.py")
video = importlib.util.module_from_spec(SPEC)
with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
    SPEC.loader.exec_module(video)

KEY = "gateway-business-token"
BASE = "https://gateway.test/team/ai/v1"
CDN = "https://cdn.test/video.mp4?signature=fixture"
MP4 = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isomfixture"


class Response:
    def __init__(self, value, *, mime=None, headers=None):
        self.body = io.BytesIO(value if isinstance(value, bytes) else json.dumps(value).encode())
        self.headers = {"Content-Type": mime or ("video/mp4" if isinstance(value, bytes) else "application/json"), **(headers or {})}
        self.status = 200

    def read(self, size=-1):
        return self.body.read(size)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class VideoNewAPITest(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name) / "nested" / "video.mp4"
        self.args = argparse.Namespace(model=None, prompt="a paper boat", duration=4,
            ratio="16:9", audio="auto", output=str(self.output), timeout=15, poll_interval=1)
        self.env = {"VIDEO_API_MODE": "new-api", "VIDEO_API_KEY": KEY,
                    "VIDEO_BASE_URL": BASE, "VIDEO_MODEL": "configured-video-alias"}
        self.calls = []
        self.responses = []
        self.clock = 0.0

    def open(self, request, timeout):
        content_type = request.get_header("Content-type", "")
        self.calls.append({"method": request.get_method(), "url": request.full_url,
            "headers": {key.lower(): value for key, value in request.header_items()},
            "payload": (json.loads(request.data) if request.data and "json" in content_type else request.data),
            "timeout": timeout})
        self.assertTrue(self.responses, "unexpected HTTP call / duplicate charged submission")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response if isinstance(response, Response) else Response(response)

    def sleep(self, seconds):
        self.clock += seconds

    def generate(self, responses, *, image=None, failure=False, real_opener=False):
        self.responses = list(responses)
        stdout, stderr = io.StringIO(), io.StringIO()
        opener = type("FixtureOpener", (), {"open": lambda _, request, timeout: self.open(request, timeout)})()
        if real_opener:
            opener = video.urllib.request.build_opener(video.urllib.request.ProxyHandler({}), video._NewAPIVideoNoRedirect())
        with patch.dict(os.environ, self.env, clear=True), \
                patch.object(video, "_NEWAPI_VIDEO_OPENER", opener, create=True), \
                patch.object(video.urllib.request, "urlopen", self.open), \
                patch.object(video.time, "monotonic", lambda: self.clock), \
                patch.object(video.time, "sleep", self.sleep), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            if failure:
                with self.assertRaises(SystemExit):
                    video.generate_openai_compatible(self.args, image)
                return stderr.getvalue()
            return video.generate_openai_compatible(self.args, image)

    def completed(self, **extra):
        return {"id": "task_public_video", "status": "completed", **extra}

    def assert_one_post(self):
        self.assertEqual(sum(call["method"] == "POST" for call in self.calls), 1)

    def test_seconds_are_strings_and_size_is_pixels_with_authenticated_content_download(self):
        result = self.generate([self.completed(video_url=CDN), MP4])
        self.assertEqual(self.calls[0]["payload"], {"model": "configured-video-alias",
            "prompt": "a paper boat", "seconds": "4", "size": "1280x720"})
        self.assertEqual([call["url"] for call in self.calls], [BASE + "/videos", BASE + "/videos/task_public_video/content"])
        self.assertTrue(all(call["headers"]["authorization"] == "Bearer " + KEY for call in self.calls))
        self.assertEqual(result, self.output)
        self.assertEqual(self.output.read_bytes(), MP4)

    def test_completion_without_url_polls_public_id_then_downloads_content(self):
        self.generate([{"id": "task_public_video", "status": "queued"},
            {"id": "task_public_video", "status": "in_progress"}, self.completed(), MP4])
        self.assertEqual([call["url"] for call in self.calls], [BASE + "/videos",
            BASE + "/videos/task_public_video", BASE + "/videos/task_public_video",
            BASE + "/videos/task_public_video/content"])
        self.assert_one_post()

    def test_bare_gateway_prefix_adds_v1_without_losing_prefix(self):
        self.env["VIDEO_BASE_URL"] = "https://gateway.test/team/ai/"
        self.generate([self.completed(), MP4])
        self.assertEqual(self.calls[0]["url"], BASE + "/videos")

    def test_veo_and_kling_have_explicit_profile_duration_and_ratio_mapping(self):
        for profile, duration, ratio, size in [("veo", 6, "9:16", "720x1280"),
                                              ("kling", 5, "1:1", "1024x1024")]:
            with self.subTest(profile=profile):
                self.calls = []
                self.env["VIDEO_NEWAPI_PROFILE"] = profile
                self.args.duration, self.args.ratio = duration, ratio
                self.generate([self.completed(), MP4])
                self.assertEqual(self.calls[0]["payload"]["seconds"], str(duration))
                self.assertEqual(self.calls[0]["payload"]["size"], size)

    def test_wan26_t2v_profile_uses_verified_720p_size_without_sending_ratio_as_size(self):
        self.env["VIDEO_NEWAPI_PROFILE"] = "wan26-t2v"
        self.args.duration, self.args.ratio = 15, "1:1"
        self.generate([self.completed(), MP4])
        self.assertEqual(self.calls[0]["payload"]["size"], "960x960")
        self.assertEqual(self.calls[0]["payload"]["seconds"], "15")

    def test_seedance_ratio_resolution_and_image_are_metadata_not_ignored_fields(self):
        self.env["VIDEO_NEWAPI_PROFILE"] = "seedance1"
        self.args.duration, self.args.ratio = 5, "9:16"
        self.generate([self.completed(), MP4], image="https://images.test/first.png")
        payload = self.calls[0]["payload"]
        self.assertEqual(payload["metadata"], {"ratio": "9:16", "resolution": "720p",
            "content": [{"type": "image_url", "image_url": {"url": "https://images.test/first.png"}}]})
        self.assertEqual(payload["size"], "720x1280")
        self.assertNotIn("image", payload)
        self.assertNotIn("input_reference", payload)

    def test_kling_url_reference_and_local_multipart_file(self):
        self.env["VIDEO_NEWAPI_PROFILE"] = "kling"
        self.args.duration = 5
        self.generate([self.completed(), MP4], image="https://images.test/frame.png")
        self.assertEqual(self.calls[0]["payload"]["input_reference"], "https://images.test/frame.png")
        image = Path(self.temp.name) / "frame.png"
        image.write_bytes(b"\x89PNG\r\n\x1a\nfixture")
        self.calls = []
        self.generate([self.completed(), MP4], image=str(image))
        body = self.calls[0]["payload"]
        self.assertIn("multipart/form-data; boundary=", self.calls[0]["headers"]["content-type"])
        self.assertIn(b'name="input_reference"; filename="reference.png"', body)
        self.assertIn(b'name="seconds"\r\n\r\n5\r\n', body)
        self.assertIn(image.read_bytes(), body)

    def test_defaults_are_profile_specific_and_model_explicit(self):
        self.env["VIDEO_NEWAPI_PROFILE"] = "kling"
        self.args.duration = self.args.ratio = None
        self.args.model = "explicit-channel-alias"
        self.generate([self.completed(), MP4])
        self.assertEqual(self.calls[0]["payload"], {"model": "explicit-channel-alias",
            "prompt": "a paper boat", "seconds": "5", "size": "1280x720"})

    def test_missing_model_unknown_mode_profile_and_unsupported_combinations_fail_before_post(self):
        cases = [({"VIDEO_MODEL": ""}, {}), ({"VIDEO_API_MODE": "typo"}, {}),
            ({"VIDEO_NEWAPI_PROFILE": "unknown"}, {}), ({"VIDEO_NEWAPI_PROFILE": "veo"}, {"duration": 5}),
            ({"VIDEO_NEWAPI_PROFILE": "standard"}, {"ratio": "1:1"}),
            ({}, {"audio": "on"}), ({}, {"audio": "off"}),
            ({}, {"duration": 0}), ({}, {"poll_interval": 0}), ({}, {"timeout": 0})]
        original_env, original_args = dict(self.env), vars(self.args).copy()
        for env, args in cases:
            with self.subTest(env=env, args=args):
                self.env = {**original_env, **env}
                self.args = argparse.Namespace(**{**original_args, **args})
                self.calls = []
                self.generate([], failure=True)
                self.assertEqual(self.calls, [])

    def test_url_only_profiles_reject_local_images_and_t2v_rejects_image_before_post(self):
        image = Path(self.temp.name) / "frame.png"
        image.write_bytes(b"\x89PNG\r\n\x1a\nfixture")
        for profile in ("seedance1", "wan26-t2v"):
            with self.subTest(profile=profile):
                self.env["VIDEO_NEWAPI_PROFILE"] = profile
                self.args.duration = 5
                self.generate([], image=str(image), failure=True)
                self.assertEqual(self.calls, [])

    def test_standard_url_reference_is_rejected_instead_of_unproven_upstream_json_image(self):
        self.generate([], image="https://images.test/frame.png", failure=True)
        self.assertEqual(self.calls, [])

    def test_unsafe_base_url_and_task_id_are_rejected(self):
        for base in ("file:///tmp/video", "https://key@gateway.test/v1", "https://gateway.test/v1?key=secret"):
            with self.subTest(base=base):
                self.env["VIDEO_BASE_URL"] = base
                self.generate([], failure=True)
                self.assertEqual(self.calls, [])
        self.env["VIDEO_BASE_URL"] = BASE
        self.generate([self.completed(id="../../users")], failure=True)
        self.assertEqual(len(self.calls), 1)

    def test_content_404_can_use_explicit_cdn_video_url_without_gateway_token(self):
        self.generate([self.completed(video_url=CDN),
            HTTPError(BASE + "/videos/task_public_video/content", 404, "missing", {}, io.BytesIO()), MP4])
        self.assertEqual(self.calls[-1]["url"], CDN)
        self.assertNotIn("authorization", self.calls[-1]["headers"])
        self.assertNotIn(KEY, json.dumps(self.calls[-1]))
        self.assert_one_post()

    def test_redirect_to_cdn_strips_token_instead_of_forwarding_credentials(self):
        self.generate([self.completed(), HTTPError(BASE, 302, "redirect", {"Location": CDN}, io.BytesIO()), MP4])
        self.assertEqual(self.calls[-1]["url"], CDN)
        self.assertNotIn("authorization", self.calls[-1]["headers"])
        self.assertEqual(self.output.read_bytes(), MP4)

    def test_redirects_are_bounded_and_https_downgrade_or_invalid_scheme_are_rejected(self):
        for location in ("http://cdn.test/video.mp4", "file:///tmp/result.mp4", "https://key@cdn.test/video.mp4"):
            with self.subTest(location=location):
                self.calls = []
                self.generate([self.completed(), HTTPError(BASE, 302, "redirect", {"Location": location}, io.BytesIO())], failure=True)
                self.assertEqual(len(self.calls), 2)
        self.calls = []
        redirects = [HTTPError(BASE, 302, "redirect", {"Location": CDN}, io.BytesIO()) for _ in range(5)]
        self.generate([self.completed(), *redirects], failure=True)
        self.assertLessEqual(len(self.calls), 5)
        self.assert_one_post()

    def test_submit_redirect_is_not_replayed_on_another_origin(self):
        message = self.generate([HTTPError(BASE, 307, "redirect", {"Location": CDN}, io.BytesIO(KEY.encode()))], failure=True)
        self.assertEqual(len(self.calls), 1)
        self.assertNotIn(KEY, message)
        self.assertIn("未知", message)

    def test_submit_connection_failure_is_unknown_and_never_reposts(self):
        message = self.generate([URLError("fixture connection lost " + KEY)], failure=True)
        self.assertEqual(len(self.calls), 1)
        self.assertIn("未知", message)
        self.assertNotIn(KEY, message)

    def test_failed_business_error_unknown_and_missing_status_never_download_or_repost(self):
        for response in (self.completed(status="failed", error={"message": KEY}),
                         self.completed(error={"message": KEY}), self.completed(status="unknown"),
                         self.completed(status="surprise"), {"id": "task_public_video"},
                         {"error": {"message": KEY}}, {"id": "task_public_video", "status": "cancelled"}):
            with self.subTest(response=response):
                self.calls = []
                message = self.generate([response], failure=True)
                self.assertEqual(len(self.calls), 1)
                self.assertNotIn(KEY, message)

    def test_changed_public_id_in_poll_is_rejected(self):
        message = self.generate([self.completed(status="queued"), self.completed(id="task_other")], failure=True)
        self.assertIn("task_public_video", message)
        self.assertEqual(len(self.calls), 2)

    def test_poll_timeout_has_public_id_and_never_reposts(self):
        self.args.timeout = 2
        message = self.generate([self.completed(status="queued"), self.completed(status="in_progress"),
                                 self.completed(status="in_progress")], failure=True)
        self.assertIn("task_public_video", message)
        self.assertIn("超时", message)
        self.assert_one_post()
        self.assertFalse(self.output.exists())

    def test_unusable_content_does_not_overwrite_existing_file_or_report_success(self):
        self.output.parent.mkdir()
        self.output.write_bytes(b"existing-result")
        for response in (Response(b"", mime="video/mp4"), Response(b'{"error":"fixture"}', mime="application/json"),
                         Response(b'{"error":"fixture"}', mime="application/octet-stream"),
                         Response(b"<html>login</html>", mime="text/html"),
                         HTTPError(BASE, 403, "forbidden", {}, io.BytesIO(KEY.encode()))):
            with self.subTest(response=response):
                self.calls = []
                message = self.generate([self.completed(), response], failure=True)
                self.assertEqual(self.output.read_bytes(), b"existing-result")
                self.assertNotIn(KEY, message)
                self.assert_one_post()

    def test_large_content_is_bounded(self):
        with patch.object(video, "_NEWAPI_VIDEO_MAX_BYTES", len(MP4) - 1, create=True):
            self.generate([self.completed(), MP4], failure=True)
        self.assertFalse(self.output.exists())

    def test_mislabeled_non_video_binary_is_rejected_without_overwriting_file(self):
        self.output.parent.mkdir()
        self.output.write_bytes(b"previous-result")
        self.generate([self.completed(), Response(b"not-a-video", mime="video/mp4")], failure=True)
        self.assertEqual(self.output.read_bytes(), b"previous-result")

    def test_content_length_must_be_valid_bounded_and_match_actual_download(self):
        self.output.parent.mkdir()
        self.output.write_bytes(b"previous-result")
        for length in (str(len(MP4) + 20), str(len(MP4) - 1), "invalid", "-1", str(512 * 1024 * 1024 + 1)):
            with self.subTest(length=length):
                self.calls = []
                self.generate([self.completed(), Response(MP4, headers={"Content-Length": length})], failure=True)
                self.assertEqual(self.output.read_bytes(), b"previous-result")

    def test_veo_http_url_reference_is_rejected_because_factory_requires_inline_data(self):
        self.env["VIDEO_NEWAPI_PROFILE"] = "veo"
        self.generate([], image="https://images.test/frame.png", failure=True)
        self.assertEqual(self.calls, [])

    def test_real_urllib_strips_gateway_token_on_cross_origin_content_redirect(self):
        observations = []
        state = {"short_read": False}
        class CDNHandler(http.server.BaseHTTPRequestHandler):
            def do_GET(handler):
                observations.append(("cdn", handler.path, handler.headers.get("Authorization")))
                handler.send_response(200)
                handler.send_header("Content-Type", "video/mp4")
                handler.send_header("Content-Length", str(len(MP4)))
                handler.end_headers()
                handler.wfile.write(MP4)
            def log_message(handler, *args):
                pass
        cdn = http.server.ThreadingHTTPServer(("127.0.0.1", 0), CDNHandler)
        cdn_url = f"http://127.0.0.1:{cdn.server_port}/result.mp4"
        class GatewayHandler(http.server.BaseHTTPRequestHandler):
            def do_POST(handler):
                handler.rfile.read(int(handler.headers.get("Content-Length", "0")))
                observations.append(("gateway-post", handler.path, handler.headers.get("Authorization")))
                raw = json.dumps({"id": "task_public_video", "status": "completed"}).encode()
                handler.send_response(200)
                handler.send_header("Content-Type", "application/json")
                handler.send_header("Content-Length", str(len(raw)))
                handler.end_headers()
                handler.wfile.write(raw)
            def do_GET(handler):
                observations.append(("gateway-content", handler.path, handler.headers.get("Authorization")))
                if state["short_read"]:
                    handler.send_response(200)
                    handler.send_header("Content-Type", "video/mp4")
                    handler.send_header("Content-Length", str(len(MP4) + 100))
                    handler.end_headers()
                    handler.wfile.write(MP4)
                    handler.close_connection = True
                    return
                handler.send_response(302)
                handler.send_header("Location", cdn_url)
                handler.end_headers()
            def log_message(handler, *args):
                pass
        gateway = http.server.ThreadingHTTPServer(("127.0.0.1", 0), GatewayHandler)
        threads = [threading.Thread(target=server.serve_forever, daemon=True) for server in (cdn, gateway)]
        for thread in threads:
            thread.start()
        try:
            self.env["VIDEO_BASE_URL"] = f"http://127.0.0.1:{gateway.server_port}/prefix/v1"
            self.generate([], real_opener=True)
            self.assertEqual(observations, [
                ("gateway-post", "/prefix/v1/videos", "Bearer " + KEY),
                ("gateway-content", "/prefix/v1/videos/task_public_video/content", "Bearer " + KEY),
                ("cdn", "/result.mp4", None)])
            self.assertEqual(self.output.read_bytes(), MP4)
            state["short_read"] = True
            self.output.write_bytes(b"previous-result")
            message = self.generate([], real_opener=True, failure=True)
            self.assertIn("Content-Length", message)
            self.assertEqual(self.output.read_bytes(), b"previous-result")
            self.assertEqual(list(self.output.parent.glob(".easel-video-*")), [])
        finally:
            for server in (gateway, cdn):
                server.shutdown()
                server.server_close()
            for thread in threads:
                thread.join(timeout=2)

    def test_legacy_unset_custom_and_legacy_modes_keep_old_fields_and_url_flow(self):
        for mode in (None, "custom", "legacy"):
            with self.subTest(mode=mode):
                self.calls = []
                if mode is None:
                    self.env.pop("VIDEO_API_MODE", None)
                else:
                    self.env["VIDEO_API_MODE"] = mode
                self.generate([{"url": CDN}, MP4])
                self.assertEqual(self.calls[0]["payload"]["seconds"], 4)
                self.assertEqual(self.calls[0]["payload"]["size"], "16:9")
                self.assertEqual(self.calls[-1]["url"], CDN)
                self.assertNotIn("authorization", self.calls[-1]["headers"])


if __name__ == "__main__":
    unittest.main()
