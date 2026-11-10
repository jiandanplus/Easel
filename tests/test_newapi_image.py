"""Explicit private variations routing; no real supplier requests."""
from __future__ import annotations

import base64
import contextlib
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
import ai_image as image

ROOT = "https://gateway.test/custom/prefix"
KEY = "gateway-variation-business-token"
PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
                    "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082")


class Response:
    def __init__(self, data, mime=None, *, status=200, declared_size=None):
        self.body = data if isinstance(data, bytes) else json.dumps(data).encode()
        self.headers = {"Content-Type": mime or ("image/png" if isinstance(data, bytes) else "application/json")}
        self.status = status
        if declared_size is not None:
            self.headers["Content-Length"] = str(declared_size)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, limit=None):
        return self.body if limit is None else self.body[:limit]


class ImageNewAPITest(unittest.TestCase):
    def setUp(self):
        self.assertTrue(hasattr(image, "_run_new_api_variations"), "explicit private variations branch is missing")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.input = Path(self.temp.name) / "input.png"
        self.input.write_bytes(PNG)
        self.output = Path(self.temp.name) / "result.png"
        self.args = image.parse_args(["variations", "--image", str(self.input), "-o", str(self.output)])
        self.env = {"IMAGE_API_MODE": "new-api", "NEW_API_BASE_URL": ROOT, "NEW_API_KEY": KEY}
        self.calls, self.responses = [], []
        self.stdout, self.stderr = io.StringIO(), io.StringIO()

    def open(self, request, timeout):
        self.calls.append({"method": request.get_method(), "url": request.full_url,
            "headers": {k.lower(): v for k, v in request.header_items()}, "body": request.data, "timeout": timeout})
        self.assertTrue(self.responses, "unexpected HTTP call / fallback or duplicate submit")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response if isinstance(response, Response) else Response(response)

    def run_variations(self, responses, *, failure=False):
        self.responses = list(responses)
        with patch.dict(os.environ, self.env, clear=True), patch.object(image.urllib.request, "build_opener") as builder, \
                patch.object(image, "_open", self.open), contextlib.redirect_stdout(self.stdout), contextlib.redirect_stderr(self.stderr):
            builder.return_value.open.side_effect = self.open
            if failure:
                with self.assertRaises(SystemExit):
                    image.cmd_variations(self.args)
            else:
                image.cmd_variations(self.args)

    def inline(self):
        return {"created": 123, "data": [{"b64_json": base64.b64encode(PNG).decode()}]}

    def test_newapi_uses_exact_private_multipart_route_model_and_keeps_prefix(self):
        self.run_variations([self.inline()])
        self.assertEqual(len(self.calls), 1)
        request = self.calls[0]
        self.assertEqual(request["url"], ROOT + "/easel/openai/v1/images/variations")
        self.assertEqual(request["method"], "POST")
        self.assertEqual(request["headers"]["authorization"], "Bearer " + KEY)
        self.assertIn(b'name="model"\r\n\r\neasel-image-variations', request["body"])
        self.assertIn(b'name="response_format"\r\n\r\nurl', request["body"])
        self.assertIn(b'name="image"; filename="input.png"\r\nContent-Type: image/png', request["body"])
        self.assertIn(PNG, request["body"])
        self.assertEqual(self.output.read_bytes(), PNG)

    def test_data_url_download_preserves_external_cdn_without_gateway_key(self):
        url = "https://cdn.example.test/image.png?signature=fixture"
        self.run_variations([{"data": [{"url": url}]}, PNG])
        self.assertEqual(self.calls[1]["url"], url)
        self.assertNotIn("authorization", self.calls[1]["headers"])
        self.assertEqual(self.output.read_bytes(), PNG)

    def test_explicit_public_artifact_url_is_not_rewritten_and_gets_same_origin_auth(self):
        url = ROOT + "/v1/tasks/task_public/artifacts/image/content"
        self.run_variations([{"data": [{"url": url}]}, PNG])
        self.assertEqual(self.calls[1]["url"], url)
        self.assertEqual(self.calls[1]["headers"]["authorization"], "Bearer " + KEY)

    def test_different_port_is_external_origin_and_never_gets_gateway_key(self):
        self.run_variations([{"data": [{"url": "https://gateway.test:444/image.png"}]}, PNG])
        self.assertNotIn("authorization", self.calls[1]["headers"])

    def test_business_or_http_error_never_falls_back_to_core_or_resends(self):
        for response in ({"error": {"message": KEY}}, {"success": False, "message": KEY},
                         HTTPError(ROOT, 501, "unsupported", {}, io.BytesIO(KEY.encode())),
                         image.http.client.IncompleteRead(b"partial", 8)):
            with self.subTest(response=type(response).__name__):
                self.calls = []
                self.run_variations([response], failure=True)
                self.assertEqual(len(self.calls), 1)
                self.assertNotIn(KEY, self.stderr.getvalue())
                self.assertFalse(self.output.exists())

    def test_no_invented_polling_for_task_or_non_data_response(self):
        self.run_variations([{"task_id": "task_public", "status": "SUBMITTED"}], failure=True)
        self.assertEqual(len(self.calls), 1)

    def test_base64_is_strict_and_failed_content_does_not_overwrite(self):
        self.output.write_bytes(b"existing-user-image")
        self.run_variations([{"data": [{"b64_json": "aGVsbG8="}]}], failure=True)
        self.assertEqual(self.output.read_bytes(), b"existing-user-image")

    def test_short_content_length_or_non_200_never_overwrites_existing_image(self):
        self.output.write_bytes(b"existing-user-image")
        for response in (Response(PNG, declared_size=len(PNG) + 8), Response(PNG, status=202)):
            with self.subTest(status=response.status, length=response.headers.get("Content-Length")):
                self.calls = []
                self.run_variations([{"data": [{"url": "https://cdn.example.test/image.png"}]}, response], failure=True)
                self.assertEqual(self.output.read_bytes(), b"existing-user-image")
                self.assertEqual(len(self.calls), 2)
        self.calls = []
        self.run_variations([{"data": [{"url": "https://cdn.example.test/image.png"}]},
                             Response(b'{"error":"not image"}', "application/json")], failure=True)
        self.assertEqual(self.output.read_bytes(), b"existing-user-image")

    def test_unset_and_custom_keep_old_core_variations_and_original_model(self):
        for mode in (None, "custom"):
            with self.subTest(mode=mode):
                self.env = {"IMG_BASE_URL": "https://legacy.test/v1", "IMG_MODEL": "legacy-model", "IMG_API_KEY": "legacy-key"}
                if mode:
                    self.env["IMAGE_API_MODE"] = mode
                self.calls = []
                self.run_variations([{"data": [{"b64_json": "aGVsbG8="}]}])
                self.assertEqual(self.calls[0]["url"], "https://legacy.test/v1/images/variations")
                self.assertIn(b'name="model"\r\n\r\nlegacy-model', self.calls[0]["body"])
                self.assertEqual(self.output.read_bytes(), b"hello")

    def test_missing_gateway_key_does_not_reuse_supplier_alias(self):
        self.env.pop("NEW_API_KEY")
        self.env["IMG_API_KEY"] = "supplier-key"
        self.run_variations([], failure=True)
        self.assertEqual(self.calls, [])
        self.assertIn("NEW_API_KEY", self.stderr.getvalue())

    def test_unsupported_options_and_unsafe_base_fail_before_post(self):
        for changes in ({"n": 0}, {"n": 11}, {"size": "16:9"}, {"mode": "async"}, {"prompt": "not supported"}):
            with self.subTest(changes=changes):
                original = vars(self.args).copy()
                for key, value in changes.items():
                    setattr(self.args, key, value)
                self.run_variations([], failure=True)
                self.assertEqual(self.calls, [])
                vars(self.args).update(original)
        self.env["NEW_API_BASE_URL"] = "https://secret@gateway.test/prefix"
        self.run_variations([], failure=True)
        self.assertEqual(self.calls, [])

    def test_input_png_and_size_are_checked_before_submit(self):
        self.input.write_bytes(b"not really PNG")
        self.run_variations([], failure=True)
        self.assertEqual(self.calls, [])
        self.input.write_bytes(PNG + b"x" * (4 << 20))
        self.run_variations([], failure=True)
        self.assertEqual(self.calls, [])

    def test_check_newapi_is_offline_and_does_not_require_or_print_supplier_key(self):
        with patch.dict(os.environ, self.env, clear=True), patch.object(image, "_open") as old, \
                patch.object(image.urllib.request, "build_opener") as builder, contextlib.redirect_stdout(self.stdout):
            image.cmd_check(image.parse_args(["check"]))
        old.assert_not_called()
        builder.assert_not_called()
        self.assertIn("NEW_API_KEY", self.stdout.getvalue())
        self.assertNotIn(KEY, self.stdout.getvalue())

    def test_other_image_commands_keep_existing_flow_even_with_newapi_variations_mode(self):
        self.env.update(IMG_BASE_URL="https://legacy.test/v1", IMG_MODEL="legacy-model", IMG_API_KEY="legacy-key")
        args = image.parse_args(["text2img", "--prompt", "cat", "-o", str(self.output)])
        with patch.dict(os.environ, self.env, clear=True), patch.object(image, "http_post", return_value=self.inline()) as post, \
                contextlib.redirect_stdout(self.stdout), contextlib.redirect_stderr(self.stderr):
            image.cmd_text2img(args)
        self.assertEqual(post.call_args.args[0], "https://legacy.test/v1/images/generations")


class ImageRedirectTest(unittest.TestCase):
    def test_actual_submit_redirect_never_reaches_second_origin(self):
        seen, submitted = [], []
        class Target(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                seen.append(self.headers.get("Authorization"))
                self.send_response(200); self.send_header("Content-Length", "0"); self.end_headers()
            do_POST = do_GET
        target = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Target)
        class Source(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                submitted.append((self.path, self.headers.get("Authorization")))
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.1:{target.server_port}/foreign")
                self.send_header("Content-Length", "0"); self.end_headers()
        source = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Source)
        for server in (target, source):
            threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01}, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                png, out = Path(directory) / "in.png", Path(directory) / "out.png"
                png.write_bytes(PNG)
                args = image.parse_args(["variations", "--image", str(png), "-o", str(out)])
                env = {"IMAGE_API_MODE": "new-api", "NEW_API_BASE_URL": f"http://127.0.0.1:{source.server_port}/prefix", "NEW_API_KEY": KEY}
                with patch.dict(os.environ, env, clear=True), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    image.cmd_variations(args)
                self.assertFalse(out.exists())
            self.assertEqual(seen, [])
            self.assertEqual(submitted, [("/prefix/easel/openai/v1/images/variations", "Bearer " + KEY)])
        finally:
            for server in (source, target):
                server.shutdown(); server.server_close()


if __name__ == "__main__":
    unittest.main()
