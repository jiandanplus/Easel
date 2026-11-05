"""Feishu custom-bot text notifications.

The webhook URL and the optional signing secret live only in private config
files written by the operator; they are never logged.
"""
import base64
import hashlib
import hmac
import json
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
