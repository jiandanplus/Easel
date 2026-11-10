"""Admission failures use owned bounded codes, never arbitrary SDK/provider text."""
import asyncio
import json
import time

import httpx
import pytest

from easel.harnesses.base import HarnessError, RunRequest
from easel.platform.execution_proxy import ExecutionProxy


SAFE_CODES = ["concurrency_limit", "insufficient_credits", "membership_required",
              "pending_reconciliation", "pending_reconciliation_limit", "execution_entitlement_required", "execution_running", "execution_waived",
              "invocation_budget_exceeded", "output_limit_exceeded", "unsupported_reasoning_effort"]


class ErrorBody(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks, self.pulls, self.closed = chunks, 0, False

    async def __aiter__(self):
        for chunk in self.chunks:
            self.pulls += 1
            yield chunk

    async def aclose(self):
        self.closed = True


async def denied_proxy(tmp_path, chunks, *, status=409, headers=None):
    calls, source = [], ErrorBody(chunks)
    async def handle(request):
        calls.append(request)
        return httpx.Response(status, stream=source,
                              headers={"content-type":"application/json", **(headers or {})})
    async with ExecutionProxy(tmp_path, "https://central.test", "private-fixture-token", "fixture", "inv",
                              transport=httpx.MockTransport(handle)) as proxy:
        async with httpx.AsyncClient(trust_env=False) as client:
            response = await client.post(proxy.base_url + "/responses", json={"model":"fixture"},
                                         headers={"Authorization":"Bearer " + proxy.local_token})
        assert response.status_code == 502
        assert b"PRIVATE" not in response.content
        assert response.json() == {"error":{"code":"central_request_failed"}}
        code = getattr(proxy, "safe_failure_code", None)
    rows = [json.loads(p.read_text()) for p in (tmp_path/"operations").glob("*.json")]
    assert len(rows) == 1 and rows[0]["status"] == "pending"
    assert len(calls) == 1 and calls[0].url.path == "/v2/responses"
    assert source.closed
    assert "PRIVATE" not in json.dumps(rows)
    return code, source


@pytest.mark.parametrize("code", SAFE_CODES)
def test_owned_proxy_retains_only_safe_admission_code_and_never_retries(tmp_path, code):
    payload=json.dumps({"error":{"code":code,"message":"PRIVATE_PROVIDER_TEXT"},"debug":"PRIVATE"}).encode()
    found, _ = asyncio.run(denied_proxy(tmp_path,[payload]))
    assert found == code


@pytest.mark.parametrize("body", [
    b'{"error":{"code":"PRIVATE_UNKNOWN"}}',
    b'{"error":{"code":["concurrency_limit"]}}',
    b'{"error":{"code":{"value":"concurrency_limit"}}}',
    b'{"error":{"code":"concurrency_limit","code":"PRIVATE"}}',
    b'{"error":{"code":"concurrency_limit"},"debug":NaN}',
    b'{"error":{"code":"concurrency_limit"}',
    b'\xffPRIVATE', b'[]',
])
def test_unknown_or_invalid_error_body_is_not_retained(tmp_path, body):
    found, _ = asyncio.run(denied_proxy(tmp_path,[body]))
    assert found is None


def test_oversized_error_body_is_bounded_and_not_retained(tmp_path):
    prefix=b'{"error":{"code":"concurrency_limit"},"debug":"'
    source=[prefix, b"x"*8192, b'PRIVATE_MUST_NOT_BE_READ"}']
    found, observed = asyncio.run(denied_proxy(tmp_path,source))
    assert found is None and observed.pulls <= 2


@pytest.mark.parametrize("status,headers", [
    (302, {}), (409,{"content-type":"text/plain"}), (409,{"content-encoding":"gzip"}),
])
def test_non_json_encoded_or_redirect_errors_cannot_set_owned_code(tmp_path,status,headers):
    found, _ = asyncio.run(denied_proxy(tmp_path,[b'{"error":{"code":"concurrency_limit"}}'],
                                       status=status,headers=headers))
    assert found is None


def test_next_explicit_operation_clears_prior_safe_failure(tmp_path):
    async def run():
        calls=[]
        async def central(request):
            calls.append(request)
            code="concurrency_limit" if len(calls)==1 else "PRIVATE_UNKNOWN"
            return httpx.Response(409,json={"error":{"code":code}})
        async with ExecutionProxy(tmp_path,"https://central.test","private-fixture-token","fixture","inv",
                                  transport=httpx.MockTransport(central)) as proxy:
            async with httpx.AsyncClient(trust_env=False) as client:
                for expected in ["concurrency_limit",None]:
                    response=await client.post(proxy.base_url+"/responses",json={"model":"fixture"},
                                               headers={"Authorization":"Bearer "+proxy.local_token})
                    assert response.status_code==502
                    assert getattr(proxy,"safe_failure_code",None)==expected
        assert len(calls)==2
    asyncio.run(run())


@pytest.mark.asyncio
@pytest.mark.parametrize("code,expected", [
    ("concurrency_limit","并发名额已满"),
    ("insufficient_credits","订阅点数不足"),
    ("membership_required","没有可用的订阅权益"),
    ("pending_reconciliation","最终费用尚未确认"),
    ("pending_reconciliation_limit","待核算请求已达上限，请先查询或联系运营者核账"),
    ("execution_entitlement_required","未开通此模型功能"),
    ("execution_running","请求仍在执行"),
    ("execution_waived","费用由平台承担，用户不扣点；供应商用量仍未知"),
    ("invocation_budget_exceeded","单次调用价格超过了平台上限"),
    ("output_limit_exceeded","回复长度超过了模型上限"),
    ("unsupported_reasoning_effort","不支持所选的思考强度"),
    ("PRIVATE_UNKNOWN","原通用执行错误"),
])
async def test_subscription_pi_surfaces_owned_admission_without_resending(tmp_path,monkeypatch,code,expected):
    from easel.harnesses import pi
    import easel.platform.central
    import easel.platform.execution_proxy
    calls=[]
    scope=dict(user_id="user",tenant_id="tenant",billing_account_id="wallet",product_id="easel",
               session_id="session",authorization_epoch=0)
    class Client:
        def __init__(self,_):pass
        def models(self):return {"models":[{"id":"fixture","limits":{"max_output_tokens":512,"max_input_tokens":2048}}]}
        def task_credential(self,*args):
            return dict(task_token="private-fixture-token",invocation_id="fixture-invocation",model="fixture",
                        scope=scope,funding_mode="live",expires_at=time.time()+600)
    async def central(request):
        calls.append(request)
        return httpx.Response(409,json={"error":{"code":code,"message":"PRIVATE_PROVIDER_TEXT"}})
    def factory(*args,**kwargs):return ExecutionProxy(*args,**kwargs,transport=httpx.MockTransport(central))
    async def worker(self,request,emit,ask,bind,model,base_url,key,*args):
        async with httpx.AsyncClient(trust_env=False) as client:
            response=await client.post(base_url+"/responses",json={"model":model},headers={"Authorization":"Bearer "+key})
        assert response.status_code==502
        raise HarnessError("原通用执行错误 concurrency_limit")
    monkeypatch.setattr(easel.platform.central,"CentralClient",Client)
    monkeypatch.setattr(easel.platform.execution_proxy,"ExecutionProxy",factory)
    monkeypatch.setattr(pi.PiAdapter,"_run",worker)
    request=RunRequest("fixture",tmp_path,{},model="fixture",service_source="subscription",subscription_backend="central",
                       account_id="user",platform_url="https://central.test",central_scope=scope,invocation_key="key")
    with pytest.raises(HarnessError) as error:
        await pi.PiAdapter().run(request,lambda *_:None,lambda *_:None,lambda *_:None)
    message=str(error.value)
    assert expected in message and "PRIVATE" not in message
    if code=="PRIVATE_UNKNOWN":
        assert message=="原通用执行错误 concurrency_limit"
    assert "已退款" not in message and "未扣点" not in message and "实际扣点0" not in message
    assert len(calls)==1


@pytest.mark.asyncio
async def test_custom_pi_does_not_reinterpret_sdk_error_text(tmp_path,monkeypatch):
    from easel.harnesses import pi
    monkeypatch.setattr(pi,"require_provider",lambda _:{"model":"fixture","base_url":"https://fixture.invalid/v1","api_key":"fixture"})
    async def fail(*args):raise HarnessError("原通用执行错误 concurrency_limit")
    monkeypatch.setattr(pi.PiAdapter,"_run",fail)
    request=RunRequest("fixture",tmp_path,{},model="fixture",service_source="custom")
    with pytest.raises(HarnessError,match="^原通用执行错误 concurrency_limit$"):
        await pi.PiAdapter().run(request,lambda *_:None,lambda *_:None,lambda *_:None)
