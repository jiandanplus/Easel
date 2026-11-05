"""Save once, then exercise actual CLI processes against an owned local gateway."""
from __future__ import annotations

import base64
import http.server
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "web"))
import app


PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jS1kAAAAASUVORK5CYII=")
MP4 = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2" + b"\x00\x00\x00\x08mdat"
MP3 = b"ID3" + b"\x00" * 24


def test_saved_gateway_reaches_all_media_clis_without_changing_custom_values(tmp_path, monkeypatch):
    calls = []
    key = "unit-only-gateway-credential"

    class Gateway(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, value, kind="application/json"):
            raw = json.dumps(value).encode() if isinstance(value, dict) else value
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            calls.append((self.path, self.headers.get("Authorization"), body))
            if self.path in ("/team/v1/images/generations", "/team/v1/images/edits", "/team/easel/openai/v1/images/variations"):
                self.send({"created": 1, "data": [{"b64_json": base64.b64encode(PNG).decode()}]})
            elif self.path == "/team/v1/videos":
                self.send({"id": "task_" + "a" * 32, "status": "completed"})
            elif self.path == "/team/easel/dashscope/api/v1/services/audio/music/generation":
                self.send({"output": {"task_status": "SUCCEEDED", "finish_reason": "stop", "audio": {"url": gateway_root + "/music.mp3"}}, "usage": {"duration": 6}})
            elif self.path == "/team/v1/audio/speech":
                self.send(MP3, "audio/mpeg")
            else:
                self.send_error(404)

        def do_GET(self):
            calls.append((self.path, self.headers.get("Authorization"), b""))
            if self.path.endswith("/content"):
                self.send(MP4, "video/mp4")
            elif self.path == "/team/music.mp3":
                self.send(MP3, "audio/mpeg")
            else:
                self.send_error(404)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Gateway)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    gateway_root = f"http://127.0.0.1:{server.server_port}/team"
    custom = {"IMG_API_KEY": "old-image-key", "IMG_BASE_URL": "https://old.invalid/v1", "IMG_MODEL": "old-image",
              "VIDEO_PROVIDER": "agnes", "VIDEO_MODEL": "old-video", "VOICE_PROVIDER": "minimax",
              "VOICE_NARRATOR_VOICE_ID": "old-upstream-id", "DASHSCOPE_API_KEY": "old-dash-key"}
    envfile = tmp_path / ".env"
    envfile.write_text("\n".join(f"{k}={v}" for k, v in custom.items()) + "\n")
    monkeypatch.setattr(app, "ENV_FILE", envfile)
    monkeypatch.setattr(app, "PROJECT_ROOT", tmp_path)
    body = {"baseUrl": gateway_root, "key": key, "modes": {k: "new-api" for k in ("image", "video", "music", "voice")},
            "image": {"model": "unit-image"}, "video": {"model": "unit-video", "profile": "kling"},
            "music": {"provider": "dashscope", "model": "fun-music-v1"},
            "voice": {"provider": "openai-compatible", "model": "unit-tts", "voiceId": "alloy"},
            "geminiTtsApi": "generate-content"}
    try:
        local = "http://127.0.0.1:7860"
        with TestClient(app.app, base_url=local, client=("127.0.0.1", 50200), headers={"Origin": local}) as client:
            response = client.put("/api/settings/media-gateway", json=body)
            assert response.status_code == 200, response.text
            assert key not in response.text
        assert all(f"{k}={v}" in envfile.read_text().splitlines() for k, v in custom.items())
        reference = tmp_path / "reference.png"
        reference.write_bytes(PNG)
        commands = [
            ("ai_image.py", "text2img", "--prompt", "test", "-o", "outputs/gateway-verification/generated.png"),
            ("ai_image.py", "img2img", "--image", str(reference), "--prompt", "test", "-o", "outputs/gateway-verification/edited.png"),
            ("ai_image.py", "variations", "--image", str(reference), "-o", "outputs/gateway-verification/variation.png"),
            ("ai_video.py", "text2video", "--prompt", "test", "--duration", "5", "-o", "outputs/gateway-verification/video.mp4"),
            ("ai_music.py", "generate", "--prompt", "test", "--instrumental", "-o", "outputs/gateway-verification/music.mp3"),
            ("voice_clone.py", "clone", "--text", "test", "-o", "outputs/gateway-verification/voice.mp3"),
        ]
        child_env = {"PATH": os.environ.get("PATH", os.defpath), "HOME": str(tmp_path), "EASEL_ROOT": str(tmp_path), "PYTHONIOENCODING": "utf-8"}
        for script, *args in commands:
            proc = subprocess.run([sys.executable, str(ROOT / "skills/shared/scripts" / script), *args],
                                  cwd=tmp_path, env=child_env, text=True, capture_output=True, timeout=30)
            assert proc.returncode == 0, f"{script}: {proc.stderr} {proc.stdout}"
            assert key not in proc.stdout + proc.stderr
        expected = {"/team/v1/images/generations", "/team/v1/images/edits", "/team/easel/openai/v1/images/variations",
                    "/team/v1/videos", "/team/v1/audio/speech", "/team/easel/dashscope/api/v1/services/audio/music/generation"}
        assert expected.issubset({path for path, _, _ in calls})
        for path, auth, _ in calls:
            assert auth == (None if path.endswith("music.mp3") else "Bearer " + key)
        assert len([1 for path, _, _ in calls if path == "/team/v1/videos"]) == 1
        assert (tmp_path / "outputs/gateway-verification/generated.png").read_bytes() == PNG
        assert (tmp_path / "outputs/gateway-verification/edited.png").read_bytes() == PNG
        assert (tmp_path / "outputs/gateway-verification/variation.png").read_bytes() == PNG
        assert (tmp_path / "outputs/gateway-verification/video.mp4").read_bytes() == MP4
        assert (tmp_path / "outputs/gateway-verification/voice.mp3").read_bytes() == MP3
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
