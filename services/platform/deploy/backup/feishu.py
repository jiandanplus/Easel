"""Feishu text notifications: a lark-cli app bot direct message, or a custom group-bot webhook.

FEISHU_USER (an open_id, ou_...) sends through `lark-cli` as its app bot; the
app secret stays in lark-cli's own store. FEISHU_WEBHOOK (+ optional
FEISHU_SECRET) posts to a custom group bot. Both live only in private config
files written by the operator and are never logged.
"""
import base64
import hashlib
import hmac
import json
import subprocess
import time
import urllib.request


def payload(text, secret=None, now=time.time):
    body = {"msg_type": "text", "content": {"text": text}}
    if secret:
        timestamp = str(int(now()))
        # Feishu signs an empty message with key "timestamp\nsecret".
        digest = hmac.new(f"{timestamp}\n{secret}".encode(), b"", hashlib.sha256).digest()
        body.update(timestamp=timestamp, sign=base64.b64encode(digest).decode())
    return body


def _post(url, data):
    request = urllib.request.Request(url, data=json.dumps(data, ensure_ascii=False).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


def send(webhook, text, secret=None, post=_post):
    """Return True only when Feishu accepted the message."""
    if not isinstance(webhook, str) or not webhook.startswith("https://"):
        return False
    try:
        result = post(webhook, payload(text, secret))
    except Exception:
        return False
    return isinstance(result, dict) and result.get("code", result.get("StatusCode")) == 0


def send_lark(user_id, text, cli="lark-cli", run=subprocess.run):
    """Direct message one user as the lark-cli app bot; True only when Feishu accepted it."""
    if not isinstance(user_id, str) or not user_id.startswith("ou_"):
        return False
    try:
        result = run([cli, "im", "+messages-send", "--as", "bot", "--user-id", user_id, "--text", text],
                     capture_output=True, text=True, timeout=60)
        return result.returncode == 0 and json.loads(result.stdout).get("ok") is True
    except (OSError, subprocess.TimeoutExpired, ValueError, AttributeError):
        return False


def notifier(config):
    """The configured channel as a text -> bool callable, or None when there is none."""
    if config.get("FEISHU_USER"):
        return lambda text: send_lark(config["FEISHU_USER"], text, cli=config.get("LARK_CLI") or "lark-cli")
    if config.get("FEISHU_WEBHOOK"):
        return lambda text: send(config["FEISHU_WEBHOOK"], text, config.get("FEISHU_SECRET") or None)
    return None
