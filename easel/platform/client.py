"""Native HTTP account client with serialized refresh and safe errors."""

import ipaddress
import time
from urllib.parse import urlsplit
import httpx
from easel.harnesses.base import HarnessError
from .session import SessionStore


class PlatformError(HarnessError):
    def __init__(self, code="platform_unavailable", status=503):
        self.code = code
        self.status = status
        messages = {
            "invalid_authentication": "平台登录已失效，请重新登录",
            "entitlement_required": "当前账号没有可用的测试订阅授权",
            "quota_exceeded": "平台订阅额度不足",
            "concurrency_exceeded": "平台订阅并发已满",
            "account_changed": "平台账号已改变，请重新提交任务",
            "model_not_allowed": "平台订阅不允许使用此模型",
        }
        super().__init__(messages.get(code, "平台服务不可用，请检查服务地址与账号状态"))


def validate_url(value):
    try:
        if (
            not isinstance(value, str)
            or "\\" in value
            or len(value) > 2048
            or any(ord(c) < 33 for c in value)
        ):
            raise ValueError
        url = urlsplit(value)
        _ = url.port  # Force validation of malformed/out-of-range ports.
        if (
            not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            raise ValueError
        host = url.hostname.lower()
        loopback = host == "localhost"
        try:
            loopback = loopback or ipaddress.ip_address(host).is_loopback
        except ValueError:
            pass
        if url.scheme != "https" and not (url.scheme == "http" and loopback):
            raise ValueError
        if url.path not in ("", "/"):
            raise ValueError
        return value.rstrip("/")
    except (ValueError, TypeError):
        raise HarnessError(
            "平台地址必须为 HTTPS，开发环境仅允许本机 HTTP；不能包含凭证、路径或查询参数"
        ) from None


class PlatformClient:
    def __init__(self, root, *, transport=None, clock=time.time):
        self.store = SessionStore(root)
        self.transport = transport
        self.clock = clock

    def _request(self, state, method, path, body=None, *, token=None):
        url = validate_url(state.get("url", ""))
        headers = {"Authorization": "Bearer " + token} if token else {}
        try:
            with httpx.Client(
                transport=self.transport,
                timeout=15,
                trust_env=False,
                follow_redirects=False,
            ) as client:
                response = client.request(
                    method, url + path, json=body, headers=headers
                )
            if len(response.content) > 2 * 1024 * 1024:
                raise PlatformError()
            if response.status_code >= 300:
                code = (response.json().get("error") or {}).get(
                    "code", "platform_unavailable"
                )
                raise PlatformError(
                    (
                        code
                        if code
                        in {
                            "invalid_authentication",
                            "entitlement_required",
                            "quota_exceeded",
                            "concurrency_exceeded",
                            "model_not_allowed",
                            "gateway_not_configured",
                            "rate_limited",
                            "invalid_request",
                            "account_unavailable",
                            "not_found",
                        }
                        else "platform_unavailable"
                    ),
                    response.status_code,
                )
            value = response.json()
            if not isinstance(value, dict):
                raise ValueError
            return value
        except PlatformError:
            raise
        except (httpx.HTTPError, ValueError, TypeError, AttributeError):
            raise PlatformError() from None

    def configure(self, url):
        url = validate_url(url)
        with self.store.locked():
            state = self.store.read()
            if state.get("auth") and state.get("url") != url:
                raise HarnessError("请先退出平台账号，再更改服务地址")
            self.store.write({**state, "version": 1, "url": url})
        return self.status(verify=False)

    def status(self, *, verify=True):
        with self.store.locked():
            state = self.store.read()
            if state.get("url"):
                validate_url(state["url"])
            failure = None
            if verify and state.get("auth"):
                try:
                    token = self._access(state)
                    account = self._request(state, "GET", "/v1/account", token=token)
                    if (
                        account.get("account", {}).get("id")
                        != state["auth"]["account"]["id"]
                    ):
                        raise PlatformError("account_changed", 409)
                except PlatformError as error:
                    failure = error
        result = {
            "configured": bool(state.get("url")),
            "url": state.get("url", ""),
            "authenticated": False,
            "account": None,
            "session_id": "",
            "expires_at": None,
            "refresh_expires_at": None,
            "authentication_verified": False,
        }
        auth = state.get("auth")
        if auth:
            result.update(
                authenticated=auth["refresh_expires_at"] > self.clock(),
                account={
                    k: auth["account"].get(k) for k in ("id", "email", "email_verified")
                },
                session_id=auth["session_id"],
                expires_at=auth["expires_at"],
                refresh_expires_at=auth["refresh_expires_at"],
            )
        if failure:
            result["authenticated"] = False
            result["error"] = {"code": failure.code, "message": str(failure)}
        return result

    def login(self, email, password, device_name="Easel", *, register=False):
        with self.store.locked():
            state = self.store.read()
            if state.get("auth"):
                raise HarnessError("请先退出当前平台账号")
            auth = self._request(
                state,
                "POST",
                "/v1/auth/" + ("register" if register else "login"),
                {"email": email, "password": password, "device_name": device_name},
            )
            self._validate_auth(auth)
            self.store.write({**state, "auth": auth})
        return self.status(verify=False)

    def _validate_auth(self, auth):
        if (
            not isinstance(auth, dict)
            or not all(
                isinstance(auth.get(k), str) and auth[k]
                for k in ("access_token", "refresh_token", "session_id")
            )
            or not isinstance(auth.get("account"), dict)
            or not isinstance(auth["account"].get("id"), str)
            or not all(
                isinstance(auth.get(k), (int, float)) and not isinstance(auth[k], bool)
                for k in ("expires_at", "refresh_expires_at")
            )
        ):
            raise PlatformError()

    def _access(self, state):
        auth = state.get("auth")
        if not auth or auth["refresh_expires_at"] <= self.clock():
            raise PlatformError("invalid_authentication", 401)
        if auth["expires_at"] <= self.clock() + 30:
            fresh = self._request(
                state,
                "POST",
                "/v1/auth/refresh",
                {"refresh_token": auth["refresh_token"]},
            )
            self._validate_auth(fresh)
            if (
                fresh["account"]["id"] != auth["account"]["id"]
                or fresh["session_id"] != auth["session_id"]
            ):
                raise PlatformError("account_changed", 409)
            state["auth"] = fresh
            self.store.write(state)
            auth = fresh
        return auth["access_token"]

    def call(self, method, path, body=None, *, public=False, account_id=""):
        with self.store.locked():
            state = self.store.read()
            if (
                account_id
                and (state.get("auth") or {}).get("account", {}).get("id") != account_id
            ):
                raise PlatformError("account_changed", 409)
            token = None if public else self._access(state)
            return self._request(state, method, path, body, token=token)

    def account(self):
        return self.call("GET", "/v1/account")

    def models(self):
        return self.call("GET", "/v1/models")

    def logout(self):
        with self.store.locked():
            state = self.store.read()
            if state.get("auth"):
                try:
                    self._request(
                        state, "POST", "/v1/auth/logout", token=self._access(state)
                    )
                except PlatformError as error:
                    if error.code != "invalid_authentication":
                        raise
                self.store.write({"version": 1, "url": state["url"]})
        return self.status()

    def authorize(self, model):
        with self.store.locked():
            state = self.store.read()
            token = self._access(state)
            catalog = self._request(state, "GET", "/v1/models", token=token)
            models = [
                item.get("id")
                for item in catalog.get("models", [])
                if isinstance(item, dict)
            ]
            if not model or model not in models:
                raise PlatformError("model_not_allowed", 403)
            if not catalog.get("ready"):
                raise PlatformError("gateway_not_configured", 503)
            grants = self._request(state, "GET", "/v1/entitlements", token=token).get(
                "entitlements", []
            )
            if not any(
                g.get("status") == "active"
                and model in g.get("models", [])
                and g.get("remaining_tokens", 0) > 0
                for g in grants
            ):
                raise PlatformError("entitlement_required", 403)
            minted = self._request(
                state, "POST", "/v1/task-tokens", {"model": model}, token=token
            )
            self._validate_mint(minted, state["auth"]["account"]["id"], model)
            return {"account_id": minted["account_id"], "platform_url": state["url"]}

    def _validate_mint(self, value, owner, model):
        if (
            value.get("account_id") != owner
            or value.get("model") != model
            or not isinstance(value.get("token"), str)
            or not value["token"]
        ):
            raise PlatformError()

    def task_credential(self, account_id, model, platform_url):
        with self.store.locked():
            state = self.store.read()
            if (
                state.get("url") != platform_url
                or (state.get("auth") or {}).get("account", {}).get("id") != account_id
            ):
                raise PlatformError("account_changed", 409)
            token = self._access(state)
            value = self._request(
                state, "POST", "/v1/task-tokens", {"model": model}, token=token
            )
            self._validate_mint(value, account_id, model)
            return value["token"]
