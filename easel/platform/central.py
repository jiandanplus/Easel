"""Native central account client. Product tokens never enter the v1 gateway."""

import asyncio
from contextlib import contextmanager
import json
import ipaddress
import os
import re
import time
import math
import subprocess
import sys
from datetime import datetime
import httpx
from urllib.parse import urlencode, urlsplit

from easel.harnesses.base import HarnessError
from .central_session import CentralSessionStore
from .session import SessionLockTimeout
from .client import PlatformClient, PlatformError, validate_url
from .privacy import remember_auth, sensitive_values
from .transport import require_local_http
from .execution_records import operation_owner, operation_turn_key, read_operations
from .auth_refresh import (access as refresh_access, access_error, ACCESS_FAILURES,
                           guard_present, guard_matches, GUARD, RefreshUncertain, write_durable, diagnostic)

_BILLING_MESSAGES = {
    "execution_unavailable": "中央文本执行尚未开放或模型当前不可用",
    "execution_entitlement_required": "当前账号缺少同类型点数的文本执行授权",
    "execution_binding_blocked": "此模型执行待核查，请选择其他模型或联系运营者",
    "insufficient_credits": "可用点数不足，请查看钱包和待核算用量",
    "pending_reconciliation": "本次执行结果待核算，请勿重复提交",
    "pending_reconciliation_limit": "待核算请求已达上限，请先查询或联系运营者核账",
    "concurrency_limit": "当前订阅并发名额已满，请等待执行中的任务完成",
    "execution_running": "本次执行仍在处理中",
    "execution_waived": "本次费用由平台承担，用户不扣点；供应商用量仍未知，原模型回复不可恢复",
    "result_expired": "执行结果缓存已过期，用量记录仍保留",
    "payments_unavailable": "支付暂不可用，请联系运营者检查渠道配置",
    "offer_not_found": "套餐已撤回或暂不可购买，请刷新目录",
    "offer_scope_denied": "此套餐不适用于当前产品或账号",
    "payments_disabled": "支付尚未开放，请联系运营者完成配置",
    "offer_unavailable": "套餐已撤回或暂不可购买，请刷新目录",
    "channel_unavailable": "此支付渠道暂不可用，请选择其他渠道",
    "idempotency_conflict": "购买请求已关联其他套餐，请重新选择",
    "order_not_found": "订单不存在或不属于当前账号",
    "order_not_pending": "订单状态已变化，请刷新订单",
    "reconciliation_required": "订单待人工核算，请联系运营者",
    "invalid_request": "购买请求格式无效，请刷新后重试",
    "redemption_code_invalid": "兑换码不正确，请核对后再输入",
    "redemption_code_used": "这个兑换码已经被使用过了",
    "redemption_code_disabled": "这批兑换码已停用",
    "redemption_code_expired": "这个兑换码已过期",
    "redemption_batch_already_used": "你已经兑换过这一批的兑换码，同一批只能兑换一次",
    "wallet_capacity_exceeded": "钱包点数已达上限，暂时不能兑换",
    "account_unavailable": "账号暂不可用，请联系运营者",
}
_AUTH_INVALID = frozenset({"invalid_authentication", "session_invalid", "refresh_replayed"})
_DESKTOP_CENTRAL_URL = "https://accounts.drawbookai.cn"


class CentralError(PlatformError):
    # True only when the connection never opened, so no request bytes left this machine.
    not_sent = False

    def __init__(self, code="platform_unavailable", status=503):
        super().__init__(code, status)
        if code in _AUTH_INVALID:
            self.args = ("中央平台登录已失效，请重新登录",)
        elif code in _BILLING_MESSAGES:
            self.args = (_BILLING_MESSAGES[code],)


def timestamp(value):
    try:
        if isinstance(value, str):
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError
            value = parsed.timestamp()
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError
        return value
    except (ValueError, OverflowError):
        raise PlatformError() from None


_STATUS_RESOLVER = """import ipaddress,json,socket,sys
addresses=[]
for family,_,_,_,peer in socket.getaddrinfo(sys.argv[1],int(sys.argv[2]),type=socket.SOCK_STREAM):
    if family not in (socket.AF_INET,socket.AF_INET6): continue
    address=ipaddress.ip_address(peer[0])
    if getattr(address,'scope_id',None) is not None: continue
    value=str(address)
    if value not in addresses: addresses.append(value)
    if len(addresses)==16: break
print(json.dumps(addresses),flush=True)
"""


async def _status_addresses(host, port):
    """An owned, killable resolver receives host/port only, never account data."""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        if getattr(address, "scope_id", None) is not None:
            raise CentralError()
        return [str(address)]
    process = None
    spawning = asyncio.create_task(asyncio.create_subprocess_exec(
        sys.executable, "-I", "-B", "-c", _STATUS_RESOLVER, host, str(port),
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        env={"PATH": os.defpath, "LANG": "C"}, close_fds=True))
    try:
        # Keep ownership even if timeout arrives during subprocess admission.
        process = await asyncio.shield(spawning)
        raw, _ = await process.communicate()
        if process.returncode != 0 or len(raw) > 4096:
            raise CentralError()
        values = json.loads(raw)
        if not isinstance(values, list) or not 1 <= len(values) <= 16:
            raise CentralError()
        addresses = []
        for value in values:
            if not isinstance(value, str) or len(value) > 45:
                raise CentralError()
            address = ipaddress.ip_address(value)
            if getattr(address, "scope_id", None) is not None:
                raise CentralError()
            addresses.append(str(address))
        return list(dict.fromkeys(addresses))
    finally:
        if process is None:
            process = await spawning
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
        # A resolver process is reaped before the caller can release its lock.
        await process.wait()


def not_sent_error(error):
    error.not_sent = True
    return error


class CentralClient:
    product_id = "easel"

    def __init__(self, root, *, transport=None, clock=time.time, url=None, absolute_deadline=None):
        require_local_http()
        self.store = CentralSessionStore(root)
        self.transport, self.clock = transport, clock
        self.absolute_deadline = absolute_deadline
        self.default_url = os.environ.get("EASEL_PLATFORM_URL", "") if url is None else url
        self.fallback_url = (_DESKTOP_CENTRAL_URL if url is None
                             and "EASEL_PLATFORM_URL" not in os.environ
                             and os.environ.get("EASEL_PORTABLE") == "1" else "")
        self.client_id = os.environ.get("EASEL_PLATFORM_CLIENT_ID", "easel-desktop")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", self.client_id):
            raise HarnessError("中央平台客户端配置无效")

    def _check_deadline(self):
        if self.absolute_deadline is not None and time.monotonic() >= self.absolute_deadline:
            raise CentralError()

    @contextmanager
    def _locked(self):
        self._check_deadline()
        options = {} if self.absolute_deadline is None else {"deadline": self.absolute_deadline}
        try:
            with self.store.locked(**options):
                self._check_deadline()
                yield
        except SessionLockTimeout:
            raise CentralError() from None

    def _response_value(self, status, content):
        try:
            value = json.loads(content)
        except (ValueError, RecursionError):
            if status < 300:
                raise
            value = None
        if status >= 300:
            # HTTP auth/account failures remain authoritative even when a
            # gateway returns an empty or malformed body.
            error = value.get("error") if isinstance(value, dict) else None
            code = error.get("code", "platform_unavailable") if isinstance(error, dict) else "platform_unavailable"
            allowed = _AUTH_INVALID | set(_BILLING_MESSAGES) | {"identity_not_configured", "invalid_request", "rate_limited", "not_found", "execution_not_found", "account_unavailable"}
            if not isinstance(code, str) or code not in allowed or (code in _AUTH_INVALID and status != 401):
                code = "platform_unavailable"
            raise CentralError(code, status if 400 <= status < 600 else 503)
        if not isinstance(value, dict):
            raise ValueError
        return value

    def _bounded_request(self, state, method, path, body, *, token, timeout, deadline):
        deadline = min(self.absolute_deadline, time.monotonic() + timeout,
                       deadline if deadline is not None else self.absolute_deadline)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise CentralError()
        url = validate_url(state.get("url", ""))
        headers = {"Authorization": "Bearer " + token} if token else {}
        failure_status = 503

        async def exchange():
            nonlocal failure_status
            # Only endpoint-owned clients enter here, in invoke's own thread.
            # Cancellation closes HTTP before the session lock is released.
            async with asyncio.timeout(max(.001, deadline - time.monotonic())):
                original = httpx.URL(url + path)
                addresses = ([None] if self.transport is not None else
                             await _status_addresses(original.raw_host.decode("ascii"),
                                                     original.port or (443 if original.scheme == "https" else 80)))
                async with httpx.AsyncClient(transport=self.transport, timeout=remaining,
                                             trust_env=False, follow_redirects=False) as client:
                    for index, address in enumerate(addresses):
                        if time.monotonic() >= deadline:
                            raise CentralError()
                        target, request_headers, extensions = original, headers, {}
                        if address is not None:
                            target = original.copy_with(host=address)
                            request_headers = {**headers, "Host": original.netloc.decode("ascii")}
                            extensions = {"sni_hostname": original.raw_host.decode("ascii")}
                        try:
                            async with client.stream(method, target, json=body, headers=request_headers,
                                                     extensions=extensions, timeout=max(.001, deadline - time.monotonic())) as response:
                                if response.status_code in (401, 403, 409):
                                    failure_status = response.status_code
                                content = bytearray()
                                async for chunk in response.aiter_bytes():
                                    if time.monotonic() >= deadline or len(content) + len(chunk) > 2 * 1024 * 1024:
                                        raise CentralError(status=failure_status)
                                    content.extend(chunk)
                                if time.monotonic() >= deadline:
                                    raise CentralError(status=failure_status)
                                return self._response_value(response.status_code, content)
                        except httpx.ConnectError:
                            # Only the real transport's pre-HTTP connection
                            # failure permits trying another resolved address.
                            if self.transport is not None or index + 1 == len(addresses):
                                raise
                raise CentralError()
        try:
            return asyncio.run(exchange())
        except PlatformError:
            raise
        except (httpx.ConnectError, httpx.ConnectTimeout):
            raise not_sent_error(CentralError()) from None
        except (httpx.HTTPError, ValueError, TypeError, AttributeError, RecursionError, TimeoutError, OSError):
            raise CentralError(status=failure_status) from None

    def _request(self, state, method, path, body=None, *, token=None, timeout=15, deadline=None):
        """Central error codes are explicit; remote details never become UI text."""
        if self.absolute_deadline is not None:
            return self._bounded_request(state, method, path, body, token=token,
                                         timeout=timeout, deadline=deadline)
        url = validate_url(state.get("url", ""))
        headers = {"Authorization": "Bearer " + token} if token else {}
        failure_status = 503
        try:
            with httpx.Client(transport=self.transport, timeout=timeout, trust_env=False, follow_redirects=False) as client:
                with client.stream(method, url + path, json=body, headers=headers) as response:
                    if response.status_code in (401, 403, 409):
                        failure_status = response.status_code
                    content = bytearray()
                    for chunk in response.iter_bytes():
                        if deadline is not None and time.monotonic() >= deadline:
                            raise CentralError(status=failure_status)
                        if len(content) + len(chunk) > 2 * 1024 * 1024:
                            raise CentralError(status=failure_status)
                        content.extend(chunk)
                    return self._response_value(response.status_code, content)
        except PlatformError:
            raise
        except (httpx.ConnectError, httpx.ConnectTimeout):
            raise not_sent_error(CentralError()) from None
        except (httpx.HTTPError, ValueError, TypeError, AttributeError, RecursionError):
            raise CentralError(status=failure_status) from None

    def _forget_invalid(self, state, error):
        # The caller owns the file lock. Only explicit authentication failure
        # permits removing persisted credentials; outages retain recoverable state.
        if getattr(error, "auth_phase", None) == "access":
            return False
        if error.status == 401 and error.code in _AUTH_INVALID:
            state.pop(GUARD, None)
            if state.pop("auth", None):
                self.store.write(state)
            return True
        return False

    def _state(self):
        state = self.store.read()
        url = self.default_url or state.get("url", "") or (self.fallback_url if not state else "")
        if url:
            url = validate_url(url)
        if state.get("auth") and state.get("url") != url:
            raise HarnessError("中央平台地址已改变，请恢复原地址并退出账号")
        if getattr(self, "_login_url", url) != url:
            raise HarnessError("中央平台地址已改变，请重新登录")
        return {**state, "version": 2, "url": url}

    def configure(self, url):
        if self.default_url:
            raise HarnessError("中央平台地址由启动环境配置")
        url = validate_url(url)
        with self.store.locked():
            state = self._state()
            if state.get("auth") and state["url"] != url:
                raise HarnessError("请先退出中央账号，再更改服务地址")
            self.store.write({**state, "url": url})
        return self.status(verify=False)

    def _validate_auth(self, auth):
        if isinstance(auth, dict):
            for key in ("expires_at", "refresh_expires_at"):
                auth[key] = timestamp(auth.get(key))
        PlatformClient._validate_auth(self, auth)
        scope = auth.get("scope")
        if (not isinstance(scope, dict)
                or set(scope) != {"user_id", "product_id", "tenant_id", "billing_account_id", "session_id", "authorization_epoch"}
                or any(not isinstance(scope.get(k), str) or not 1 <= len(scope[k]) <= 160
                       for k in ("user_id", "product_id", "tenant_id", "billing_account_id", "session_id"))
                or type(scope.get("authorization_epoch")) is not int
                or scope["authorization_epoch"] < 0
                or scope["product_id"] != self.product_id
                or scope["session_id"] != auth["session_id"]
                or scope["user_id"] != auth["account"]["id"]
                or type(auth["account"].get("email_verified")) is not bool):
            raise PlatformError()

    def _safe(self, value):
        raw = json.dumps(value, ensure_ascii=False)
        if any(key in raw for key in ('"task_token"', '"access_token"', '"refresh_token"', '"api_key"', '"api_v3_key"', '"private_key"', '"signature"')) or any(
            secret in raw for secret in sensitive_values(self.store.root)
        ):
            raise PlatformError()
        return value

    def begin(self, callback_uri, challenge, state):
        with self.store.locked():
            current = self._state()
            if guard_present(current):
                if not guard_matches(current):raise HarnessError("中央账号已改变，请先退出当前账号")
                current.pop("auth", None); current.pop(GUARD, None)
                write_durable(self.store, current)
            if current.get("auth"):
                if current["auth"]["refresh_expires_at"] <= self.clock():
                    self._forget_invalid(current, CentralError("invalid_authentication", 401))
                else:
                    raise HarnessError("请先退出当前中央账号")
            self._login_url = current["url"]
        value = self._request(current, "POST", "/v2/auth/transactions", {
            "product_id": self.product_id, "client_id": self.client_id,
            "callback_uri": callback_uri, "handoff_challenge": challenge, "local_state": state,
        })
        try:
            target, origin = urlsplit(value["authorize_url"]), urlsplit(current["url"])
            if (target.scheme, target.netloc) != (origin.scheme, origin.netloc) or target.username or target.password or target.fragment:
                raise ValueError
            if not target.path.startswith("/oidc/easel/"):
                raise ValueError
            value["expires_at"] = timestamp(value["expires_at"])
            if not self.clock() < value["expires_at"] <= self.clock() + 900:
                raise ValueError
        except (KeyError, ValueError, TypeError):
            raise PlatformError() from None
        return self._safe(value)

    def redeem(self, code, verifier, callback_uri, state):
        with self.store.locked():
            current = self._state()
        value = self._request(current, "POST", "/v2/auth/redeem", {
            "code": code, "code_verifier": verifier, "product_id": self.product_id,
            "client_id": self.client_id, "callback_uri": callback_uri, "state": state,
        })
        self._validate_auth(value)
        remember_auth(self.store.root, {"auth": value})
        return value

    def discard_auth(self, auth):
        """Best-effort revoke a redeemed session that local admission rejected."""
        self._request({"url": self._login_url}, "POST", "/v2/auth/logout", token=auth["access_token"])

    def save_auth(self, auth):
        self._validate_auth(auth)
        with self.store.locked():
            state = self._state()
            if state.get("auth"):
                raise HarnessError("中央账号已改变，请重新登录")
            state.pop(GUARD, None)
            self.store.write({**state, "auth": auth})

    def _access(self, state, *, deadline=None, force=False):
        self._check_deadline()
        if self.absolute_deadline is not None:
            deadline = min(self.absolute_deadline, deadline if deadline is not None else self.absolute_deadline)
            if time.monotonic() >= deadline:
                raise CentralError()
        def send(refresh):
            options = {} if deadline is None else {"timeout": max(.001, min(15, deadline - time.monotonic())), "deadline": deadline}
            if deadline is not None and deadline <= time.monotonic():raise CentralError()
            return self._request(state, "POST", "/v2/auth/refresh", {"refresh_token": refresh}, **options)
        return refresh_access(self, state, send=send, clock=self.clock, force=force)

    def _get(self, state, path, *, timeout=15, deadline=None):
        deadline=min(deadline,time.monotonic()+timeout) if deadline is not None else time.monotonic()+timeout
        initial=(state.get("auth") or {}).get("access_token")
        token=self._access(state, deadline=deadline)
        renewed=token!=initial
        def request():
            remaining=deadline-time.monotonic()
            if remaining<=0:raise CentralError()
            return self._request(state,"GET",path,token=token,timeout=min(timeout,remaining),deadline=deadline)
        try:return request()
        except PlatformError as error:
            diagnostic(error.code,error.status,path,(state.get("auth") or {}).get("scope"))
            if error.status!=401 or error.code not in ACCESS_FAILURES or renewed:raise access_error(error)
        token=self._access(state, deadline=deadline, force=True)
        try:return request()
        except PlatformError as error:
            diagnostic(error.code,error.status,path,(state.get("auth") or {}).get("scope"))
            raise access_error(error)

    def status(self, *, verify=True):
        with self._locked():
            state = self._state()
            auth, failure = state.get("auth"), None
            if auth:
                self._validate_auth(auth)
                if guard_present(state):
                    failure = RefreshUncertain()
                elif auth["refresh_expires_at"] <= self.clock():
                    failure = CentralError("invalid_authentication", 401)
                    self._forget_invalid(state, failure)
                    auth = None
            username = None
            if auth and verify and failure is None:
                try:
                    me = self._safe(self._get(state, "/v2/me", deadline=self.absolute_deadline))
                    auth = state["auth"]
                    if me.get("scope") != auth["scope"] or me.get("account", {}).get("id") != auth["account"]["id"]:
                        raise PlatformError("account_changed", 409)
                    name = me.get("account", {}).get("username")
                    if isinstance(name, str) and 0 < len(name) <= 128 and name.isprintable():
                        username = name
                except PlatformError as error:
                    failure = error
                    if self._forget_invalid(state, error):
                        auth = None
        result = {"configured": bool(state["url"]), "url": state["url"], "authenticated": bool(auth and auth["refresh_expires_at"] > self.clock() and not failure),
                  "account": {**{k: auth["account"].get(k) for k in ("id", "email", "email_verified")}, "username": username} if auth else None,
                  "scope": dict(auth["scope"]) if auth else None, "login": {"pending": False}}
        if failure:
            result["error"] = {"code": failure.code, "message": str(failure)}
        return self._safe(result)

    def call(self, method, path, body=None, *, public=False):
        allowed = {"/v2/capabilities?product_id=easel", "/v2/plans?product_id=easel", "/v2/wallet", "/v2/products/easel/entitlements", "/v2/usage", "/v2/sessions", "/v2/billing/catalog?product_id=easel", "/v2/orders", "/v2/products/easel/models", "/v2/executions"}
        order_read = method == "GET" and re.fullmatch(r"/v2/orders/[A-Za-z0-9]{32}", path)
        order_action = method == "POST" and re.fullmatch(r"/v2/orders/[A-Za-z0-9]{32}/(refresh|close)", path) and body is None
        order_create = method == "POST" and path == "/v2/orders"
        redeem = method == "POST" and path == "/v2/redemptions"
        if redeem and (not isinstance(body, dict) or set(body) != {"code"} or not isinstance(body.get("code"), str)
                       or not re.fullmatch(r"[A-Za-z0-9 -]{4,64}", body["code"])):
            raise CentralError("invalid_request", 400)
        if order_create:
            if (not isinstance(body, dict) or set(body) != {"offer_id", "channel", "idempotency_key"}
                    or not all(isinstance(body.get(k), str) and re.fullmatch(r"[A-Za-z0-9_-]{1,160}", body[k]) for k in ("offer_id", "idempotency_key"))
                    or body.get("channel") not in {"wechat", "alipay"}):
                raise CentralError("invalid_request", 400)
        if method == "GET" and body is not None:
            raise CentralError("invalid_request", 400)
        if not (order_read or order_action or order_create or redeem or (method == "GET" and path in allowed) or (method == "DELETE" and re.fullmatch(r"/v2/sessions/[A-Za-z0-9_-]{1,160}", path))):
            raise HarnessError("中央平台执行链路 S3 尚未开放")
        if public and path not in {"/v2/capabilities?product_id=easel", "/v2/plans?product_id=easel", "/v2/billing/catalog?product_id=easel"}:
            raise HarnessError("中央平台请求需要登录")
        with self._locked():
            state = self._state()
            try:
                if not public and method == "GET":
                    value = self._safe(self._get(state, path))
                else:
                    token = None if public else self._access(state)
                    try:value = self._safe(self._request(state, method, path, body, token=token))
                    except PlatformError as error:
                        if not public:access_error(error)
                        raise
                if path in ("/v2/products/easel/models", "/v2/executions"):
                    return self._execution_view(path, value)
                if path == "/v2/wallet":
                    return self._wallet_view(value)
                return value
            except PlatformError as error:
                if not public:
                    self._forget_invalid(state, error)
                raise

    def logout(self):
        with self.store.locked():
            state = self._state()
            if guard_present(state):
                # Explicit local logout must not replay an uncertain refresh.
                state.pop("auth", None); state.pop(GUARD, None)
                write_durable(self.store, state)
            if state.get("auth"):
                try:
                    self._request(state, "POST", "/v2/auth/logout", token=self._access(state))
                except PlatformError as error:
                    if guard_present(state):
                        state.pop("auth", None); state.pop(GUARD, None)
                        write_durable(self.store, state)
                    elif not self._forget_invalid(state, error):
                        raise
                self.store.write({"version": 2, "url": state["url"]})
        return self.status(verify=False)

    @staticmethod
    def _wallet_view(value):
        if not isinstance(value, dict):
            raise CentralError()
        # Older servers omit these fields. Preserve absence rather than
        # inventing zero active requests or a default pending allowance.
        for key, minimum in (("active_requests", 0), ("pending_reconciliation_limit", 1)):
            if key in value:
                number = value[key]
                if type(number) is not int or not minimum <= number <= 2**53 - 1:
                    raise CentralError()
        return value

    @staticmethod
    def _execution_view(path, value):
        """Validate bounded, non-secret public fields before browser rendering."""
        def text(value):
            if not isinstance(value, str) or not 1 <= len(value) <= 240 or any(ord(c) < 32 for c in value):
                raise ValueError
            return value
        def money(value, *, positive=False):
            if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,19}", value):
                raise ValueError
            if int(value) > 2**63 - 1 or (positive and int(value) == 0):
                raise ValueError
            return value
        try:
            key = "models" if path.endswith("/models") else "executions"
            rows = value[key]
            if not isinstance(rows, list) or len(rows) > 1000:
                raise ValueError
            result = []
            for row in rows:
                if not isinstance(row, dict):
                    raise ValueError
                if key == "models":
                    safe = {k: text(row[k]) for k in ("id", "name", "version")}
                    if (row["capability"] != "text" or type(row["test_only"]) is not bool
                            or row["readiness"] not in ("test", "live")
                            or row["test_only"] != (row["readiness"] == "test")):
                        raise ValueError
                    rate = {"version": text(row["rate"]["version"])}
                    for side in ("input", "output"):
                        rate[side] = {"numerator": money(row["rate"][side]["numerator"]),
                                      "denominator": money(row["rate"][side]["denominator"], positive=True)}
                    limits = {}
                    for name, maximum in (("max_input_bytes", 1048576), ("max_input_tokens", 65536), ("max_output_tokens", 4096)):
                        number = row["limits"][name]
                        if type(number) is not int or not 0 < number <= maximum:
                            raise ValueError
                        limits[name] = number
                    safe.update(capability="text", test_only=row["test_only"], readiness=row["readiness"], rate=rate, limits=limits)
                    if "reasoning" in row:
                        capability = row["reasoning"]
                        allowed_efforts = ("minimal", "low", "medium", "high", "xhigh", "max")
                        if (not isinstance(capability, dict) or set(capability) != {"efforts"}
                                or not isinstance(capability["efforts"], list)
                                or len(capability["efforts"]) > len(allowed_efforts)
                                or any(not isinstance(level, str) or level not in allowed_efforts
                                       for level in capability["efforts"])
                                or len(set(capability["efforts"])) != len(capability["efforts"])):
                            raise ValueError
                        safe["reasoning"] = {"efforts": list(capability["efforts"])}
                else:
                    safe = {k: text(row[k]) for k in ("id", "model", "rate_version", "created_at", "updated_at")}
                    if row["status"] not in ("reserved", "dispatched", "settled", "pending_reconciliation", "released", "waived") or row["funding_mode"] not in ("test", "live"):
                        raise ValueError
                    safe.update(status=row["status"], funding_mode=row["funding_mode"], amount=money(row["amount"]),
                                actual=None if row.get("actual") is None else money(row["actual"]))
                    if row["status"] == "pending_reconciliation" and row.get("failure_code") in (
                        "upstream_timeout", "upstream_unavailable", "upstream_error",
                        "invalid_upstream_usage", "request_cancelled",
                    ):
                        safe["failure_code"] = row["failure_code"]
                    for name in ("input_tokens", "output_tokens"):
                        number = row.get(name)
                        if number is not None and (type(number) is not int or not 0 <= number <= 2**63 - 1):
                            raise ValueError
                        safe[name] = number
                    if row["status"] == "waived":
                        if (row.get("actual") is not None or row.get("input_tokens") is not None
                                or row.get("output_tokens") is not None or row.get("customer_charge") != "0"
                                or row.get("resolution") != "platform_absorbed"
                                or row.get("provider_usage_known") is not False):
                            raise ValueError
                        safe.update(customer_charge="0", resolution="platform_absorbed", provider_usage_known=False)
                result.append(safe)
            return {key: result}
        except (KeyError, TypeError, ValueError):
            raise CentralError() from None

    @staticmethod
    def _operation_view(value):
        """Keep cached-result availability distinct from financial settlement."""
        if not isinstance(value, dict) or value.get("result_state") not in ("available", "expired", "not_available"):
            raise CentralError()
        try:
            for key in ("created_at", "updated_at"):
                timestamp(value.get(key))
        except PlatformError:
            raise CentralError() from None
        if value["result_state"] in ("available", "expired") and value.get("status") != "settled":
            raise CentralError()
        row = CentralClient._execution_view("/v2/executions", {"executions": [value]})["executions"][0]
        return {**row, "result_state": value["result_state"]}

    def recoveries(self, session_id=None, turn_id=None):
        """Inspect owned local tuples via authenticated GETs; never resend them."""
        turn_key = None
        if session_id is not None or turn_id is not None:
            turn_key = operation_turn_key(session_id, turn_id)
        # Account verification and operation lookups share one bounded window.
        # A remote central service may take several seconds per authenticated
        # GET; the old two-second allowance rejected otherwise valid reads.
        read_timeout = 10
        deadline = time.monotonic() + 30
        with self.store.locked():
            state = self._state()
            try:
                me = self._safe(self._get(state, "/v2/me", timeout=read_timeout, deadline=deadline))
                auth = state["auth"]
                if me.get("scope") != auth["scope"] or me.get("account", {}).get("id") != auth["account"]["id"]:
                    raise PlatformError("account_changed", 409)
                records, truncated = read_operations(self.store.root, operation_owner(state["url"], auth["scope"]), turn_key)
                rows = []
                for record in records:
                    row = {**record, "lookup_status": "unavailable"}
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        truncated = True
                    else:
                        try:
                            query = urlencode({"invocation_id": record["invocation_id"], "operation_key": record["operation_key"]})
                            value = self._safe(self._get(state, "/v2/execution-operations?" + query,
                                                       timeout=min(read_timeout, remaining), deadline=deadline))
                            row.update(lookup_status="found", execution=self._operation_view(value))
                        except PlatformError as error:
                            # No partial rows survive account/authentication failure.
                            if error.status in (401, 403, 409) or error.code in ("account_changed", "account_unavailable"):
                                raise
                            if error.status == 404:
                                row["lookup_status"] = "not_found"
                    rows.append(row)
                return self._safe({"recoveries": rows, "truncated": truncated})
            except PlatformError as error:
                self._forget_invalid(state, error)
                raise

    def models(self):
        return self.call("GET", "/v2/products/easel/models")

    def authorize(self, model):
        with self.store.locked():
            state = self._state()
            me = self._safe(self._get(state, "/v2/me"))
            scope = state["auth"]["scope"]
            if me.get("scope") != scope:
                raise PlatformError("account_changed", 409)
            catalog = self._execution_view("/v2/products/easel/models", self._safe(self._get(state, "/v2/products/easel/models")))
            if not model or not any(item.get("id") == model for item in catalog.get("models", [])):
                raise HarnessError("中央模型不可用，请从当前中央目录重新选择")
            return {"account_id": scope["user_id"], "platform_url": state["url"], "scope": dict(scope)}

    def task_credential(self, account_id, model, platform_url, scope, invocation_key):
        """Private mint seam. This return value must never enter a browser response."""
        with self.store.locked():
            state = self._state()
            token = self._access(state)
            if (state["url"] != platform_url or state["auth"]["scope"] != scope
                    or scope.get("user_id") != account_id or not invocation_key):
                raise HarnessError("中央账号或会话范围已改变，请新建对话")
            value = self._request(state, "POST", "/v2/task-tokens",
                                  {"model": model, "invocation_key": invocation_key}, token=token)
            if (not isinstance(value.get("task_token"), str) or not 16 <= len(value["task_token"]) <= 16384
                    or value.get("scope") != scope or value.get("model") != model
                    or value.get("base_url_path") != "/v2"
                    or not isinstance(value.get("invocation_id"), str)
                    or value.get("funding_mode") not in ("test", "live")
                    or timestamp(value.get("expires_at")) <= self.clock()):
                raise PlatformError()
            from .privacy import remember
            remember(self.store.root, [value["task_token"]])
            return value
