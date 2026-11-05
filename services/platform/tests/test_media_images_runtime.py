import asyncio
import base64
import copy
import hashlib
import io
import json
from uuid import uuid4

import httpx
import pytest
from b2_legacy_media_fixtures import legacy_media
from PIL import Image
from platform_service.errors import PlatformError


def png(size=(2, 3)):
    out = io.BytesIO(); Image.new("RGB", size, (41, 62, 83)).save(out, format="PNG"); return out.getvalue()


def definition(capability="image.generate"):
    return {"id": "public", "capability": capability,
            "limits": {"max_input_bytes": 1024 * 1024, "max_assets": 4, "max_artifact_bytes": 1024 * 1024, "max_units": {"image": 4}},
            "rate": {"version": "fixture-only", "meters": [{"unit": "image", "basis": "verified_delivery_v1", "numerator": "3", "denominator": "2"}]}}


def record_actual_service(service, task_token):
    """Observe the real service methods without replacing their capability guards."""
    if hasattr(service,'_legacy_observed_originals'):
        for name,original in service._legacy_observed_originals.items():setattr(service,name,original)
    names=('authenticate_task','admit','record_provider_attempt','dispatch','record_provider_association','register_artifact','record_terminal_evidence','finish_from_evidence','pending','cancel')
    service._legacy_observed_originals={name:getattr(service,name)for name in names}
    service.jobs,service.assets,service.artifacts,service.calls={},{},{},[]
    by_id={}
    authenticate=service.authenticate_task
    async def authenticate_task(token):
        if token!='private-task':raise PlatformError(401,'invalid_authentication')
        return await authenticate(task_token)
    service.authenticate_task=authenticate_task
    original_admit=service.admit
    async def admit(context,**values):
        row=await original_admit(context,**values)
        if not row['replay']:
            service.jobs[values['key']]=row;by_id[row['id']]=row;service.calls.append('reserve')
        return row
    service.admit=admit
    def observe(name,event):
        original=getattr(service,name)
        async def called(*args,**values):
            result=await original(*args,**values)
            service.calls.append(event)
            if name=='register_artifact':service.artifacts[result['id']]=result
            if name=='record_terminal_evidence':by_id[args[0]]['evidence']=values
            if name in {'dispatch','pending','finish_from_evidence','cancel'}:
                job_id=args[1]if name=='cancel'else args[0]
                async with service.db.connection()as c:
                    row=await(await c.execute('SELECT status FROM media_jobs WHERE id=%s',(job_id,))).fetchone()
                by_id[job_id]['status']=row['status']
            return result
        setattr(service,name,called)
    for name,event in [('record_provider_attempt','attempt'),('dispatch','dispatch'),
                       ('record_provider_association','association'),('register_artifact','artifact'),
                       ('record_terminal_evidence','evidence'),('finish_from_evidence','settle'),
                       ('pending','pending'),('cancel','cancel')]:observe(name,event)
    return service


async def runtime(legacy_media,tmp_path,handler,*,capability='image.generate',**provider_patch):
    from platform_service.media_config import MediaSettings,MediaProvider
    from platform_service.media_runtime import MediaRuntime
    from test_media_contracts import definition as complete_definition
    # Each explicit runtime fixture owns its real definition and private task.
    if not legacy_media.generation._started:
        await legacy_media.restart_generation(legacy_media.runtime_db)
    if not hasattr(legacy_media,'_image_seed'):
        legacy_media._image_seed=await legacy_media.setup(legacy_media.db)
    service,ledger,scope,context,*_=legacy_media._image_seed
    service=legacy_media.client(service.raw)
    settings=MediaSettings(mode='test',storage_root=legacy_media.source_path,providers={'fixture':MediaProvider(
        'fixture-v1','http://127.0.0.1:9999/v1','private-provider',{'public':'upstream'},**provider_patch)})
    model={**complete_definition(capability),**definition(capability)}
    model.update({key:settings.bindings['public'][key]for key in ('provider_id','provider_version','adapter','protocol_version')})
    await service.publish(model,__import__('test_media_credits').OPERATOR)
    await service.set_enabled('public',True,__import__('test_media_credits').OPERATOR)
    minted=await service.mint(scope,model='public',capability=capability,invocation_key='image-unit',mode='test',bindings=settings.bindings)
    raw=record_actual_service(service.raw,minted['task_token'])
    current=MediaRuntime(legacy_media.db,raw.identity,settings,service=raw,maintenance=legacy_media.generation,transport=httpx.MockTransport(handler))
    current._legacy_scope=scope
    current._legacy_context=await raw.authenticate_task('private-task')
    await legacy_media.start_runtime(current)
    return current


async def seed_image(legacy_media,current,asset_id,raw):
    async def chunks():yield raw
    value=await current.upload_asset('private-task',chunks(),mime='image/png')
    # Actual server-owned ID stays authoritative; callers use this exact returned ID.
    return value['id']


def body(capability="image.generate", **patch): return {"model": "public", "capability": capability, "prompt": "synthetic image", "assets": [], "parameters": {"n": 1}, **patch}


@pytest.mark.asyncio
async def test_real_image_decode_hash_and_evidence_precede_settlement_without_repeat_post(tmp_path,legacy_media):
    requests = []
    async def handler(req):
        requests.append(req)
        assert req.url.path == "/v1/images/generations" and req.headers["authorization"] == "Bearer private-provider"
        assert json.loads(req.content)["model"] == "upstream"
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(png()).decode()}]}, headers={"x-request-id": "response-fixture"})
    current = await runtime(legacy_media,tmp_path, handler)
    try:
        result = await current.submit("private-task", "one", body())
        repeated = await current.submit("private-task", "one", body())
        assert result["financial_state"] == "settled" and repeated["id"] == result["id"] and len(requests) == 1
        artifact = result["artifacts"][0]
        assert "storage_key" not in artifact
        assert current.store.read(current.service.artifacts[artifact["id"]]) == png() and artifact["properties"]["height"] == 3
        assert current.service.calls.index("evidence") < current.service.calls.index("settle")
        assert current.service.jobs["one"]["evidence"]["quantities"] == {"image": 1}
    finally: await current.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["timeout", "invalid_image", "redirect", "no_response_id", "bad_usage"])
async def test_uncertain_and_invalid_upstream_never_resends_or_fakes_usage(tmp_path, kind,legacy_media):
    seen = []
    async def handler(req):
        seen.append(req)
        if kind == "timeout": raise httpx.ReadTimeout("private-provider", request=req)
        if kind == "redirect": return httpx.Response(302, headers={"location": "https://outside.example/image"})
        value = {"data": [{"b64_json": base64.b64encode(b"<html>wrong</html>" if kind == "invalid_image" else png()).decode()}]}
        if kind == "bad_usage": value["usage"] = {"input_tokens": -1, "output_tokens": 1}
        return httpx.Response(200, json=value, headers={} if kind == "no_response_id" else {"x-request-id": "upstream-fixture"})
    current = await runtime(legacy_media,tmp_path, handler)
    try:
        with pytest.raises(PlatformError) as caught: await current.submit("private-task", "one", body())
        assert "private-provider" not in str(caught.value)
        assert current.service.jobs["one"]["status"] == "pending_reconciliation"
        assert "settle" not in current.service.calls and len(seen) == 1
        await current.submit("private-task", "one", body())
        assert len(seen) == 1
    finally: await current.close()


@pytest.mark.asyncio
async def test_edit_uses_owned_decoded_asset_and_fixed_multipart_endpoint(tmp_path,legacy_media):
    seen = []
    async def handler(req):
        seen.append(req)
        assert req.url.path == "/v1/images/edits" and "multipart/form-data" in req.headers["content-type"]
        assert png() in req.content and b'name="model"' in req.content
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(png()).decode()}]}, headers={"x-request-id": "edited-fixture"})
    current = await runtime(legacy_media,tmp_path, handler,capability="image.edit")
    asset_id = await seed_image(legacy_media,current,"asset-one",png())
    try:
        result = await current.submit("private-task", "edit", body("image.edit", assets=[asset_id]))
        assert result["financial_state"] == "settled" and len(seen) == 1
    finally: await current.close()


@pytest.mark.asyncio
async def test_url_download_is_allowlisted_and_never_gets_provider_auth(tmp_path,legacy_media):
    seen = []
    async def handler(req):
        seen.append(req)
        if req.method == "POST":
            return httpx.Response(200, json={"data": [{"url": "http://127.0.0.1:9998/image.png"}]}, headers={"x-request-id": "url-fixture"})
        assert req.headers.get("authorization") is None
        return httpx.Response(200, content=png(), headers={"content-type": "image/png"})
    current = await runtime(legacy_media,tmp_path, handler, download_origins=("http://127.0.0.1:9998",))
    try:
        assert (await current.submit("private-task", "download", body()))["financial_state"] == "settled"
        assert len(seen) == 2
    finally: await current.close()
    blocked = await runtime(legacy_media,tmp_path / "blocked", handler)
    try:
        with pytest.raises(PlatformError): await blocked.submit("private-task", "blocked", body())
        assert len(seen) == 3
    finally: await blocked.close()


@pytest.mark.asyncio
async def test_client_disconnect_does_not_start_a_second_paid_attempt(tmp_path,legacy_media):
    entered, finish = asyncio.Event(), asyncio.Event()
    seen = []
    async def handler(req):
        seen.append(req); entered.set(); await finish.wait()
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(png()).decode()}]}, headers={"x-request-id": "late-fixture"})
    current = await runtime(legacy_media,tmp_path, handler)
    task = asyncio.create_task(current.submit("private-task", "one", body()))
    try:
        await entered.wait(); task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
        finish.set()
        await asyncio.gather(*current.tasks)
        assert current.service.jobs["one"]["status"] == "settled" and len(seen) == 1
    finally: finish.set(); await current.close()


@pytest.mark.asyncio
async def test_signed_download_url_is_allowed_without_forwarding_api_auth(tmp_path,legacy_media):
    seen = []
    def handler(request):
        seen.append(request)
        if request.method == "POST":
            return httpx.Response(200, json={"data": [{"url": "http://127.0.0.1:9998/image.png?signature=fixture"}]}, headers={"x-request-id": "signed-fixture"})
        assert request.url.query == b"signature=fixture" and request.headers.get("authorization") is None
        return httpx.Response(200, content=png(), headers={"content-type": "image/png"})
    current = await runtime(legacy_media,tmp_path, handler, download_origins=("http://127.0.0.1:9998",))
    try: assert (await current.submit("private-task", "signed", body()))["financial_state"] == "settled"
    finally: await current.close()


@pytest.mark.asyncio
async def test_low_disk_fails_before_wallet_reservation_or_provider_dispatch(tmp_path, monkeypatch,legacy_media):
    import shutil
    from types import SimpleNamespace
    monkeypatch.setattr(shutil, "disk_usage", lambda path: SimpleNamespace(free=0))
    current = await runtime(legacy_media,tmp_path, lambda request: (_ for _ in ()).throw(AssertionError("no model request")))
    try:
        with pytest.raises(PlatformError): await current.submit("private-task", "no-space", body())
        assert current.service.calls == []
    finally: await current.close()


@pytest.mark.asyncio
async def test_more_outputs_than_requested_remains_unknown_without_customer_charge(tmp_path,legacy_media):
    current = await runtime(legacy_media,tmp_path, lambda request: httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(png()).decode()}] * 2}, headers={"x-request-id": "extra-fixture"}))
    try:
        with pytest.raises(PlatformError): await current.submit("private-task", "extra", body())
        assert current.service.jobs["extra"]["status"] == "pending_reconciliation" and "settle" not in current.service.calls
    finally: await current.close()


@pytest.mark.asyncio
async def test_legacy_variation_is_explicit_and_never_silently_uses_edit(tmp_path,legacy_media):
    seen = []
    def handler(request):
        seen.append(request)
        assert request.url.path == "/v1/images/variations" and b'name="prompt"' not in request.content
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(png()).decode()}]}, headers={"x-request-id": "legacy-fixture"})
    current = await runtime(legacy_media,tmp_path, handler, capability="image.variation", capabilities=("image.variation",), legacy_variations=True)
    async def variations_catalog(scope, **kwargs):
        return {"models": [{"id": "public", **definition("image.variation")}]}
    current.service.catalog = variations_catalog
    asset_id = await seed_image(legacy_media,current,"one",png((2,2)))
    try:
        result = await current.submit("private-task", "variation", body("image.variation", prompt="", assets=[asset_id]))
        assert result["financial_state"] == "settled" and len(seen) == 1
    finally: await current.close()


@pytest.mark.asyncio
async def test_replay_keeps_original_job_when_input_removed_and_disk_low(tmp_path, monkeypatch,legacy_media):
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(png()).decode()}]}, headers={"x-request-id": "replay-original"})
    current = await runtime(legacy_media,tmp_path, handler,capability="image.edit")
    asset_id = await seed_image(legacy_media,current,"asset-one",png())
    request = body("image.edit", assets=[asset_id])
    try:
        original = await current.submit("private-task", "original", request)
        async def unavailable(*args,**kwargs):raise PlatformError(404,'media_asset_unavailable')
        monkeypatch.setattr(current.service,'get_asset',unavailable)
        def no_space(*args): raise PlatformError(507, "media_storage_unavailable")
        monkeypatch.setattr(current.store, "check_capacity", no_space)
        result = await current.submit("private-task", "original", request)
        assert result["id"] == original["id"] and len(seen) == 1
        with pytest.raises(PlatformError) as error:
            await current.submit("private-task", "original", {**request, "prompt": "changed"})
        assert error.value.code == "idempotency_conflict" and len(seen) == 1
    finally: await current.close()


@pytest.mark.asyncio
async def test_authorized_upload_has_wall_deadline_and_releases_upload_slot(tmp_path,legacy_media):
    current = await runtime(legacy_media,tmp_path, lambda request: None)
    object.__setattr__(current.settings, "timeout_seconds", 1)
    async def stalled():
        yield b"prefix"
        await asyncio.sleep(2)
    try:
        with pytest.raises(PlatformError) as caught:
            await current.upload_asset("private-task", stalled(), mime="image/png")
        assert caught.value.code == "media_upload_timeout"
        assert not current._uploads.locked() and not current.service.assets
        async with legacy_media.db.connection()as c:
            assert(await(await c.execute('SELECT count(*) n FROM media_assets')).fetchone())['n']==0
    finally: await current.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("unit,basis", [("image", "provider_usage_v1"), ("image", "verified_input_v1"), ("audio_ms", "provider_usage_v1")])
async def test_unsupported_meter_source_is_closed_before_reservation_or_paid_post(tmp_path, unit, basis,legacy_media):
    seen = []
    current = await runtime(legacy_media,tmp_path, lambda request: seen.append(request))
    model = definition()
    model["rate"]["meters"] = [{"unit": unit, "basis": basis, "numerator": "1", "denominator": "1"}]
    async def catalog(scope, **kwargs): return {"models": [{"id": "public", **model}]}
    current.service.catalog = catalog
    try:
        assert await current.catalog(current._legacy_scope) == {"models": []}
        with pytest.raises(PlatformError) as caught:
            await current.submit("private-task", "unsupported", body())
        assert caught.value.code == "media_metering_unsupported"
        assert current.service.jobs == {} and seen == []
    finally: await current.close()


@pytest.mark.parametrize("case", ["non_square", "jpeg", "four_mebibytes"])
def test_legacy_variation_requires_the_declared_legacy_png_input_contract(case):
    from platform_service.media_config import MediaProvider
    from platform_service.media.adapters.openai_images import OpenAIImagesAdapter
    out = io.BytesIO(); Image.new("RGB", (2, 3) if case == "non_square" else (2, 2)).save(out, format="JPEG" if case == "jpeg" else "PNG")
    raw = out.getvalue()
    if case == "four_mebibytes": raw += b"0" * (4 * 1024 * 1024 - len(raw))
    provider = MediaProvider("legacy-v1", "http://127.0.0.1:9999/v1", "fixture", {"public": "legacy-model"}, capabilities=("image.variation",), legacy_variations=True)
    asset = {"id": "one", "raw": raw, "size": len(raw), "mime": "image/jpeg" if case == "jpeg" else "image/png", "properties": {"width": 2, "height": 3 if case == "non_square" else 2}}
    with pytest.raises(PlatformError) as error:
        OpenAIImagesAdapter(provider, "test").prepare(body("image.variation", prompt="", assets=["one"]), [asset])
    assert error.value.code == "invalid_media_variation"


@pytest.mark.asyncio
async def test_nested_provider_configuration_change_cannot_reuse_old_binding(tmp_path,legacy_media):
    seen = []
    current = await runtime(legacy_media,tmp_path, lambda request: seen.append(request))
    current.settings.providers["fixture"].models["public"] = "changed-under-old-binding"
    try:
        with pytest.raises(PlatformError) as error:
            await current.submit("private-task", "stale-provider", body())
        assert error.value.code == "media_binding_unavailable"
        assert current.service.jobs == {} and seen == []
        assert await current.catalog(current._legacy_scope) == {"models": []}
    finally: await current.close()
