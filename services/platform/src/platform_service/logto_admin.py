"""The console's narrow use of the Logto Management API: make, edit, suspend sign-in accounts.

Only the console process holds these machine-to-machine credentials. Passwords pass through
once to Logto and are never logged, stored or hashed into an audit record.
"""
from __future__ import annotations

import re
import time

import httpx

from .errors import PlatformError

RESOURCE = "https://default.logto.app/api"
USERNAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}")
EMAIL = re.compile(r"[^@\s]{1,64}@[^@\s]{1,190}\.[A-Za-z]{2,24}")
LOGTO_ERRORS = {
    "user.username_already_in_use": (409, "console_username_taken"),
    "user.email_already_in_use": (409, "console_email_taken"),
    "user.phone_already_in_use": (409, "console_phone_taken"),
    "user.invalid_phone": (422, "console_phone_invalid"),
    "user.invalid_email": (422, "console_email_invalid"),
    "entity.not_exists_with_id": (404, "console_user_not_found"),
    "entity.not_found": (404, "console_user_not_found"),
}


def clean_username(value):
    if not isinstance(value, str) or not USERNAME.fullmatch(value):
        raise PlatformError(422, "console_username_invalid")
    return value


def clean_email(value):
    if value in (None, ""):
        return None
    if not isinstance(value, str) or len(value) > 254 or not EMAIL.fullmatch(value.strip()):
        raise PlatformError(422, "console_email_invalid")
    return value.strip().lower()


def clean_phone(value):
    """Digits with the country code, as Logto stores them; a bare mainland number gets 86."""
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise PlatformError(422, "console_phone_invalid")
    compact = re.sub(r"[\s\-()]", "", value)
    digits = compact.removeprefix("+")
    if not compact.startswith("+") and re.fullmatch(r"1[3-9]\d{9}", digits):
        digits = "86" + digits
    if not re.fullmatch(r"[1-9]\d{6,18}", digits):
        raise PlatformError(422, "console_phone_invalid")
    return digits


def clean_password(value):
    if not isinstance(value, str) or not 8 <= len(value) <= 128 or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise PlatformError(422, "console_password_invalid")
    return value


def profile(user):
    """What the console shows of a sign-in account. Never identities, custom data or secrets."""
    return {
        "username": user.get("username"),
        "email": user.get("primaryEmail"),
        "phone": user.get("primaryPhone"),
        "name": user.get("name"),
        "has_password": bool(user.get("hasPassword")),
        "suspended": bool(user.get("isSuspended")),
        "last_sign_in_at": user.get("lastSignInAt"),
    }


class LogtoAdmin:
    def __init__(self, endpoint, client_id, client_secret, *, resource=RESOURCE, transport=None, clock=time.monotonic):
        self.endpoint = endpoint.rstrip("/")
        self._id, self._secret, self.resource = client_id, client_secret, resource
        self._client = httpx.AsyncClient(timeout=10, transport=transport, follow_redirects=False)
        self._token, self._expires, self._clock = None, 0.0, clock

    def __repr__(self):
        return f"LogtoAdmin({self.endpoint!r})"

    async def _access_token(self):
        if self._token and self._clock() < self._expires:
            return self._token
        try:
            response = await self._client.post(self.endpoint + "/oidc/token", auth=(self._id, self._secret),
                                               data={"grant_type": "client_credentials", "resource": self.resource, "scope": "all"})
            value = response.json() if response.status_code == 200 else None
        except (httpx.HTTPError, ValueError):
            value = None
        if not isinstance(value, dict) or not isinstance(value.get("access_token"), str):
            raise PlatformError(502, "console_logto_unavailable")
        self._token = value["access_token"]
        self._expires = self._clock() + max(30, int(value.get("expires_in", 3600)) - 60)
        return self._token

    async def _call(self, method, path, body=None):
        headers = {"Authorization": "Bearer " + await self._access_token()}
        try:
            response = await self._client.request(method, self.endpoint + "/api" + path, json=body, headers=headers)
        except httpx.HTTPError:
            raise PlatformError(502, "console_logto_unavailable") from None
        if response.status_code == 401:
            self._token = None
        if response.status_code >= 400:
            try:
                code = response.json().get("code")
            except (ValueError, AttributeError):
                code = None
            if isinstance(code, str) and code.startswith("password."):
                raise PlatformError(422, "console_password_rejected")
            status, mapped = LOGTO_ERRORS.get(code, (404, "console_user_not_found") if response.status_code == 404
                                              else (502, "console_logto_unavailable"))
            raise PlatformError(status, mapped)
        return response.json() if response.content else {}

    async def create_user(self, *, username, phone=None, email=None, password=None):
        body = {"username": username}
        if phone:
            body["primaryPhone"] = phone
        if email:
            body["primaryEmail"] = email
        if password:
            body["password"] = password
        value = await self._call("POST", "/users", body)
        if not isinstance(value.get("id"), str) or not value["id"]:
            raise PlatformError(502, "console_logto_unavailable")
        return value

    async def update_user(self, subject, *, username, phone, email):
        return await self._call("PATCH", "/users/" + subject, {"username": username, "primaryPhone": phone, "primaryEmail": email})

    async def set_password(self, subject, password):
        await self._call("PATCH", f"/users/{subject}/password", {"password": password})

    async def set_suspended(self, subject, suspended):
        await self._call("PATCH", f"/users/{subject}/is-suspended", {"isSuspended": suspended})

    async def get_user(self, subject):
        return await self._call("GET", "/users/" + subject)

    async def aclose(self):
        await self._client.aclose()


_SHARED = {}


def from_settings(settings):
    """None unless all three console settings for Logto are present; one client per process."""
    endpoint = getattr(settings, "logto_endpoint", None)
    client_id = getattr(settings, "logto_client_id", None)
    secret = getattr(settings, "logto_client_secret", None)
    if not (endpoint and client_id and secret):
        return None
    key = (endpoint, client_id, secret)
    if key not in _SHARED:
        _SHARED[key] = LogtoAdmin(endpoint, client_id, secret)
    return _SHARED[key]
